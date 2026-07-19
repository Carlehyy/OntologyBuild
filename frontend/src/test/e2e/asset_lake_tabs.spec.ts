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

    if (url.pathname === '/api/v2/curated/ds-reviewed/export') {
      const format = url.searchParams.get('format')
      return route.fulfill({
        status: 200,
        contentType: format === 'xlsx'
          ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
          : 'text/csv; charset=utf-8',
        body: format === 'xlsx' ? 'mock-xlsx' : 'order_id,amount\nSO-001,1280\nSO-002,760\n',
      })
    }
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
    if (url.pathname === '/api/v2/curated/ds-pending-pk/review-diff') {
      const offset = Number(url.searchParams.get('offset') || 0)
      const limit = Number(url.searchParams.get('limit') || 50)
      return ok({
        pk: ['order_id'],
        current: {
          version_no: 6,
          dataset_version_id: 'version-6',
          total: 65,
          rows: [{ order_id: `SO-${offset + 1}`, amount: offset ? 880 : 1280, customer: '华东门店' }],
          offset,
          limit,
          has_more: offset + limit < 65,
        },
        previous: {
          version_no: 5,
          dataset_version_id: 'version-5',
          total: 62,
          rows: [{ order_id: `SO-${offset + 1}`, amount: offset ? 760 : 980, customer: '华东门店' }],
          offset,
          limit,
          has_more: offset + limit < 62,
        },
        delta: {
          keyed_by: ['order_id'],
          total_before: 62,
          total_after: 65,
          added_count: 1,
          updated_count: 1,
          deleted_count: 1,
          unchanged_count: 61,
          added_sample: [{ order_id: 'SO-065', amount: 520, customer: '华南门店' }],
          updated_sample: [{
            before: { order_id: 'SO-001', amount: 980, customer: '华东门店' },
            after: { order_id: 'SO-001', amount: 1280, customer: '华东门店' },
          }],
          deleted_sample: [{ order_id: 'SO-004', amount: 120, customer: '华北门店' }],
          sample_truncated: false,
        },
        review: {
          id: 'review-pending-pk', dataset_version_id: 'version-6', status: 'pending',
          stale: false, latest_dataset_version_id: 'version-6', latest_version_no: 6,
        },
      })
    }
    if (url.pathname === '/api/v2/curated/ds-pending-nopk/review-diff') {
      return ok({
        pk: [],
        current: {
          version_no: 2, dataset_version_id: 'version-no-pk-2', total: 2,
          rows: [{ body: '{"status":"new"}', webhookUrl: 'https://example.test/hook' }],
          offset: 0, limit: 50, has_more: false,
        },
        previous: {
          version_no: 1, dataset_version_id: 'version-no-pk-1', total: 2,
          rows: [{ body: '{"status":"old"}', webhookUrl: 'https://example.test/hook' }],
          offset: 0, limit: 50, has_more: false,
        },
        delta: {
          keyed_by: null,
          total_before: 2,
          total_after: 2,
          added_count: 1,
          updated_count: 0,
          deleted_count: 1,
          unchanged_count: 1,
          added_sample: [{ body: '{"status":"new"}', webhookUrl: 'https://example.test/hook' }],
          updated_sample: [],
          deleted_sample: [{ body: '{"status":"old"}', webhookUrl: 'https://example.test/hook' }],
          sample_truncated: false,
        },
        review: {
          id: 'review-pending-nopk', dataset_version_id: 'version-no-pk-2', status: 'pending',
          stale: false, latest_dataset_version_id: 'version-no-pk-2', latest_version_no: 2,
        },
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
    if (url.pathname === '/api/v2/datasets/ds-pending-pk/schema') {
      return ok({
        dataset_id: 'ds-pending-pk',
        columns: [
          { name: 'order_id', display_name: '订单编号', type: 'string', nullable: false, is_primary_key: true, sample_values: ['SO-001'] },
          { name: 'amount', display_name: '订单金额', type: 'integer', nullable: false, is_primary_key: false, sample_values: [1280] },
          { name: 'customer', display_name: '客户区域', type: 'string', nullable: true, is_primary_key: false, sample_values: ['华东门店'] },
        ],
      })
    }
    if (url.pathname === '/api/v2/datasets/ds-pending-nopk/schema') {
      return ok({
        dataset_id: 'ds-pending-nopk',
        columns: [
          { name: 'body', display_name: 'body', display_name_configured: true, type: 'json', nullable: true, is_primary_key: false, sample_values: [] },
          { name: 'webhookUrl', display_name: 'webhookUrl', display_name_configured: false, type: 'string', nullable: true, is_primary_key: false, sample_values: [] },
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
  const deleteButton = page.getByRole('button', { name: '删除' })
  await expect(deleteButton).toBeEnabled()
  await expect(page.getByRole('button', { name: /批准|拒绝|撤回/ })).toHaveCount(0)

  await deleteButton.click()
  await expect(page.getByText(/全部历史版本、审核记录和行级审核修改都将被永久删除/)).toBeVisible()
  await page.getByRole('button', { name: '取消' }).click()

  await page.getByRole('button', { name: /2 行/ }).click()
  const dialog = page.getByRole('dialog', { name: '客户订单成品表' })
  await expect(dialog).toBeVisible()
  await expect.poll(async () => (await dialog.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(520)
  await expect.poll(async () => (await dialog.boundingBox())?.width ?? 0).toBeGreaterThan(1000)
  await expect(dialog.getByText('当前没有新数据需要审核')).toBeVisible()
  await expect(dialog.getByText('已审核全量数据')).toBeVisible()
  await expect(dialog.getByText('订单编号（order_id）')).toBeVisible()
  await expect(dialog.getByText('订单金额（amount）')).toBeVisible()
  await expect(dialog.getByText('主键 · 非空')).toBeVisible()
  await expect(dialog.getByText('只读模式，不会修改数据')).toBeVisible()
  await expect(dialog.getByLabel('已审核数据每页显示条数')).toHaveValue('50')
  await expect(dialog.getByRole('button', { name: '导出 Excel' })).toBeVisible()
  await expect(dialog.getByRole('button', { name: /通过审核|拒绝本次数据|撤回/ })).toHaveCount(0)

  const downloadPromise = page.waitForEvent('download')
  await dialog.getByRole('button', { name: '导出 CSV' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('客户订单成品表.csv')
  await dialog.getByRole('button', { name: '关闭审核详情' }).click()

  await page.getByRole('button', { name: /客户订单清洗流水线/ }).click()
  await expect(page).toHaveURL(/data\/pipelines\?search=pipeline-reviewed/)
  await expect(page.getByPlaceholder('搜索名称 / ID...')).toHaveValue('pipeline-reviewed')
})

test('待审核详情提供三视角、分页、变更列标识和无主键说明', async ({ page }) => {
  await mockAssetLake(page, {
    curated: [
      {
        id: 'ds-pending-pk', name: '待审核订单成品表', status: 'pending_review',
        row_count: 65, quality_score: 0.91, primary_key: 'order_id',
        producer_pipeline_id: 'pipeline-pending', output_key: 'orders',
        has_review_evidence: true, updated_at: '2026-07-19T08:00:00Z',
      },
      {
        id: 'ds-pending-nopk', name: '无主键回调数据', status: 'pending_review',
        row_count: 2, quality_score: 0.88, primary_key: '',
        producer_pipeline_id: 'pipeline-no-pk', output_key: 'callbacks',
        has_review_evidence: true, updated_at: '2026-07-19T07:00:00Z',
      },
    ],
    pipelines: [
      { id: 'pipeline-pending', name: '订单增量流水线', domain: '零售', status: 'published', target_curated_ids: ['ds-pending-pk'] },
      { id: 'pipeline-no-pk', name: '回调采集流水线', domain: '通用', status: 'published', target_curated_ids: ['ds-pending-nopk'] },
    ],
  })
  await page.goto('/#/data/structured?tab=curated', { waitUntil: 'domcontentloaded' })

  const orderRow = page.getByRole('row').filter({ hasText: '待审核订单成品表' })
  await orderRow.getByRole('button', { name: '查看' }).click()
  let dialog = page.getByRole('dialog', { name: '待审核订单成品表' })
  await expect(dialog).toBeVisible()
  await expect.poll(async () => (await dialog.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(520)
  await expect(dialog.getByRole('button', { name: /变化量/ })).toBeVisible()
  await expect(dialog.getByRole('button', { name: /上一版本全量/ })).toBeVisible()
  await expect(dialog.getByRole('button', { name: /本次接受后全量/ })).toBeVisible()
  await expect(dialog.getByText('变更列：订单金额（amount）')).toBeVisible()
  await expect(dialog.getByText('绿色为新值')).toBeVisible()
  await expect(dialog.getByRole('cell', { name: /1280.*已变更/ })).toHaveClass(/bg-emerald-100/)
  await expect(dialog.getByText(/变化量基于两个完整版本计算/)).toBeVisible()

  await dialog.getByRole('button', { name: /上一版本全量/ }).click()
  await expect(dialog.getByLabel('待审核数据每页显示条数')).toHaveValue('50')
  await dialog.getByLabel('待审核数据每页显示条数').selectOption('20')
  await expect(dialog.getByText(/第 1 \/ 4 页/)).toBeVisible()
  await dialog.getByRole('button', { name: '下一页' }).click()
  await expect(dialog.getByText(/第 2 \/ 4 页/)).toBeVisible()

  await dialog.getByRole('button', { name: /本次接受后全量/ }).click()
  await expect(dialog.getByText(/如果接受本次变化/)).toBeVisible()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()

  const noPkRow = page.getByRole('row').filter({ hasText: '无主键回调数据' })
  await noPkRow.getByRole('button', { name: '查看' }).click()
  dialog = page.getByRole('dialog', { name: '无主键回调数据' })
  await expect(dialog.getByText(/当前流水线采用无主键模式，可以正常审核/)).toBeVisible()
  await expect(dialog.getByText(/不满足稳定识别数据行的契约/)).toHaveCount(0)
  await expect(dialog.getByText('无主键 · 按整行比较，不单独识别更新')).toBeVisible()
  await expect(dialog.getByText(/沿用字段标识|中文名未配置/)).toHaveCount(0)
  await expect(dialog.getByText('未设置字段名称')).toHaveCount(2)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
})
