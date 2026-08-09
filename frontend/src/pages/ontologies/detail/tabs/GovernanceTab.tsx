import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'
import { sentinelApi, type Sentinel, type SentinelFiring } from '@/api/sentinelApi'
import { useAuthStore } from '@/stores/authStore'
import { buildGovernanceKpis } from './governanceFormat'
import {
  CheckCircle2, HandMetal, Rocket, ShieldAlert, ScrollText,
  Loader2, RefreshCw,
} from 'lucide-react'
import ExecutionHero, { type HeroFlowCounts } from '../governance/ExecutionHero'
import PendingStoryList, { type PendingLog } from '../governance/PendingStoryList'
import AutonomyJourney from '../governance/AutonomyJourney'
import SentinelRadar from '../governance/SentinelRadar'
import FactStream, { type FactRow } from '../governance/FactStream'
import { ApproveDialog, RejectDialog } from '../governance/DecisionDialogs'
import {
  buildAutonomyTimeline, buildDailySpark,
} from '../governance/storyModel'
import '../governance/governanceNarrative.css'

/* 治理与推演驾驶舱 ——「执行故事线」版:
   ⓪ Hero      —— KPI + 近 7 日执行心电图 + 2.5D 本体执行链
   ① 待审批    —— 每条都是「起因 → 判定 → 后果」的故事,看明白再裁决
   ② 放权旅程  —— 等级路径 + 近期执行履历 + 批准率,放权靠履历来挣
   ③ 哨兵      —— 值守雷达,平台正在替你盯什么、最近命中了什么
   ④ 事实流    —— 每一个变化的出处与因果,全量留痕 */

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

interface WorkspaceActionDef {
  id: string; name?: string; displayName?: string; description?: string | null
  requiresApproval?: boolean
  rules?: Array<{ type: string; name?: string; enabled?: boolean; config?: Record<string, unknown> | null; description?: string }> | null
}

interface ActionLogRow {
  id: string; actionId: string; status?: string | null
  executedAt?: string | null; durationMs?: number | null
  decisionReason?: string | null; errorMessage?: string | null
  dryRun?: boolean | null; ontologyReleaseId?: string | null
}

interface OverviewLite {
  data?: {
    instances?: number
    linkInstances?: number
    instancesBySource?: Record<string, number>
    mappings?: { bound?: number; total?: number }
  }
  runtime?: {
    daily7d?: Array<{
      date: string
      firings?: { fired?: number; error?: number } | null
      actionRuns?: { success?: number; failed?: number } | null
    }>
  }
  facts?: { total?: number }
}

interface InstanceCatalogLite {
  objectTypes: Array<{ id: string; name: string; displayName?: string }>
}

interface GovernanceTabProps {
  ontologyId: string
  currentReleaseId?: string | null
  currentReleaseVersion: string
}

const BACKGROUND_REFRESH_INTERVAL_MS = 12_000
const BACKGROUND_REFRESH_MAX_CYCLES = 10
const SUCCESS_MESSAGE_DISMISS_MS = 5_000

function SectionHead({ icon: Icon, iconCls, title, sub, badge, extra }: {
  icon: any; iconCls: string; title: string; sub: string
  badge?: React.ReactNode; extra?: React.ReactNode
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Icon size={16} className={iconCls} />
      <p className="whitespace-nowrap text-[15px] font-semibold text-gray-800">{title}</p>
      {badge}
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
  const role = useAuthStore(state => state.user?.role)
  const canDecide = role === 'admin'
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [rejectTarget, setRejectTarget] = useState<PendingLog | null>(null)
  const [rejectError, setRejectError] = useState<string | null>(null)
  const [approveTarget, setApproveTarget] = useState<PendingLog | null>(null)
  const [approveError, setApproveError] = useState<string | null>(null)
  const [kindFilter, setKindFilter] = useState('')
  const [isPageVisible, setIsPageVisible] = useState(() =>
    typeof document === 'undefined' || document.visibilityState === 'visible')
  const [remainingRefreshCycles, setRemainingRefreshCycles] = useState(
    BACKGROUND_REFRESH_MAX_CYCLES)
  const [manualRefreshPending, setManualRefreshPending] = useState(false)
  const pendingRef = useRef<HTMLDivElement>(null)
  const autonomyRef = useRef<HTMLDivElement>(null)
  const sentinelsRef = useRef<HTMLDivElement>(null)
  const factsRef = useRef<HTMLDivElement>(null)

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
  // 叙事新增:动作定义(rules/description)、执行履历(自治时间线)、
  // 发布快照类型名(绑定句)、总览(心电图与执行链计数)。全部只读。
  const workspaceQuery = useQuery<{ actions?: WorkspaceActionDef[] }>({
    queryKey: ['gov-workspace', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/current-release/workspace`) as any,
    enabled: queryEnabled,
    staleTime: 60_000,
  })
  const logsQuery = useQuery<ActionLogRow[]>({
    queryKey: ['gov-logs', ontologyId, currentReleaseId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/logs`) as any,
    enabled: queryEnabled,
    staleTime: 30_000,
  })
  const catalogQuery = useQuery<InstanceCatalogLite>({
    queryKey: ['instance-browser-catalog', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/instance-browser/catalog`) as any,
    enabled: queryEnabled,
    staleTime: 60_000,
    retry: 1,
  })
  const overviewQuery = useQuery<OverviewLite>({
    queryKey: ['formal-overview', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/overview`) as any,
    enabled: queryEnabled,
    staleTime: 30_000,
    retry: 1,
  })

  const pending = pendingQuery.data ?? []
  const autonomy = autonomyQuery.data ?? []
  const sentinels = sentinelsQuery.data ?? []
  const firings = firingsQuery.data ?? []
  const facts = factsQuery.data ?? []
  const workspaceActions = workspaceQuery.data?.actions ?? []
  const releaseLogs = (logsQuery.data ?? []).filter(
    log => !currentReleaseId || !log.ontologyReleaseId || log.ontologyReleaseId === currentReleaseId,
  )
  const governanceQueries = [
    pendingQuery, autonomyQuery, sentinelsQuery, firingsQuery, factsQuery,
  ]
  const failedQuery = governanceQueries.find(query => query.isError)
  const isInitialLoading = governanceQueries.some(query => query.isLoading)
  const isRefreshing = governanceQueries.some(query => query.isFetching)
  const lastUpdatedAt = Math.max(
    0, ...governanceQueries.map(query => query.dataUpdatedAt))
  const backgroundRefreshActive = queryEnabled && remainingRefreshCycles > 0
  const kpis = buildGovernanceKpis({ pending, autonomy, sentinels })

  const objectTypeName = useCallback((objectTypeId: string) => {
    const hit = catalogQuery.data?.objectTypes?.find(item => item.id === objectTypeId)
    return hit ? hit.displayName || hit.name : objectTypeId || '未知类型'
  }, [catalogQuery.data])

  const overview = overviewQuery.data
  const flowCounts: HeroFlowCounts = {
    datasetsBound: overview?.data?.mappings?.bound ?? 0,
    instances: (overview?.data?.instances ?? 0) + (overview?.data?.linkInstances ?? 0),
    sentinelsOnline: kpis.sentinelsOnline,
    sentinelsTotal: kpis.sentinelsTotal,
    pendingCount: kpis.pendingCount,
    autoRuns: autonomy.reduce((sum, item) => sum + item.autoRuns.total, 0),
    factsTotal: overview?.facts?.total ?? facts.length,
  }
  const dailySpark = buildDailySpark(overview?.runtime?.daily7d)

  const scrollToSection = (ref: React.RefObject<HTMLDivElement | null>) => {
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const navigateHero = (section: 'pending' | 'autonomy' | 'sentinels' | 'facts') => {
    const target = section === 'pending' ? pendingRef
      : section === 'autonomy' ? autonomyRef
      : section === 'sentinels' ? sentinelsRef : factsRef
    scrollToSection(target)
  }

  const refreshAll = useCallback(() => Promise.all([
    qc.invalidateQueries({ queryKey: ['gov-pending', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-autonomy', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-sentinels', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-firings', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-facts', ontologyId] }),
    qc.invalidateQueries({ queryKey: ['gov-logs', ontologyId] }),
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

  // 成功反馈短暂展示后自动消退;错误反馈常驻,需用户注意。
  useEffect(() => {
    if (!msg?.ok) return
    const timer = window.setTimeout(() => setMsg(null), SUCCESS_MESSAGE_DISMISS_MS)
    return () => window.clearTimeout(timer)
  }, [msg])

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
    else setApproveError(null)
    try {
      await apiClientV2.post(`/formal/ontologies/${ontologyId}/action-logs/${log.id}/decide`,
        { decision, reason, releaseId: currentReleaseId })
      setMsg({ ok: true, text: decision === 'approved' ? '已批准并提交执行，决策已写入事实流。' : '已拒绝，决策已写入事实流。' })
      if (decision === 'approved') {
        setApproveTarget(null)
      }
      if (decision === 'rejected') {
        setRejectTarget(null)
      }
      setRemainingRefreshCycles(BACKGROUND_REFRESH_MAX_CYCLES)
      void refreshAll()
    } catch (e: any) {
      const text = errorMessage(e, '决策失败，请重试')
      if (decision === 'rejected') setRejectError(text)
      else setApproveError(text)
    } finally {
      setBusy(null)
    }
  }

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
    <div className="space-y-5">
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
              ? '自动同步中 · 正在获取最新的审批与哨兵结果'
              : '自动同步已暂停 · 页面隐藏中'
            : isRefreshing ? '正在刷新治理结果' : '自动同步已结束 · 可手动刷新'}
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
          title="立即刷新，并重新开始最多 2 分钟的自动同步"
        >
          <RefreshCw size={11} className={manualRefreshPending ? 'animate-spin' : ''} />
          立即刷新
        </button>
      </div>
      {msg && (
        <div role={msg.ok ? 'status' : 'alert'} aria-live={msg.ok ? 'polite' : 'assertive'}
          className={`rounded-lg border px-3 py-2 text-xs ${
          msg.ok ? 'border-green-200 bg-green-50 text-green-700' : 'border-red-200 bg-red-50 text-red-600'
        }`}>{msg.text}</div>
      )}

      <ExecutionHero
        kpis={kpis}
        dailySpark={dailySpark}
        flowCounts={flowCounts}
        isRefreshing={isRefreshing}
        onNavigate={navigateHero}
      />

      {/* ① 待审批 —— 待你裁决的故事 */}
      <div ref={pendingRef} className="rounded-xl border bg-white p-5">
        <SectionHead icon={HandMetal} iconCls="text-blue-500" title="待审批"
          badge={pending.length > 0 && (
            <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-medium text-blue-700">
              {pending.length} 项待处理
            </span>
          )}
          sub="每条都是一段「起因 → 判定 → 后果」的故事 · 批准/拒绝都会留痕" />
        {pending.length === 0 ? (
          <div className="flex flex-col items-center gap-1.5 py-4 text-center">
            <CheckCircle2 size={16} className="text-emerald-500" />
            <p className="text-xs text-gray-400">没有等待审批的动作。开启动作的「需人工审批」后，真实执行会先在这里等你拍板。</p>
          </div>
        ) : (
          <PendingStoryList
            ontologyId={ontologyId}
            pending={pending}
            firings={firings}
            sentinels={sentinels}
            actions={workspaceActions}
            objectTypeName={objectTypeName}
            canDecide={canDecide}
            busyId={busy}
            onApprove={log => {
              setApproveError(null)
              setApproveTarget(log)
            }}
            onReject={log => {
              setRejectError(null)
              setRejectTarget(log)
            }}
          />
        )}
      </div>

      {/* ② 放权旅程 */}
      <div ref={autonomyRef} className="rounded-xl border bg-white p-5">
        <SectionHead icon={Rocket} iconCls="text-amber-500" title="自治等级"
          sub="放权旅程:影子 → 人审 → 自动,自治是按历史执行效果挣来的" />
        {autonomy.length === 0 ? (
          <div className="py-3 text-center">
            <p className="text-xs text-gray-400">还没有动作。请在版本草稿中创建动作并绑定哨兵，发布后再在这里管理放权等级。</p>
            <button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
              className="mt-2 inline-flex items-center gap-1 text-xs text-amber-600 hover:underline">
              去版本草稿创建动作
            </button>
          </div>
        ) : (
          <div className="gov-stagger space-y-2.5">
            {autonomy.map(stat => (
              <AutonomyJourney
                key={stat.actionId}
                stat={stat}
                timeline={buildAutonomyTimeline(releaseLogs, stat.actionId)}
                onGoPending={() => scrollToSection(pendingRef)}
                onGoVersions={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ③ 哨兵 */}
      <div ref={sentinelsRef} className="rounded-xl border bg-white p-5">
        <SectionHead icon={ShieldAlert} iconCls="text-rose-500" title="哨兵"
          sub="值守雷达:平台正在替你盯什么"
          extra={<button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
            className="inline-flex items-center gap-1 text-xs text-rose-500 hover:underline">
            在版本草稿中修改</button>} />
        <SentinelRadar
          sentinels={sentinels}
          firings={firings}
          onGoVersions={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}
        />
      </div>

      {/* ④ 事实流 */}
      <div ref={factsRef} className="rounded-xl border bg-white p-5">
        <SectionHead icon={ScrollText} iconCls="text-indigo-500" title="事实流"
          sub="追加不修改 · 每个变化都有出处与因果 · 最近 50 条" />
        <FactStream facts={facts} kindFilter={kindFilter} onKindFilterChange={setKindFilter} />
      </div>

      <RejectDialog
        target={rejectTarget}
        busy={Boolean(rejectTarget && busy === rejectTarget.id)}
        error={rejectError}
        onClose={() => {
          if (rejectTarget && busy === rejectTarget.id) return
          setRejectTarget(null)
          setRejectError(null)
        }}
        onConfirm={reason => {
          if (!rejectTarget || busy === rejectTarget.id) return
          void decide(rejectTarget, 'rejected', reason)
        }}
      />
      <ApproveDialog
        target={approveTarget}
        busy={Boolean(approveTarget && busy === approveTarget.id)}
        error={approveError}
        onClose={() => {
          if (approveTarget && busy === approveTarget.id) return
          setApproveTarget(null)
          setApproveError(null)
        }}
        onConfirm={() => {
          if (!approveTarget || busy === approveTarget.id) return
          void decide(approveTarget, 'approved')
        }}
      />
    </div>
  )
}
