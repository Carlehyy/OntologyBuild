import { apiClientV2 } from '@/api/client'

export interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
  quality_score: number | null
  /** 资产湖固化的主键契约；复合主键以逗号分隔，空串表示未声明。 */
  primary_key: string
  /** 数据库约束的稳定产物身份；legacy 资产可能为空。 */
  producer_pipeline_id: string | null
  output_key: string | null
  has_review_evidence: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface CuratedDatasetPage {
  items: CuratedDataset[]
  total: number
  page: number
  page_size: number
}

export interface CuratedPreview {
  dataset_id: string
  name: string
  rows: Record<string, unknown>[]
  count: number
  columns?: string[]
  total_rows?: number
  offset?: number
  limit?: number
  has_more?: boolean
  error?: string
}

export interface ReviewSession {
  review_id: string
  status: string
  dataset_version_id?: string | null
}

export interface ReviewDiffView {
  version_no: number | null
  dataset_version_id?: string | null
  total: number
  rows: Record<string, unknown>[]
  offset: number
  limit: number
  has_more: boolean
}

export interface ReviewDelta {
  keyed_by: string[] | null
  total_before: number
  total_after: number
  added_count: number
  updated_count: number
  deleted_count: number
  unchanged_count: number
  added_sample: Record<string, unknown>[]
  updated_sample: { before: Record<string, unknown>; after: Record<string, unknown> }[]
  deleted_sample: Record<string, unknown>[]
  sample_truncated: boolean
}

export interface ReviewDiffSession {
  id: string
  dataset_version_id: string | null
  status: string
  stale: boolean
  latest_dataset_version_id: string | null
  latest_version_no: number | null
}

export interface ReviewDiff {
  pk: string[]
  /** 行级编辑主键编码：单主键为原值字符串，复合主键为 JSON 字符串数组。 */
  row_pk_encoding?: 'plain-string' | 'json-array'
  current: ReviewDiffView
  previous: ReviewDiffView
  delta: ReviewDelta | null
  review?: ReviewDiffSession | null
}

const curatedApi = {
  list: () => apiClientV2.get<CuratedDataset[]>('/curated'),
  listPage: (params: {
    pipeline?: string
    task_id?: string
    status?: string
    page?: number
    page_size?: number
  }) => apiClientV2.get<CuratedDatasetPage>('/curated', {
    params: { ...params, paginated: true },
  }),
  get: (id: string) => apiClientV2.get<CuratedDataset>(`/curated/${id}`),
  preview: (id: string, limit = 200, offset = 0) =>
    apiClientV2.get<CuratedPreview>(`/curated/${id}/preview`, { params: { limit, offset } }),
  /** 审批三视角：变化量 / 上一版全量 / 本次全量 */
  reviewDiff: (id: string, limit = 200, offset = 0, reviewId?: string) =>
    apiClientV2.get<ReviewDiff>(`/curated/${id}/review-diff`, {
      params: { limit, offset, ...(reviewId ? { review_id: reviewId } : {}) },
    }),
  quality: (id: string) => apiClientV2.get(`/curated/${id}/quality`),

  /** Quick approve/reject (no review session needed) */
  approve: (id: string, notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=approve&notes=${encodeURIComponent(notes)}`),
  reject: (id: string, notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=reject&notes=${encodeURIComponent(notes)}`),

  /** 完整删除成品数据集（仅管理员；存在外部引用时拦截） */
  delete: (id: string) => apiClientV2.delete(`/curated/${id}`),

  /** 导出最新已审核版本的全量数据，不受详情分页限制 */
  export: (id: string, format: 'csv' | 'xlsx'): Promise<Blob> =>
    apiClientV2.get(`/curated/${id}/export`, {
      params: { format },
      responseType: 'blob',
    }),

  /** Start a review session for row-level edits */
  startReview: (id: string) =>
    apiClientV2.post<ReviewSession>(`/curated/${id}/reviews`),

  /** Save batch edits within a review session */
  saveEdits: (reviewId: string, edits: Array<{ row_pk: string; field_name: string; old_value: string; new_value: string }>) =>
    apiClientV2.post<{ saved: number }>(`/curated/reviews/${reviewId}/edits`, { edits }),

  /** Approve/reject a review session */
  approveReview: (reviewId: string, notes = '') =>
    apiClientV2.post(`/curated/reviews/${reviewId}/approve?notes=${encodeURIComponent(notes)}`),
  rejectReview: (reviewId: string, notes = '') =>
    apiClientV2.post(`/curated/reviews/${reviewId}/reject?notes=${encodeURIComponent(notes)}`),
}

export default curatedApi
