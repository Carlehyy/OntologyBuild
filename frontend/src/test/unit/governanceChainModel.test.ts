import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildGovernanceChain,
  collectChainNeighborhood,
  type BuildChainInput,
} from '../../pages/ontologies/detail/governance/chainModel.ts'

const baseInput: BuildChainInput = {
  pending: [{
    id: 'log-1',
    actionId: 'act-1',
    actionName: '标记风险复核',
    objectTypeId: 'ot-order',
    objectInstanceId: 'inst-1',
    parameters: { review_status: 'risk_review_pending' },
    triggerSource: 'sentinel',
  }],
  firings: [{
    id: 'firing-1',
    sentinelId: 'sen-1',
    sentinelName: '高风险订单复核标记',
    status: 'pending',
    matchCount: 1,
    matches: [{ a: 'inst-1' }],
    actionResults: [{ logId: 'log-1' }],
  }],
  sentinels: [{
    id: 'sen-1',
    name: 'mark_risk_review',
    displayName: '高风险订单复核标记',
    condition: 'a["risk_score"] >= 80',
    conditionRows: [{ leftAlias: 'a', leftProp: 'risk_score', op: '>=', rightKind: 'value', rightValue: '80' }],
    actionIds: ['act-1', 'act-2'],
    muted: false,
    enabled: true,
  }],
  actions: [
    { id: 'act-1', name: 'mark_risk_review', displayName: '标记风险复核' },
    { id: 'act-2', name: 'risk_edge_notify', displayName: '风险边沿通知' },
  ],
  autonomy: [
    { actionId: 'act-1', level: 'L1', autoRuns: { total: 0 } },
    { actionId: 'act-2', level: 'L2', autoRuns: { total: 21 } },
  ],
  mappings: [
    { id: 'map-1', entity_class: '采购订单映射', curated_dataset_id: 'ds-1', target_object_type_id: 'ot-order' },
    { id: 'map-2', entityClass: '供应商映射', curatedDatasetId: 'ds-9', targetObjectTypeId: 'ot-supplier' },
  ],
  datasets: [
    { id: 'ds-1', name: '订单 curated', row_count: 4, quality_score: 1, producer_pipeline_id: 'pipe-1' },
  ],
  pipelines: [
    { id: 'pipe-1', name: '订单管道', status: '已发布', engine: 'n8n', enabled: true },
  ],
  instanceTotal: 13,
  targetLabel: () => '采购订单 · O-1001',
  objectTypeName: (id: string) => ({ 'ot-order': '采购订单', 'ot-supplier': '供应商' })[id] || id,
}

describe('buildGovernanceChain', () => {
  it('按七段链路产出上游聚合节点与治理环路实体节点', () => {
    const { nodes } = buildGovernanceChain(baseInput)
    const byId = new Map(nodes.map(node => [node.id, node]))

    assert.equal(byId.get('pipe:pipe-1')?.column, 0)
    assert.equal(byId.get('pipe:pipe-1')?.sub, 'n8n 编排 · 已发布')
    assert.equal(byId.get('ds:ds-1')?.column, 1)
    assert.equal(byId.get('ds:ds-1')?.sub, '4 行 · 质量 100%')
    assert.equal(byId.get('map:map-1')?.column, 2)
    assert.equal(byId.get('map:map-1')?.sub, '→ 采购订单')
    // 蛇形/驼峰字段都能识别
    assert.equal(byId.get('map:map-2')?.title, '供应商映射')
    assert.equal(byId.get('inst-hub')?.sub, '共 13 个')
    assert.equal(byId.get('inst:inst-1')?.badge?.text, '命中')
    assert.equal(byId.get('sen:sen-1')?.sub, 'a.risk_score ≥ 80')
    assert.equal(byId.get('sen:sen-1')?.badge?.text, '在线')
    assert.equal(byId.get('pend:log-1')?.pulse, true)
    assert.equal(byId.get('pend:log-1')?.sub, '采购订单 · O-1001')
    assert.equal(byId.get('act:act-1')?.sub, 'L1 人审 · 自动执行 0 次')
    assert.equal(byId.get('act:act-2')?.badge?.text, '自动')
  })

  it('连线覆盖 管道→数据集→映射→热点实例→哨兵→待审批→动作 全链路', () => {
    const { edges } = buildGovernanceChain(baseInput)
    const keys = new Set(edges.map(edge => `${edge.from}->${edge.to}:${edge.kind}`))

    assert.ok(keys.has('pipe:pipe-1->ds:ds-1:flow'))
    assert.ok(keys.has('ds:ds-1->map:map-1:flow'))
    // map-2 引用的 ds-9 不在数据集清单中,不画上游边
    assert.ok(!keys.has('ds:ds-9->map:map-2:flow'))
    assert.ok(keys.has('map:map-1->inst-hub:flow'))
    // 热点实例精确连到目标类型匹配的映射(map-1 → ot-order),不走聚合枢纽
    assert.ok(keys.has('map:map-1->inst:inst-1:flow'))
    assert.ok(!keys.has('inst-hub->inst:inst-1:flow'))
    assert.ok(keys.has('inst:inst-1->pend:log-1:hit'))
    assert.ok(keys.has('sen:sen-1->pend:log-1:hit'))
    assert.ok(keys.has('pend:log-1->act:act-1:flow'))
    // act-2 无待审批,哨兵直连动作(自治通路)
    assert.ok(keys.has('sen:sen-1->act:act-2:auto'))
    // act-1 有待审批,不再重复直连
    assert.ok(!keys.has('sen:sen-1->act:act-1:auto'))
  })

  it('人工发起的待审批实例直连待审批,哨兵按 actionIds 绑定兜底连线', () => {
    const { edges, nodes } = buildGovernanceChain({
      ...baseInput,
      firings: [],
      pending: [{
        id: 'log-2', actionId: 'act-1', actionName: '标记风险复核',
        objectInstanceId: 'inst-7', triggerSource: 'manual', actorId: 'admin',
      }],
    })
    const keys = new Set(edges.map(edge => `${edge.from}->${edge.to}:${edge.kind}`))
    assert.ok(keys.has('inst:inst-7->pend:log-2:flow'))
    // 无 firing 时按 actionIds 绑定兜底出哨兵边
    assert.ok(keys.has('sen:sen-1->pend:log-2:hit'))
    assert.ok(nodes.some(node => node.id === 'pend:log-2'))
  })

  it('上游数据缺失时对应列留空、实例退回枢纽连线且不产生悬空边', () => {
    const { nodes, edges } = buildGovernanceChain({
      ...baseInput,
      mappings: [], datasets: [], pipelines: [], instanceTotal: 0,
    })
    assert.ok(!nodes.some(node => node.column <= 2 && node.kind !== 'instanceHub' && node.kind !== 'instance'))
    assert.ok(nodes.some(node => node.id === 'inst-hub'))
    // 无映射可匹配时,热点实例由聚合枢纽兜底连线
    const keys = new Set(edges.map(edge => `${edge.from}->${edge.to}:${edge.kind}`))
    assert.ok(keys.has('inst-hub->inst:inst-1:flow'))
    for (const edge of edges) {
      assert.ok(nodes.some(node => node.id === edge.from), `edge from ${edge.from} dangling`)
      assert.ok(nodes.some(node => node.id === edge.to), `edge to ${edge.to} dangling`)
    }
  })

  it('链路导读按待审批逐条生成,携带链上节点与 pendingLogId', () => {
    const { guides } = buildGovernanceChain(baseInput)
    assert.equal(guides.length, 1)
    assert.equal(guides[0].title, '采购订单 · O-1001 · 停滞于审批')
    assert.equal(guides[0].pendingLogId, 'log-1')
    assert.deepEqual(guides[0].nodeIds, ['inst:inst-1', 'sen:sen-1', 'pend:log-1', 'act:act-1'])
  })

  it('待审批目标缺失时不生成热点实例节点', () => {
    const { nodes, guides } = buildGovernanceChain({
      ...baseInput,
      pending: [{ id: 'log-3', actionId: 'act-1', actionName: '标记风险复核', triggerSource: 'system' }],
      firings: [],
    })
    assert.ok(!nodes.some(node => node.kind === 'instance'))
    assert.equal(guides[0].nodeIds[0], 'sen:sen-1')
  })
})

describe('collectChainNeighborhood', () => {
  it('从待审批节点出发覆盖精准血缘链,聚合枢纽不向外扩散', () => {
    const { edges } = buildGovernanceChain(baseInput)
    const seen = collectChainNeighborhood('pend:log-1', edges, new Set(['inst-hub']))
    assert.ok(seen.has('inst:inst-1'))
    assert.ok(seen.has('sen:sen-1'))
    assert.ok(seen.has('act:act-1'))
    // 精准上游血缘:映射 → 数据集 → 管道
    assert.ok(seen.has('map:map-1'))
    assert.ok(seen.has('ds:ds-1'))
    assert.ok(seen.has('pipe:pipe-1'))
    // 自治动作 act-2 经哨兵也在邻域内
    assert.ok(seen.has('act:act-2'))
    // 自治动作 act-2 经哨兵也在邻域内
    assert.ok(seen.has('act:act-2'))
    // 聚合枢纽可作为终端叶子出现,但不向外扩散:无关的供应商映射不点亮
    assert.ok(!seen.has('map:map-2'))
  })

  it('从聚合枢纽出发可以看到与之相连的映射与实例', () => {
    const { edges } = buildGovernanceChain(baseInput)
    const seen = collectChainNeighborhood('inst-hub', edges, new Set(['inst-hub']))
    // 起点豁免终端规则:枢纽自身出发可触达直接相连的映射及其下游
    assert.ok(seen.has('map:map-1'))
    assert.ok(seen.has('map:map-2'))
    assert.ok(seen.has('inst:inst-1'))
  })

  it('孤立节点邻域仅含自身', () => {
    const seen = collectChainNeighborhood('nowhere', [])
    assert.deepEqual([...seen], ['nowhere'])
  })
})
