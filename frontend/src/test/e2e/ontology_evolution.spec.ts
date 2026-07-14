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

  await draftRow.getByRole('button', { name: '进入试跑' }).click()
  await expect(page.getByText('隔离试跑结果')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('外部动作执行数：0')).toBeVisible()
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(draftRow).toContainText('试跑态')
  await expect(draftRow.getByRole('button', { name: '编辑结构' })).toHaveCount(0)

  await draftRow.getByRole('button', { name: '审核并发布' }).click()
  await expect(page.getByText('审核 v0.1 的发布影响')).toBeVisible()
  await page.getByRole('button', { name: '确认发布' }).click()
  await expect(page.getByTestId('version-node-v1')).toContainText('当前发布', { timeout: 20_000 })
  await expect(draftRow).toContainText('已晋级')

  // 只有版本演进里可以打开历史快照，且历史发布版严格只读。
  await page.getByTestId('version-node-v0').getByRole('button', { name: '查看快照' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph\\?versionId=`))
  await expect(page.getByText(/历史发布 v0 · 只读结构快照/)).toBeVisible({ timeout: 20_000 })

  // 本体结构页的主 CTA 始终进入最新发布版；开始修改时无需再次选择版本。
  await page.goto(`/#/ontologies/${ontology.id}?tab=design`)
  await expect(page.getByTestId('current-release-version')).toHaveText('v1')
  await page.getByRole('button', { name: '打开图谱编辑器修改模型' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph$`))
  await expect(page.getByText(/当前发布 v1 · 结构不可修改/)).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: '基于此版本开始修改' }).click()
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ontology.id}/graph\\?versionId=`))
  await expect(page.getByText(/草稿 v1\.1 · 可编辑结构与映射/)).toBeVisible({ timeout: 20_000 })

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
