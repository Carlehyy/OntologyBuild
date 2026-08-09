/* 治理推演页的纯格式化助手：与组件解耦，便于 node:test 单测。
   目标：把内部机制术语翻译成用户可读的中文表达，并保证
   「待审批」「事实流」「弹窗」对同一目标的展示口径一致。 */

export interface TargetLabelLike {
  objectTypeName?: string | null
  objectInstanceLabel?: string | null
}

/** 目标摘要去重：实例标签已包含类型名前缀时不再重复拼接。 */
export function readableTargetSummary(
  target: TargetLabelLike,
  fallback = '未提供可读目标名称',
): string {
  const type = target.objectTypeName?.trim() || ''
  const label = target.objectInstanceLabel?.trim() || ''
  if (type && label) {
    if (label === type) return label
    if (label.startsWith(`${type} · `) || label.startsWith(`${type}·`)) return label
    return `${type} · ${label}`
  }
  return label || type || fallback
}

export interface DecisionValue {
  decision: 'approved' | 'rejected'
  reason?: string
}

/** 决策事实值可读化：支持字符串值（"APPROVED"）与对象值（{decision, reason}）。 */
export function formatDecisionValue(value: unknown): DecisionValue | null {
  if (typeof value === 'string') {
    const normalized = value.trim().toUpperCase()
    if (normalized === 'APPROVED') return { decision: 'approved' }
    if (normalized === 'REJECTED') return { decision: 'rejected' }
    return null
  }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>
    const raw = typeof record.decision === 'string' ? record.decision.trim().toUpperCase() : ''
    if (raw !== 'APPROVED' && raw !== 'REJECTED') return null
    const reason = typeof record.reason === 'string' && record.reason.trim()
      ? record.reason.trim()
      : undefined
    const result: DecisionValue = { decision: raw === 'APPROVED' ? 'approved' : 'rejected' }
    if (reason) result.reason = reason
    return result
  }
  return null
}

/** 事实来源可读化：把协议式 source 翻译成中文表达，原始值由调用方留在 title。 */
export function formatFactSource(source: string | null | undefined): string {
  if (!source) return '—'
  if (source.startsWith('user://')) return `${source.slice('user://'.length)} · 人工`
  if (source.startsWith('action://')) return `动作 · ${source.slice('action://'.length)}`
  if (source.startsWith('ontology-release://')) return '发布快照'
  if (source.startsWith('fn:')) return `函数 · ${source.slice('fn:'.length)}`
  if (source === 'pipeline') return '数据管道'
  return source
}

export interface FiringStatusMeta {
  label: string
  pillCls: string
  dotCls: string
}

export const FIRING_STATUS_META: Record<string, FiringStatusMeta> = {
  fired: { label: '已触发', pillCls: 'bg-rose-50 text-rose-500', dotCls: 'bg-rose-400' },
  pending: { label: '待审批', pillCls: 'bg-amber-50 text-amber-600', dotCls: 'bg-amber-300' },
  no_match: { label: '未命中', pillCls: 'bg-gray-50 text-gray-400', dotCls: 'bg-gray-300' },
  no_change: { label: '无变化', pillCls: 'bg-gray-50 text-gray-400', dotCls: 'bg-gray-300' },
  muted: { label: '影子记录', pillCls: 'bg-amber-50 text-amber-600', dotCls: 'bg-amber-300' },
  error: { label: '错误', pillCls: 'bg-red-50 text-red-500', dotCls: 'bg-red-400' },
  skipped: { label: '已跳过', pillCls: 'bg-gray-50 text-gray-400', dotCls: 'bg-gray-300' },
}

const UNKNOWN_FIRING_STATUS: FiringStatusMeta = {
  label: '',
  pillCls: 'bg-gray-50 text-gray-400',
  dotCls: 'bg-gray-300',
}

/** 未知状态原样展示（兜底），已知状态返回中文 meta。 */
export function firingStatusMeta(status: string): FiringStatusMeta {
  return FIRING_STATUS_META[status] ?? { ...UNKNOWN_FIRING_STATUS, label: status }
}

/** 扫描间隔可读化：<60s 按秒、<60min 按分钟、否则按小时（取整）。 */
export function formatScanInterval(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  if (seconds < 60) return `每 ${Math.round(seconds)} 秒`
  if (seconds < 3600) return `每 ${Math.round(seconds / 60)} 分钟`
  return `每 ${Math.round(seconds / 3600)} 小时`
}

export interface AutonomyKpiLike {
  level: string
  decisions?: { approved?: number; rejected?: number } | null
}

export interface SentinelKpiLike {
  enabled?: boolean
  muted?: boolean
}

export interface GovernanceKpis {
  pendingCount: number
  sentinelsTotal: number
  sentinelsOnline: number
  sentinelsMuted: number
  sentinelsDisabled: number
  actionsTotal: number
  levelCounts: { L0: number; L1: number; L2: number }
  decisionsApproved: number
  decisionsRejected: number
  decisionsTotal: number
  approvalRate: number | null
}

/** KPI 总览条数据：全部从已加载的 query 结果派生，零新增请求。 */
export function buildGovernanceKpis(input: {
  pending: unknown[]
  autonomy: AutonomyKpiLike[]
  sentinels: SentinelKpiLike[]
}): GovernanceKpis {
  const { pending, autonomy, sentinels } = input
  const sentinelsOnline = sentinels.filter(s => s.enabled !== false && !s.muted).length
  const sentinelsMuted = sentinels.filter(s => s.enabled !== false && s.muted).length
  const sentinelsDisabled = sentinels.filter(s => s.enabled === false).length
  const levelCounts = { L0: 0, L1: 0, L2: 0 }
  let decisionsApproved = 0
  let decisionsRejected = 0
  for (const a of autonomy) {
    if (a.level === 'L0' || a.level === 'L1' || a.level === 'L2') levelCounts[a.level] += 1
    decisionsApproved += a.decisions?.approved ?? 0
    decisionsRejected += a.decisions?.rejected ?? 0
  }
  const decisionsTotal = decisionsApproved + decisionsRejected
  return {
    pendingCount: pending.length,
    sentinelsTotal: sentinels.length,
    sentinelsOnline,
    sentinelsMuted,
    sentinelsDisabled,
    actionsTotal: autonomy.length,
    levelCounts,
    decisionsApproved,
    decisionsRejected,
    decisionsTotal,
    approvalRate: decisionsTotal > 0 ? decisionsApproved / decisionsTotal : null,
  }
}
