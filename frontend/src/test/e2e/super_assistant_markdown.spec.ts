import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-07-19T08:00:00+00:00'

const wrappedMarkdown = `\`\`\`\`markdown
### 三级标题

**粗体文本**、*斜体文本*以及 ~~删除线~~。

> 这是一段引用文字。

- 无序项
- [x] 已完成
- [ ] 待办事项

| 名称 | 类型 | 备注 |
| --- | --- | --- |
| 项目 A | 工具 | 开源免费 |

\`\`\`python
def hello():
    print("Hello, World!")
\`\`\`

[OpenOntology](https://openontology.org)
\`\`\`\`

围栏外的补充说明。`

async function mockSuperAssistant(page: Page) {
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
    if (path === '/api/v1/models') return json(route, [{
      id: 'model-1',
      name: 'DeepSeek',
      config_type: 'llm',
      provider: 'deepseek',
      api_base: 'https://api.deepseek.com',
      has_api_key: true,
      enabled: true,
      is_default: true,
      last_test_status: 'success',
      last_tested_at: now,
      last_test_message: 'ok',
      models: ['deepseek-v4-pro'],
      options: { max_context_tokens: 100_000 },
      created_by: 'admin',
      created_at: now,
      updated_at: now,
    }])
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.route('**/api/v2/super-assistant/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v2/super-assistant/conversations') return json(route, [{
      id: 'conversation-1',
      title: 'Markdown 渲染验证',
      model_config_id: 'model-1',
      status: 'active',
      created_at: now,
      updated_at: now,
    }])
    if (path === '/api/v2/super-assistant/conversations/conversation-1/messages') return json(route, [
      {
        id: 'user-1',
        conversation_id: 'conversation-1',
        role: 'user',
        content: '展示 Markdown',
        status: 'complete',
        steps: [],
        token_usage: {},
        created_at: now,
      },
      {
        id: 'assistant-1',
        conversation_id: 'conversation-1',
        role: 'assistant',
        content: wrappedMarkdown,
        status: 'complete',
        steps: [],
        token_usage: { inputTokens: 974, contextTokens: 974, contextLimit: 100_000 },
        created_at: now,
      },
    ])
    if (path === '/api/v2/super-assistant/skills') return json(route, [])
    if (path === '/api/v2/super-assistant/mcp-servers') return json(route, [])
    return route.fulfill({ status: 404, body: '{}' })
  })
}

test('渲染 Markdown 主体并允许拖动上下文浮窗', async ({ page }) => {
  await mockSuperAssistant(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/#/super-assistant')

  await expect(page.getByRole('heading', { name: '三级标题' })).toBeVisible()
  await expect(page.locator('strong').filter({ hasText: '粗体文本' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '名称' })).toBeVisible()
  await expect(page.locator('pre').filter({ hasText: 'def hello()' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'OpenOntology' })).toHaveAttribute('target', '_blank')
  await expect(page.getByText('围栏外的补充说明。')).toBeVisible()
  await expect(page.getByText('### 三级标题', { exact: true })).toHaveCount(0)

  const panel = page.locator('aside[aria-label^="当前上下文占比"]')
  const handle = panel.getByRole('button', { name: /移动当前上下文窗口/ })
  const before = await panel.boundingBox()
  const handleBox = await handle.boundingBox()
  expect(before).not.toBeNull()
  expect(handleBox).not.toBeNull()

  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
  await page.mouse.down()
  await page.mouse.move(handleBox!.x - 230, handleBox!.y - 150, { steps: 8 })
  await page.mouse.up()

  const moved = await panel.boundingBox()
  expect(moved).not.toBeNull()
  expect(moved!.x).toBeLessThan(before!.x - 150)
  expect(moved!.y).toBeLessThan(before!.y - 90)

  const main = await panel.locator('..').boundingBox()
  expect(main).not.toBeNull()
  expect(moved!.x).toBeGreaterThanOrEqual(main!.x + 10)
  expect(moved!.y).toBeGreaterThanOrEqual(main!.y + 10)

  await handle.dblclick()
  const reset = await panel.boundingBox()
  expect(reset).not.toBeNull()
  expect(Math.abs(reset!.x - before!.x)).toBeLessThan(2)
  expect(Math.abs(reset!.y - before!.y)).toBeLessThan(2)
})
