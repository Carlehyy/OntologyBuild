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
  source: 'sync' | 'upload'
  connection_name: string
  version_count: number
  latest_version_no: number
  rowcount: number | null
  consumers: DatasetConsumer[]
  created_at: string | null
  updated_at: string | null
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
  storage_uri: string
}

export interface DatasetSchemaColumn {
  name: string
  type: string
  sample_values: string[]
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
  overview: (): Promise<{ items: DatasetOverviewItem[]; total: number }> =>
    apiClientV2.get('/datasets/overview'),

  /** 上传文件新建数据集 */
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return apiClientV2.post<{ id: string; name: string; kind: string }>('/datasets/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

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

  /** 删除数据集（被流水线引用时返回 409，force=true 强制删除） */
  delete: (datasetId: string, force = false): Promise<{ status: string; id: string }> =>
    apiClientV2.delete(`/datasets/${datasetId}`, { params: { force } }),

  schema: (datasetId: string): Promise<{ dataset_id: string; columns: DatasetSchemaColumn[] }> =>
    apiClientV2.get(`/datasets/${datasetId}/schema`),

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
