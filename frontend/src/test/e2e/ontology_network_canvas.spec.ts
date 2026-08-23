import assert from 'node:assert/strict'

import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * 本体网络画布（ECharts graph 内核）mocked E2E。
 *
 * 覆盖：图数据渲染（svg 文本级断言）、头部统计、图例、节点点击打开详情、
 * 工具条缩放按钮、搜索触发的新图请求。全部请求本地 mock，不依赖后端。
 */

const overview = [
  {
    id: 'o-supply', name: '供应链', domain: '供应链', published: true,
    releaseId: 'rel-supply', version: 'v2', typeCount: 2, linkTypeCount: 1, instanceCount: 3,
  },
  {
    id: 'o-device', name: '设备台账', domain: '设备', published: false,
    releaseId: null, version: null, typeCount: 1, linkTypeCount: 0, instanceCount: 1,
  },
]

const graphNodes = [
  { id: 'type:t-customer', entityId: 't-customer', kind: 'object_type', label: '客户', technicalName: 'customer',
    ontologyId: 'o-supply', ontologyName: '供应链', count: 2 },
  { id: 'type:t-order', entityId: 't-order', kind: 'object_type', label: '订单', technicalName: 'sales_order',
    ontologyId: 'o-supply', ontologyName: '供应链', count: 1 },
  { id: 'instance:c1', entityId: 'c1', kind: 'instance', label: '华东制造', objectTypeId: 't-customer',
    objectTypeLabel: '客户', ontologyId: 'o-supply', ontologyName: '供应链' },
  { id: 'instance:c2', entityId: 'c2', kind: 'instance', label: '华南贸易', objectTypeId: 't-customer',
    objectTypeLabel: '客户', ontologyId: 'o-supply', ontologyName: '供应链' },
  { id: 'instance:o9', entityId: 'o9', kind: 'instance', label: 'SO-2026-001', objectTypeId: 't-order',
    objectTypeLabel: '订单', ontologyId: 'o-supply', ontologyName: '供应链' },
  { id: 'type:d-customer', entityId: 'd-customer', kind: 'object_type', label: '客户', technicalName: 'customer',
    ontologyId: 'o-device', ontologyName: '设备台账', count: 1 },
  { id: 'instance:d1', entityId: 'd1', kind: 'instance', label: '设备-77', objectTypeId: 'd-customer',
    objectTypeLabel: '客户', ontologyId: 'o-device', ontologyName: '设备台账' },
]

const graphEdges = [
  { id: 'link:l1', kind: 'relation', source: 'instance:c1', target: 'instance:o9', label: '下达',
    ontologyId: 'o-supply', ontologyName: '供应链' },
  { id: 'link:l2', kind: 'relation', source: 'instance:c2', target: 'instance:o9', label: '下达',
    ontologyId: 'o-supply', ontologyName: '供应链' },
  { id: 'schema:s1', kind: 'schema_relation', source: 'type:t-customer', target: 'type:t-order', label: '下游',
    ontologyId: 'o-supply', ontologyName: '供应链' },
  { id: 'bridge:type:t-customer::type:d-customer', kind: 'bridge', source: 'type:t-customer',
    target: 'type:d-customer', label: '同名类型', crossOntology: true },
]

const graphResponse = {
  level: 2,
  query: null,
  limitPerType: 10,
  ontologies: overview.map(item => ({ ...item, error: undefined })),
  errors: [],
  nodes: graphNodes,
  edges: graphEdges,
  bridges: {
    enabled: true,
    groups: [{
      key: 'bridge-group-fix-1',
      label: '客户',
      members: [
        { nodeId: 'type:t-customer', entityId: 't-customer', ontologyId: 'o-supply', ontologyName: '供应链', label: '客户' },
        { nodeId: 'type:d-customer', entityId: 'd-customer', ontologyId: 'o-device', ontologyName: '设备台账', label: '客户' },
      ],
    }],
  },
  meta: {
    nodeBudget: 800, edgeBudget: 2000, truncated: false, droppedEdges: 0,
    nodeCount: 7, edgeCount: 4, selectedOntologies: 2, totalInstances: 4,
  },
}

const instanceDetail = {
  id: 'c1',
  label: '华东制造',
  objectType: {
    id: 't-customer', name: 'customer', displayName: '客户', primaryKey: 'code',
    properties: [{ name: 'region', displayName: '区域', type: 'string' }],
  },
  properties: { region: '华东' },
  computed: {},
  source: 'seed',
  externalId: null,
}

async function mockNetworkApi(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })

  const graphQueries: string[] = []
  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const ok = (data: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (url.pathname === '/api/v2/ontology-network/overview') return ok(overview)
    if (url.pathname === '/api/v2/ontology-network/graph') {
      graphQueries.push(url.searchParams.toString())
      return ok(graphResponse)
    }
    if (/^\/api\/v2\/ontology-network\/[^/]+\/instances\/[^/]+$/.test(url.pathname)) {
      return ok(instanceDetail)
    }
    if (url.pathname === '/api/v2/inbox/summary') return ok({ unread: 0, actionable: 0 })
    if (url.pathname === '/api/v2/inbox') return ok({ items: [], total: 0 })
    return ok({})
  })
  return { graphQueries }
}

test('本体网络画布渲染全局图：统计、图例与节点标签可见', async ({ page }) => {
  await mockNetworkApi(page)
  await page.goto('/#/ontology-model/network', { waitUntil: 'domcontentloaded' })

  const card = page.getByTestId('network-canvas-card')
  await expect(card).toBeVisible()
  await expect(card.getByText('2 个本体 · 7 节点 / 4 边')).toBeVisible()

  // 图例（按本体着色）+ 桥接提示
  const legend = card.locator('.backdrop-blur').filter({ hasText: '图例' })
  await expect(legend.getByText('供应链')).toBeVisible()
  await expect(legend.getByText('设备台账')).toBeVisible()
  await expect(legend.getByText('同名类型桥接（启发式）')).toBeVisible()

  // ECharts svg 渲染：实例标签以 <text> 呈现且可被定位
  const chartSvg = card.locator('svg').last()
  await expect(chartSvg).toBeVisible()
  await expect(card.getByText('华东制造', { exact: true })).toBeVisible()
  await expect(card.getByText('SO-2026-001', { exact: true })).toBeVisible()
})

test('点击实例节点打开详情抽屉，可关闭', async ({ page }) => {
  await mockNetworkApi(page)
  await page.goto('/#/ontology-model/network', { waitUntil: 'domcontentloaded' })

  const card = page.getByTestId('network-canvas-card')
  const label = card.getByText('华东制造', { exact: true })
  await expect(label).toBeVisible()

  // 标签挂在节点正下方：向标签上方偏移点击命中节点圆心
  const box = await label.boundingBox()
  assert.ok(box, '标签应有可点击的包围盒')
  await page.mouse.click(box.x + box.width / 2, box.y - 18)

  const inspector = page.getByTestId('network-inspector')
  await expect(inspector).toBeVisible()
  await expect(inspector.getByText('华东制造')).toBeVisible()
  await expect(inspector.getByRole('button', { name: /设为起点/ })).toBeVisible()

  await inspector.getByRole('button', { name: '关闭节点详情' }).click()
  await expect(inspector).toHaveCount(0)
})

test('工具条缩放/适应可用，搜索触发带 query 的图请求', async ({ page }) => {
  const { graphQueries } = await mockNetworkApi(page)
  await page.goto('/#/ontology-model/network', { waitUntil: 'domcontentloaded' })

  const card = page.getByTestId('network-canvas-card')
  await expect(card.getByText('2 个本体 · 7 节点 / 4 边')).toBeVisible()

  await card.getByRole('button', { name: '放大画布' }).click()
  await card.getByRole('button', { name: '缩小画布' }).click()
  await card.getByRole('button', { name: '适应画布' }).click()
  // 未截断时不出现预算提示
  await expect(card.getByText(/已按预算截断/)).toHaveCount(0)

  await page.getByLabel('搜索实例').fill('华南')
  await page.getByLabel('搜索实例').press('Enter')
  await expect.poll(() => graphQueries.some(query => query.includes('query='))).toBe(true)
})
