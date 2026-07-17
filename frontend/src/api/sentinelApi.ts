import { apiClient } from './client'

export interface SentinelBinding {
  alias: string
  objectTypeId: string
  filter?: string | null
}

export interface SentinelLink {
  from: string
  linkTypeId: string
  to: string
}

export interface Sentinel {
  id: string
  ontologyId: string
  name: string
  displayName: string
  description?: string
  bindings: SentinelBinding[]
  links: SentinelLink[]
  condition?: string
  conditionRows?: any[]
  conditionLogic?: string
  primaryAlias?: string
  actionIds: string[]
  onChange: boolean
  onSchedule: boolean
  scanIntervalSeconds: number
  lastScannedAt?: string
  muted: boolean
  enabled: boolean
  status: string
  createdAt?: string
  updatedAt?: string
}

export interface SentinelFiring {
  id: string
  sentinelId: string
  sentinelName: string
  triggerSource: string
  status: string
  matchCount: number
  matches: Record<string, string>[]
  actionResults: any[]
  error?: string
  durationMs?: number
  ontologyVersion?: string | null
  createdAt?: string
}

const base = (ontologyId: string) => `/ontologies/${ontologyId}/sentinels`
const releaseQuery = (releaseId?: string | null) => releaseId
  ? `?release_id=${encodeURIComponent(releaseId)}`
  : ''

export const sentinelApi = {
  list: (ontologyId: string, releaseId?: string | null) =>
    apiClient.get<Sentinel[]>(`${base(ontologyId)}/${releaseQuery(releaseId)}`),
  create: (ontologyId: string, body: Partial<Sentinel>) =>
    apiClient.post<Sentinel>(`${base(ontologyId)}/`, body),
  update: (ontologyId: string, id: string, body: Partial<Sentinel>) =>
    apiClient.put<Sentinel>(`${base(ontologyId)}/${id}`, body),
  remove: (ontologyId: string, id: string) =>
    apiClient.delete(`${base(ontologyId)}/${id}`),
  toggle: (ontologyId: string, id: string) =>
    apiClient.post<{ enabled: boolean }>(`${base(ontologyId)}/${id}/toggle`),
  run: (ontologyId: string) =>
    apiClient.post<any>(`${base(ontologyId)}/run`),
  firings: (ontologyId: string, releaseId?: string | null) =>
    apiClient.get<SentinelFiring[]>(`${base(ontologyId)}/firings${releaseQuery(releaseId)}`),
  notifications: (ontologyId: string) =>
    apiClient.get<SentinelNotification[]>(`${base(ontologyId)}/notifications`),
}

export interface SentinelNotification {
  id: string
  channel: string
  recipient: string
  subject?: string
  body?: string
  relatedObjectId?: string
  actionId?: string
  status: string
  createdAt?: string
}
