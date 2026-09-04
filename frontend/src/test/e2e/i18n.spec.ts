import { test, expect } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

async function login(page: any) {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
  await page.getByLabel('密码', { exact: true }).fill(STACK_ADMIN_PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForURL('**/#/super-assistant')
}

test.describe('Internationalization (i18n)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('current platform renders its default Chinese interface', async ({ page }) => {
    await page.goto('/#/overview')
    await expect(page.getByRole('heading', { name: '本体治理中枢', exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: '平台概览', exact: true })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '超级助手', exact: true })).toBeVisible()
  })

  test('login page has Chinese labels', async ({ page }) => {
    await page.goto('/#/login')
    await expect(page.getByLabel('用户名', { exact: true })).toBeVisible()
    await expect(page.getByLabel('密码', { exact: true })).toBeVisible()
  })
})
