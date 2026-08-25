/**
 * 助手评估 API — /api/v1/assistant-evaluation
 *
 * 基于 OpenJudge 的助手会话质量评估（仅 admin）。
 * apiClient 已解包 {data} 信封。
 */
import { apiClient } from './client'

export interface AssistantMeta {
  key: string
  label: string
  description: string
  conversation_count: number
  supported_dimension_keys: string[]
}

export interface DimensionMeta {
  key: string
  label: string
  kind: 'llm' | 'code'
  description: string
}

export interface EvalMeta {
  engine: string
  assistants: AssistantMeta[]
  dimension_catalog: DimensionMeta[]
  base_dimension_keys: string[]
}

export interface ConversationRef {
  id: string
  title: string
  created_at: string | null
  updated_at: string | null
  message_count: number
}

export interface ConversationPage {
  total: number
  items: ConversationRef[]
}

export type EvalTaskStatus = 'queued' | 'running' | 'success' | 'error'

export interface EvalDimensionStat {
  label: string
  avg: number
  min: number
  max: number
  count: number
}

export interface EvalSummary {
  overall?: number | null
  dimensions: Record<string, EvalDimensionStat>
  badcase_conversation_ids: string[]
  evaluated: number
  failed: number
  /** 无适用维度或内容不完整的会话数（非失败） */
  skipped?: number
  llm_calls: number
  engine: string
}

export interface EvalTask {
  id: string
  assistant_key: string
  assistant_label: string
  title: string
  status: EvalTaskStatus
  params: { mode?: string; dimension_keys?: string[]; conversation_ids?: string[] }
  judge_model_name: string
  conversation_count: number
  completed_conversations: number
  summary: EvalSummary
  error: string | null
  created_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface EvalItemReason {
  score?: number | null
  reason?: string
}

export interface EvalTaskItem {
  id: string
  conversation_id: string
  conversation_title: string
  overall_score: number | null
  scores: Record<string, number>
  reasons: Record<string, EvalItemReason>
  flags: { loop_detected?: boolean; tool_error_count?: number; low_dims?: string[]; engine_error?: string }
  root_cause: string
  created_at: string | null
}

export interface EvalTaskDetail extends EvalTask {
  items: EvalTaskItem[]
}

export interface CreateEvalTaskBody {
  assistant_key: string
  conversation_ids: string[]
  sample_size?: number
  sample_days?: number
  dimension_keys: string[]
  model_config_id?: string | null
}

export const assistantEvaluationApi = {
  meta: () => apiClient.get<EvalMeta>('/assistant-evaluation/meta'),
  conversations: (assistantKey: string, limit = 50, offset = 0) =>
    apiClient.get<ConversationPage>(`/assistant-evaluation/${assistantKey}/conversations`, {
      params: { limit, offset },
    }),
  createTask: (body: CreateEvalTaskBody) =>
    apiClient.post<EvalTask>('/assistant-evaluation/tasks', body),
  tasks: (assistantKey?: string) =>
    apiClient.get<EvalTask[]>('/assistant-evaluation/tasks', {
      params: assistantKey ? { assistant_key: assistantKey } : {},
    }),
  taskDetail: (taskId: string) =>
    apiClient.get<EvalTaskDetail>(`/assistant-evaluation/tasks/${taskId}`),
  deleteTask: (taskId: string) => apiClient.delete(`/assistant-evaluation/tasks/${taskId}`),
  exportReport: async (taskId: string) => {
    // 必须走鉴权请求下载（纯链接不带 Bearer token）
    const blob = (await apiClient.get(`/assistant-evaluation/tasks/${taskId}/export`, {
      responseType: 'blob',
    })) as unknown as Blob
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `assistant-eval-${taskId.slice(0, 8)}.md`
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  },
}
