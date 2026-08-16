import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-08-12T08:00:00+00:00'

async function mockNavTabs(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'admin', username: 'admin', email: 'admin@example.com', role: 'admin' },
      },
      version: 0,
    }))
  })

  const json = (route: Route, data: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data }),
  })

  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/ontologies') return json(route, {
      items: [{
        id: 'ontology-1',
        name: '供应链本体',
        domain: '供应链',
        description: '多标签页导航验证',
        status: 'published',
        version: 'v1',
        current_release_id: 'release-1',
        current_release_version: 'v1',
        created_at: now,
        updated_at: now,
      }],
      total: 1,
      page: 1,
      page_size: 20,
    })
    if (path === '/api/v1/domains') return json(route, [])
    if (path === '/api/v1/models') return json(route, [])
    return route.fallback()
  })

  await page.route('**/api/v2/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v2/inbox/summary') return json(route, {
      openAlertCount: 0,
      actionableCount: 0,
      unreadCount: 0,
      resolvedCount: 0,
    })
    if (path === '/api/v2/ontologies/ontology-1/versions/release-1/workspace'
      || path === '/api/v2/formal/ontologies/ontology-1/full') return json(route, {
      id: 'ontology-1',
      name: '供应链本体',
      version: 'v1',
      workspaceMode: 'release',
      objectTypes: [],
      linkTypes: [],
      actions: [],
      functions: [],
      instances: [],
      linkInstances: [],
      executionLogs: [],
    })
    if (path === '/api/v2/formal/ontologies/ontology-1/agent/capabilities') return json(route, {
      enabled: true,
      objectTypes: [],
      linkTypes: [],
      actions: [],
      allowActionProposals: true,
      maxRowsPerQuery: 50,
      maxSteps: 8,
      skillCard: '',
      releaseId: 'release-1',
      releaseVersion: 'v1',
    })
    if (path === '/api/v2/formal/ontologies/ontology-1/agent/profile') return json(route, {
      id: 'profile-1',
      ontologyId: 'ontology-1',
      enabled: true,
      allowedObjectTypeIds: null,
      allowedLinkTypeIds: null,
      allowedActionIds: [],
      allowActionProposals: true,
      maxRowsPerQuery: 50,
      maxSteps: 8,
      systemPromptExtra: '',
      defaultModelId: null,
      updatedAt: now,
    })
    if (path === '/api/v2/formal/ontologies/ontology-1/agent/conversations') return json(route, [])
    if (path === '/api/v2/formal/ontologies/ontology-1/agent/decision-simulations') return json(route, [])
    if (path.startsWith('/api/v2/formal/ontologies/ontology-1/agent/dynamic-sentinels')) return json(route, [])
    return route.fallback()
  })
}

test('顶栏多标签页：打开、切换、域内路径恢复、关闭与刷新恢复', async ({ page }) => {
  await mockNavTabs(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/agent')

  const tabList = page.getByRole('tablist', { name: '页面标签' })
  const agentTab = tabList.getByRole('tab', { name: '本体助手' })
  const ontologiesTab = tabList.getByRole('tab', { name: '本体管理' })

  // 访问本体助手，出现第一个标签且为激活态
  await expect(agentTab).toBeVisible()
  await expect(agentTab).toHaveAttribute('aria-selected', 'true')
  await expect(ontologiesTab).toHaveCount(0)

  // 侧边栏打开本体管理，出现第二个标签
  await page.getByRole('navigation').getByRole('link', { name: '本体管理' }).click()
  await expect(page).toHaveURL(/\/#\/ontologies$/)
  await expect(agentTab).toHaveAttribute('aria-selected', 'false')
  await expect(ontologiesTab).toHaveAttribute('aria-selected', 'true')

  // 点击标签切回本体助手
  await agentTab.click()
  await expect(page).toHaveURL(/\/#\/agent$/)
  await expect(agentTab).toHaveAttribute('aria-selected', 'true')

  // 刷新后标签列表与激活态从 localStorage 恢复
  await page.reload()
  await expect(agentTab).toHaveAttribute('aria-selected', 'true')
  await expect(ontologiesTab).toBeVisible()

  // 菜单域内跳转复用同一标签并记住路径（含 query）
  await page.getByLabel('选择本体').selectOption('ontology-1')
  await expect(page).toHaveURL(/\/#\/agent\?ontology_id=ontology-1$/)
  await expect(tabList.getByRole('tab', { name: '本体助手' })).toHaveCount(1)
  await ontologiesTab.click()
  await expect(page).toHaveURL(/\/#\/ontologies$/)
  await agentTab.click()
  await expect(page).toHaveURL(/\/#\/agent\?ontology_id=ontology-1$/)

  // 关闭激活标签，回退到最近使用的标签
  await agentTab.getByRole('button', { name: '关闭 本体助手' }).click()
  await expect(page).toHaveURL(/\/#\/ontologies$/)
  await expect(tabList.getByRole('tab', { name: '本体助手' })).toHaveCount(0)
  await expect(ontologiesTab).toHaveAttribute('aria-selected', 'true')

  // 关闭最后一个标签，回到默认落地页并重新记录标签
  await ontologiesTab.getByRole('button', { name: '关闭 本体管理' }).click()
  await expect(page).toHaveURL(/\/#\/agent$/)
  await expect(tabList.getByRole('tab', { name: '本体助手' })).toHaveAttribute('aria-selected', 'true')

  // 边界：在落地页上关闭唯一的标签，标签应随同路径导航重新记录
  await tabList.getByRole('tab', { name: '本体助手' })
    .getByRole('button', { name: '关闭 本体助手' }).click()
  await expect(page).toHaveURL(/\/#\/agent$/)
  await expect(tabList.getByRole('tab', { name: '本体助手' })).toHaveAttribute('aria-selected', 'true')
})

test('标签可见标题只保留页面一层描述，悬停提示保留两级', async ({ page }) => {
  await mockNavTabs(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/agent')

  const tabList = page.getByRole('tablist', { name: '页面标签' })

  // 列表页：可见标题为菜单名
  await page.getByRole('navigation').getByRole('link', { name: '本体管理' }).click()
  await expect(page).toHaveURL(/\/#\/ontologies$/)
  const listTab = tabList.getByRole('tab', { name: '本体管理' })
  await expect(listTab).toBeVisible()
  await expect(listTab).toHaveAttribute('title', '本体管理')

  // 详情页：只显示“详情”，悬停提示为“本体管理 · 详情”
  await page.goto('/#/ontologies/ontology-1')
  const detailTab = tabList.getByRole('tab', { name: '详情' })
  await expect(detailTab).toBeVisible()
  await expect(detailTab).toHaveAttribute('aria-selected', 'true')
  await expect(detailTab).toHaveAttribute('title', '本体管理 · 详情')

  // 图谱页：只显示“图谱”
  await page.goto('/#/ontologies/ontology-1/graph')
  await expect(tabList.getByRole('tab', { name: '图谱' })).toBeVisible()

  // 系统设置子页：显示页面自身名称，悬停提示两级
  await page.goto('/#/settings/users')
  const settingsTab = tabList.getByRole('tab', { name: '用户管理' })
  await expect(settingsTab).toBeVisible()
  await expect(settingsTab).toHaveAttribute('title', '系统设置 · 用户管理')
})
