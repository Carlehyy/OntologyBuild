import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-08-13T08:00:00+00:00'

const json = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

const projectRow = {
  id: 'wm-project-1',
  name: '台区负荷短期推演',
  description: '基于历史负荷曲线做 24 步外推',
  engine_type: 'statistical',
  status: 'draft',
  version_count: 1,
  created_at: now,
  updated_at: now,
}

const projectDetail = {
  ...projectRow,
  script: 'def simulate(context, actions, horizon):\n    return {"trajectory": [1, 2, 3]}\n',
}

async function seedAuth(page: Page, role = 'admin', menuPermissions?: string[]) {
  await page.addInitScript(([userRole, menus]) => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: {
          id: 'u-wm',
          username: 'wm-tester',
          role: userRole,
          ...(menus ? { menu_permissions: menus } : {}),
        },
      },
      version: 0,
    }))
  }, [role, menuPermissions])
}

async function mockPlatformShell(page: Page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/v2/inbox/summary') {
      return json(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    if (path === '/api/v2/inbox') return json(route, { items: [], nextCursor: null, hasMore: false })
    return json(route, [])
  })
}

async function mockWorldModel(page: Page) {
  await page.route('**/api/v2/world-model/**', route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v2/world-model/projects' && request.method() === 'GET') {
      return json(route, { items: [projectRow], total: 1, page: 1, size: 500 })
    }
    if (path === '/api/v2/world-model/projects' && request.method() === 'POST') {
      return json(route, { ...projectRow, id: 'wm-project-new', name: '新建推演模型' }, 201)
    }
    if (path === `/api/v2/world-model/projects/${projectRow.id}` && request.method() === 'GET') {
      return json(route, projectDetail)
    }
    if (path.endsWith('/execute') && request.method() === 'POST') {
      return json(route, {
        ok: true,
        payload: { trajectory: [100, 102, 105], confidence: 0.8, boundary: '仅适用于居民用户' },
        stdout: 'done\n',
        error: null,
        traceback: '',
        duration_ms: 42,
        kernel_id: 'kernel-e2e',
      })
    }
    if (path.endsWith('/save') && request.method() === 'POST') {
      return json(route, { ok: true, version_no: 2, execution: null })
    }
    if (path.endsWith('/versions') && request.method() === 'GET') {
      return json(route, [{ id: 'v1', version_no: 1, duration_ms: 40, created_at: now }])
    }
    if (path === '/api/v2/world-model/calls/overview') {
      return json(route, { total: 1, failed: 0, avg_duration_ms: 120 })
    }
    if (path === '/api/v2/world-model/calls' && request.method() === 'GET') {
      return json(route, {
        items: [{
          id: 'call-1',
          project_id: projectRow.id,
          service_name: '台区负荷短期推演',
          caller: 'agent-session-1',
          ok: true,
          duration_ms: 120,
          created_at: now,
        }],
        total: 1,
        page: 1,
        size: 20,
      })
    }
    if (path === '/api/v2/world-model/calls/call-1') {
      return json(route, {
        id: 'call-1',
        project_id: projectRow.id,
        service_name: '台区负荷短期推演',
        caller: 'agent-session-1',
        ok: true,
        duration_ms: 120,
        request_payload: { context: { current_value: 100 } },
        response_payload: { trajectory: [100, 102, 105] },
        error: null,
        created_at: now,
      })
    }
    return json(route, [])
  })
}

test('世界模型为一级导航分组，本体管理恢复单项链接', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/overview')

  const sidebar = page.locator('aside')
  // 本体管理恢复为单项链接（不再是分组按钮）
  await expect(sidebar.getByRole('link', { name: '本体管理', exact: true })).toBeVisible()
  // 世界模型是一级分组按钮，展开后出现两个子项并自动导航到推演模型
  const worldModelGroup = sidebar.getByRole('button', { name: '世界模型' })
  await expect(worldModelGroup).toBeVisible()
  await worldModelGroup.click()
  await expect(page).toHaveURL(/#\/world-model\/models$/)
  await expect(sidebar.getByRole('link', { name: '推演模型' })).toBeVisible()
  await expect(sidebar.getByRole('link', { name: '调用记录' })).toBeVisible()
})

test('旧 /ontologies/world-model 路径重定向到一级路由', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.goto('/#/ontologies/world-model')
  await expect(page).toHaveURL(/#\/world-model\/models$/)

  await page.goto('/#/ontologies/world-model/calls')
  await expect(page).toHaveURL(/#\/world-model\/calls$/)
})

test('推演模型列表渲染与筛选，页内 Tab 可切换', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/world-model/models')

  await expect(page.getByRole('heading', { name: '世界模型' })).toBeVisible()
  await expect(page.getByText('台区负荷短期推演')).toBeVisible()

  // 列表筛选（本地过滤）
  await page.getByLabel('按模型名称或描述筛选').fill('不存在的模型')
  await expect(page.getByText('台区负荷短期推演')).toHaveCount(0)
  await page.getByLabel('按模型名称或描述筛选').fill('')
  await expect(page.getByText('台区负荷短期推演')).toBeVisible()

  // Tab 切换到调用记录（页内 Tab 栏，区别于侧边栏子项）
  await page.getByRole('navigation', { name: '世界模型子功能' }).getByRole('link', { name: '调用记录' }).click()
  await expect(page).toHaveURL(/#\/world-model\/calls$/)
  await expect(page.getByText('agent-session-1')).toBeVisible()
})

test('开发页：执行通过才可保存，版本恢复需二次确认', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/world-model/models/${projectRow.id}/develop`)

  // 初始：未修改，保存不可用
  const saveButton = page.getByRole('button', { name: '保存', exact: true })
  await expect(saveButton).toBeDisabled()

  // 修改测试入参（含 JSON 布尔/null，回归场景）
  await page.getByLabel('测试入参 JSON').fill(JSON.stringify({
    context: { current_value: 100, online: true, note: null },
    actions: [],
    horizon: 3,
  }))
  // 修改脚本（dirty=true）；任何修改都会让执行凭证失效
  await page.locator('.cm-content').first().click()
  await page.keyboard.press('End')
  await page.keyboard.type('# tweak')
  await expect(saveButton).toBeDisabled()

  // 执行通过后保存才可用
  await page.getByRole('button', { name: '执行', exact: true }).click()
  await expect(page.getByText('simulate 返回值')).toBeVisible()
  await expect(page.getByText('仅适用于居民用户')).toBeVisible()
  await expect(saveButton).toBeEnabled()

  // 保存成功出现版本号提示
  await saveButton.click()
  await expect(page.getByText('已保存为版本 v2')).toBeVisible()

  // 版本恢复弹二次确认，取消不覆盖编辑器
  await page.getByRole('button', { name: '历史版本' }).click()
  await page.getByRole('button', { name: '恢复' }).first().click()
  await expect(page.getByText('恢复到该历史版本？')).toBeVisible()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByText('恢复到该历史版本？')).toHaveCount(0)
})

test('无 world_model 菜单权限的用户不可见且直达被拒', async ({ page }) => {
  await seedAuth(page, 'custom', ['overview'])
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/overview')

  const sidebar = page.locator('aside')
  await expect(sidebar.getByText('世界模型', { exact: true })).toHaveCount(0)
  // 本体管理单项对该用户也不可见（未授权），但不影响世界模型断言
  await page.goto('/#/world-model/models')
  await expect(page.getByText('当前页面无法访问')).toBeVisible()
})
