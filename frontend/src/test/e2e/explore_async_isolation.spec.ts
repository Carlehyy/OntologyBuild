import { expect, test, type Page, type Route } from '@playwright/test'

const readiness = {
  ready: false,
  stage: '阶段0 · 定边界',
  gatesPassed: 0,
  gatesTotal: 9,
  blockingCount: 3,
  advisoryCount: 0,
  openQuestions: { blocking: 0, advisory: 0 },
  gates: [],
}

const emptyCanvas = {
  objects: [], actors: [], behaviors: [], events: [], rules: [], scenarios: [], questions: [],
}

const session = (id: string, marker: string) => ({
  id,
  title: id === 's1' ? '会话 A' : '会话 B',
  canvasVersion: 1,
  status: 'active',
  createdAt: '2026-07-24T00:00:00Z',
  updatedAt: '2026-07-24T00:00:00Z',
  canvas: emptyCanvas,
  completeness: {
    counts: { objects: 0, actors: 0, behaviors: 0, events: 0, rules: 0, scenarios: 0 },
    gaps: [],
  },
  readiness,
  messages: [{
    id: `${id}-message`,
    role: 'user',
    content: marker,
    steps: [],
    createdAt: '2026-07-24T00:00:00Z',
  }],
})

const file = (id: string, name: string, updatedAt: string) => ({
  id,
  sessionId: 's1',
  filename: name,
  relativePath: name,
  mimeType: 'text/markdown',
  fileSize: 20,
  charCount: 20,
  sha256: id,
  version: 1,
  source: 'upload',
  editable: true,
  status: 'ready',
  error: null,
  createdAt: updatedAt,
  updatedAt,
})

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

test.describe('业务探索异步结果归属隔离', () => {
  test('同一会话的迟到 GET 不会覆盖已开始的对话与新消息', async ({ page }) => {
    await authenticate(page)
    let sessionReads = 0
    let chatStarted = false

    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/v2/exploration/sessions' && request.method() === 'GET') {
        return ok(route, [
          { id: 's1', title: '会话 A', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' },
        ])
      }
      if (path === '/api/v2/exploration/sessions/s1' && request.method() === 'GET') {
        sessionReads += 1
        if (sessionReads === 1) return ok(route, session('s1', 'INITIAL_SNAPSHOT'))
        await new Promise(resolve => setTimeout(resolve, 500))
        return ok(route, session('s1', 'STALE_LOAD_SNAPSHOT'))
      }
      if (path === '/api/v2/exploration/sessions/s1/attachments') return ok(route, [])
      if (path === '/api/v2/exploration/sessions/s1/chat') {
        chatStarted = true
        await new Promise(resolve => setTimeout(resolve, 80))
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: [
            'data: {"type":"meta","sessionId":"s1","model":"mock-model"}',
            '',
            'data: {"type":"answer","content":"LIVE_CHAT_ANSWER"}',
            '',
            'data: {"type":"done"}',
            '',
            '',
          ].join('\n'),
        })
      }
      return ok(route, [])
    })

    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('INITIAL_SNAPSHOT', { exact: true })).toBeVisible()

    // 对当前会话再次发起慢加载，并在其返回前开始新对话。
    await page.getByRole('button', { name: '查看历史会话' }).click()
    await page.getByRole('button', { name: /^会话 A/ }).click()
    await page.getByTestId('exploration-composer').fill('并发中的新消息')
    await page.getByRole('button', { name: '发送消息' }).click()
    await expect.poll(() => chatStarted).toBe(true)
    await expect(page.getByText('LIVE_CHAT_ANSWER', { exact: true })).toBeVisible()

    // 慢 GET 此时才返回；它携带的旧消息快照不得覆盖新 user/assistant 消息。
    await page.waitForTimeout(550)
    await expect(page.getByText('并发中的新消息', { exact: true })).toBeVisible()
    await expect(page.getByText('LIVE_CHAT_ANSWER', { exact: true })).toBeVisible()
    await expect(page.getByText('STALE_LOAD_SNAPSHOT', { exact: true })).toBeHidden()
  })

  test('空态并发发送与上传只创建一个会话并写入同一 sid', async ({ page }) => {
    await authenticate(page)
    let createCount = 0
    const chatTargets: string[] = []
    const uploadTargets: string[] = []

    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/v2/exploration/sessions' && request.method() === 'GET') {
        return ok(route, [])
      }
      if (path === '/api/v2/exploration/sessions' && request.method() === 'POST') {
        createCount += 1
        const id = `created-${createCount}`
        await new Promise(resolve => setTimeout(resolve, 250))
        return ok(route, {
          id,
          title: '新探索',
          canvasVersion: 0,
          status: 'active',
          createdAt: '2026-07-24T00:00:00Z',
          updatedAt: '2026-07-24T00:00:00Z',
        })
      }
      const chatMatch = path.match(/^\/api\/v2\/exploration\/sessions\/([^/]+)\/chat$/)
      if (chatMatch) {
        chatTargets.push(chatMatch[1])
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: [
            `data: {"type":"meta","sessionId":"${chatMatch[1]}","model":"mock-model"}`,
            '',
            'data: {"type":"answer","content":"SHARED_SESSION_ANSWER"}',
            '',
            'data: {"type":"done"}',
            '',
            '',
          ].join('\n'),
        })
      }
      const attachmentMatch = path.match(
        /^\/api\/v2\/exploration\/sessions\/([^/]+)\/attachments$/,
      )
      if (attachmentMatch && request.method() === 'POST') {
        uploadTargets.push(attachmentMatch[1])
        return ok(route, {
          id: 'attachment-1',
          sessionId: attachmentMatch[1],
          filename: 'brief.md',
          relativePath: 'brief.md',
          mimeType: 'text/markdown',
          fileSize: 12,
          charCount: 12,
          sha256: 'brief',
          version: 1,
          source: 'upload',
          editable: true,
          status: 'ready',
          error: null,
          createdAt: '2026-07-24T00:00:01Z',
          updatedAt: '2026-07-24T00:00:01Z',
        })
      }
      if (attachmentMatch && request.method() === 'GET') return ok(route, [])
      return ok(route, [])
    })

    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    await page.getByTestId('exploration-composer').fill('首条并发消息')
    const sendButton = page.getByRole('button', { name: '发送消息' })
    await expect(sendButton).toBeEnabled()

    await Promise.all([
      page.locator('input[type="file"]').setInputFiles({
        name: 'brief.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from('# 业务资料'),
      }),
      sendButton.click(),
    ])

    await expect.poll(() => createCount).toBe(1)
    await expect.poll(() => chatTargets).toEqual(['created-1'])
    await expect.poll(() => uploadTargets).toEqual(['created-1'])
    await expect(page.getByText('首条并发消息', { exact: true })).toBeVisible()
    await expect(page.getByText('SHARED_SESSION_ANSWER', { exact: true })).toBeVisible()
  })

  test('空态慢创建时同一事件轮双击发送只产生一组对话', async ({ page }) => {
    await authenticate(page)
    let createCount = 0
    let chatCount = 0

    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/v2/exploration/sessions' && request.method() === 'GET') {
        return ok(route, [])
      }
      if (path === '/api/v2/exploration/sessions' && request.method() === 'POST') {
        createCount += 1
        await new Promise(resolve => setTimeout(resolve, 300))
        return ok(route, {
          id: 'created-once',
          title: '新探索',
          canvasVersion: 0,
          status: 'active',
          createdAt: '2026-07-24T00:00:00Z',
          updatedAt: '2026-07-24T00:00:00Z',
        })
      }
      if (path === '/api/v2/exploration/sessions/created-once/chat') {
        chatCount += 1
        await new Promise(resolve => setTimeout(resolve, 80))
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: [
            'data: {"type":"meta","sessionId":"created-once","model":"mock-model"}',
            '',
            'data: {"type":"answer","content":"DOUBLE_CLICK_ANSWER"}',
            '',
            'data: {"type":"done"}',
            '',
            '',
          ].join('\n'),
        })
      }
      if (path === '/api/v2/exploration/sessions/created-once/attachments') {
        return ok(route, [])
      }
      return ok(route, [])
    })

    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    await page.getByTestId('exploration-composer').fill('双击只发送一次')
    const sendButton = page.getByRole('button', { name: '发送消息' })
    await expect(sendButton).toBeEnabled()

    // 两次原生 click 在同一个浏览器任务内触发，第二次发生在 React busy
    // 状态来得及重渲染之前，稳定覆盖真实快速双击的竞态窗口。
    await sendButton.evaluate((button: HTMLButtonElement) => {
      button.click()
      button.click()
    })

    await expect.poll(() => createCount).toBe(1)
    await expect.poll(() => chatCount).toBe(1)
    await expect(page.getByText('双击只发送一次', { exact: true })).toHaveCount(1)
    await expect(page.getByText('DOUBLE_CLICK_ANSWER', { exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: '复制用户消息' })).toHaveCount(1)
    await expect(page.getByRole('button', { name: '复制助手回复' })).toHaveCount(1)
    await expect(page.getByText('正在理解业务，规划澄清问题…', { exact: true })).toHaveCount(0)
  })

  test('会话创建失败会释放发送锁并允许保留原消息重试', async ({ page }) => {
    await authenticate(page)
    let createCount = 0
    let chatCount = 0

    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/v2/exploration/sessions' && request.method() === 'GET') {
        return ok(route, [])
      }
      if (path === '/api/v2/exploration/sessions' && request.method() === 'POST') {
        createCount += 1
        if (createCount === 1) {
          return route.fulfill({
            status: 500,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'SESSION_CREATE_FAILED' }),
          })
        }
        return ok(route, {
          id: 'retry-session',
          title: '重试会话',
          canvasVersion: 0,
          status: 'active',
          createdAt: '2026-07-24T00:00:00Z',
          updatedAt: '2026-07-24T00:00:00Z',
        })
      }
      if (path === '/api/v2/exploration/sessions/retry-session/chat') {
        chatCount += 1
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: [
            'data: {"type":"answer","content":"RETRY_SUCCEEDED"}',
            '',
            'data: {"type":"done"}',
            '',
            '',
          ].join('\n'),
        })
      }
      if (path === '/api/v2/exploration/sessions/retry-session/attachments') {
        return ok(route, [])
      }
      return ok(route, [])
    })

    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    const composer = page.getByTestId('exploration-composer')
    const sendButton = page.getByRole('button', { name: '发送消息' })
    await composer.fill('创建失败后重试')
    await sendButton.click()
    await expect(page.getByText('SESSION_CREATE_FAILED', { exact: true })).toBeVisible()
    await expect(composer).toHaveValue('创建失败后重试')
    await expect(sendButton).toBeEnabled()

    await sendButton.click()
    await expect.poll(() => createCount).toBe(2)
    await expect.poll(() => chatCount).toBe(1)
    await expect(page.getByText('创建失败后重试', { exact: true })).toHaveCount(1)
    await expect(page.getByText('RETRY_SUCCEEDED', { exact: true })).toBeVisible()
  })

  test('迟到的会话 GET 和 SSE 不会污染当前会话', async ({ page }) => {
    await authenticate(page)
    let s1Reads = 0
    let chatStarted = false

    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/v2/exploration/sessions' && route.request().method() === 'GET') {
        return ok(route, [
          { id: 's1', title: '会话 A', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' },
          { id: 's2', title: '会话 B', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' },
        ])
      }
      if (path === '/api/v2/exploration/sessions/s1' && route.request().method() === 'GET') {
        s1Reads += 1
        if (s1Reads > 1) await new Promise(resolve => setTimeout(resolve, 40))
        return ok(route, session('s1', 'S1_ONLY'))
      }
      if (path === '/api/v2/exploration/sessions/s2' && route.request().method() === 'GET') {
        await new Promise(resolve => setTimeout(resolve, 450))
        return ok(route, session('s2', 'S2_ONLY'))
      }
      if (path.endsWith('/attachments')) return ok(route, [])
      if (path === '/api/v2/exploration/sessions/s1/chat') {
        chatStarted = true
        await new Promise(resolve => setTimeout(resolve, 500))
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: [
            'data: {"type":"meta","sessionId":"s1","model":"slow-model"}',
            '',
            'data: {"type":"answer","content":"A_STREAM_RESULT"}',
            '',
            'data: {"type":"done"}',
            '',
            '',
          ].join('\n'),
        })
      }
      return ok(route, [])
    })

    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('S1_ONLY', { exact: true })).toBeVisible()

    // 先发出慢的 B 请求，再立即切回 A；B 的迟到结果不得覆盖 A。
    await page.getByRole('button', { name: '查看历史会话' }).click()
    await page.getByRole('button', { name: /^会话 B/ }).click()
    await page.getByRole('button', { name: '查看历史会话' }).click()
    await page.getByRole('button', { name: /^会话 A/ }).click()
    await expect(page.getByText('S1_ONLY', { exact: true })).toBeVisible()
    await page.waitForTimeout(600)
    await expect(page.getByText('S1_ONLY', { exact: true })).toBeVisible()
    await expect(page.getByText('S2_ONLY', { exact: true })).toBeHidden()

    // A 的流式请求尚未返回时切到 B，A 的 answer 不得进入 B。
    await page.getByTestId('exploration-composer').fill('启动慢请求')
    await page.getByRole('button', { name: '发送消息' }).click()
    await expect.poll(() => chatStarted).toBe(true)
    await page.getByRole('button', { name: '查看历史会话' }).click()
    await page.getByRole('button', { name: /^会话 B/ }).click()
    await expect(page.getByText('S2_ONLY', { exact: true })).toBeVisible()
    await page.waitForTimeout(650)
    await expect(page.getByText('A_STREAM_RESULT', { exact: true })).toBeHidden()
    await expect(page.getByText('S2_ONLY', { exact: true })).toBeVisible()
  })

  test('反向返回的文件预览始终对应当前文件', async ({ page }) => {
    await authenticate(page)
    const fileA = file('a', 'A.md', '2026-07-24T00:00:02Z')
    const fileB = file('b', 'B.md', '2026-07-24T00:00:01Z')

    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path === '/api/v2/exploration/sessions') {
        return ok(route, [
          { id: 's1', title: '会话 A', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' },
        ])
      }
      if (path === '/api/v2/exploration/sessions/s1') return ok(route, session('s1', 'S1_ONLY'))
      if (path === '/api/v2/exploration/sessions/s1/attachments') return ok(route, [fileA, fileB])
      if (path.endsWith('/attachments/a/preview')) {
        await new Promise(resolve => setTimeout(resolve, 40))
        return ok(route, {
          id: 'a', relativePath: 'A.md', content: '# A_CURRENT', version: 1,
          mimeType: 'text/markdown', editable: true, truncated: false,
        })
      }
      if (path.endsWith('/attachments/b/preview')) {
        await new Promise(resolve => setTimeout(resolve, 450))
        return ok(route, {
          id: 'b', relativePath: 'B.md', content: '# B_STALE', version: 1,
          mimeType: 'text/markdown', editable: true, truncated: false,
        })
      }
      return ok(route, [])
    })

    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: '查看会话文件' }).click()
    await expect(page.getByText('A_CURRENT', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: /B\.md/ }).click()
    await page.getByRole('button', { name: /A\.md/ }).click()
    await expect(page.getByText('A_CURRENT', { exact: true })).toBeVisible()
    await page.waitForTimeout(600)
    await expect(page.getByText('A_CURRENT', { exact: true })).toBeVisible()
    await expect(page.getByText('B_STALE', { exact: true })).toBeHidden()
  })
})
