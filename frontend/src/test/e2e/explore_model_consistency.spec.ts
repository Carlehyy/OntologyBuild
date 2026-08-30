import { expect, test, type Page, type Route } from '@playwright/test'

const ontologyId = 'ont-1'
const draftVersionId = 'ver-1'
const sessionId = 's1'

const emptyCanvas = {
  objects: [{ id: 'obj-order', name: 'Order', display_name: '订单', attributes: [], relations: [] }],
  actors: [], behaviors: [], events: [], rules: [], processes: [], scenarios: [], questions: [],
}

const readiness = {
  ready: true,
  stage: '已就绪 · 全部质量门通过，可生成需求文档与本体草稿',
  gatesPassed: 10,
  gatesTotal: 10,
  blockingCount: 0,
  advisoryCount: 1,
  openQuestions: { blocking: 0, advisory: 0 },
  gates: [],
}

const semanticIssues = [
  {
    code: 'semantic_business_missing',
    kind: 'objectType',
    id: 'Order',
    name: '订单',
    message: '结构中的对象「订单」在业务画布中没有对应对象/主体',
  },
  {
    code: 'semantic_signature_mismatch',
    kind: 'action',
    id: 'Approve',
    name: '审批',
    message: '动作「审批」与画布行为同名但签名不一致',
  },
  {
    code: 'semantic_structure_missing',
    kind: 'object',
    id: 'canvas-order-item',
    name: '订单明细',
    message: '画布中的对象「订单明细」尚未落地到本体结构',
  },
  {
    code: 'semantic_document_stale',
    kind: 'document',
    id: 'document',
    name: '需求文档',
    message: '需求文档生成后画布已发生变化',
  },
]

const semanticResponse = {
  semantic: { documentTitle: '订单业务需求文档', documentMd: '# 订单业务需求文档' },
  overview: {
    hasSemanticLayer: true,
    documentTitle: '订单业务需求文档',
    documentStale: true,
    canvasCounts: { objects: 1, actors: 0, behaviors: 0, events: 0, rules: 0, scenarios: 0, processes: 0 },
    structureCounts: { objectTypes: 1, linkTypes: 0, actions: 1, functions: 0, sentinels: 0 },
    consistency: {
      issueCount: semanticIssues.length,
      byCode: {
        semantic_business_missing: 1,
        semantic_signature_mismatch: 1,
        semantic_structure_missing: 1,
        semantic_document_stale: 1,
      },
    },
  },
  issues: semanticIssues,
}

const preflightResponse = {
  ok: true,
  versionId: draftVersionId,
  revision: 'r3',
  checks: [
    { id: 'editable_draft', label: '草稿可编辑', status: 'pass', errors: [] },
    { id: 'single_flight', label: '单飞控制', status: 'pass', errors: [] },
    { id: 'base_up_to_date', label: '基线最新', status: 'pass', errors: [] },
    { id: 'structure', label: '结构完整', status: 'pass', errors: [] },
    { id: 'mapping_contract', label: '映射契约', status: 'pass', errors: [] },
    { id: 'semantic_consistency', label: '语义一致性', status: 'pass', errors: [] },
  ],
}

const trialGateDetail = {
  code: 'publish_validation_failed',
  message: '本体试跑门禁未通过（2 个错误）',
  errors: [
    {
      code: 'semantic_business_missing',
      kind: 'objectType',
      id: 'Order',
      name: '订单',
      message: '结构中的对象「订单」在业务画布中没有对应对象/主体',
    },
    {
      code: 'trial_object_mapping_required',
      kind: 'mapping',
      id: 'm-1',
      name: '订单映射',
      message: '对象「订单」缺少数据映射，无法创建隔离试跑数据集',
    },
  ],
}

const draftNode = (lifecycle: string) => ({
  id: draftVersionId,
  version_number: 'v1.1',
  version_label: '在线配置草稿',
  parent_version_id: 'rel-1',
  base_release_id: 'rel-1',
  node_kind: 'draft',
  lifecycle_status: lifecycle,
  revision: 3,
  created_at: '2026-08-29T00:00:00Z',
})

const releaseNode = {
  id: 'rel-1',
  version_number: 'v1',
  version_label: '当前发布',
  parent_version_id: null,
  node_kind: 'release',
  lifecycle_status: 'released',
  revision: 1,
  created_at: '2026-08-20T00:00:00Z',
}

const workspacePayload = {
  id: ontologyId,
  name: '订单本体',
  description: '',
  version: 'v1.1',
  revision: 'r3',
  workspaceMode: 'draft',
  objectTypes: [],
  linkTypes: [],
  actions: [],
  functions: [],
  instances: [],
  linkInstances: [],
  executionLogs: [],
}

const ok = (route: Route, data: unknown, status = 200) => route.fulfill({
  status,
  contentType: 'application/json',
  body: JSON.stringify({ data, message: 'ok' }),
})

interface MockState {
  trialStarted: boolean
  trialFails422: boolean
  versionTreeReads: number
  workspaceReads: number
  chatMessages: string[]
}

async function mockExploreModel(page: Page, state: MockState) {
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
        id: sessionId,
        title: '订单探索',
        canvasVersion: 2,
        status: 'active',
        createdAt: '2026-08-29T00:00:00Z',
        updatedAt: '2026-08-29T00:00:00Z',
        ontologyId,
        ontologyVersionId: draftVersionId,
      }])
    }
    if (path === `/api/v2/exploration/sessions/${sessionId}` && request.method() === 'GET') {
      return ok(route, {
        id: sessionId,
        title: '订单探索',
        canvasVersion: 2,
        status: 'active',
        createdAt: '2026-08-29T00:00:00Z',
        updatedAt: '2026-08-29T00:00:00Z',
        ontologyId,
        ontologyVersionId: draftVersionId,
        canvas: emptyCanvas,
        completeness: {
          counts: { objects: 1, actors: 0, behaviors: 0, events: 0, rules: 0, processes: 0, scenarios: 0 },
          gaps: [],
        },
        readiness,
        messages: [],
      })
    }
    if (path === `/api/v2/exploration/sessions/${sessionId}/attachments`) return ok(route, [])
    if (path === `/api/v2/exploration/sessions/${sessionId}/chat` && request.method() === 'POST') {
      state.chatMessages.push((request.postDataJSON() as { message?: string }).message || '')
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          `data: {"type":"meta","sessionId":"${sessionId}","model":"mock-model"}`,
          '',
          'data: {"type":"answer","content":"已复述理解并同步画布"}',
          '',
          'data: {"type":"done"}',
          '',
          '',
        ].join('\n'),
      })
    }
    if (path === `/api/v2/ontologies/${ontologyId}` && request.method() === 'GET') {
      return ok(route, { id: ontologyId, name: '订单本体' })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/version-tree` && request.method() === 'GET') {
      state.versionTreeReads += 1
      return ok(route, {
        current_release_id: 'rel-1',
        current_release_number: 'v1',
        current_release_version: 'v1',
        versions: [releaseNode, draftNode(state.trialStarted ? 'trial_ready' : 'editing')],
      })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/versions/${draftVersionId}/semantic`) {
      return ok(route, semanticResponse)
    }
    if (path === `/api/v2/ontologies/${ontologyId}/versions/${draftVersionId}/workspace`) {
      state.workspaceReads += 1
      return ok(route, workspacePayload)
    }
    if (
      path === `/api/v2/ontologies/${ontologyId}/versions/${draftVersionId}/trial-preflight`
      && request.method() === 'POST'
    ) {
      return ok(route, preflightResponse)
    }
    if (
      path === `/api/v2/ontologies/${ontologyId}/versions/${draftVersionId}/trial-runs`
      && request.method() === 'POST'
    ) {
      if (state.trialFails422) {
        return route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({ detail: trialGateDetail }),
        })
      }
      state.trialStarted = true
      return ok(route, {
        id: 'trial-1',
        status: 'passed',
        result: {
          counts: { objects: 3, links: 1, facts: 0, datasets: 1 },
          errors: [],
          warnings: [],
          sentinels: [],
          actionsExecuted: 0,
          sideEffects: 'blocked',
        },
        impact_hash: 'hash-1',
        created_at: '2026-08-30T00:00:00Z',
      }, 201)
    }
    return ok(route, [])
  })

  await page.goto('/#/explore', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('session-binding-badge')).toBeVisible()
}

const newState = (overrides: Partial<MockState> = {}): MockState => ({
  trialStarted: false,
  trialFails422: false,
  versionTreeReads: 0,
  workspaceReads: 0,
  chatMessages: [],
  ...overrides,
})

test.describe('业务澄清 · 本体模型一致性面板与试跑预检', () => {
  test('模型视图展示漂移角标与一致性面板，文档/结构提示可跳转需求文档视图', async ({ page }) => {
    await mockExploreModel(page, newState())

    // 「本体模型」标签页角标在切视图前已可见（与会话绑定同步出现）
    const badge = page.getByTestId('explore-model-drift-badge')
    await expect(badge).toHaveText('4')

    await page.getByTestId('explore-view-model').click()
    await expect(page.getByTestId('explore-view-model')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByTestId('consistency-status-badge')).toHaveText('4 项漂移')

    await page.getByTestId('consistency-panel-toggle').click()
    await expect(page.getByTestId('consistency-group-semantic_business_missing')).toContainText('结构缺业务语义')
    await expect(page.getByTestId('consistency-group-semantic_structure_missing')).toContainText('画布模型未落地')
    await expect(page.getByTestId('consistency-group-semantic_signature_mismatch')).toContainText('签名不一致')
    await expect(page.getByTestId('consistency-group-semantic_document_stale')).toContainText('文档/画布已变更')
    await expect(page.getByTestId('consistency-group-semantic_business_missing'))
      .toContainText('结构中的对象「订单」在业务画布中没有对应对象/主体')

    // 结构缺失 → 引导回需求文档视图重新「生成本体模型」
    await page.getByTestId('regenerate-model-hint').click()
    await expect(page.getByTestId('explore-view-docs')).toHaveAttribute('aria-pressed', 'true')
  })

  test('回译到业务语义把受影响元素拼装为一条对话消息发出', async ({ page }) => {
    const state = newState()
    await mockExploreModel(page, state)

    await page.getByTestId('explore-view-model').click()
    await page.getByTestId('consistency-panel-toggle').click()
    await page.getByTestId('back-translate-button').click()

    await expect.poll(() => state.chatMessages.length).toBe(1)
    const message = state.chatMessages[0]
    expect(message).toContain('我在本体模型中做了人工修改')
    expect(message).toContain('对象类型「订单」、动作「审批」')
    expect(message).toContain('请先用业务语言复述你的理解，再更新画布。')
    // 结构缺失/文档过期不回译，只提示到需求文档视图补齐
    expect(message).not.toContain('订单明细')
    expect(message).not.toContain('需求文档「需求文档」')
    await expect(page.getByText(/我在本体模型中做了人工修改/)).toBeVisible()
    await expect(page.getByText('已复述理解并同步画布', { exact: true })).toBeVisible()
  })

  test('预检弹窗列出权威门禁，确认后发起试跑并展示试跑结果', async ({ page }) => {
    const state = newState()
    await mockExploreModel(page, state)

    await page.getByTestId('explore-view-model').click()
    await page.getByTestId('explore-trial-entry').click()

    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('权威门禁', { exact: true })).toBeVisible()
    await expect(page.getByTestId('trial-preflight-check-editable_draft')).toContainText('草稿可编辑')
    await expect(page.getByTestId('trial-preflight-check-semantic_consistency')).toContainText('语义一致性')
    await expect(page.getByTestId('trial-preflight-checks').getByTestId(/trial-preflight-check-/)).toHaveCount(6)
    // 业务语义质量仅参考：展示会话质量门快照
    await expect(page.getByTestId('trial-preflight-readiness')).toContainText('质量门 10/10')
    await expect(page.getByTestId('trial-preflight-readiness')).toContainText('建议 1 项')

    const treeReadsBefore = state.versionTreeReads
    await page.getByTestId('trial-confirm-button').click()
    await expect.poll(() => state.trialStarted).toBe(true)

    // 试跑结果原地展示：动作计划审查 + 无副作用结论
    await expect(page.getByTestId('trial-run-result')).toContainText('已进入试跑态')
    await expect(dialog.getByText('仅预览 · 无副作用', { exact: true })).toBeVisible()

    // 版本树失效重取 → 生命周期 trial_ready → 嵌入编辑器按 key 重挂、重新只读加载
    await expect.poll(() => state.versionTreeReads).toBeGreaterThan(treeReadsBefore)
    await expect.poll(() => state.workspaceReads).toBeGreaterThan(1)
  })

  test('试跑门禁 422 在弹窗内以告警样式展示明细，弹窗保持打开', async ({ page }) => {
    const state = newState({ trialFails422: true })
    await mockExploreModel(page, state)

    await page.getByTestId('explore-view-model').click()
    await page.getByTestId('explore-trial-entry').click()
    await page.getByTestId('trial-confirm-button').click()

    const alert = page.getByRole('dialog').getByRole('alert')
    await expect(alert).toContainText('暂时不能进入试跑态：仍有 2 项试跑门禁条件未满足')
    await expect(alert).toContainText('结构中的对象「订单」在业务画布中没有对应对象/主体')
    await expect(alert).toContainText('对象「订单」缺少数据映射，无法创建隔离试跑数据集')
    await expect(page.getByTestId('trial-confirm-button')).toBeVisible()
    await expect(page.getByRole('dialog')).toBeVisible()
  })
})
