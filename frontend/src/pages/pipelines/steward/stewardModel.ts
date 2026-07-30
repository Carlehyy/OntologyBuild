import type {
  StewardArtifact,
  StewardPipeline,
  StewardStep,
  StewardTablePreview,
} from '@/api/steward'


export type {
  StewardArtifact,
  StewardPipeline,
  StewardStep,
  StewardTablePreview,
}

export interface StewardChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: StewardStep[]
  targetName?: string
  loading?: boolean
  error?: string
  createdAt?: string
}

export interface StewardPendingUpload {
  uid: string
  name: string
  ts: number
}

export type StewardTimelineItem =
  | { key: string; ts: number; kind: 'message'; message: StewardChatMessage }
  | { key: string; ts: number; kind: 'file'; file: StewardArtifact }
  | { key: string; ts: number; kind: 'upload'; upload: StewardPendingUpload }

export function buildStewardTimeline(
  messages: StewardChatMessage[],
  files: StewardArtifact[],
  uploads: StewardPendingUpload[],
): StewardTimelineItem[] {
  const items: StewardTimelineItem[] = []
  let lastTimestamp = 0
  messages.forEach(message => {
    let timestamp = message.createdAt ? Date.parse(message.createdAt) : NaN
    if (Number.isNaN(timestamp)) timestamp = lastTimestamp + 1
    lastTimestamp = timestamp
    items.push({ key: message.id, ts: timestamp, kind: 'message', message })
  })
  files
    .filter(file => file.source === 'upload')
    .forEach(file => items.push({
      key: `file-${file.id}`,
      ts: Date.parse(file.createdAt) || 0,
      kind: 'file',
      file,
    }))
  uploads.forEach(upload => items.push({
    key: upload.uid,
    ts: upload.ts,
    kind: 'upload',
    upload,
  }))
  return items.sort((left, right) => left.ts - right.ts)
}

export function filterStewardTargets(
  records: StewardPipeline[],
  search: string,
): StewardPipeline[] {
  const keyword = search.trim().toLowerCase()
  if (!keyword) return records
  return records.filter(record => (
    record.name.toLowerCase().includes(keyword)
    || record.description.toLowerCase().includes(keyword)
    || record.id.toLowerCase().includes(keyword)
  ))
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function errorText(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const value = error as { detail?: unknown; message?: unknown }
    if (typeof value.detail === 'string') return value.detail
    if (typeof value.message === 'string') return value.message
  }
  return fallback
}
