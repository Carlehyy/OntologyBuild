import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * 「业务模型」弹窗（MYW-77 ②4）：结构页工具栏入口，展示业务澄清会话
 * 沉淀的七类模型；目录按类别归纳，右侧展示选中模型详情；无模型时空态。
 */
const ontologyId = 'ontology-business-model'

const ok = (route: Route, data: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ data, message: 'ok' }),
})

async function mockStructurePage(page: Page, options: { canvas?: unknown } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'business-model-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'business-model-token',
        user: { id: 'business-model-user', username: 'business-model-user', role: 'admin' },
      },
      version: 0,
    }))
  })

  const emptyCanvas = {
    objects: [], actors: [], behaviors: [], events: [], rules: [], processes: [], scenarios: [],
  }
  const canvas = options.canvas === undefined ? {
    objects: [{
      id: 'obj-order', name: 'order', display_name: '订单',
      description: '核心交易实体',
      key_attribute: 'order_no',
      attributes: [{ name: 'order_no', display_name: '订单号', type_hint: 'string' }],
      relations: [{ name: 'contains', display_name: '包含', target: 'order_item' }],
    }],
    actors: [{
      id: 'actor-ops', name: 'ops', display_name: '运营专员', kind: 'role',
      responsibilities: ['审核订单', '处理退款'],
    }],
    behaviors: [],
    events: [],
    rules: [{
      id: 'rule-amount', name: 'amount_limit', display_name: '金额上限校验',
      kind: 'validation', applies_to: '订单', statement: '订单金额不得超过一百万',
      error_message: '订单金额超限，请拆分提交',
    }],
    processes: [],
    scenarios: [],
  } : options.canvas

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async route => {
    const path = new URL(route.request().url()).pathname

    if (path === `/api/v1/ontologies/${ontologyId}`) {
      return ok(route, {
        id: ontologyId,
        name: '业务模型弹窗测试本体',
        domain: '供应链',
        description: '验证业务模型弹窗',
        version: 'v1',
        current_release_id: 'release-1',
        current_release_version: 'v1',
        status: 'published',
        entity_count: 1,
        relation_count: 0,
        action_count: 0,
        sentinel_count: 0,
        created_by: 'business-model-user',
        created_at: '2026-08-07T00:00:00Z',
        updated_at: '2026-08-07T00:00:00Z',
      })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/current-release/workspace`) {
      return ok(route, {
        version: 'v1',
        versionId: 'release-1',
        workspaceMode: 'release',
        editable: false,
        isCurrentRelease: true,
        objectTypes: [{
          id: 'object-order',
          name: 'Order',
          displayName: '订单',
          primaryKey: 'order_no',
          properties: [{ id: 'order_no', name: 'order_no', displayName: '订单号', type: 'string', required: true }],
        }],
        linkTypes: [],
        actions: [],
        functions: [],
        sentinels: [],
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/agent/dynamic-sentinels`) return ok(route, [])
    if (path === `/api/v2/formal/ontologies/${ontologyId}/overview`) {
      return ok(route, {
        release: { id: 'release-1', version: 'v1', publishedAt: '2026-08-07T00:00:00Z' },
        model: { objectTypes: 1, linkTypes: 0, actions: 0, actionsRequiringApproval: 0, functions: 0, sentinels: { total: 0, enabled: 0, muted: 0 } },
        data: { instances: 0, instancesBySource: {}, linkInstances: 0, mappings: { total: 0, bound: 0, nameMatch: 0, autoCreate: 0, autoApply: 0 }, topTypes: [] },
        runtime: { pendingApprovals: 0, decisions: { total: 0, approved: 0, rejected: 0, recentApprovalRate: null }, firings7d: { total: 0, fired: 0, error: 0 }, actionRuns7d: { success: 0, failed: 0, total: 0 }, daily7d: [] },
        facts: { total: 0, byKind: {} },
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/facts/recent`) return ok(route, [])
    if (path === '/api/v2/exploration/sessions') {
      return ok(route, [
        // 绑定本体的会话按 updatedAt 取最近一支；另一支无关会话不参与
        {
          id: 'session-stale',
          title: '旧会话',
          canvasVersion: 1,
          status: 'active',
          ontologyId,
          ontologyVersionId: 'version-old',
          createdAt: '2026-08-01T00:00:00Z',
          updatedAt: '2026-08-01T00:00:00Z',
        },
        {
          id: 'session-latest',
          title: '订单业务澄清',
          canvasVersion: 3,
          status: 'active',
          ontologyId,
          ontologyVersionId: 'release-1',
          createdAt: '2026-08-02T00:00:00Z',
          updatedAt: '2026-08-06T00:00:00Z',
        },
        {
          id: 'session-other',
          title: '别的本体会话',
          canvasVersion: 1,
          status: 'active',
          ontologyId: 'ontology-other',
          ontologyVersionId: null,
          createdAt: '2026-08-03T00:00:00Z',
          updatedAt: '2026-08-07T00:00:00Z',
        },
      ])
    }
    if (path === '/api/v2/exploration/sessions/session-latest/canvas') {
      return ok(route, {
        canvas,
        version: 3,
        completeness: { counts: {}, gaps: [] },
        readiness: { ready: true, stage: 's0', gatesPassed: 0, gatesTotal: 0, blockingCount: 0, advisoryCount: 0, openQuestions: { blocking: 0, advisory: 0 }, gates: [] },
      })
    }
    if (path === '/api/v2/exploration/sessions/session-stale/canvas') {
      return ok(route, { canvas: emptyCanvas, version: 1, completeness: { counts: {}, gaps: [] }, readiness: { ready: true, stage: 's0', gatesPassed: 0, gatesTotal: 0, blockingCount: 0, advisoryCount: 0, openQuestions: { blocking: 0, advisory: 0 }, gates: [] } })
    }
    if (path === '/api/v2/inbox/summary') {
      return ok(route, { openAlertCount: 0, actionableCount: 0, unreadCount: 0, resolvedCount: 0 })
    }
    return ok(route, [])
  })
}

async function openBusinessModelDialog(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/ontologies/' + ontologyId, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '本体结构', exact: true }).click()
  await expect(page.getByTestId('structure-node-object')).toBeVisible()
  await page.getByTestId('open-business-model-dialog').click()
  await expect(page.getByTestId('business-model-content')).toBeVisible()
}

test('业务模型弹窗按类别归纳目录并联动详情', async ({ page }) => {
  await mockStructurePage(page)
  await openBusinessModelDialog(page)

  // 目录只列出有模型的类别，按类别分组
  const catalog = page.getByTestId('business-model-catalog')
  await expect(catalog).toContainText('对象模型')
  await expect(catalog).toContainText('主体模型')
  await expect(catalog).toContainText('规则模型')
  await expect(catalog).toContainText('订单')
  await expect(catalog).toContainText('运营专员')
  // 空类别不出现
  await expect(catalog).not.toContainText('事件模型')

  // 默认选中第一类第一个模型：订单（对象模型）
  const detail = page.getByTestId('business-model-detail')
  await expect(detail).toContainText('订单')
  await expect(detail).toContainText('订单号')
  await expect(detail).toContainText('主键')

  // 点击规则模型 → 右侧切换详情
  await catalog.getByRole('button', { name: '金额上限校验' }).click()
  await expect(detail).toContainText('金额上限校验')
  await expect(detail).toContainText('订单金额不得超过一百万')
  await expect(detail).toContainText('不满足时')

  await page.getByLabel('关闭业务模型').click()
  await expect(page.getByTestId('business-model-content')).toHaveCount(0)
})

test('没有业务模型时展示空态', async ({ page }) => {
  await mockStructurePage(page, {
    canvas: {
      objects: [], actors: [], behaviors: [], events: [], rules: [], processes: [], scenarios: [],
    },
  })
  await openBusinessModelDialog(page)

  await expect(page.getByTestId('business-model-empty')).toContainText('当前本体没有关联的业务模型')
  await expect(page.getByTestId('business-model-catalog-item')).toHaveCount(0)
})
