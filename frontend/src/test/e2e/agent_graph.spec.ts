import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const API = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000'

async function login(page: Page): Promise<string> {
  await page.goto('/login')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('admin123')
  await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/overview')
  const token = await page.evaluate(() => localStorage.getItem('token'))
  expect(token).toBeTruthy()
  return token!
}

async function api<T>(
  request: APIRequestContext,
  token: string,
  method: 'get' | 'post' | 'put',
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await request[method](API + path, {
    headers: { Authorization: 'Bearer ' + token },
    data,
  })
  expect(response.ok(), method.toUpperCase() + ' ' + path + ': ' + await response.text()).toBeTruthy()
  const body = await response.json()
  return body.data ?? body
}

test('assistant data graph supports progressive depth, paths, impact preview and chat handoff', async ({ page, request }) => {
  test.setTimeout(process.env.PLAYWRIGHT_EXPECT_LLM === '1' ? 120_000 : 60_000)
  const token = await login(page)
  const suffix = Date.now().toString(36)
  const ontology = await api<any>(request, token, 'post', '/api/v1/ontologies', {
    name: '助手图谱真机-' + suffix,
    domain: '供应链',
    description: '路径和拟议变更关联范围端到端测试',
  })

  try {
    await api(request, token, 'put', '/api/v2/formal/ontologies/' + ontology.id + '/full', {
      objectTypes: [
        {
          id: 'ot-device-' + suffix, name: 'Device', displayName: '设备',
          icon: 'radar',
          primaryKey: 'device_no', positionX: 0, positionY: 0,
          properties: [
            { id: 'dp1', name: 'device_no', displayName: '设备编号', type: 'string', required: true },
            { id: 'dp2', name: 'status', displayName: '运行状态', type: 'string', required: false },
          ],
        },
        {
          id: 'ot-task-' + suffix, name: 'Task', displayName: '生产任务',
          icon: 'alert-triangle',
          primaryKey: 'task_no', positionX: 0, positionY: 0,
          properties: [
            { id: 'tp1', name: 'task_no', displayName: '任务编号', type: 'string', required: true },
            { id: 'tp2', name: 'state', displayName: '任务状态', type: 'string', required: false },
          ],
        },
        {
          id: 'ot-order-' + suffix, name: 'WorkOrder', displayName: '工单',
          icon: 'building',
          primaryKey: 'order_no', positionX: 0, positionY: 0,
          properties: [
            { id: 'op1', name: 'order_no', displayName: '工单号', type: 'string', required: true },
            { id: 'op2', name: 'priority', displayName: '优先级', type: 'string', required: false },
          ],
        },
      ],
      linkTypes: [
        {
          id: 'lt-carries-' + suffix, name: 'carries', displayName: '承载',
          sourceObjectTypeId: 'ot-device-' + suffix,
          targetObjectTypeId: 'ot-task-' + suffix,
          cardinality: 'one-to-many', properties: [],
        },
        {
          id: 'lt-produces-' + suffix, name: 'produces', displayName: '生成',
          sourceObjectTypeId: 'ot-task-' + suffix,
          targetObjectTypeId: 'ot-order-' + suffix,
          cardinality: 'one-to-many', properties: [],
        },
      ],
      actions: [],
      functions: [],
      instances: [
        {
          id: 'inst-device-' + suffix, objectTypeId: 'ot-device-' + suffix,
          properties: { device_no: 'DEV-A', status: 'running' }, computed: {},
        },
        {
          id: 'inst-task-' + suffix, objectTypeId: 'ot-task-' + suffix,
          properties: { task_no: 'TASK-B', state: 'active' }, computed: {},
        },
        {
          id: 'inst-order-' + suffix, objectTypeId: 'ot-order-' + suffix,
          properties: { order_no: 'WO-C', priority: 'high' }, computed: {},
        },
      ],
      linkInstances: [
        {
          id: 'li-carries-' + suffix, linkTypeId: 'lt-carries-' + suffix,
          sourceObjectId: 'inst-device-' + suffix, targetObjectId: 'inst-task-' + suffix,
        },
        {
          id: 'li-produces-' + suffix, linkTypeId: 'lt-produces-' + suffix,
          sourceObjectId: 'inst-task-' + suffix, targetObjectId: 'inst-order-' + suffix,
        },
      ],
    })

    await page.goto('/#/agent')
    await page.getByLabel('选择本体').selectOption(ontology.id)
    await expect(page.getByRole('heading', { name: '本体拓扑图' })).toBeVisible()
    const topologyNodes = page.getByTestId('ontology-network-node')
    await expect(topologyNodes).toHaveCount(3)
    await expect(page.getByText('radar', { exact: true })).toHaveCount(0)
    await expect(page.getByText('alert-triangle', { exact: true })).toHaveCount(0)
    const topologyBoxes = await topologyNodes.evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect()
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }
    }))
    for (let i = 0; i < topologyBoxes.length; i += 1) {
      for (let j = i + 1; j < topologyBoxes.length; j += 1) {
        const a = topologyBoxes[i]
        const b = topologyBoxes[j]
        const overlaps = a.left < b.right - 1 && a.right > b.left + 1
          && a.top < b.bottom - 1 && a.bottom > b.top + 1
        expect(overlaps, `topology nodes ${i} and ${j} should not overlap`).toBeFalsy()
      }
    }

    const topologySvg = page.getByRole('img', { name: '本体拓扑图' })
    const zoomLevel = page.getByTestId('ontology-zoom-level')
    const networkViewport = page.getByTestId('ontology-network-viewport')
    const graphBox = await topologySvg.boundingBox()
    expect(graphBox).not.toBeNull()
    if (!graphBox) throw new Error('topology SVG must have a visible bounding box')

    await page.mouse.move(graphBox.x + graphBox.width / 2, graphBox.y + graphBox.height / 2)
    await page.mouse.wheel(0, -240)
    await expect(zoomLevel).not.toHaveText('100%')

    const transformBeforeDrag = await networkViewport.getAttribute('transform')
    await page.mouse.move(graphBox.x + 32, graphBox.y + graphBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(graphBox.x + 92, graphBox.y + graphBox.height / 2 + 44, { steps: 4 })
    await page.mouse.up()
    await expect(networkViewport).not.toHaveAttribute('transform', transformBeforeDrag || '')

    await page.mouse.dblclick(graphBox.x + 32, graphBox.y + graphBox.height / 2)
    await expect(zoomLevel).toHaveText('100%')
    await expect(networkViewport).toHaveAttribute('transform', /^translate\(0 0\)/)

    await page.getByTestId('workspace-view-toggle').click()
    await expect(page.getByRole('heading', { name: '数据推演图谱' })).toBeVisible()
    await expect(page.getByTestId('instance-knowledge-graph')).toBeVisible()
    await expect(page.getByText('3 个实例已加载')).toBeVisible()
    await expect(page.getByText('2 条真实关系')).toBeVisible()

    await page.getByRole('button', { name: 'L1' }).click()
    await expect(page.getByText('0 个实例已加载')).toBeVisible()
    await page.getByRole('button', { name: 'L2' }).click()
    await expect(page.getByText('3 个实例已加载')).toBeVisible()

    await page.getByLabel('快速选择实例').selectOption('instance:inst-device-' + suffix)
    await expect(page.getByTestId('graph-inspector')).toContainText('DEV-A')
    await page.getByRole('button', { name: '展开字段' }).click()
    await expect(page.getByRole('button', { name: 'L3' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByText('2 个字段节点')).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByTestId('graph-inspector')).toContainText('运行状态')

    await page.getByTestId('instance-knowledge-graph').getByRole('button', { name: '路径', exact: true }).click()
    await page.getByLabel('起点实例').selectOption('inst-device-' + suffix)
    await page.getByLabel('终点实例').selectOption('inst-order-' + suffix)
    await page.getByRole('button', { name: '查找路径' }).click()
    await expect(page.getByTestId('analysis-summary')).toContainText('找到 1 条候选路径')
    await expect(page.getByTestId('analysis-summary')).toContainText('DEV-A → WO-C')

    await page.getByRole('button', { name: '推演' }).click()
    await page.getByLabel('快速选择实例').selectOption('instance:inst-device-' + suffix)
    await page.getByLabel('字段').selectOption('status')
    await page.getByLabel('拟议新值').fill('maintenance')
    await page.getByRole('button', { name: '只读推演' }).click()
    await expect(page.getByTestId('analysis-summary')).toContainText('直接 1 · 间接 1 · 未写入真实数据')
    await expect(page.getByTestId('analysis-summary')).toContainText('不等同于确定的业务因果')

    const detail = await api<any>(
      request,
      token,
      'get',
      '/api/v2/formal/ontologies/' + ontology.id + '/agent/graph/instances/inst-device-' + suffix,
    )
    expect(detail.properties.status).toBe('running')

    if (process.env.PLAYWRIGHT_EXPECT_LLM === '1') {
      const modelSelect = page.getByLabel('选择对话模型')
      const flashModelValue = await modelSelect.locator('option').evaluateAll(options =>
        options.find(option => option.textContent?.toLowerCase().includes('flash'))?.getAttribute('value'),
      )
      expect(flashModelValue, 'an enabled Flash model must be available').toBeTruthy()
      await modelSelect.selectOption(flashModelValue!)
      await page.getByRole('button', { name: '让助手分析影响与建议' }).click()
      await expect(page.getByText('关联影响预演', { exact: true }).last()).toBeVisible({ timeout: 90_000 })
      await expect(page.getByText(/关系可达|关联范围|直接关联/).last()).toBeVisible({ timeout: 90_000 })
      await expect(page.getByRole('heading', { name: '数据推演图谱' })).toBeVisible()
    }
  } finally {
    await request.delete(API + '/api/v1/ontologies/' + ontology.id, {
      headers: { Authorization: 'Bearer ' + token },
    })
  }
})
