import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildAutonomyTimeline,
  buildBindingSentence,
  buildConditionSentence,
  buildDailySpark,
  buildEffectPreview,
  buildLevelSteps,
  buildOperationsRows,
  findTriggerFiring,
  firingMatchedInstanceIds,
  renderMessageTemplate,
  resolvePendingContext,
} from '../../pages/ontologies/detail/governance/storyModel.ts'


describe('findTriggerFiring', () => {
  it('finds the firing whose actionResults reference the pending log', () => {
    const firings = [
      { id: 'f1', sentinelId: 's1', sentinelName: '哨兵A', status: 'pending', matchCount: 1, actionResults: [{ logId: 'other' }] },
      { id: 'f2', sentinelId: 's2', sentinelName: '哨兵B', status: 'pending', matchCount: 2, actionResults: [{ logId: 'log-1' }] },
    ]
    assert.equal(findTriggerFiring({ id: 'log-1', actionId: 'a' }, firings as any)?.id, 'f2')
    assert.equal(findTriggerFiring({ id: 'log-9', actionId: 'a' }, firings as any), null)
  })
})

describe('firingMatchedInstanceIds', () => {
  it('dedupes matches and splits entered from持续命中', () => {
    const firing = {
      id: 'f', sentinelId: 's', sentinelName: 'n', status: 'pending', matchCount: 3,
      matches: [{ a: 'i-1' }, { a: 'i-2' }, { a: 'i-1', b: 'i-3' }],
      entered: ['i-2'], left: [],
    }
    assert.deepEqual(firingMatchedInstanceIds(firing as any), {
      entered: ['i-2'],
      others: ['i-1', 'i-3'],
    })
  })
})

describe('buildConditionSentence', () => {
  it('renders structured condition rows with readable operators', () => {
    const sentence = buildConditionSentence({
      id: 's',
      conditionLogic: 'and',
      conditionRows: [
        { leftAlias: 'a', leftProp: 'risk_score', op: '>=', rightKind: 'value', rightValue: '80' },
        { leftAlias: 'a', leftProp: 'amount', op: '>', rightKind: 'value', rightValue: '10000' },
      ],
    } as any)
    assert.equal(sentence, 'a.risk_score ≥ 80 且 a.amount > 10000')
  })

  it('joins or-logic with 或', () => {
    const sentence = buildConditionSentence({
      id: 's', conditionLogic: 'or',
      conditionRows: [{ leftAlias: 'a', leftProp: 'vip', op: '==', rightKind: 'value', rightValue: 'true' }],
    } as any)
    assert.equal(sentence, 'a.vip = true')
  })

  it('falls back to raw condition string, then placeholder', () => {
    assert.equal(buildConditionSentence({ id: 's', condition: ' a.x > 1 ' } as any), 'a.x > 1')
    assert.equal(buildConditionSentence({ id: 's' } as any), '未配置条件')
    assert.equal(buildConditionSentence(null), '未配置条件')
  })
})

describe('buildBindingSentence', () => {
  it('renders bound type names and link count', () => {
    const sentence = buildBindingSentence(
      { id: 's', bindings: [{ alias: 'a', objectTypeId: 'ot-1' }], links: [{ from: 'a', linkTypeId: 'lt', to: 'b' }] } as any,
      id => (id === 'ot-1' ? '采购订单' : id),
    )
    assert.equal(sentence, '监听 采购订单,经由 1 条关系关联')
  })

  it('handles missing bindings', () => {
    assert.equal(buildBindingSentence({ id: 's', bindings: [] } as any, id => id), '未配置监听对象')
  })
})

describe('renderMessageTemplate', () => {
  it('fills object and params placeholders', () => {
    const rendered = renderMessageTemplate('订单 {{object.order_id}} 边沿={{params.edge}} 评分={{object.risk_score}}', {
      object: { order_id: 'O-1001', risk_score: 92 },
      params: { edge: 'enter' },
    })
    assert.equal(rendered, '订单 O-1001 边沿=enter 评分=92')
  })

  it('keeps placeholders for missing values', () => {
    assert.equal(
      renderMessageTemplate('订单 {{object.order_id}}', { object: {}, params: {} }),
      '订单 {{object.order_id}}',
    )
  })
})

describe('buildEffectPreview', () => {
  it('renders update_property with parameter value', () => {
    const items = buildEffectPreview({
      action: {
        id: 'act',
        description: '标记待复核',
        rules: [{
          type: 'update_property',
          name: '更新履约状态',
          config: { targetProperty: 'status', valueSource: 'parameter', value: 'review_status' },
        }],
      } as any,
      parameters: { review_status: 'risk_review_pending' },
      targetLabel: '采购订单 · O-1001',
    })
    assert.equal(items[0].sentence, '标记待复核')
    assert.equal(items[1].sentence, '把 采购订单 · O-1001 的「status」更新为 "risk_review_pending"')
  })

  it('renders notification with pre-rendered message template', () => {
    const items = buildEffectPreview({
      action: {
        id: 'act',
        rules: [{
          type: 'notification',
          config: { recipient: 'admin', messageTemplate: '订单 {{object.order_id}} 边沿={{params.edge}}' },
        }],
      } as any,
      parameters: { edge: 'enter' },
      targetLabel: '采购订单 · O-1001',
      objectValues: { order_id: 'O-1001' },
    })
    assert.equal(items[0].sentence, '向 admin 发送站内通知')
    assert.equal(items[0].detail, '订单 O-1001 边沿=enter')
  })

  it('handles missing action definition and empty rules', () => {
    assert.equal(
      buildEffectPreview({ action: null, parameters: {}, targetLabel: 'x' })[0].type,
      'unknown',
    )
    assert.equal(
      buildEffectPreview({ action: { id: 'a', rules: [] } as any, parameters: {}, targetLabel: 'x' })[0].sentence,
      '该动作未定义执行规则,批准后不会产生数据变更',
    )
  })
})

describe('buildAutonomyTimeline', () => {
  it('filters by action, skips dry runs, normalizes statuses and caps length', () => {
    const logs = [
      { id: 'l1', actionId: 'a', status: 'success', executedAt: 't1', durationMs: 10 },
      { id: 'l2', actionId: 'a', status: 'failed', executedAt: 't2', errorMessage: 'boom' },
      { id: 'l3', actionId: 'b', status: 'success', executedAt: 't3' },
      { id: 'l4', actionId: 'a', status: 'success', dryRun: true, executedAt: 't4' },
      { id: 'l5', actionId: 'a', status: 'rejected', executedAt: 't5', decisionReason: '证据不足' },
    ]
    const timeline = buildAutonomyTimeline(logs as any, 'a')
    assert.deepEqual(timeline.map(dot => dot.status), ['success', 'failed', 'rejected'])
    assert.equal(timeline[2].reason, '证据不足')
  })
})

describe('buildDailySpark', () => {
  it('maps daily7d into spark datums with zero defaults', () => {
    const data = buildDailySpark([
      { date: '08-03', firings: { fired: 2 }, actionRuns: { success: 1 } },
      { date: '08-04' } as any,
    ])
    assert.deepEqual(data, [
      { date: '08-03', fired: 2, firedError: 0, runSuccess: 1, runFailed: 0 },
      { date: '08-04', fired: 0, firedError: 0, runSuccess: 0, runFailed: 0 },
    ])
    assert.deepEqual(buildDailySpark(null), [])
  })
})

describe('buildLevelSteps', () => {
  it('marks reached and current step', () => {
    assert.deepEqual(buildLevelSteps('L1'), [
      { key: 'L0', reached: true, current: false },
      { key: 'L1', reached: true, current: true },
      { key: 'L2', reached: false, current: false },
    ])
  })
})

describe('resolvePendingContext', () => {
  const sentinels = [
    { id: 's1', name: '哨兵A', actionIds: ['a1'] },
    { id: 's2', name: '哨兵B', actionIds: ['a2'] },
  ]
  const actions = [{ id: 'a1' }, { id: 'a2' }]
  it('优先经 firing 硬关联解析哨兵,其次按 actionIds 绑定兜底', () => {
    const firings = [
      { id: 'f1', sentinelId: 's2', sentinelName: '哨兵B', status: 'pending', matchCount: 1, actionResults: [{ logId: 'log-1' }] },
    ]
    const withFiring = resolvePendingContext({ id: 'log-1', actionId: 'a1' }, firings as any, sentinels as any, actions as any)
    assert.equal(withFiring.firing?.id, 'f1')
    assert.equal(withFiring.sentinel?.id, 's2')
    assert.equal(withFiring.actionDef?.id, 'a1')
    const fallback = resolvePendingContext({ id: 'log-2', actionId: 'a1' }, [], sentinels as any, actions as any)
    assert.equal(fallback.firing, null)
    assert.equal(fallback.sentinel?.id, 's1')
  })
})

describe('buildOperationsRows', () => {
  const autonomy = [
    {
      actionId: 'a1', actionName: '标记风险复核', requiresApproval: true, level: 'L1',
      sentinels: [{ id: 's1', name: '复核标记', muted: false, enabled: true }],
      decisions: { approved: 1, rejected: 1, total: 2, recentCount: 2, recentApprovalRate: 0.5 },
      autoRuns: { total: 0, failed: 0 }, pending: 1,
      recommendation: null, recommendationReason: null,
      thresholds: { promoteMinDecisions: 10, promoteRate: 0.95 },
    },
    {
      actionId: 'a2', actionName: '风险边沿通知', requiresApproval: false, level: 'L2',
      sentinels: [{ id: 's2', name: '边沿监控', muted: true, enabled: true }],
      decisions: { approved: 0, rejected: 0, total: 0, recentCount: 0, recentApprovalRate: null },
      autoRuns: { total: 21, failed: 1 }, pending: 0,
      recommendation: null, recommendationReason: null,
      thresholds: { promoteMinDecisions: 10, promoteRate: 0.95 },
    },
  ]
  const pending = [
    { id: 'log-1', actionId: 'a1' },
    { id: 'log-2', actionId: 'a1' },
  ]
  const firings = [
    { id: 'f1', sentinelId: 's2', sentinelName: '边沿监控', status: 'fired', matchCount: 2 },
    { id: 'f2', sentinelId: 's2', sentinelName: '边沿监控', status: 'fired', matchCount: 1 },
  ]

  it('按动作并联待审批与哨兵状态,有待审批的动作排前', () => {
    const rows = buildOperationsRows({ autonomy: autonomy as any, pending: pending as any, firings: firings as any })
    assert.equal(rows.length, 2)
    assert.equal(rows[0].stat.actionId, 'a1')
    assert.deepEqual(rows[0].pendings.map(log => log.id), ['log-1', 'log-2'])
    assert.equal(rows[1].pendings.length, 0)
  })

  it('哨兵状态与最近命中数(matchCount 求和)正确推导', () => {
    const rows = buildOperationsRows({ autonomy: autonomy as any, pending: [] as any, firings: firings as any })
    const s1 = rows.find(row => row.stat.actionId === 'a1')?.sentinelViews[0]
    assert.equal(s1?.status, 'online')
    assert.equal(s1?.recentHits, 0)
    const s2 = rows.find(row => row.stat.actionId === 'a2')?.sentinelViews[0]
    assert.equal(s2?.status, 'muted')
    assert.equal(s2?.recentHits, 3)
  })

  it('停用哨兵状态为 disabled', () => {
    const rows = buildOperationsRows({
      autonomy: [{
        ...autonomy[0],
        sentinels: [{ id: 's9', name: '停用哨兵', muted: false, enabled: false }],
      }] as any,
      pending: [] as any,
      firings: [] as any,
    })
    assert.equal(rows[0].sentinelViews[0].status, 'disabled')
  })
})
