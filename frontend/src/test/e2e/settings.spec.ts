import { test, expect } from '@playwright/test'

async function login(page: any) {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL('**/#/overview')
}

test.describe('Settings Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto('/#/settings/extraction')
  })

  test('settings page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/#\/settings\/extraction$/)
    await expect(page.getByRole('heading', { name: '置信度规则', exact: true })).toBeVisible()
  })

  test('shows extraction rules section', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'LLM 提取约束', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '本体质量验证规则', exact: true })).toBeVisible()
  })

  test('confidence threshold inputs exist', async ({ page }) => {
    await expect(page.locator('input').first()).toBeVisible()
  })

  test('save settings button exists', async ({ page }) => {
    await expect(page.locator('button:has-text("保存")')).toBeVisible()
  })
})
