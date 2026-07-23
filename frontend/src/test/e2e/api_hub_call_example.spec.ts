import { expect, test } from '@playwright/test'

const exampleInterface = {
  id: 11,
  name: '订单详情',
  description: '查询指定订单详情',
  group_name: '订单服务',
  method: 'GET',
  url: 'https://vendor.example/v1/orders',
  query_params: [{ key: 'order_id', value: 'A-1024' }],
  headers: [{ key: 'Accept', value: 'application/json' }],
  body_type: 'none',
  body_content: '',
  file_fields: [],
  use_w3: true,
  mcp_enabled: false,
  open_enabled: false,
  http_enabled: false,
  proxy_slug: '',
  proxy_query_keys: [],
  proxy_header_keys: [],
  proxy_body_enabled: false,
  proxy_body_keys: [],
  parameter_schema: [],
}

test('调用示例以导出 cURL 弹窗展示并可选择登录 Cookie 后复制', async ({ page, context }, testInfo) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.addInitScript(() => {
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
  })

  await page.route('**/api/v1/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/api-hub/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'GET' && path === '/api/api-hub/interfaces') {
      await route.fulfill({ json: [exampleInterface] })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/credential/cookie-header') {
      await route.fulfill({ json: { cookie: 'session_id=test-cookie', count: 1 } })
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
  await expect(page.getByText('订单详情').first()).toBeVisible()
  await page.getByRole('button', { name: '调用示例' }).click()

  const dialog = page.getByRole('dialog', { name: '导出 cURL' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('复制到终端直接运行，CMD / PowerShell / bash 通用。')).toBeVisible()
  await expect(dialog.getByLabel('cURL 命令')).toContainText('order_id=A-1024')

  const includeCookie = dialog.getByRole('checkbox', { name: /包含当前登录 Cookie/ })
  await expect(includeCookie).toBeEnabled()
  await includeCookie.check()
  await expect(dialog.getByLabel('cURL 命令')).toContainText("session_id=test-cookie")

  await dialog.getByRole('button', { name: '复制', exact: true }).click()
  await expect(dialog.getByRole('button', { name: '已复制', exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain('session_id=test-cookie')
  await page.screenshot({ path: testInfo.outputPath('call-example-dialog.png'), fullPage: true })
})
