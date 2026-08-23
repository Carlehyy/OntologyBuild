/**
 * 本体网络 API — /api/v2/ontology-network/*
 *
 * 跨本体全局图只读视图：数据源是 PG fo_* 表 / 发布快照，不依赖 Neo4j 投影就绪。
 * 全部走 apiClientV2；响应已由拦截器解包 data 字段。
 */
import { apiClientV2 } from './client'

// ---------- 类型 ----------

export interface NetworkOntologySection {
  id: string
  name: string
  domain: string
  /** true = 读当前发布版快照；false = 未发布，回退工作区实时数据 */
  published: boolean
  releaseId: string | null
  version: string | null
  typeCount: number
  linkTypeCount: number
  instanceCount: number
  error?: string
}

export type NetworkGraphNodeKind = 'object_type' | 'instance' | 'property'

export interface NetworkGraphNode {
  id: string
  entityId: string
  kind: NetworkGraphNodeKind
  label: string
  secondaryLabel?: string
  technicalName?: string
  objectTypeId?: string
  objectTypeLabel?: string
  count?: number
  color?: string | null
  description?: string | null
  source?: string | null
  externalId?: string | null
  preview?: { name: string; label: string; value: string }[]
  updatedAt?: string | null
  /** 本体归属标注（跨本体全局图必有） */
  ontologyId: string
  ontologyName: string
}

export type NetworkGraphEdgeKind = 'relation' | 'schema_relation' | 'contains' | 'attribute' | 'bridge'

export interface NetworkGraphEdge {
  id: string
  entityId?: string
  kind: NetworkGraphEdgeKind
  source: string
  target: string
  label: string
  linkTypeId?: string
  cardinality?: string
  properties?: Record<string, unknown>
  /** 本体归属标注（桥接边没有单一归属本体） */
  ontologyId?: string
  ontologyName?: string
  bridgeGroup?: string
  crossOntology?: boolean
}

export interface NetworkBridgeMember {
  nodeId: string
  entityId: string
  ontologyId: string
  ontologyName: string
  label: string
}

export interface NetworkBridgeGroup {
  key: string
  label: string
  members: NetworkBridgeMember[]
}

export interface NetworkGraphData {
  level: number
  query: string | null
  limitPerType: number
  ontologies: NetworkOntologySection[]
  errors: { ontologyId: string; message: string }[]
  nodes: NetworkGraphNode[]
  edges: NetworkGraphEdge[]
  bridges: { enabled: boolean; groups: NetworkBridgeGroup[] }
  meta: {
    nodeBudget: number
    edgeBudget: number
    truncated: boolean
    droppedEdges: number
    nodeCount: number
    edgeCount: number
    selectedOntologies: number
    totalInstances: number
  }
}

export interface NetworkInstanceDetail {
  id: string
  label: string
  objectType: {
    id: string
    name: string
    displayName: string
    primaryKey: string | null
    properties: { name: string; displayName?: string; display_name?: string; type?: string }[]
  }
  properties: Record<string, unknown>
  computed: Record<string, unknown>
  source: string | null
  externalId: string | null
}

export interface NetworkPathResult {
  kind: 'path'
  sourceInstanceId: string
  targetInstanceId: string
  sourceLabel: string
  targetLabel: string
  direction: string
  maxDepth: number
  paths: { nodeIds: string[]; edgeIds: string[]; hops: number }[]
  nodes: NetworkGraphNode[]
  edges: NetworkGraphEdge[]
  found: boolean
  truncated: boolean
}

export interface NetworkImpactResult {
  kind: 'impact'
  mode: string
  change: {
    instanceId: string
    instanceLabel: string
    objectType: string
    property: string
    propertyLabel: string
    currentValue?: unknown
    proposedValue?: unknown
  }
  direction: string
  maxDepth: number
  summary: { related: number; direct: number; indirect: number }
  impacts: {
    instanceId: string
    label: string
    objectType: string
    depth: number
    classification: 'direct' | 'indirect'
    certainty: string
  }[]
  nodes: NetworkGraphNode[]
  edges: NetworkGraphEdge[]
  truncated: boolean
  disclaimer: string
}

type Direction = 'both' | 'outgoing' | 'incoming'

const base = '/ontology-network'

// ---------- API ----------

export const ontologyNetworkApi = {
  overview: (fresh?: boolean) => apiClientV2.get<NetworkOntologySection[]>(`${base}/overview`, {
    params: fresh ? { fresh: 'true' } : undefined,
  }),

  graph: (params: {
    ontologyIds: string[]
    level: 1 | 2
    query?: string
    limitPerType?: number
    bridgeSameName?: boolean
    /** 跳过实例计数缓存，强制直查（手动刷新时使用）。 */
    fresh?: boolean
  }) => apiClientV2.get<NetworkGraphData>(`${base}/graph`, {
    params: {
      ontology_ids: params.ontologyIds.join(','),
      level: params.level,
      query: params.query || undefined,
      limit_per_type: params.limitPerType,
      bridge_same_name: params.bridgeSameName !== false,
      fresh: params.fresh ? 'true' : undefined,
    },
  }),

  instanceDetail: (oid: string, instanceId: string, releaseId?: string | null) =>
    apiClientV2.get<NetworkInstanceDetail>(
      `${base}/${encodeURIComponent(oid)}/instances/${encodeURIComponent(instanceId)}`,
      { params: releaseId ? { release_id: releaseId } : undefined },
    ),

  findPaths: (oid: string, body: {
    sourceInstanceId: string
    targetInstanceId: string
    direction?: Direction
    maxDepth?: number
    maxPaths?: number
    releaseId?: string | null
  }) => apiClientV2.post<NetworkPathResult>(`${base}/${encodeURIComponent(oid)}/paths`, {
    // 请求体走 CamelModel 别名（camelCase），与 agent 图接口保持一致
    sourceInstanceId: body.sourceInstanceId,
    targetInstanceId: body.targetInstanceId,
    direction: body.direction || 'both',
    maxDepth: body.maxDepth ?? 5,
    maxPaths: body.maxPaths ?? 3,
    releaseId: body.releaseId || null,
  }),

  analyzeImpact: (oid: string, body: {
    instanceId: string
    property: string
    proposedValue: unknown
    direction?: Direction
    maxDepth?: number
    releaseId?: string | null
  }) => apiClientV2.post<NetworkImpactResult>(`${base}/${encodeURIComponent(oid)}/impact`, {
    instanceId: body.instanceId,
    property: body.property,
    proposedValue: body.proposedValue,
    direction: body.direction || 'both',
    maxDepth: body.maxDepth ?? 3,
    releaseId: body.releaseId || null,
  }),
}
