import { expect, test, type Page, type Route } from '@playwright/test'


const now = '2026-07-21T08:00:00+00:00'

const json = (route: Route, data: unknown) => route.fulfill({
  status: 200,
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

test('MCP 列表失败时仍加载文本模型并允许输入', async ({ page }) => {
  await authenticate(page)
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/models') return json(route, [{
      id: 'model-1',
      name: 'Available model',
      config_type: 'llm',
      provider: 'openai',
      api_base: 'https://example.com',
      has_api_key: true,
      enabled: true,
      is_default: true,
      last_test_status: 'success',
      last_tested_at: now,
      last_test_message: 'ok',
      models: ['available-model'],
      options: {},
      created_by: 'admin',
      created_at: now,
      updated_at: now,
    }])
    return route.fulfill({ status: 404, body: '{}' })
  })
  await page.route('**/api/v2/super-assistant/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v2/super-assistant/conversations') return json(route, [])
    if (path === '/api/v2/super-assistant/skills') return json(route, [])
    if (path === '/api/v2/super-assistant/mcp-servers') {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' }),
      })
    }
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.goto('/#/super-assistant')

  const composer = page.getByRole('textbox', { name: '向超级助手发送消息' })
  await expect(composer).toBeEnabled()
  await expect(composer).toHaveAttribute('placeholder', '输入消息；Shift + Enter 换行')
  await expect(page.getByText('超级助手部分功能加载失败')).toBeVisible()
  await expect(page.getByText(/MCP：Internal Server Error/)).toBeVisible()
})
