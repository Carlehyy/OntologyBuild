import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

const API = (
  process.env.PLAYWRIGHT_API_URL
  || process.env.E2E_API_BASE
  || 'http://localhost:8000'
).replace(/\/+$/, '')

async function login(page: Page): Promise<string> {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
  await page.getByLabel('密码', { exact: true }).fill(STACK_ADMIN_PASSWORD)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL('**/#/overview')
  const token = await page.evaluate(() => localStorage.getItem('token'))
  expect(token).toBeTruthy()
  return token!
}

async function removePrompt(
  request: APIRequestContext,
  token: string,
  promptId: string,
) {
  const response = await request.delete(`${API}/api/v1/prompts/${promptId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(response.status(), await response.text()).toBe(204)
}

test.describe('Prompt Management', () => {
  let token = ''

  test.beforeEach(async ({ page }) => {
    token = await login(page)
    await page.goto('/#/settings/prompts')
  })

  test('prompt list page loads from system settings', async ({ page }) => {
    await expect(page).toHaveURL(/\/#\/settings\/prompts$/)
    await expect(page.getByRole('button', { name: '新建提示词', exact: true })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '模版 ID', exact: true })).toBeVisible()
  })

  test('shows the current business-domain filter', async ({ page }) => {
    const domainFilter = page.getByLabel('按业务域筛选提示词', { exact: true })
    await expect(domainFilter).toBeVisible()
    await expect(domainFilter.locator('option')).toContainText([
      '全部领域',
      '供应链',
      '法律',
      '医疗',
      'HR',
      '财务',
      '教育',
      '通用',
      '其他',
    ])
  })

  test('creates a prompt through the settings modal and cleans it up', async ({ page, request }) => {
    const name = `测试提示词-${Date.now()}`
    let promptId = ''
    try {
      await page.getByRole('button', { name: '新建提示词', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: '新建提示词模版' })
      await dialog.getByLabel('名称', { exact: true }).fill(name)
      await dialog.getByLabel('业务域', { exact: true }).selectOption('供应链')
      await dialog.getByLabel('内容', { exact: true }).fill('提取本体信息：{"entities":[]}')

      const responsePromise = page.waitForResponse(response => (
        response.url().endsWith('/api/v1/prompts')
          && response.request().method() === 'POST'
      ))
      await dialog.getByRole('button', { name: '确认保存', exact: true }).click()
      const response = await responsePromise
      expect(response.status(), await response.text()).toBe(201)
      const body = await response.json()
      promptId = (body.data ?? body).id
      await expect(page.getByText(name, { exact: true })).toBeVisible()
    } finally {
      if (promptId) await removePrompt(request, token, promptId)
    }
  })

  test('built-in prompts are seeded', async ({ page }) => {
    await expect(page.getByText('通用本体提取', { exact: true })).toBeVisible()
  })
})
