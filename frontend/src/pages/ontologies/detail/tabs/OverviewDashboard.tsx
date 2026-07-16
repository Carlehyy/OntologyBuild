import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2, ChevronRight, CircleAlert, Clock3,
  Database, FileCheck2, FileX2, GitBranch, Loader2, PackageCheck, ShieldCheck,
  Sparkles, XCircle,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import type { OntologyDetail } from '@/types/ontology'
import './overview-dashboard.css'

interface Overview {
  model: {
    objectTypes: number; linkTypes: number; actions: number
    actionsRequiringApproval: number; functions: number
    sentinels: { total: number; enabled: number; muted: number }
  }
  data: {
    instances: number; instancesBySource: Record<string, number>
    linkInstances: number
    mappings: { total: number; bound: number; nameMatch: number; autoCreate: number; autoApply: number }
    topTypes: { id: string; name: string; count: number }[]
  }
  runtime: {
    pendingApprovals: number
    decisions: { total: number; approved: number; rejected: number; recentApprovalRate: number | null }
    firings7d: { total: number; fired: number; error: number }
    actionRuns7d: { total: number; success: number; failed: number }
  }
  facts: { total: number; byKind: Record<string, number> }
}

interface PendingLog {
  id: string
  actionId: string
  actionName: string | null
  objectTypeName: string | null
  objectInstanceId: string | null
  objectInstanceLabel: string | null
  parameters: Record<string, unknown>
  actorId: string | null
  triggerSource: 'sentinel' | 'manual' | 'system' | null
  ontologyVersion: string | null
  executedAt: string
}

interface FactRow {
  id: string
  subjectLabel: string
  propertyName: string
  value: unknown
  kind: string
  source: string
  recordedAt: string | null
}

const FACT_META: Record<string, { label: string; color: string; className: string }> = {
  property: { label: '属性', color: '#7c5ce0', className: 'fact-property' },
  derived: { label: '派生', color: '#9d7ee8', className: 'fact-derived' },
  link: { label: '链接', color: '#3b82f6', className: 'fact-link' },
  decision: { label: '决策', color: '#f59e0b', className: 'fact-decision' },
  object: { label: '存在', color: '#ef6464', className: 'fact-object' },
  absence: { label: '缺席', color: '#aeb8c6', className: 'fact-absence' },
}

const SOURCE_LABEL: Record<string, string> = {
  manual: '手工录入', pipeline: '管道灌入', collector: '采集器', import: '批量导入', action: '动作生成',
}

const TRIGGER_LABEL: Record<string, string> = {
  sentinel: '哨兵命中', manual: '人工发起', system: '系统触发',
}

const formatDateTime = (iso?: string | null, compact = false) => {
  if (!iso) return '—'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return '—'
  return value.toLocaleString('zh-CN', compact
    ? { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }
    : { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

const formatRelativeTime = (iso?: string | null) => {
  if (!iso) return '时间未记录'
  // SQLite 会丢失 timezone 后缀，但后端时间统一按 UTC 写入；补回 Z，避免东八区显示成 8 小时前。
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`
  const timestamp = new Date(normalized).getTime()
  if (Number.isNaN(timestamp)) return '时间未记录'
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000))
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  return days < 30 ? `${days}天前` : formatDateTime(iso, true)
}

const formatValue = (value: unknown, max = 30) => {
  if (value === null || value === undefined) return '∅'
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return text.length > max ? `${text.slice(0, max)}…` : text
}

const firstParameter = (parameters: Record<string, unknown>) => {
  const first = Object.entries(parameters ?? {})[0]
  return first ? `${first[0]} = ${formatValue(first[1], 18)}` : '无需额外参数'
}

function NetworkMark() {
  return (
    <svg viewBox="0 0 92 92" role="img" aria-label="本体网络" className="overview-network-mark">
      <path d="M21 25 44 17l25 13 2 29-25 17-27-13Z" fill="none" stroke="currentColor" strokeWidth="3.2" />
      <path d="m21 25 25 19 23-14M46 44v32M19 63l27-19 25 15" fill="none" stroke="currentColor" strokeWidth="2.4" opacity=".72" />
      {[[21, 25], [44, 17], [69, 30], [71, 59], [46, 76], [19, 63], [46, 44]].map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="5.2" fill="white" stroke="currentColor" strokeWidth="3" />
      ))}
    </svg>
  )
}

function PanelTitle({ title, sub, action }: { title: string; sub?: string; action?: React.ReactNode }) {
  return (
    <div className="overview-panel-title">
      <div className="overview-panel-title-copy">
        <h2>{title}</h2>
        {sub && <span>{sub}</span>}
      </div>
      {action}
    </div>
  )
}

function ApprovalPipeline({
  pending, selected, busy, message, loading, failed, onSelect, onDecide, onRetry, onViewAll,
}: {
  pending: PendingLog[]
  selected: PendingLog | null
  busy: string | null
  message: { ok: boolean; text: string } | null
  loading: boolean
  failed: boolean
  onSelect: (item: PendingLog) => void
  onDecide: (item: PendingLog, decision: 'approved' | 'rejected') => void
  onRetry: () => void
  onViewAll: () => void
}) {
  const targetLabel = selected?.objectInstanceLabel || selected?.objectTypeName || '未绑定业务对象'
  return (
    <section className="overview-panel approval-pipeline" aria-labelledby="approval-title">
      <header className="approval-header">
        <div className="approval-title-copy">
          <h2 id="approval-title">待审批流水线</h2>
          <span>动作在人工闸口暂停，批准或拒绝都会写入决策事实</span>
        </div>
        <div className="approval-header-actions">
          <div className="approval-summary" aria-live="polite">
            <span className={pending.length ? 'approval-count is-active' : 'approval-count'}>{loading ? '—' : pending.length}</span>
            <span>件待处理</span>
          </div>
          <button className="overview-link-button approval-view-all" onClick={onViewAll}>
            查看全部审批 <ChevronRight aria-hidden="true" />
          </button>
        </div>
      </header>

      <div
        className={`approval-workbench ${pending.length === 0 ? 'is-empty' : ''}`}
        data-testid="approval-pipeline"
        data-pending-count={pending.length}
      >
        <div className="approval-belt" aria-label="待审批动作包裹">
          <span className="belt-entry-label">动作进入</span>
          <span className="belt-entry-chute" aria-hidden="true" />
          <div className="belt-items">
            {pending.slice(0, 2).map((item) => (
              <button
                key={item.id}
                type="button"
                className={`approval-package ${selected?.id === item.id ? 'is-selected' : ''}`}
                onClick={() => onSelect(item)}
                aria-pressed={selected?.id === item.id}
              >
                <span className="package-tape" aria-hidden="true" />
                <span className="package-name">{item.actionName || '未命名动作'}</span>
                <span className="package-target">{item.objectInstanceLabel || item.objectTypeName || '未绑定业务对象'}</span>
                <span className="package-parameter">{firstParameter(item.parameters)}</span>
                <span className="package-source">{TRIGGER_LABEL[item.triggerSource || 'system']} · {formatRelativeTime(item.executedAt)}</span>
                <span className="package-version">{item.ontologyVersion || '版本未记录'}</span>
              </button>
            ))}
            {!loading && !failed && pending.length === 0 && (
              <div className="approval-empty-package">
                <PackageCheck size={28} />
                <span>流水线已清空</span>
                <small>新的审批请求会自动送达这里</small>
              </div>
            )}
            {loading && (
              <div className="approval-empty-package"><Loader2 className="spin" size={26} /><span>正在读取审批流水线</span></div>
            )}
            {failed && (
              <button type="button" className="approval-empty-package approval-load-error" onClick={onRetry}>
                <CircleAlert size={26} /><span>审批队列加载失败</span><small>点击重试</small>
              </button>
            )}
          </div>
          <div className="belt-rail belt-rail-top" />
          <div className="belt-rollers" aria-hidden="true">
            {Array.from({ length: 6 }, (_, index) => <span key={index} />)}
          </div>
          <div className="belt-direction" aria-hidden="true">
            <span>›››</span><span>›››</span>
          </div>
        </div>

        <div className="approval-gate">
          <strong>人工审批闸口</strong>
          <div className="gate-beacon" aria-hidden="true" />
          <div className="gate-frame" aria-hidden="true">
            {selected && <span className="gate-package"><i /></span>}
          </div>
        </div>

        <div className="approval-inspector" data-testid="approval-inspector">
          {selected ? (
            <>
              <h3>{selected.actionName || '未命名动作'}</h3>
              <p>{targetLabel}</p>
              <div className="inspector-actions">
                <button className="approve-button" disabled={busy === selected.id} onClick={() => onDecide(selected, 'approved')}>
                  {busy === selected.id ? <Loader2 className="spin" /> : <CheckCircle2 />} 批准并执行
                </button>
                <button className="reject-button" disabled={busy === selected.id} onClick={() => onDecide(selected, 'rejected')}>
                  <XCircle /> 拒绝
                </button>
              </div>
            </>
          ) : (
            <div className="inspector-empty"><CheckCircle2 size={32} /><strong>审批已处理完毕</strong><span>当前没有等待人工决策的动作</span></div>
          )}
          {message && <p className={`approval-message ${message.ok ? 'is-success' : 'is-error'}`}>{message.text}</p>}
        </div>

        <div className="approval-routes" aria-hidden="true">
          <span className="route-brace" />
          <div className="route-outcome route-approved"><FileCheck2 /><strong>执行并留痕</strong></div>
          <div className="route-outcome route-rejected"><FileX2 /><strong>拒绝并留痕</strong></div>
        </div>
      </div>
    </section>
  )
}

export default function OverviewDashboard({ ontologyId, ontology, onGoGroup }: {
  ontologyId: string
  ontology: OntologyDetail
  onGoGroup: (group: string) => void
}) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const overviewQuery = useQuery<Overview>({
    queryKey: ['formal-overview', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/overview`) as Promise<Overview>,
    refetchInterval: 30000,
  })
  const factsQuery = useQuery<FactRow[]>({
    queryKey: ['recent-facts', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/facts/recent?limit=8`) as Promise<FactRow[]>,
    refetchInterval: 30000,
  })
  const pendingQuery = useQuery<PendingLog[]>({
    queryKey: ['gov-pending', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/pending-actions`) as Promise<PendingLog[]>,
    refetchInterval: 15000,
  })

  const pending = pendingQuery.data ?? []
  useEffect(() => {
    if (pending.length === 0) {
      setSelectedId(null)
      return
    }
    if (!selectedId || !pending.some(item => item.id === selectedId)) setSelectedId(pending[0].id)
  }, [pending, selectedId])
  const selected = pending.find(item => item.id === selectedId) ?? pending[0] ?? null

  const refreshOverview = () => {
    queryClient.invalidateQueries({ queryKey: ['gov-pending', ontologyId] })
    queryClient.invalidateQueries({ queryKey: ['formal-overview', ontologyId] })
    queryClient.invalidateQueries({ queryKey: ['recent-facts', ontologyId] })
    queryClient.invalidateQueries({ queryKey: ['gov-facts', ontologyId] })
    queryClient.invalidateQueries({ queryKey: ['gov-autonomy', ontologyId] })
  }

  const decide = async (item: PendingLog, decision: 'approved' | 'rejected') => {
    setBusy(item.id)
    setMessage(null)
    try {
      const result = await apiClientV2.post<any>(
        `/formal/ontologies/${ontologyId}/action-logs/${item.id}/decide`, { decision })
      const executionFailed = decision === 'approved' && result?.executionLog?.status === 'failed'
      setMessage(executionFailed
        ? { ok: false, text: '审批已留痕，但动作执行失败；请到运行记录查看原因。' }
        : { ok: true, text: decision === 'approved' ? '已批准并执行，决策已进入事实流。' : '已拒绝，决策已进入事实流。' })
      refreshOverview()
    } catch (error: any) {
      setMessage({ ok: false, text: error?.detail?.message || error?.detail || error?.message || '审批失败，请稍后重试。' })
    } finally {
      setBusy(null)
    }
  }

  if (overviewQuery.isLoading || !overviewQuery.data) {
    return <div className="overview-loading"><Loader2 className="spin" size={20} /> 正在读取当前发布投影…</div>
  }
  if (overviewQuery.isError) {
    return <div className="overview-error"><CircleAlert size={20} /> 当前发布投影读取失败，请刷新后重试。</div>
  }

  const ov = overviewQuery.data
  const facts = factsQuery.data ?? []
  const modelParts = [
    { label: '对象实体', value: ov.model.objectTypes, color: '#3b82f6' },
    { label: '实体关系', value: ov.model.linkTypes, color: '#0ba78f' },
    { label: '动作', value: ov.model.actions, color: '#7c63db' },
    { label: '函数', value: ov.model.functions, color: '#b5bfcb' },
    { label: '哨兵', value: ov.model.sentinels.total, color: '#ffad3d' },
  ]
  const modelTotal = modelParts.reduce((sum, item) => sum + item.value, 0)
  let modelCursor = 0
  const modelGradient = modelParts.map(item => {
    const start = modelCursor
    modelCursor += modelTotal ? (item.value / modelTotal) * 100 : 0
    return `${item.color} ${start}% ${modelCursor}%`
  }).join(', ')

  const factParts = Object.entries(ov.facts.byKind)
    .filter(([, value]) => value > 0)
    .sort(([a], [b]) => (FACT_META[a] ? 0 : 1) - (FACT_META[b] ? 0 : 1))
  const maxRuntime = Math.max(ov.runtime.firings7d.fired, ov.runtime.firings7d.error,
    ov.runtime.actionRuns7d.success, ov.runtime.actionRuns7d.failed, 1)
  const sourceEntries = Object.entries(ov.data.instancesBySource).sort((a, b) => b[1] - a[1])
  const sourceTotal = Math.max(ov.data.instances, 1)
  const boundPct = ov.data.mappings.total ? Math.round((ov.data.mappings.bound / ov.data.mappings.total) * 100) : 0

  return (
    <main className="overview-dashboard" aria-label="本体总览">
      <div className="overview-hero-grid">
        <section className="overview-panel ontology-profile">
          <PanelTitle title="本体概况" />
          <div className="profile-heading">
            <div className="profile-mark"><NetworkMark /></div>
            <div>
              <h1>{ontology.name}</h1>
              <span className="profile-domain">{ontology.domain || '未分类'}</span>
            </div>
          </div>
          <p className="profile-description">{ontology.description || '暂无本体描述。可在本体管理中补充业务范围与使用说明。'}</p>
          <dl className="profile-meta">
            <div><dt>当前发布</dt><dd>{ontology.current_release_version || ontology.version || '—'}</dd></div>
            <div><dt>创建时间</dt><dd>{formatDateTime(ontology.created_at)}</dd></div>
            <div><dt>更新时间</dt><dd>{formatDateTime(ontology.updated_at)}</dd></div>
          </dl>
          <div className="profile-trust"><ShieldCheck size={18} /><span>总览数据来自当前发布投影</span></div>
        </section>

        <section className="overview-panel kpi-rail" aria-label="关键指标">
          <button type="button" className="kpi-cell" onClick={() => onGoGroup('design')}>
            <span className="kpi-label">当前发布结构</span>
            <strong>{ov.model.objectTypes}<small>对象实体</small></strong>
            <p>关系 {ov.model.linkTypes} · 动作 {ov.model.actions} · 函数 {ov.model.functions} · 哨兵 {ov.model.sentinels.total}</p>
            {ov.model.actions === 0
              ? <em className="kpi-status is-neutral"><CircleAlert size={15} />暂无动作类型</em>
              : ov.model.actionsRequiringApproval > 0
              ? <em className="kpi-status is-warning"><CircleAlert size={15} />{ov.model.actionsRequiringApproval} 个动作类型需人工审批</em>
              : <em className="kpi-status"><CheckCircle2 size={15} />动作均无需人工审批</em>}
          </button>
          <button type="button" className="kpi-cell" onClick={() => onGoGroup('data')}>
            <span className="kpi-label">当前实例投影</span>
            <strong>{ov.data.instances}<small>对象实例</small></strong>
            <p>链接实例 {ov.data.linkInstances}</p>
            <em className="kpi-status is-neutral">
              {sourceEntries.map(([source, count]) => `${SOURCE_LABEL[source] || source} ${count}`).join(' · ') || '暂无实例数据'}
            </em>
          </button>
          <button type="button" className="kpi-cell" onClick={() => onGoGroup('data-mapping')}>
            <span className="kpi-label">数据映射</span>
            <strong>{ov.data.mappings.bound}<small>/ {ov.data.mappings.total} 已显式绑定</small></strong>
            <p>名称匹配 {ov.data.mappings.nameMatch} · 数据自建 {ov.data.mappings.autoCreate}</p>
            <em className={`kpi-status ${ov.data.mappings.total === 0 ? 'is-neutral' : ov.data.mappings.bound === ov.data.mappings.total ? '' : 'is-warning'}`}>
              {ov.data.mappings.total === 0
                ? <><Database size={15} />暂无映射记录</>
                : ov.data.mappings.bound === ov.data.mappings.total
                ? <><CheckCircle2 size={15} />当前未发现未绑定映射</>
                : <><CircleAlert size={15} />{ov.data.mappings.total - ov.data.mappings.bound} 条映射待确认</>}
            </em>
          </button>
          <button type="button" className="kpi-cell" onClick={() => onGoGroup('governance')}>
            <span className="kpi-label">累计事实流</span>
            <strong>{ov.facts.total}<small>条</small></strong>
            <p>{factParts.slice(0, 3).map(([kind, value]) => `${FACT_META[kind]?.label || kind} ${value}`).join(' · ') || '尚无事实记录'}</p>
            <em className="kpi-status is-purple"><GitBranch size={15} />追加式留痕，可回放与追溯</em>
          </button>
        </section>

        <ApprovalPipeline
          pending={pending}
          selected={selected}
          busy={busy}
          message={message}
          loading={pendingQuery.isLoading}
          failed={pendingQuery.isError}
          onSelect={item => { setSelectedId(item.id); setMessage(null) }}
          onDecide={decide}
          onRetry={() => pendingQuery.refetch()}
          onViewAll={() => onGoGroup('governance')}
        />
      </div>

      <div className="overview-row overview-row-model">
        <section className="overview-panel model-composition">
          <PanelTitle title="模型资产构成" sub="当前发布版本" />
          <div className="model-composition-body">
            <div className="overview-donut" style={{ background: modelTotal ? `conic-gradient(${modelGradient})` : '#eef2f6' }}>
              <div><strong>{modelTotal}</strong><span>项模型资产</span></div>
            </div>
            <div className="model-legend">
              <div className="legend-head"><span>类型</span><span>数量</span></div>
              {modelParts.map(item => (
                <div key={item.label}><span><i style={{ background: item.color }} />{item.label}</span><strong>{item.value}</strong></div>
              ))}
            </div>
          </div>
        </section>

        <section className="overview-panel runtime-summary">
          <PanelTitle title="近 7 日运行汇总" sub="近 7 日聚合 · 分类对比" action={<button className="overview-link-button" onClick={() => onGoGroup('governance')}>查看运行记录 <ChevronRight size={15} /></button>} />
          <div className="runtime-chart">
            <div className="runtime-group">
              <span className="runtime-group-name">哨兵评估</span>
              {[
                ['命中', ov.runtime.firings7d.fired, 'teal'], ['错误', ov.runtime.firings7d.error, 'slate'],
              ].map(([label, value, tone]) => (
                <div className="runtime-bar-item" key={String(label)}>
                  <span className="runtime-value">{value}</span>
                  <span className={`runtime-bar tone-${tone}`} style={{ height: `${Math.max((Number(value) / maxRuntime) * 122, Number(value) ? 12 : 2)}px` }} />
                  <strong>{label}</strong>
                </div>
              ))}
            </div>
            <div className="runtime-divider" />
            <div className="runtime-group">
              <span className="runtime-group-name">动作执行</span>
              {[
                ['成功', ov.runtime.actionRuns7d.success, 'blue'], ['失败', ov.runtime.actionRuns7d.failed, 'slate'],
              ].map(([label, value, tone]) => (
                <div className="runtime-bar-item" key={String(label)}>
                  <span className="runtime-value">{value}</span>
                  <span className={`runtime-bar tone-${tone}`} style={{ height: `${Math.max((Number(value) / maxRuntime) * 122, Number(value) ? 12 : 2)}px` }} />
                  <strong>{label}</strong>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      <div className="overview-row overview-row-data">
        <section className="overview-panel instance-distribution">
          <PanelTitle title="实例分布与来源" sub="当前发布投影" action={<button className="overview-link-button" onClick={() => onGoGroup('data')}>查看实例数据 <ChevronRight size={15} /></button>} />
          <div className="instance-body">
            <div className="type-bars">
              <h3>实例按类型分布</h3>
              {(ov.data.topTypes.length ? ov.data.topTypes.slice(0, 4) : [{ id: 'empty', name: '暂无实例类型', count: 0 }]).map(item => {
                const max = Math.max(...ov.data.topTypes.map(type => type.count), 1)
                return <div className="horizontal-stat" key={item.id}><span title={item.name}>{item.name}</span><i><b style={{ width: `${(item.count / max) * 100}%` }} /></i><strong>{item.count}</strong></div>
              })}
            </div>
            <div className="source-share">
              <h3>实例来源（按数量占比）</h3>
              <div className="source-stack">
                {sourceEntries.length ? sourceEntries.map(([source, count], index) => (
                  <span key={source} className={`source-${index % 4}`} style={{ width: `${(count / sourceTotal) * 100}%` }}>{count >= sourceTotal * .12 ? `${count} (${Math.round(count / sourceTotal * 100)}%)` : ''}</span>
                )) : <span className="source-empty" style={{ width: '100%' }}>暂无数据</span>}
              </div>
              <div className="source-legend">{sourceEntries.map(([source, count], index) => <span key={source}><i className={`source-${index % 4}`} />{SOURCE_LABEL[source] || source} {count}</span>)}</div>
            </div>
          </div>
        </section>

        <section className="overview-panel mapping-status">
          <PanelTitle title="映射状态" sub="当前生效映射" action={<button className="overview-link-button" onClick={() => onGoGroup('data-mapping')}>查看数据映射 <ChevronRight size={15} /></button>} />
          <div className="mapping-body">
            <div className="mapping-ring" style={{ '--mapping-pct': `${boundPct * 3.6}deg` } as React.CSSProperties}>
              <div><strong>{ov.data.mappings.bound} / {ov.data.mappings.total}</strong><span>已显式绑定</span></div>
            </div>
            <div className="mapping-legend">
              <div><span><i className="map-bound" />显式绑定</span><strong>{ov.data.mappings.bound}</strong></div>
              <div><span><i className="map-name" />名称匹配</span><strong>{ov.data.mappings.nameMatch}</strong></div>
              <div><span><i className="map-auto" />数据自建</span><strong>{ov.data.mappings.autoCreate}</strong></div>
              <p className={ov.data.mappings.total === 0 ? 'is-neutral' : ov.data.mappings.bound === ov.data.mappings.total ? 'is-good' : 'is-warning'}>
                {ov.data.mappings.total === 0 ? <Database size={17} /> : ov.data.mappings.bound === ov.data.mappings.total ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}
                {ov.data.mappings.total === 0 ? '暂无生效映射' : ov.data.mappings.bound === ov.data.mappings.total ? '所有映射均已绑定现有对象实体' : '仍有映射需要人工确认'}
              </p>
            </div>
          </div>
        </section>
      </div>

      <div className="overview-row overview-row-facts">
        <section className="overview-panel fact-composition">
          <PanelTitle title="事实类型构成" sub={`累计事实 ${ov.facts.total}`} />
          <div className="fact-stack">
            {factParts.length ? factParts.map(([kind, count]) => (
              <span key={kind} style={{ width: `${Math.max(count / Math.max(ov.facts.total, 1) * 100, 1)}%`, background: FACT_META[kind]?.color || '#64748b' }}>
                {count / Math.max(ov.facts.total, 1) > .08 ? `${FACT_META[kind]?.label || kind} ${count} (${(count / ov.facts.total * 100).toFixed(1)}%)` : ''}
              </span>
            )) : <span className="fact-empty">暂无事实</span>}
          </div>
          <div className="fact-legend-row">
            <div className="fact-legend">
              {factParts.map(([kind, count]) => <span key={kind}><i style={{ background: FACT_META[kind]?.color || '#64748b' }} />{FACT_META[kind]?.label || kind} {count} ({(count / Math.max(ov.facts.total, 1) * 100).toFixed(1)}%)</span>)}
            </div>
            <p><ShieldCheck size={18} />事实追加不修改，可按时间回放与追溯</p>
          </div>
        </section>

        <section className="overview-panel recent-facts">
          <PanelTitle title="最近发生了什么" sub="事实流 · 追加不修改" action={<button className="overview-link-button" onClick={() => onGoGroup('governance')}>查看全部 <ChevronRight size={15} /></button>} />
          {factsQuery.isLoading ? <div className="recent-empty"><Loader2 className="spin" size={18} />正在读取事实流…</div> : facts.length === 0 ? (
            <div className="recent-empty"><Sparkles size={20} />还没有事实记录；数据灌入或动作执行后会在这里留痕。</div>
          ) : (
            <div className="recent-list">
              {facts.slice(0, 6).map((fact, index) => (
                <div className="recent-row" key={fact.id}>
                  <span className="timeline-dot" style={{ background: FACT_META[fact.kind]?.color || '#64748b' }}>{index === 5 ? '' : <i />}</span>
                  <span className={`fact-chip ${FACT_META[fact.kind]?.className || 'fact-property'}`}>{FACT_META[fact.kind]?.label || fact.kind}</span>
                  <strong title={fact.subjectLabel}>{fact.subjectLabel}</strong>
                  <code title={fact.propertyName}>{fact.propertyName}</code>
                  <span className="fact-equals">=</span>
                  <span className="fact-value" title={formatValue(fact.value, 100)}>{formatValue(fact.value)}</span>
                  <span className="fact-source">{SOURCE_LABEL[fact.source] || fact.source}</span>
                  <time><Clock3 size={12} />{formatDateTime(fact.recordedAt, true)}</time>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
