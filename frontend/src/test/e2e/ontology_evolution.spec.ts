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

async function api<T>(request: APIRequestContext, token: string, method: 'get' | 'post' | 'put', path: string, data?: unknown): Promise<T> {
  const response = await request[method](`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data,
  })
  expect(response.ok(), `${method.toUpperCase()} ${path}: ${await response.text()}`).toBeTruthy()
  const body = await response.json()
  return body.data ?? body
}

async function verifyReadonlyGraphInspection(page: Page, objectTypeId: string) {
  const objectButton = page.getByRole('button', { name: /查看对象实体，共 1 个/ })
  const linkButton = page.getByRole('button', { name: /查看实体关系，共 1 个/ })
  const actionButton = page.getByRole('button', { name: /查看执行动作，共 1 个/ })
  const functionButton = page.getByRole('button', { name: /查看激活函数，共 1 个/ })
  await expect(objectButton).toBeEnabled()
  await expect(linkButton).toBeEnabled()
  await expect(actionButton).toBeEnabled()
  await expect(functionButton).toBeEnabled()

  await objectButton.click()
  await expect(page.getByRole('heading', { name: '对象实体列表' })).toBeVisible()
  await page.getByTitle('查看对象详情').click()
  await expect(page.getByTestId('readonly-definition-detail')).toContainText('订单号')
  await page.getByRole('button', { name: '关闭详情' }).click()

  await linkButton.click()
  await expect(page.getByRole('heading', { name: '实体关系列表' })).toBeVisible()
  await page.getByTitle('查看关系详情').click()
  await expect(page.getByTestId('readonly-definition-detail')).toContainText('一对一 (1:1)')
  await page.getByRole('button', { name: '关闭详情' }).click()

  await actionButton.click()
  await expect(page.getByRole('heading', { name: '动作列表' })).toBeVisible()
  await page.getByTitle('查看动作详情').click()
  await expect(page.getByTestId('readonly-definition-detail')).toContainText('人工审批')
  await page.getByRole('button', { name: '关闭详情' }).click()

  await functionButton.click()
  await expect(page.getByRole('heading', { name: '函数列表' })).toBeVisible()
  await page.getByTitle('查看函数详情').click()
  await expect(page.getByTestId('readonly-definition-detail')).toContainText('object.name')
  await page.getByRole('button', { name: '关闭详情' }).click()

  const node = page.locator(`.react-flow__node[data-id="${objectTypeId}"]`)
  await expect(node).toBeVisible()
  await expect(node.getByTestId('object-type-icon')).toHaveText('📦')
  await expect(node.getByTestId('object-type-icon')).not.toContainText('真机订单')
  const before = await node.boundingBox()
  expect(before).toBeTruthy()
  const layoutSaved = page.waitForResponse(response =>
    response.request().method() === 'PUT'
      && response.url().includes('/api/v2/ontologies/')
      && response.url().endsWith('/layout'))
  await page.mouse.move(before!.x + before!.width / 2, before!.y + before!.height / 2)
  await page.mouse.down()
  await page.mouse.move(before!.x + before!.width / 2 + 110, before!.y + before!.height / 2 + 70, { steps: 8 })
  await page.mouse.up()
  expect((await layoutSaved).ok()).toBeTruthy()
  const after = await node.boundingBox()
  expect(after).toBeTruthy()
  expect(Math.abs(after!.x - before!.x)).toBeGreaterThan(50)

  await node.dblclick()
  await expect(page.locator('h2').filter({ hasText: '真机订单' })).toBeVisible()
  await expect(page.getByTestId('readonly-definition-detail')).toContainText('订单号')
  await page.getByRole('button', { name: '关闭详情' }).click()
  await expect(page.getByRole('button', { name: /保存全部更改/ })).toHaveCount(0)
}

test('complete branch → real-data trial → reviewed release works in the browser', async ({ page, request }) => {
  test.setTimeout(90_000)
  const token = await login(page)
  const suffix = Date.now().toString(36)
  const objectTypeId = `ot-browser-order-${suffix}`
  const ontology = await api<any>(request, token, 'post', '/api/v1/ontologies', {
    name: `版本演进真机-${suffix}`, domain: '供应链', description: 'Playwright lifecycle check',
  })
  expect(ontology.version).toBe('v0')
  expect(ontology.current_release_version).toBe('v0')
  const tree = await api<any>(request, token, 'get', `/api/v2/ontologies/${ontology.id}/version-tree`)
  const root = tree.versions.find((item: any) => item.version_number === 'v0')
  const draft = await api<any>(request, token, 'post', `/api/v2/ontologies/${ontology.id}/versions/${root.id}/drafts`, {
    versionLabel: '浏览器验证分支', description: '真实湖数据隔离试跑',
  })
  const saved = await api<any>(request, token, 'put', `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/workspace`, {
    baseRevision: `${draft.revision}:${draft.snapshot_hash}`,
    version: draft.version_number,
    objectTypes: [{
      id: objectTypeId, name: 'BrowserOrder', displayName: '真机订单', icon: '真机订单',
      primaryKey: 'p-id', positionX: 100, positionY: 100,
      properties: [
        { id: 'p-id', name: 'id', displayName: '订单号', type: 'string', required: true },
        { id: 'p-name', name: 'name', displayName: '名称', type: 'string', required: true },
      ],
    }],
    linkTypes: [{
      id: `lt-browser-order-${suffix}`, name: 'order_lineage', displayName: '订单追踪',
      sourceObjectTypeId: objectTypeId, targetObjectTypeId: objectTypeId,
      cardinality: 'one-to-one', description: '订单自身追踪关系', properties: [],
    }],
    actions: [{
      id: `act-browser-order-${suffix}`, name: 'review_order', displayName: '审核订单',
      description: '核对订单定义', objectTypeId, requiresApproval: true,
      parameters: [{ id: 'ap-note', name: 'note', displayName: '审核备注', type: 'string', required: false }],
      rules: [],
    }],
    functions: [{
      id: `fn-browser-order-${suffix}`, name: 'order_name', displayName: '订单名称',
      description: '读取订单名称', functionType: 'object', language: 'expression',
      targetObjectTypeId: objectTypeId, parameters: [], returnType: 'string',
      body: 'object.name', cacheStrategy: 'none', enabled: true,
    }],
    instances: [], linkInstances: [],
  })
  const dataset = await api<any>(request, token, 'post', '/api/v2/datasets/create-table', {
    name: `真机订单数据-${suffix}`,
    columns: [
      { name: 'id', display_name: '订单号', type: 'string', nullable: false },
      { name: 'name', display_name: '名称', type: 'string', nullable: false },
    ],
    primary_key: 'id',
  })
  const upload = await request.post(`${API}/api/v2/datasets/${dataset.id}/upload`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      file: {
        name: 'orders.csv', mimeType: 'text/csv',
        buffer: Buffer.from('id,name\nE2E-1,真机一号\nE2E-2,真机二号\n'),
      },
    },
  })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  await api<any>(request, token, 'put', `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/workspace/mappings`, {
    baseRevision: saved.revision,
    mappings: [{
      id: `map-browser-order-${suffix}`, curatedDatasetId: dataset.id,
      entityClass: 'BrowserOrder', targetObjectTypeId: objectTypeId,
      fieldMapping: { id: 'id', name: 'name', __primary_key__: 'id' },
      status: 'draft', confidence: 1,
    }],
    linkMappings: [{
      id: `link-map-browser-order-${suffix}`,
      linkTypeId: `lt-browser-order-${suffix}`,
      relationType: 'order_lineage',
      srcDatasetId: dataset.id,
      tgtDatasetId: dataset.id,
      edgeDatasetId: null,
      srcKey: 'id',
      tgtKey: 'id',
      fieldMapping: {},
      status: 'draft',
    }],
    sentinels: [],
  })

  // 日常入口不再要求用户选择版本：列表“查看”先进入本体总览。
  await page.goto('/#/ontologies')
  const ontologyCard = page.locator('article').filter({ hasText: ontology.name })
  await ontologyCard.getByRole('button', { name: '查看', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}$`))
  await expect(page.getByTestId('current-release-version')).toHaveText('v0')
  await expect(page.getByRole('button', { name: '本体总览' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByText('这里始终展示当前发布投影。')).toHaveCount(0)
  await page.getByRole('button', { name: '查看当前发布图谱' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph$`))

  await page.goto(`/#/ontologies/${ontology.id}`)
  await expect(page.getByTestId('current-release-version')).toHaveText('v0')
  await page.getByRole('button', { name: '查看历史版本' }).click()
  await expect(page.getByText('在秩序中演化')).toBeVisible()
  await expect(page.getByTestId('version-tree')).toBeVisible()
  await expect(page.getByTestId('version-node-v0')).toContainText('当前发布')
  const draftRow = page.getByTestId('version-node-v0.1')
  await expect(draftRow).toContainText('草稿态')
  await expect(draftRow).not.toContainText('真实湖数据隔离试跑')
  await expect(draftRow.getByRole('button', { name: '编辑模型' })).toBeVisible()
  await expect(draftRow.getByRole('button', { name: 'v0.1 更多操作' })).toBeVisible()

  await draftRow.getByRole('button', { name: '进入试跑' }).click()
  const trialDialog = page.getByRole('dialog', { name: '隔离试跑结果' })
  await expect(trialDialog).toBeVisible({ timeout: 20_000 })
  await expect(trialDialog.getByText('外部动作执行数：0')).toBeVisible()
  await trialDialog.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(draftRow).toContainText('试跑态')
  await expect(draftRow.getByRole('button', { name: '编辑模型' })).toHaveCount(0)

  // 试跑态虽然冻结结构，但模型定义必须可查看，画布仍可移动。
  await page.goto(`/#/ontologies/${ontology.id}/graph?versionId=${draft.id}`)
  await expect(page.getByText(/试跑态 v0\.1 · 可查看定义并保存画布布局/)).toBeVisible({ timeout: 20_000 })
  await verifyReadonlyGraphInspection(page, objectTypeId)
  const movedTrial = await api<any>(request, token, 'get', `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/workspace`)
  const trialObject = movedTrial.objectTypes.find((item: any) => item.id === objectTypeId)
  const trialPosition = { x: trialObject.positionX, y: trialObject.positionY }
  expect(trialPosition).not.toEqual({ x: 100, y: 100 })
  await page.goto(`/#/ontologies/${ontology.id}`)
  await page.getByRole('button', { name: '查看历史版本' }).click()
  await expect(page.getByTestId('version-tree')).toBeVisible()

  await page.getByTestId('version-node-v0.1').getByRole('button', { name: '审核发布' }).click()
  await expect(page.getByText('审核 v0.1 的发布影响')).toBeVisible()
  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page.getByTestId('version-node-v1')).toContainText('当前发布', { timeout: 20_000 })
  await expect(draftRow).toContainText('已晋级')

  // 只有版本演进里可以打开历史快照，且历史发布版严格只读。
  await page.getByTestId('version-node-v0').getByRole('button', { name: '查看快照' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph\\?versionId=`))
  await expect(page.getByText(/历史发布 v0 · 可查看定义并保存画布布局/)).toBeVisible({ timeout: 20_000 })

  // 本体结构页的主 CTA 始终进入最新发布版；开始修改时无需再次选择版本。
  await page.goto(`/#/ontologies/${ontology.id}?tab=design`)
  await expect(page.getByTestId('current-release-version')).toHaveText('v1')
  await page.getByRole('button', { name: '打开图谱编辑器修改模型' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph$`))
  await expect(page.getByText(/当前发布 v1 · 可查看定义并保存画布布局/)).toBeVisible({ timeout: 20_000 })
  const inheritedRelease = await api<any>(request, token, 'get', `/api/v2/formal/ontologies/${ontology.id}/full`)
  const inheritedReleaseObject = inheritedRelease.objectTypes.find((item: any) => item.id === objectTypeId)
  expect({ x: inheritedReleaseObject.positionX, y: inheritedReleaseObject.positionY }).toEqual(trialPosition)
  await verifyReadonlyGraphInspection(page, objectTypeId)
  const movedRelease = await api<any>(request, token, 'get', `/api/v2/formal/ontologies/${ontology.id}/full`)
  const releaseObject = movedRelease.objectTypes.find((item: any) => item.id === objectTypeId)
  expect({ x: releaseObject.positionX, y: releaseObject.positionY }).not.toEqual(trialPosition)

  // 哨兵入口只出现在当前发布态；只读模型不应阻止运行态面板挂载。
  await page.getByTitle('打开菜单').click()
  await expect(page.getByRole('button', { name: 'API 文档' })).toHaveCount(0)
  await page.getByRole('button', { name: '哨兵引擎' }).click()
  await expect(page.getByRole('heading', { name: '哨兵引擎' })).toBeVisible()
  await page.getByRole('button', { name: '关闭哨兵引擎' }).click()

  await page.getByRole('button', { name: '基于此版本开始修改' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph\\?versionId=`))
  await expect(page.getByText(/草稿 v1\.1 · 可编辑并查看全部模型定义/)).toBeVisible({ timeout: 20_000 })

  const finalTree = await api<any>(request, token, 'get', `/api/v2/ontologies/${ontology.id}/version-tree`)
  expect(finalTree.current_release_number).toBe('v1')
  expect(finalTree.versions.some((item: any) => item.version_number === 'v1.1')).toBeTruthy()
  const releasedOntology = await api<any>(request, token, 'get', `/api/v1/ontologies/${ontology.id}`)
  expect(releasedOntology.version).toBe('v1')
  expect(releasedOntology.current_release_version).toBe('v1')
  expect(releasedOntology.current_release_id).toBe(finalTree.current_release_id)
  const instances = await api<any[]>(request, token, 'get', `/api/v2/formal/ontologies/${ontology.id}/instances`)
  expect(instances).toHaveLength(2)

  // 总览使用当前发布投影与真实 HITL 待办：包裹可选择、可直接决策，结果进入事实流。
  const pendingAction = await api<any>(request, token, 'post', `/api/v2/formal/ontologies/${ontology.id}/run-action`, {
    actionId: `act-browser-order-${suffix}`,
    targetInstanceId: instances[0].id,
    parameters: { note: 'overview-e2e' },
    dryRun: false,
  })
  expect(pendingAction.status).toBe('pending')
  await page.goto(`/#/ontologies/${ontology.id}`)
  await expect(page.getByRole('heading', { name: '本体概况' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '模型资产构成' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '近 7 日运行汇总' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '实例分布与来源' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '映射状态' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '事实类型构成' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '健康检查' })).toHaveCount(0)
  const profileBox = await page.locator('.ontology-profile').boundingBox()
  const kpiBox = await page.locator('.kpi-rail').boundingBox()
  const approvalBox = await page.locator('.approval-pipeline').boundingBox()
  const modelBox = await page.locator('.model-composition').boundingBox()
  const factBox = await page.locator('.fact-composition').boundingBox()
  expect(profileBox).not.toBeNull()
  expect(kpiBox).not.toBeNull()
  expect(approvalBox).not.toBeNull()
  expect(modelBox).not.toBeNull()
  expect(factBox).not.toBeNull()
  expect(Math.abs(profileBox!.width - modelBox!.width)).toBeLessThan(2)
  expect(Math.abs(profileBox!.height - (kpiBox!.height + approvalBox!.height + 14))).toBeLessThan(3)
  expect(Math.abs(kpiBox!.x - approvalBox!.x)).toBeLessThan(2)
  expect(Math.abs(kpiBox!.width - approvalBox!.width)).toBeLessThan(2)
  expect(approvalBox!.x).toBeGreaterThan(profileBox!.x + profileBox!.width)
  expect(kpiBox!.height).toBeLessThan(300)
  expect(factBox!.height).toBeLessThan(260)
  const runtimeSummary = page.locator('.runtime-summary')
  const runtimeStart = runtimeSummary.getByLabel('运行汇总开始日期')
  const runtimeEnd = runtimeSummary.getByLabel('运行汇总结束日期')
  await expect(runtimeStart).toHaveValue('0')
  await expect(runtimeEnd).toHaveValue('6')
  await runtimeStart.press('ArrowRight')
  await expect(runtimeStart).toHaveValue('1')
  await expect(runtimeSummary).toContainText('6 日聚合')
  const approvalPipeline = page.locator('.approval-pipeline')
  await expect(approvalPipeline).toContainText('审核订单')
  await expect(approvalPipeline).toContainText(`真机订单 · ${instances[0].properties.id}`)
  await approvalPipeline.getByRole('button', { name: '拒绝', exact: true }).click()
  await expect(approvalPipeline).toContainText('当前没有待审批动作')
  await expect(approvalPipeline).toContainText('已拒绝，决策已进入事实流。')
  const decisionFacts = await api<any[]>(request, token, 'get',
    `/api/v2/formal/ontologies/${ontology.id}/facts/recent?limit=10&kind=decision`)
  expect(decisionFacts.some((fact: any) => fact.value === 'REJECTED')).toBeTruthy()

  await request.delete(`${API}/api/v1/ontologies/${ontology.id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
})
