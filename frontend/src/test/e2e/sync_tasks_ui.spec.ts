import { expect, test, type Page, type Route } from '@playwright/test'

type MockTask = Record<string, unknown>

async function mockTaskPool(page: Page, tasks: MockTask[] = [], recentRuns: MockTask[] = []) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })

  const historyItems = Array.from({ length: 24 }, (_, index) => ({
    id: `run-${String(index + 1).padStart(2, '0')}`,
    status: index % 3 === 0 ? 'failed' : 'success',
    trigger_type: index % 2 === 0 ? 'manual' : 'scheduled',
    started_at: `2026-07-${String(18 - Math.floor(index / 3)).padStart(2, '0')}T02:00:00Z`,
    finished_at: `2026-07-${String(18 - Math.floor(index / 3)).padStart(2, '0')}T02:00:05Z`,
    rows_in: 10,
    rows_out: 9,
    lake_rows: 18,
    write_mode: index % 2 === 0 ? 'overwrite' : 'append',
    skipped_outputs: [],
    curated_dataset_ids: ['dataset-orders'],
    lake_impact: { added: 2, updated: 1, deleted: 0 },
    config_snapshot: null,
    error_message: index % 3 === 0 ? '测试执行失败' : '',
  }))

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
    if (url.pathname === '/api/v2/pipeline-tasks/task-orders/histories') {
      const status = url.searchParams.get('status')
      const triggerType = url.searchParams.get('trigger_type')
      const pageNo = Number(url.searchParams.get('page') || 1)
      const pageSize = Number(url.searchParams.get('page_size') || 10)
      const filtered = historyItems.filter(run =>
        (!status || run.status === status) && (!triggerType || run.trigger_type === triggerType),
      )
      return ok({
        total: filtered.length,
        items: filtered.slice((pageNo - 1) * pageSize, pageNo * pageSize),
        page: pageNo,
        page_size: pageSize,
      })
    }
    if (/^\/api\/v2\/pipeline-tasks\/task-orders\/runs\/run-\d+\/audit$/.test(url.pathname)) {
      const runId = url.pathname.split('/').at(-2) || 'run-01'
      return ok({
        id: runId,
        task_id: 'task-orders',
        status: 'success',
        trigger_type: 'manual',
        started_at: '2026-07-18T02:00:00Z',
        finished_at: '2026-07-18T02:00:05Z',
        created_at: '2026-07-18T02:00:00Z',
        rows_in: 10,
        rows_out: 9,
        lake_rows: 18,
        write_mode: 'overwrite',
        lake_impact: { added: 2, updated: 1, deleted: 0 },
        config_snapshot: {
          write_mode: 'overwrite', schedule_type: 'CRON', cron_expression: '0 2 * * *', skip_empty: true,
        },
        pipeline: { id: 'pipeline-orders', name: '订单标准化流水线', version: 3, status: 'published', domain: '供应链' },
        outputs: [],
        error_message: '',
      })
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
        recent_runs: recentRuns,
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
  const recentRuns = Array.from({ length: 35 }, (_, index) => ({
    id: `recent-${index + 1}`,
    task_id: `task-${index + 1}`,
    task_name: `最近执行任务 ${index + 1}`,
    pipeline_name: '订单标准化流水线',
    status: 'success',
    trigger_type: 'scheduled',
    started_at: `2026-07-18T${String(23 - (index % 20)).padStart(2, '0')}:00:00Z`,
    finished_at: `2026-07-18T${String(23 - (index % 20)).padStart(2, '0')}:00:05Z`,
    rows_out: 10,
    lake_impact: { added: 10, updated: 0, deleted: 0 },
    error_message: '',
  }))
  await mockTaskPool(page, [], recentRuns)
  await page.goto('/#/data/pipelines/sync-tasks', { waitUntil: 'domcontentloaded' })

  const emptyState = page.getByTestId('task-empty-state')
  await expect(emptyState).toBeVisible()
  expect((await emptyState.boundingBox())?.height).toBeGreaterThan(300)
  await expect(page.getByText('最近执行记录')).toBeVisible()
  await expect(page.getByTestId('recent-run-item')).toHaveCount(30)
  const recentRunFeed = page.getByTestId('recent-run-feed')
  expect(await recentRunFeed.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await expect(recentRunFeed).toHaveCSS('scrollbar-width', 'none')
  await recentRunFeed.evaluate(element => { element.scrollTop = 120 })
  expect(await recentRunFeed.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  const sevenDayChart = page.getByTestId('seven-day-chart')
  const recentRunCard = page.getByTestId('recent-run-card')
  const taskListPanel = page.getByTestId('task-list-panel')
  const sevenDayChartBox = await sevenDayChart.boundingBox()
  const recentRunCardBox = await recentRunCard.boundingBox()
  const taskListPanelBox = await taskListPanel.boundingBox()
  const sevenDayChartHeight = sevenDayChartBox?.height ?? 0
  expect(sevenDayChartHeight).toBeGreaterThanOrEqual(200)
  expect(sevenDayChartHeight).toBeLessThanOrEqual(250)
  expect(sevenDayChartBox?.y ?? 0).toBeLessThan(recentRunCardBox?.y ?? 0)
  const recentRunBottom = (recentRunCardBox?.y ?? 0) + (recentRunCardBox?.height ?? 0)
  const taskListBottom = (taskListPanelBox?.y ?? 0) + (taskListPanelBox?.height ?? 0)
  expect(Math.abs(recentRunBottom - taskListBottom)).toBeLessThanOrEqual(1)
  await page.screenshot({ path: testInfo.outputPath('sidebar-order-alignment.png'), fullPage: true })

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

test('任务表格按优先级拆列、入库策略使用独立颜色并允许横向滚动', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1728, height: 1000 })
  const baseTask = {
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
  }
  await mockTaskPool(page, [
    baseTask,
    {
      ...baseTask,
      id: 'task-orders-append',
      name: '订单实时追加',
      description: '实时追加订单增量数据并保留历史记录',
      write_mode: 'append',
      schedule_type: 'MANUAL',
      cron_expression: '',
      next_run_at: null,
    },
  ])
  await page.goto('/#/data/pipelines/sync-tasks', { waitUntil: 'domcontentloaded' })

  const row = page.getByRole('row').filter({ hasText: '订单每日入湖' })
  await expect(row).toContainText('已启用')
  await expect(row).toContainText('尚未执行')
  await expect(row).not.toContainText('待运行')
  const headers = await page.getByRole('columnheader').allTextContents()
  expect(headers).toEqual([
    '任务名称', '运行状态', '启停', '关联流水线', '最近执行', '入湖结果',
    '下次执行', '调度方式', '入库策略', '调度规则', '任务描述', '操作',
  ])
  const tableScroll = page.getByTestId('task-table-scroll')
  expect(await tableScroll.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
  const headerAlignments = await page.getByRole('columnheader').evaluateAll(elements =>
    elements.map(element => getComputedStyle(element).textAlign),
  )
  expect(headerAlignments).toEqual(Array(headers.length).fill('center'))
  const cellAlignments = await row.locator('td').evaluateAll(elements =>
    elements.map(element => getComputedStyle(element).textAlign),
  )
  expect(cellAlignments).toEqual(Array(headers.length).fill('center'))
  const fixedName = row.locator('[data-column="task-name"]')
  const fixedActions = row.locator('[data-column="actions"]')
  await expect(fixedName).toHaveCSS('position', 'sticky')
  await expect(fixedActions).toHaveCSS('position', 'sticky')
  const fixedNameX = (await fixedName.boundingBox())?.x ?? 0
  const fixedActionsX = (await fixedActions.boundingBox())?.x ?? 0
  await expect(page.locator('[data-write-mode="overwrite"]')).toHaveClass(/emerald/)
  await expect(page.locator('[data-write-mode="append"]')).toHaveClass(/sky/)
  expect(await row.evaluate(element => getComputedStyle(element).whiteSpace)).toBe('nowrap')
  await page.screenshot({ path: testInfo.outputPath('task-table-status.png'), fullPage: true })
  await tableScroll.evaluate(element => { element.scrollLeft = element.scrollWidth })
  expect(Math.abs(((await fixedName.boundingBox())?.x ?? 0) - fixedNameX)).toBeLessThanOrEqual(1)
  expect(Math.abs(((await fixedActions.boundingBox())?.x ?? 0) - fixedActionsX)).toBeLessThanOrEqual(1)
  await page.screenshot({ path: testInfo.outputPath('task-table-write-modes.png'), fullPage: true })
})

test('编辑任务沿用五步向导，执行记录支持筛选分页且保留详情', async ({ page }, testInfo) => {
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
    status: 'success',
    last_run_at: '2026-07-18T02:00:00Z',
    next_run_at: '2026-07-19T02:00:00Z',
    last_rows: 9,
    last_error: '',
    created_at: '2026-07-18T02:00:00Z',
    updated_at: '2026-07-18T02:00:00Z',
  }])
  await page.goto('/#/data/pipelines/sync-tasks', { waitUntil: 'domcontentloaded' })

  await page.getByTitle('编辑').click()
  await expect(page.getByRole('heading', { name: '编辑调度任务' })).toBeVisible()
  await expect(page.getByLabel('任务名称')).toBeVisible()
  await expect(page.getByText('完整字段契约')).toBeHidden()
  await page.getByRole('button', { name: '下一步' }).click()
  await expect(page.getByLabel('任务名称')).toBeHidden()
  await expect(page.getByText('完整字段契约')).toBeVisible()
  await page.getByRole('button', { name: '关闭弹窗' }).click()

  await page.getByTitle('执行记录').click()
  const historyDrawer = page.getByTestId('execution-history-drawer')
  await expect(historyDrawer.getByRole('heading', { name: '执行记录', exact: true })).toBeVisible()
  await expect(page.getByText('点击任一记录可查看执行详情')).toBeVisible()

  const statusRequest = page.waitForRequest(request => {
    const url = new URL(request.url())
    return url.pathname.endsWith('/task-orders/histories') && url.searchParams.get('status') === 'success'
  })
  await page.getByLabel('执行状态筛选').selectOption('success')
  await statusRequest
  await expect(page.getByTestId('execution-record-run-02')).toBeVisible()

  await page.getByTestId('execution-record-run-02').click()
  await expect(page.getByText('执行信息')).toBeVisible()
  await expect(page.getByText('调用的流水线')).toBeVisible()

  await page.getByLabel('执行状态筛选').selectOption('')
  const secondPageRequest = page.waitForRequest(request => {
    const url = new URL(request.url())
    return url.pathname.endsWith('/task-orders/histories') && url.searchParams.get('page') === '2'
  })
  await page.getByRole('button', { name: '执行记录下一页' }).click()
  await secondPageRequest
  await expect(page.getByText('第 2 / 3 页')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('task-history-filters.png'), fullPage: true })
})
