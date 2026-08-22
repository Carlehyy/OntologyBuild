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
  await page.waitForURL('**/#/agent')
}

test.describe('Settings Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto('/#/settings')
  })

  test('defaults to the surviving user-management module', async ({ page }) => {
    await expect(page).toHaveURL(/\/#\/settings\/users$/)
    await expect(page.getByRole('button', { name: '用户账号', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '新增用户', exact: true })).toBeVisible()
  })

  test('keeps the remaining settings navigation', async ({ page }) => {
    const navigation = page.getByRole('navigation')
    await expect(navigation.getByText('用户管理', { exact: true })).toBeVisible()
    await expect(navigation.getByText('领域设置', { exact: true })).toBeVisible()
  })

  test('does not expose retired settings entries', async ({ page }) => {
    await expect(page.getByText('规则设置', { exact: true })).toHaveCount(0)
    await expect(page.getByText('提示词模板', { exact: true })).toHaveCount(0)
    await expect(page.getByText('开放接口', { exact: true })).toHaveCount(0)
    await expect(page.getByText('MinIO 存储', { exact: true })).toHaveCount(0)
    await expect(page.getByText('工作流配置', { exact: true })).toHaveCount(0)
    await expect(page.getByText('智能体配置', { exact: true })).toHaveCount(0)
  })

  test('legacy settings deep links resolve to user management', async ({ page }) => {
    for (const retired of ['extraction', 'rules', 'prompts', 'open-interfaces', 'minio', 'workflows', 'agents']) {
      await page.goto(`/#/settings/${retired}`)
      await expect(page).toHaveURL(/\/#\/settings\/users$/)
    }
  })
})
