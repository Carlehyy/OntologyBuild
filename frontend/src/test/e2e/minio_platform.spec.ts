import { expect, test, type Page, type Route } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

const now = '2026-07-20T08:00:00+00:00'

const authenticate = async (page: Page) => {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
  await page.getByLabel('密码', { exact: true }).fill(STACK_ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/#/overview')
}

const json = (route: Route, data: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})


test('管理员测试并保存 MinIO 后只显示一次外部 MCP 配置', async ({ page }) => {
  await authenticate(page)
  let connected = false
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/settings/rules') return json(route, [])
    if (path === '/api/v1/settings/minio-config' && route.request().method() === 'GET') return json(route, {
      enabled: connected,
      endpoint: connected ? 'minio.example:9000' : '',
      secure: false,
      region: 'us-east-1',
      default_bucket: 'openontology',
      has_access_key: connected,
      has_secret_key: connected,
      read_enabled: true,
      write_enabled: true,
      delete_enabled: false,
      mcp_enabled: true,
      has_mcp_token: connected,
      mcp_token_hint: connected ? 'abc123' : '',
      connected,
      last_test_status: connected ? 'success' : null,
      last_test_message: connected ? '连接成功' : null,
      last_tested_at: connected ? now : null,
      mcp_path: '/mcp/minio',
    })
    if (path === '/api/v1/settings/minio-config/test') {
      connected = true
      return json(route, {
        ok: true,
        message: '连接成功，可访问 2 个 Bucket',
        endpoint: 'minio.example:9000',
        bucket_count: 2,
        default_bucket_ready: true,
        mcp_path: '/mcp/minio',
        mcp_token: 'one-time-mcp-token',
      })
    }
    return route.continue()
  })

  await page.goto('/#/settings/minio')
  await expect(page.getByRole('heading', { name: 'MinIO 对象存储' })).toBeVisible()
  await page.getByLabel('S3 API 端点').fill('http://minio.example:9000')
  await page.getByLabel('Access Key').fill('admin-access')
  await page.getByLabel('Secret Key').fill('admin-secret')
  await page.getByRole('button', { name: '测试连接并保存' }).click()

  await expect(page.getByText('已连接', { exact: true })).toBeVisible()
  await expect(page.getByText('连接成功，可访问 2 个 Bucket')).toBeVisible()
  const config = page.locator('pre').filter({ hasText: 'one-time-mcp-token' })
  await expect(config).toBeVisible()
  await expect(config).toContainText('/mcp/minio')
  await expect(page.getByText('admin-secret')).toHaveCount(0)
})


test('超级助手配置隐藏平台内置 MinIO，只展示可用的外部 MCP', async ({ page }) => {
  await authenticate(page)
  const builtin = {
    id: 'minio-mcp-1',
    name: 'platform_minio',
    builtin_key: 'minio',
    transport: 'streamable_http',
    url: 'builtin://minio',
    header_names: [],
    command: null,
    args: [],
    env_names: [],
    enabled: true,
    require_confirmation: true,
    tool_manifest: Array.from({ length: 13 }, (_, index) => ({
      name: `minio_tool_${index + 1}`,
      description: 'MinIO tool',
      input_schema: { type: 'object', properties: {} },
    })),
    last_test_status: 'success',
    last_test_message: '平台内置连接成功，发现 13 个工具',
    last_tested_at: now,
    created_at: now,
    updated_at: now,
  }
  const external = {
    ...builtin,
    id: 'external-mcp-1',
    name: 'api-hub',
    builtin_key: null,
    url: 'https://api.example.com/mcp',
    tool_manifest: builtin.tool_manifest.slice(0, 2),
    last_test_message: '连接成功，发现 2 个工具',
  }
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/models') return json(route, [])
    return route.continue()
  })
  await page.route('**/api/v2/super-assistant/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v2/super-assistant/conversations') return json(route, [])
    if (path === '/api/v2/super-assistant/skills') return json(route, [])
    if (path === '/api/v2/super-assistant/mcp-servers') return json(route, [builtin, external])
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.goto('/#/super-assistant')
  await page.getByRole('button', { name: '打开助手配置' }).click()
  await page.getByRole('button', { name: /^MCP/ }).click()

  await expect(page.getByRole('button', { name: 'MCP 1' })).toBeVisible()
  await expect(page.getByText('api-hub', { exact: true })).toBeVisible()
  await expect(page.getByText('2 tools', { exact: true })).toBeVisible()
  await expect(page.getByText('platform_minio', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /平台 MinIO/ })).toHaveCount(0)
  await expect(page.getByText('MCP Servers', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/保存后测试连接/)).toHaveCount(0)
})
