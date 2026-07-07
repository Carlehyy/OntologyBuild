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

// ---------- REST ----------

const base = (oid: string) => `/formal/ontologies/${oid}/agent`

export const agentApi = {
  getProfile: (oid: string) => apiClientV2.get<AgentProfile>(`${base(oid)}/profile`),
  updateProfile: (oid: string, body: AgentProfileUpdate) =>
    apiClientV2.put<AgentProfile>(`${base(oid)}/profile`, body),
  capabilities: (oid: string) => apiClientV2.get<AgentCapabilities>(`${base(oid)}/capabilities`),
  conversations: (oid: string) => apiClientV2.get<AgentConversationDTO[]>(`${base(oid)}/conversations`),
  conversation: (oid: string, cid: string) =>
    apiClientV2.get<AgentConversationDTO>(`${base(oid)}/conversations/${cid}`),
  deleteConversation: (oid: string, cid: string) =>
    apiClientV2.delete(`${base(oid)}/conversations/${cid}`),
  executeProposal: (oid: string, body: { actionId: string; parameters: Record<string, unknown>; targetInstanceId?: string | null }) =>
    apiClientV2.post<ExecuteProposalResult>(`${base(oid)}/execute-proposal`, body),
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
