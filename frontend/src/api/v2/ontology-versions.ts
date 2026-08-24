import { apiClientV2 } from '@/api/client'

export interface OntologyTrialActionEffect {
  type?: string
  description?: string
  status?: string
  committed?: boolean
  [key: string]: unknown
}

export interface OntologyTrialActionSample {
  actionId?: string
  actionName?: string
  edge?: 'enter' | 'leave' | string
  targetInstanceId?: string | null
  match?: Record<string, unknown>
  parameters?: Record<string, unknown>
  status?: string
  effects?: OntologyTrialActionEffect[]
  validationErrors?: string[]
  errorMessage?: string | null
  sideEffects?: string
}

export interface OntologyTrialSentinelResult {
  id?: string
  name?: string
  activation?: 'active' | 'muted' | 'disabled' | string
  matched?: number
  candidateCount?: number
  candidateCapReached?: boolean
  parameterErrorCount?: number
  errors?: string[]
  plannedActions?: number
  plannedActionSamples?: OntologyTrialActionSample[]
  plannedActionsTruncated?: boolean
  sideEffects?: string
  skipped?: boolean
}

export interface OntologyTrialRun {
  id: string
  status: 'running' | 'passed' | 'failed' | 'stale'
  result?: {
    counts?: { objects?: number; links?: number; facts?: number; datasets?: number }
    errors?: Array<{ message: string }>
    warnings?: Array<{ message: string }>
    sentinels?: OntologyTrialSentinelResult[]
    actionsExecuted?: number
    sideEffects?: string
  }
  impact_hash?: string
  created_at?: string
}

export interface OntologyVersionNode {
  id: string
  version_number: string
  version_label?: string
  description?: string
  parent_version_id?: string | null
  base_release_id?: string | null
  promoted_from_id?: string | null
  node_kind: 'release' | 'draft'
  lifecycle_status: 'editing' | 'trial_ready' | 'released' | 'superseded'
  revision: number
  /** 语义层只透摘要标记：是否已沉淀业务语义（画布/需求文档）。 */
  hasSemanticLayer?: boolean
  /** 语义层修订号；无语义层或旧后端为 0/缺省。 */
  semanticRevision?: number
  latest_trial?: OntologyTrialRun | null
  created_at?: string
  published_at?: string
}

export interface OntologyVersionTree {
  current_release_id: string
  current_release_number: string
  current_release_version: string
  versions: OntologyVersionNode[]
}

/** 版本业务语义层只读总览（后端 semantic_gate.semantic_overview 同口径）。 */
export interface OntologySemanticOverview {
  hasSemanticLayer: boolean
  documentTitle?: string | null
  documentStale: boolean
  canvasCounts: {
    objects: number
    actors: number
    behaviors: number
    events: number
    rules: number
    scenarios: number
    processes: number
  }
  structureCounts: {
    objectTypes: number
    linkTypes: number
    actions: number
    functions: number
    sentinels: number
  }
  consistency: { issueCount: number; byCode: Record<string, number> }
}

/** GET .../versions/{vid}/semantic 的 data：语义层快照原文 + 一致性总览。 */
export interface OntologyVersionSemantic {
  semantic: Record<string, unknown> | null
  overview: OntologySemanticOverview
}

export interface OntologyImpactReport {
  impactHash: string
  baseOutdated: boolean
  breakingCount: number
  breaking: Array<{ message: string }>
  total: { added: number; modified: number; deleted: number }
  /** 旧后端无此字段，前端按缺失处理（不渲染业务语义区块）。 */
  semanticOverview?: OntologySemanticOverview
  releaseReadiness?: {
    ready: boolean
    blockingCount: number
    errors: OntologyReleaseGateIssue[]
    trialRunId?: string | null
    runtimeStateConflicts?: OntologyRuntimeStateConflictReport
    repairStrategy?: 'create_draft' | 'rebase' | null
    repairSourceVersionId?: string | null
  }
}

export interface OntologyRuntimeStateConflict {
  resourceKind: 'objectProperty' | 'object' | 'link'
  objectId?: string
  objectTypeId?: string | null
  property?: string
  linkId?: string
  linkTypeId?: string | null
  current: unknown
  currentPresent?: boolean
  candidate: unknown
  candidatePresent?: boolean
  candidateObjectPresent?: boolean
  source: string
  factId?: string | null
}

export interface OntologyRuntimeStateConflictReport {
  totalCount: number
  propertyConflictCount: number
  objectConflictCount: number
  linkConflictCount: number
  itemLimit: number
  truncated: boolean
  items: OntologyRuntimeStateConflict[]
}

export interface OntologyReleaseGateIssue {
  code: string
  kind: string
  id?: string
  name?: string
  message: string
  field?: string
  targetId?: string
  targetName?: string
}

export const ontologyVersionApi = {
  tree: (ontologyId: string) =>
    apiClientV2.get<OntologyVersionTree>(`/ontologies/${ontologyId}/version-tree`),
  createDraft: (ontologyId: string, sourceVersionId: string, body: {
    versionLabel?: string
    description?: string
    recoveryMode?: 'current_release_trial'
    expectedCurrentReleaseId?: string
  }) => apiClientV2.post<OntologyVersionNode>(
    `/ontologies/${ontologyId}/versions/${sourceVersionId}/drafts`, body),
  deleteVersion: (ontologyId: string, versionId: string) =>
    apiClientV2.delete<{ id: string; version_number: string }>(
      `/ontologies/${ontologyId}/versions/${versionId}`),
  runTrial: (ontologyId: string, versionId: string) =>
    apiClientV2.post<OntologyTrialRun>(
      `/ontologies/${ontologyId}/versions/${versionId}/trial-runs`, {}),
  impact: (ontologyId: string, versionId: string) =>
    apiClientV2.get<OntologyImpactReport>(
      `/ontologies/${ontologyId}/versions/${versionId}/impact`),
  versionSemantic: (ontologyId: string, versionId: string) =>
    apiClientV2.get<OntologyVersionSemantic>(
      `/ontologies/${ontologyId}/versions/${versionId}/semantic`),
  promote: (ontologyId: string, versionId: string, body: {
    trialRunId: string
    impactHash: string
    versionLabel?: string
  }) => apiClientV2.post<OntologyVersionNode>(
    `/ontologies/${ontologyId}/versions/${versionId}/promote`, body),
}
