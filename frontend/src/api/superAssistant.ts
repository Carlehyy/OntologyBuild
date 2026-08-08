import { apiClientV2 } from '@/api/client'

export interface SuperConversation {
  id: string
  title: string
  model_config_id: string | null
  status: string
  created_at: string
  updated_at: string
}

export interface SuperMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  status: 'streaming' | 'complete' | 'cancelled' | 'error'
  steps: ToolStep[]
  token_usage: Record<string, number>
  created_at: string
}

export interface ToolStep {
  toolName: string
  status: string
  arguments?: Record<string, unknown>
  preview?: string
}

export interface SkillFile {
  path: string
  size: number
  editable: boolean
}

export interface SuperSkill {
  id: string
  name: string
  description: string
  manifest: SkillFile[]
  enabled: boolean
  revision: number
  created_at: string
  updated_at: string
}

export interface McpTool {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export type McpTransport = 'stdio' | 'sse' | 'streamable_http'

export interface SuperMcpServer {
  id: string
  name: string
  builtin_key: string | null
  transport: McpTransport
  url: string
  header_names: string[]
  command: string | null
  args: string[]
  env_names: string[]
  enabled: boolean
  require_confirmation: boolean
  tool_manifest: McpTool[]
  last_test_status: 'success' | 'error' | null
  last_test_message: string | null
  last_tested_at: string | null
  created_at: string
  updated_at: string
}

export type StreamEventName =
  | 'meta' | 'thinking' | 'text_delta' | 'tool_start'
  | 'tool_confirmation_required' | 'tool_result' | 'message_end'
  | 'cancelled' | 'error' | 'done'

export interface StreamEvent {
  event: StreamEventName
  data: Record<string, any>
}

const pathPart = (path: string) => path.split('/').map(encodeURIComponent).join('/')

const runtimeApiBase = () => {
  const injected = typeof window !== 'undefined' ? (window as any).__API_BASE_URL__ || '' : ''
  return `${injected}/api/v2`
}

const streamChat = async (
  conversationId: string,
  body: { message: string; model_config_id?: string | null },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
) => {
  const token = localStorage.getItem('token')
  const response = await fetch(`${runtimeApiBase()}/super-assistant/conversations/${conversationId}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const payload = await response.json()
      message = payload.detail || payload.message || message
    } catch { /* keep HTTP status */ }
    throw new Error(message)
  }
  if (!response.body) throw new Error('浏览器未提供流式响应体')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const dispatch = (block: string) => {
    let event: StreamEventName = 'done'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim() as StreamEventName
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    try {
      onEvent({ event, data: JSON.parse(dataLines.join('\n')) })
    } catch {
      onEvent({ event: 'error', data: { message: '无法解析服务端流式事件' } })
    }
  }
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let split = buffer.indexOf('\n\n')
    while (split >= 0) {
      dispatch(buffer.slice(0, split))
      buffer = buffer.slice(split + 2)
      split = buffer.indexOf('\n\n')
    }
    if (done) break
  }
  if (buffer.trim()) dispatch(buffer)
}

export const superAssistantApi = {
  conversations: () => apiClientV2.get<SuperConversation[]>('/super-assistant/conversations'),
  createConversation: (body: { title?: string; model_config_id?: string | null } = {}) =>
    apiClientV2.post<SuperConversation>('/super-assistant/conversations', body),
  updateConversation: (id: string, body: { title?: string; model_config_id?: string | null }) =>
    apiClientV2.patch<SuperConversation>(`/super-assistant/conversations/${id}`, body),
  deleteConversation: (id: string) => apiClientV2.delete(`/super-assistant/conversations/${id}`),
  messages: (id: string) => apiClientV2.get<SuperMessage[]>(`/super-assistant/conversations/${id}/messages`),
  streamChat,
  cancel: (id: string) => apiClientV2.post(`/super-assistant/conversations/${id}/cancel`),
  decideToolRun: (id: string, decision: 'approve' | 'deny') =>
    apiClientV2.post(`/super-assistant/tool-runs/${id}/decision`, { decision }),

  skills: () => apiClientV2.get<SuperSkill[]>('/super-assistant/skills'),
  createSkill: (body: {
    name: string; description: string; content: string; enabled: boolean
  }) => apiClientV2.post<SuperSkill>('/super-assistant/skills', body),
  updateSkill: (id: string, body: Partial<Pick<SuperSkill, 'enabled'>>) =>
    apiClientV2.patch<SuperSkill>(`/super-assistant/skills/${id}`, body),
  deleteSkill: (id: string) => apiClientV2.delete(`/super-assistant/skills/${id}`),
  importSkill: (archive: File) => {
    const form = new FormData()
    form.append('archive', archive)
    return apiClientV2.post<SuperSkill>('/super-assistant/skills/import', form)
  },
  skillFiles: (id: string) => apiClientV2.get<SkillFile[]>(`/super-assistant/skills/${id}/files`),
  skillFile: (id: string, path: string) =>
    apiClientV2.get<{ path: string; content: string }>(`/super-assistant/skills/${id}/files/${pathPart(path)}`),
  putSkillFile: (id: string, path: string, content: string) =>
    apiClientV2.put(`/super-assistant/skills/${id}/files/${pathPart(path)}`, { content }),
  deleteSkillFile: (id: string, path: string) =>
    apiClientV2.delete(`/super-assistant/skills/${id}/files/${pathPart(path)}`),

  mcpServers: () => apiClientV2.get<SuperMcpServer[]>('/super-assistant/mcp-servers'),
  createMcpServer: (body: {
    name: string; transport: McpTransport; url: string; headers: Record<string, string>;
    command?: string | null; args?: string[]; env?: Record<string, string>;
    enabled: boolean; require_confirmation: boolean
  }) => apiClientV2.post<SuperMcpServer>('/super-assistant/mcp-servers', body),
  updateMcpServer: (id: string, body: Partial<{
    transport: McpTransport; url: string; headers: Record<string, string>;
    command: string | null; args: string[]; env: Record<string, string>;
    enabled: boolean; require_confirmation: boolean
  }>) => apiClientV2.patch<SuperMcpServer>(`/super-assistant/mcp-servers/${id}`, body),
  deleteMcpServer: (id: string) => apiClientV2.delete(`/super-assistant/mcp-servers/${id}`),
  testMcpServer: (id: string) => apiClientV2.post<{ ok: boolean; message: string; tools: McpTool[] }>(
    `/super-assistant/mcp-servers/${id}/test`,
  ),
}
