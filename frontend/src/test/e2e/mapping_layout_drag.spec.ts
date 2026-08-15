import { expect, test, type Page, type Route } from '@playwright/test'
import type { MappingObjectType } from '../../pages/ontologies/detail/mapping/mapping-data'

const order: MappingObjectType = {
  id: 'ot-order',
  name: 'Order',
  displayName: '订单',
  primaryKey: 'prop-order-id',
  properties: [
    { id: 'prop-order-id', name: 'order_id', displayName: '订单编号', type: 'string' },
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

async function mockMappingWorkspace(page: Page, options: { withSavedLayout?: boolean } = {}) {
  const layoutBodies: Array<Record<string, unknown>> = []
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u1', username: 'tester', role: 'admin' },
      },
      version: 0,
    }))
    localStorage.setItem('mapping-tutorial:ontology-layout', 'seen')
  })
  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const url = new URL(route.request().url())
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (url.pathname === '/api/v2/ontologies/ontology-layout/layout'
      && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON()
      layoutBodies.push(body)
      return ok({ versionId: 'draft-1', positions: body.positions })
    }
    if (url.pathname === '/api/v2/ontologies/ontology-layout/versions/draft-1/workspace') {
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
            fieldMapping: { order_id: 'order_id', __primary_key__: 'order_id' },
          },
          {
            id: 'map-supplier',
            curatedDatasetId: 'dataset-orders',
            targetObjectTypeId: supplier.id,
            entityClass: supplier.name,
            fieldMapping: { supplier_id: 'supplier_id', __primary_key__: 'supplier_id' },
          },
        ],
        linkMappings: [],
        ...(options.withSavedLayout
          ? { canvasLayout: { 'object:ot-order': { x: 460, y: 1400 } } }
          : {}),
      })
    }
    if (url.pathname === '/api/v2/curated') {
      return ok([{
        id: 'dataset-orders',
        name: '订单宽表',
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
  return layoutBodies
}

test('拖拽映射画布节点即持久化位置，且不点亮映射保存', async ({ page }) => {
  const layoutBodies = await mockMappingWorkspace(page)
  await page.goto(
    '/#/ontologies/ontology-layout/mapping-config?versionId=draft-1',
    { waitUntil: 'domcontentloaded' },
  )

  const objectNode = page.locator('.react-flow__node[data-id="object:ot-order"]')
  await expect(page.locator('.react-flow__node')).toHaveCount(3)
  await expect(objectNode).toBeVisible()
  const before = await objectNode.boundingBox()
  expect(before).toBeTruthy()

  const layoutSaved = page.waitForResponse(response =>
    response.request().method() === 'PUT'
      && response.url().includes('/api/v2/ontologies/ontology-layout/layout'))
  // 从节点头部空白处起拖，避开字段行上的连线锚点。
  await page.mouse.move(before!.x + before!.width / 2, before!.y + 14)
  await page.mouse.down()
  await page.mouse.move(before!.x + before!.width / 2 + 120, before!.y + 14 + 90, { steps: 8 })
  await page.mouse.up()
  expect((await layoutSaved).ok()).toBeTruthy()

  const body = layoutBodies.at(-1) as {
    versionId: string
    positions: Record<string, { x: number; y: number }>
  }
  expect(body.versionId).toBe('draft-1')
  const saved = body.positions['object:ot-order']
  expect(saved).toBeTruthy()
  expect(Number.isFinite(saved.x)).toBeTruthy()
  expect(Number.isFinite(saved.y)).toBeTruthy()
  // 未映射连线的位置调整属于独立展示元数据，不能点亮“保存配置”。
  await expect(page.getByRole('button', { name: '已保存' })).toBeDisabled()
})

test('映射画布初始化优先恢复已保存的节点位置', async ({ page }) => {
  await mockMappingWorkspace(page, { withSavedLayout: true })
  await page.goto(
    '/#/ontologies/ontology-layout/mapping-config?versionId=draft-1',
    { waitUntil: 'domcontentloaded' },
  )

  const datasetNode = page.locator('.react-flow__node[data-id="dataset:dataset-orders"]')
  const objectNode = page.locator('.react-flow__node[data-id="object:ot-order"]')
  await expect(page.locator('.react-flow__node')).toHaveCount(3)
  const datasetBox = await datasetNode.boundingBox()
  const objectBox = await objectNode.boundingBox()
  expect(datasetBox).toBeTruthy()
  expect(objectBox).toBeTruthy()
  // 车道式自动布局下对象节点 y 与数据集节点基本持平；已保存的 y=1400
  // 恢复后对象节点应明显低于数据集节点（考虑 fitView 缩放仍远超阈值）。
  expect(objectBox!.y).toBeGreaterThan(datasetBox!.y + 300)
})

test('拖拽节点跨侧后字段行锚点自动换侧，连线始终走短边', async ({ page }) => {
  await mockMappingWorkspace(page)
  await page.goto(
    '/#/ontologies/ontology-layout/mapping-config?versionId=draft-1',
    { waitUntil: 'domcontentloaded' },
  )

  const datasetNode = page.locator('.react-flow__node[data-id="dataset:dataset-orders"]')
  const orderNode = page.locator('.react-flow__node[data-id="object:ot-order"]')
  await expect(page.locator('.react-flow__node')).toHaveCount(3)
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)

  // 初始车道布局（数据集在左、对象在右）：所有锚点保持默认朝向，无换侧
  await expect(page.locator('.dmc-handle--flip')).toHaveCount(0)

  // 把数据集节点拖到对象节点右侧：拖拽距离覆盖跨中心线（含 fitView 缩放余量）
  const before = await datasetNode.boundingBox()
  expect(before).toBeTruthy()
  await page.mouse.move(before!.x + before!.width / 2, before!.y + 14)
  await page.mouse.down()
  await page.mouse.move(before!.x + before!.width / 2 + 900, before!.y + 14, { steps: 12 })
  await page.mouse.up()

  // 数据集锚点翻到左侧（2 个字段行），两个对象节点锚点翻到右侧（各 1 个字段行）
  await expect(datasetNode.locator('.dmc-handle--flip')).toHaveCount(2)
  await expect(orderNode.locator('.dmc-handle--flip')).toHaveCount(1)
  await expect(page.locator('.react-flow__node[data-id="object:ot-supplier"] .dmc-handle--flip')).toHaveCount(1)
  // 换侧只是视觉派生，连线数量与映射关系不变
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
})
