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
  actors: [], behaviors: [], events: [], rules: [], processes: [], scenarios: [], questions: [],
}

const sessionDetail = (id: string, title: string) => ({
  id, title, canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '',
  canvas,
  completeness: { counts: { objects: 1, actors: 0, behaviors: 0, events: 0, rules: 0, processes: 0, scenarios: 0 }, gaps: [] },
  readiness,
  messages: id === 's1' ? [
    {
      id: 'u1', role: 'user', createdAt: '2026-07-12T00:00:00Z',
      content: '请展示工单状态流程', steps: [],
    },
    {
      id: 'm1', role: 'assistant', createdAt: '2026-07-12T00:00:01Z',
      content: '下方是业务示意图：\n\n![业务示意图](/mock-image.svg)',
      steps: [{
        tool: 'show_diagram', arguments: { kind: 'state' }, summary: '展示状态图', durationMs: 1,
        diagram: { kind: 'state', title: '工单状态图', mermaid: diagram, warnings: [] },
      }],
    },
  ] : [],
})

const readiness = {
  ready: false, stage: '阶段0 · 定边界', gatesPassed: 2, gatesTotal: 10,
  blockingCount: 2, advisoryCount: 0, openQuestions: { blocking: 0, advisory: 0 },
  gates: [],
}

const requirementsDocument = {
  id: 'doc-1', sessionId: 's1', title: '工单业务 · 需求文档 v2', version: 2,
  createdAt: '2026-07-12T00:00:00Z',
  contentMd: '# 工单业务 · 需求文档 v2\n\n## 背景与目标\n\n梳理工单处理流程。',
}

const htmlAttachment = {
  id: 'html-1', sessionId: 's1', filename: 'anthropic_timeline.html',
  relativePath: 'anthropic_timeline.html', mimeType: 'text/html', fileSize: 512,
  charCount: 512, sha256: 'html-sha', version: 2, source: 'agent', editable: true,
  status: 'ready', error: null, createdAt: '2026-07-12T00:00:00Z', updatedAt: '2026-07-12T00:00:00Z',
}

const htmlContent = `<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>Anthropic 时间线</title></head>
  <body>
    <main><h1>Anthropic 全景时间线</h1><p id="rendered">等待脚本渲染</p></main>
    <script>document.getElementById('rendered').textContent = 'HTML 脚本已执行'</script>
  </body>
</html>`

const textAttachment = {
  ...htmlAttachment,
  id: 'text-1', filename: 'brief.md', relativePath: 'notes/brief.md', mimeType: 'text/markdown',
  fileSize: 128, charCount: 48, sha256: 'text-sha', version: 1, source: 'agent',
  createdAt: '2026-07-11T02:00:00Z', updatedAt: '2026-07-11T02:00:00Z',
}

const archivedTextAttachment = {
  ...textAttachment,
  id: 'text-2', filename: 'old.md', relativePath: 'notes/archive/old.md', sha256: 'old-text-sha',
  createdAt: '2026-07-11T01:00:00Z', updatedAt: '2026-07-11T01:00:00Z',
}

const binaryAttachment = {
  ...htmlAttachment,
  id: 'pdf-1', filename: 'reference.pdf', relativePath: 'uploads/reference.pdf', mimeType: 'application/pdf',
  fileSize: 2048, charCount: 36, sha256: 'pdf-sha', version: 1, source: 'upload', editable: false,
  createdAt: '2026-07-11T03:00:00Z', updatedAt: '2026-07-11T03:00:00Z',
}

async function mockExplore(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    const originalExecCommand = Document.prototype.execCommand
    Document.prototype.execCommand = function execCommand(commandId: string) {
      if (commandId.toLowerCase() === 'copy') {
        const active = document.activeElement
        const copied = active instanceof HTMLTextAreaElement
          ? active.value.slice(active.selectionStart, active.selectionEnd)
          : window.getSelection()?.toString() || ''
        sessionStorage.setItem('last-copied-text', copied)
        return Boolean(copied)
      }
      return originalExecCommand?.call(this, commandId) ?? false
    }
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
      return ok([
        { id: 's1', title: '图表交互测试', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' },
        { id: 's2', title: '第二个业务会话', canvasVersion: 1, status: 'active', createdAt: '', updatedAt: '' },
      ])
    }
    if (path === '/api/v2/exploration/sessions/s1') {
      return ok(sessionDetail('s1', '图表交互测试'))
    }
    if (path === '/api/v2/exploration/sessions/s2') return ok(sessionDetail('s2', '第二个业务会话'))
    if (path === '/api/v2/exploration/sessions/s1/attachments') {
      return ok([htmlAttachment, textAttachment, archivedTextAttachment, binaryAttachment])
    }
    if (path === '/api/v2/exploration/sessions/s1/documents') {
      return ok([requirementsDocument])
    }
    if (path === '/api/v2/exploration/documents/doc-1') {
      return ok(requirementsDocument)
    }
    if (path === '/api/v2/exploration/sessions/s1/attachments/html-1/preview') {
      return ok({
        id: htmlAttachment.id,
        relativePath: htmlAttachment.relativePath,
        content: htmlContent,
        version: htmlAttachment.version,
        mimeType: htmlAttachment.mimeType,
        editable: true,
        truncated: false,
      })
    }
    if (path === '/api/v2/exploration/sessions/s1/attachments/text-1/preview') {
      return ok({
        id: textAttachment.id,
        relativePath: textAttachment.relativePath,
        content: '# 业务简报\n\n这里是可复制的文本内容。',
        version: textAttachment.version,
        mimeType: textAttachment.mimeType,
        editable: true,
        truncated: false,
      })
    }
    if (path === '/api/v2/exploration/sessions/s1/attachments/pdf-1/preview') {
      return ok({
        id: binaryAttachment.id,
        relativePath: binaryAttachment.relativePath,
        content: '这是从 PDF 附件抽取的只读文本。',
        version: binaryAttachment.version,
        mimeType: binaryAttachment.mimeType,
        editable: false,
        truncated: false,
      })
    }
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
    await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
    await expect(page.getByTestId('diagram-thumbnail')).toBeVisible({ timeout: 10_000 })
  })

  test('HTTP 环境下消息和图表源码可降级复制，全局提示固定在右下角', async ({ page }) => {
    const toastViewport = page.locator('[aria-live="polite"][aria-atomic="false"]')
    await expect(toastViewport).toHaveClass(/bottom-5/)
    await expect(toastViewport).not.toHaveClass(/top-20/)

    const messageCopy = page.getByRole('button', { name: '复制用户消息' })
    await messageCopy.click()
    await expect(messageCopy).toContainText('已复制')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('last-copied-text')))
      .toBe('请展示工单状态流程')

    await page.getByRole('button', { name: '图示', exact: true }).click()
    await page.getByRole('button', { name: '状态图', exact: true }).click()
    await page.getByRole('button', { name: '复制源码', exact: true }).click()
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('last-copied-text')))
      .toBe(diagram)
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
    await page.getByRole('button', { name: '图示', exact: true }).click()
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

  test('会话、输入与业务场景使用无渐变的本体拓扑画布背景', async ({ page }) => {
    const chat = page.getByTestId('exploration-chat-region')
    const region = page.getByTestId('exploration-composer-region')
    const shell = page.getByTestId('exploration-composer-shell')
    const scenario = page.getByTestId('business-scenario-region')

    const chatBackground = await chat.evaluate(element => getComputedStyle(element).backgroundColor)
    const regionBackground = await region.evaluate(element => getComputedStyle(element).backgroundColor)
    const shellBackground = await shell.evaluate(element => getComputedStyle(element).backgroundColor)
    const scenarioBackground = await scenario.evaluate(element => getComputedStyle(element).backgroundColor)
    expect(chatBackground).toBe('rgb(248, 251, 255)')
    expect(regionBackground).toBe(shellBackground)
    expect(regionBackground).toBe(chatBackground)
    expect(scenarioBackground).toBe(chatBackground)
    await expect(chat).toHaveCSS('background-image', 'none')
    await expect(region).toHaveCSS('background-image', 'none')
    await expect(shell).toHaveCSS('background-image', 'none')
    await expect(scenario).toHaveCSS('background-image', 'none')
    await expect(shell).toHaveCSS('border-color', 'rgb(20, 184, 166)')
  })

  test('模型分组进入和切换会话后默认折叠，可由用户自行展开', async ({ page }) => {
    const objectSection = page.getByRole('button', { name: /对象模型/ })
    const objectCard = page.getByTitle('查看详情')
    await expect(objectSection).toHaveAttribute('aria-expanded', 'false')
    await expect(objectCard).toBeHidden()

    await objectSection.click()
    await expect(objectSection).toHaveAttribute('aria-expanded', 'true')
    await expect(objectCard).toBeVisible()

    await page.getByRole('button', { name: '查看历史会话' }).click()
    await page.getByRole('button', { name: /^第二个业务会话/ }).click()
    await expect(objectSection).toHaveAttribute('aria-expanded', 'false')
    await expect(objectCard).toBeHidden()
  })

  test('文件清单中的 HTML 使用隔离网页预览并保留源码编辑', async ({ page }) => {
    await page.getByRole('button', { name: '查看会话文件' }).click()

    const iframe = page.getByTestId('html-file-preview')
    await expect(iframe).toBeVisible()
    await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts')
    await expect(iframe).not.toHaveAttribute('sandbox', /allow-same-origin/)

    const htmlFrame = page.frameLocator('[data-testid="html-file-preview"]')
    await expect(htmlFrame.getByRole('heading', { name: 'Anthropic 全景时间线' })).toBeVisible()
    await expect(htmlFrame.getByText('HTML 脚本已执行')).toBeVisible()
    await expect(page.getByText('<!doctype html>', { exact: false })).toBeHidden()

    const indicator = page.getByTestId('workspace-view-mode-indicator')
    const modeSwitch = page.getByTestId('workspace-view-mode-switch')
    const previewButton = page.getByRole('button', { name: '预览', exact: true })
    const editButton = page.getByRole('button', { name: '编辑', exact: true })
    const indicatorBefore = await indicator.boundingBox()
    const modeSwitchBefore = await modeSwitch.boundingBox()
    await expect(previewButton).toHaveAttribute('aria-pressed', 'true')
    await editButton.click()
    await expect(editButton).toHaveAttribute('aria-pressed', 'true')
    await expect.poll(async () => {
      const indicatorBox = await indicator.boundingBox()
      const switchBox = await modeSwitch.boundingBox()
      return (indicatorBox?.x || 0) - (switchBox?.x || 0)
    }).toBeGreaterThan((indicatorBefore?.x || 0) - (modeSwitchBefore?.x || 0) + 20)
    const saveButton = page.getByRole('button', { name: '保存', exact: true })
    const copyButton = page.getByRole('button', { name: '复制文件内容' })
    await expect(saveButton).toBeVisible()
    await expect(copyButton).toHaveClass(/border-/)
    const saveBox = await saveButton.boundingBox()
    const copyBox = await copyButton.boundingBox()
    expect(saveBox?.x).toBeLessThan(copyBox?.x || 0)
    await expect(page.getByLabel('文件内容编辑器')).toContainText('Anthropic 全景时间线')
  })

  test('文件树默认折叠并显示直属数量，复制仅对文本文件开放', async ({ page }) => {
    await page.getByRole('button', { name: '查看会话文件' }).click()

    const notesDirectory = page.getByRole('button', { name: 'notes，下一级 2 项' })
    const uploadsDirectory = page.getByRole('button', { name: 'uploads，下一级 1 项' })
    await expect(notesDirectory).toHaveAttribute('aria-expanded', 'false')
    await expect(uploadsDirectory).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('button', { name: /brief\.md/ })).toBeHidden()

    await page.getByRole('button', { name: '展开全部文件夹' }).click()
    await expect(notesDirectory).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('button', { name: 'archive，下一级 1 项' })).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('button', { name: /old\.md/ })).toBeVisible()

    await page.getByRole('button', { name: '折叠全部文件夹' }).click()
    await expect(notesDirectory).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('button', { name: /brief\.md/ })).toBeHidden()

    await notesDirectory.click()
    await page.getByRole('button', { name: /brief\.md/ }).click()
    await expect(page.getByRole('button', { name: '复制文件内容' })).toBeVisible()
    await page.getByRole('button', { name: '复制文件内容' }).click()
    await expect(page.getByRole('button', { name: '已复制' })).toBeVisible()
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('last-copied-text')))
      .toContain('可复制的文本内容')

    await uploadsDirectory.click()
    await page.getByRole('button', { name: /reference\.pdf/ }).click()
    await expect(page.getByRole('button', { name: '复制文件内容' })).toBeHidden()
    await expect(page.getByTitle('删除文件')).toHaveClass(/bg-rose-50\/80/)
  })

  test('文件清单只能通过右上角关闭按钮关闭', async ({ page }) => {
    await page.getByRole('button', { name: '查看会话文件' }).click()
    const drawer = page.getByTestId('workspace-drawer')
    await expect(drawer).toBeVisible()

    await page.getByTestId('workspace-dialog-backdrop').click({ position: { x: 2, y: 2 } })
    await expect(drawer).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(drawer).toBeVisible()

    await page.getByRole('button', { name: '关闭文件清单' }).click()
    await expect(drawer).toBeHidden()
  })

  test('四个工作区入口展示明确标签并使用不同状态色', async ({ page }) => {
    const fileButton = page.getByTestId('workspace-files-button')
    const historyButton = page.getByTestId('session-history-button')
    const flowButton = page.getByTestId('business-flow-button')
    const documentButton = page.getByTestId('requirements-document-button')

    await expect(fileButton).toContainText('会话文件')
    await expect(historyButton).toContainText('历史会话')
    await expect(flowButton).toContainText('图示')
    await expect(documentButton).toContainText('需求文档')

    const colors = await Promise.all([fileButton, historyButton, flowButton, documentButton]
      .map(button => button.evaluate(element => getComputedStyle(element).color)))
    expect(new Set(colors).size).toBe(4)
  })

  test('用户消息和助手回复均可一键复制，聊天区域滚动条隐藏', async ({ page }) => {
    const userCopy = page.getByRole('button', { name: '复制用户消息' })
    const assistantCopy = page.getByRole('button', { name: '复制助手回复' })

    await userCopy.click()
    await expect(userCopy).toContainText('已复制')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('last-copied-text')))
      .toBe('请展示工单状态流程')

    await assistantCopy.click()
    await expect(assistantCopy).toContainText('已复制')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('last-copied-text')))
      .toContain('下方是业务示意图')

    await expect(page.getByTestId('exploration-chat-region')).toHaveCSS('scrollbar-width', 'none')
  })

  test('需求文档历史版本与详情标题栏的分割线保持水平对齐', async ({ page }) => {
    await page.getByRole('button', { name: '查看需求文档' }).click()
    await expect(page.getByRole('dialog', { name: '需求文档' })).toBeVisible()

    const [historyHeader, detailHeader] = await Promise.all([
      page.getByTestId('requirements-history-header').boundingBox(),
      page.getByTestId('requirements-detail-header').boundingBox(),
    ])
    expect(historyHeader).not.toBeNull()
    expect(detailHeader).not.toBeNull()
    expect(Math.abs(
      historyHeader!.y + historyHeader!.height - (detailHeader!.y + detailHeader!.height),
    )).toBeLessThan(1)
  })
})
