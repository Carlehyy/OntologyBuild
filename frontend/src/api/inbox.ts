import { apiClientV2 } from './client'

export type InboxKind = 'task' | 'alert' | 'notice'
export type InboxPriority = 'urgent' | 'high' | 'normal' | 'low'
export type InboxBusinessState = 'open' | 'resolved' | 'cancelled' | 'expired'
export type InboxDeliveryState = 'unread' | 'read' | 'archived'
export type InboxTab = 'actionable' | 'unread' | 'resolved' | 'all' | 'archived'

export interface InboxSummary {
  openAlertCount: number
  actionableCount: number
  unreadCount: number
  resolvedCount: number
}

export interface InboxAction {
  key: string
  label: string
  mode: 'navigate'
  href: string
}

export interface InboxDelivery {
  id: string
  itemId: string
  kind: InboxKind
  priority: InboxPriority
  businessState: InboxBusinessState
  deliveryState: InboxDeliveryState
  title: string
  summary: string
  safeContext: {
    taskName?: string
    pipelineName?: string
    triggerType?: string
    latestRunId?: string | null
    errorSummary?: string
    failureCount?: number
    [key: string]: unknown
  }
  source: {
    system: string
    type: string
    id: string
    occurrenceId?: string | null
  }
  resource: {
    type: string
    id: string
    label?: string
    href: string
  }
  actions: InboxAction[]
  occurrenceCount: number
  firstOccurredAt: string
  lastOccurredAt: string
  resolvedAt?: string | null
  resolutionReason?: string | null
  expiresAt?: string | null
  readAt?: string | null
  createdAt: string
  canArchive: boolean
}

export interface InboxPageResult {
  items: InboxDelivery[]
  nextCursor: string | null
  hasMore: boolean
}

export const inboxApi = {
  summary: (): Promise<InboxSummary> => apiClientV2.get('/inbox/summary'),

  list: (params: {
    tab?: InboxTab
    kind?: InboxKind
    cursor?: string | null
    limit?: number
  } = {}): Promise<InboxPageResult> => apiClientV2.get('/inbox', { params }),

  get: (id: string): Promise<InboxDelivery> => apiClientV2.get(`/inbox/${id}`),

  updateState: (id: string, state: 'read' | 'unread' | 'archived'): Promise<InboxDelivery> =>
    apiClientV2.patch(`/inbox/${id}`, { state }),

  readAll: (): Promise<{ updated: number }> => apiClientV2.post('/inbox/read-all'),
}
