import { expect, test, type Page, type Route } from '@playwright/test'

const ontologyId = 'ontology-trial-plan-review'

const ontology = {
  id: ontologyId,
  name: '供应链试跑审查本体',
  domain: '供应链',
  description: '验证试跑动作计划的用户审查体验',
  version: 'v1',
  current_release_id: 'release-v1',
  current_release_version: 'v1',
  status: 'published',
  entity_count: 1,
  relation_count: 0,
  action_count: 1,
  sentinel_count: 2,
  created_by: 'tester',
  created_at: '2026-07-28T00:00:00Z',
  updated_at: '2026-07-28T00:00:00Z',
}

const versions = {
  current_release_id: 'release-v1',
  current_release_number: 'v1',
  current_release_version: 'v1',
  versions: [
    {
      id: 'release-v1',
      version_number: 'v1',
      version_label: '当前发布',
      parent_version_id: null,
      node_kind: 'release',
      lifecycle_status: 'released',
      revision: 1,
      created_at: '2026-07-27T00:00:00Z',
    },
    {
      id: 'draft-v1-1',
      version_number: 'v1.1',
      version_label: '动作审查候选',
      parent_version_id: 'release-v1',
      base_release_id: 'release-v1',
      node_kind: 'draft',
      lifecycle_status: 'editing',
      revision: 2,
      created_at: '2026-07-28T00:00:00Z',
    },
  ],
}

const overview = {
  release: { id: 'release-v1', version: 'v1', publishedAt: '2026-07-27T00:00:00Z' },
  model: {
    objectTypes: 1,
    linkTypes: 0,
    actions: 1,
    actionsRequiringApproval: 1,
    functions: 0,
    sentinels: { total: 2, enabled: 2, muted: 0 },
  },
  data: {
    instances: 1,
    instancesBySource: { pipeline: 1 },
    linkInstances: 0,
    mappings: { total: 1, bound: 1, nameMatch: 0, autoCreate: 0, autoApply: 0 },
    topTypes: [],
  },
  runtime: {
    pendingApprovals: 0,
    decisions: { total: 0, approved: 0, rejected: 0, recentApprovalRate: null },
    firings7d: { total: 0, fired: 0, error: 0 },
    actionRuns7d: { total: 0, success: 0, failed: 0 },
    daily7d: [],
  },
  facts: { total: 0, byKind: {} },
}

function fillerSample(index: number) {
  return {
    actionId: `action-${index}`,
    actionName: `折叠动作 ${index}`,
    edge: 'enter',
    targetInstanceId: `order-${index}`,
    match: { order: `order-${index}` },
    parameters: { threshold: index },
    status: 'preview',
    effects: [{
      type: 'update_property',
      property: 'status',
      oldValue: 'pending',
      newValue: 'review',
      status: 'preview',
      committed: false,
    }],
    validationErrors: [],
    sideEffects: 'none',
  }
}

const richTrialResult = {
  counts: { objects: 1, links: 0, facts: 2, datasets: 1 },
  actionsExecuted: 0,
  sideEffects: 'blocked',
  warnings: [{
    message: 'Webhook URL https://private.example/warning 使用 apiKey=warning-secret-999',
  }],
  sentinels: [
    {
      id: 'sentinel-risk',
      name: '高风险订单哨兵',
      activation: 'active',
      matched: 27,
      candidateCount: 27,
      candidateCapReached: true,
      errors: [],
      plannedActions: 27,
      plannedActionSamples: [
        {
          actionId: 'action-notify',
          actionName: '通知并回调',
          edge: 'enter',
          targetInstanceId: 'order-sensitive-1',
          match: { order: 'order-sensitive-1' },
          parameters: {
            threshold: 80,
            apiKey: 'param-secret-123',
            nested: {
              authorization: 'Bearer secret-token-123456789',
              ownerEmail: 'owner@example.com',
              callbackUrl: 'https://private.example/hooks/order',
            },
          },
          status: 'invalid',
          effects: [
            {
              type: 'notification',
              channel: 'internal',
              recipient: 'owner@example.com',
              message: '订单 owner@example.com 需要处理',
              description: '向 owner@example.com 发送风险提醒',
              status: 'preview',
              committed: false,
            },
            {
              type: 'webhook',
              method: 'POST',
              url: 'https://private.example/hooks/order',
              headers: { Authorization: 'Bearer webhook-secret-123456789' },
              body: { apiToken: 'body-secret-123' },
              targetValidation: 'syntax_only_dns_deferred',
              description: '调用合作方回调',
              status: 'preview',
              committed: false,
            },
            {
              type: 'update_property',
              property: 'owner_email',
              oldValue: 'old-owner@example.com',
              newValue: 'new-owner@example.com',
              description: '更新责任人邮箱',
              status: 'preview',
              committed: false,
            },
          ],
          validationErrors: [
            'Webhook https://private.example/hooks/order 对 owner@example.com 校验失败',
          ],
          errorMessage: null,
          sideEffects: 'none',
        },
        ...Array.from({ length: 26 }, (_, index) => fillerSample(index + 2)),
      ],
      plannedActionsTruncated: true,
      sideEffects: 'none',
    },
    {
      id: 'sentinel-legacy',
      name: '旧版库存哨兵',
      activation: 'active',
      matched: 2,
      plannedActions: 2,
      errors: [],
      sideEffects: 'none',
    },
  ],
}

async function mockTrialReview(page: Page, trialResult: Record<string, unknown>) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'tester', username: 'tester', role: 'admin' },
      },
      version: 0,
    }))
  })

  const ok = (route: Route, data: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data }),
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async (route: Route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname

    if (path === `/api/v1/ontologies/${ontologyId}`) return ok(route, ontology)
    if (path === `/api/v2/ontologies/${ontologyId}/version-tree`) return ok(route, versions)
    if (
      path === `/api/v2/ontologies/${ontologyId}/versions/draft-v1-1/trial-runs`
      && request.method() === 'POST'
    ) {
      return ok(route, {
        id: 'trial-review-1',
        status: 'passed',
        result: trialResult,
        impact_hash: 'impact-review-1',
        created_at: '2026-07-28T01:00:00Z',
      })
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/overview`) return ok(route, overview)
    if (path === `/api/v2/formal/ontologies/${ontologyId}/facts/recent`) return ok(route, [])
    return ok(route, [])
  })
}

async function openTrialResult(page: Page) {
  await page.goto(`/#/ontologies/${ontologyId}?tab=versions`, { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('version-tree')).toBeVisible()
  await page.getByRole('button', { name: '转为试跑态' }).click()
  const dialog = page.getByRole('dialog', { name: '隔离试跑结果' })
  await expect(dialog).toBeVisible()
  return dialog
}

test('按哨兵、动作和目标审查计划，且通知与 Webhook 敏感值不进入页面', async ({ page }) => {
  await mockTrialReview(page, richTrialResult)
  const dialog = await openTrialResult(page)

  await expect(dialog.getByText('仅预览 · 无副作用', { exact: true })).toBeVisible()
  await expect(dialog.getByText(/不会调用 Webhook、不会投递通知/)).toBeVisible()
  await expect(dialog.getByText('外部动作执行数：0')).toBeVisible()
  await expect(dialog.getByText('副作用策略：已阻断')).toBeVisible()
  await expect(dialog.getByLabel('动作计划摘要')).toContainText('29计划动作')
  await expect(dialog.getByText(/每个哨兵最多展示 25 个动作样本/)).toBeVisible()
  await expect(dialog.getByText('折叠动作 27')).toHaveCount(0)

  await dialog.locator('summary').filter({ hasText: '通知并回调' }).click()
  await expect(dialog.getByText('安全结果：未投递通知')).toBeVisible()
  await expect(dialog.getByText('安全结果：未建立网络连接')).toBeVisible()
  await expect(dialog.getByText('目标地址、请求体与请求头：已隐藏')).toBeVisible()
  await expect(dialog.getByText('仅语法；DNS 已延后')).toBeVisible()
  await expect(dialog.getByText('••••••（已隐藏）').first()).toBeVisible()
  await expect(dialog.getByText(/\[地址已隐藏\].*\[邮箱已隐藏\]/)).toBeVisible()

  await expect(dialog).not.toContainText('param-secret-123')
  await expect(dialog).not.toContainText('owner@example.com')
  await expect(dialog).not.toContainText('private.example')
  await expect(dialog).not.toContainText('webhook-secret-123456789')
  await expect(dialog).not.toContainText('body-secret-123')
  await expect(dialog).not.toContainText('warning-secret-999')

  await dialog.locator('summary').filter({ hasText: '旧版库存哨兵' }).click()
  await expect(dialog.getByText(/只报告了 2 个计划动作.*兼容旧版本/)).toBeVisible()
})

test('旧试跑缺少 sentinels 字段时给出兼容提示且仍保留安全结论', async ({ page }) => {
  await mockTrialReview(page, {
    counts: { objects: 1, links: 0, facts: 0, datasets: 1 },
    actionsExecuted: 0,
  })
  const dialog = await openTrialResult(page)

  await expect(dialog.getByText('仅预览 · 无副作用', { exact: true })).toBeVisible()
  await expect(dialog.getByText(/未包含哨兵动作计划明细（兼容旧版本）/)).toBeVisible()
  await expect(dialog.getByText('副作用策略：按隔离试跑策略阻断')).toBeVisible()
})

test('试跑显式未确认阻断副作用时按异常展示而不误报安全', async ({ page }) => {
  await mockTrialReview(page, {
    counts: { objects: 1, links: 0, facts: 0, datasets: 1 },
    actionsExecuted: 0,
    sideEffects: 'none',
    sentinels: [],
  })
  const dialog = await openTrialResult(page)

  await expect(dialog.getByText('试跑响应存在副作用异常', { exact: true })).toBeVisible()
  await expect(dialog.getByText(/响应没有确认副作用已被阻断/)).toBeVisible()
  await expect(dialog.getByText('副作用策略：异常：响应未确认阻断')).toBeVisible()
  await expect(dialog.getByText('仅预览 · 无副作用', { exact: true })).toHaveCount(0)
})
