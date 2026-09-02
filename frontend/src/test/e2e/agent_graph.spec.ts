import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

const API = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000'

async function login(page: Page): Promise<string> {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
  await page.getByLabel('密码', { exact: true }).fill(STACK_ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/#/super-assistant')
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
  test.setTimeout(process.env.PLAYWRIGHT_EXPECT_LLM === '1' ? 240_000 : 180_000)
  const token = await login(page)
  const suffix = Date.now().toString(36)
  const ontology = await api<any>(request, token, 'post', '/api/v1/ontologies', {
    name: '助手图谱真机-' + suffix,
    domain: '供应链',
    description: '路径和拟议变更关联范围端到端测试',
  })
  const deviceTypeId = 'ot-device-' + suffix
  const taskTypeId = 'ot-task-' + suffix
  const orderTypeId = 'ot-order-' + suffix
  const carriesTypeId = 'lt-carries-' + suffix
  const producesTypeId = 'lt-produces-' + suffix
  const objectTypes = [
    {
      id: deviceTypeId, name: 'Device', displayName: '设备',
      icon: 'radar',
      primaryKey: 'device_no', positionX: 0, positionY: 0,
      properties: [
        { id: 'dp1', name: 'device_no', displayName: '设备编号', type: 'string', required: true },
        { id: 'dp2', name: 'status', displayName: '运行状态', type: 'string', required: false },
      ],
    },
    {
      id: taskTypeId, name: 'Task', displayName: '生产任务',
      icon: 'alert-triangle',
      primaryKey: 'task_no', positionX: 0, positionY: 0,
      properties: [
        { id: 'tp1', name: 'task_no', displayName: '任务编号', type: 'string', required: true },
        { id: 'tp2', name: 'state', displayName: '任务状态', type: 'string', required: false },
      ],
    },
    {
      id: orderTypeId, name: 'WorkOrder', displayName: '工单',
      icon: 'building',
      primaryKey: 'order_no', positionX: 0, positionY: 0,
      properties: [
        { id: 'op1', name: 'order_no', displayName: '工单号', type: 'string', required: true },
        { id: 'op2', name: 'priority', displayName: '优先级', type: 'string', required: false },
      ],
    },
  ]
  const linkTypes = [
    {
      id: carriesTypeId, name: 'carries', displayName: '承载',
      sourceObjectTypeId: deviceTypeId,
      targetObjectTypeId: taskTypeId,
      cardinality: 'one-to-many', properties: [],
    },
    {
      id: producesTypeId, name: 'produces', displayName: '生成',
      sourceObjectTypeId: taskTypeId,
      targetObjectTypeId: orderTypeId,
      cardinality: 'one-to-many', properties: [],
    },
  ]
  let datasetId: string | undefined

  try {
    // A new ontology has a real, immutable, empty v0 release. Draft modeling
    // must not leak into the Agent's published topology before promotion.
    await page.goto('/#/agent')
    await page.getByLabel('选择本体').selectOption(ontology.id)
    await expect(page.getByRole('heading', { name: '本体拓扑图' })).toBeVisible()
    await expect(page.getByText('当前本体暂无可视化对象')).toBeVisible()
    await expect(page.getByTestId('ontology-network-node')).toHaveCount(0)
    await expect(page.getByLabel('选择本体').locator(`option[value="${ontology.id}"]`)).toContainText('· v0')

    const tree = await api<any>(request, token, 'get', `/api/v2/ontologies/${ontology.id}/version-tree`)
    const root = tree.versions.find((item: any) => item.version_number === 'v0')
    expect(root, 'new ontology must expose the immutable v0 release').toBeTruthy()
    const draft = await api<any>(
      request,
      token,
      'post',
      `/api/v2/ontologies/${ontology.id}/versions/${root.id}/drafts`,
      {
        versionLabel: '助手图谱三态验证',
        description: '共享单行数据在隔离试跑通过后发布',
      },
    )
    const saved = await api<any>(
      request,
      token,
      'put',
      `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/workspace`,
      {
        baseRevision: `${draft.revision}:${draft.snapshot_hash}`,
        version: draft.version_number,
        objectTypes,
        linkTypes,
        actions: [],
        functions: [],
        instances: [],
        linkInstances: [],
      },
    )

    const dataset = await api<any>(request, token, 'post', '/api/v2/datasets/create-table', {
      name: '助手图谱共享链路-' + suffix,
      columns: [
        { name: 'row_key', display_name: '链路键', type: 'string', nullable: false },
        { name: 'device_no', display_name: '设备编号', type: 'string', nullable: false },
        { name: 'status', display_name: '运行状态', type: 'string', nullable: false },
        { name: 'task_no', display_name: '任务编号', type: 'string', nullable: false },
        { name: 'state', display_name: '任务状态', type: 'string', nullable: false },
        { name: 'order_no', display_name: '工单号', type: 'string', nullable: false },
        { name: 'priority', display_name: '优先级', type: 'string', nullable: false },
      ],
      primary_key: 'row_key',
    })
    datasetId = dataset.id
    const upload = await request.post(`${API}/api/v2/datasets/${dataset.id}/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: 'assistant-chain.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(
            'row_key,device_no,status,task_no,state,order_no,priority\n'
            + 'CHAIN-1,DEV-A,running,TASK-B,active,WO-C,high\n',
          ),
        },
      },
    })
    expect(upload.ok(), await upload.text()).toBeTruthy()

    await api<any>(
      request,
      token,
      'put',
      `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/workspace/mappings`,
      {
        baseRevision: saved.revision,
        mappings: [
          {
            id: 'map-device-' + suffix,
            curatedDatasetId: dataset.id,
            entityClass: 'Device',
            targetObjectTypeId: deviceTypeId,
            fieldMapping: {
              device_no: 'device_no',
              status: 'status',
              __primary_key__: 'device_no',
              __auto_apply_on_version__: true,
            },
            status: 'draft',
            confidence: 1,
          },
          {
            id: 'map-task-' + suffix,
            curatedDatasetId: dataset.id,
            entityClass: 'Task',
            targetObjectTypeId: taskTypeId,
            fieldMapping: {
              task_no: 'task_no',
              state: 'state',
              __primary_key__: 'task_no',
              __auto_apply_on_version__: true,
            },
            status: 'draft',
            confidence: 1,
          },
          {
            id: 'map-order-' + suffix,
            curatedDatasetId: dataset.id,
            entityClass: 'WorkOrder',
            targetObjectTypeId: orderTypeId,
            fieldMapping: {
              order_no: 'order_no',
              priority: 'priority',
              __primary_key__: 'order_no',
              __auto_apply_on_version__: true,
            },
            status: 'draft',
            confidence: 1,
          },
        ],
        linkMappings: [
          {
            id: 'link-map-carries-' + suffix,
            linkTypeId: carriesTypeId,
            relationType: 'carries',
            srcDatasetId: dataset.id,
            tgtDatasetId: dataset.id,
            edgeDatasetId: null,
            srcKey: 'row_key',
            tgtKey: 'row_key',
            fieldMapping: { __auto_apply_on_version__: true },
            status: 'draft',
          },
          {
            id: 'link-map-produces-' + suffix,
            linkTypeId: producesTypeId,
            relationType: 'produces',
            srcDatasetId: dataset.id,
            tgtDatasetId: dataset.id,
            edgeDatasetId: null,
            srcKey: 'row_key',
            tgtKey: 'row_key',
            fieldMapping: { __auto_apply_on_version__: true },
            status: 'draft',
          },
        ],
        sentinels: [],
      },
    )

    const trial = await api<any>(
      request,
      token,
      'post',
      `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/trial-runs`,
      {},
    )
    expect(trial.status).toBe('passed')
    expect(trial.result?.counts).toMatchObject({ objects: 3, links: 2, datasets: 1 })
    expect(trial.result?.errors).toEqual([])

    const impact = await api<any>(
      request,
      token,
      'get',
      `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/impact`,
    )
    expect(impact.impactHash).toBeTruthy()
    expect(impact.baseOutdated).toBeFalsy()
    expect(impact.releaseReadiness).toMatchObject({
      ready: true,
      blockingCount: 0,
      trialRunId: trial.id,
    })

    const promoted = await api<any>(
      request,
      token,
      'post',
      `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/promote`,
      { trialRunId: trial.id, impactHash: impact.impactHash },
    )
    expect(promoted).toMatchObject({
      version_number: 'v1',
      node_kind: 'release',
      lifecycle_status: 'released',
      trial_run_id: trial.id,
      impact_hash: impact.impactHash,
    })

    const released = await api<any>(
      request,
      token,
      'get',
      `/api/v2/formal/ontologies/${ontology.id}/full`,
    )
    expect(released.instances).toHaveLength(3)
    expect(released.linkInstances).toHaveLength(2)
    const device = released.instances.find((item: any) => item.objectTypeId === deviceTypeId)
    const task = released.instances.find((item: any) => item.objectTypeId === taskTypeId)
    const order = released.instances.find((item: any) => item.objectTypeId === orderTypeId)
    expect(device).toMatchObject({ properties: { device_no: 'DEV-A', status: 'running' } })
    expect(task).toMatchObject({ properties: { task_no: 'TASK-B', state: 'active' } })
    expect(order).toMatchObject({ properties: { order_no: 'WO-C', priority: 'high' } })
    expect(released.linkInstances).toEqual(expect.arrayContaining([
      expect.objectContaining({
        linkTypeId: carriesTypeId,
        sourceObjectId: device.id,
        targetObjectId: task.id,
      }),
      expect.objectContaining({
        linkTypeId: producesTypeId,
        sourceObjectId: task.id,
        targetObjectId: order.id,
      }),
    ]))

    // Reload the Agent so its ontology list observes the new authoritative v1 pointer.
    await page.reload()
    await page.getByLabel('选择本体').selectOption(ontology.id)
    await expect(page.getByLabel('选择本体').locator(`option[value="${ontology.id}"]`)).toContainText('· v1')
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

    await page.getByLabel('快速选择实例').selectOption('instance:' + device.id)
    await expect(page.getByTestId('graph-inspector')).toContainText('DEV-A')
    await page.getByRole('button', { name: '展开字段' }).click()
    await expect(page.getByRole('button', { name: 'L3' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByText('2 个字段节点')).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByTestId('graph-inspector')).toContainText('运行状态')

    await page.getByTestId('instance-knowledge-graph').getByRole('button', { name: '路径', exact: true }).click()
    await page.getByLabel('起点实例').selectOption(device.id)
    await page.getByLabel('终点实例').selectOption(order.id)
    await page.getByRole('button', { name: '查找路径' }).click()
    await expect(page.getByTestId('analysis-summary')).toContainText('找到 1 条候选路径')
    await expect(page.getByTestId('analysis-summary')).toContainText('DEV-A → WO-C')

    await page.getByTestId('instance-knowledge-graph')
      .getByRole('button', { name: '推演', exact: true }).click()
    await page.getByLabel('快速选择实例').selectOption('instance:' + device.id)
    await page.getByLabel('字段').selectOption('status')
    await page.getByLabel('拟议新值').fill('maintenance')
    await page.getByRole('button', { name: '只读推演' }).click()
    await expect(page.getByTestId('analysis-summary')).toContainText('直接 1 · 间接 1 · 未写入真实数据')
    await expect(page.getByTestId('analysis-summary')).toContainText('不等同于确定的业务因果')

    const detail = await api<any>(
      request,
      token,
      'get',
      '/api/v2/formal/ontologies/' + ontology.id + '/agent/graph/instances/' + device.id,
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
    if (datasetId) {
      await request.delete(API + '/api/v2/datasets/' + datasetId, {
        headers: { Authorization: 'Bearer ' + token },
      })
    }
  }
})
