import { expect, test, type Page, type Route } from '@playwright/test'

// 个人资料弹窗（MYW-56）：头像下拉 → 个人资料；用户名只读、邮箱可改、
// 密码修改走既有接口、私有环境变量 key/value 全量保存。

const now = '2026-07-21T08:00:00+00:00'
const initialUser = {
  id: 'u-profile',
  username: 'profile-tester',
  email: 'profile@example.com',
  role: 'admin',
  is_active: true,
  created_at: now,
  menu_permissions: [],
}

const json = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data, message: 'ok' }),
})

async function mockPlatformShell(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    // 与登录成功后的本地状态一致：预置完整 user，Layout 直接可用
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: {
          id: 'u-profile',
          username: 'profile-tester',
          email: 'profile@example.com',
          role: 'admin',
          is_active: true,
          created_at: '2026-07-21T08:00:00+00:00',
        },
      },
      version: 0,
    }))
  })

  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/v2/inbox/summary') {
      return json(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    if (path === '/api/v2/inbox') {
      return json(route, { items: [], nextCursor: null, hasMore: false })
    }
    return json(route, [])
  })
}

async function openProfileDialog(page: Page) {
  await page.getByRole('button', { name: '用户中心' }).click()
  await page.getByRole('button', { name: /个人资料/ }).click()
  const dialog = page.getByRole('dialog', { name: '个人资料' })
  await expect(dialog).toBeVisible()
  return dialog
}

test('个人资料弹窗展示只读用户名，修改邮箱后同步本地登录态', async ({ page }) => {
  await mockPlatformShell(page)
  let profilePutBody: Record<string, unknown> | null = null

  await page.route('**/api/v1/auth/profile', async route => {
    if (route.request().method() !== 'PUT') return json(route, initialUser)
    profilePutBody = route.request().postDataJSON() as Record<string, unknown>
    const email = String((profilePutBody as { email?: string }).email ?? '')
    return json(route, { ...initialUser, email })
  })

  await page.goto('/#/inbox', { waitUntil: 'domcontentloaded' })
  const dialog = await openProfileDialog(page)

  // 用户名只读：disabled 且不可编辑（账号唯一标识）
  const usernameInput = dialog.getByLabel('用户名')
  await expect(usernameInput).toBeDisabled()
  await expect(usernameInput).toHaveValue('profile-tester')

  await expect(dialog.getByLabel('邮箱')).toHaveValue('profile@example.com')

  await dialog.getByLabel('邮箱').fill('renamed@example.com')
  await dialog.getByRole('button', { name: '保存资料' }).click()

  await expect(profilePutBody).toEqual({ email: 'renamed@example.com' })
  await expect(dialog.getByText('资料已更新')).toBeVisible()

  // PUT 返回的用户写回持久化的 auth-store，下次进入平台无需重新拉取
  const storedEmail = await page.evaluate(
    () => (JSON.parse(localStorage.getItem('auth-store')!) as { state: { user: { email: string } } }).state.user.email,
  )
  expect(storedEmail).toBe('renamed@example.com')
})

test('修改密码时验证当前密码，失败提示错误、成功后清空输入', async ({ page }) => {
  await mockPlatformShell(page)
  const passwordBodies: Array<Record<string, unknown>> = []

  await page.route('**/api/v1/auth/password', async route => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    passwordBodies.push(body)
    if (body.current_password === 'wrong-pass') {
      return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: '当前密码不正确' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Password updated' }) })
  })

  await page.goto('/#/inbox', { waitUntil: 'domcontentloaded' })
  const dialog = await openProfileDialog(page)

  await dialog.getByLabel('当前密码').fill('wrong-pass')
  await dialog.getByLabel('新密码').fill('newpass123')
  await dialog.getByRole('button', { name: '更新密码' }).click()
  await expect(dialog.getByText('当前密码不正确')).toBeVisible()

  await dialog.getByLabel('当前密码').fill('right-pass')
  await dialog.getByRole('button', { name: '更新密码' }).click()
  await expect(dialog.getByText('密码已更新')).toBeVisible()
  await expect(dialog.getByLabel('当前密码')).toHaveValue('')
  await expect(dialog.getByLabel('新密码')).toHaveValue('')
  expect(passwordBodies).toHaveLength(2)
})

test('私有环境变量支持增删改并全量保存', async ({ page }) => {
  await mockPlatformShell(page)
  // 用容器对象持有异步回调里的捕获值，避免 TS 控制流把变量收窄成 never
  const captured: { envPut?: { items?: Array<{ key: string; value: string }> } } = {}
  let stored: Array<{ key: string; value: string }> = [
    { key: 'EXISTING_KEY', value: 'existing-value' },
  ]

  await page.route('**/api/v1/auth/env-vars', async route => {
    if (route.request().method() === 'PUT') {
      captured.envPut = route.request().postDataJSON() as { items?: Array<{ key: string; value: string }> }
      stored = [...(captured.envPut.items ?? [])].sort((a, b) => a.key.localeCompare(b.key))
      return json(route, stored)
    }
    return json(route, stored)
  })

  await page.goto('/#/inbox', { waitUntil: 'domcontentloaded' })
  const dialog = await openProfileDialog(page)

  // 存量变量回显
  await expect(dialog.getByLabel('第 1 个变量名')).toHaveValue('EXISTING_KEY')

  // 新增一行并填写
  await dialog.getByRole('button', { name: '添加变量' }).click()
  await dialog.getByLabel('第 2 个变量名').fill('NEW_KEY')
  await dialog.getByLabel('第 2 个变量值').fill('new-value')

  await dialog.getByRole('button', { name: '保存变量' }).click()
  await expect(dialog.getByText('环境变量已保存')).toBeVisible()
  expect(captured.envPut?.items).toEqual([
    { key: 'EXISTING_KEY', value: 'existing-value' },
    { key: 'NEW_KEY', value: 'new-value' },
  ])

  // 删除一行后再次保存，列表按全量语义更新
  await dialog.getByRole('button', { name: '删除变量 EXISTING_KEY' }).click()
  await dialog.getByRole('button', { name: '保存变量' }).click()
  await expect(dialog.getByText('环境变量已保存')).toBeVisible()
  expect(captured.envPut?.items).toEqual([{ key: 'NEW_KEY', value: 'new-value' }])
  await expect(dialog.getByLabel('第 1 个变量名')).toHaveValue('NEW_KEY')
})
