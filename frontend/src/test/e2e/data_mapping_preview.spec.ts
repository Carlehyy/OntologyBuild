import { expect, test, type Page, type Route } from '@playwright/test'

async function mockMappingPreview(page: Page) {
  const columns = Array.from({ length: 8 }, (_, index) => `field_${index + 1}`)
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (url.pathname === '/api/v1/ontologies/ontology-preview') return ok({
      id: 'ontology-preview', name: '预览测试本体', domain: '测试', version: 'v1',
      current_release_id: 'release-1', current_release_version: 'v1', status: 'published',
      entity_count: 1, relation_count: 0, action_count: 0, sentinel_count: 0,
      created_by: 'tester', created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
    })
    if (url.pathname === '/api/v2/ontologies/ontology-preview/current-release/workspace') return ok({
      versionId: 'release-1', versionNumber: 'v1', isCurrentRelease: true,
      workspaceMode: 'release', editable: false, revision: 'r1',
      objectTypes: [{
        id: 'object-order', name: 'Order', displayName: '订单', primaryKey: 'id',
        properties: [{ id: 'id', name: 'id', displayName: '订单编号', type: 'string' }],
      }],
      linkTypes: [],
      mappings: [{
        id: 'mapping-1', curatedDatasetId: 'dataset-wide', targetObjectTypeId: 'object-order',
        entityClass: 'Order', fieldMapping: { field_1: 'id' }, status: 'published',
      }],
      linkMappings: [],
    })
    if (url.pathname === '/api/v2/curated') return ok([{
      id: 'dataset-wide', name: '订单宽表', status: 'approved', row_count: 45,
      quality_score: .96, primary_key: 'field_1', producer_pipeline_id: null,
      output_key: null, has_review_evidence: true,
    }])
    if (url.pathname === '/api/v2/datasets/overview') return ok({ items: [], total: 0, page: 1, page_size: 20 })
    if (url.pathname === '/api/v2/datasets/dataset-wide/schema') return ok({
      dataset_id: 'dataset-wide',
      columns: columns.map((name, index) => ({
        name, display_name: `字段 ${index + 1}`, type: 'string', nullable: index > 0,
        is_primary_key: index === 0, sample_values: [`值 ${index + 1}`],
      })),
    })
    if (url.pathname === '/api/v2/curated/dataset-wide/preview') {
      const offset = Number(url.searchParams.get('offset') || 0)
      const limit = Number(url.searchParams.get('limit') || 20)
      const end = Math.min(offset + limit, 45)
      return ok({
        dataset_id: 'dataset-wide', name: '订单宽表', columns, total_rows: 45,
        offset, limit, has_more: end < 45,
        rows: Array.from({ length: end - offset }, (_, rowIndex) => Object.fromEntries(
          columns.map((column, columnIndex) => [column, `R${offset + rowIndex + 1}-C${columnIndex + 1}`]),
        )),
        count: end - offset,
      })
    }
    if (url.pathname.startsWith('/api/v2/formal/ontologies/ontology-preview/')) return ok([])
    return ok([])
  })
}

test('数据源眼睛按钮打开分页预览，宽表提供横向滚动', async ({ page }) => {
  await mockMappingPreview(page)
  await page.goto('/#/ontologies/ontology-preview?tab=data-mapping', { waitUntil: 'domcontentloaded' })

  await expect(page.getByText('映射结果清单')).toBeVisible()
  await expect(page.getByText('数据血缘详情')).toBeVisible()
  await expect(page.getByText('把本体结构，接到真实数据上')).toHaveCount(0)
  const cardBox = await page.locator('.dmo-card').boundingBox()
  expect(cardBox).not.toBeNull()
  expect(cardBox!.y + cardBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1)

  await page.getByRole('button', { name: '预览数据源 订单宽表' }).click()
  const dialog = page.getByRole('dialog', { name: '订单宽表' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('1–20 / 45 行')).toBeVisible()
  await expect(dialog.getByText('R1-C1')).toBeVisible()

  const scrollRegion = dialog.locator('.dmo-preview-table-scroll')
  await expect(dialog.locator('.dmo-preview-table')).toHaveClass(/is-scrollable/)
  expect(await scrollRegion.evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy()
  expect((await dialog.boundingBox())!.height).toBeLessThanOrEqual(680)

  await dialog.getByRole('button', { name: '下一页' }).click()
  await expect(dialog.getByText('21–40 / 45 行')).toBeVisible()
  await expect(dialog.getByText('R21-C1')).toBeVisible()
  await dialog.getByRole('button', { name: '关闭数据预览' }).click()
  await expect(dialog).toHaveCount(0)
})
