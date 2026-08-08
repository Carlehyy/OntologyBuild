import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'
import { sentinelApi, type Sentinel, type SentinelFiring } from '@/api/sentinelApi'
import { Button } from '@/components/ui/Button'
import { ConfirmModal, Modal } from '@/components/ui/Modal'
import {
  HandMetal, Rocket, ShieldAlert, ScrollText, Loader2, CheckCircle2, XCircle,
  Eye, Bolt, ArrowUpCircle, ArrowDownCircle, ExternalLink, AlertTriangle,
  RefreshCw,
} from 'lucide-react'

/* 治理与推演驾驶舱：
   ① 待审批 —— 人是最终裁决者，批准/拒绝都是事实
   ② 自治等级 —— 按人工批准率逐级放权（影子→人审→自动）
   ③ 哨兵 —— 平台正在替你盯什么、最近命中了什么
   ④ 事实流 —— 每一个变化的出处与因果，全量留痕 */

interface PendingLog {
  id: string; actionId: string; actionName: string | null
  objectInstanceId: string | null; parameters: Record<string, unknown>
  actorId: string | null; executedAt: string; ontologyVersion?: string | null
  objectTypeName?: string | null; objectInstanceLabel?: string | null
  triggerSource?: string | null
}

interface AutonomyStat {
  actionId: string; actionName: string; requiresApproval: boolean
  level: 'L0' | 'L1' | 'L2'; shadow: boolean
  sentinels: { id: string; name: string; muted: boolean; enabled: boolean }[]
  decisions: { approved: number; rejected: number; total: number; recentCount: number; recentApprovalRate: number | null }
  autoRuns: { total: number; failed: number }
  pending: number
  recommendation: 'promote' | 'demote' | 'observe' | null
  recommendationReason: string | null
  thresholds: { promoteMinDecisions: number; promoteRate: number }
}

interface FactRow {
  id: string; subjectLabel: string; propertyName: string; value: unknown
  present?: boolean
  kind: string; source: string; actorId?: string | null
  causedBy?: string | null; supersedesId?: string | null; recordedAt: string | null
  ontologyVersion?: string | null
}

interface GovernanceTabProps {
  ontologyId: string
  currentReleaseId?: string | null
  currentReleaseVersion: string
}

const KIND_META: Record<string, { label: string; cls: string; title: string }> = {
  property: { label: '属性', cls: 'bg-blue-50 text-blue-600 border-blue-200', title: '数据源/人工写入的存储属性变化' },
  derived: { label: '派生', cls: 'bg-purple-50 text-purple-600 border-purple-200', title: '函数自动重算的派生值（可溯源到输入事实）' },
  link: { label: '链接', cls: 'bg-cyan-50 text-cyan-600 border-cyan-200', title: '关系的建立/解除' },
  object: { label: '存在', cls: 'bg-red-50 text-red-600 border-red-200', title: '实例存在性（删除留墓碑）' },
  decision: { label: '决策', cls: 'bg-amber-50 text-amber-700 border-amber-200', title: '人的审批决策（批准/拒绝都记录）' },
  absence: { label: '缺席', cls: 'bg-gray-100 text-gray-500 border-gray-300', title: '查询结果为空/非空的翻转快照——"没有"也有出处' },
}

const LEVEL_META: Record<string, { label: string; icon: any; cls: string; desc: string }> = {
  L0: { label: 'L0 影子', icon: Eye, cls: 'bg-gray-100 text-gray-600 border-gray-300', desc: '哨兵全部静默，只观察不动手' },
  L1: { label: 'L1 人审', icon: HandMetal, cls: 'bg-blue-50 text-blue-700 border-blue-300', desc: '每次执行等人批准' },
  L2: { label: 'L2 自动', icon: Bolt, cls: 'bg-emerald-50 text-emerald-700 border-emerald-300', desc: '命中即执行' },
}

const BACKGROUND_REFRESH_INTERVAL_MS = 12_000
const BACKGROUND_REFRESH_MAX_CYCLES = 10

function readableTargetSummary(log: PendingLog, fallback = '未提供可读目标名称'): string {
  const labels = [log.objectTypeName, log.objectInstanceLabel]
    .map(label => label?.trim())
    .filter((label): label is string => Boolean(label))
  return [...new Set(labels)].join(' · ') || fallback
}

function SectionHead({ icon: Icon, iconCls, title, sub, extra }: {
  icon: any; iconCls: string; title: string; sub: string; extra?: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon size={15} className={iconCls} />
      <p className="text-sm font-medium text-gray-700">{title}</p>
      <span className="text-xs text-gray-400">{sub}</span>
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  )
}

function errorMessage(error: unknown, fallback = '治理数据加载失败'): string {
  if (!error || typeof error !== 'object') return fallback
  const candidate = error as { detail?: unknown; message?: unknown }
  if (typeof candidate.detail === 'string') return candidate.detail
  if (candidate.detail && typeof candidate.detail === 'object' && 'message' in candidate.detail) {
    const message = (candidate.detail as { message?: unknown }).message
    if (typeof message === 'string') return message
  }
  return typeof candidate.message === 'string' ? candidate.message : fallback
}

export default function GovernanceTab({
  ontologyId, currentReleaseId, currentReleaseVersion,
}: GovernanceTabProps) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [rejectTarget, setRejectTarget] = useState<PendingLog | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectError, setRejectError] = useState<string | null>(null)
  const [approveTarget, setApproveTarget] = useState<PendingLog | null>(null)
  const [kindFilter, setKindFilter] = useState('')
  const [isPageVisible, setIsPageVisible] = useState(() =>
    typeof document === 'undefined' || document.visibilityState === 'visible')
  const [remainingRefreshCycles, setRemainingRefreshCycles] = useState(
    BACKGROUND_REFRESH_MAX_CYCLES)
  const [manualRefreshPending, setManualRefreshPending] = useState(false)

  const releaseParam = currentReleaseId
    ? `release_id=${encodeURIComponent(currentReleaseId)}`
    : ''
  const queryEnabled = Boolean(currentReleaseId)
  const pendingQuery = useQuery<PendingLog[]>({
    queryKey: ['gov-pending', ontologyId, currentReleaseId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/pending-actions?${releaseParam}`) as any,
    enabled: queryEnabled,
  })
  const autonomyQuery = useQuery<AutonomyStat[]>({
    queryKey: ['gov-autonomy', ontologyId, currentReleaseId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/autonomy?${releaseParam}`) as any,
    enabled: queryEnabled,
  })
  const sentinelsQuery = useQuery<Sentinel[]>({
    queryKey: ['gov-sentinels', ontologyId, currentReleaseId],
    queryFn: () => sentinelApi.list(ontologyId, currentReleaseId) as any,
    enabled: queryEnabled,
  })
  const firingsQuery = useQuery<SentinelFiring[]>({
    queryKey: ['gov-firings', ontologyId, currentReleaseId],
    queryFn: () => sentinelApi.firings(ontologyId, currentReleaseId) as any,
    enabled: queryEnabled,
  })
  const factsQuery = useQuery<FactRow[]>({
    queryKey: ['gov-facts', ontologyId, currentReleaseId, kindFilter],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/facts/recent?limit=50&${releaseParam}${kindFilter ? `&kind=${kindFilter}` : ''}`) as any,
    enabled: queryEnabled,
  })
  const pending = pendingQuery.data ?? []
  const autonomy = autonomyQuery.data ?? []
  const sentinels = sentinelsQuery.data ?? []
  const firings = firingsQuery.data ?? []
  const facts = factsQuery.data ?? []
  const governanceQueries = [
    pendingQuery, autonomyQuery, sentinelsQuery, firingsQuery, factsQuery,
  ]
  const failedQuery = governanceQueries.find(query => query.isError)
  const isInitialLoading = governanceQueries.some(query => query.isLoading)
  const isRefreshing = governanceQueries.some(query => query.isFetching)
  const lastUpdatedAt = Math.max(
    0, ...governanceQueries.map(query => query.dataUpdatedAt))
  const backgroundRefreshActive = queryEnabled && remainingRefreshCycles > 0

  const refreshAll = useCallback(() => Promise.all([
    qc.invalidateQueries({ queryKey: ['gov-pending', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-autonomy', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-sentinels', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-firings', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-facts', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['formal-overview', ontologyId] }),
  ]), [ontologyId, qc])

  useEffect(() => {
    setRemainingRefreshCycles(BACKGROUND_REFRESH_MAX_CYCLES)
  }, [currentReleaseId, ontologyId])

  useEffect(() => {
    const syncVisibility = () => {
      setIsPageVisible(document.visibilityState === 'visible')
    }
    document.addEventListener('visibilitychange', syncVisibility)
    return () => document.removeEventListener('visibilitychange', syncVisibility)
  }, [])

  useEffect(() => {
    if (
      !backgroundRefreshActive
      || !isPageVisible
      || isInitialLoading
      || isRefreshing
      || failedQuery
    ) return
    const timer = window.setTimeout(() => {
      setRemainingRefreshCycles(current => Math.max(0, current - 1))
      void refreshAll()
    }, BACKGROUND_REFRESH_INTERVAL_MS)
    return () => window.clearTimeout(timer)
  }, [
    backgroundRefreshActive, failedQuery, isInitialLoading, isPageVisible,
    isRefreshing, refreshAll, remainingRefreshCycles,
  ])

  const reloadReleaseContext = () => {
    qc.invalidateQueries({ queryKey: ['ontology', ontologyId] })
    setRemainingRefreshCycles(BACKGROUND_REFRESH_MAX_CYCLES)
    void refreshAll()
  }

  const refreshNow = async () => {
    setRemainingRefreshCycles(BACKGROUND_REFRESH_MAX_CYCLES)
    setManualRefreshPending(true)
    try {
      await refreshAll()
    } finally {
      setManualRefreshPending(false)
    }
  }

  const decide = async (
    log: PendingLog,
    decision: 'approved' | 'rejected',
    reason?: string,
  ) => {
    setBusy(log.id)
    setMsg(null)
    if (decision === 'rejected') setRejectError(null)
    try {
      await apiClientV2.post(`/formal/ontologies/${ontologyId}/action-logs/${log.id}/decide`,
        { decision, reason, releaseId: currentReleaseId })
      setMsg({ ok: true, text: decision === 'approved' ? '已批准并提交执行，决策已写入事实流。' : '已拒绝，决策已写入事实流。' })
      if (decision === 'approved') {
        setApproveTarget(null)
      }
      if (decision === 'rejected') {
        setRejectTarget(null)
        setRejectReason('')
      }
      setRemainingRefreshCycles(BACKGROUND_REFRESH_MAX_CYCLES)
      void refreshAll()
    } catch (e: any) {
      const text = errorMessage(e, '决策失败，请重试')
      if (decision === 'rejected') setRejectError(text)
      else setMsg({ ok: false, text })
    } finally {
      setBusy(null)
    }
  }

  const openRejectDialog = (log: PendingLog) => {
    setRejectTarget(log)
    setRejectReason('')
    setRejectError(null)
  }

  const closeRejectDialog = () => {
    if (rejectTarget && busy === rejectTarget.id) return
    setRejectTarget(null)
    setRejectReason('')
    setRejectError(null)
  }

  const confirmReject = () => {
    if (!rejectTarget || busy === rejectTarget.id) return
    const reason = rejectReason.trim() || undefined
    void decide(rejectTarget, 'rejected', reason)
  }

  const fmtTime = (iso?: string | null) => iso
    ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '-'
  const fmtVal = (v: unknown) => {
    if (v === null || v === undefined) return '∅'
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
    return s.length > 30 ? s.slice(0, 30) + '…' : s
  }
  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)
  const lastRefreshText = lastUpdatedAt
    ? new Date(lastUpdatedAt).toLocaleTimeString('zh-CN', {
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      })
    : '尚未完成'

  if (!currentReleaseId) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        当前本体没有有效的发布指针。为避免混入草稿数据，治理推演已停止加载。
      </div>
    )
  }

  if (isInitialLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-500">
        <Loader2 size={16} className="animate-spin" /> 正在加载当前发布版治理数据…
      </div>
    )
  }

  if (failedQuery) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        <p className="font-medium">当前发布版治理数据加载失败</p>
        <p className="mt-1 text-xs">{errorMessage(failedQuery.error)}。页面已停止展示，避免把失败误判为“暂无数据”。</p>
        <button type="button" onClick={reloadReleaseContext}
          className="mt-3 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-xs hover:bg-red-100">
          重新加载
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs text-teal-700">
        <span className="inline-flex items-center gap-2">
          <CheckCircle2 size={13} />
          数据范围已锁定到最新发布版 <span className="font-mono font-semibold">{currentReleaseVersion}</span>
        </span>
        <span className="hidden h-3 w-px bg-teal-200 sm:block" aria-hidden="true" />
        <span
          className="inline-flex items-center gap-1.5 font-medium"
          data-testid="governance-background-refresh-status"
          role="status"
          aria-live="polite"
          aria-busy={isRefreshing}
        >
          {(backgroundRefreshActive || isRefreshing)
            ? <Loader2 size={12} className={isPageVisible ? 'animate-spin' : ''} />
            : <CheckCircle2 size={12} />}
          {backgroundRefreshActive
            ? isPageVisible
              ? '结果监听中 · 正在短时同步可靠队列与哨兵结果'
              : '结果监听暂停 · 页面已隐藏，自动刷新已暂停'
            : isRefreshing ? '正在刷新治理结果' : '后台结果监听已结束'}
        </span>
        {backgroundRefreshActive && isPageVisible && (
          <span className="text-teal-600/70">每 12 秒刷新，最多 2 分钟</span>
        )}
        <span className="text-teal-600/80" data-testid="governance-last-refreshed">
          最近刷新：{lastRefreshText}
        </span>
        <button
          type="button"
          onClick={() => void refreshNow()}
          disabled={manualRefreshPending}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-teal-300 bg-white/80 px-2 py-1 font-medium text-teal-700 transition hover:bg-white disabled:cursor-wait disabled:opacity-60"
          aria-label="立即刷新治理结果"
          title="立即刷新，并重新开始最多 2 分钟的后台结果监听"
        >
          <RefreshCw size={11} className={manualRefreshPending ? 'animate-spin' : ''} />
          立即刷新
        </button>
      </div>
      {msg && (
        <div role={msg.ok ? 'status' : 'alert'} aria-live={msg.ok ? 'polite' : 'assertive'}
          className={`px-3 py-2 rounded-lg border text-xs ${
          msg.ok ? 'bg-green-50 border-green-200 text-green-700' : 'bg-red-50 border-red-200 text-red-600'
        }`}>{msg.text}</div>
      )}

      {/* ① 待审批 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={HandMetal} iconCls="text-blue-500" title="待审批"
          sub="人是最终裁决者 · 批准/拒绝都写入决策事实" />
        {pending.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">没有等待审批的动作。开启动作的「需人工审批」后，真实执行会先在这里等你拍板。</p>
        ) : (
          <div className="space-y-2">
            {pending.map(l => (
              <div key={l.id} className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50/50 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-gray-800 font-medium">{l.actionName || l.actionId}</p>
                  <p className="text-xs text-gray-500 truncate" title={l.objectInstanceId ? `实例 ID：${l.objectInstanceId}` : undefined}>
                    {l.objectInstanceId && <>目标 <span className="text-gray-600 font-medium">{readableTargetSummary(l, `${l.objectInstanceId.slice(0, 10)}…`)}</span> · </>}
                    {Object.entries(l.parameters || {}).slice(0, 3).map(([k, v]) => `${k}=${fmtVal(v)}`).join('，') || '无参数'}
                    <span className="text-gray-400"> · {l.actorId ? '人工发起' : '哨兵触发'} · {fmtTime(l.executedAt)}</span>
                  </p>
                </div>
                <button onClick={() => setApproveTarget(l)} disabled={busy === l.id}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 shrink-0">
                  {busy === l.id ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                  批准并执行
                </button>
                <button onClick={() => openRejectDialog(l)} disabled={busy === l.id}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs border border-red-300 text-red-600 hover:bg-red-50 disabled:opacity-50 shrink-0">
                  <XCircle size={12} /> 拒绝
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ② 自治等级 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={Rocket} iconCls="text-amber-500" title="自治等级"
          sub="影子 → 人审 → 自动：自治是按批准率挣来的" />
        {autonomy.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">还没有动作。请在版本草稿中创建动作并绑定哨兵，发布后再在这里管理放权等级。</p>
        ) : (
          <div className="space-y-2">
            {autonomy.map(s => {
              const meta = LEVEL_META[s.level]
              const r = s.decisions.recentApprovalRate
              return (
                <div key={s.actionId} className={`rounded-lg border px-3 py-2.5 ${
                  s.recommendation === 'promote' ? 'border-emerald-300 bg-emerald-50/40'
                  : s.recommendation === 'demote' ? 'border-red-300 bg-red-50/40' : 'border-gray-200'
                }`}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium ${meta.cls}`} title={meta.desc}>
                      <meta.icon size={11} /> {meta.label}
                    </span>
                    <span className="text-sm text-gray-800 font-medium">{s.actionName}</span>
                    {s.pending > 0 && <span className="text-[10px] px-1.5 rounded bg-blue-100 text-blue-700">{s.pending} 待审批</span>}
                    {s.sentinels.map(sn => (
                      <span key={sn.id} className={`text-[10px] px-1.5 rounded ${sn.muted ? 'bg-gray-100 text-gray-400' : 'bg-rose-50 text-rose-500'}`}>
                        {sn.name}{sn.muted ? '·影子' : ''}
                      </span>
                    ))}
                    <div className="ml-auto flex gap-1.5">
                      {s.level === 'L1' && (
                        <span
                          className="inline-flex"
                          title={s.recommendation === 'promote' ? '批准率达标，请在版本草稿中变更后重新发布'
                            : `晋升条件：近 ${s.thresholds.promoteMinDecisions} 次批准率 ≥ ${Math.round(s.thresholds.promoteRate * 100)}%（当前 ${s.decisions.recentCount} 次 / ${pct(r)}）`}
                        >
                          <button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
                            disabled={s.recommendation !== 'promote'}
                            className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] border disabled:pointer-events-none ${
                              s.recommendation === 'promote'
                                ? 'bg-emerald-600 text-white border-emerald-600 hover:bg-emerald-700'
                                : 'border-gray-200 text-gray-300 cursor-not-allowed'
                            }`}>
                            <ArrowUpCircle size={12} /> 去草稿晋升
                          </button>
                        </span>
                      )}
                      {s.level === 'L2' && (
                        <button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] border border-blue-300 text-blue-600 hover:bg-blue-50">
                          <ArrowDownCircle size={12} /> 去草稿调整
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-[11px] text-gray-500">
                    <span className="w-16 shrink-0">近期批准率</span>
                    <div className="flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                      <div className={`h-full rounded-full ${
                        r !== null && r >= s.thresholds.promoteRate ? 'bg-emerald-400'
                        : r !== null && r >= 0.5 ? 'bg-amber-400' : 'bg-red-300'
                      }`} style={{ width: `${Math.round((r ?? 0) * 100)}%` }} />
                    </div>
                    <span className="font-mono">{pct(r)}</span>
                    <span className="text-gray-400">({s.decisions.recentCount}/{s.thresholds.promoteMinDecisions})</span>
                    <span className="text-gray-400 ml-2">累计 👍{s.decisions.approved} 👎{s.decisions.rejected} · 自动 {s.autoRuns.total}{s.autoRuns.failed ? `（失败 ${s.autoRuns.failed}）` : ''}</span>
                  </div>
                  {s.recommendationReason && (
                    <p className={`mt-1.5 text-[11px] ${
                      s.recommendation === 'promote' ? 'text-emerald-600'
                      : s.recommendation === 'demote' ? 'text-red-600' : 'text-gray-500'
                    }`}>{s.recommendationReason}</p>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ③ 哨兵与触发 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={ShieldAlert} iconCls="text-rose-500" title="哨兵"
          sub="平台正在替你盯什么"
          extra={<button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
            className="text-xs text-rose-500 hover:underline inline-flex items-center gap-1">
            在版本草稿中修改 <ExternalLink size={11} /></button>} />
        {sentinels.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">还没有哨兵。哨兵 = 常驻监听条件 + 命中执行动作，是治理与推演的发动机。</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
            {sentinels.map(s => (
              <div key={s.id} className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-xs">
                <span className={`w-2 h-2 rounded-full shrink-0 ${!s.enabled ? 'bg-gray-300' : s.muted ? 'bg-amber-400' : 'bg-emerald-500'}`}
                  title={!s.enabled ? '已停用' : s.muted ? '影子（只记录不执行）' : '在线'} />
                <span className="text-gray-800 font-medium truncate">{s.displayName}</span>
                {s.muted && <span className="text-[10px] px-1 rounded bg-amber-50 text-amber-600 border border-amber-200">影子</span>}
                {s.condition && <code className="text-gray-400 truncate flex-1" title={s.condition}>{s.condition}</code>}
              </div>
            ))}
          </div>
        )}
        {firings.length > 0 && (
          <div className="space-y-1 pt-2 border-t">
            <p className="text-xs text-gray-400 mb-1.5">最近触发</p>
            {firings.slice(0, 8).map(f => (
              <div key={f.id} className="flex items-center gap-2 text-xs py-0.5">
                {f.status === 'error'
                  ? <AlertTriangle size={12} className="text-red-400 shrink-0" />
                  : <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                      f.status === 'fired' ? 'bg-rose-400' : f.status === 'no_match' ? 'bg-gray-300' : 'bg-amber-300'}`} />}
                <span className="text-gray-600 truncate max-w-[140px]">{f.sentinelName}</span>
                <span className={`px-1 rounded text-[10px] ${f.status === 'error' ? 'bg-red-50 text-red-500' : 'bg-gray-50 text-gray-400'}`}>{f.status}</span>
                <span className="text-gray-400">命中 {f.matchCount}</span>
                {f.error && <span className="text-red-400 truncate flex-1" title={f.error}>{f.error}</span>}
                <span className="ml-auto text-gray-400 shrink-0">{fmtTime(f.createdAt)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ④ 事实流 */}
      <div className="rounded-xl border bg-white p-4">
        <SectionHead icon={ScrollText} iconCls="text-indigo-500" title="事实流"
          sub="追加不修改 · 每个变化都有出处与因果"
          extra={
            <div className="flex gap-1">
              <button onClick={() => setKindFilter('')}
                className={`px-2 py-0.5 rounded-full text-[10px] border ${!kindFilter ? 'bg-indigo-50 border-indigo-300 text-indigo-600' : 'border-gray-200 text-gray-400 hover:text-gray-600'}`}>
                全部
              </button>
              {Object.entries(KIND_META).map(([k, m]) => (
                <button key={k} onClick={() => setKindFilter(k)} title={m.title}
                  className={`px-2 py-0.5 rounded-full text-[10px] border ${kindFilter === k ? m.cls : 'border-gray-200 text-gray-400 hover:text-gray-600'}`}>
                  {m.label}
                </button>
              ))}
            </div>
          } />
        {facts.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">暂无{kindFilter ? `「${KIND_META[kindFilter]?.label}」类` : ''}事实。</p>
        ) : (
          <div className="space-y-0.5 max-h-96 overflow-y-auto">
            {facts.map(f => (
              <div key={f.id} className="flex items-center gap-2 text-xs py-1 border-b border-gray-50 last:border-0">
                <span className={`px-1.5 py-0.5 rounded border text-[10px] shrink-0 ${KIND_META[f.kind]?.cls ?? KIND_META.property.cls}`}
                  title={KIND_META[f.kind]?.title}>
                  {KIND_META[f.kind]?.label ?? f.kind}
                </span>
                <span className="text-gray-600 truncate max-w-[140px]" title={f.subjectLabel}>{f.subjectLabel}</span>
                <span className="font-mono text-gray-400 truncate max-w-[110px]">{f.propertyName}</span>
                <span className="text-gray-300">=</span>
                <span
                  className="font-mono text-gray-700 truncate flex-1"
                  title={f.present === false ? '属性已删除' : String(fmtVal(f.value))}
                >
                  {f.present === false ? '（已删除）' : fmtVal(f.value)}
                </span>
                {f.causedBy && <span className="text-[10px] text-gray-400 shrink-0" title={`因果指针 → ${f.causedBy}`}>因果</span>}
                {f.supersedesId && <span className="text-[10px] text-violet-400 shrink-0" title="覆盖了旧事实">⤴</span>}
                <span className="text-gray-400 truncate max-w-[110px] shrink-0" title={f.source}>{f.source}</span>
                <span className="text-gray-400 shrink-0">{fmtTime(f.recordedAt)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal
        open={Boolean(rejectTarget)}
        onClose={closeRejectDialog}
        title={rejectTarget ? `拒绝动作：${rejectTarget.actionName || rejectTarget.actionId}` : '拒绝动作'}
        description="本次操作只会写入人工拒绝的决策事实，不会执行动作，也不会修改目标对象。"
        size="md"
        headerIcon={<XCircle size={19} className="text-red-600" />}
        footer={(
          <>
            <Button
              type="button"
              variant="outline"
              onClick={closeRejectDialog}
              disabled={Boolean(rejectTarget && busy === rejectTarget.id)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="danger"
              onClick={confirmReject}
              loading={Boolean(rejectTarget && busy === rejectTarget.id)}
              className="shadow-sm shadow-red-900/10"
            >
              确认拒绝
            </Button>
          </>
        )}
      >
        {rejectTarget && (
          <div className="space-y-4">
            <dl className="grid gap-3 rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm">
              <div className="grid gap-1 sm:grid-cols-[5rem_minmax(0,1fr)] sm:gap-3">
                <dt className="font-medium text-slate-500">动作</dt>
                <dd className="break-words font-medium text-slate-900">
                  {rejectTarget.actionName || rejectTarget.actionId}
                </dd>
              </div>
              <div className="grid gap-1 sm:grid-cols-[5rem_minmax(0,1fr)] sm:gap-3">
                <dt className="font-medium text-slate-500">目标摘要</dt>
                <dd className="min-w-0 space-y-1 text-slate-700">
                  <p className="break-words text-sm">
                    {readableTargetSummary(rejectTarget)}
                  </p>
                  {rejectTarget.objectInstanceId && (
                    <p className="break-all font-mono text-xs text-slate-500">
                      实例 {rejectTarget.objectInstanceId}
                    </p>
                  )}
                  <p className="break-words text-xs leading-5 text-slate-500">
                    {Object.entries(rejectTarget.parameters || {}).slice(0, 3)
                      .map(([key, value]) => `${key}=${fmtVal(value)}`).join('，') || '无参数'}
                  </p>
                </dd>
              </div>
            </dl>

            <div>
              <label htmlFor="governance-reject-reason"
                className="mb-1.5 block text-sm font-medium text-slate-800">
                拒绝原因
              </label>
              <textarea
                id="governance-reject-reason"
                value={rejectReason}
                onChange={event => {
                  setRejectReason(event.target.value)
                  if (rejectError) setRejectError(null)
                }}
                rows={4}
                autoFocus
                disabled={busy === rejectTarget.id}
                aria-describedby={`governance-reject-reason-help${rejectError ? ' governance-reject-error' : ''}`}
                aria-invalid={Boolean(rejectError)}
                placeholder="例如：当前风险信息不足，请补充证据后重新提交"
                className={`min-h-24 w-full resize-y rounded-lg border bg-white px-3 py-2.5 text-sm leading-6 text-slate-900 outline-none transition focus:ring-2 disabled:cursor-wait disabled:bg-slate-50 ${
                  rejectError
                    ? 'border-red-300 focus:border-red-400 focus:ring-red-100'
                    : 'border-slate-300 focus:border-teal-500 focus:ring-teal-100'
                }`}
              />
              <p id="governance-reject-reason-help" className="mt-1.5 text-xs leading-5 text-slate-500">
                可留空；填写后会与拒绝结果一起记录到决策事实，便于后续追溯。
              </p>
              {rejectError && (
                <p id="governance-reject-error" role="alert"
                  className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
                  拒绝提交失败：{rejectError}。请核对待办状态后重试。
                </p>
              )}
            </div>
          </div>
        )}
      </Modal>

      <ConfirmModal
        open={Boolean(approveTarget)}
        onClose={() => { if (!approveTarget || busy !== approveTarget.id) setApproveTarget(null) }}
        onConfirm={() => { if (approveTarget && busy !== approveTarget.id) void decide(approveTarget, 'approved') }}
        title={approveTarget ? `批准动作：${approveTarget.actionName || approveTarget.actionId}` : '批准动作'}
        description={approveTarget
          ? `将对「${readableTargetSummary(approveTarget)}」立即执行该动作，并写入人工批准的决策事实。执行由可靠队列异步完成，结果会同步到哨兵与事实流。`
          : undefined}
        confirmText="批准并执行"
        loading={Boolean(approveTarget && busy === approveTarget.id)}
      />
    </div>
  )
}
