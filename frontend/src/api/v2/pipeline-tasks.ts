import { apiClientV2 } from '../client'

export type WriteMode = 'overwrite' | 'append' | 'upsert' | 'append_dedup'
export type PipelineTaskScheduleType = 'MANUAL' | 'CRON' | 'INTERVAL'
export type PipelineTaskStatus = 'idle' | 'running' | 'success' | 'failed'

export interface PipelineTask {
  id: string
  name: string
  description: string
  pipeline_id: string
  pipeline_name?: string
  pipeline_status?: string
  pipeline_version?: number | null
  write_mode: WriteMode
  primary_key: string
  soft_delete_column: string
  skip_empty: boolean
  schedule_type: PipelineTaskScheduleType
  cron_expression: string
  interval_seconds: number
  enabled: boolean
  status: PipelineTaskStatus
  last_run_at: string | null
  last_rows: number
  last_error: string
  created_at: string | null
  updated_at: string | null
}

export interface PipelineTaskRun {
  id: string
  status: string
  trigger_type: string
  started_at: string | null
  finished_at: string | null
  rows_in: number
  rows_out: number
  lake_rows: number | null
  write_mode: string | null
  skipped_outputs: Array<Record<string, unknown>> | null
  curated_dataset_ids: string[]
  error_message: string
}

export interface PipelineTaskStats {
  total: number
  running: number
  enabled: number
  failed: number
  today_runs: number
}

export interface PipelineTaskPayload {
  name: string
  description?: string
  pipeline_id: string
  write_mode: WriteMode
  primary_key?: string
  soft_delete_column?: string
  skip_empty?: boolean
  schedule_type: PipelineTaskScheduleType
  cron_expression?: string
  interval_seconds?: number
  enabled?: boolean
}

export const pipelineTasksApi = {
  list: (params?: Record<string, unknown>): Promise<{ total: number; items: PipelineTask[] }> =>
    apiClientV2.get('/pipeline-tasks', { params }),

  get: (id: string): Promise<PipelineTask> =>
    apiClientV2.get(`/pipeline-tasks/${id}`),

  create: (payload: PipelineTaskPayload): Promise<PipelineTask> =>
    apiClientV2.post('/pipeline-tasks', payload),

  update: (id: string, payload: Partial<PipelineTaskPayload>): Promise<PipelineTask> =>
    apiClientV2.put(`/pipeline-tasks/${id}`, payload),

  delete: (id: string): Promise<{ status: string }> =>
    apiClientV2.delete(`/pipeline-tasks/${id}`),

  toggle: (id: string, enabled: boolean): Promise<PipelineTask> =>
    apiClientV2.post(`/pipeline-tasks/${id}/toggle`, null, { params: { enabled } }),

  trigger: (id: string, sync = false): Promise<Record<string, unknown>> =>
    apiClientV2.post(`/pipeline-tasks/${id}/trigger`, null, { params: { sync } }),

  histories: (id: string, page = 1, page_size = 20): Promise<{ total: number; items: PipelineTaskRun[] }> =>
    apiClientV2.get(`/pipeline-tasks/${id}/histories`, { params: { page, page_size } }),

  stats: (): Promise<PipelineTaskStats> =>
    apiClientV2.get('/pipeline-tasks/stats'),
}

export const WRITE_MODE_META: Record<WriteMode, { label: string; desc: string }> = {
  overwrite:    { label: '全量覆盖', desc: '资产 = 本次流水线输出，先清空后全量写入' },
  append:       { label: '直接追加', desc: '本次输出直接追加到资产尾部' },
  upsert:       { label: '主键合并', desc: '按主键去重保留最新，可选软删除标记' },
  append_dedup: { label: '去重追加', desc: '按整行内容去重后追加，无主键防重复导入' },
}
