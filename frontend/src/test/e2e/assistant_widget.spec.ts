import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-08-12T08:00:00+00:00'

const json = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

const conversationOne = {
  id: 'conversation-1',
  title: '会话一',
  model_config_id: 'model-1',
  status: 'active',
  created_at: now,
  updated_at: now,
}
const conversationTwo = {
  id: 'conversation-2',
  title: '会话二',
  model_config_id: 'model-1',
  status: 'active',
  created_at: now,
  updated_at: now,
}

const messageOf = (conversationId: string, id: string, role: 'user' | 'assistant', content: string) => ({
  id,
  conversation_id: conversationId,
  role,
  content,
  status: 'complete',
  steps: [],
  token_usage: {},
  created_at: now,
})

const sseBody = [
  'event: meta\ndata: {"conversationId":"conversation-1","assistantMessageId":"assistant-live"}',
  'event: thinking\ndata: {"round":1}',
  'event: tool_start\ndata: {"toolRunId":"run-1","toolName":"use_skill","arguments":{"skill":"demo"}}',
  'event: tool_result\ndata: {"toolRunId":"run-1","status":"success","preview":"读取成功"}',
  'event: text_delta\ndata: {"delta":"流式最终答复"}',
  'event: message_end\ndata: {"message":{"id":"assistant-live","content":"流式最终答复","steps":[{"toolName":"use_skill","status":"success","arguments":{"skill":"demo"},"preview":"读取成功"}],"tokenUsage":{"inputTokens":10}}}',
  'event: done\ndata: {}',
  '',
].join('\n\n')

async function seedAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'u-widget', username: 'widget-tester', role: 'admin' },
      },
      version: 0,
    }))
  })
}

/** 平台外壳兜底：未显式声明的接口一律返回空集合，避免触达真实后端 */
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

interface WidgetMockOptions { denyMenu?: boolean }

async function mockSuperAssistant(page: Page, options: WidgetMockOptions = {}) {
  let chatCompleted = false
  await page.route('**/api/v2/super-assistant/**', route => {
    const request = route.request()
    const path = new URL(request.url()).pathname

    if (path === '/api/v2/super-assistant/conversations' && request.method() === 'GET') {
      if (options.denyMenu) {
        return route.fulfill({
          status: 403,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: { code: 'MENU_ACCESS_DENIED', message: '当前角色无权访问此功能', menu_key: 'super_assistant' },
          }),
        })
      }
      return json(route, [conversationOne, conversationTwo])
    }
    if (path === '/api/v2/super-assistant/conversations' && request.method() === 'POST') {
      return json(route, { ...conversationOne, id: 'conversation-new', title: '新会话' }, 201)
    }
    if (path === '/api/v2/super-assistant/conversations/conversation-1/messages') {
      return json(route, chatCompleted
        ? [
            messageOf('conversation-1', 'user-live', 'user', '介绍一下平台'),
            {
              ...messageOf('conversation-1', 'assistant-live', 'assistant', '流式最终答复'),
              steps: [{ toolName: 'use_skill', status: 'success', arguments: { skill: 'demo' }, preview: '读取成功' }],
              token_usage: { inputTokens: 10 },
            },
          ]
        : [
            messageOf('conversation-1', 'user-1', 'user', '你好'),
            messageOf('conversation-1', 'assistant-1', 'assistant', '这是**会话一**的历史答复'),
          ])
    }
    if (path === '/api/v2/super-assistant/conversations/conversation-2/messages') {
      return json(route, [
        messageOf('conversation-2', 'user-2', 'user', '第二个问题'),
        messageOf('conversation-2', 'assistant-2', 'assistant', '第二个会话的内容'),
      ])
    }
    if (path === '/api/v2/super-assistant/conversations/conversation-1/chat' && request.method() === 'POST') {
      chatCompleted = true
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: sseBody,
      })
    }
    if (path.endsWith('/cancel')) return json(route, {}, 202)
    return json(route, [])
  })
}

test('悬浮入口在所有业务页面常驻且可开合', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockSuperAssistant(page)
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/overview')
  const fab = page.getByTestId('assistant-widget-fab')
  await expect(fab).toBeVisible()

  await page.goto('/#/models')
  await expect(fab).toBeVisible()

  await fab.click()
  const panel = page.getByTestId('assistant-widget-panel')
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: '关闭 AI 助手' }).click()
  await expect(page.getByTestId('assistant-widget-panel')).toHaveCount(0)
  await expect(fab).toBeVisible()
})

test('面板加载历史消息并以 Markdown 渲染', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockSuperAssistant(page)
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/overview')
  await page.getByTestId('assistant-widget-fab').click()

  const panel = page.getByTestId('assistant-widget-panel')
  await expect(panel.getByText('你好', { exact: true })).toBeVisible()
  await expect(panel.locator('strong').filter({ hasText: '会话一' })).toBeVisible()
})

test('发送消息后渲染思考链与流式答复', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockSuperAssistant(page)
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/overview')
  await page.getByTestId('assistant-widget-fab').click()
  const panel = page.getByTestId('assistant-widget-panel')
  await expect(panel.getByText('这是会话一的历史答复')).toBeVisible()

  await panel.getByPlaceholder('输入消息，Enter 发送 / Shift+Enter 换行').fill('介绍一下平台')
  await panel.getByPlaceholder('输入消息，Enter 发送 / Shift+Enter 换行').press('Enter')

  // 思考链：工具调用步骤可见；流式结束后展示最终答复（含服务端回刷的持久化结果）
  await expect(panel.getByText('use_skill')).toBeVisible()
  await expect(panel.getByText('流式最终答复').first()).toBeVisible()
  await expect(panel.getByText('介绍一下平台').first()).toBeVisible()
})

test('历史会话浮层可切换会话', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockSuperAssistant(page)
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/overview')
  await page.getByTestId('assistant-widget-fab').click()
  const panel = page.getByTestId('assistant-widget-panel')
  await expect(panel.getByText('这是会话一的历史答复')).toBeVisible()

  await panel.getByRole('button', { name: '历史会话' }).click()
  await page.getByTestId('assistant-widget-history').getByText('会话二').click()

  await expect(panel.getByText('第二个会话的内容')).toBeVisible()
})

test('跳转完整页携带当前会话并直达同一会话', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockSuperAssistant(page)
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/overview')
  await page.getByTestId('assistant-widget-fab').click()
  const panel = page.getByTestId('assistant-widget-panel')
  await expect(panel.getByText('这是会话一的历史答复')).toBeVisible()

  await panel.getByRole('button', { name: '历史会话' }).click()
  await page.getByTestId('assistant-widget-history').getByText('会话二').click()
  await expect(panel.getByText('第二个会话的内容')).toBeVisible()

  await panel.getByTestId('assistant-widget-open-full').click()
  await expect(page).toHaveURL(/#\/super-assistant\?conversation=conversation-2/)
  // 完整页头部标题按钮显示目标会话，且历史消息一致
  await expect(page.getByRole('button', { name: /会话二/ })).toBeVisible()
  await expect(page.getByText('第二个会话的内容')).toBeVisible()
  // 悬浮入口在超级助手页面依旧可用
  await expect(page.getByTestId('assistant-widget-fab')).toBeVisible()
})

test('无菜单权限时面板展示不可用提示且输入被禁用', async ({ page }) => {
  await seedAuth(page)
  await mockPlatformShell(page)
  await mockSuperAssistant(page, { denyMenu: true })
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/overview')
  await page.getByTestId('assistant-widget-fab').click()

  const panel = page.getByTestId('assistant-widget-panel')
  await expect(panel.getByText('当前账号暂无 AI 助手使用权限，请联系管理员开通。')).toBeVisible()
  await expect(panel.getByPlaceholder('输入消息，Enter 发送 / Shift+Enter 换行')).toBeDisabled()
})
