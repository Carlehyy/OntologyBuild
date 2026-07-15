/**
 * 本体智能体 API — /api/v2/formal/ontologies/{oid}/agent/*
 *
 * chat 走 SSE 流式（fetch + ReadableStream 手动解析，axios 不支持流），
 * 其余走 apiClientV2。
 */
import { apiClientV2 } from './client'

// ---------- 类型 ----------

export interface AgentProfile {
  id: string
  ontologyId: string
  enabled: boolean
  allowedObjectTypeIds: string[] | null
  allowedLinkTypeIds: string[] | null
  allowedActionIds: string[] | null
  allowActionProposals: boolean
  maxRowsPerQuery: number
  maxSteps: number
  systemPromptExtra: string
  defaultModelId: string | null
  updatedAt: string
}

export interface AgentProfileUpdate {
  enabled?: boolean
  allowedObjectTypeIds?: string[] | null
  allowedLinkTypeIds?: string[] | null
  allowedActionIds?: string[] | null
  allowActionProposals?: boolean
  maxRowsPerQuery?: number
  maxSteps?: number
  systemPromptExtra?: string
  defaultModelId?: string | null
  /** 把某些白名单字段重置为 null（=全部允许）：字段名用 snake_case */
  resetToAll?: string[]
}

export interface AgentCapabilities {
  enabled: boolean
  objectTypes: { id: string; name: string; displayName: string; instanceCount: number }[]
  linkTypes: { id: string; name: string; displayName: string }[]
  actions: { id: string; name: string; displayName: string; requiresApproval: boolean }[]
  allowActionProposals: boolean
  maxRowsPerQuery: number
  maxSteps: number
  skillCard: string
}

export interface AgentStep {
  tool: string
  arguments: Record<string, unknown>
  summary: string
  durationMs: number
  /** 工具原始输出（后端截断后下发），用于「展开查看工具输出」 */
  result?: unknown
  error?: string
}

export interface AgentCitation {
  instanceId: string
  objectType: string
  label: string
  /** 统一引用契约：展示就绪串（objectType · label） */
  sourceLabel?: string
  /** 属性摘要，引用卡片副文本 / 悬浮显示 */
  snippet?: string
}

export interface AgentProposal {
  proposalId: string
  actionId: string
  actionName: string
  parameters: Record<string, unknown>
  targetInstanceId: string | null
  requiresApproval: boolean
  status: string
  validationErrors: string[]
  effects: { type: string; description?: string; [k: string]: unknown }[]
}

export interface AgentMessageDTO {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: AgentStep[]
  citations: AgentCitation[]
  proposals: AgentProposal[]
  model?: string | null
  createdAt: string
}

export interface AgentConversationDTO {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messages?: AgentMessageDTO[]
}

export type AgentEvent =
  | { type: 'meta'; conversationId: string; model: string }
  | ({ type: 'step' } & AgentStep)
  | { type: 'answer'; content: string; citations: AgentCitation[]; proposals: AgentProposal[]; usage?: unknown }
  | { type: 'error'; message: string }
  | { type: 'done' }

export interface ExecuteProposalResult {
  status: string
  errorMessage?: string | null
  validationErrors?: string[]
  effects?: { type: string; description?: string }[]
  pendingApproval?: boolean
  id?: string
}

export type AgentGraphNodeKind = 'object_type' | 'instance' | 'property'
export type AgentGraphEdgeKind = 'schema_relation' | 'contains' | 'relation' | 'attribute'

export interface AgentGraphNode {
  id: string
  entityId: string
  kind: AgentGraphNodeKind
  label: string
  secondaryLabel?: string | null
  technicalName?: string
  objectTypeId?: string
  objectTypeLabel?: string
  count?: number
  color?: string | null
  description?: string | null
  propertiesCount?: number
  source?: string | null
  externalId?: string | null
  preview?: { name: string; label: string; value: string }[]
  updatedAt?: string | null
  instanceId?: string
  propertyName?: string
  propertyType?: string
  value?: unknown
  isNull?: boolean
}

export interface AgentGraphEdge {
  id: string
  entityId?: string
  kind: AgentGraphEdgeKind
  source: string
  target: string
  label: string
  linkTypeId?: string
  linkTypeName?: string
  cardinality?: string
  properties?: Record<string, unknown>
}

export interface AgentGraphData {
  ontologyId: string
  ontologyName: string
  depth: 1 | 2 | 3
  nodes: AgentGraphNode[]
  edges: AgentGraphEdge[]
  meta: {
    query?: string | null
    objectTypeId?: string | null
    focusInstanceId?: string | null
    instanceCounts: Record<string, number>
    loadedInstances: number
    matchedInstances: number
    limitPerType: number
    truncated: boolean
    propertyTruncated: boolean
    nodeBudget: number
    edgeBudget: number
  }
}

export interface AgentInstanceDetail {
  id: string
  label: string
  objectType: {
    id: string
    name: string
    displayName: string
    primaryKey?: string | null
    properties: { id?: string; name: string; displayName?: string; display_name?: string; type?: string }[]
  }
  properties: Record<string, unknown>
  computed: Record<string, unknown>
  source?: string | null
  externalId?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface AgentGraphPath {
  nodeIds: string[]
  edgeIds: string[]
  steps: { linkInstanceId: string; linkTypeId: string; direction: 'out' | 'in' }[]
  hops: number
}

export interface AgentGraphPathResult {
  kind: 'path'
  sourceInstanceId: string
  targetInstanceId: string
  sourceLabel: string
  targetLabel: string
  direction: 'both' | 'outgoing' | 'incoming'
  maxDepth: number
  paths: AgentGraphPath[]
  nodes: AgentGraphNode[]
  edges: AgentGraphEdge[]
  found: boolean
  truncated: boolean
  visualizationTruncated?: boolean
  visualizationCounts?: AgentGraphVisualizationCounts
}

export interface AgentGraphVisualizationCounts {
  available: { nodes: number; edges: number; impacts: number }
  displayed: { nodes: number; edges: number; impacts: number }
}

export interface AgentGraphImpactResult {
  kind: 'impact'
  mode: 'association_only'
  change: {
    instanceId: string
    instanceLabel: string
    objectType: string
    property: string
    propertyLabel: string
    currentValue: unknown
    proposedValue: unknown
  }
  direction: 'both' | 'outgoing' | 'incoming'
  maxDepth: number
  summary: { related: number; direct: number; indirect: number }
  impacts: {
    instanceId: string
    label: string
    objectType: string
    depth: number
    classification: 'direct' | 'indirect'
    certainty: 'related'
    path: AgentGraphPath
  }[]
  nodes: AgentGraphNode[]
  edges: AgentGraphEdge[]
  truncated: boolean
  disclaimer: string
  visualizationTruncated?: boolean
  visualizationCounts?: AgentGraphVisualizationCounts
}

export type ReportVisualization = 'auto' | 'kpi' | 'bar' | 'line' | 'pie' | 'table' | 'none'

export interface ReportQuery {
  tool: 'aggregate_objects' | 'search_objects'
  arguments: Record<string, unknown>
}

export interface ReportSection {
  id: string
  title: string
  goal: string
  visualization: ReportVisualization
  queryPlan: ReportQuery[]
}

export interface AnalysisReportTemplate {
  id: string
  ontologyId: string
  createdBy: string
  name: string
  description: string
  sourcePrompt: string
  generationMode: 'ai' | 'fallback' | 'manual'
  status: 'draft' | 'published'
  revision: number
  sections: ReportSection[]
  style: Record<string, unknown>
  defaultModelId: string | null
  lastPreviewRunId: string | null
  lastPreviewRevision: number | null
  publishedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface ReportQuality {
  passed: boolean
  score: number
  threshold: number
  summary: string
  blockers: string[]
  warnings: string[]
  checks: { key: string; label: string; passed: boolean; detail: string }[]
  templateRevision: number
}

export interface AnalysisReportRun {
  id: string
  templateId: string
  ontologyId: string
  createdBy: string
  triggerType: 'preview' | 'manual' | 'scheduled'
  status: 'running' | 'succeeded' | 'failed'
  templateRevision: number
  templateSnapshot: Record<string, unknown>
  sectionResults: Record<string, unknown>[]
  qualityReport: ReportQuality
  htmlContent: string
  errorMessage: string | null
  startedAt: string
  completedAt: string | null
}

// ---------- REST ----------

const base = (oid: string) => `/formal/ontologies/${oid}/agent`

export const agentApi = {
  getProfile: (oid: string) => apiClientV2.get<AgentProfile>(`${base(oid)}/profile`),
  updateProfile: (oid: string, body: AgentProfileUpdate) =>
    apiClientV2.put<AgentProfile>(`${base(oid)}/profile`, body),
  capabilities: (oid: string) => apiClientV2.get<AgentCapabilities>(`${base(oid)}/capabilities`),
  graph: (oid: string, params: {
    depth?: 1 | 2 | 3
    query?: string
    objectType?: string
    focusInstanceId?: string
    limitPerType?: number
  }) => apiClientV2.get<AgentGraphData>(`${base(oid)}/graph`, {
    params: {
      depth: params.depth,
      query: params.query,
      object_type: params.objectType,
      focus_instance_id: params.focusInstanceId,
      limit_per_type: params.limitPerType,
    },
  }),
  graphInstance: (oid: string, instanceId: string) =>
    apiClientV2.get<AgentInstanceDetail>(`${base(oid)}/graph/instances/${encodeURIComponent(instanceId)}`),
  findPaths: (oid: string, body: {
    sourceInstanceId: string
    targetInstanceId: string
    direction?: 'both' | 'outgoing' | 'incoming'
    maxDepth?: number
    maxPaths?: number
  }) => apiClientV2.post<AgentGraphPathResult>(`${base(oid)}/graph/paths`, body),
  analyzeImpact: (oid: string, body: {
    instanceId: string
    property: string
    proposedValue: unknown
    direction?: 'both' | 'outgoing' | 'incoming'
    maxDepth?: number
  }) => apiClientV2.post<AgentGraphImpactResult>(`${base(oid)}/graph/impact`, body),
  conversations: (oid: string) => apiClientV2.get<AgentConversationDTO[]>(`${base(oid)}/conversations`),
  conversation: (oid: string, cid: string) =>
    apiClientV2.get<AgentConversationDTO>(`${base(oid)}/conversations/${cid}`),
  deleteConversation: (oid: string, cid: string) =>
    apiClientV2.delete(`${base(oid)}/conversations/${cid}`),
  executeProposal: (oid: string, body: { actionId: string; parameters: Record<string, unknown>; targetInstanceId?: string | null }) =>
    apiClientV2.post<ExecuteProposalResult>(`${base(oid)}/execute-proposal`, body),
  reportTemplates: (oid: string) =>
    apiClientV2.get<AnalysisReportTemplate[]>(`${base(oid)}/report-templates`),
  createReportTemplate: (oid: string, body: { brief: string; modelId?: string | null; conversationId?: string | null }) =>
    apiClientV2.post<AnalysisReportTemplate>(`${base(oid)}/report-templates/ai-draft`, body),
  reportTemplate: (oid: string, templateId: string) =>
    apiClientV2.get<AnalysisReportTemplate>(`${base(oid)}/report-templates/${templateId}`),
  updateReportTemplate: (oid: string, templateId: string, body: {
    expectedRevision: number; name: string; description: string; sections: ReportSection[];
    style: Record<string, unknown>; defaultModelId?: string | null
  }) => apiClientV2.put<AnalysisReportTemplate>(`${base(oid)}/report-templates/${templateId}`, body),
  deleteReportTemplate: (oid: string, templateId: string) =>
    apiClientV2.delete(`${base(oid)}/report-templates/${templateId}`),
  previewReportTemplate: (oid: string, templateId: string, modelId?: string | null) =>
    apiClientV2.post<AnalysisReportRun>(`${base(oid)}/report-templates/${templateId}/preview`, { modelId }),
  publishReportTemplate: (oid: string, templateId: string) =>
    apiClientV2.post<AnalysisReportTemplate>(`${base(oid)}/report-templates/${templateId}/publish`, {}),
  runReportTemplate: (oid: string, templateId: string, modelId?: string | null) =>
    apiClientV2.post<AnalysisReportRun>(`${base(oid)}/report-templates/${templateId}/runs`, { modelId }),
  reportRuns: (oid: string, templateId: string) =>
    apiClientV2.get<AnalysisReportRun[]>(`${base(oid)}/report-templates/${templateId}/runs`),
  reportRun: (oid: string, runId: string) =>
    apiClientV2.get<AnalysisReportRun>(`${base(oid)}/report-runs/${runId}`),
}

export function reportHtmlUrl(oid: string, runId: string): string {
  return `${apiRoot()}${base(oid)}/report-runs/${runId}/html`
}

// ---------- SSE 流式 chat ----------

function apiRoot(): string {
  const runtimeBase = (typeof window !== 'undefined' && (window as any).__API_BASE_URL__) || ''
  return `${runtimeBase}/api/v2`
}

export async function streamAgentChat(
  oid: string,
  body: { message: string; conversationId?: string | null; modelId?: string | null },
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('token') || ''
  const resp = await fetch(`${apiRoot()}${base(oid)}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      message: body.message,
      conversationId: body.conversationId || undefined,
      modelId: body.modelId || undefined,
      stream: true,
    }),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '')
    throw new Error(`对话请求失败 (${resp.status}) ${text.slice(0, 200)}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 事件以空行分隔
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as AgentEvent)
        } catch { /* 忽略无法解析的行 */ }
      }
    }
  }
}
