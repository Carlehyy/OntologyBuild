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
      spans: [
        {
          seq: 1,
          layer: 'db',
          name: 'SELECT',
          target: 'domains',
          start_ms: 5,
          duration_ms: 210,
          status: '',
          detail: 'SELECT domains.name FROM domains WHERE domains.id = ?',
        },
        {
          seq: 2,
          layer: 'llm',
          name: 'chat.completions',
          target: 'openai/deepseek-chat',
          start_ms: 220,
          duration_ms: 3800,
          status: 'success',
          detail: '',
        },
      ],
      spans_truncated: false,
    },
    {
      id: 2,
      created_at: '2026-08-12T09:01:00Z',
      method: 'GET',
      route: '/api/v1/ontologies',
      status_code: 200,
      duration_ms: 1500,
      request_id: 'f1e2d3c4b5a697887766554433221100',
      username: 'admin',
      source_ip: '10.0.0.8',
      user_agent: 'Mozilla/5.0',
      breakdown: { db: { count: 9, total_ms: 130 } },
      spans: [],
      spans_truncated: false,
    },
  ],
  total: 2,
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

  test('慢请求展开调用链抽屉展示时间轴与明细', async ({ page }) => {
    await mockMonitoring(page)
    await page.goto('/#/settings/monitoring')

    await page.getByRole('button', { name: '调用链' }).first().click()
    await expect(page.getByText('调用链时间轴（相对请求开始）')).toBeVisible()
    await expect(page.getByText('chat.completions')).toBeVisible()
    await expect(page.getByText('openai/deepseek-chat')).toBeVisible()
    await expect(page.getByText(/未归因耗时/)).toBeVisible()
    await expect(page.getByText(/已归因 4.01s/)).toBeVisible()

    // 展开 span 明细行查看截断后的 SQL 文本（跳过无明细行的占位图标）
    const drawer = page.locator('.ant-drawer')
    await drawer
      .locator('.ant-table-row-expand-icon:not(.ant-table-row-expand-icon-spaced)')
      .first()
      .click()
    await expect(drawer.getByText(/SELECT domains.name FROM domains/)).toBeVisible()
  })

  test('复制分析提示词生成完整分析请求并提示成功', async ({ page }) => {
    await mockMonitoring(page)
    await page.goto('/#/settings/monitoring')

    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.getByRole('button', { name: '调用链' }).first().click()
    await expect(page.getByText('调用链时间轴（相对请求开始）')).toBeVisible()

    await page.getByRole('button', { name: '复制分析提示词' }).click()

    // 兜底弹窗：始终展示提示词全文，文本框自动全选，可手动复制或下载
    const modal = page.locator('.ant-modal')
    await expect(modal.getByText('慢请求分析提示词')).toBeVisible()
    const textarea = modal.locator('textarea')
    await expect(textarea).toHaveValue(/# 平台慢接口调用链分析请求/)
    const selection = await textarea.evaluate((element: HTMLTextAreaElement) => ({
      start: element.selectionStart,
      end: element.selectionEnd,
      length: element.value.length,
    }))
    expect(selection.start).toBe(0)
    expect(selection.end).toBeGreaterThan(0)
    await expect(modal.getByRole('button', { name: '下载 .md 文件' })).toBeVisible()

    // z 层级契约：抽屉 z-index 1000，toast 必须位于其上方（此前被遮挡却仍判
    // 可见，靠 elementFromPoint 命中检测才能真正防止回归）
    const toastOnTop = await page.evaluate(() => {
      const title = [...document.querySelectorAll('body *')].find(
        element => element.textContent === '已尝试写入剪贴板' && element.children.length === 0,
      )
      if (!title) return false
      let card: Element | null = title
      while (card && getComputedStyle(card).position !== 'fixed') {
        card = card.parentElement
      }
      if (!card) return false
      const rect = card.getBoundingClientRect()
      const hit = document.elementFromPoint(
        rect.x + rect.width / 2,
        rect.y + rect.height / 2,
      )
      return Boolean(hit && card.contains(hit))
    })
    await expect(toastOnTop).toBe(true)

    const copied = await page.evaluate(() => navigator.clipboard.readText())
    await expect(copied).toContain('# 平台慢接口调用链分析请求')
    await expect(copied).toContain('/api/v2/super-assistant/conversations/x/chat')
    await expect(copied).toContain('chat.completions')
    await expect(copied).toContain('SELECT domains.name FROM domains')
    await expect(copied).toContain('未归因耗时')
    await expect(copied).toContain('瓶颈定位')
  })

  test('旧版本慢请求标记为历史记录并解释原因', async ({ page }) => {
    await mockMonitoring(page)
    await page.goto('/#/settings/monitoring')

    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    await expect(page.getByRole('button', { name: /历史记录/ }).first()).toBeVisible()
    await page.getByRole('button', { name: /历史记录/ }).first().click()

    const drawer = page.locator('.ant-drawer')
    await expect(
      drawer.getByText(/该请求记录于调用链功能上线前的旧版本/),
    ).toBeVisible()
    await expect(drawer.getByText('DB 9 次')).toBeVisible()
    // 历史记录同样可以生成提示词（说明缺失调用链）
    await drawer.getByRole('button', { name: '复制分析提示词' }).click()
    const modal = page.locator('.ant-modal')
    await expect(modal.getByText('慢请求分析提示词')).toBeVisible()
    await expect(modal.locator('textarea')).toHaveValue(/未采集到逐步调用链/)
  })
})

