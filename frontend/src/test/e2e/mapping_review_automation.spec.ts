import { expect, test, type Page, type Route } from '@playwright/test'

async function mockDraftReviewAutomation(page: Page) {
  let savedBody: Record<string, unknown> | null = null
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u1', username: 'tester', role: 'admin' },
      },
      version: 0,
    }))
    localStorage.setItem('mapping-tutorial:ontology-review-policy', 'seen')
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (
      url.pathname === '/api/v2/ontologies/ontology-review-policy/versions/draft-1/workspace/mappings'
      && route.request().method() === 'PUT'
    ) {
      savedBody = route.request().postDataJSON()
      return ok({ revision: 'r2' })
    }
    if (url.pathname === '/api/v2/ontologies/ontology-review-policy/versions/draft-1/workspace') {
      return ok({
        versionId: 'draft-1',
        versionNumber: 'v1-draft.1',
        workspaceMode: 'draft',
        editable: true,
        revision: 'r1',
        objectTypes: [
          {
            id: 'object-order',
            name: 'Order',
            displayName: '订单',
            primaryKey: 'order_id',
            properties: [
              { id: 'order_id', name: 'order_id', displayName: '订单编号', type: 'string' },
            ],
          },
          {
            id: 'object-supplier',
            name: 'Supplier',
            displayName: '供应商',
            primaryKey: 'supplier_id',
            properties: [
              { id: 'supplier_id', name: 'supplier_id', displayName: '供应商编号', type: 'string' },
            ],
          },
        ],
        linkTypes: [{
          id: 'link-supplier',
          name: 'fulfilled_by',
          displayName: '由供应商履约',
          sourceObjectTypeId: 'object-order',
          targetObjectTypeId: 'object-supplier',
          cardinality: 'many-to-one',
          properties: [],
        }],
        mappings: [
          {
            id: 'mapping-order',
            curatedDatasetId: 'dataset-orders',
            targetObjectTypeId: 'object-order',
            entityClass: 'Order',
            fieldMapping: {
              order_id: 'order_id',
              __primary_key__: 'order_id',
            },
            status: 'draft',
          },
          {
            id: 'mapping-supplier',
            curatedDatasetId: 'dataset-suppliers',
            targetObjectTypeId: 'object-supplier',
            entityClass: 'Supplier',
            fieldMapping: {
              supplier_id: 'supplier_id',
              __primary_key__: 'supplier_id',
            },
            status: 'draft',
          },
        ],
        linkMappings: [{
          id: 'mapping-supplier-link',
          srcDatasetId: 'dataset-orders',
          tgtDatasetId: 'dataset-suppliers',
          edgeDatasetId: 'dataset-order-suppliers',
          relationType: 'fulfilled_by',
          linkTypeId: 'link-supplier',
          srcKey: 'order_id',
          tgtKey: 'supplier_id',
          fieldMapping: {},
          status: 'draft',
        }],
      })
    }
    if (url.pathname === '/api/v2/curated') {
      return ok([
        {
          id: 'dataset-orders',
          name: '订单成品',
          status: 'approved',
          row_count: 4,
          quality_score: 1,
          primary_key: 'order_id',
          producer_pipeline_id: 'pipeline-orders',
          output_key: 'orders',
          has_review_evidence: true,
        },
        {
          id: 'dataset-suppliers',
          name: '供应商成品',
          status: 'approved',
          row_count: 2,
          quality_score: 1,
          primary_key: 'supplier_id',
          producer_pipeline_id: 'pipeline-suppliers',
          output_key: 'suppliers',
          has_review_evidence: true,
        },
        {
          id: 'dataset-order-suppliers',
          name: '订单供应商关系成品',
          // This pending dataset has no object mapping. Its existing link-only
          // reference must still keep the review policy reachable.
          status: 'pending_review',
          row_count: 4,
          quality_score: 1,
          primary_key: 'edge_id',
          producer_pipeline_id: 'pipeline-order-suppliers',
          output_key: 'order_suppliers',
          has_review_evidence: true,
        },
      ])
    }
    if (url.pathname === '/api/v2/datasets/overview') {
      return ok({ items: [], total: 0, page: 1, page_size: 20 })
    }
    if (url.pathname === '/api/v2/datasets/dataset-orders/schema') {
      return ok({
        dataset_id: 'dataset-orders',
        columns: [{ name: 'order_id', type: 'string', nullable: false, is_primary_key: true }],
      })
    }
    if (url.pathname === '/api/v2/datasets/dataset-suppliers/schema') {
      return ok({
        dataset_id: 'dataset-suppliers',
        columns: [{ name: 'supplier_id', type: 'string', nullable: false, is_primary_key: true }],
      })
    }
    if (url.pathname === '/api/v2/datasets/dataset-order-suppliers/schema') {
      return ok({
        dataset_id: 'dataset-order-suppliers',
        columns: [
          { name: 'edge_id', type: 'string', nullable: false, is_primary_key: true },
          { name: 'order_id', type: 'string', nullable: false, is_primary_key: false },
          { name: 'supplier_id', type: 'string', nullable: false, is_primary_key: false },
        ],
      })
    }
    return ok([])
  })
  return () => savedBody
}

test('草稿中的成品审核订阅写入对象快照，并可到达仅由关系引用的边数据集', async ({ page }) => {
  const savedBody = await mockDraftReviewAutomation(page)
  await page.goto(
    '/#/ontologies/ontology-review-policy/mapping-config?versionId=draft-1',
    { waitUntil: 'domcontentloaded' },
  )

  const objectPolicy = page.getByRole('checkbox', { name: '审核通过后自动灌入 订单成品' })
  const edgePolicy = page.getByRole('checkbox', { name: '审核通过后自动灌入 订单供应商关系成品' })
  await expect(objectPolicy).toBeVisible()
  await expect(edgePolicy).toBeVisible()
  await expect(edgePolicy).toBeEnabled()
  await expect(edgePolicy).not.toBeChecked()

  await objectPolicy.check()
  await edgePolicy.check()
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByRole('button', { name: '已保存' })).toBeDisabled()

  const body = savedBody() as {
    mappings: Array<{ id: string; fieldMapping: Record<string, unknown> }>
    linkMappings: Array<{ id: string; fieldMapping: Record<string, unknown> }>
  }
  expect(body.mappings.find(item => item.id === 'mapping-order')
    ?.fieldMapping.__auto_apply_on_review__).toBe(true)
  expect(body.mappings.find(item => item.id === 'mapping-supplier')
    ?.fieldMapping.__auto_apply_on_review__).toBeUndefined()
  expect(body.linkMappings.find(item => item.id === 'mapping-supplier-link')
    ?.fieldMapping.__auto_apply_on_review__).toBe(true)
})
