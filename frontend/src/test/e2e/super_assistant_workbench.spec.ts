import { expect, test, type Page, type Route } from '@playwright/test'

// AI 原生工作台（前台）：登录默认落地、七项入口、历史会话分组时间线、归档流转、
// 本体治理跳后台并返回。全部接口本地 mock，不触真实后端。
// 本 spec 另覆盖：分组限量展开、naive UTC 时区显示、行悬停不抖动、
// 会话附件上传/移除/位于输入框上方与跨会话隔离、流式生成跨会话隔离、ReUI 模型选择器、
// 删除确认弹窗、新建任务空会话去重、空态品牌文案与占位符、配置面板白底、
// 重命名 blur 取消、记忆宫殿页签弹窗（文件库上传/删除/预览/在线编辑/ZIP 导入 +
// 知识图谱过滤/节点详情/邻域检索高亮）、外部集成占位、⌘K/Ctrl+K 唤起全局搜索、输入草稿按会话缓存、
// 全局搜索 Command 面板检索与跳转、历史分组 shadcn Sidebar 原语、空态品牌字号。

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

const palaceGraphFixture = {
  available: true,
  nodes: [
    { id: 'e-1', name: '张三', type: '人物', aliases: [], source_files: ['个人知识库.md'], mention_count: 5, match_count: 2 },
    { id: 'e-2', name: 'ACME', type: '组织', aliases: [], source_files: ['个人知识库.md'], mention_count: 3, match_count: 1 },
    { id: 'e-3', name: '知识图谱', type: '技术', aliases: [], source_files: ['行业研究.pdf'], mention_count: 2, match_count: 0 },
  ],
  edges: [
    { source: 'e-1', target: 'e-2', name: '任职', source_files: ['个人知识库.md'] },
    { source: 'e-2', target: 'e-3', name: '使用', source_files: ['行业研究.pdf'] },
  ],
  totals: { entities: 3, relations: 2 },
  truncated: false,
}

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
  const createCalls: string[] = []
  const fileUploads: string[] = []
  const fileDeletes: string[] = []
  const chatCalls: string[] = []
  const searchQueries: string[] = []
  const createdConvs: Array<Record<string, unknown>> = []
  let chatDone = false
  const filesByConv: Record<string, Array<Record<string, unknown>>> = {
    'c-today': [{
      id: 'f-1', filename: '需求清单.md', mimeType: 'text/markdown', size: 1204,
      extractedChars: 100, extractError: null, createdAt: at(0, 9),
    }],
  }
  const palaceFiles: Array<Record<string, unknown>> = [
    {
      id: 'pf-1', filename: '个人知识库.md', mimeType: 'text/markdown', size: 2048,
      sha256: 'pf-1-hash', extractedChars: 1800, status: 'built', error: null,
      entityCount: 3, relationCount: 2, editable: true, createdAt: at(0, 8), updatedAt: at(0, 8),
    },
    {
      id: 'pf-2', filename: '行业研究.pdf', mimeType: 'application/pdf', size: 40960,
      sha256: 'pf-2-hash', extractedChars: 12000, status: 'building', error: null,
      entityCount: 0, relationCount: 0, editable: false, createdAt: at(0, 9), updatedAt: at(0, 9),
    },
  ]
  const palaceUploads: string[] = []
  const palaceDeletes: string[] = []
  const palacePreviews: string[] = []
  const palaceContentPuts: string[] = []
  const palaceReplaces: string[] = []
  const palaceBatchImports: string[] = []
  const palaceGraphSearches: string[] = []
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
    // 记忆宫殿：文件库 / 图谱 / 上传 / 删除 / 重建
    if (path === '/api/v2/super-assistant/palace/files') {
      if (request.method() === 'GET') return json(route, palaceFiles)
      if (request.method() === 'POST') {
        const filename = /filename="([^"]+)"/.exec(request.postData() || '')?.[1] || 'palace.bin'
        palaceUploads.push(filename)
        palaceFiles.unshift({
          id: `pf-new-${palaceUploads.length}`, filename, mimeType: 'text/markdown', size: 64,
          sha256: 'new-hash', extractedChars: 10, status: 'pending', error: null,
          entityCount: 0, relationCount: 0, editable: true, createdAt: at(0, 10), updatedAt: at(0, 10),
        })
        return json(route, palaceFiles[0], 201)
      }
    }
    const palaceDeleteMatch = path.match(/^\/api\/v2\/super-assistant\/palace\/files\/([^/]+)$/)
    if (palaceDeleteMatch && request.method() === 'DELETE') {
      palaceDeletes.push(palaceDeleteMatch[1])
      const index = palaceFiles.findIndex(row => row.id === palaceDeleteMatch[1])
      if (index >= 0) palaceFiles.splice(index, 1)
      return route.fulfill({ status: 204 })
    }
    const palaceRebuildMatch = path.match(/^\/api\/v2\/super-assistant\/palace\/files\/([^/]+)\/rebuild$/)
    if (palaceRebuildMatch && request.method() === 'POST') {
      return json(route, { dispatched: true })
    }
    // ZIP 批量导入：created 1 个 + skipped 1 个（mock 不校验真实 zip 字节，只看 multipart 文件名）
    if (path === '/api/v2/super-assistant/palace/files/batch' && request.method() === 'POST') {
      const filename = /filename="([^"]+)"/.exec(request.postData() || '')?.[1] || 'import.zip'
      palaceBatchImports.push(filename)
      const created = {
        id: 'pf-zip-1', filename: '导入笔记.md', mimeType: 'text/markdown', size: 128,
        sha256: 'pf-zip-1-hash', extractedChars: 0, status: 'pending', error: null,
        entityCount: 0, relationCount: 0, editable: true, createdAt: at(0, 10), updatedAt: at(0, 10),
      }
      palaceFiles.unshift(created)
      return json(route, {
        created: [created],
        skipped: [{ filename: '重复笔记.md', reason: '同名文件已存在' }],
      }, 201)
    }
    const palacePreviewMatch = path.match(/^\/api\/v2\/super-assistant\/palace\/files\/([^/]+)\/preview$/)
    if (palacePreviewMatch && request.method() === 'GET') {
      palacePreviews.push(palacePreviewMatch[1])
      const row = palaceFiles.find(item => item.id === palacePreviewMatch[1])
      if (!row) return json(route, { detail: '文件不存在' }, 404)
      const isText = row.mimeType === 'text/markdown' || String(row.filename).endsWith('.txt')
      return json(route, {
        file: row,
        content: isText ? `# ${row.filename}\n\n张三 任职 ACME，正在研究知识图谱。` : '',
        truncated: false,
        previewable: isText,
      })
    }
    const palaceContentMatch = path.match(/^\/api\/v2\/super-assistant\/palace\/files\/([^/]+)\/content$/)
    if (palaceContentMatch && request.method() === 'PUT') {
      palaceContentPuts.push(request.postData() || '')
      const row = palaceFiles.find(item => item.id === palaceContentMatch[1])
      if (!row) return json(route, { detail: '文件不存在' }, 404)
      row.status = 'pending'
      row.updatedAt = at(0, 10)
      return json(route, row)
    }
    const palaceReplaceMatch = path.match(/^\/api\/v2\/super-assistant\/palace\/files\/([^/]+)\/replace$/)
    if (palaceReplaceMatch && request.method() === 'POST') {
      const filename = /filename="([^"]+)"/.exec(request.postData() || '')?.[1] || 'replace.bin'
      palaceReplaces.push(`${palaceReplaceMatch[1]}:${filename}`)
      const row = palaceFiles.find(item => item.id === palaceReplaceMatch[1])
      if (!row) return json(route, { detail: '文件不存在' }, 404)
      row.filename = filename
      row.status = 'pending'
      row.updatedAt = at(0, 10)
      return json(route, row)
    }
    if (path === '/api/v2/super-assistant/palace/graph' && request.method() === 'GET') {
      return json(route, palaceGraphFixture)
    }
    if (path === '/api/v2/super-assistant/palace/graph/search' && request.method() === 'GET') {
      palaceGraphSearches.push(new URL(request.url()).searchParams.get('q') || '')
      return json(route, {
        available: true,
        entities: [palaceGraphFixture.nodes[0], palaceGraphFixture.nodes[1]],
        relations: [{
          source: 'e-1', target: 'e-2', source_name: '张三', target_name: 'ACME',
          name: '任职', source_files: ['个人知识库.md'],
        }],
      })
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
    if (path === '/api/v2/super-assistant/conversations') {
      if (request.method() === 'POST') {
        createCalls.push(request.postData() || '')
        const row = {
          id: `c-new-${createCalls.length}`, title: '新会话', model_config_id: 'model-1',
          status: 'active', created_at: at(0, 12), updated_at: at(0, 12),
        }
        createdConvs.unshift(row)
        return json(route, row, 201)
      }
      return json(route, [...createdConvs, ...conversationsFixture])
    }
    if (/^\/api\/v2\/super-assistant\/conversations\/[^/]+\/messages$/.test(path)) {
      const id = path.split('/')[5]
      if (id === 'c-today' && chatDone) {
        return json(route, [
          { id: 'm-1', conversation_id: 'c-today', role: 'user', content: '你好', status: 'complete', steps: [], token_usage: {}, created_at: at(0, 9) },
          { id: 'm-2', conversation_id: 'c-today', role: 'assistant', content: '你好，我是超级助手', status: 'complete', steps: [], token_usage: {}, created_at: at(0, 9) },
        ])
      }
      // c-earlier 是「有消息的历史会话」：新建任务去重、底部输入框等场景以此为夹具
      if (id === 'c-earlier') {
        return json(route, [
          { id: 'm-e1', conversation_id: 'c-earlier', role: 'user', content: '上周的问题', status: 'complete', steps: [], token_usage: {}, created_at: at(5, 10) },
          { id: 'm-e2', conversation_id: 'c-earlier', role: 'assistant', content: '上周的答复', status: 'complete', steps: [], token_usage: {}, created_at: at(5, 10) },
        ])
      }
      return json(route, [])
    }
    if (path === '/api/v2/super-assistant/skills') return json(route, [])
    if (path === '/api/v2/super-assistant/mcp-servers') return json(route, [])
    // 全局搜索：会话标题 + 消息内容（查询词含「需求」时命中 c-today 的标题与一条消息）
    if (path === '/api/v2/super-assistant/search/conversations') {
      const q = new URL(request.url()).searchParams.get('q') || ''
      searchQueries.push(q)
      return json(route, {
        query: q,
        conversations: q.includes('需求') ? [{
          id: 'c-today', title: '今日需求梳理', status: 'active', updatedAt: at(0, 9),
          titleMatched: true,
          messageHits: [{
            messageId: 'm-1', role: 'user',
            snippet: '……这是需求梳理的上下文片段……', createdAt: at(0, 9),
          }],
        }] : [],
      })
    }
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
    createCalls,
    fileUploads,
    fileDeletes,
    chatCalls,
    searchQueries,
    palaceUploads,
    palaceDeletes,
    palacePreviews,
    palaceContentPuts,
    palaceReplaces,
    palaceBatchImports,
    palaceGraphSearches,
    isChatDone: () => chatDone,
  }
}

test('工作台骨架：七项入口齐备，历史会话按今日/昨日/历史分组，归档折叠', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  await expect(page.getByRole('button', { name: '新建任务' })).toBeVisible()
  await expect(page.getByRole('button', { name: /全局搜索/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '定时任务' })).toBeVisible()
  await expect(page.getByRole('button', { name: '记忆宫殿' })).toBeVisible()
  await expect(page.getByRole('link', { name: '本体治理' })).toBeVisible()
  await expect(page.getByRole('button', { name: '外部集成' })).toBeVisible()
  await expect(page.getByRole('button', { name: /退出登录/ })).toBeVisible()

  await expect(page.locator('[data-workbench-group="today"] [data-workbench-conversation="c-today"]')).toHaveCount(1)
  await expect(page.locator('[data-workbench-group="yesterday"] [data-workbench-conversation="c-yesterday"]')).toHaveCount(1)
  await expect(page.locator('[data-workbench-group="earlier"] [data-workbench-conversation="c-earlier"]')).toHaveCount(1)
  // 历史分组采用 shadcn Sidebar 展示原语（组标签 + 菜单列表）
  await expect(page.locator('[data-workbench-group="today"] [data-slot="sidebar-group-label"]')).toHaveText('今日对话')
  await expect(page.locator('[data-workbench-group="today"] ul[data-slot="sidebar-menu"]')).toHaveCount(1)
  await expect(page.locator('[data-workbench-group="yesterday"] [data-slot="sidebar-group-label"]')).toHaveText('昨日对话')
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

test('定时任务为如实占位的即将上线弹窗，全局搜索打开真实检索面板', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  // 全局搜索已是真实功能：打开 ReUI Command 检索面板
  await page.getByRole('button', { name: '全局搜索' }).click()
  await expect(page.getByPlaceholder('搜索会话标题与消息内容…')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

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

  // 附件 chips 展示在输入框上方
  const chipsBox = await page.getByTestId('super-assistant-attachments').boundingBox()
  const inputBox = await page.getByRole('textbox', { name: '向超级助手发送消息' }).boundingBox()
  expect(chipsBox && inputBox && chipsBox.y + chipsBox.height <= inputBox.y + 1).toBe(true)

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

test('新建任务去重：空会话或全新视图下点击不再创建新会话', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  // c-today 是空会话：点击新建任务不创建
  await expect(page.getByTestId('super-assistant-composer')).toBeVisible()
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.waitForTimeout(300)
  expect(mocks.createCalls).toHaveLength(0)

  // 切到有消息的 c-earlier：点击新建任务创建新会话并选中
  await page.locator('[data-workbench-conversation="c-earlier"] button').first().click()
  await expect(page.getByText('上周的答复')).toBeVisible()
  await page.getByRole('button', { name: '新建任务' }).click()
  await expect.poll(() => mocks.createCalls.length).toBe(1)
  await expect(page.locator('[data-workbench-conversation="c-new-1"]')).toHaveCount(1)

  // 新会话仍是空会话：再次点击不再创建
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.waitForTimeout(300)
  expect(mocks.createCalls).toHaveLength(1)
})

test('空态只保留品牌一句话，输入框占位符不混入用户输入', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  const hero = page.getByText('SuperAgent 工作空间2.0')
  await expect(hero).toBeVisible()
  await expect(page.getByText('有什么可以帮你？')).toHaveCount(0)
  await expect(page.getByText('试试这样问')).toHaveCount(0)
  // 品牌一句话是页面视觉主角：字号不小于 text-3xl（30px）
  const heroFontSize = await hero.evaluate(el => parseFloat(getComputedStyle(el).fontSize))
  expect(heroFontSize).toBeGreaterThanOrEqual(30)

  const textbox = page.getByRole('textbox', { name: '向超级助手发送消息' })
  await expect(textbox).toHaveAttribute('placeholder', '咨询任何问题，创造任何事物')
  await textbox.fill('帮我梳理需求')
  await expect(textbox).toHaveValue('帮我梳理需求')
})

test('助手配置面板为白色背景', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  await page.getByRole('button', { name: '打开助手配置' }).click()
  const panel = page.locator('section[aria-label="助手配置"]')
  await expect(panel).toBeVisible()
  await expect.poll(() => panel.evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgb(255, 255, 255)')
})

test('重命名会话：点击其它处自动取消，Enter 仍可保存', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  // 点击表单外（聊天输入框）→ 自动取消编辑，不发 PATCH
  const titleButton = page.locator('section header').getByRole('button', { name: '今日需求梳理' })
  await titleButton.click()
  const renameInput = page.getByRole('textbox', { name: '编辑会话名称' })
  await expect(renameInput).toBeVisible()
  await renameInput.fill('改名尝试')
  await page.getByRole('textbox', { name: '向超级助手发送消息' }).click()
  await expect(renameInput).toHaveCount(0)
  expect(mocks.patchBodies).toHaveLength(0)
  await expect(page.locator('section header').getByRole('button', { name: '今日需求梳理' })).toBeVisible()

  // Enter 保存路径不受影响
  await page.locator('section header').getByRole('button', { name: '今日需求梳理' }).click()
  await page.getByRole('textbox', { name: '编辑会话名称' }).fill('新名称')
  await page.getByRole('textbox', { name: '编辑会话名称' }).press('Enter')
  await expect.poll(() => mocks.patchBodies.length).toBe(1)
  expect(JSON.parse(mocks.patchBodies[0])).toMatchObject({ title: '新名称' })
})

test('记忆宫殿：文件库/知识图谱页签与上传删除联动，外部集成为如实占位', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('button', { name: '记忆宫殿' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('heading', { name: '记忆宫殿' })).toBeVisible()

  // 页签骨架：默认落在文件库，知识图谱需切换
  await expect(dialog.getByRole('tab', { name: '文件库' })).toHaveAttribute('aria-selected', 'true')
  const filesSection = dialog.getByTestId('super-assistant-palace-files')
  await expect(filesSection).toBeVisible()
  await expect(filesSection.getByText('个人知识库.md')).toBeVisible()
  await expect(filesSection.getByText('已建图')).toBeVisible()
  await expect(filesSection.getByText('抽取中')).toBeVisible()
  await expect(dialog.getByTestId('super-assistant-palace-graph')).toHaveCount(0)

  // 图谱页签：实体/关系计数 + ECharts canvas 渲染
  await dialog.getByRole('tab', { name: '知识图谱' }).click()
  const graphSection = dialog.getByTestId('super-assistant-palace-graph')
  await expect(graphSection).toBeVisible()
  await expect(graphSection.getByText(/3 实体 \/ 2 关系/)).toBeVisible()
  await expect(graphSection.locator('canvas').first()).toBeVisible()

  // 回到文件库上传：隐藏 input 接线到 palace 上传端点，列表即时刷新
  await dialog.getByRole('tab', { name: '文件库' }).click()
  await dialog.getByTestId('palace-file-input').setInputFiles({
    name: '新文档.md', mimeType: 'text/markdown', buffer: Buffer.from('# 新文档\n张三 任职 ACME'),
  })
  await expect.poll(() => mocks.palaceUploads.length).toBe(1)
  expect(mocks.palaceUploads[0]).toBe('新文档.md')
  await expect(filesSection.getByText('新文档.md')).toBeVisible()

  // 删除：行内删除按钮联动 DELETE 端点并从列表消失
  await filesSection.locator('[data-palace-file="pf-2"]').getByRole('button', { name: '删除 行业研究.pdf' }).click()
  await expect.poll(() => mocks.palaceDeletes.length).toBe(1)
  await expect(filesSection.getByText('行业研究.pdf')).toHaveCount(0)

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  // 外部集成仍是如实占位的「即将上线」弹窗
  await page.getByRole('button', { name: '外部集成' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').getByRole('heading', { name: '外部集成' })).toBeVisible()
  await expect(page.getByRole('dialog').getByText(/邮箱、GitHub/)).toBeVisible()
})

test('记忆宫殿：文本文件内嵌预览，md 在线编辑保存触发 PUT content', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('button', { name: '记忆宫殿' }).click()
  const dialog = page.getByRole('dialog')
  const filesSection = dialog.getByTestId('super-assistant-palace-files')

  // 预览：Eye 图标 → 内嵌展开区显示 preview.content
  await filesSection.locator('[data-palace-file="pf-1"]').getByRole('button', { name: '预览 个人知识库.md' }).click()
  await expect.poll(() => mocks.palacePreviews.length).toBe(1)
  const previewPanel = dialog.getByTestId('palace-file-preview')
  await expect(previewPanel).toBeVisible()
  await expect(previewPanel.getByText('张三 任职 ACME，正在研究知识图谱。')).toBeVisible()

  // pdf 不可预览：previewable=false 时如实提示；且非 editable 文件不渲染编辑按钮
  await filesSection.locator('[data-palace-file="pf-2"]').getByRole('button', { name: '预览 行业研究.pdf' }).click()
  await expect(dialog.getByTestId('palace-file-preview')).toContainText('该格式暂不支持预览')
  await expect(filesSection.locator('[data-palace-file="pf-2"]').getByRole('button', { name: '编辑 行业研究.pdf' })).toHaveCount(0)

  // 编辑：md 文件经 preview 端点取初稿，改写后显式保存
  await filesSection.locator('[data-palace-file="pf-1"]').getByRole('button', { name: '编辑 个人知识库.md' }).click()
  const editorPanel = dialog.getByTestId('palace-file-editor')
  await expect(editorPanel).toBeVisible()
  await expect.poll(() => mocks.palacePreviews.length).toBe(3)
  const textarea = editorPanel.locator('textarea')
  await expect(textarea).toHaveValue(/张三 任职 ACME/)
  await textarea.fill('# 更新后的知识库\n\n张三 任职 ACME。')
  await editorPanel.getByRole('button', { name: '保存并重建图谱' }).click()
  await expect.poll(() => mocks.palaceContentPuts.length).toBe(1)
  expect(JSON.parse(mocks.palaceContentPuts[0])).toMatchObject({ content: '# 更新后的知识库\n\n张三 任职 ACME。' })
  // toast 提示（渲染在弹窗外层的 ToastProvider）+ 编辑器关闭
  await expect(page.getByText('已保存，图谱重建已排队')).toBeVisible()
  await expect(editorPanel).toHaveCount(0)
})

test('记忆宫殿：ZIP 批量导入展示「导入 N 个，跳过 M 个」与跳过原因', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('button', { name: '记忆宫殿' }).click()
  const dialog = page.getByRole('dialog')
  const filesSection = dialog.getByTestId('super-assistant-palace-files')

  // mock 端点不校验真实 zip 字节：普通 buffer + .zip 文件名即可断言 batch 请求
  await dialog.getByTestId('palace-zip-input').setInputFiles({
    name: '知识库备份.zip', mimeType: 'application/zip', buffer: Buffer.from('placeholder-zip'),
  })
  await expect.poll(() => mocks.palaceBatchImports.length).toBe(1)
  expect(mocks.palaceBatchImports[0]).toBe('知识库备份.zip')

  const banner = dialog.getByTestId('palace-import-result')
  await expect(banner).toBeVisible()
  await expect(banner.getByText('导入 1 个，跳过 1 个')).toBeVisible()
  await banner.getByRole('button', { name: '查看跳过原因' }).click()
  await expect(banner.getByText('重复笔记.md：同名文件已存在')).toBeVisible()
  await expect(filesSection.getByText('导入笔记.md')).toBeVisible()
})

test('记忆宫殿：图谱关键词过滤、节点详情面板与邻域检索高亮', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('button', { name: '记忆宫殿' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('tab', { name: '知识图谱' }).click()
  const graphSection = dialog.getByTestId('super-assistant-palace-graph')
  await expect(graphSection.locator('canvas').first()).toBeVisible()

  // 过滤：300ms 防抖后只剩「张三」一个节点（计数文案，不做脆弱断言）
  await graphSection.getByTestId('palace-graph-filter').fill('张三')
  await expect(graphSection.getByTestId('palace-graph-filter-count')).toHaveText(/1 \/ 3 实体/)

  // 单节点力导向收敛于布局中心（series top 28 / bottom 8）：轮询点击画布中心命中节点
  const canvas = graphSection.locator('canvas').first()
  const box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  const detail = graphSection.getByTestId('palace-node-detail')
  await expect.poll(async () => {
    await canvas.click({ position: { x: box!.width / 2, y: box!.height / 2 + 10 } })
    return detail.isVisible()
  }).toBe(true)
  await expect(detail.getByText('张三', { exact: true })).toBeVisible()
  await expect(detail.getByText(/人物 · 提及 5 次 · 被引用 2 次/)).toBeVisible()
  await expect(detail.getByText('ACME（任职）')).toBeVisible()

  // 邻域检索：返回实体 id 集合成为高亮集，非命中节点静态降透明
  await detail.getByRole('button', { name: '在图谱中检索邻域' }).click()
  await expect.poll(() => mocks.palaceGraphSearches.length).toBe(1)
  expect(mocks.palaceGraphSearches[0]).toBe('张三')
  await expect(graphSection.getByTestId('palace-graph-highlight-count')).toContainText('2 个实体已高亮')
})

test('⌘K / Ctrl+K 唤起全局搜索面板', async ({ page }) => {
  await seedAuth(page)
  await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.keyboard.press('Control+k')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByPlaceholder('搜索会话标题与消息内容…')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('全局搜索：检索会话标题与消息内容，命中消息可跳转会话', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant')

  await page.getByRole('button', { name: '全局搜索' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()

  // 输入关键词（防抖 300ms 后发出检索请求）
  await page.getByPlaceholder('搜索会话标题与消息内容…').fill('需求')
  await expect.poll(() => mocks.searchQueries.length).toBeGreaterThan(0)
  await expect.poll(() => mocks.searchQueries.at(-1)).toBe('需求')

  // 标题命中与消息命中分组展示，关键词高亮
  await expect(page.getByTestId('global-search-title-hit')).toHaveCount(1)
  await expect(dialog.locator('mark').first()).toHaveText('需求')
  await expect(page.getByTestId('global-search-message-hit')).toHaveCount(1)

  // 选中消息命中：面板关闭并切到该会话
  await page.getByTestId('global-search-message-hit').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('[data-workbench-conversation="c-today"]')).toHaveClass(/bg-teal-50/)

  // 无结果文案
  await page.keyboard.press('Control+k')
  await page.getByPlaceholder('搜索会话标题与消息内容…').fill('不存在的词')
  await expect(page.getByText('没有匹配的会话或消息')).toBeVisible()
})

test('输入草稿按会话缓存：切换会话不丢内容，发送后清空', async ({ page }) => {
  await seedAuth(page)
  const mocks = await mockApis(page)
  await page.goto('/#/super-assistant?conversation=c-today')

  const textbox = page.getByRole('textbox', { name: '向超级助手发送消息' })
  await textbox.fill('A 会话的草稿')

  await page.locator('[data-workbench-conversation="c-yesterday"] button').first().click()
  await expect(textbox).toHaveValue('')
  await textbox.fill('B 会话的草稿')

  await page.locator('[data-workbench-conversation="c-today"] button').first().click()
  await expect(textbox).toHaveValue('A 会话的草稿')
  await page.locator('[data-workbench-conversation="c-yesterday"] button').first().click()
  await expect(textbox).toHaveValue('B 会话的草稿')

  // A 发送后其草稿清空，且不影响 B 的草稿
  await page.locator('[data-workbench-conversation="c-today"] button').first().click()
  await expect(textbox).toHaveValue('A 会话的草稿')
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect.poll(mocks.isChatDone).toBe(true)
  await expect(textbox).toHaveValue('')
  await page.locator('[data-workbench-conversation="c-yesterday"] button').first().click()
  await expect(textbox).toHaveValue('B 会话的草稿')
  await page.locator('[data-workbench-conversation="c-today"] button').first().click()
  await expect(textbox).toHaveValue('')
})
