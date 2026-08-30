import { expect, test, type Page, type Route } from '@playwright/test'

const emptyCanvas = {
  objects: [{
    id: 'obj-order',
    name: 'Order',
    display_name: '订单',
    key_attribute: 'order_no',
    attributes: [{ name: 'order_no', display_name: '订单号', type_hint: '文本' }],
    relations: [],
  }],
  actors: [],
  behaviors: [],
  events: [],
  rules: [],
  processes: [],
  scenarios: [],
  questions: [],
}

const ready = {
  ready: true,
  stage: '已就绪 · 全部质量门通过，可生成需求文档与本体草稿',
  gatesPassed: 10,
  gatesTotal: 10,
  blockingCount: 0,
  advisoryCount: 0,
  openQuestions: { blocking: 0, advisory: 0 },
  gates: [],
}

const blockedReadiness = {
  ...ready,
  ready: false,
  stage: '阶段4 · 规则定量：阈值/枚举/边界给到数字',
  gatesPassed: 7,
  blockingCount: 2,
  gates: [{
    id: 'rules',
    label: '规则口径',
    passed: false,
    blockingItems: ['审批阈值缺少金额与币种'],
    advisoryItems: [],
  }],
}

const documentState = (isStale: boolean) => ({
  id: 'doc-1',
  sessionId: 's1',
  title: '订单业务需求文档',
  version: 2,
  sourceCanvasVersion: 2,
  sourceCanvasFingerprint: 'source-fingerprint',
  currentCanvasVersion: isStale ? 3 : 2,
  currentCanvasFingerprint: isStale ? 'current-fingerprint' : 'source-fingerprint',
  isStale,
  createdAt: '2026-07-24T00:00:00Z',
  contentMd: '# 订单业务需求文档\n\n订单创建后由运营审核。',
})

const sourceDocument = (isStale: boolean) => ({
  sourceCanvasVersion: 2,
  sourceCanvasFingerprint: 'source-fingerprint',
  currentCanvasVersion: isStale ? 3 : 2,
  currentCanvasFingerprint: isStale ? 'current-fingerprint' : 'source-fingerprint',
  isStale,
})

const draftResponse = (report: Record<string, unknown>) => ({
  id: 'draft-1',
  sessionId: 's1',
  documentId: 'doc-1',
  targetOntologyId: 'ont-1',
  draft: {
    objectTypes: [],
    linkTypes: [],
    actions: [],
    functions: [],
    sentinels: [],
  },
  report: {
    warnings: [],
    conflicts: [],
    scenarioCoverage: [],
    llmRefined: false,
    ...report,
  },
  status: 'draft',
  appliedOntologyId: null,
  createdAt: '2026-07-24T00:00:00Z',
  updatedAt: '2026-07-24T00:00:00Z',
})

const ok = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data, message: 'ok' }),
})

async function mockExplore(
  page: Page,
  doc: ReturnType<typeof documentState>,
  handleDraft: (route: Route, body: Record<string, unknown>) => Promise<void>,
) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/v2/exploration/sessions' && request.method() === 'GET') {
      return ok(route, [{
        id: 's1',
        title: '订单探索',
        canvasVersion: doc.currentCanvasVersion,
        status: 'active',
        createdAt: '',
        updatedAt: '',
        ontologyId: 'ont-1',
        ontologyVersionId: 'ver-1',
      }])
    }
    if (path === '/api/v2/exploration/sessions/s1' && request.method() === 'GET') {
      return ok(route, {
        id: 's1',
        title: '订单探索',
        canvasVersion: doc.currentCanvasVersion,
        status: 'active',
        createdAt: '',
        updatedAt: '',
        ontologyId: 'ont-1',
        ontologyVersionId: 'ver-1',
        canvas: emptyCanvas,
        completeness: {
          counts: { objects: 1, actors: 0, behaviors: 0, events: 0, rules: 0, processes: 0, scenarios: 0 },
          gaps: [],
        },
        readiness: ready,
        messages: [],
      })
    }
    if (path === '/api/v2/ontologies/ont-1' && request.method() === 'GET') {
      return ok(route, { id: 'ont-1', name: '订单本体' })
    }
    if (path === '/api/v2/ontologies/ont-1/version-tree' && request.method() === 'GET') {
      return ok(route, {
        versions: [{ id: 'ver-1', version_number: 'v0.1' }],
        current_release_id: 'rel-1',
        current_release_version: 'v0',
      })
    }
    if (path === '/api/v2/exploration/sessions/s1/attachments') return ok(route, [])
    if (path === '/api/v2/exploration/sessions/s1/documents' && request.method() === 'GET') {
      return ok(route, [doc])
    }
    if (path === '/api/v2/exploration/documents/doc-1' && request.method() === 'GET') {
      return ok(route, doc)
    }
    if (path === '/api/v2/exploration/documents/doc-1/drafts' && request.method() === 'POST') {
      return handleDraft(route, request.postDataJSON() as Record<string, unknown>)
    }
    return ok(route, [])
  })

  await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
  await page.getByTestId('explore-view-docs').click()
  await expect(page.getByTestId('requirements-view')).toBeVisible()
  await expect(page.getByTestId('explore-view-docs')).toHaveAttribute('aria-pressed', 'true')
}

test.describe('业务探索文档转化风险', () => {
  test('陈旧文档先明确阻断，显式强制后在草稿中保留来源留痕', async ({ page }) => {
    const doc = documentState(true)
    let forced = false
    await mockExplore(page, doc, async (route, body) => {
      if (!body.force) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: {
              code: 'stale_document',
              message: '该需求文档对应的画布已发生变化。',
              source: sourceDocument(true),
            },
          }),
        })
      }
      forced = true
      return ok(route, draftResponse({
        staleDocumentOverride: true,
        sourceDocument: sourceDocument(true),
        warnings: ['⚠️ 使用已过期需求文档的画布快照强制生成'],
      }), 201)
    })

    await expect(page.getByTestId('stale-document-doc-1')).toHaveText('已过期')
    await expect(page.getByTestId('stale-document-notice')).toContainText('当前文档已过期')
    await page.getByRole('button', { name: '生成本体模型', exact: true }).click()
    await expect(page.getByTestId('stale-draft-block')).toContainText('文档快照已过期，默认拒绝生成')
    await expect(page.getByTestId('stale-draft-block')).toContainText('同时越过后续质量门与语义阻断')

    await page.getByRole('button', { name: '仍使用旧快照强制生成' }).click()
    await expect.poll(() => forced).toBe(true)
    await expect(page.getByText('本体草稿审阅')).toBeVisible()
    await expect(page.getByTestId('draft-source-document')).toContainText('旧文档快照越权生成')
    await expect(page.getByTestId('draft-source-document')).toContainText('来源画布 v2 → 当前画布 v3 · 内容已变化')
  })

  test('语义损失逐项展示，强制生成后审阅页结构化呈现保真结论', async ({ page }) => {
    const issue = {
      code: 'object_approval_unsupported',
      severity: 'blocking',
      message: '对象「订单」的审批规则无法无损映射',
      key: 'rule:order_approval',
      sourceRefs: ['rule-order-approval'],
    }
    await mockExplore(page, documentState(false), async (route, body) => {
      if (!body.force) {
        return route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: {
              code: 'semantic_conversion_blocked',
              message: '画布有 1 项语义无法无损转换。',
              semanticIssues: [issue],
            },
          }),
        })
      }
      return ok(route, draftResponse({
        semanticOverride: true,
        semanticIssues: [issue],
        semanticFidelity: { blockingCount: 1, unsupportedCount: 0, readyToApply: false },
        sourceDocument: sourceDocument(false),
        warnings: ['⚠️ 1 项不可无损转换语义被显式越权'],
      }), 201)
    })

    await page.getByRole('button', { name: '生成本体模型', exact: true }).click()
    await expect(page.getByTestId('semantic-draft-block')).toContainText('语义无法无损转换，默认拒绝生成')
    await expect(page.getByTestId('semantic-draft-block')).toContainText('对象「订单」的审批规则无法无损映射')
    await page.getByRole('button', { name: '接受语义损失并强制生成' }).click()

    await expect(page.getByTestId('draft-semantic-fidelity')).toContainText('语义保真越权生成')
    await expect(page.getByTestId('draft-semantic-fidelity')).toContainText('堵门 1 · 暂不支持 0 · 不可无损落地')
    await expect(page.getByTestId('draft-semantic-fidelity')).toContainText('来源 rule-order-approval')
    await expect(page.getByTestId('draft-source-document')).toContainText('内容一致')
  })

  test('原有质量门阻断仍展示门级明细与留痕式越权入口', async ({ page }) => {
    await mockExplore(page, documentState(false), async (route, body) => {
      if (!body.force) {
        return route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: {
              code: 'quality_gate_blocked',
              message: '质量门未通过。',
              readiness: blockedReadiness,
            },
          }),
        })
      }
      return ok(route, draftResponse({
        gateOverride: true,
        readiness: blockedReadiness,
        sourceDocument: sourceDocument(false),
      }), 201)
    })

    await page.getByRole('button', { name: '生成本体模型', exact: true }).click()
    await expect(page.getByText('质量门未通过（7/10 门）', { exact: false })).toBeVisible()
    await expect(page.getByText('[规则口径] 审批阈值缺少金额与币种', { exact: false })).toBeVisible()
    await page.getByRole('button', { name: '已知悉风险，越权生成（留痕）' }).click()
    await expect(page.getByText('质量门越权生成')).toBeVisible()
  })
})
