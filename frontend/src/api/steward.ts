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
  connections: Record<string, Record<string, { node: string; type: string; index: number }[][]>>
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

export interface StewardArtifact {
  id: string
  filename: string
  source: 'upload' | 'download' | string
  sourceUrl?: string | null
  mimeType: string
  size: number
  sha256: string
  extractedChars: number
  extractError?: string | null
  urls: string[]
  createdAt: string
}

export interface BrowserCapture {
  id: string
  method: string
  url: string
  resourceType: string
  status: number
  contentType: string
  responseShape?: unknown
  responsePreview?: string
  pagination?: { mode: string; requestParams: Record<string, string>; responseFields: Record<string, unknown> } | null
  isApi: boolean
  isFile: boolean
  capturedAt: number
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
  createConversation: (title = '新对话') =>
    apiClientV2.post<StewardConversationDTO>('/steward/conversations', { title }),
  files: (cid: string) =>
    apiClientV2.get<StewardArtifact[]>(`/steward/conversations/${cid}/files`),
  uploadFile: (cid: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return apiClientV2.post<StewardArtifact>(`/steward/conversations/${cid}/files`, form)
  },
  deleteFile: (cid: string, artifactId: string) =>
    apiClientV2.delete(`/steward/conversations/${cid}/files/${artifactId}`),
  browserStart: (cid: string, url: string) =>
    apiClientV2.post<{ url: string; title: string }>(`/steward/conversations/${cid}/browser/start`, { url }),
  browserNavigate: (cid: string, url: string) =>
    apiClientV2.post<{ url: string; title: string }>(`/steward/conversations/${cid}/browser/navigate`, { url }),
  browserTicket: (cid: string) =>
    apiClientV2.post<{ ticket: string; expiresIn: number }>(`/steward/conversations/${cid}/browser/ticket`),
  browserCaptures: (cid: string, keyword = '') =>
    apiClientV2.get<BrowserCapture[]>(`/steward/conversations/${cid}/browser/captures`, { params: { keyword, limit: 100 } }),
  downloadCapture: (cid: string, captureId: string) =>
    apiClientV2.post<StewardArtifact>(`/steward/conversations/${cid}/browser/captures/${captureId}/download`),

  pipelines: () => apiClientV2.get<StewardPipeline[]>('/steward/pipelines'),
  pipeline: (id: string) => apiClientV2.get<StewardPipelineDetail>(`/steward/pipelines/${id}`),
  /** 列表页 / 数据管家「新建 n8n 流水线」：后台自动在 n8n 创建骨架工作流并登记（未发布） */
  bootstrap: (name: string, description = '') =>
    apiClientV2.post<{ record: StewardPipeline }>('/steward/pipelines/bootstrap', { name, description }),
}

export async function downloadStewardFile(
  cid: string, artifactId?: string, filename = 'session-files.zip',
): Promise<void> {
  const token = localStorage.getItem('token') || ''
  const path = artifactId
    ? `/steward/conversations/${cid}/files/${artifactId}`
    : `/steward/conversations/${cid}/archive`
  const resp = await fetch(`${apiRoot()}${path}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!resp.ok) throw new Error(`下载失败 (${resp.status})`)
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// ---------- SSE 流式 chat ----------

function apiRoot(): string {
  const runtimeWindow = typeof window !== 'undefined'
    ? window as Window & { __API_BASE_URL__?: string }
    : undefined
  const runtimeBase = runtimeWindow?.__API_BASE_URL__ || ''
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
