/* 治理链路全景拼装纯逻辑:把上游数据资产与治理环路组装成七段链路
   数据采集 → 数据资产湖 → 字段映射 → 本体实例 → 哨兵 → 待审批 → 动作执行
   的节点与连线,并推导「链路导读」与点选高亮邻域。
   与组件解耦,全部可在 node:test 中验证;上游数据缺口时优雅降级(对应列留空)。 */

import {
  findTriggerFiring,
  buildConditionSentence,
  type FiringLike,
  type PendingLogLike,
  type SentinelLike,
  type WorkspaceActionLike,
} from './storyModel.ts'

export type ChainColumn = 0 | 1 | 2 | 3 | 4 | 5 | 6

export type ChainNodeKind =
  | 'pipeline' | 'dataset' | 'mapping'
  | 'instanceHub' | 'instance'
  | 'sentinel' | 'pending' | 'action'

export interface ChainNode {
  id: string
  column: ChainColumn
  kind: ChainNodeKind
  title: string
  sub?: string
  badge?: { text: string; tone: 'ok' | 'warn' | 'danger' | 'info' }
  /** 待审批节点持续脉冲,提示这是当前瓶颈。 */
  pulse?: boolean
  /** 业务关联 id:pending=actionLogId,其余为实体自身 id。 */
  refId?: string
}

export type ChainEdgeKind = 'flow' | 'hit' | 'auto'

export interface ChainEdge {
  id: string
  from: string
  to: string
  kind: ChainEdgeKind
}

export interface ChainGuide {
  id: string
  title: string
  sub: string
  nodeIds: string[]
  pendingLogId?: string
}

/** workspace 快照中的映射记录:蛇形/驼峰字段并存,读取时兜底。 */
export interface ChainMappingLike {
  id?: string | null
  entity_class?: string | null
  entityClass?: string | null
  dataset_name?: string | null
  datasetName?: string | null
  curated_dataset_id?: string | null
  curatedDatasetId?: string | null
  target_object_type_id?: string | null
  targetObjectTypeId?: string | null
}

/** workspace 快照中的关系映射:源/目标/边数据集均为 curated id。 */
export interface ChainLinkMappingLike {
  id?: string | null
  relation_type?: string | null
  relationType?: string | null
  link_type_id?: string | null
  linkTypeId?: string | null
  src_dataset_id?: string | null
  tgt_dataset_id?: string | null
  edge_dataset_id?: string | null
}

export interface ChainDatasetLike {
  id: string
  name?: string | null
  row_count?: number | null
  quality_score?: number | null
  producer_pipeline_id?: string | null
}

export interface ChainPipelineLike {
  id: string
  name?: string | null
  status?: string | null
  engine?: string | null
  enabled?: boolean | null
}

export interface ChainAutonomyLike {
  actionId: string
  level: 'L0' | 'L1' | 'L2'
  autoRuns: { total: number }
}

export interface ChainPendingLike extends PendingLogLike {
  objectTypeId?: string | null
}

export interface BuildChainInput {
  pending: ChainPendingLike[]
  firings: FiringLike[]
  sentinels: SentinelLike[]
  actions: WorkspaceActionLike[]
  autonomy: ChainAutonomyLike[]
  mappings: ChainMappingLike[]
  /** 关系映射(可选):与字段映射同列展示,上游边来自源/目标/边数据集。 */
  linkMappings?: ChainLinkMappingLike[]
  /** 已被本本体映射引用的成品数据集(资产湖口径,调用方预过滤)。 */
  datasets: ChainDatasetLike[]
  /** 产出上述数据集的管道(调用方预过滤)。 */
  pipelines: ChainPipelineLike[]
  /** 实例总数(实体实例 + 关系实例)。 */
  instanceTotal: number
  targetLabel: (log: ChainPendingLike) => string
  objectTypeName?: (objectTypeId: string) => string
}

export const CHAIN_COLUMNS: Array<{ column: ChainColumn; name: string }> = [
  { column: 0, name: '数据采集' },
  { column: 1, name: '数据资产湖' },
  { column: 2, name: '字段映射' },
  { column: 3, name: '本体实例' },
  { column: 4, name: '哨兵引擎' },
  { column: 5, name: '审批裁决' },
  { column: 6, name: '动作执行' },
]

const pick = (...values: Array<string | null | undefined>): string | null => {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

const LEVEL_LABEL: Record<ChainAutonomyLike['level'], string> = {
  L0: '影子', L1: '人审', L2: '自动',
}

/** 七段链路组装:上游聚合(管道/数据集/映射)+ 治理环路真实实体(实例/哨兵/待审批/动作)。 */
export function buildGovernanceChain(input: BuildChainInput): {
  nodes: ChainNode[]
  edges: ChainEdge[]
  guides: ChainGuide[]
} {
  const nodes: ChainNode[] = []
  const edges: ChainEdge[] = []
  const edgeKeys = new Set<string>()
  const addEdge = (from: string, to: string, kind: ChainEdgeKind) => {
    const key = `${from}->${to}`
    if (edgeKeys.has(key)) return
    edgeKeys.add(key)
    edges.push({ id: `e:${key}`, from, to, kind })
  }

  const typeName = input.objectTypeName || ((id: string) => id || '未知类型')

  // ① 数据采集:产出被引用数据集的管道
  const pipelineIds = new Set(input.pipelines.map(item => item.id))
  for (const pipeline of input.pipelines) {
    nodes.push({
      id: `pipe:${pipeline.id}`,
      column: 0,
      kind: 'pipeline',
      title: pipeline.name?.trim() || pipeline.id,
      sub: [pipeline.engine === 'n8n' ? 'n8n 编排' : pipeline.engine === 'python' ? 'Python 脚本' : null,
        pipeline.enabled === false ? '已停用' : pipeline.status || null]
        .filter(Boolean).join(' · ') || undefined,
      badge: pipeline.enabled === false ? { text: '停用', tone: 'danger' } : undefined,
      refId: pipeline.id,
    })
  }

  // ② 数据资产湖:被映射引用的成品数据集
  const datasetIds = new Set(input.datasets.map(item => item.id))
  for (const dataset of input.datasets) {
    nodes.push({
      id: `ds:${dataset.id}`,
      column: 1,
      kind: 'dataset',
      title: dataset.name?.trim() || dataset.id,
      sub: [
        dataset.row_count != null ? `${dataset.row_count} 行` : null,
        dataset.quality_score != null
          ? `质量 ${Math.round(dataset.quality_score * 100)}%`
          : null,
      ].filter(Boolean).join(' · ') || undefined,
      refId: dataset.id,
    })
    if (dataset.producer_pipeline_id && pipelineIds.has(dataset.producer_pipeline_id)) {
      addEdge(`pipe:${dataset.producer_pipeline_id}`, `ds:${dataset.id}`, 'flow')
    }
  }

  // ③ 字段映射:发布快照中的映射定义(含关系映射)
  for (const mapping of input.mappings) {
    const mappingId = pick(mapping.id)
    if (!mappingId) continue
    const datasetId = pick(mapping.curated_dataset_id, mapping.curatedDatasetId)
    const targetTypeId = pick(mapping.target_object_type_id, mapping.targetObjectTypeId)
    nodes.push({
      id: `map:${mappingId}`,
      column: 2,
      kind: 'mapping',
      title: pick(mapping.entity_class, mapping.entityClass, mapping.dataset_name, mapping.datasetName) || '字段映射',
      sub: targetTypeId ? `→ ${typeName(targetTypeId)}` : '未绑定对象实体',
      refId: mappingId,
    })
    if (datasetId && datasetIds.has(datasetId)) {
      addEdge(`ds:${datasetId}`, `map:${mappingId}`, 'flow')
    }
  }
  for (const linkMapping of input.linkMappings || []) {
    const mappingId = pick(linkMapping.id)
    if (!mappingId) continue
    nodes.push({
      id: `map:${mappingId}`,
      column: 2,
      kind: 'mapping',
      title: pick(linkMapping.relation_type, linkMapping.relationType) || '关系映射',
      sub: '关系映射',
      refId: mappingId,
    })
    for (const datasetId of [linkMapping.src_dataset_id, linkMapping.tgt_dataset_id, linkMapping.edge_dataset_id]) {
      if (datasetId && datasetIds.has(datasetId)) {
        addEdge(`ds:${datasetId}`, `map:${mappingId}`, 'flow')
      }
    }
  }

  // ④ 本体实例:聚合枢纽 + 待审批命中的热点实例
  const HUB_ID = 'inst-hub'
  nodes.push({
    id: HUB_ID,
    column: 3,
    kind: 'instanceHub',
    title: '本体实例',
    sub: `共 ${input.instanceTotal} 个`,
  })
  for (const mapping of input.mappings) {
    const mappingId = pick(mapping.id)
    if (mappingId) addEdge(`map:${mappingId}`, HUB_ID, 'flow')
  }
  for (const linkMapping of input.linkMappings || []) {
    const mappingId = pick(linkMapping.id)
    if (mappingId) addEdge(`map:${mappingId}`, HUB_ID, 'flow')
  }

  const hotInstanceIds = new Set<string>()
  for (const log of input.pending) {
    if (!log.objectInstanceId || hotInstanceIds.has(log.objectInstanceId)) continue
    hotInstanceIds.add(log.objectInstanceId)
    nodes.push({
      id: `inst:${log.objectInstanceId}`,
      column: 3,
      kind: 'instance',
      title: input.targetLabel(log),
      sub: '待审批目标',
      badge: { text: '命中', tone: 'danger' },
      refId: log.objectInstanceId,
    })
    // 上游血缘:目标类型与映射绑定类型匹配时精确连到该映射,否则退回聚合枢纽
    const sourceMapping = input.mappings.find(mapping =>
      pick(mapping.target_object_type_id, mapping.targetObjectTypeId) === log.objectTypeId
      && pick(mapping.id))
    addEdge(
      sourceMapping ? `map:${pick(sourceMapping.id)}` : HUB_ID,
      `inst:${log.objectInstanceId}`,
      'flow',
    )
  }

  // ⑤ 哨兵引擎
  const sentinelIds = new Set(input.sentinels.map(item => item.id))
  for (const sentinel of input.sentinels) {
    nodes.push({
      id: `sen:${sentinel.id}`,
      column: 4,
      kind: 'sentinel',
      title: (sentinel.displayName || sentinel.name || sentinel.id).trim(),
      sub: buildConditionSentence(sentinel),
      badge: sentinel.muted
        ? { text: '影子', tone: 'warn' }
        : sentinel.enabled === false
          ? { text: '停用', tone: 'danger' }
          : { text: '在线', tone: 'ok' },
      refId: sentinel.id,
    })
  }

  // ⑥ 审批裁决 + ⑦ 动作执行
  const actionIds = new Set(input.actions.map(item => item.id))
  for (const action of input.actions) {
    const stat = input.autonomy.find(item => item.actionId === action.id)
    nodes.push({
      id: `act:${action.id}`,
      column: 6,
      kind: 'action',
      title: (action.displayName || action.name || action.id).trim(),
      sub: stat
        ? `${stat.level} ${LEVEL_LABEL[stat.level]} · 自动执行 ${stat.autoRuns.total} 次`
        : undefined,
      badge: stat?.level === 'L2' ? { text: '自动', tone: 'ok' } : undefined,
      refId: action.id,
    })
  }

  const guides: ChainGuide[] = []
  for (const log of input.pending) {
    const firing = findTriggerFiring(log, input.firings)
    const sentinel = firing
      ? input.sentinels.find(item => item.id === firing.sentinelId) || null
      : input.sentinels.find(item => (item.actionIds || []).includes(log.actionId)) || null
    const node: ChainNode = {
      id: `pend:${log.id}`,
      column: 5,
      kind: 'pending',
      title: (log.actionName || log.actionId).trim(),
      sub: log.objectInstanceId ? input.targetLabel(log) : undefined,
      badge: { text: '待裁决', tone: 'warn' },
      pulse: true,
      refId: log.id,
    }
    nodes.push(node)

    const guideNodeIds = [node.id]
    if (log.objectInstanceId) {
      guideNodeIds.unshift(`inst:${log.objectInstanceId}`)
      addEdge(`inst:${log.objectInstanceId}`, node.id, firing ? 'hit' : 'flow')
    }
    if (sentinel && sentinelIds.has(sentinel.id)) {
      // 哨兵 → 待审批:硬关联(firing.actionResults)优先,绑定关系兜底
      addEdge(`sen:${sentinel.id}`, node.id, 'hit')
      guideNodeIds.splice(guideNodeIds.length - 1, 0, `sen:${sentinel.id}`)
    }
    if (actionIds.has(log.actionId)) {
      addEdge(node.id, `act:${log.actionId}`, 'flow')
      guideNodeIds.push(`act:${log.actionId}`)
    }
    guides.push({
      id: `guide:${log.id}`,
      title: `${log.objectInstanceId ? input.targetLabel(log) : (log.actionName || log.actionId)} · 停滞于审批`,
      sub: sentinel
        ? `哨兵「${(sentinel.displayName || sentinel.name || '').trim()}」命中后等待人工裁决`
        : log.triggerSource === 'manual'
          ? '人工发起,等待裁决'
          : '等待人工裁决',
      nodeIds: guideNodeIds,
      pendingLogId: log.id,
    })
  }

  // 哨兵 → 动作 的自治直连:该动作当前无待审批时,链路经哨兵直通动作
  for (const sentinel of input.sentinels) {
    if (!sentinelIds.has(sentinel.id)) continue
    for (const actionId of sentinel.actionIds || []) {
      if (!actionIds.has(actionId)) continue
      const hasPending = input.pending.some(log => log.actionId === actionId)
      if (!hasPending) addEdge(`sen:${sentinel.id}`, `act:${actionId}`, 'auto')
    }
  }

  return { nodes, edges, guides }
}

/** 点选高亮邻域:从某节点出发的整条上下游(含自身),双向 BFS。
   blockedIds(如实例聚合枢纽)作为终端节点:不穿过它们继续扩展,
   避免聚合节点把全图连成一片、失去高亮意义。 */
export function collectChainNeighborhood(
  nodeId: string,
  edges: ChainEdge[],
  blockedIds?: Set<string>,
): Set<string> {
  const upstream = new Map<string, string[]>()
  const downstream = new Map<string, string[]>()
  for (const edge of edges) {
    downstream.set(edge.from, [...(downstream.get(edge.from) || []), edge.to])
    upstream.set(edge.to, [...(upstream.get(edge.to) || []), edge.from])
  }
  const seen = new Set<string>([nodeId])
  const queue = [nodeId]
  while (queue.length) {
    const current = queue.shift()!
    if (current !== nodeId && blockedIds?.has(current)) continue
    for (const next of [
      ...(downstream.get(current) || []),
      ...(upstream.get(current) || []),
    ]) {
      if (!seen.has(next)) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return seen
}
