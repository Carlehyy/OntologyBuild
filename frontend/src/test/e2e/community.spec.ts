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

  await expect(community).toHaveAttribute('aria-expanded', 'true')
  await community.click()
  await expect(community).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('link', { name: '插件社区' })).toHaveCount(0)
  await community.click()
  await expect(community).toHaveAttribute('aria-expanded', 'true')

  await page.getByRole('link', { name: '插件社区' }).click()
  await expect(page).toHaveURL(/#\/community\/plugins$/)
  await expect(page.getByRole('heading', { name: '插件社区' })).toBeVisible()
  await expect(page.locator('table').getByText('weather_tools', { exact: true })).toBeVisible()
  await expect(page.getByText('platform_minio', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '添加平台 MinIO' })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: '开放状态' })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: '执行策略' })).toHaveCount(0)

  const serverList = page.getByRole('region', { name: 'MCP Server 清单' })
  const serverListBox = await serverList.boundingBox()
  const viewportHeight = await page.evaluate(() => window.innerHeight)
  expect(serverListBox).not.toBeNull()
  expect(serverListBox!.height).toBeGreaterThan(500)
  expect(viewportHeight - serverListBox!.y - serverListBox!.height).toBeGreaterThanOrEqual(20)
  expect(viewportHeight - serverListBox!.y - serverListBox!.height).toBeLessThanOrEqual(28)
  await expect(page.getByTestId('mcp-server-stats')).toHaveCSS('justify-content', 'center')

  await page.getByRole('button', { name: '测试 MCP weather_tools' }).click()
  await expect(page.locator('table').getByText('已通过', { exact: true })).toBeVisible()
  await expect(page.locator('table').getByText('共 1 个', { exact: true })).toBeVisible()
  await expect(page.getByRole('switch')).toHaveCount(0)

  await page.getByRole('button', { name: '添加 MCP', exact: true }).click()
  await expect(page.getByText('开放到超级助手', { exact: true })).toHaveCount(0)
  await expect(page.getByText('每次工具调用前要求确认', { exact: true })).toHaveCount(0)
  await page.getByLabel('MCP 客户端 JSON').fill(`{
    // VS Code mcp.json
    "servers": {
      "Remote Docs": {
        "type": "http",
        "url": "https://docs.example.com/mcp",
      },
      "local-tools": {
        "type": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
      },
    },
  }`)
  await page.getByRole('button', { name: '解析并填入下方表单' }).click()
  await expect(page.getByLabel('选择要填入的 MCP Server')).toBeVisible()
  await expect(page.getByLabel(/名称/)).toHaveValue('Remote-Docs')
  await expect(page.getByLabel(/传输方式/)).toHaveValue('streamable_http')
  await expect(page.getByLabel(/MCP URL/)).toHaveValue('https://docs.example.com/mcp')
  await page.getByLabel('选择要填入的 MCP Server').selectOption('1')
  await expect(page.getByLabel(/传输方式/)).toHaveValue('stdio')
  await expect(page.getByLabel(/^command/)).toHaveValue('uvx')
  await page.getByLabel('选择要填入的 MCP Server').selectOption('0')
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
