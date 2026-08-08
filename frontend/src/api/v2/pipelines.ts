import { apiClientV2 } from '@/api/client'

/** definition.python — Python 脚本流水线保存在定义里的脚本与元数据 */
export interface PythonScriptDefinition {
  script?: string
  saved_at?: string
  output_columns?: string[]
}

/** definition 除画布 nodes/edges 外，还承载引擎标记与引擎自有配置 */
export interface PipelineDefinition {
  nodes: unknown[]
  edges: unknown[]
  engine?: string
  n8n?: Record<string, unknown>
  python?: PythonScriptDefinition
}

/** 流水线引擎：canvas=旧版系统流水线 / n8n=数据管家托管 / python=Python 脚本 */
export function getPipelineEngine(pl: Pick<Pipeline, 'definition'>): 'n8n' | 'python' | 'canvas' {
  const engine = (pl.definition as PipelineDefinition | null)?.engine
  return engine === 'n8n' ? 'n8n' : engine === 'python' ? 'python' : 'canvas'
}

export interface Pipeline {
  id: string
  name: string
  domain?: string
  description?: string
  source_dataset_id?: string | null
  route?: string | null
  spec?: Record<string, unknown>
  definition?: PipelineDefinition | null
  column_definitions?: ColumnDefinition[] | null  // [{field_key, field_name, field_type, is_primary_key, nullable}]
  status: string
  engine?: string          // canvas=系统自定义 / n8n=数据管家托管 / python=Python 脚本
  enabled?: boolean        // 启用开关：停用后任务池/链式触发不执行
  branch?: string
  version?: number
  target_curated_ids?: string[]
  task_count?: number       // 数据任务池中的关联任务数（不区分任务启用状态）
  created_at?: string | null
  updated_at?: string | null
  last_run_status?: string
  last_run_at?: string | null
  last_run_error?: string
}

export interface PipelinePage {
  items: Pipeline[]
  total: number
  page: number
  page_size: number
}

/** 字段契约——流水线产出列的入湖元数据（发布后封版） */
export interface ColumnDefinition {
  source_key: string      // 原始列名（流水线输出的列，只读）
  field_key: string       // 入湖列名（字段标识，可改名；须字母/下划线开头）
  field_name: string      // 字段显示名称
  field_type: string      // string | integer | float | boolean | timestamp | json
  is_primary_key: boolean // 是否主键
  nullable: boolean       // 是否允许为空
}

/** 平台类型词表——与资产湖 columns_typed 推断词表一致 */
export const CONTRACT_FIELD_TYPES = ['string', 'integer', 'float', 'boolean', 'timestamp', 'json'] as const

export interface DryRunOutput {
  dataset_name: string
  dataset_exists: boolean
  rows_out: number
  columns: string[]
  sample: Array<Record<string, unknown>>
  gate_error: string | null
  pk: string
  pk_source: string
  warnings: string[]
  drift: { added: string[]; missing: string[] } | null
}

export interface DryRunResult {
  dry_run_id: string
  engine: string
  rows_in: number
  rows_out: number
  outputs: DryRunOutput[]
}

/** 试运行暂存数据的分页读取结果 */
export interface DryRunRowsPage {
  total: number
  page: number
  page_size: number
  columns: string[]
  rows: Array<Record<string, unknown>>
}

export interface PipelineCreateBody {
  name: string
  domain?: string
  description?: string
  source_dataset_id?: string | null
  route?: string | null
  spec?: Record<string, unknown>
  definition?: PipelineDefinition | null
  column_definitions?: ColumnDefinition[] | null
}

/** Python 脚本执行结果（脚本级失败以 ok=false 承载，不是 HTTP 错误） */
export interface ScriptExecutionResult {
  ok: boolean
  format_valid: boolean
  format_error: string | null
  row_count: number
  columns: string[]
  sample: Array<Record<string, unknown>>
  stdout: string
  error: string | null
  traceback: string
  duration_ms: number
}

/** 保存脚本 = 服务端重跑复验通过后才落库；返回更新后的流水线与执行摘要 */
export interface ScriptSaveResult {
  pipeline: Pipeline
  execution: ScriptExecutionResult
}

export interface PipelineRunItem {
  id: string
  status: string
  stats?: Record<string, unknown> | null
  started_at: string | null
  finished_at: string | null
}

export interface RunDetail {
  id: string
  status: string
  stats: Record<string, unknown> | null
  error_log: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ValidateResult {
  valid: boolean
  errors: Array<{ node_id: string; severity: string; message: string }>
  warnings: Array<{ node_id: string; severity: string; message: string }>
}

/** 字段定义校验 */
export interface ValidateDefinitionsBody {
  column_definitions: ColumnDefinition[]
}

export interface ValidateDefinitionsResult {
  valid: boolean
  errors: Array<{ field_key: string; message: string; severity: string }>
}

const pipelinesApi = {
  /** Pipeline CRUD */
  list: (params?: { search?: string; domain?: string; status?: string }) =>
    apiClientV2.get<Pipeline[]>('/pipelines', { params }).then(r => r),
  listPage: (params: {
    search?: string
    domain?: string
    status?: string
    engine?: string
    enabled?: boolean
    page?: number
    page_size?: number
  }) => apiClientV2.get<PipelinePage>('/pipelines', {
    params: { ...params, paginated: true },
  }).then(r => r),
  get: (id: string) =>
    apiClientV2.get<Pipeline>(`/pipelines/${id}`).then(r => r),
  create: (body: PipelineCreateBody) =>
    apiClientV2.post<Pipeline>('/pipelines', body).then(r => r),
  // 发布是单向封版；启用经 setEnabled，通用 update 不接受 status/enabled
  update: (id: string, body: Partial<PipelineCreateBody>) =>
    apiClientV2.put<Pipeline>(`/pipelines/${id}`, body).then(r => r),
  delete: (id: string) =>
    apiClientV2.delete(`/pipelines/${id}`).then(r => r),

  /** Validate & Publish */
  validate: (id: string) =>
    apiClientV2.post<ValidateResult>(`/pipelines/${id}/validate`).then(r => r),
  /** 发布（封版）；enable=true 同时启用 */
  publish: (id: string, enable = false) =>
    apiClientV2.post<{ id: string; status: string; version: number; enabled: boolean }>(`/pipelines/${id}/publish`, { enable }).then(r => r),
  versions: (id: string) =>
    apiClientV2.get<Array<{ id: string; version: number; status: string; created_at: string | null }>>(`/pipelines/${id}/versions`).then(r => r),

  /** 字段契约校验：基于第 2 步 dry-run 暂存的完整数据做全量校验（不重跑流水线） */
  validateDefinitions: (id: string, body: ValidateDefinitionsBody, dryRunId: string) =>
    apiClientV2.post<ValidateDefinitionsResult>(`/pipelines/${id}/validate-definitions`, body, { params: { dry_run_id: dryRunId } }).then(r => r),

  /** 启用开关：停用后任务池调度/链式触发不执行（手动试运行不受限） */
  setEnabled: (id: string, enabled: boolean) =>
    apiClientV2.patch<Pipeline>(`/pipelines/${id}/enabled`, { enabled }).then(r => r),

  /** 试运行：真实执行但不写资产湖，返回产物预览与契约诊断 */
  dryRun: (id: string, maxRows?: number) =>
    apiClientV2.post<DryRunResult>(`/pipelines/${id}/dry-run`, null, { params: { max_rows: maxRows ?? 100 } }).then(r => r),
  /** 分页读取试运行暂存的完整输出（「展开查看全部数据」） */
  dryRunRows: (id: string, dryRunId: string, params?: { output_index?: number; page?: number; page_size?: number }) =>
    apiClientV2.get<DryRunRowsPage>(`/pipelines/${id}/dry-run/${dryRunId}/rows`, { params }).then(r => r),

  /** Python 脚本：执行编辑器当前内容（不落库），返回结果与行格式校验结论 */
  executeScript: (id: string, script: string) =>
    apiClientV2.post<ScriptExecutionResult>(`/pipelines/${id}/script/execute`, { script }).then(r => r),
  /** Python 脚本：保存（服务端重跑复验，输出格式合法才落库） */
  saveScript: (id: string, script: string) =>
    apiClientV2.put<ScriptSaveResult>(`/pipelines/${id}/script`, { script }).then(r => r),

  /** Runs */
  run: (id: string) =>
    apiClientV2.post<{ run_id: string; status: string }>(`/pipelines/${id}/run`).then(r => r),
  runSync: (id: string) =>
    apiClientV2.post<{ run_id: string; status: string; stats?: Record<string, unknown> }>(`/pipelines/${id}/run-sync`).then(r => r),
  runs: (id: string) =>
    apiClientV2.get<PipelineRunItem[]>(`/pipelines/${id}/runs`).then(r => r),
  getRun: (runId: string) =>
    apiClientV2.get<RunDetail>(`/pipelines/runs/${runId}`).then(r => r),
}

export default pipelinesApi
