import { apiClientV2 } from '@/api/client'

export interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
  quality_score: number | null
}

export interface CuratedPreview {
  dataset_id: string
  name: string
  rows: Record<string, string>[]
  count: number
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
  get: (id: string) => apiClientV2.get<CuratedDataset>(`/curated/${id}`),
  preview: (id: string, limit = 200) =>
    apiClientV2.get<CuratedPreview>(`/curated/${id}/preview?limit=${limit}`),
  /** 审批三视角：变化量 / 上一版全量 / 本次全量 */
  reviewDiff: (id: string, limit = 500, reviewId?: string) =>
    apiClientV2.get<ReviewDiff>(`/curated/${id}/review-diff`, {
      params: { limit, ...(reviewId ? { review_id: reviewId } : {}) },
    }),
  quality: (id: string) => apiClientV2.get(`/curated/${id}/quality`),

  /** Quick approve/reject (no review session needed) */
  approve: (id: string, notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=approve&notes=${encodeURIComponent(notes)}`),
  reject: (id: string, notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=reject&notes=${encodeURIComponent(notes)}`),

  /** Delete a curated dataset (admin only; approved datasets are blocked) */
  delete: (id: string, force = false) =>
    apiClientV2.delete(`/curated/${id}${force ? '?force=true' : ''}`),

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
