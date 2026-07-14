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
  const before = await node.boundingBox()
  expect(before).toBeTruthy()
  await page.mouse.move(before!.x + before!.width / 2, before!.y + before!.height / 2)
  await page.mouse.down()
  await page.mouse.move(before!.x + before!.width / 2 + 110, before!.y + before!.height / 2 + 70, { steps: 8 })
  await page.mouse.up()
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
      id: objectTypeId, name: 'BrowserOrder', displayName: '真机订单',
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
    linkMappings: [], sentinels: [],
  })

  // 日常入口不再要求用户选择版本：列表“查看”直接落到当前发布结构。
  await page.goto('/#/ontologies')
  const ontologyCard = page.locator('article').filter({ hasText: ontology.name })
  await ontologyCard.getByRole('button', { name: '查看', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}\\?tab=design$`))
  await expect(page.getByTestId('current-release-version')).toHaveText('v0')
  await expect(page.getByRole('button', { name: '打开图谱编辑器修改模型' })).toBeVisible()

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
  await expect(page.getByText('隔离试跑结果')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('外部动作执行数：0')).toBeVisible()
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(draftRow).toContainText('试跑态')
  await expect(draftRow.getByRole('button', { name: '编辑模型' })).toHaveCount(0)

  // 试跑态虽然冻结结构，但模型定义必须可查看，画布仍可移动。
  await page.goto(`/#/ontologies/${ontology.id}/graph?versionId=${draft.id}`)
  await expect(page.getByText(/试跑态 v0\.1 · 可查看定义和调整画布视图/)).toBeVisible({ timeout: 20_000 })
  await verifyReadonlyGraphInspection(page, objectTypeId)
  const unchangedTrial = await api<any>(request, token, 'get', `/api/v2/ontologies/${ontology.id}/versions/${draft.id}/workspace`)
  const trialObject = unchangedTrial.objectTypes.find((item: any) => item.id === objectTypeId)
  expect({ x: trialObject.positionX, y: trialObject.positionY }).toEqual({ x: 100, y: 100 })
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
  await expect(page.getByText(/历史发布 v0 · 可查看定义和调整画布视图/)).toBeVisible({ timeout: 20_000 })

  // 本体结构页的主 CTA 始终进入最新发布版；开始修改时无需再次选择版本。
  await page.goto(`/#/ontologies/${ontology.id}?tab=design`)
  await expect(page.getByTestId('current-release-version')).toHaveText('v1')
  await page.getByRole('button', { name: '打开图谱编辑器修改模型' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph$`))
  await expect(page.getByText(/当前发布 v1 · 可查看定义和调整画布视图/)).toBeVisible({ timeout: 20_000 })
  await verifyReadonlyGraphInspection(page, objectTypeId)
  const unchangedRelease = await api<any>(request, token, 'get', `/api/v2/formal/ontologies/${ontology.id}/full`)
  const releaseObject = unchangedRelease.objectTypes.find((item: any) => item.id === objectTypeId)
  expect({ x: releaseObject.positionX, y: releaseObject.positionY }).toEqual({ x: 100, y: 100 })
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

  await request.delete(`${API}/api/v1/ontologies/${ontology.id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
})
