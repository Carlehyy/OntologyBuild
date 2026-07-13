import type { RowEditOp } from './v2/datasets'

export interface PublicManualDataset {
  dataset: {
    id: string
    name: string
    version_no: number
    total_rows: number
    columns: string[]
    column_types: Record<string, string>
    column_meta: Record<string, { display_name: string; nullable: boolean }>
    primary_key: string
    rows: Record<string, unknown>[]
  }
  share: { permission: 'view' | 'edit'; label: string; expires_at: string | null }
  changes: Array<{
    id: string
    status: 'pending' | 'approved' | 'rejected'
    summary: { updated: number; inserted: number; deleted: number; result_rows: number }
    review_comment: string
    submitted_at: string
    reviewed_at: string | null
    applied_version_no: number | null
  }>
}

const base = () => {
  const runtime = (window as Window & { __API_BASE_URL__?: string }).__API_BASE_URL__ || ''
  return `${runtime}/api/public/manual-datasets`
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base()}${url}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message || '请求失败，请稍后重试')
  }
  return body as T
}

export const publicManualSharingApi = {
  get: (token: string, offset = 0, limit = 50) =>
    request<PublicManualDataset>(`/${encodeURIComponent(token)}?offset=${offset}&limit=${limit}`),
  submit: (token: string, payload: {
    base_version_no: number
    updates: RowEditOp[]
    inserts: RowEditOp[]
    deletes: RowEditOp[]
  }) => request<{ id: string; status: string }>(`/${encodeURIComponent(token)}/changes`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
}
