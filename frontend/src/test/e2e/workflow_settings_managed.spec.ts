import { expect, test, type Page, type Route } from '@playwright/test'


const json = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

async function authenticate(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'admin', username: 'admin', email: 'admin@example.com', role: 'admin' },
      },
      version: 0,
    }))
  })
}

test('n8n 设置只读展示启动配置并只允许连接测试', async ({ page }) => {
  await authenticate(page)
  let testBody: Record<string, unknown> | null = null

  await page.route('**/api/v2/inbox/summary', route => json(route, {
    openAlertCount: 0,
    actionableCount: 0,
    unreadCount: 0,
    resolvedCount: 0,
  }))
  await page.route('**/api/v1/settings/rules', route => json(route, []))
  await page.route('**/api/v1/settings/workflow-config', async route => {
    const request = route.request()
    if (request.method() === 'GET') {
      return json(route, {
        enabled: true,
        api_url: 'https://n8n.example.test/api/v1',
        has_api_key: true,
        timeout_seconds: 30,
      })
    }
    return route.fulfill({ status: 409, body: '{}' })
  })
  await page.route('**/api/v1/settings/workflow-config/test', async route => {
    testBody = route.request().postDataJSON() as Record<string, unknown>
    return json(route, {
      ok: true,
      message: 'n8n 环境托管配置连接成功',
      api_base: 'https://n8n.example.test/api/v1',
    })
  })

  await page.goto('/#/settings/workflows')

  await expect(page.getByRole('heading', { name: '工作流配置' })).toBeVisible()
  await expect(page.getByText(/启动环境\/配置中心托管/)).toBeVisible()
  await expect(page.getByLabel('启用工作流集成')).toBeDisabled()
  await expect(page.getByLabel('n8n API 地址')).toHaveAttribute('readonly', '')
  await expect(page.getByLabel('n8n API 地址')).toHaveValue('https://n8n.example.test/api/v1')
  await expect(page.getByLabel('n8n API Key')).toHaveAttribute('readonly', '')
  await expect(page.getByLabel('n8n API Key')).toHaveValue('')
  await expect(page.getByLabel('n8n API Key')).toHaveAttribute(
    'placeholder',
    '已由启动环境提供（内容不返回）',
  )
  await expect(page.getByLabel('请求超时（秒）')).toHaveAttribute('readonly', '')
  await expect(page.getByRole('button', { name: /保存/ })).toHaveCount(0)

  await page.getByRole('button', { name: '测试连接' }).click()
  await expect(page.getByText('n8n 环境托管配置连接成功')).toBeVisible()
  expect(testBody).toEqual({
    enabled: true,
    api_url: 'https://n8n.example.test/api/v1',
    api_key: '',
    timeout_seconds: 30,
  })
})
