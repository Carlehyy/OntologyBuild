import { expect, test, type Page, type Route } from '@playwright/test'

type MockTask = Record<string, unknown>

async function mockTaskPool(page: Page, tasks: MockTask[] = []) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (url.pathname === '/api/v2/pipeline-tasks') {
      return ok({ total: tasks.length, items: tasks, page: 1, page_size: 10 })
    }
    if (url.pathname === '/api/v2/pipeline-tasks/stats') {
      return ok({
        total: tasks.length, running: 0, success: 0, idle: tasks.length,
        enabled: tasks.filter(task => task.enabled).length, failed: 0,
        today_runs: 0, today_errors: 0, total_runs: 0, total_errors: 0,
        trend_7d: [
          { date: '2026-07-12', runs: 0, errors: 0 },
          { date: '2026-07-13', runs: 0, errors: 0 },
          { date: '2026-07-14', runs: 0, errors: 0 },
          { date: '2026-07-15', runs: 0, errors: 0 },
          { date: '2026-07-16', runs: 0, errors: 0 },
          { date: '2026-07-17', runs: 0, errors: 0 },
          { date: '2026-07-18', runs: 0, errors: 0 },
        ],
        recent_runs: [],
      })
    }
    if (url.pathname === '/api/v2/pipeline-tasks/pipeline-options') return ok({ items: [] })
    if (url.pathname === '/api/v2/pipeline-tasks/selectable-pipelines') {
      return ok({
        total: 1,
        items: [{
          id: 'pipeline-orders',
          name: '订单标准化流水线',
          version: 3,
          domain: '供应链',
          status: 'published',
          total_rows: 0,
          curated_datasets: [],
          contract: {
            primary_key: 'order_id',
            columns: [
              { name: 'order_id', field_name: '订单编号', type: 'string', is_primary_key: true, nullable: false },
              { name: 'customer_name', field_name: '客户名称', type: 'string', is_primary_key: false, nullable: false },
              { name: 'amount', field_name: '订单金额', type: 'float', is_primary_key: false, nullable: true },
            ],
          },
        }],
      })
    }
    return ok([])
  })
}

test('任务池空状态、侧栏比例与新建任务字段契约完整展示', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1728, height: 1000 })
  await mockTaskPool(page)
  await page.goto('/#/data/pipelines/sync-tasks', { waitUntil: 'domcontentloaded' })

  const emptyState = page.getByTestId('task-empty-state')
  await expect(emptyState).toBeVisible()
  expect((await emptyState.boundingBox())?.height).toBeGreaterThan(300)
  await expect(page.getByText('最近执行记录')).toBeVisible()
  expect((await page.getByTestId('seven-day-chart').boundingBox())?.height).toBeGreaterThan(150)

  const filterIndicator = page.getByTestId('task-filter-indicator')
  const initialIndicatorX = (await filterIndicator.boundingBox())?.x ?? 0
  await page.getByRole('button', { name: '运行中', exact: true }).click()
  await expect.poll(async () => (await filterIndicator.boundingBox())?.x ?? 0).toBeGreaterThan(initialIndicatorX)
  await page.getByRole('button', { name: '全部', exact: true }).click()

  await page.getByRole('button', { name: '新建第一个任务' }).click()
  const modal = page.getByTestId('pipeline-task-modal')
  await expect(modal).toBeVisible()

  // 点击遮罩空白处不会误关弹窗。
  await page.mouse.click(8, 8)
  await expect(modal).toBeVisible()

  await page.getByRole('button', { name: '下一步' }).click()
  await expect(page.getByText('请填写任务名称')).toBeVisible()
  await page.getByLabel('任务名称').fill('订单每日入湖')
  await page.getByRole('button', { name: '下一步' }).click()
  await expect(page.getByText('请填写任务描述')).toBeVisible()
  await page.getByLabel('任务描述').fill('每天同步订单数据并写入资产湖')
  await page.getByRole('button', { name: '下一步' }).click()

  await page.getByRole('button', { name: /订单标准化流水线/ }).click()
  await expect(page.getByText('完整字段契约')).toBeVisible()
  await expect(page.getByText('订单编号')).toBeVisible()
  await expect(page.getByText('客户名称')).toBeVisible()
  await expect(page.getByText('订单金额')).toBeVisible()
  await expect(page.getByText('主键', { exact: true })).toBeVisible()
  await expect(page.getByText('非空', { exact: true })).toHaveCount(2)
  await page.screenshot({ path: testInfo.outputPath('task-modal-schema.png'), fullPage: true })
})

test('启停状态与执行状态分列展示，不再出现已启用后跟待运行', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1728, height: 1000 })
  await mockTaskPool(page, [{
    id: 'task-orders',
    name: '订单每日入湖',
    description: '每天同步订单数据并写入资产湖',
    pipeline_id: 'pipeline-orders',
    pipeline_name: '订单标准化流水线',
    pipeline_status: 'published',
    pipeline_enabled: true,
    pipeline_version: 3,
    write_mode: 'overwrite',
    primary_key: 'order_id',
    soft_delete_column: '',
    skip_empty: true,
    schedule_type: 'CRON',
    cron_expression: '0 2 * * *',
    interval_seconds: 0,
    enabled: true,
    status: 'idle',
    last_run_at: null,
    next_run_at: '2026-07-19T02:00:00Z',
    last_rows: 0,
    last_error: '',
    created_at: '2026-07-18T02:00:00Z',
    updated_at: '2026-07-18T02:00:00Z',
  }])
  await page.goto('/#/data/pipelines/sync-tasks', { waitUntil: 'domcontentloaded' })

  const row = page.getByRole('row').filter({ hasText: '订单每日入湖' })
  await expect(row).toContainText('已启用')
  await expect(row).toContainText('尚未执行')
  await expect(row).not.toContainText('待运行')
  await expect(page.getByRole('columnheader', { name: '入库策略' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '调度计划' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '启停' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('task-table-status.png'), fullPage: true })
})
