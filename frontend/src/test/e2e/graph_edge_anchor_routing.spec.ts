import { expect, test, type Page, type Route } from '@playwright/test'

const ontologyId = 'ontology-edge-anchor'

async function mockGraphWorkspace(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u1', username: 'tester', role: 'admin' },
      },
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
    if (url.pathname === `/api/v2/ontologies/${ontologyId}/layout`
      && route.request().method() === 'PUT') {
      return ok({ positions: {} })
    }
    if (url.pathname === `/api/v2/formal/ontologies/${ontologyId}/full`) {
      return ok({
        id: ontologyId,
        name: '边锚点测试本体',
        version: 'v1',
        revision: '1:snapshot',
        workspaceMode: 'runtime',
        editable: false,
        objectTypes: [
          {
            id: 'ot-order',
            name: 'Order',
            displayName: '订单',
            primaryKey: 'order_no',
            properties: [
              { id: 'order_no', name: 'order_no', displayName: '订单号', type: 'string', required: true },
            ],
            positionX: 0,
            positionY: 0,
          },
          {
            id: 'ot-customer',
            name: 'Customer',
            displayName: '客户',
            primaryKey: 'cust_no',
            properties: [
              { id: 'cust_no', name: 'cust_no', displayName: '客户编号', type: 'string', required: true },
            ],
            positionX: 700,
            positionY: 0,
          },
        ],
        linkTypes: [{
          id: 'lt-placed-by',
          name: 'placed_by',
          displayName: '下单',
          sourceObjectTypeId: 'ot-order',
          targetObjectTypeId: 'ot-customer',
          cardinality: 'many-to-one',
        }],
        actions: [],
        functions: [],
        instances: [],
        linkInstances: [],
        executionLogs: [],
        sentinels: [],
      })
    }
    if (url.pathname === '/api/v2/inbox/summary') {
      return ok({ unread_count: 0 })
    }
    return ok([])
  })
}

/** 边 path 的起点横坐标（flow 坐标系，不受缩放影响） */
function edgeStartX(d: string): number {
  const match = d.match(/^M\s*([-\d.]+)[ ,]/)
  expect(match, `无法从边路径解析起点: ${d}`).toBeTruthy()
  return Number(match![1])
}

test('图谱编辑器拖拽节点跨侧时连线锚点自动换侧', async ({ page }) => {
  await mockGraphWorkspace(page)
  await page.goto(`/#/ontologies/${ontologyId}/graph`, { waitUntil: 'domcontentloaded' })

  await expect(page.getByTestId('graph-workspace-stage')).toBeVisible()
  const customerNode = page.locator('.react-flow__node[data-id="ot-customer"]')
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  const edgePath = page.locator('.react-flow__edge-path').first()
  // 两节点同高时边是一条零高度的水平直线，Playwright 视为 hidden；改为断言 path 数据
  await expect(edgePath).toHaveAttribute('d', /^M\s*[-\d.]/)

  // 初始客户在订单右侧：边从订单右侧锚点出发
  const before = await edgePath.getAttribute('d')
  expect(before).toBeTruthy()
  const beforeStartX = edgeStartX(before!)

  // 把客户节点拖到订单节点左侧（拖拽距离覆盖跨中心线，含 fitView 缩放余量）
  const box = await customerNode.boundingBox()
  expect(box).toBeTruthy()
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 20)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width / 2 - 950, box!.y + 20, { steps: 12 })
  await page.mouse.up()

  // 锚点换侧后边起点从订单左侧出发：起点横坐标明显左移（约一个节点宽度）
  await expect
    .poll(async () => edgeStartX((await edgePath.getAttribute('d')) || ''))
    .toBeLessThan(beforeStartX - 100)
  await expect(edgePath).toHaveAttribute('d', /^M\s*[-\d.]/)
})
