import { expect, test, type Page, type Route } from '@playwright/test'

const currentConversation = {
  id: 'conversation-current',
  title: '现在有哪些流水线',
  browserSourceId: 'managed',
  createdAt: '2026-07-23T06:33:00.000Z',
  updatedAt: '2026-07-23T06:33:00.000Z',
}

const previousConversation = {
  id: 'conversation-previous',
  title: '你好',
  browserSourceId: 'managed',
  createdAt: '2026-07-23T06:32:00.000Z',
  updatedAt: '2026-07-23T06:32:00.000Z',
}

function ok(route: Route, data: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data }),
  })
}

async function mockSteward(
  page: Page,
  options: { failConversationCreation?: boolean } = {},
) {
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

  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/v1/')) return route.continue()
    if (path === '/api/v1/models') return ok(route, [])
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.route('**/api/v2/**', route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/v2/')) return route.continue()
    const method = route.request().method()
    if (path === '/api/v2/steward/status') {
      return ok(route, {
        n8n: { configured: true, enabled: true, api_url: 'http://n8n.test', reachable: true },
        llmReady: true,
        pipelineCounts: {},
      })
    }
    if (path === '/api/v2/steward/pipelines') return ok(route, [])
    if (path === '/api/v2/steward/conversations') {
      if (method === 'POST' && options.failConversationCreation) {
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: '暂时无法创建会话' }),
        })
      }
      return ok(route, [currentConversation, previousConversation])
    }
    if (path === '/api/v2/steward/conversations/conversation-current') {
      return ok(route, {
        ...currentConversation,
        messages: [{
          id: 'message-user',
          role: 'user',
          content: '现在有哪些流水线',
          steps: [],
          touchedPipelineIds: [],
          createdAt: '2026-07-23T06:33:00.000Z',
        }],
      })
    }
    if (path === '/api/v2/steward/conversations/conversation-current/files') return ok(route, [])
    if (path === '/api/v2/steward/conversations/conversation-current/export') {
      return ok(route, {
        format: 'openontology.data-steward.conversation',
        version: 1,
        exportedAt: '2026-07-23T07:00:00.000Z',
        conversation: {
          ...currentConversation,
          messageCount: 2,
          messages: [
            {
              id: 'message-user',
              role: 'user',
              content: '现在有哪些流水线',
              steps: [],
              touchedPipelineIds: [],
              model: null,
              tokenUsage: null,
              createdAt: '2026-07-23T06:33:00.000Z',
            },
            {
              id: 'message-assistant',
              role: 'assistant',
              content: '共有 4 条流水线。',
              steps: [{
                tool: 'list_pipelines',
                arguments: { includeArchived: false },
                summary: '列出 4 条流水线',
                durationMs: 18,
              }],
              touchedPipelineIds: ['pipeline-1'],
              model: 'deepseek-chat',
              tokenUsage: { inputTokens: 120, outputTokens: 24 },
              createdAt: '2026-07-23T06:33:02.000Z',
            },
          ],
        },
      })
    }
    return ok(route, [])
  })
}

test('历史会话将当前标识置于最右并导出完整 JSON', async ({ page }, testInfo) => {
  await mockSteward(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/data/pipelines/steward', { waitUntil: 'domcontentloaded' })

  await page.getByRole('button', { name: '查看会话记录' }).click()
  const dialog = page.getByRole('dialog', { name: '历史会话' })
  await expect(dialog).toBeVisible()

  await dialog.locator('[data-session-history-item="conversation-current"]').getByRole('button').first().click()
  await expect(dialog).toBeHidden()
  await page.getByRole('button', { name: '查看会话记录' }).click()

  const currentRow = dialog.locator('[data-session-history-item="conversation-current"]')
  await currentRow.hover()
  const exportButton = currentRow.getByRole('button', { name: '导出会话 现在有哪些流水线 的完整 JSON' })
  const deleteButton = currentRow.getByRole('button', { name: '删除会话 现在有哪些流水线' })
  const currentBadge = currentRow.getByText('当前', { exact: true })
  const [exportBox, deleteBox, currentBox] = await Promise.all([
    exportButton.boundingBox(),
    deleteButton.boundingBox(),
    currentBadge.boundingBox(),
  ])
  expect(exportBox).not.toBeNull()
  expect(deleteBox).not.toBeNull()
  expect(currentBox).not.toBeNull()
  expect(exportBox!.x).toBeLessThan(deleteBox!.x)
  expect(deleteBox!.x).toBeLessThan(currentBox!.x)

  const downloadPromise = page.waitForEvent('download')
  await exportButton.click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('现在有哪些流水线-conversa.json')

  const outputPath = testInfo.outputPath('steward-conversation.json')
  await download.saveAs(outputPath)
  const downloaded = JSON.parse(await import('node:fs/promises').then(fs => fs.readFile(outputPath, 'utf8')))
  expect(downloaded.conversation.messageCount).toBe(2)
  expect(downloaded.conversation.messages[1].content).toBe('共有 4 条流水线。')
  expect(downloaded.conversation.messages[1].steps[0].arguments).toEqual({ includeArchived: false })
  expect(downloaded.conversation.messages[1].tokenUsage).toEqual({ inputTokens: 120, outputTokens: 24 })
})

test('创建会话失败时保留已选附件以维持原有重试语义', async ({ page }) => {
  await mockSteward(page, { failConversationCreation: true })
  await page.goto('/#/data/pipelines/steward', { waitUntil: 'domcontentloaded' })

  const fileInput = page.locator('input[type="file"][accept*=".csv"]').first()
  const failedCreation = page.waitForResponse(response => (
    response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/v2/steward/conversations'
  ))

  await fileInput.setInputFiles({
    name: 'retry.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('id,name\n1,retry\n'),
  })
  await failedCreation

  await expect.poll(() => fileInput.evaluate((element: HTMLInputElement) => ({
    count: element.files?.length ?? 0,
    name: element.files?.[0]?.name ?? '',
  }))).toEqual({ count: 1, name: 'retry.csv' })
})

test('建议指令只填入输入框，不会立即触发 AI 执行', async ({ page }) => {
  await mockSteward(page)
  let chatRequests = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/v2/steward/chat') chatRequests += 1
  })
  await page.goto('/#/data/pipelines/steward', { waitUntil: 'domcontentloaded' })

  await page.getByRole('button', { name: '帮我执行指定n8n流水线并展示结果' }).click()

  await expect(page.getByTestId('steward-composer')).toHaveValue('帮我执行指定n8n流水线并展示结果')
  await expect(page.getByTestId('steward-composer')).toBeFocused()
  expect(chatRequests).toBe(0)
})
