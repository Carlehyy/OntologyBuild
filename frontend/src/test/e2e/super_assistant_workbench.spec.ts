import { expect, test, type Page, type Route } from '@playwright/test'

// AI 原生工作台（前台）：登录默认落地、五项入口、历史会话分组时间线、归档流转、
// 本体治理跳后台并返回。全部接口本地 mock，不触真实后端。

const json = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

/** 相对当前时刻的本地日期 ISO 串：daysAgo=0 今天、1 昨天……保证分组断言与环境无关 */
const at = (daysAgo: number, hour: number) => {
  const date = new Date()
  date.setDate(date.getDate() - daysAgo)
  date.setHours(hour, 0, 0, 0)
  return date.toISOString()
}

const conversationsFixture = [
  { id: 'c-today', title: '今日需求梳理', model_config_id: 'model-1', status: 'active', created_at: at(0, 9), updated_at: at(0, 9) },
  { id: 'c-yesterday', title: '昨日方案讨论', model_config_id: 'model-1', status: 'active', created_at: at(1, 20), updated_at: at(1, 20) },
  { id: 'c-earlier', title: '上周数据摸底', model_config_id: 'model-1', status: 'active', created_at: at(5, 10), updated_at: at(5, 10) },
  { id: 'c-archived', title: '旧会话存档', model_config_id: 'model-1', status: 'archived', created_at: at(2, 8), updated_at: at(2, 8) },
]

async function seedAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u-1', username: 'workbench-tester', role: 'admin' },
      },
      version: 0,
    }))
  })
}

async function mockApis(page: Page) {
  const patchBodies: string[] = []
  await page.route('**/api/**', route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) return route.continue()

    if (path === '/api/v1/models') {
      return json(route, [{
        id: 'model-1', name: 'DeepSeek', config_type: 'llm', provider: 'deepseek',
        api_base: 'https://api.deepseek.com', has_api_key: true, enabled: true, is_default: true,
        last_test_status: 'success', last_tested_at: at(0, 8), last_test_message: 'ok',
        models: ['deepseek-v4-pro'], options: {}, created_by: 'admin',
        created_at: at(0, 8), updated_at: at(0, 8),
      }])
    }
    if (path.startsWith('/api/v2/super-assistant/conversations/') && request.method() === 'PATCH') {
      const id = path.split('/')[5]
      const body = JSON.parse(request.postData() || '{}')
      patchBodies.push(request.postData() || '')
      const source = conversationsFixture.find(item => item.id === id)
      return json(route, { ...source, ...body })
    }
    if (path === '/api/v2/super-assistant/conversations') return json(route, conversationsFixture)
    if (/^\/api\/v2\/super-assistant\/conversations\/[^/]+\/messages$/.test(path)) return json(route, [])
    if (path === '/api/v2/super-assistant/skills') return json(route, [])
    if (path === '/api/v2/super-assistant/mcp-servers') return json(route, [])
    if (path === '/api/v2/inbox/summary') {
      return json(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    if (path === '/api/v1/overview/stats') {
      return json(route, {
        ontology_count: 1, entity_count: 2, relation_count: 3, logic_count: 4, action_count: 5,
        rule_hits: 6, recent_ontologies: [], domain_counts: {}, status_counts: {},
      })
    }
    return json(route, [])
  })
  return { patchBodies }
}

test('工作台骨架：五项入口齐备，历史会话按今日/昨日/历史分组，归档折叠', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  await expect(page.getByRole('button', { name: '新建任务' })).toBeVisible()
  await expect(page.getByRole('button', { name: '全局搜索' })).toBeVisible()
  await expect(page.getByRole('button', { name: '定时任务' })).toBeVisible()
  await expect(page.getByRole('link', { name: '本体治理' })).toBeVisible()
  await expect(page.getByRole('button', { name: /退出登录/ })).toBeVisible()

  await expect(page.locator('[data-workbench-group="today"] [data-workbench-conversation="c-today"]')).toHaveCount(1)
  await expect(page.locator('[data-workbench-group="yesterday"] [data-workbench-conversation="c-yesterday"]')).toHaveCount(1)
  await expect(page.locator('[data-workbench-group="earlier"] [data-workbench-conversation="c-earlier"]')).toHaveCount(1)
  // 归档区默认折叠：标题含计数，条目不可见
  await expect(page.getByRole('button', { name: /归档会话（1）/ })).toBeVisible()
  await expect(page.locator('[data-workbench-group="archived"] [data-workbench-conversation="c-archived"]')).toHaveCount(0)

  // 聊天区就绪（输入框占位符来自模型加载成功分支）
  await expect(page.getByTestId('super-assistant-composer')).toBeVisible()
})

test('归档流转：会话移入归档区且 PATCH 携带 status', async ({ page }) => {
  await seedAuth(page)
  const { patchBodies } = await mockApis(page)
  await page.goto('/#/super-assistant')

  const row = page.locator('[data-workbench-conversation="c-today"]')
  await row.hover()
  await row.getByRole('button', { name: '归档会话 今日需求梳理' }).click()

  await expect.poll(() => patchBodies.length).toBe(1)
  expect(JSON.parse(patchBodies[0])).toMatchObject({ status: 'archived' })
  await expect(page.locator('[data-workbench-group="today"] [data-workbench-conversation="c-today"]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /归档会话（2）/ })).toBeVisible()

  // 展开归档区后可恢复
  await page.getByRole('button', { name: /归档会话（2）/ }).click()
  const archivedRow = page.locator('[data-workbench-group="archived"] [data-workbench-conversation="c-today"]')
  await expect(archivedRow).toHaveCount(1)
  await archivedRow.hover()
  await archivedRow.getByRole('button', { name: '恢复会话 今日需求梳理' }).click()
  await expect.poll(() => patchBodies.length).toBe(2)
  expect(JSON.parse(patchBodies[1])).toMatchObject({ status: 'active' })
  await expect(page.locator('[data-workbench-group="today"] [data-workbench-conversation="c-today"]')).toHaveCount(1)
})

test('本体治理跳转后台，后台经右下角悬浮助手返回工作台', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('link', { name: '本体治理' }).click()
  await page.waitForURL('**/#/overview')
  // 后台导航不提供 AI 工作台入口，回前台走右下角悬浮助手
  await expect(page.getByRole('link', { name: 'AI 工作台' })).toHaveCount(0)

  await page.getByTestId('assistant-widget-fab').click()
  await page.getByTestId('assistant-widget-open-full').click()
  await page.waitForURL(/#\/super-assistant/)
  await expect(page.getByRole('button', { name: '新建任务' })).toBeVisible()
})

test('全局搜索与定时任务为如实占位的即将上线弹窗', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('button', { name: '全局搜索' }).click()
  await expect(page.getByText(/即将上线/).first()).toBeVisible()
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: '定时任务' }).click()
  await expect(page.getByText(/即将上线/).first()).toBeVisible()
})
