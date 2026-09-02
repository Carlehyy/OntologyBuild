import { test, expect } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

async function loginAs(
  page: any,
  username = STACK_ADMIN_USERNAME,
  password = STACK_ADMIN_PASSWORD,
) {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.click('button[type="submit"]')
  await page.waitForURL('**/#/super-assistant')
}

test.describe('Authentication', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/#/login')
    await expect(page.locator('body')).toContainText('OpenOntology')
    await expect(page.locator('button[type="submit"]')).toBeVisible()
  })

  test('redirects unauthenticated users to login', async ({ page }) => {
    await page.goto('/#/overview')
    await expect(page).toHaveURL(/\/#\/login(?:\?|$)/)
  })

  test('login with valid credentials', async ({ page }) => {
    await loginAs(page)
    await expect(page).toHaveURL(/\/#\/super-assistant$/)
  })

  test('login with wrong password shows error', async ({ page }) => {
    await page.goto('/#/login')
    await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
    await page.getByLabel('密码', { exact: true }).fill('wrongpassword')
    await page.click('button[type="submit"]')
    await expect(page.getByRole('alert')).toContainText(/用户名|密码|登录失败|incorrect/i)
  })

  test('unknown register route redirects to login', async ({ page }) => {
    await page.goto('/#/register')
    await expect(page).toHaveURL(/\/#\/login$/)
    await expect(page.getByRole('heading', { name: '欢迎回来', exact: true })).toBeVisible()
  })

  test('logout redirects to login', async ({ page }) => {
    await loginAs(page)
    await page.click('button:has-text("退出")')
    await expect(page).toHaveURL(/\/#\/login(?:\?|$)/)
  })
})
