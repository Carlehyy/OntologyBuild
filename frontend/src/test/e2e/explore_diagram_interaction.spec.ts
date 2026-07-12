import { expect, test, type Page, type Route } from '@playwright/test'

const diagram = `stateDiagram-v2
  [*] --> pending
  pending --> processing : accept
  processing --> closed : finish`

const canvas = {
  objects: [{
    id: 'o1', name: 'WorkOrder', display_name: '工单', key_attribute: 'id',
    attributes: [
      { name: 'id', type_hint: '文本' },
      { name: 'status', display_name: '状态', type_hint: '枚举', enum: ['pending', 'processing', 'closed'] },
    ],
    relations: [],
  }],
  actors: [], behaviors: [], events: [], rules: [], scenarios: [], questions: [],
}

const readiness = {
  ready: false, stage: '阶段0 · 定边界', gatesPassed: 2, gatesTotal: 9,
  blockingCount: 2, advisoryCount: 0, openQuestions: { blocking: 0, advisory: 0 },
  gates: [],
}

async function mockExplore(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })
  await page.route('**/mock-image.svg', route => route.fulfill({
    status: 200,
    contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="700" viewBox="0 0 1200 700"><rect width="1200" height="700" fill="#dff5ef"/><circle cx="600" cy="350" r="180" fill="#0f766e"/></svg>',
  }))
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })
    if (path === '/api/v2/exploration/sessions') {
      return ok([{ id: 's1', title: '图表交互测试', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' }])
    }
    if (path === '/api/v2/exploration/sessions/s1') {
      return ok({
        id: 's1', title: '图表交互测试', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '',
        canvas,
        completeness: { counts: { objects: 1, actors: 0, behaviors: 0, events: 0, rules: 0, scenarios: 0 }, gaps: [] },
        readiness,
        messages: [{
          id: 'm1', role: 'assistant', createdAt: '2026-07-12T00:00:00Z',
          content: '下方是业务示意图：\n\n![业务示意图](/mock-image.svg)',
          steps: [{
            tool: 'show_diagram', arguments: { kind: 'state' }, summary: '展示状态图', durationMs: 1,
            diagram: { kind: 'state', title: '工单状态图', mermaid: diagram, warnings: [] },
          }],
        }],
      })
    }
    if (path === '/api/v2/exploration/sessions/s1/attachments') return ok([])
    if (path.startsWith('/api/v2/exploration/sessions/s1/diagrams/')) {
      const kind = path.split('/').pop()
      if (kind === 'er') await new Promise(resolve => setTimeout(resolve, 250))
      return ok({ kind, title: kind === 'state' ? '工单状态图' : 'ER 图', mermaid: diagram, warnings: [] })
    }
    return ok([])
  })
}

async function expectWheelAndDrag(page: Page, testId: string) {
  const viewport = page.getByTestId(testId)
  await expect(viewport).toBeVisible()
  const scaleBefore = Number(await viewport.getAttribute('data-scale'))
  await viewport.hover()
  await page.mouse.wheel(0, -420)
  await expect.poll(async () => Number(await viewport.getAttribute('data-scale')))
    .toBeGreaterThan(scaleBefore)

  const box = await viewport.boundingBox()
  expect(box).not.toBeNull()
  const xBefore = await viewport.getAttribute('data-offset-x')
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width / 2 + 80, box!.y + box!.height / 2 + 45, { steps: 4 })
  await page.mouse.up()
  await expect.poll(async () => viewport.getAttribute('data-offset-x')).not.toBe(xBefore)
}

test.describe('业务探索图表与图片交互', () => {
  test.beforeEach(async ({ page }) => {
    await mockExplore(page)
    await page.goto('/#/explore')
    await expect(page.getByTestId('diagram-thumbnail')).toBeVisible({ timeout: 10_000 })
  })

  test('聊天图表支持完整预览、滚轮缩放和拖拽移动', async ({ page }) => {
    await expect(page.getByTestId('diagram-thumbnail')).toBeVisible()
    await page.getByTestId('diagram-thumbnail').click()
    await expect(page.getByTestId('diagram-preview-modal')).toBeVisible()
    await expectWheelAndDrag(page, 'diagram-preview-canvas')
    await page.getByRole('button', { name: '关闭图表预览' }).click()
    await expect(page.getByTestId('diagram-preview-modal')).toBeHidden()
  })

  test('业务画布弹窗和 Markdown 图片使用同一套自适应交互', async ({ page }) => {
    await page.getByRole('button', { name: '图表' }).click()
    await page.getByRole('button', { name: '状态图', exact: true }).click()
    await expect(page.getByTestId('canvas-diagram-title')).toHaveText('工单状态图')
    await page.waitForTimeout(300)
    await expect(page.getByTestId('canvas-diagram-title')).toHaveText('工单状态图')
    await expectWheelAndDrag(page, 'diagram-inline-canvas')
    await page.getByRole('button', { name: '关闭业务建模图表' }).click()

    await page.getByRole('button', { name: '预览图片：业务示意图' }).click()
    await expectWheelAndDrag(page, 'image-preview-viewport')
    await page.getByRole('button', { name: '关闭图片预览' }).click()
  })

  test('长文本输入与底部操作栏保持独立层级', async ({ page }) => {
    const composer = page.getByTestId('exploration-composer')
    const inputLayer = page.getByTestId('exploration-composer-input')
    const toolbar = page.getByTestId('exploration-composer-toolbar')

    await composer.fill(Array.from({ length: 14 }, (_, index) => `第 ${index + 1} 行业务描述`).join('\n'))

    const inputBox = await inputLayer.boundingBox()
    const toolbarBox = await toolbar.boundingBox()
    expect(inputBox).not.toBeNull()
    expect(toolbarBox).not.toBeNull()
    expect(inputBox!.y + inputBox!.height).toBeLessThanOrEqual(toolbarBox!.y + 1)
    expect(await composer.evaluate(element => element.clientHeight)).toBeLessThanOrEqual(208)
    expect(await composer.evaluate(element => element.scrollHeight)).toBeGreaterThan(208)

    const uploadBox = await page.getByRole('button', { name: '上传参考资料' }).boundingBox()
    const webBox = await page.getByTestId('web-search-toggle').boundingBox()
    const sendBox = await page.getByRole('button', { name: '发送消息' }).boundingBox()
    const historyBox = await page.getByTestId('message-history-button').boundingBox()
    expect(uploadBox!.x).toBeLessThan(webBox!.x)
    expect(webBox!.x).toBeLessThan(sendBox!.x)
    expect(sendBox!.x).toBeLessThan(historyBox!.x)
    expect(toolbarBox!.y).toBeLessThan(uploadBox!.y + uploadBox!.height)
    expect(toolbarBox!.y).toBeLessThan(sendBox!.y + sendBox!.height)
  })
})
