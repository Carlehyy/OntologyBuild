import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * 业务澄清入口（本体管理首卡）端到端契约：
 * 1. 首卡按钮顺序为 立即创建 · 业务澄清 · 本地导入；
 * 2. 点击业务澄清跳转 /explore?session=new，进入待建新会话态 ——
 *    不自动恢复最近会话，用户未输入内容前不发生 POST /exploration/sessions；
 * 3. 首条消息发出后经懒创建只产生一个会话；
 * 4. 普通 /explore 进入仍自动恢复最近会话（回归保护）。
 */

const readiness = {
  ready: false,
  stage: '阶段0 · 定边界',
  gatesPassed: 0,
  gatesTotal: 10,
  blockingCount: 3,
  advisoryCount: 0,
  openQuestions: { blocking: 0, advisory: 0 },
  gates: [],
}

const emptyCanvas = {
  objects: [], actors: [], behaviors: [], events: [], rules: [], processes: [], scenarios: [], questions: [],
}

const emptyCounts = {
  counts: { objects: 0, actors: 0, behaviors: 0, events: 0, rules: 0, processes: 0, scenarios: 0 },
  gaps: [],
}

const existingSessions = [{
  id: 's-existing',
  title: '既有会话',
  canvasVersion: 1,
  status: 'active',
  createdAt: '2026-08-26T00:00:00Z',
  updatedAt: '2026-08-26T00:00:00Z',
}]

async function authenticate(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })
}

const ok = (route: Route, data: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ data, message: 'ok' }),
})

/** 平台壳 + 本体管理列表 + 业务探索接口。返回计数器以断言「未输入不建会话」。 */
async function mockPlatformWithExplore(page: Page) {
  const state = { sessionCreates: 0 }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    // 本体管理列表页
    if (path === '/api/v1/ontologies') return ok(route, { items: [], total: 0, page: 1, page_size: 20 })
    if (path === '/api/v1/domains') return ok(route, [])
    if (path === '/api/v2/inbox/summary') {
      return ok(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    // 业务探索：既有会话存在，用于证明待建态不抢恢复、普通进入会恢复
    if (path === '/api/v2/exploration/sessions' && request.method() === 'GET') return ok(route, existingSessions)
    if (path === '/api/v2/exploration/sessions' && request.method() === 'POST') {
      state.sessionCreates += 1
      return ok(route, {
        id: 's-new-1',
        title: '新会话',
        canvasVersion: 0,
        status: 'active',
        createdAt: '2026-08-27T00:00:00Z',
        updatedAt: '2026-08-27T00:00:00Z',
      })
    }
    if (path === '/api/v2/exploration/sessions/s-existing' && request.method() === 'GET') {
      return ok(route, {
        ...existingSessions[0],
        canvas: emptyCanvas,
        completeness: emptyCounts,
        readiness,
        messages: [{
          id: 'existing-message',
          role: 'user',
          content: 'EXISTING_SESSION_MARKER',
          steps: [],
          createdAt: '2026-08-26T00:00:00Z',
        }],
      })
    }
    if (path === '/api/v2/exploration/sessions/s-new-1/chat' && request.method() === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'data: {"type":"meta","sessionId":"s-new-1","model":"mock-model"}',
          '',
          'data: {"type":"answer","content":"CLARIFIED_BUSINESS_REPLY"}',
          '',
          'data: {"type":"done"}',
          '',
          '',
        ].join('\n'),
      })
    }
    return ok(route, [])
  })
  return state
}

test.describe('业务澄清入口与待建新会话', () => {
  test('业务澄清按钮位于两按钮之间，点击跳转后不输入不创建会话，输入后恰好创建一次', async ({ page }) => {
    await authenticate(page)
    const state = await mockPlatformWithExplore(page)

    await page.goto('/#/ontologies')
    const firstCard = page.locator('article').filter({ hasText: '新建本体' })
    await expect(firstCard.getByRole('button')).toHaveText(['立即创建', '业务澄清', '本地导入'])

    await firstCard.getByRole('button', { name: '业务澄清' }).click()
    await expect(page).toHaveURL(/\/#\/explore\?session=new$/)

    // 待建新会话态：空白欢迎语出现；既有会话未被恢复；未创建任何会话
    await expect(page.getByText('从描述你的业务开始')).toBeVisible()
    await expect(page.getByTestId('exploration-chat-region')).not.toContainText('EXISTING_SESSION_MARKER')
    await expect(page.getByTestId('exploration-chat-region')).not.toContainText('既有会话')
    expect(state.sessionCreates).toBe(0)

    // 等待既有会话解析窗口过后仍未创建，排除“延迟创建”假阴性
    await page.waitForTimeout(400)
    expect(state.sessionCreates).toBe(0)

    // 首条输入触发懒创建：恰好一次，回复可见
    await page.getByTestId('exploration-composer').fill('帮我梳理采购到付款的业务流程')
    await page.getByRole('button', { name: '发送消息' }).click()
    await expect(page.getByText('CLARIFIED_BUSINESS_REPLY')).toBeVisible()
    expect(state.sessionCreates).toBe(1)
  })

  test('重复点击业务澄清入口（深链重进）同样不堆积空会话', async ({ page }) => {
    await authenticate(page)
    const state = await mockPlatformWithExplore(page)

    await page.goto('/#/explore?session=new')
    await expect(page.getByText('从描述你的业务开始')).toBeVisible()
    await page.waitForTimeout(400)
    expect(state.sessionCreates).toBe(0)

    // 模拟再次点击入口：整页重新进入同一深链，仍是零创建
    await page.reload()
    await expect(page.getByText('从描述你的业务开始')).toBeVisible()
    await page.waitForTimeout(400)
    expect(state.sessionCreates).toBe(0)
  })

  test('回归：不带参数进入 /explore 仍自动恢复最近会话', async ({ page }) => {
    await authenticate(page)
    const state = await mockPlatformWithExplore(page)

    await page.goto('/#/explore')
    await expect(page.getByTestId('exploration-chat-region')).toContainText('EXISTING_SESSION_MARKER')
    expect(state.sessionCreates).toBe(0)
  })
})
