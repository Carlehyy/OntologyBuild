import { expect, test, type Page, type Route } from '@playwright/test'
import type { MappingObjectType } from '../../pages/ontologies/detail/mapping/mapping-data'
import { resolveObjectMappingPrimaryKey } from '../../pages/ontologies/mapping/object-mapping-primary-key'

const order: MappingObjectType = {
  id: 'ot-order',
  name: 'Order',
  displayName: '订单',
  primaryKey: 'prop-order-id',
  properties: [
    { id: 'prop-order-id', name: 'order_id', displayName: '订单编号', type: 'string' },
    { id: 'prop-supplier-id', name: 'supplier_id', displayName: '供应商编号', type: 'string' },
  ],
}

const supplier: MappingObjectType = {
  id: 'ot-supplier',
  name: 'Supplier',
  displayName: '供应商',
  primaryKey: 'prop-supplier-id',
  properties: [
    { id: 'prop-supplier-id', name: 'supplier_id', displayName: '供应商编号', type: 'string' },
  ],
}

test('同一订单数据集按各对象主键连线生成独立身份列', () => {
  expect(resolveObjectMappingPrimaryKey(
    order,
    { order_id: 'order_id', supplier_id: 'supplier_id' },
  )).toMatchObject({ ok: true, column: 'order_id', source: 'edge' })

  expect(resolveObjectMappingPrimaryKey(
    supplier,
    { supplier_id: 'supplier_id' },
  )).toMatchObject({ ok: true, column: 'supplier_id', source: 'edge' })
})

test('已有显式对象身份列优先保留，缺失主键连线时给出明确门禁原因', () => {
  expect(resolveObjectMappingPrimaryKey(
    supplier,
    { supplier_code: 'supplier_id' },
    { __primary_key__: 'tenant_id,supplier_id' },
  )).toMatchObject({
    ok: true,
    column: 'tenant_id,supplier_id',
    source: 'explicit',
  })

  expect(resolveObjectMappingPrimaryKey(
    supplier,
    { supplier_name: 'supplier_name' },
    { __primary_key__: 'supplier_id' },
  )).toMatchObject({
    ok: false,
    issue: 'primary_key_edge_missing',
    property: { id: 'prop-supplier-id', name: 'supplier_id' },
  })
})

async function mockLegacyMappingsWithoutPrimaryKey(page: Page) {
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
    localStorage.setItem('mapping-tutorial:ontology-migration', 'seen')
  })
  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (
      url.pathname === '/api/v2/ontologies/ontology-migration/versions/draft-1/workspace/mappings'
      && route.request().method() === 'PUT'
    ) {
      savedBody = route.request().postDataJSON()
      return ok({ revision: 'r2' })
    }
    if (url.pathname === '/api/v2/ontologies/ontology-migration/versions/draft-1/workspace') {
      return ok({
        versionId: 'draft-1',
        versionNumber: 'v1-draft.1',
        workspaceMode: 'draft',
        editable: true,
        revision: 'r1',
        objectTypes: [order, supplier],
        linkTypes: [],
        mappings: [
          {
            id: 'map-order',
            curatedDatasetId: 'dataset-orders',
            targetObjectTypeId: order.id,
            entityClass: order.name,
            fieldMapping: {
              order_id: 'order_id',
              supplier_id: 'supplier_id',
            },
          },
          {
            id: 'map-supplier',
            curatedDatasetId: 'dataset-orders',
            targetObjectTypeId: supplier.id,
            entityClass: supplier.name,
            fieldMapping: { supplier_id: 'supplier_id' },
          },
        ],
        linkMappings: [],
      })
    }
    if (url.pathname === '/api/v2/curated') {
      return ok([{
        id: 'dataset-orders',
        name: '订单供应商宽表',
        status: 'approved',
        row_count: 4,
        quality_score: 1,
        primary_key: 'order_id',
      }])
    }
    if (url.pathname === '/api/v2/datasets/overview') {
      return ok({ items: [], total: 0, page: 1, page_size: 20 })
    }
    if (url.pathname === '/api/v2/datasets/dataset-orders/schema') {
      return ok({
        dataset_id: 'dataset-orders',
        columns: [
          { name: 'order_id', type: 'string', nullable: false, is_primary_key: true },
          { name: 'supplier_id', type: 'string', nullable: false, is_primary_key: false },
        ],
      })
    }
    return ok([])
  })
  return () => savedBody
}

test('旧映射可从现有主键连线一键补齐身份列，无需清空重建', async ({ page }) => {
  const savedBody = await mockLegacyMappingsWithoutPrimaryKey(page)
  await page.goto(
    '/#/ontologies/ontology-migration/mapping-config?versionId=draft-1',
    { waitUntil: 'domcontentloaded' },
  )

  const save = page.getByRole('button', { name: '保存配置' })
  await expect(save).toBeEnabled()
  await expect(page.getByText('检测到 2 个历史对象映射可补齐稳定身份列，请保存一次完成兼容升级。')).toBeVisible()
  await save.click()
  await expect(page.getByRole('button', { name: '已保存' })).toBeDisabled()

  const body = savedBody() as {
    mappings: Array<{
      entityClass: string
      fieldMapping: Record<string, string>
    }>
  }
  expect(body.mappings.find(mapping => mapping.entityClass === 'Order')
    ?.fieldMapping.__primary_key__).toBe('order_id')
  expect(body.mappings.find(mapping => mapping.entityClass === 'Supplier')
    ?.fieldMapping.__primary_key__).toBe('supplier_id')
})
