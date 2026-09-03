import { expect, test, type Page, type Route } from '@playwright/test'

// AI 原生工作台（前台）：登录默认落地、五项入口、历史会话分组时间线、归档流转、
// 本体治理跳后台并返回。全部接口本地 mock，不触真实后端。
// 本 spec 另覆盖：分组限量展开、naive UTC 时区显示、行悬停不抖动、
// 会话附件上传/移除与跨会话隔离、流式生成跨会话隔离、ReUI 模型选择器、删除确认弹窗。

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

const sseBody = (text: string) => [
  'event: meta\ndata: {"conversationId":"c-today","model":"deepseek-v4-pro"}',
  `event: text_delta\ndata: ${JSON.stringify({ delta: text })}`,
  `event: message_end\ndata: ${JSON.stringify({ message: { content: text, steps: [], tokenUsage: {} } })}`,
  'event: done\ndata: {}',
  '',
].join('\n\n')

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

interface MockOptions {
  /** chat SSE 响应的延迟毫秒数：用于模拟「生成中」窗口期 */
  chatDelayMs?: number
}

async function mockApis(page: Page, options: MockOptions = {}) {
  const patchBodies: string[] = []
  const deleteCalls: string[] = []
  const fileUploads: string[] = []
  const fileDeletes: string[] = []
  const chatCalls: string[] = []
  let chatDone = false
  const filesByConv: Record<string, Array<Record<string, unknown>>> = {
    'c-today': [{
      id: 'f-1', filename: '需求清单.md', mimeType: 'text/markdown', size: 1204,
      extractedChars: 100, extractError: null, createdAt: at(0, 9),
    }],
  }
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
      }, {
        id: 'model-2', name: 'Qwen', config_type: 'llm', provider: 'qwen',
        api_base: 'https://dashscope.aliyuncs.com', has_api_key: true, enabled: true, is_default: false,
        last_test_status: 'success', last_tested_at: at(0, 8), last_test_message: 'ok',
        models: ['qwen-max'], options: {}, created_by: 'admin',
        created_at: at(0, 8), updated_at: at(0, 8),
      }])
    }
    // 会话附件：list / upload / delete（仅按会话隔离）
    const filesMatch = path.match(/^\/api\/v2\/super-assistant\/conversations\/([^/]+)\/files(?:\/([^/]+))?$/)
    if (filesMatch) {
      const [, conversationId, artifactId] = filesMatch
      if (request.method() === 'GET' && !artifactId) return json(route, filesByConv[conversationId] || [])
      if (request.method() === 'POST' && !artifactId) {
        const filename = /filename="([^"]+)"/.exec(request.postData() || '')?.[1] || 'upload.bin'
        fileUploads.push(filename)
        const row = {
          id: `f-${fileUploads.length + 1}`, filename, mimeType: 'text/markdown', size: 64,
          extractedChars: 10, extractError: null, createdAt: at(0, 10),
        }
        filesByConv[conversationId] = [...(filesByConv[conversationId] || []), row]
        return json(route, row, 201)
      }
      if (request.method() === 'DELETE' && artifactId) {
        fileDeletes.push(artifactId)
        filesByConv[conversationId] = (filesByConv[conversationId] || []).filter(row => row.id !== artifactId)
        return route.fulfill({ status: 204 })
      }
    }
    // 会话流式对话：可控延迟的 SSE
    const chatMatch = path.match(/^\/api\/v2\/super-assistant\/conversations\/([^/]+)\/chat$/)
    if (chatMatch && request.method() === 'POST') {
      chatCalls.push(chatMatch[1])
      const reply = '你好，我是超级助手'
      const fulfill = () => {
        chatDone = true
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: sseBody(reply),
        })
      }
      if (options.chatDelayMs) {
        return new Promise(resolve => setTimeout(() => resolve(fulfill()), options.chatDelayMs))
      }
      return fulfill()
    }
    if (path.startsWith('/api/v2/super-assistant/conversations/') && request.method() === 'PATCH') {
      const id = path.split('/')[5]
      const body = JSON.parse(request.postData() || '{}')
      patchBodies.push(request.postData() || '')
      const source = conversationsFixture.find(item => item.id === id)
      return json(route, { ...source, ...body })
    }
    const conversationMatch = path.match(/^\/api\/v2\/super-assistant\/conversations\/([^/]+)$/)
    if (conversationMatch && request.method() === 'DELETE') {
      deleteCalls.push(conversationMatch[1])
      return route.fulfill({ status: 204 })
    }
    if (path === '/api/v2/super-assistant/conversations') return json(route, conversationsFixture)
    if (/^\/api\/v2\/super-assistant\/conversations\/[^/]+\/messages$/.test(path)) {
      const id = path.split('/')[5]
      if (id === 'c-today' && chatDone) {
        return json(route, [
          { id: 'm-1', conversation_id: 'c-today', role: 'user', content: '你好', status: 'complete', steps: [], token_usage: {}, created_at: at(0, 9) },
          { id: 'm-2', conversation_id: 'c-today', role: 'assistant', content: '你好，我是超级助手', status: 'complete', steps: [], token_usage: {}, created_at: at(0, 9) },
        ])
      }
      return json(route, [])
    }
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
  return {
    patchBodies,
    deleteCalls,
    fileUploads,
    fileDeletes,
    chatCalls,
    isChatDone: () => chatDone,
  }
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

test('历史分组默认限量 10 条，展开全部后显示完整列表且可收起', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  const many = Array.from({ length: 13 }, (_, index) => ({
    id: `c-cap-${index}`, title: `批量会话 ${index + 1}`, model_config_id: 'model-1',
    status: 'active', created_at: at(0, 8), updated_at: at(0, 8),
  }))
  // 后注册的精确路由优先于 mockApis 的通用 /** 路由
  await page.route('**/api/v2/super-assistant/conversations', route => {
    if (route.request().method() === 'GET') return json(route, many)
    return route.fallback()
  })
  await page.goto('/#/super-assistant')

  const today = page.locator('[data-workbench-group="today"]')
  await expect(today.locator('[data-workbench-conversation]')).toHaveCount(10)
  const toggle = page.locator('[data-workbench-group-toggle="today"]')
  await expect(toggle).toHaveText('展开全部（还有 3 条）')

  await toggle.click()
  await expect(today.locator('[data-workbench-conversation]')).toHaveCount(13)
  await expect(toggle).toHaveText('收起')

  await toggle.click()
  await expect(today.locator('[data-workbench-conversation]')).toHaveCount(10)
})

test('会话时间按本地时区显示：naive UTC 串按 UTC 解析', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  // 后端实际返回形态：UTC 瞬间、无 Z 后缀；误按本地解析会慢 8 小时（上海）
  const naive = '2026-08-20T08:30:00'
  await page.route('**/api/v2/super-assistant/conversations', route => {
    if (route.request().method() === 'GET') {
      return json(route, [{
        id: 'c-naive', title: '时差验证', model_config_id: 'model-1',
        status: 'active', created_at: naive, updated_at: naive,
      }])
    }
    return route.fallback()
  })
  await page.goto('/#/super-assistant')

  const expected = new Date(`${naive}Z`).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
  await expect(page.locator('[data-workbench-conversation="c-naive"]')).toContainText(expected)
})

test('会话行悬停时行高与相邻组位置不变（无抖动）', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  const row = page.locator('[data-workbench-conversation="c-today"]')
  const nextGroup = page.locator('[data-workbench-group="yesterday"]')
  const beforeBox = await row.boundingBox()
  const beforeY = (await nextGroup.boundingBox())?.y

  await row.hover()
  // 悬停后动作按钮可见（归档/删除浮出）
  await expect(row.getByRole('button', { name: '删除会话 今日需求梳理' })).toBeVisible()
  const hoverBox = await row.boundingBox()

  expect(hoverBox?.height).toBe(beforeBox?.height)
  expect((await nextGroup.boundingBox())?.y).toBe(beforeY)
})

test('会话附件：上传/展示/移除，且跨会话不可见', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  // c-today 预置 1 个附件 chip
  await expect(page.getByTestId('super-assistant-attachments')).toBeVisible()
  await expect(page.getByText('需求清单.md')).toBeVisible()

  // 切到昨日会话：附件区不可见（跨会话隔离）
  await page.locator('[data-workbench-conversation="c-yesterday"] button').first().click()
  await expect(page.getByTestId('super-assistant-attachments')).toHaveCount(0)

  // 切回后重新出现
  await page.locator('[data-workbench-conversation="c-today"] button').first().click()
  await expect(page.getByTestId('super-assistant-attachments')).toBeVisible()

  // 上传新附件（隐藏 file input 驱动）
  await page.getByLabel('选择会话附件文件').setInputFiles({
    name: '会议纪要.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# 纪要'),
  })
  await expect.poll(() => mocks.fileUploads).toEqual(['会议纪要.md'])
  await expect(page.getByText('会议纪要.md')).toBeVisible()

  // 移除附件
  await page.getByRole('button', { name: '移除附件 会议纪要.md' }).click()
  await expect.poll(() => mocks.fileDeletes.length).toBe(1)
  await expect(page.getByText('会议纪要.md')).toHaveCount(0)
})

test('流式生成跨会话隔离：A 生成中切到 B，B 输入区不受影响且不串内容', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page, { chatDelayMs: 1500 })
  await page.goto('/#/super-assistant?conversation=c-today')

  await page.getByRole('textbox', { name: '向超级助手发送消息' }).fill('你好')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect(page.getByRole('button', { name: '停止生成' })).toBeVisible()

  // 生成中切到昨日会话：输入区不表现为发送中，可以正常输入与发送
  await page.locator('[data-workbench-conversation="c-yesterday"] button').first().click()
  await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0)
  await page.getByRole('textbox', { name: '向超级助手发送消息' }).fill('B 会话消息')
  await expect(page.getByRole('button', { name: '发送消息' })).toBeEnabled()
  // A 会话的流式内容不得串到 B
  await expect(page.getByText('你好，我是超级助手')).toHaveCount(0)

  // A 完成后切回：消息从服务端刷新，内容完整可见
  await expect.poll(mocks.isChatDone).toBe(true)
  await page.locator('[data-workbench-conversation="c-today"] button').first().click()
  await expect(page.getByText('你好，我是超级助手')).toBeVisible()
})

test('会话模型选择器为 ReUI Select，选择后 PATCH 持久化', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  // c-today 当前为 model-1（DeepSeek）；Radix Select 选中相同值不触发 onValueChange，切到 Qwen
  await page.getByRole('combobox', { name: '会话模型' }).click()
  await page.getByRole('option', { name: /Qwen/ }).click()
  await expect.poll(() => mocks.patchBodies.length).toBe(1)
  expect(JSON.parse(mocks.patchBodies[0])).toMatchObject({ model_config_id: 'model-2' })
})

test('删除会话走 ReUI 确认弹窗（非 window.confirm）', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant')

  const row = page.locator('[data-workbench-conversation="c-earlier"]')
  await row.hover()
  await row.getByRole('button', { name: '删除会话 上周数据摸底' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText(/确定删除会话「上周数据摸底」/)).toBeVisible()

  // 取消：弹窗关闭、会话仍在
  await dialog.getByRole('button', { name: '取消' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('[data-workbench-conversation="c-earlier"]')).toHaveCount(1)

  // 确认：DELETE 发出、会话移除
  await row.hover()
  await row.getByRole('button', { name: '删除会话 上周数据摸底' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '删除', exact: true }).click()
  await expect.poll(() => mocks.deleteCalls).toEqual(['c-earlier'])
  await expect(page.locator('[data-workbench-conversation="c-earlier"]')).toHaveCount(0)
})
