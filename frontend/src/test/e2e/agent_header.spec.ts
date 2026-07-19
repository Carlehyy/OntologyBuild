import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-07-19T08:00:00+00:00'

async function mockAgentHeader(page: Page) {
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
        description: '智能助手顶栏验证',
        created_at: now,
        updated_at: now,
      }],
      total: 1,
      page: 1,
      page_size: 20,
    })
    if (path === '/api/v1/models') return json(route, [])
    return route.fallback()
  })

  await page.route('**/api/v2/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v2/formal/ontologies/ontology-1/full') return json(route, {
      id: 'ontology-1',
      name: '供应链本体',
      version: '1.0.0',
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
    return route.fallback()
  })
}

test('智能助手顶栏只保留有色历史会话入口', async ({ page }) => {
  await mockAgentHeader(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/#/agent')
  await page.getByLabel('选择本体').selectOption('ontology-1')

  await expect(page.getByText('创建新会话')).toHaveCount(0)
  const historyButton = page.getByTestId('agent-session-history-button')
  await expect(historyButton).toHaveCSS('background-color', 'rgb(240, 253, 250)')
  await expect(historyButton).toHaveCSS('color', 'rgb(13, 148, 136)')

  await historyButton.click()
  await expect(page.getByRole('dialog', { name: '历史会话' })).toBeVisible()
  await expect(page.getByRole('button', { name: '新建' })).toBeVisible()
  await expect(historyButton).toHaveCSS('background-color', 'rgb(204, 251, 241)')
})

test('授权边界弹窗高度跟随内容且附加指令后无大块留白', async ({ page }) => {
  await mockAgentHeader(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/#/agent')
  await page.getByLabel('选择本体').selectOption('ontology-1')
  await page.getByRole('button', { name: '授权边界配置' }).click()

  const dialog = page.getByTestId('agent-boundary-dialog')
  const extra = page.getByTestId('agent-boundary-extra')
  const footer = page.getByTestId('agent-boundary-footer')
  await expect(dialog).toBeVisible()

  const dialogBox = await dialog.boundingBox()
  const extraBox = await extra.boundingBox()
  const footerBox = await footer.boundingBox()
  expect(dialogBox).not.toBeNull()
  expect(extraBox).not.toBeNull()
  expect(footerBox).not.toBeNull()
  expect(dialogBox!.height).toBeLessThan(700)
  expect(footerBox!.y - (extraBox!.y + extraBox!.height)).toBeLessThanOrEqual(24)
})
