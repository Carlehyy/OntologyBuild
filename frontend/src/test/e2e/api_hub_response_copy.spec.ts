import { expect, test } from '@playwright/test'

const realHttp = process.env.PLAYWRIGHT_REAL_HTTP === '1'
const expectedCopy = '{\n  "message": "copied over http",\n  "ok": true\n}'

const responseInterface = {
  id: 9,
  name: 'HTTP 响应复制测试',
  description: '验证非安全上下文中的剪贴板降级能力',
  group_name: '测试接口',
  method: 'GET',
  url: 'https://vendor.example/v1/result',
  query_params: [],
  headers: [],
  body_type: 'none',
  body_content: '',
  file_fields: [],
  use_w3: false,
  mcp_enabled: false,
  open_enabled: false,
  http_enabled: false,
  proxy_slug: '',
  proxy_query_keys: [],
  proxy_header_keys: [],
  proxy_body_enabled: false,
  proxy_body_keys: [],
}

test('Clipboard API 不可用时仍可复制接口响应', async ({ page }) => {
  await page.addInitScript(({ instrumentFallback }) => {
    localStorage.setItem('token', 'admin-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'admin-token',
        user: {
          id: 1,
          username: 'admin',
          email: 'admin@example.com',
          role: 'admin',
          is_active: true,
        },
      },
      version: 0,
    }))
    if (!instrumentFallback) return
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    const originalExecCommand = Document.prototype.execCommand
    Document.prototype.execCommand = function execCommand(commandId: string) {
      if (commandId.toLowerCase() === 'copy') {
        const active = document.activeElement
        const copied = active instanceof HTMLTextAreaElement
          ? active.value.slice(active.selectionStart, active.selectionEnd)
          : window.getSelection()?.toString() || ''
        sessionStorage.setItem('fallback-copied-text', copied)
        return Boolean(copied)
      }
      return originalExecCommand?.call(this, commandId) ?? false
    }
  }, { instrumentFallback: !realHttp })

  await page.route('**/api/v1/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/api-hub/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'GET' && path === '/api/api-hub/interfaces') {
      await route.fulfill({ json: [responseInterface] })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/interfaces/9') {
      await route.fulfill({ json: responseInterface })
      return
    }
    if (request.method() === 'POST' && path === '/api/api-hub/interfaces/preview-run/raw') {
      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'X-Api-Hub-Upstream': '1',
          'X-Api-Hub-Run-Id': '43',
          'X-Api-Hub-Elapsed-Ms': '12',
          'X-Api-Hub-Relogin': '0',
        },
        body: JSON.stringify({ message: 'copied over http', ok: true }),
      })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/credential/status') {
      await route.fulfill({ json: {
        configured: false,
        has_session: false,
        expired: false,
        expires_at: null,
        acquired_at: null,
        last_result: null,
        message: '',
        refreshed_at: null,
        cron: '0 */2 * * *',
        next_run: null,
        username: '',
        credential_source: 'environment',
      } })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/proxy/info') {
      await route.fulfill({ json: {
        path: '/proxy',
        key_header: 'X-API-Hub-Key',
        port: 8000,
        key_count: 0,
        published: [],
      } })
      return
    }
    await route.fulfill({ json: {} })
  })

  await page.goto('/#/api-hub/interfaces')
  if (realHttp) {
    await expect.poll(() => page.evaluate(() => ({
      secure: window.isSecureContext,
      clipboardAvailable: Boolean(navigator.clipboard?.writeText),
    }))).toEqual({ secure: false, clipboardAvailable: false })
  }
  await expect(page.getByText('HTTP 响应复制测试').first()).toBeVisible()
  await page.getByRole('button', { name: '调用', exact: true }).click()
  await expect(page.getByText('copied over http')).toBeVisible()

  const copyButton = page.getByTitle('复制响应内容')
  await copyButton.click()
  await expect(copyButton).toContainText('已复制')
  if (realHttp) {
    await page.evaluate(() => {
      const probe = document.createElement('textarea')
      probe.id = 'clipboard-paste-probe'
      document.body.appendChild(probe)
    })
    const probe = page.locator('#clipboard-paste-probe')
    await probe.focus()
    await probe.press(process.platform === 'darwin' ? 'Meta+V' : 'Control+V')
    await expect(probe).toHaveValue(expectedCopy)
    await probe.evaluate(element => element.remove())
  } else {
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('fallback-copied-text')))
      .toBe(expectedCopy)
    await expect(copyButton).toBeFocused()
  }
  await expect(page.locator('textarea[aria-hidden="true"]')).toHaveCount(0)
})
