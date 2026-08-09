import { apiClientV2 } from '@/api/client'

export interface Dataset { id: string; name: string; kind: string }

export interface DatasetConsumer {
  id: string
  name: string
  status: string
  domain: string
}

export interface DatasetOverviewItem {
  id: string
  name: string
  raw_name: string
  kind: string
  /** 已声明的主键契约（逗号分隔复合主键），空串 = 未声明 */
  primary_key: string
  /** sync=同步任务落地 / upload=文件上传 / manual=在线建表 */
  source: 'sync' | 'upload' | 'manual'
  connection_name: string
  version_count: number
  latest_version_no: number
  rowcount: number | null
  consumers: DatasetConsumer[]
  created_at: string | null
  updated_at: string | null
}

export interface DatasetOverviewPage {
  items: DatasetOverviewItem[]
  total: number
  page: number
  page_size: number
}

export interface RowEditOp {
  key?: Record<string, string>
  values?: Record<string, unknown>
}

export interface RowEditsResult {
  dataset_id: string
  version_no: number
  rowcount: number
  updated: number
  inserted: number
  deleted: number
}

export interface DatasetVersionItem {
  id: string
  version_no: number
  rowcount: number | null
  /** 表格数据存于平台数据库时为 null；文件/历史版本才有对象 URI */
  storage_uri: string | null
}

export interface DatasetSchemaColumn {
  name: string
  display_name: string
  /** true 表示名称来自已保存的字段契约；名称可以与字段标识相同 */
  display_name_configured?: boolean
  type: string
  nullable: boolean
  is_primary_key: boolean
  sample_values: unknown[]
}

/** 平台类型词表的中文提示（与后端 lake_gate.FIELD_TYPE_LABELS 一致） */
export const FIELD_TYPE_LABELS: Record<string, string> = {
  string: '文本', integer: '整数', float: '小数',
  boolean: '布尔', timestamp: '时间', json: 'JSON',
}

export interface CreateTableColumn {
  name: string
  /** 上传文件中的原始表头；正式数据和本体映射始终使用 name */
  source_key?: string
  display_name?: string
  /** 平台类型词表 CONTRACT_FIELD_TYPES，非法值会被后端明确拒绝 */
  type: string
  nullable?: boolean
}

export interface CreateTableResult {
  id: string
  name: string
  kind: string
  columns: string[]
  primary_key: string
  version_no: number
  rowcount: number
  source: 'upload' | 'manual'
}

export type DatasetImportStatus =
  | 'uploading'
  | 'queued'
  | 'parsing'
  | 'ready'
  | 'import_queued'
  | 'importing'
  | 'completed'
  | 'failed'

export interface DatasetImportJob {
  job_id: string
  status: DatasetImportStatus
  filename: string
  file_size: number
  sheet_name?: string
  rowcount?: number
  columns?: { name: string; type: string }[]
  preview_rows?: Record<string, unknown>[]
  result?: CreateTableResult
  error?: string | null
  /** 当前后台阶段的近似进度（0-100）；浏览器上传进度由 onProgress 单独上报 */
  progress?: number
  phase?: string
  execution_mode?: 'celery' | 'local' | 'nats'
}

export interface UploadVersionResult {
  dataset_id: string
  dataset_name: string
  version_no: number
  rowcount: number | null
  columns_added: string[]
  columns_removed: string[]
  consumers: DatasetConsumer[]
}

const datasetsApi = {
  list: (kind?: string) => apiClientV2.get<Dataset[]>('/datasets', { params: kind ? { kind } : {} }),
  get: (id: string) => apiClientV2.get<Dataset>(`/datasets/${id}`),
  versions: (id: string) => apiClientV2.get<DatasetVersionItem[]>(`/datasets/${id}/versions`),
  preview: (id: string, versionNo: number, limit = 100) =>
    apiClientV2.get(`/datasets/${id}/versions/${versionNo}/preview`, { params: { limit } }),

  /** 资产湖原始数据集总览：版本/行数/来源/消费流水线 */
  overview: (params?: {
    source?: 'manual' | 'sync'
    search?: string
    sort_by?: 'created_at' | 'updated_at'
    page?: number
    page_size?: number
    paginated?: boolean
  }): Promise<DatasetOverviewPage> =>
    apiClientV2.get('/datasets/overview', { params }),

  /** 上传文件新建数据集 */
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return apiClientV2.post<{ id: string; name: string; kind: string }>('/datasets/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** 在线新建空表格：定义列名/类型/主键，不上传文件，之后在「维护数据」中逐行录入 */
  createTable: (payload: { name: string; columns: CreateTableColumn[]; primary_key?: string }): Promise<CreateTableResult> =>
    apiClientV2.post('/datasets/create-table', payload),

  /** 在统一建表弹窗中上传表格，并将字段设置与文件一起创建为 v1 */
  uploadConfigured: (
    file: File,
    payload: { name: string; columns: CreateTableColumn[]; primary_key?: string },
  ): Promise<CreateTableResult> => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('metadata', JSON.stringify(payload))
    return apiClientV2.post('/datasets/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** 在线建表专用：浏览器只上传文件，首工作表由后端后台任务异步解析 */
  startImport: (
    file: File,
    onProgress?: (percentage: number) => void,
  ): Promise<DatasetImportJob> => {
    const fd = new FormData()
    fd.append('file', file)
    return apiClientV2.post('/datasets/imports', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: event => {
        const total = event.total ?? file.size
        if (!total) return
        onProgress?.(Math.min(100, Math.round((event.loaded / total) * 100)))
      },
    })
  },

  importStatus: (jobId: string): Promise<DatasetImportJob> =>
    apiClientV2.get(`/datasets/imports/${jobId}`),

  /** 字段确认后异步完成全量契约校验与现有 DatasetVersion 持久化 */
  commitImport: (
    jobId: string,
    payload: { name: string; columns: CreateTableColumn[]; primary_key?: string },
  ): Promise<DatasetImportJob> =>
    apiClientV2.post(`/datasets/imports/${jobId}/commit`, payload),

  /** 给已有数据集上传新版本（数据集 ID 不变，流水线绑定不受影响） */
  uploadVersion: (datasetId: string, file: File): Promise<UploadVersionResult> => {
    const fd = new FormData()
    fd.append('file', file)
    return apiClientV2.post(`/datasets/${datasetId}/upload`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** 使用该数据集的流水线列表 */
  consumers: (datasetId: string): Promise<{ dataset_id: string; consumers: DatasetConsumer[] }> =>
    apiClientV2.get(`/datasets/${datasetId}/consumers`),

  /** 删除数据集；存在流水线或本体映射依赖时必须先解除依赖 */
  delete: (datasetId: string): Promise<{ status: string; id: string }> =>
    apiClientV2.delete(`/datasets/${datasetId}`),

  schema: (datasetId: string): Promise<{ dataset_id: string; columns: DatasetSchemaColumn[] }> =>
    apiClientV2.get(`/datasets/${datasetId}/schema`),

  /** 导出最新版本的全部数据，不受维护弹窗分页限制 */
  export: (datasetId: string, format: 'csv' | 'xlsx'): Promise<Blob> =>
    apiClientV2.get(`/datasets/${datasetId}/export`, {
      params: { format },
      responseType: 'blob',
    }),

  /** 最新版本数据预览（支持 offset 分页） */
  previewLatest: (datasetId: string, limit = 20, offset = 0): Promise<{
    dataset_id: string
    dataset_name?: string
    version_no?: number
    total_rows: number
    columns: string[]
    rows: Record<string, unknown>[]
  }> =>
    apiClientV2.get(`/datasets/${datasetId}/preview`, { params: { limit, offset } }),

  /** 声明主键契约（存在·非空·唯一三校验；被映射绑定后锁定） */
  declareContract: (datasetId: string, primaryKey: string): Promise<{
    dataset_id: string
    primary_key: string
    rows_validated: number
  }> =>
    apiClientV2.put(`/datasets/${datasetId}/contract`, { primary_key: primaryKey }),

  /** 在线维护：改单元格/增删行 → 生成新版本；base 版本不一致返回 409 */
  editRows: (datasetId: string, payload: {
    base_version_no: number
    updates?: RowEditOp[]
    inserts?: RowEditOp[]
    deletes?: RowEditOp[]
  }): Promise<RowEditsResult> =>
    apiClientV2.post(`/datasets/${datasetId}/rows/edit`, payload),
}

export default datasetsApi
