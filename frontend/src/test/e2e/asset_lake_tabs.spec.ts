import { expect, test, type Page, type Route } from '@playwright/test'

async function mockAssetLake(page: Page, fixtures?: {
  curated?: Array<Record<string, unknown>>
  pipelines?: Array<Record<string, unknown>>
}) {
  const curated = fixtures?.curated ?? []
  const pipelines = fixtures?.pipelines ?? []
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

    if (url.pathname === '/api/v2/curated/ds-reviewed/review-diff') {
      return ok({
        pk: ['order_id'],
        current: {
          version_no: 3,
          dataset_version_id: 'version-3',
          total: 2,
          rows: [
            { order_id: 'SO-001', amount: 1280 },
            { order_id: 'SO-002', amount: 760 },
          ],
          offset: 0,
          limit: 200,
          has_more: false,
        },
        previous: {
          version_no: 2,
          dataset_version_id: 'version-2',
          total: 0,
          rows: [],
          offset: 0,
          limit: 200,
          has_more: false,
        },
        delta: null,
        review: null,
      })
    }
    if (url.pathname === '/api/v2/datasets/ds-reviewed/schema') {
      return ok({
        dataset_id: 'ds-reviewed',
        columns: [
          { name: 'order_id', display_name: '订单编号', type: 'string', nullable: false, is_primary_key: true, sample_values: ['SO-001'] },
          { name: 'amount', display_name: '订单金额', type: 'integer', nullable: false, is_primary_key: false, sample_values: [1280] },
        ],
      })
    }
    if (url.pathname === '/api/v2/curated') {
      return ok(url.searchParams.get('paginated') === 'true'
        ? { items: curated, total: curated.length, page: 1, page_size: 10 }
        : curated)
    }
    if (url.pathname === '/api/v2/datasets/overview') {
      return ok({ items: [], total: 0, page: 1, page_size: 10 })
    }
    if (url.pathname === '/api/v2/pipelines') {
      return ok(url.searchParams.get('paginated') === 'true'
        ? { items: pipelines, total: pipelines.length, page: 1, page_size: 10 }
        : pipelines)
    }
    if (url.pathname === '/api/v2/pipeline-tasks') return ok({ items: [], total: 0 })
    return ok([])
  })
}

test('资产湖仅保留两个数据集入口，并复用滑动选中动画', async ({ page }) => {
  await mockAssetLake(page)
  await page.goto('/#/data/structured?tab=sync', { waitUntil: 'domcontentloaded' })

  const curatedTab = page.getByRole('button', { name: '成品数据集' })
  const manualTab = page.getByRole('button', { name: '人工数据集' })
  const indicator = page.getByTestId('asset-lake-tab-indicator')

  await expect(page).toHaveURL(/tab=curated/)
  await expect(curatedTab).toHaveAttribute('aria-pressed', 'true')
  await expect(manualTab).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('button', { name: '连接同步数据集' })).toHaveCount(0)
  await expect(indicator).toHaveCSS('transition-duration', '0.3s')

  const initialLeft = Number.parseFloat(await indicator.evaluate(element => (element as HTMLElement).style.left))
  await manualTab.click()

  await expect(page).toHaveURL(/tab=raw/)
  await expect(manualTab).toHaveAttribute('aria-pressed', 'true')
  await expect(curatedTab).toHaveAttribute('aria-pressed', 'false')
  await expect.poll(async () => Number.parseFloat(
    await indicator.evaluate(element => (element as HTMLElement).style.left),
  )).toBeGreaterThan(initialLeft)
})

test('成品数据集列表与详情按待办语义展示审核状态', async ({ page }) => {
  await mockAssetLake(page, {
    curated: [{
      id: 'ds-reviewed',
      name: '客户订单成品表',
      status: 'rejected',
      row_count: 2,
      quality_score: 0.96,
      primary_key: 'order_id',
      producer_pipeline_id: 'pipeline-reviewed',
      output_key: 'orders',
      has_review_evidence: true,
      created_at: '2026-07-17T09:00:00Z',
      updated_at: '2026-07-18T06:35:00Z',
    }],
    pipelines: [{
      id: 'pipeline-reviewed',
      name: '客户订单清洗流水线',
      domain: '零售',
      status: 'published',
      target_curated_ids: ['ds-reviewed'],
    }],
  })
  await page.goto('/#/data/structured?tab=curated', { waitUntil: 'domcontentloaded' })

  await expect(page.getByRole('cell', { name: '客户订单成品表' })).toBeVisible()
  await expect(page.getByText('ds-revie')).toHaveCount(0)
  await expect(page.getByRole('cell', { name: '已审核' })).toBeVisible()
  await expect(page.getByText(/2026.*07.*18.*14:35/)).toBeVisible()
  await expect(page.getByRole('button', { name: '查看' })).toBeVisible()
  await expect(page.getByRole('button', { name: '删除' })).toBeDisabled()
  await expect(page.getByRole('button', { name: /批准|拒绝|撤回/ })).toHaveCount(0)

  await page.getByRole('button', { name: /2 行/ }).click()
  const dialog = page.getByRole('dialog', { name: '客户订单成品表' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('当前没有新数据需要审核')).toBeVisible()
  await expect(dialog.getByText('已审核全量数据')).toBeVisible()
  await expect(dialog.getByText('订单编号（order_id）')).toBeVisible()
  await expect(dialog.getByText('订单金额（amount）')).toBeVisible()
  await expect(dialog.getByText('主键 · 非空')).toBeVisible()
  await expect(dialog.getByRole('button', { name: /通过审核|拒绝本次数据|撤回/ })).toHaveCount(0)
  await dialog.getByRole('button', { name: '关闭审核详情' }).click()

  await page.getByRole('button', { name: /客户订单清洗流水线/ }).click()
  await expect(page).toHaveURL(/data\/pipelines\?search=pipeline-reviewed/)
  await expect(page.getByPlaceholder('搜索名称 / ID...')).toHaveValue('pipeline-reviewed')
})
