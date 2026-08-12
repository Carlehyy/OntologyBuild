/**
 * 世界模型 API — /api/v2/world-model
 *
 * 推演模型项目管理、脚本调试执行/保存/版本，以及调用记录（只读）。
 * 字段命名对齐数据通道脚本执行接口（snake_case），apiClientV2 已解包 {data} 信封。
 */
import { apiClientV2 } from './client'

// ---------- 类型 ----------

export type EngineType = 'statistical' | 'mechanistic' | 'state_machine' | 'learned'

export interface WorldModelProjectSummary {
  id: string
  name: string
  description: string
  engine_type: EngineType
  status: string
  version_count: number
  created_at: string | null
  updated_at: string | null
}

export interface WorldModelProjectDetail extends WorldModelProjectSummary {
  script: string
}

export interface ProjectListResponse {
  items: WorldModelProjectSummary[]
  total: number
}

export interface TestInput {
  context?: Record<string, unknown>
  actions?: unknown[]
  horizon?: number
}

export interface ScriptExecutionResult {
  ok: boolean
  payload: unknown
  stdout: string
  error: string | null
  traceback: string
  duration_ms: number
  kernel_id: string
}

export interface ScriptSaveResult {
  ok: boolean
  execution: ScriptExecutionResult
  version_no: number | null
}

export interface ScriptVersionItem {
  id: string
  version_no: number
  test_input: TestInput | null
  duration_ms: number
  created_by: string | null
  created_at: string | null
}

export interface ScriptVersionDetail extends ScriptVersionItem {
  script: string
}

export interface CallRecordItem {
  id: string
  project_id: string | null
  service_name: string
  caller: string
  ok: boolean
  duration_ms: number
  error: string | null
  created_at: string | null
}

export interface CallRecordDetail extends CallRecordItem {
  request_payload: Record<string, unknown> | null
  response_payload: Record<string, unknown> | null
}

export interface CallRecordListResponse {
  items: CallRecordItem[]
  total: number
}

export interface CallRecordOverview {
  total: number
  failed: number
  avg_duration_ms: number
}

export function apiError(error: unknown): string {
  if (!error || typeof error !== 'object') return '请求失败，请稍后重试'
  const candidate = error as { detail?: unknown; message?: unknown }
  if (typeof candidate.detail === 'string') return candidate.detail
  if (typeof candidate.message === 'string') return candidate.message
  return '请求失败，请稍后重试'
}

// ---------- 项目管理 ----------

export const worldModelApi = {
  listProjects: (params: { keyword?: string; engine_type?: string; page?: number; size?: number }) =>
    apiClientV2.get<ProjectListResponse>('/world-model/projects', { params }),

  createProject: (body: { name: string; description: string; engine_type: EngineType }) =>
    apiClientV2.post<WorldModelProjectDetail>('/world-model/projects', body),

  getProject: (id: string) =>
    apiClientV2.get<WorldModelProjectDetail>(`/world-model/projects/${id}`),

  updateProject: (id: string, body: { name?: string; description?: string; engine_type?: EngineType }) =>
    apiClientV2.patch<WorldModelProjectSummary>(`/world-model/projects/${id}`, body),

  deleteProject: (id: string) =>
    apiClientV2.delete(`/world-model/projects/${id}`),

  // ---------- 开发调试 ----------

  executeScript: (id: string, script: string, testInput: TestInput, signal?: AbortSignal) =>
    apiClientV2.post<ScriptExecutionResult>(
      `/world-model/projects/${id}/execute`,
      { script, test_input: testInput },
      { signal },
    ),

  saveScript: (id: string, script: string, testInput: TestInput) =>
    apiClientV2.post<ScriptSaveResult>(
      `/world-model/projects/${id}/save`,
      { script, test_input: testInput },
    ),

  listVersions: (id: string) =>
    apiClientV2.get<ScriptVersionItem[]>(`/world-model/projects/${id}/versions`),

  getVersion: (id: string, versionId: string) =>
    apiClientV2.get<ScriptVersionDetail>(`/world-model/projects/${id}/versions/${versionId}`),

  // ---------- 调用记录（只读） ----------

  listCalls: (params: {
    keyword?: string
    result?: 'all' | 'failed'
    start?: string
    end?: string
    page?: number
    size?: number
  }) => apiClientV2.get<CallRecordListResponse>('/world-model/calls', { params }),

  callsOverview: () =>
    apiClientV2.get<CallRecordOverview>('/world-model/calls/overview'),

  getCall: (id: string) =>
    apiClientV2.get<CallRecordDetail>(`/world-model/calls/${id}`),
}

export default worldModelApi
