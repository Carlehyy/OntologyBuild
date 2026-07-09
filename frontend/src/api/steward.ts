/**
 * 数据管家 API — /api/v2/steward/*
 *
 * chat 走 SSE 流式（fetch + ReadableStream 手动解析，axios 不支持流），
 * 其余走 apiClientV2。
 */
import { apiClientV2 } from './client'

// ---------- 类型 ----------

/** 治理记录自身状态：在管 / 已归档。发布状态见 pipelineStatus（影子流水线） */
export type StewardPipelineStatus = 'draft' | 'archived'

export interface StewardWorkflowSummary {
  node_count: number
  nodes: { name: string; type: string; disabled?: boolean }[]
  has_trigger: boolean
  webhook_path: string | null
}

export interface StewardPipeline {
  id: string
  name: string
  description: string
  n8nWorkflowId: string
  status: StewardPipelineStatus
  pipelineId: string | null
  /** 发布状态（生命周期唯一真源=影子流水线）：发布/撤回只在流水线编辑向导 */
  pipelineStatus: 'draft' | 'published'
  conversationId: string | null
  summary: StewardWorkflowSummary
  createdAt: string | null
  updatedAt: string | null
  active?: boolean | null
}

export interface StewardPipelineDetail extends StewardPipeline {
  workflow?: Record<string, unknown> | null
  executions?: { id: string; status: string; startedAt: string | null; stoppedAt: string | null }[]
  n8nError?: string
}

export interface StewardStatus {
  n8n: { configured: boolean; enabled: boolean; api_url: string; reachable?: boolean; error?: string }
  llmReady: boolean
  pipelineCounts: Record<string, number>
}

export interface StewardStep {
  tool: string
  arguments: Record<string, unknown>
  summary: string
  durationMs: number
  error?: string
}

export interface StewardMessageDTO {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: StewardStep[]
  touchedPipelineIds: string[]
  model?: string | null
  createdAt: string
}

export interface StewardConversationDTO {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messages?: StewardMessageDTO[]
}

export interface TestRunResult {
  rows: number
  columns: string[]
  sample: Record<string, unknown>[]
  execution: Record<string, unknown>
  error?: string | null
}

export type StewardEvent =
  | { type: 'meta'; conversationId: string; model: string }
  | ({ type: 'step' } & StewardStep)
  | { type: 'answer'; content: string; touchedPipelineIds: string[]; usage?: unknown }
  | { type: 'error'; message: string }
  | { type: 'done' }

// ---------- REST ----------

export const stewardApi = {
  status: () => apiClientV2.get<StewardStatus>('/steward/status'),
  conversations: () => apiClientV2.get<StewardConversationDTO[]>('/steward/conversations'),
  conversation: (cid: string) =>
    apiClientV2.get<StewardConversationDTO>(`/steward/conversations/${cid}`),
  deleteConversation: (cid: string) =>
    apiClientV2.delete(`/steward/conversations/${cid}`),

  pipelines: () => apiClientV2.get<StewardPipeline[]>('/steward/pipelines'),
  pipeline: (id: string) => apiClientV2.get<StewardPipelineDetail>(`/steward/pipelines/${id}`),
  /** 列表页新建 n8n 流水线：后台自动在 n8n 创建骨架工作流并纳管（未发布） */
  bootstrap: (name: string, description = '') =>
    apiClientV2.post<{ record: StewardPipeline }>('/steward/pipelines/bootstrap', { name, description }),
  testRun: (id: string, payload?: Record<string, unknown>) =>
    apiClientV2.post<TestRunResult>(`/steward/pipelines/${id}/test-run`, { payload }),
  archive: (id: string, deleteWorkflow = false) =>
    apiClientV2.delete(`/steward/pipelines/${id}`, { params: { delete_workflow: deleteWorkflow } }),
}

// ---------- SSE 流式 chat ----------

function apiRoot(): string {
  const runtimeBase = (typeof window !== 'undefined' && (window as any).__API_BASE_URL__) || ''
  return `${runtimeBase}/api/v2`
}

export async function streamStewardChat(
  body: { message: string; conversationId?: string | null; modelId?: string | null },
  onEvent: (e: StewardEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('token') || ''
  const resp = await fetch(`${apiRoot()}/steward/chat`, {
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
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as StewardEvent)
        } catch { /* 忽略无法解析的行 */ }
      }
    }
  }
}
