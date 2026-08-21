// 实例数据页(instance-browser)共享类型。FormalInstancesView、
// InstanceDetailPanel、InstanceSummaryBar 共用,避免组件间循环引用。

export interface ReleaseSummary {
  id: string
  version: string
  publishedAt?: string | null
}

export interface SchemaProperty {
  id: string
  name: string
  displayName?: string
  type?: string
  required?: boolean
  source?: string
}

export interface AssociatedDataset {
  id: string
  name: string
  kind?: string | null
  roles: string[]
  available: boolean
}

export interface ObjectTypeNode {
  id: string
  name: string
  displayName?: string
  description?: string
  icon?: string | null
  color?: string | null
  primaryKey?: string | null
  properties: SchemaProperty[]
  instanceCount: number
  associatedDatasets: AssociatedDataset[]
}

export interface LinkTypeNode {
  id: string
  name: string
  displayName?: string
  description?: string
  icon?: string | null
  color?: string | null
  sourceObjectTypeId: string
  targetObjectTypeId: string
  cardinality?: string
  properties: SchemaProperty[]
  instanceCount: number
  associatedDatasets: AssociatedDataset[]
}

export interface InstanceCatalog {
  release: ReleaseSummary
  objectTypes: ObjectTypeNode[]
  linkTypes: LinkTypeNode[]
  legacyProjection: LegacyProjectionStatus
}

export interface LegacyProjectionStatus {
  objectInstances: number
  linkInstances: number
  total: number
  canAdopt: boolean
  recommendedAction: 'none' | 'adopt_legacy' | 'publish_draft' | 'manual_review'
  blockingReasons: Array<{ code: string; message: string }>
}

export interface ObjectRow {
  id: string
  objectTypeId: string
  properties: Record<string, unknown>
  computed: Record<string, unknown>
  source?: string | null
  externalId?: string | null
  createdAt: string
  updatedAt: string
}

export interface EndpointSummary {
  id: string
  objectTypeId: string
  label: string
  externalId?: string | null
}

export interface LinkRow {
  id: string
  linkTypeId: string
  sourceObjectId: string
  targetObjectId: string
  sourceObject?: EndpointSummary | null
  targetObject?: EndpointSummary | null
  properties: Record<string, unknown>
  createdAt?: string | null
}

export interface InstancePage<T> {
  release: ReleaseSummary
  items: T[]
  total: number
  page: number
  pageSize: number
}

export interface DataColumn {
  name: string
  label: string
  type?: string
  primary?: boolean
  required?: boolean
  computed?: boolean
  runtime?: boolean
}

export type Selection = { kind: 'object' | 'link'; id: string }

// 实例属性级事实(/instances/{id}/facts 返回项,页面用到的子集)。
export interface InstanceFact {
  id: string
  propertyName: string
  value: unknown
  present: boolean
  kind: string
  source?: string | null
  actorId?: string | null
  recordedAt?: string | null
}

// /overview 响应中本页汇总条与概览区用到的子集。
export interface FormalOverviewSummary {
  data?: {
    instances?: number
    instancesBySource?: Record<string, number>
    linkInstances?: number
  }
  runtime?: {
    daily7d?: Array<{
      date: string
      firings?: { fired?: number; error?: number }
      actionRuns?: { success?: number; failed?: number }
    }>
  }
}
