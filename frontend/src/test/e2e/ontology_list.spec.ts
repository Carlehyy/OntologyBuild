import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const API = (
  process.env.PLAYWRIGHT_API_URL
  || process.env.E2E_API_BASE
  || 'http://localhost:8000'
).replace(/\/+$/, '')

async function login(page: Page): Promise<string> {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('admin123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL('**/#/overview')
  const token = await page.evaluate(() => localStorage.getItem('token'))
  expect(token).toBeTruthy()
  return token!
}

async function createFromList(page: Page, name: string): Promise<string> {
  const responsePromise = page.waitForResponse(response => (
    response.url().includes('/api/v1/ontologies')
      && response.request().method() === 'POST'
  ))
  await page.getByRole('button', { name: '立即创建', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '新建本体' })
  await dialog.getByLabel('本体名称', { exact: true }).fill(name)
  await dialog.getByRole('button', { name: '创建本体', exact: true }).click()
  const response = await responsePromise
  expect(response.ok(), await response.text()).toBeTruthy()
  const body = await response.json()
  await expect(page.getByRole('button', { name, exact: true })).toBeVisible()
  return (body.data ?? body).id
}

async function removeOntology(
  request: APIRequestContext,
  token: string,
  ontologyId: string,
) {
  const response = await request.delete(`${API}/api/v1/ontologies/${ontologyId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
}

test.describe('Ontology List', () => {
  let token = ''

  test.beforeEach(async ({ page }) => {
    token = await login(page)
    await page.goto('/#/ontologies')
  })

  test('ontology list page loads', async ({ page }) => {
    await expect(page.getByRole('region', { name: '本体筛选' })).toBeVisible()
    await expect(page.getByRole('button', { name: '立即创建', exact: true }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: '本地导入', exact: true })).toBeVisible()
  })

  test('create ontology modal opens', async ({ page }) => {
    await page.getByRole('button', { name: '立即创建', exact: true }).first().click()
    const dialog = page.getByRole('dialog', { name: '新建本体' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByLabel('本体名称', { exact: true })).toBeVisible()
    await expect(dialog.getByRole('button', { name: '创建本体', exact: true })).toBeDisabled()
  })

  test('create and view ontology', async ({ page, request }) => {
    const uniqueName = `测试本体-${Date.now()}`
    const ontologyId = await createFromList(page, uniqueName)
    try {
      await page.getByRole('button', { name: uniqueName, exact: true }).click()
      await expect(page).toHaveURL(new RegExp(`/#/ontologies/${ontologyId}$`))
      await expect(page.getByTestId('current-release-version')).toHaveText('v0')
    } finally {
      await removeOntology(request, token, ontologyId)
    }
  })

  test('filter ontologies by name', async ({ page }) => {
    await page.getByLabel('按本体名称筛选', { exact: true }).fill('不存在的本体xyz')
    await expect(page.getByText('没有符合条件的本体', { exact: true })).toBeVisible()
  })

  test('cancel delete dialog', async ({ page, request }) => {
    const name = `删除测试-${Date.now()}`
    const ontologyId = await createFromList(page, name)
    try {
      await page.getByRole('button', { name: `删除本体 ${name}`, exact: true }).click()
      const dialog = page.getByRole('dialog', { name: `删除「${name}」？` })
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: '取消', exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await expect(page.getByRole('button', { name, exact: true })).toBeVisible()
    } finally {
      await removeOntology(request, token, ontologyId)
    }
  })
})
