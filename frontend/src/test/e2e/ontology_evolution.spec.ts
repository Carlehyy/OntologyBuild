import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const API = 'http://localhost:8000'

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
    linkTypes: [], actions: [], functions: [], instances: [], linkInstances: [],
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

  await page.goto(`/#/ontologies/${ontology.id}`)
  await page.getByRole('button', { name: '查看历史版本' }).click()
  await expect(page.getByText('在秩序中演化')).toBeVisible()
  await expect(page.getByTestId('version-row-v0')).toContainText('当前发布')
  const draftRow = page.getByTestId('version-row-v0.1')
  await expect(draftRow).toContainText('草稿')

  await draftRow.getByRole('button', { name: '试跑' }).click()
  await expect(page.getByText('隔离试跑结果')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('外部动作执行数：0')).toBeVisible()
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(draftRow).toContainText('试跑通过')

  await draftRow.getByRole('button', { name: '发布' }).click()
  await expect(page.getByText('审核 v0.1 的发布影响')).toBeVisible()
  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page.getByTestId('version-row-v1')).toContainText('当前发布', { timeout: 20_000 })
  await expect(draftRow).toContainText('已晋级')

  const finalTree = await api<any>(request, token, 'get', `/api/v2/ontologies/${ontology.id}/version-tree`)
  expect(finalTree.current_release_number).toBe('v1')
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
