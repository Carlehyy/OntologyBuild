import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/#/login')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('admin123')
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(/\/#\/overview$/)
  const token = await page.evaluate(() => localStorage.getItem('token'))
  const domainResponse = await page.request.post('http://localhost:5173/api/v1/domains', {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: '供应链', description: '导出导入浏览器测试' },
  })
  if (![201, 409].includes(domainResponse.status())) {
    throw new Error(`测试领域初始化失败: ${domainResponse.status()}`)
  }
}

async function createOntology(page: Page): Promise<string> {
  await page.goto('/#/ontologies')
  const name = `导出导入测试-${Date.now()}`
  await page.getByRole('button', { name: '立即创建', exact: true }).click()
  await page.getByPlaceholder('例如：供应链知识本体').fill(name)
  await page.getByRole('button', { name: '创建本体', exact: true }).click()
  await expect(page.getByText(name, { exact: true })).toBeVisible()
  await page.getByText(name, { exact: true }).click()
  await page.waitForURL(/\/#\/ontologies\/[a-f0-9-]+$/)
  const ontologyId = page.url().split('/').at(-1)
  const token = await page.evaluate(() => localStorage.getItem('token'))
  const objectTypeResponse = await page.request.post(
    `http://localhost:5173/api/v2/formal/ontologies/${ontologyId}/object-types`,
    {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        name: 'Order',
        displayName: '订单',
        primaryKey: 'order_no',
        properties: [{
          id: 'order-no',
          name: 'order_no',
          displayName: '订单号',
          type: 'string',
          required: true,
        }],
      },
    },
  )
  if (objectTypeResponse.status() !== 201) {
    throw new Error(`测试对象类型初始化失败: ${objectTypeResponse.status()}`)
  }
  return name
}

test.describe('Ontology structure export and import', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('detail export downloads JSON directly without a format modal', async ({ page }) => {
    const name = await createOntology(page)
    const downloadPromise = page.waitForEvent('download')

    await page.getByRole('button', { name: '导出本体结构 JSON' }).click()
    const download = await downloadPromise

    expect(download.suggestedFilename()).toBe(`${name}_v0.json`)
    await expect(page.getByText('选择格式下载')).toHaveCount(0)
  })

  test('a downloaded package can be selected from the management page', async ({ page }, testInfo) => {
    await createOntology(page)
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出本体结构 JSON' }).click()
    const download = await downloadPromise
    const packagePath = testInfo.outputPath('ontology-structure.json')
    await download.saveAs(packagePath)

    await page.goto('/#/ontologies')
    await expect(page.getByRole('button', { name: '本地导入' })).toBeVisible()
    const importResponsePromise = page.waitForResponse(response => (
      response.url().includes('/api/v1/ontologies/import') && response.request().method() === 'POST'
    ))
    await page.getByLabel('选择本体结构 JSON 文件').setInputFiles(packagePath)
    const importResponse = await importResponsePromise
    const imported = await importResponse.json()

    expect(importResponse.status()).toBe(201)
    expect(imported.data.ontology.status).toBe('published')
    expect(imported.data.ontology.version).toBe('v0')
    await page.waitForURL(new RegExp(`/#/ontologies/${imported.data.ontology.id}$`))
  })
})
