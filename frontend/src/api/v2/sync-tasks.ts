import { apiClientV2 } from '../client'

export type SyncMode = 'SNAPSHOT' | 'APPEND'
export type ScheduleType = 'MANUAL' | 'CRON' | 'INTERVAL'
export type TaskStatus = 'IDLE' | 'RUNNING' | 'SUCCESS' | 'FAILED'

export interface SyncTask {
  id: string
  name: string
  description: string
  connection_id: string
  connection_name?: string
  source_table: string
  source_query: string
  sync_mode: SyncMode
  primary_key: string
  watermark_column: string
  is_deleted_column: string
  schedule_type: ScheduleType
  cron_expression: string
  interval_seconds: number
  enabled: boolean
  trigger_pipeline_id: string | null
  trigger_pipeline_name?: string
  status: TaskStatus
  last_sync_at: string | null
  last_rows: number
  last_error: string
  last_watermark: string
  created_at: string
  updated_at: string
}

export interface SyncHistory {
  id: string
  task_id: string
  trigger_type: 'MANUAL' | 'SCHEDULED'
  started_at: string
  ended_at: string | null
  duration_ms: number
  status: 'RUNNING' | 'SUCCESS' | 'FAILED'
  source_count: number
  inserted_count: number
  updated_count: number
  deleted_count: number
  error_message: string
  watermark_before: string
  watermark_after: string
  dataset_id: string | null
  dataset_version: number
}

export interface TaskListResponse {
  total: number
  items: SyncTask[]
  page: number
  page_size: number
}

export interface SyncStats {
  total: number
  running: number
  enabled: number
  failed: number
  today_runs: number
  success_rate: number
  mode_distribution: Record<string, number>
}

export interface HistoryListResponse {
  total: number
  items: SyncHistory[]
  page: number
  page_size: number
}

export interface SourceTablesInfo {
  tables: string[]
  columns: string[]
  sample: Record<string, any>[]
}

export interface TableSample {
  table: string
  columns: string[]
  sample: Record<string, any>[]
}

export interface TaskCreatePayload {
  name: string
  description?: string
  connection_id: string
  source_table?: string
  source_query?: string
  sync_mode: SyncMode
  primary_key?: string
  watermark_column?: string
  is_deleted_column?: string
  schedule_type: ScheduleType
  cron_expression?: string
  interval_seconds?: number
  enabled?: boolean
  trigger_pipeline_id?: string | null
}

export const syncTasksApi = {
  list: (params?: Record<string, any>): Promise<TaskListResponse> =>
    apiClientV2.get('/sync-tasks', { params }),

  get: (id: string): Promise<SyncTask> =>
    apiClientV2.get(`/sync-tasks/${id}`),

  create: (payload: TaskCreatePayload): Promise<SyncTask> =>
    apiClientV2.post('/sync-tasks', payload),

  update: (id: string, payload: Partial<TaskCreatePayload>): Promise<SyncTask> =>
    apiClientV2.put(`/sync-tasks/${id}`, payload),

  delete: (id: string): Promise<{ status: string }> =>
    apiClientV2.delete(`/sync-tasks/${id}`),

  toggle: (id: string, enabled: boolean): Promise<SyncTask> =>
    apiClientV2.post(`/sync-tasks/${id}/toggle`, null, { params: { enabled } }),

  trigger: (id: string, sync = false): Promise<any> =>
    apiClientV2.post(`/sync-tasks/${id}/trigger`, null, { params: { sync } }),

  histories: (id: string, page = 1, page_size = 20): Promise<{ total: number; items: SyncHistory[] }> =>
    apiClientV2.get(`/sync-tasks/${id}/histories`, { params: { page, page_size } }),

  stats: (): Promise<SyncStats> =>
    apiClientV2.get('/sync-tasks/stats'),

  listSourceTables: (connId: string): Promise<SourceTablesInfo> =>
    apiClientV2.get(`/sync-tasks/sources/${connId}/tables`),

  previewTable: (connId: string, table: string): Promise<TableSample> =>
    apiClientV2.get(`/sync-tasks/sources/${connId}/tables/${encodeURIComponent(table)}/sample`),
}
