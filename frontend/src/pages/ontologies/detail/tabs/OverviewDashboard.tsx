import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity, CheckCircle2, ChevronRight, CircleAlert, Clock3, Database,
  GitBranch, Loader2, ShieldCheck, Sparkles, Workflow,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import type { OntologyDetail } from '@/types/ontology'
import VersionEvolutionCard from './VersionEvolutionCard'
import './overview-dashboard.css'

interface Overview {
  release: { id: string; version: string; publishedAt: string | null }
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
    daily7d: {
      date: string
      firings: { fired: number; error: number }
      actionRuns: { success: number; failed: number }
    }[]
  }
  facts: { total: number; byKind: Record<string, number> }
}

interface FactRow {
  id: string
  subjectLabel: string
  propertyName: string
  value: unknown
  present?: boolean
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

const formatDateTime = (iso?: string | null, compact = false) => {
  if (!iso) return '—'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return '—'
  return value.toLocaleString('zh-CN', compact
    ? { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }
    : { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

const formatRuntimeDay = (date: string) => {
  const value = new Date(`${date}T00:00:00`)
  if (Number.isNaN(value.getTime())) return date
  return value.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

const formatValue = (value: unknown, max = 30) => {
  if (value === null || value === undefined) return '∅'
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return text.length > max ? `${text.slice(0, max)}…` : text
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

export default function OverviewDashboard({ ontologyId, ontology, onGoGroup }: {
  ontologyId: string
  ontology: OntologyDetail
  onGoGroup: (group: string) => void
}) {
  const [runtimeRange, setRuntimeRange] = useState<[number, number]>([0, 6])

  const overviewQuery = useQuery<Overview>({
    queryKey: ['formal-overview', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/overview`) as Promise<Overview>,
    refetchInterval: 30000,
  })
  const factsQuery = useQuery<FactRow[]>({
    queryKey: ['recent-facts', ontologyId],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/facts/recent?limit=8&current_release_only=true`) as Promise<FactRow[]>,
    refetchInterval: 30000,
  })
  const runtimeDayCount = overviewQuery.data?.runtime.daily7d?.length || 7
  useEffect(() => {
    setRuntimeRange([0, Math.max(runtimeDayCount - 1, 0)])
  }, [ontologyId, runtimeDayCount])

  if (overviewQuery.isLoading || !overviewQuery.data) {
    return <div className="overview-loading"><Loader2 className="spin" size={20} /> 正在读取当前发布投影…</div>
  }
  if (overviewQuery.isError) {
    return <div className="overview-error"><CircleAlert size={20} /> 当前发布投影读取失败，请刷新后重试。</div>
  }

  const ov = overviewQuery.data
  const facts = factsQuery.data ?? []
  const factParts = Object.entries(ov.facts.byKind)
    .filter(([, value]) => value > 0)
    .sort(([a], [b]) => (FACT_META[a] ? 0 : 1) - (FACT_META[b] ? 0 : 1))
  const sourceEntries = Object.entries(ov.data.instancesBySource).sort((a, b) => b[1] - a[1])
  const runtimeDays = ov.runtime.daily7d?.length ? ov.runtime.daily7d : Array.from({ length: 7 }, (_, index) => {
    const date = new Date()
    date.setDate(date.getDate() - (6 - index))
    return {
      date: date.toISOString().slice(0, 10),
      firings: {
        fired: index === 6 ? ov.runtime.firings7d.fired : 0,
        error: index === 6 ? ov.runtime.firings7d.error : 0,
      },
      actionRuns: {
        success: index === 6 ? ov.runtime.actionRuns7d.success : 0,
        failed: index === 6 ? ov.runtime.actionRuns7d.failed : 0,
      },
    }
  })
  const runtimeRangeEnd = Math.min(runtimeRange[1], runtimeDays.length - 1)
  const runtimeRangeStart = Math.min(runtimeRange[0], runtimeRangeEnd)
  const selectedRuntimeDays = runtimeDays.slice(runtimeRangeStart, runtimeRangeEnd + 1)
  const selectedRuntime = selectedRuntimeDays.reduce((summary, day) => ({
    fired: summary.fired + day.firings.fired,
    error: summary.error + day.firings.error,
    success: summary.success + day.actionRuns.success,
    failed: summary.failed + day.actionRuns.failed,
  }), { fired: 0, error: 0, success: 0, failed: 0 })
  const maxDailyRuntime = Math.max(...runtimeDays.flatMap(day => [
    day.firings.fired + day.firings.error,
    day.actionRuns.success + day.actionRuns.failed,
  ]), 1)
  const selectedRuntimeTotal = selectedRuntime.fired + selectedRuntime.error
    + selectedRuntime.success + selectedRuntime.failed
  const runtimeStartLabel = formatRuntimeDay(runtimeDays[runtimeRangeStart].date)
  const runtimeEndLabel = formatRuntimeDay(runtimeDays[runtimeRangeEnd].date)
  const runtimeRangeLabel = runtimeRangeStart === runtimeRangeEnd
    ? runtimeStartLabel
    : `${runtimeStartLabel} – ${runtimeEndLabel}`
  const runtimeRangeSpan = Math.max(runtimeDays.length - 1, 1)

  return (
    <main className="overview-dashboard" aria-label="本体总览">
      <div className="overview-hero-grid">
        <section className="overview-panel ontology-profile">
          <PanelTitle title="本体概况" sub="当前发布投影" />
          <div className="profile-heading">
            <div className="profile-mark"><NetworkMark /></div>
            <div>
              <h1 title={ontology.name}>{ontology.name}</h1>
              <span className="profile-domain">{ontology.domain || '未分类'}</span>
            </div>
          </div>
          <p className="profile-description">{ontology.description || '暂无本体描述。可在本体管理中补充业务范围与使用说明。'}</p>
          <dl className="profile-meta profile-meta--compact">
            <div><dt>当前发布</dt><dd>{ov.release.version}</dd></div>
            <div><dt>更新时间</dt><dd>{formatDateTime(ontology.updated_at)}</dd></div>
          </dl>
          <div className="profile-evolution">
            <VersionEvolutionCard ontologyId={ontologyId} />
          </div>
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
            <span className="kpi-label">当前版本事实流</span>
            <strong>{ov.facts.total}<small>条</small></strong>
            <p>{factParts.slice(0, 3).map(([kind, value]) => `${FACT_META[kind]?.label || kind} ${value}`).join(' · ') || '尚无事实记录'}</p>
            <em className="kpi-status is-purple"><GitBranch size={15} />追加式留痕，可回放与追溯</em>
          </button>
        </section>

        <section className="overview-panel runtime-summary">
          <PanelTitle title="近 7 日运行汇总" sub={`${runtimeRangeLabel} · ${selectedRuntimeDays.length} 日聚合`} action={<button className="overview-link-button" onClick={() => onGoGroup('governance')}>查看运行记录 <ChevronRight size={15} /></button>} />
          <div className="runtime-highlights">
            <article className="runtime-highlight runtime-highlight--sentinel">
              <span className="runtime-highlight-icon"><Activity size={17} /></span>
              <div className="runtime-highlight-total">
                <span>哨兵评估</span>
                <strong>{selectedRuntime.fired + selectedRuntime.error}<small>次</small></strong>
              </div>
              <dl>
                <div><dt><i className="tone-teal" />命中</dt><dd>{selectedRuntime.fired}</dd></div>
                <div><dt><i className="tone-coral" />错误</dt><dd>{selectedRuntime.error}</dd></div>
              </dl>
            </article>
            <article className="runtime-highlight runtime-highlight--action">
              <span className="runtime-highlight-icon"><Workflow size={17} /></span>
              <div className="runtime-highlight-total">
                <span>动作执行</span>
                <strong>{selectedRuntime.success + selectedRuntime.failed}<small>次</small></strong>
              </div>
              <dl>
                <div><dt><i className="tone-blue" />成功</dt><dd>{selectedRuntime.success}</dd></div>
                <div><dt><i className="tone-amber" />失败</dt><dd>{selectedRuntime.failed}</dd></div>
              </dl>
            </article>
          </div>

          <div className="runtime-trend">
            <div className="runtime-trend-heading">
              <span>每日运行趋势</span>
              <div className="runtime-trend-legend" aria-hidden="true">
                <span><i className="tone-teal" />哨兵</span>
                <span><i className="tone-blue" />动作</span>
                <span><i className="tone-issue" />异常</span>
              </div>
            </div>
            <div className={`runtime-daily-chart ${selectedRuntimeTotal === 0 ? 'is-empty' : ''}`} role="img" aria-label={`${runtimeRangeLabel} 每日运行趋势`}>
              <span className="runtime-gridline runtime-gridline--top" aria-hidden="true" />
              <span className="runtime-gridline runtime-gridline--mid" aria-hidden="true" />
              {runtimeDays.map((day, index) => {
                const inRange = index >= runtimeRangeStart && index <= runtimeRangeEnd
                const sentinelTotal = day.firings.fired + day.firings.error
                const actionTotal = day.actionRuns.success + day.actionRuns.failed
                return (
                  <div className={`runtime-day ${inRange ? 'is-selected' : ''}`} key={day.date}>
                    <div className="runtime-day-bars">
                      <span
                        className="runtime-day-stack runtime-day-stack--sentinel"
                        title={`${formatRuntimeDay(day.date)}：哨兵命中 ${day.firings.fired}，错误 ${day.firings.error}`}
                      >
                        <i style={{ height: `${Math.max(day.firings.fired / maxDailyRuntime * 68, day.firings.fired ? 5 : 2)}px` }} />
                        <b style={{ height: `${Math.max(day.firings.error / maxDailyRuntime * 68, day.firings.error ? 5 : 2)}px` }} />
                      </span>
                      <span
                        className="runtime-day-stack runtime-day-stack--action"
                        title={`${formatRuntimeDay(day.date)}：动作成功 ${day.actionRuns.success}，失败 ${day.actionRuns.failed}`}
                      >
                        <i style={{ height: `${Math.max(day.actionRuns.success / maxDailyRuntime * 68, day.actionRuns.success ? 5 : 2)}px` }} />
                        <b style={{ height: `${Math.max(day.actionRuns.failed / maxDailyRuntime * 68, day.actionRuns.failed ? 5 : 2)}px` }} />
                      </span>
                    </div>
                    <time dateTime={day.date}>{formatRuntimeDay(day.date)}</time>
                    <span className="runtime-day-total">{sentinelTotal + actionTotal || '—'}</span>
                  </div>
                )
              })}
              {selectedRuntimeTotal === 0 && (
                <div className="runtime-empty-note">
                  <Sparkles size={17} />
                  <span>所选时段暂无运行记录<small>运行开始后将按日呈现命中、执行与异常趋势</small></span>
                </div>
              )}
            </div>
          </div>
          <div className="runtime-range" aria-label="运行汇总时间范围">
            <div className="runtime-range-heading">
              <span>时间范围</span>
              <output aria-live="polite">{runtimeRangeLabel}</output>
            </div>
            <div
              className="runtime-range-control"
              style={{
                '--runtime-range-start': `${runtimeRangeStart / runtimeRangeSpan * 100}%`,
                '--runtime-range-end': `${runtimeRangeEnd / runtimeRangeSpan * 100}%`,
              } as React.CSSProperties}
            >
              <span className="runtime-range-track" aria-hidden="true"><i /></span>
              <input
                className="runtime-range-input"
                type="range"
                min="0"
                max={runtimeDays.length - 1}
                value={runtimeRangeStart}
                aria-label="运行汇总开始日期"
                aria-valuetext={runtimeStartLabel}
                onChange={event => {
                  const next = Math.min(Number(event.target.value), runtimeRangeEnd)
                  setRuntimeRange([next, runtimeRangeEnd])
                }}
              />
              <input
                className="runtime-range-input"
                type="range"
                min="0"
                max={runtimeDays.length - 1}
                value={runtimeRangeEnd}
                aria-label="运行汇总结束日期"
                aria-valuetext={runtimeEndLabel}
                onChange={event => {
                  const next = Math.max(Number(event.target.value), runtimeRangeStart)
                  setRuntimeRange([runtimeRangeStart, next])
                }}
              />
            </div>
            <div className="runtime-range-bounds" aria-hidden="true">
              <time dateTime={runtimeDays[0].date}>{formatRuntimeDay(runtimeDays[0].date)}</time>
              <time dateTime={runtimeDays.at(-1)?.date}>{formatRuntimeDay(runtimeDays.at(-1)?.date || '')}</time>
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
          <PanelTitle title="最近发生了什么" sub={`${ov.release.version} 事实流 · 追加不修改`} action={<button className="overview-link-button" onClick={() => onGoGroup('governance')}>查看全部 <ChevronRight size={15} /></button>} />
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
                  <span
                    className="fact-value"
                    title={fact.present === false ? '属性已删除' : formatValue(fact.value, 100)}
                  >
                    {fact.present === false ? '（已删除）' : formatValue(fact.value)}
                  </span>
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
