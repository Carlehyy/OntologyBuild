import { expect, test, type Route } from '@playwright/test'


const ontologyId = 'ontology-runtime-state-conflict'
const release = {
  id: 'release-v2',
  version_number: 'v2',
  version_label: '当前发布',
  parent_version_id: null,
  base_release_id: 'release-v2',
  node_kind: 'release',
  lifecycle_status: 'released',
  revision: 0,
  created_at: '2026-07-28T00:00:00Z',
}
const trialDraft = {
  id: 'draft-v2-1',
  version_number: 'v2.1',
  version_label: '候选发布',
  parent_version_id: release.id,
  base_release_id: release.id,
  node_kind: 'draft',
  lifecycle_status: 'trial_ready',
  revision: 1,
  latest_trial: {
    id: 'trial-v2-1',
    status: 'passed',
    result: { counts: { objects: 2, links: 0, facts: 4, datasets: 1 } },
  },
  created_at: '2026-07-28T01:00:00Z',
}

const overview = {
  release: { id: release.id, version: 'v2', publishedAt: '2026-07-28T00:00:00Z' },
  model: {
    objectTypes: 1,
    linkTypes: 0,
    actions: 1,
    actionsRequiringApproval: 1,
    functions: 0,
    sentinels: { total: 1, enabled: 1, muted: 0 },
  },
  data: {
    instances: 2,
    instancesBySource: { pipeline: 2 },
    linkInstances: 0,
    mappings: { total: 1, bound: 1, nameMatch: 0, autoCreate: 0, autoApply: 0 },
    topTypes: [],
  },
  runtime: {
    pendingApprovals: 0,
    decisions: { total: 0, approved: 0, rejected: 0, recentApprovalRate: null },
    firings7d: { total: 0, fired: 0, error: 0 },
    actionRuns7d: { total: 1, success: 1, failed: 0 },
    daily7d: [],
  },
  facts: { total: 5, byKind: { property: 5 } },
}

test('发布弹窗结构化展示运行态冲突且不给自动覆盖入口', async ({ page }) => {
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

  let promoteCalls = 0
  const ok = (route: Route, data: unknown, status = 200) => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify({ data }),
  })

  await page.route(/^https?:\/\/[^/]+\/api\/v[12]\//, async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === `/api/v1/ontologies/${ontologyId}`) {
      return ok(route, {
        id: ontologyId,
        name: '运行态冲突验证本体',
        domain: '供应链',
        version: 'v2',
        current_release_id: release.id,
        current_release_version: 'v2',
        status: 'published',
        entity_count: 2,
        relation_count: 0,
        action_count: 1,
        sentinel_count: 1,
        created_by: 'tester',
        created_at: '2026-07-28T00:00:00Z',
        updated_at: '2026-07-28T01:00:00Z',
      })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/version-tree`) {
      return ok(route, {
        current_release_id: release.id,
        current_release_number: 'v2',
        current_release_version: 'v2',
        versions: [release, trialDraft],
      })
    }
    if (path === `/api/v2/ontologies/${ontologyId}/versions/${trialDraft.id}/impact`) {
      return ok(route, {
        impactHash: 'impact-runtime-conflict',
        baseOutdated: false,
        breakingCount: 0,
        breaking: [],
        total: { added: 0, modified: 0, deleted: 0 },
        releaseReadiness: {
          ready: false,
          blockingCount: 1,
          errors: [{
            code: 'runtime_state_conflict',
            kind: 'runtimeState',
            id: trialDraft.latest_trial.id,
            name: trialDraft.version_number,
            field: 'runtimeStateConflicts',
            conflictCount: 3,
            message: '试跑候选会覆盖当前发布版中的非数据湖运行态事实，系统不会自动选择保留或覆盖',
          }],
          trialRunId: trialDraft.latest_trial.id,
          runtimeStateConflicts: {
            totalCount: 3,
            propertyConflictCount: 2,
            objectConflictCount: 0,
            linkConflictCount: 1,
            itemLimit: 50,
            truncated: false,
            items: [{
              resourceKind: 'objectProperty',
              objectId: 'order-O-1',
              objectTypeId: 'ot-order',
              property: 'status',
              current: '人工风险复核',
              candidate: '待处理',
              candidatePresent: true,
              candidateObjectPresent: true,
              source: 'action://mark-risk',
              factId: 'fact-action-status',
            }, {
              resourceKind: 'objectProperty',
              objectId: 'order-O-2',
              objectTypeId: 'ot-order',
              property: 'api_key',
              current: 'secret-runtime-value',
              candidate: 'secret-candidate-value',
              candidatePresent: true,
              candidateObjectPresent: true,
              source: 'user://[redacted]',
              factId: 'fact-manual-note',
            }, {
              resourceKind: 'link',
              linkId: 'link-order-supplier',
              linkTypeId: 'lt-supplied-by',
              current: {
                exists: true,
                linkTypeId: 'lt-supplied-by',
                sourceObjectId: 'order-O-1',
                targetObjectId: 'supplier-S-1',
                properties: { client_secret: 'raw-current-link-secret' },
              },
              candidate: { exists: false },
              source: 'action://unlink-supplier',
              factId: 'fact-action-link',
            }],
          },
          repairStrategy: null,
          repairSourceVersionId: trialDraft.id,
        },
      })
    }
    if (
      path === `/api/v2/ontologies/${ontologyId}/versions/${trialDraft.id}/promote`
      && request.method() === 'POST'
    ) {
      promoteCalls += 1
      return ok(route, {}, 500)
    }
    if (path === `/api/v2/formal/ontologies/${ontologyId}/overview`) return ok(route, overview)
    if (path === `/api/v2/formal/ontologies/${ontologyId}/facts/recent`) return ok(route, [])
    return ok(route, [])
  })

  await page.goto(`/#/ontologies/${ontologyId}?tab=versions`, {
    waitUntil: 'domcontentloaded',
  })
  await page.getByTestId('version-node-v2.1').getByRole(
    'button', { name: '转为发布态' },
  ).click()

  const dialog = page.getByRole('dialog', { name: '发布前检查 · v2.1' })
  await expect(dialog.getByTestId('release-readiness-blocked')).toContainText(
    '发现 3 项当前运行态与试跑候选值冲突',
  )
  await expect(dialog.getByTestId('release-readiness-blocked')).toContainText(
    '系统不会自动选择保留或覆盖',
  )
  const conflicts = dialog.getByTestId('runtime-state-conflict-item')
  await expect(conflicts).toHaveCount(3)
  await expect(conflicts.nth(0)).toContainText('order-O-1 · status')
  await expect(conflicts.nth(0)).toContainText('当前运行态值')
  await expect(conflicts.nth(0)).toContainText('人工风险复核')
  await expect(conflicts.nth(0)).toContainText('试跑候选值')
  await expect(conflicts.nth(0)).toContainText('待处理')
  await expect(conflicts.nth(0)).toContainText('action://mark-risk')
  await expect(conflicts.nth(1)).toContainText('••••••（已隐藏）')
  await expect(conflicts.nth(1)).toContainText('user://[redacted]')
  await expect(conflicts.nth(1)).not.toContainText('secret-runtime-value')
  await expect(conflicts.nth(1)).not.toContainText('secret-candidate-value')
  await expect(conflicts.nth(2)).toContainText(
    '链接 link-order-supplier · lt-supplied-by',
  )
  await expect(conflicts.nth(2)).toContainText('当前正式关系')
  await expect(conflicts.nth(2)).toContainText('试跑候选关系')
  await expect(conflicts.nth(2)).toContainText('action://unlink-supplier')
  await expect(conflicts.nth(2)).not.toContainText('raw-current-link-secret')
  await expect(dialog.getByRole('button', { name: '暂不可发布' })).toBeDisabled()
  await expect(dialog.getByRole('button', { name: '确认发布' })).toHaveCount(0)
  await expect(dialog.getByRole('button', {
    name: '创建修复分支并完善映射',
  })).toHaveCount(0)
  expect(promoteCalls).toBe(0)
})
