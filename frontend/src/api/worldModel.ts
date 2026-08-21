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
  /** 服务状态：null=未发布（草稿）；online=在线；offline=已下线 */
  service_status: 'online' | 'offline' | null
  /** 已发布服务摘要（未发布或详情接口为 null/缺省）：卡片服务快捷入口与删除影响提示 */
  service_name?: string | null
  service_endpoint?: string | null
  service_version_no?: number | null
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

// ---------- 推演服务（发布 / 状态 / 调用） ----------

export interface ServicePrecondition {
  object_type_id: string
  min_count: number
}

export interface WorldModelServiceInfo {
  id: string
  project_id: string
  version_id: string | null
  version_no: number | null
  name: string
  description: string
  status: 'online' | 'offline' | string
  endpoint_path: string | null
  applicable_object_types: {
    ontology_id: string
    object_type_ids: string[]
  } | null
  preconditions: ServicePrecondition[] | null
  created_at: string | null
  updated_at: string | null
}

export interface ServicePublishBody {
  version_id?: string | null
  name: string
  description: string
  applicable_ontology_id: string
  applicable_object_type_ids: string[]
  preconditions: ServicePrecondition[]
}

/** 推演服务注册表条目（跨项目列表） */
export interface WorldModelServiceSummary {
  id: string
  project_id: string
  project_name: string
  version_id: string | null
  version_no: number | null
  name: string
  description: string
  status: 'online' | 'offline' | string
  endpoint_path: string | null
  applicable_object_types: {
    ontology_id: string
    object_type_ids: string[]
  } | null
  preconditions: ServicePrecondition[] | null
  call_count: number
  failed_count: number
  created_at: string | null
  updated_at: string | null
}

export interface ServiceListResponse {
  items: WorldModelServiceSummary[]
  total: number
}

/** 服务试调用结果（invoke 端点返回口径） */
export interface ServiceInvokeResult {
  ok: boolean
  payload: unknown
  error: string | null
  duration_ms: number
  call_id: string | null
}

/** 官方脚本模板（唯一事实源在后端，前端不复制脚本副本） */
export interface WorldModelTemplate {
  key: string
  name: string
  description: string
  script: string
  test_input: Record<string, unknown>
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
    service_id?: string
    start?: string
    end?: string
    page?: number
    size?: number
  }) => {
    // 空值参数（如 start/end 未设置时的空串）不得发出：
    // 后端 datetime 参数收到空串会 422
    const query = Object.fromEntries(
      Object.entries(params).filter(([, value]) => value !== '' && value !== undefined),
    )
    return apiClientV2.get<CallRecordListResponse>('/world-model/calls', { params: query })
  },

  callsOverview: () =>
    apiClientV2.get<CallRecordOverview>('/world-model/calls/overview'),

  getCall: (id: string) =>
    apiClientV2.get<CallRecordDetail>(`/world-model/calls/${id}`),

  // ---------- 推演服务 ----------

  getService: (projectId: string) =>
    apiClientV2.get<WorldModelServiceInfo | null>(`/world-model/projects/${projectId}/service`),

  publishService: (projectId: string, body: ServicePublishBody) =>
    apiClientV2.post<WorldModelServiceInfo>(`/world-model/projects/${projectId}/publish`, body),

  setServiceStatus: (projectId: string, status: 'online' | 'offline') =>
    apiClientV2.post<WorldModelServiceInfo>(`/world-model/projects/${projectId}/service/status`, { status }),

  invokeService: (serviceId: string, body: { context: Record<string, unknown>; actions: unknown[]; horizon: number }) =>
    apiClientV2.post<ServiceInvokeResult>(`/world-model/services/${serviceId}/invoke`, body),

  // ---------- 推演服务注册表（跨项目） ----------

  listServices: (params: { keyword?: string; status?: string; page?: number; size?: number }) =>
    apiClientV2.get<ServiceListResponse>('/world-model/services', { params }),

  getServiceById: (serviceId: string) =>
    apiClientV2.get<WorldModelServiceSummary>(`/world-model/services/${serviceId}`),

  setServiceStatusById: (serviceId: string, status: 'online' | 'offline') =>
    apiClientV2.post<WorldModelServiceSummary>(`/world-model/services/${serviceId}/status`, { status }),

  // ---------- 官方脚本模板 ----------

  getTimeSeriesTemplate: () =>
    apiClientV2.get<WorldModelTemplate>('/world-model/templates/time-series'),
}

export default worldModelApi
