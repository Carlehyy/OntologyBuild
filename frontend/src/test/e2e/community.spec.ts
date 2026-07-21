import { expect, test, type Page, type Route } from '@playwright/test'


const now = '2026-07-21T08:00:00+00:00'

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

test('开放社区导航、技能占位页与 MCP 完整生命周期可用', async ({ page }) => {
  await authenticate(page)
  let patchBodies: Array<Record<string, unknown>> = []
  let createBody: Record<string, unknown> | null = null
  let server = {
    id: 'mcp-1',
    name: 'weather_tools',
    builtin_key: null,
    transport: 'streamable_http',
    url: 'https://mcp.example.com/mcp',
    header_names: [],
    command: null,
    args: [],
    env_names: [],
    enabled: false,
    require_confirmation: true,
    tool_manifest: [] as Array<{ name: string; description: string; input_schema: Record<string, unknown> }>,
    last_test_status: null as 'success' | 'error' | null,
    last_test_message: null as string | null,
    last_tested_at: null as string | null,
    created_at: now,
    updated_at: now,
  }
  const builtinMinio = {
    ...server,
    id: 'builtin-minio',
    name: 'platform_minio',
    builtin_key: 'minio',
    url: 'builtin://minio',
    enabled: true,
  }
  const servers = () => [builtinMinio, server]

  await page.route('**/api/v2/community/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v2/community/mcp-servers' && request.method() === 'GET') {
      return json(route, servers())
    }
    if (path === '/api/v2/community/mcp-servers' && request.method() === 'POST') {
      createBody = request.postDataJSON() as Record<string, unknown>
      return json(route, {
        ...server,
        id: 'mcp-2',
        name: createBody.name,
        url: createBody.url,
        enabled: createBody.enabled,
      }, 201)
    }
    if (path === '/api/v2/community/mcp-servers/mcp-1/test' && request.method() === 'POST') {
      server = {
        ...server,
        last_test_status: 'success',
        last_test_message: '连接成功，发现 1 个工具',
        last_tested_at: now,
        tool_manifest: [{ name: 'get_forecast', description: '读取城市天气预报', input_schema: { type: 'object' } }],
      }
      return json(route, { ok: true, message: server.last_test_message, tools: server.tool_manifest })
    }
    if (path === '/api/v2/community/mcp-servers/mcp-1' && request.method() === 'PATCH') {
      const body = request.postDataJSON() as Record<string, unknown>
      patchBodies = [...patchBodies, body]
      server = { ...server, ...body }
      return json(route, server)
    }
    return route.fulfill({ status: 404, body: '{}' })
  })
  await page.route('**/api/v2/inbox/summary', route => json(route, { unread_count: 0 }))

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/community/skills')
  await expect(page.getByRole('heading', { name: '此功能正在修缮中，稍等片刻~' })).toBeVisible()

  const apiHub = page.getByRole('button', { name: '接口代理' })
  const community = page.getByRole('button', { name: '开放社区' })
  const models = page.getByRole('link', { name: '模型配置' })
  const [apiHubBox, communityBox, modelsBox] = await Promise.all([
    apiHub.boundingBox(),
    community.boundingBox(),
    models.boundingBox(),
  ])
  expect(apiHubBox).not.toBeNull()
  expect(communityBox).not.toBeNull()
  expect(modelsBox).not.toBeNull()
  expect(apiHubBox!.y).toBeLessThan(communityBox!.y)
  expect(communityBox!.y).toBeLessThan(modelsBox!.y)

  await page.getByRole('link', { name: '插件社区' }).click()
  await expect(page).toHaveURL(/#\/community\/plugins$/)
  await expect(page.getByRole('heading', { name: '插件社区' })).toBeVisible()
  await expect(page.locator('table').getByText('weather_tools', { exact: true })).toBeVisible()
  await expect(page.getByText('platform_minio', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '添加平台 MinIO' })).toHaveCount(0)

  await page.getByRole('button', { name: '测试 MCP weather_tools' }).click()
  await expect(page.locator('table').getByText('已通过', { exact: true })).toBeVisible()
  const openSwitch = page.getByRole('switch', { name: '开放 MCP weather_tools' })
  await expect(openSwitch).toBeEnabled()
  await openSwitch.click()
  await expect(page.getByRole('switch', { name: '停用 MCP weather_tools' })).toBeChecked()
  await page.getByRole('switch', { name: '停用 MCP weather_tools' }).click()
  await expect(page.getByRole('switch', { name: '开放 MCP weather_tools' })).not.toBeChecked()
  expect(patchBodies).toEqual([{ enabled: true }, { enabled: false }])

  await page.getByRole('button', { name: '添加 MCP', exact: true }).click()
  await page.getByLabel(/名称/).fill('knowledge_search')
  await page.getByLabel(/MCP URL/).fill('https://knowledge.example.com/mcp')
  await page.getByRole('button', { name: '保存' }).click()
  await expect.poll(() => createBody).not.toBeNull()
  expect(createBody).toMatchObject({
    name: 'knowledge_search',
    transport: 'streamable_http',
    url: 'https://knowledge.example.com/mcp',
    enabled: false,
    require_confirmation: true,
  })
})

test('插件社区在窄屏下不产生页面横向溢出', async ({ page }) => {
  await authenticate(page)
  await page.route('**/api/v2/community/mcp-servers', route => json(route, []))
  await page.route('**/api/v2/inbox/summary', route => json(route, { unread_count: 0 }))
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/#/community/plugins')
  await expect(page.getByRole('heading', { name: '插件社区' })).toBeVisible()
  await expect(page.getByRole('button', { name: '添加 MCP', exact: true })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})
