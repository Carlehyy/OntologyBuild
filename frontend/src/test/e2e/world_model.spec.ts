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
    if (path === '/api/v2/world-model/templates/time-series') {
      return json(route, {
        key: 'time-series',
        name: '时序推演示例（ARIMA / SARIMA）',
        description: 'ITSM 式统计时序建模',
        script: 'import statsmodels\ndef simulate(context, actions, horizon):\n    return {"trajectory": [1, 2, 3]}\n',
        test_input: {
          context: { series: Array.from({ length: 36 }, (_, i) => 100 + i), period: 12 },
          actions: [],
          horizon: 6,
        },
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

test('推演模型与调用记录为独立页面（无页内 Tab 与共享标题）', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/world-model/models')

  // 共享标题与页内 Tab 已移除
  await expect(page.getByRole('heading', { name: '世界模型' })).toHaveCount(0)
  await expect(page.getByText('演化层能力', { exact: false })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '世界模型子功能' })).toHaveCount(0)
  // 列表页内容正常
  await expect(page.getByText('台区负荷短期推演')).toBeVisible()

  // 列表筛选（本地过滤）
  await page.getByLabel('按模型名称或描述筛选').fill('不存在的模型')
  await expect(page.getByText('台区负荷短期推演')).toHaveCount(0)
  await page.getByLabel('按模型名称或描述筛选').fill('')
  await expect(page.getByText('台区负荷短期推演')).toBeVisible()

  // 经侧边栏子项进入独立的调用记录页
  // 回归：未设置时间筛选时，请求不得携带空的 start=/end=（后端 datetime 校验会 422）
  const callsRequests: string[] = []
  page.on('request', req => {
    if (req.url().includes('/world-model/calls?')) callsRequests.push(req.url())
  })
  const sidebar = page.locator('aside')
  // 当前已在世界模型域内，分组处于激活展开态，子项直接可见（点击分组按钮反而会收起）
  await sidebar.getByRole('link', { name: '调用记录' }).click()
  await expect(page).toHaveURL(/#\/world-model\/calls$/)
  await expect(page.getByText('agent-session-1')).toBeVisible()
  expect(callsRequests.length).toBeGreaterThan(0)
  expect(callsRequests[0]).not.toContain('start=')
  expect(callsRequests[0]).not.toContain('end=')
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

test('保存首个版本后发布按钮立即可用（无需刷新页面）', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  // 场景回归：项目 version_count=0 时发布按钮禁用；
  // 保存成功必须立即解锁，不能要求用户刷新页面。
  await page.route('**/api/v2/world-model/**', route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === `/api/v2/world-model/projects/${projectRow.id}` && request.method() === 'GET') {
      return json(route, { ...projectDetail, version_count: 0 })
    }
    if (path.endsWith('/execute') && request.method() === 'POST') {
      return json(route, { ok: true, payload: { trajectory: [1] }, stdout: '', error: null, traceback: '', duration_ms: 10, kernel_id: 'k' })
    }
    if (path.endsWith('/save') && request.method() === 'POST') {
      return json(route, { ok: true, version_no: 1, execution: null })
    }
    if (path.endsWith('/service') && request.method() === 'GET') {
      return json(route, null)
    }
    return json(route, [])
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/world-model/models/${projectRow.id}/develop`)

  const publishButton = page.getByRole('button', { name: '发布', exact: true })
  await expect(publishButton).toBeDisabled()

  // 修改脚本后执行通过并保存
  await page.locator('.cm-content').first().click()
  await page.keyboard.press('End')
  await page.keyboard.type('# v1')
  await page.getByRole('button', { name: '执行', exact: true }).click()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('已保存为版本 v1')).toBeVisible()

  // 缺陷修复点：保存成功后版本数刷新，发布按钮无需刷新即可点击
  await expect(publishButton).toBeEnabled()
})

test('时序示例：一键插入官方 ARIMA 模板并替换测试入参', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockWorldModel(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/world-model/models/${projectRow.id}/develop`)

  const insertButton = page.getByRole('button', { name: '时序示例', exact: true })
  await expect(insertButton).toBeVisible()

  // 编辑器干净（无未保存修改）时直接插入，不弹确认
  await insertButton.click()
  const editor = page.locator('.cm-content').first()
  await expect(editor).toContainText('statsmodels')
  const inputArea = page.getByLabel('测试入参 JSON')
  await expect(inputArea).toHaveValue(/"period": 12/)

  // 插入后需重新执行通过才能保存（与保存纪律一致）
  const saveButton = page.getByRole('button', { name: '保存', exact: true })
  await expect(saveButton).toBeDisabled()
  await page.getByRole('button', { name: '执行', exact: true }).click()
  await expect(saveButton).toBeEnabled()

  // 编辑器与已保存脚本不同（dirty）时先弹二次确认；取消不改动内容
  await editor.click()
  await page.keyboard.press('End')
  await page.keyboard.type('# dirty')
  await insertButton.click()
  await expect(page.getByText('插入时序推演示例？')).toBeVisible()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByText('插入时序推演示例？')).toHaveCount(0)
  await expect(editor).toContainText('# dirty')

  // 再次插入并确认后，示例模板覆盖当前内容
  await insertButton.click()
  await expect(page.getByText('插入时序推演示例？')).toBeVisible()
  await page.getByRole('button', { name: '插入示例' }).click()
  await expect(page.getByText('插入时序推演示例？')).toHaveCount(0)
  await expect(editor).toContainText('statsmodels')
  await expect(editor).not.toContainText('# dirty')
  // 内容替换后执行凭证失效，需重新执行
  await expect(saveButton).toBeDisabled()
})

test('发布为推演服务：语义注册、服务面板与状态切换', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)

  let published: Record<string, unknown> | null = null
  const serviceOut = (status: string) => ({
    id: 'svc-1',
    project_id: projectRow.id,
    version_id: 'v1',
    version_no: 1,
    name: '台区负荷短期推演服务',
    description: '',
    status,
    endpoint_path: '/api/v2/world-model/services/svc-1/invoke',
    applicable_object_types: { ontology_id: 'ontology-1', object_type_ids: ['ot-line'] },
    preconditions: [],
    created_at: now,
    updated_at: now,
  })

  await page.route('**/api/v1/ontologies**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/ontologies') {
      return json(route, {
        items: [{ id: 'ontology-1', name: '供应链本体', current_release_id: 'release-1' }],
        total: 1, page: 1, page_size: 200,
      })
    }
    if (path === '/api/v1/ontologies/ontology-1/entities') {
      return json(route, [{ id: 'ot-line', name_cn: '线路' }, { id: 'ot-user', name_cn: '用户' }])
    }
    return json(route, [])
  })
  await page.route('**/api/v2/world-model/**', route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === `/api/v2/world-model/projects/${projectRow.id}` && request.method() === 'GET') {
      return json(route, {
        ...projectDetail,
        version_count: 1,
        service_status: published ? (published as { status: string }).status : null,
      })
    }
    if (path === `/api/v2/world-model/projects/${projectRow.id}/versions`) {
      return json(route, [{ id: 'v1', version_no: 1, duration_ms: 40, created_at: now }])
    }
    if (path === `/api/v2/world-model/projects/${projectRow.id}/service` && request.method() === 'GET') {
      return json(route, published)
    }
    if (path === `/api/v2/world-model/projects/${projectRow.id}/publish` && request.method() === 'POST') {
      published = serviceOut('online')
      return json(route, published, 201)
    }
    if (path === `/api/v2/world-model/projects/${projectRow.id}/service/status`) {
      const body = request.postDataJSON() as { status: string }
      published = serviceOut(body.status)
      return json(route, published)
    }
    return json(route, [])
  })

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/world-model/models/${projectRow.id}/develop`)

  // 打开发布对话框，完成语义注册
  await page.getByRole('button', { name: '发布', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('发布为推演服务')).toBeVisible()
  await dialog.getByLabel('服务名称').fill('台区负荷短期推演服务')
  await dialog.getByLabel('选择所属本体').selectOption('ontology-1')
  await dialog.getByRole('checkbox', { name: '线路' }).click()
  await dialog.getByRole('button', { name: '发布并上线' }).click()

  // 服务面板：端点、状态、切换
  const panel = page.getByTestId('world-model-service-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByText('在线')).toBeVisible()
  await expect(panel.getByText('/api/v2/world-model/services/svc-1/invoke')).toBeVisible()
  await panel.getByRole('button', { name: '下线' }).click()
  await expect(panel.getByText('已下线')).toBeVisible()
  // 重新发布入口存在
  await expect(page.getByRole('button', { name: '重新发布' })).toBeVisible()
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
