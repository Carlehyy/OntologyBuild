/**
 * 事件登记 API — /api/v2/events（平台）+ /api/v2/ingest（第三方，本前端不直接用）
 *
 * 后端对外统一 camelCase，apiClientV2 已自动解包 {data:...} 信封。
 */
import { apiClientV2 } from './client'

// ---------- 类型 ----------

export interface Attachment {
  id: string
  eventId: string
  filename: string
  fileSize: number
  mimeType: string | null
  sha256: string | null
  uploadedBy: string | null
  createdAt: string
}

export interface AuditEntry {
  id: string
  seq: number
  action: string
  actorType: string
  actorId: string | null
  actorName: string | null
  changes: Record<string, { from: any; to: any }> | null
  note: string | null
  ip: string | null
  createdAt: string
}

export interface EventItem {
  id: string
  eventNo: string
  title: string
  description: string
  eventType: string
  severity: string
  tags: string[]
  payload: Record<string, any>
  occurredAt: string | null
  recordedAt: string | null
  sourceType: string        // platform | api | system
  sourceLabel: string       // 平台录入 / 第三方·<来源>
  sourceSystem: string | null
  sourceRef: string | null
  reporterType: string
  reporterName: string | null
  ingestKeyId: string | null
  clientIp: string | null
  confidence: number | null
  ontologyId: string | null
  subjectRef: string | null
  supersedesId: string | null
  status: string            // active | archived
  createdAt: string
  updatedAt: string
  attachmentCount?: number
  attachments?: Attachment[]
  auditTrail?: AuditEntry[]
}

export interface EventListResp {
  items: EventItem[]
  total: number
  page: number
  pageSize: number
}

export interface EventStats {
  total: number
  active: number
  archived: number
  platform: number
  api: number
  today: number
  bySeverity: Record<string, number>
  trend7d: Array<{
    date: string
    total: number
    bySeverity: Record<string, number>
  }>
}

export interface IngestKey {
  id: string
  name: string
  keyPrefix: string
  enabled: boolean
  allowedSourceSystem: string | null
  createdBy: string | null
  createdAt: string
  lastUsedAt: string | null
  revokedAt: string | null
  plaintextKey?: string     // 仅创建响应带
}

export interface IngestKeyListParams {
  q?: string
  status?: 'all' | 'active' | 'revoked'
  sourceSystem?: string
  page?: number
  pageSize?: number
}

export interface IngestKeyListResp {
  items: IngestKey[]
  total: number
  page: number
  pageSize: number
}

export interface EventCreateBody {
  title: string
  description?: string
  eventType?: string
  severity?: string
  tags?: string[]
  payload?: Record<string, any>
  occurredAt?: string | null
  ontologyId?: string | null
  subjectRef?: string | null
  confidence?: number | null
}

export interface EventListParams {
  q?: string
  sourceType?: string
  eventType?: string
  severity?: string
  status?: string           // active | archived | all
  page?: number
  pageSize?: number
}

// ---------- 客户端 ----------

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'attachment'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export const eventsApi = {
  list: (params: EventListParams = {}): Promise<EventListResp> =>
    apiClientV2.get('/events', { params }),

  get: (id: string): Promise<EventItem> =>
    apiClientV2.get(`/events/${id}`),

  create: (body: EventCreateBody): Promise<EventItem> =>
    apiClientV2.post('/events', body),

  update: (id: string, body: Partial<EventCreateBody>): Promise<EventItem> =>
    apiClientV2.patch(`/events/${id}`, body),

  changeStatus: (id: string, status: string, note?: string): Promise<EventItem> =>
    apiClientV2.post(`/events/${id}/status`, { status, note }),

  archive: (id: string): Promise<EventItem> =>
    apiClientV2.delete(`/events/${id}`),

  remove: (id: string): Promise<{ status: string; id: string }> =>
    apiClientV2.delete(`/events/${id}`, { params: { hard: true } }),

  stats: (): Promise<EventStats> =>
    apiClientV2.get('/events/stats/summary'),

  uploadAttachment: (id: string, file: File): Promise<Attachment> => {
    const fd = new FormData()
    fd.append('file', file)
    return apiClientV2.post(`/events/${id}/attachments`, fd)
  },

  deleteAttachment: (eventId: string, attId: string): Promise<any> =>
    apiClientV2.delete(`/events/${eventId}/attachments/${attId}`),

  downloadAttachment: (eventId: string, att: Attachment): Promise<void> =>
    apiClientV2
      .get(`/events/${eventId}/attachments/${att.id}/download`, { responseType: 'blob' })
      .then((blob: Blob) => saveBlob(blob, att.filename)),

  // 密钥（admin）
  listKeys: (params: IngestKeyListParams = {}): Promise<IngestKeyListResp> =>
    apiClientV2.get('/events/ingest-keys', {
      params: {
        q: params.q,
        status: params.status,
        source_system: params.sourceSystem,
        page: params.page,
        page_size: params.pageSize,
      },
    }),

  createKey: (name: string, allowedSourceSystem?: string): Promise<IngestKey> =>
    apiClientV2.post('/events/ingest-keys', { name, allowedSourceSystem }),

  revokeKey: (id: string): Promise<IngestKey> =>
    apiClientV2.delete(`/events/ingest-keys/${id}`),
}

// ---------- 展示辅助 ----------

export const SEVERITY_META: Record<string, { label: string; cls: string; dot: string }> = {
  info: { label: '信息', cls: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400' },
  low: { label: '低级', cls: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400' },
  medium: { label: '中级', cls: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500' },
  high: { label: '高级', cls: 'bg-orange-50 text-orange-700', dot: 'bg-orange-500' },
  critical: { label: '严重', cls: 'bg-red-50 text-red-600', dot: 'bg-red-500' },
}

export const AUDIT_ACTION_LABEL: Record<string, string> = {
  created: '登记',
  updated: '编辑',
  status_changed: '状态变更',
  attachment_added: '添加附件',
  attachment_removed: '删除附件',
  ingested: '第三方上传',
  ingest_duplicate: '重复投递',
}

export function formatBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(n) / Math.log(1024))
  return `${(n / Math.pow(1024, i)).toFixed(i ? 1 : 0)} ${units[i]}`
}
