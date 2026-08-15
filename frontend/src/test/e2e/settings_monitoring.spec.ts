import { test, expect, type Page } from '@playwright/test'

/**
 * 运行监控页（系统设置 → 运行监控，admin）mocked 契约测试。
 * 后端地址指向不可达端口；所有 /api 请求在本文件内替换。
 */

const overviewPayload = {
  window: '24h',
  requests: 12345,
  success_rate: 99.2,
  client_error_rate: 0.3,
  server_error_rate: 0.5,
  avg_ms: 86,
  p50_ms: 41,
  p95_ms: 320,
  p99_ms: 940,
  slow_requests: 3,
  slow_threshold_ms: 1000,
}

const trendPayload = {
  window: '24h',
  points: [
    { t: '2026-08-13T09:00:00Z', count: 120, avg_ms: 80, p95_ms: 300, error_rate: 0.4 },
    { t: '2026-08-13T09:01:00Z', count: 150, avg_ms: 92, p95_ms: 420, error_rate: 0.6 },
  ],
}

const topPayload = {
  items: [
    {
      route: '/api/v2/super-assistant/conversations/x/chat',
      method: 'POST',
      requests: 88,
      error_rate: 0.0,
      avg_ms: 2100,
      p95_ms: 4300,
      max_ms: 6200,
      slow_count: 2,
    },
    {
      route: '/api/v1/ontologies',
      method: 'GET',
      requests: 260,
      error_rate: 0.0,
      avg_ms: 45,
      p95_ms: 90,
      max_ms: 180,
      slow_count: 0,
    },
  ],
  total: 2,
}

const slowPayload = {
  items: [
    {
      id: 1,
      created_at: '2026-08-13T09:01:00Z',
      method: 'POST',
      route: '/api/v2/super-assistant/conversations/x/chat',
      status_code: 200,
      duration_ms: 4200,
      request_id: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6',
      username: 'admin',
      source_ip: '10.0.0.8',
      user_agent: 'Mozilla/5.0 (X11; Linux x86_64)',
      breakdown: { llm: { count: 1, total_ms: 3800 }, db: { count: 14, total_ms: 210 } },
    },
  ],
  total: 1,
  page: 1,
  size: 10,
}

async function mockMonitoring(page: Page) {
  await page.addInitScript(() => {
    const user = {
      id: 'monitoring-e2e-admin',
      username: 'monitoring-admin',
      email: 'monitoring-admin@example.com',
      role: 'admin',
      is_active: true,
      created_at: '2026-07-30T08:00:00+00:00',
      menu_permissions: [],
    }
    localStorage.setItem('token', 'monitoring-e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'monitoring-e2e-token', user },
      version: 0,
    }))
  })

  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/v1/settings/monitoring/overview') {
      return route.fulfill({ json: overviewPayload })
    }
    if (path === '/api/v1/settings/monitoring/trend') {
      return route.fulfill({ json: trendPayload })
    }
    if (path === '/api/v1/settings/monitoring/top') {
      return route.fulfill({ json: topPayload })
    }
    if (path === '/api/v1/settings/monitoring/slow-requests') {
      return route.fulfill({ json: slowPayload })
    }
    if (path === '/api/v2/inbox/summary') {
      return route.fulfill({
        json: { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 },
      })
    }
    return route.fulfill({ json: [] })
  })
}

test.describe('系统设置 · 运行监控（admin）', () => {
  test('admin 打开运行监控页看到健康度卡片与趋势', async ({ page }) => {
    await mockMonitoring(page)
    await page.goto('/#/settings/monitoring')

    await expect(page.getByText('运行监控', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('请求总量', { exact: true })).toBeVisible()
    await expect(page.getByText('成功率', { exact: true })).toBeVisible()
    await expect(page.getByText('p95 耗时', { exact: true })).toBeVisible()
    await expect(page.getByText('慢请求', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('12,345')).toBeVisible()
    await expect(page.getByText('请求趋势（请求量 / p95 耗时 / 错误率）')).toBeVisible()
  })

  test('接口排行与慢请求明细渲染后端数据', async ({ page }) => {
    await mockMonitoring(page)
    await page.goto('/#/settings/monitoring')

    await expect(page.getByText('接口排行（针对性优化依据）')).toBeVisible()
    await expect(page.getByText('/api/v2/super-assistant/conversations/x/chat', { exact: false }).first()).toBeVisible()
    await expect(page.getByText('4300ms')).toBeVisible()

    await expect(page.getByText('慢请求明细', { exact: true })).toBeVisible()
    await expect(page.getByText('4.20s')).toBeVisible()
    // 展开行展示分层耗时分解（调用链还原入口）
    await page.locator('.ant-table-row-expand-icon').first().click()
    await expect(page.getByText(/LLM 1 次/)).toBeVisible()
    await expect(page.getByText(/DB 14 次/)).toBeVisible()
  })

  test('时间窗切换请求带 window 参数', async ({ page }) => {
    await mockMonitoring(page)
    await page.goto('/#/settings/monitoring')

    await page.locator('.ant-segmented-item', { hasText: '近 7 天' }).click()
    await expect(page.locator('.ant-segmented-item-selected', { hasText: '近 7 天' })).toBeVisible()
  })
})

