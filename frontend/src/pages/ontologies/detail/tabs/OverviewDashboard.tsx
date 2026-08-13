import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity, CheckCircle2, ChevronRight, CircleAlert, Database,
  GitBranch, Loader2, Sparkles, Workflow, Zap,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import type { OntologyDetail } from '@/types/ontology'
import RuntimeTrendChart from './RuntimeTrendChart'
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
  health?: { level: 'info' | 'warn' | 'action'; message: string; hint?: string; target?: string }[]
}

const FACT_KIND_LABEL: Record<string, string> = {
  property: '属性', derived: '派生', link: '链接',
  decision: '决策', object: '存在', absence: '缺席',
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

/* KPI 数字滚动：数据到达或变化时从旧值缓动到新值；
   prefers-reduced-motion 下直接呈现最终值。 */
function useCountUp(target: number, duration = 620) {
  const [display, setDisplay] = useState(0)
  const fromRef = useRef(0)
  useEffect(() => {
    const from = fromRef.current
    if (from === target) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      fromRef.current = target
      setDisplay(target)
      return
    }
    const started = performance.now()
    let raf = 0
    const tick = (now: number) => {
      const progress = Math.min((now - started) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplay(Math.round(from + (target - from) * eased))
      if (progress < 1) raf = requestAnimationFrame(tick)
      else fromRef.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return display
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
  const runtimeDayCount = overviewQuery.data?.runtime.daily7d?.length || 7
  useEffect(() => {
    setRuntimeRange([0, Math.max(runtimeDayCount - 1, 0)])
  }, [ontologyId, runtimeDayCount])

  const kpiObjectTypes = useCountUp(overviewQuery.data?.model.objectTypes ?? 0)
  const kpiInstances = useCountUp(overviewQuery.data?.data.instances ?? 0)
  const kpiMappingsBound = useCountUp(overviewQuery.data?.data.mappings.bound ?? 0)
  const kpiFactsTotal = useCountUp(overviewQuery.data?.facts.total ?? 0)

  if (!overviewQuery.data) {
    if (overviewQuery.isError) {
      return (
        <div className="overview-error" role="alert">
          <CircleAlert size={20} />
          <span>当前发布投影读取失败，请稍后重试。</span>
          <button
            type="button"
            onClick={() => void overviewQuery.refetch()}
            className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
          >
            重新加载
          </button>
        </div>
      )
    }
    return <div className="overview-loading"><Loader2 className="spin" size={20} /> 正在读取当前发布投影…</div>
  }

  const ov = overviewQuery.data
  // 只把"需要用户处理"的建议（warn/action）摆上总览；info 级属于常规引导，不打扰。
  const healthItems = (ov.health ?? []).filter(item => item.level !== 'info')
  const factParts = Object.entries(ov.facts.byKind)
    .filter(([, value]) => value > 0)
    .sort(([a], [b]) => (FACT_KIND_LABEL[a] ? 0 : 1) - (FACT_KIND_LABEL[b] ? 0 : 1))
  const sourceEntries = Object.entries(ov.data.instancesBySource).sort((a, b) => b[1] - a[1])
  // daily7d 只信后端按日返回的数据；没有就如实呈现空态，绝不把 7 日汇总堆到"今天"。
  const runtimeDays = ov.runtime.daily7d ?? []
  const hasRuntimeDays = runtimeDays.length > 0
  const runtimeRangeEnd = hasRuntimeDays ? Math.min(runtimeRange[1], runtimeDays.length - 1) : 0
  const runtimeRangeStart = hasRuntimeDays ? Math.min(runtimeRange[0], runtimeRangeEnd) : 0
  const selectedRuntimeDays = runtimeDays.slice(runtimeRangeStart, runtimeRangeEnd + 1)
  const selectedRuntime = selectedRuntimeDays.reduce((summary, day) => ({
    fired: summary.fired + day.firings.fired,
    error: summary.error + day.firings.error,
    success: summary.success + day.actionRuns.success,
    failed: summary.failed + day.actionRuns.failed,
  }), { fired: 0, error: 0, success: 0, failed: 0 })
  const selectedRuntimeTotal = selectedRuntime.fired + selectedRuntime.error
    + selectedRuntime.success + selectedRuntime.failed
  const runtimeStartLabel = hasRuntimeDays ? formatRuntimeDay(runtimeDays[runtimeRangeStart].date) : ''
  const runtimeEndLabel = hasRuntimeDays ? formatRuntimeDay(runtimeDays[runtimeRangeEnd].date) : ''
  const runtimeRangeLabel = !hasRuntimeDays
    ? '近 7 日'
    : runtimeRangeStart === runtimeRangeEnd
      ? runtimeStartLabel
      : `${runtimeStartLabel} – ${runtimeEndLabel}`
  const runtimeRangeSpan = Math.max(runtimeDays.length - 1, 1)

  return (
    <main className="overview-dashboard" aria-label="本体总览">
      {healthItems.length > 0 && (
        <section className="overview-health" aria-label="待处理事项">
          {healthItems.slice(0, 3).map(item => (
            <button
              key={`${item.level}-${item.message}`}
              type="button"
              className={`overview-health-item is-${item.level}`}
              title={item.hint || item.message}
              onClick={() => item.target && onGoGroup(item.target)}
            >
              {item.level === 'action' ? <Zap size={13} /> : <CircleAlert size={13} />}
              <span className="overview-health-message">{item.message}</span>
              {item.hint && <span className="overview-health-hint">{item.hint}</span>}
              {item.target && <ChevronRight size={13} className="overview-health-go" aria-hidden="true" />}
            </button>
          ))}
          {healthItems.length > 3 && (
            <span className="overview-health-more">还有 {healthItems.length - 3} 条待处理</span>
          )}
        </section>
      )}
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
          <p className="profile-description" title={ontology.description || undefined}>{ontology.description || '暂无本体描述。可在本体管理中补充业务范围与使用说明。'}</p>
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
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong>{kpiObjectTypes}<small>对象实体</small></strong>
            <p>关系 {ov.model.linkTypes} · 动作 {ov.model.actions} · 函数 {ov.model.functions} · 哨兵 {ov.model.sentinels.total}</p>
            {ov.model.actions === 0 && (
              <em className="kpi-status is-neutral"><CircleAlert size={15} />暂无动作类型</em>
            )}
          </button>
          <button type="button" className="kpi-cell" onClick={() => onGoGroup('data')}>
            <span className="kpi-label">当前实例投影</span>
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong>{kpiInstances}<small>对象实例</small></strong>
            <p>链接实例 {ov.data.linkInstances}</p>
            <em className="kpi-status is-neutral">
              {sourceEntries.map(([source, count]) => `${SOURCE_LABEL[source] || source} ${count}`).join(' · ') || '暂无实例数据'}
            </em>
          </button>
          <button type="button" className="kpi-cell" onClick={() => onGoGroup('data-mapping')}>
            <span className="kpi-label">数据映射</span>
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong>{kpiMappingsBound}<small>/ {ov.data.mappings.total} 已显式绑定</small></strong>
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
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong>{kpiFactsTotal}<small>条</small></strong>
            <p>{factParts.slice(0, 3).map(([kind, value]) => `${FACT_KIND_LABEL[kind] || kind} ${value}`).join(' · ') || '尚无事实记录'}</p>
            <em className="kpi-status is-purple"><GitBranch size={15} />追加式留痕，可回放与追溯</em>
          </button>
        </section>

        <section className="overview-panel runtime-summary">
          <PanelTitle title="近 7 日运行汇总" sub={hasRuntimeDays ? `${runtimeRangeLabel} · ${selectedRuntimeDays.length} 日聚合` : '暂无按日运行数据'} action={<button className="overview-link-button" onClick={() => onGoGroup('governance')}>查看运行记录 <ChevronRight size={15} /></button>} />
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
            </div>
            <div className={`runtime-trend-chart ${selectedRuntimeTotal === 0 ? 'is-empty' : ''}`} role="img" aria-label={`${runtimeRangeLabel} 每日运行趋势`}>
              <RuntimeTrendChart days={runtimeDays} rangeStart={runtimeRangeStart} rangeEnd={runtimeRangeEnd} />
              {selectedRuntimeTotal === 0 && (
                <div className="runtime-empty-note">
                  <Sparkles size={17} />
                  <span>所选时段暂无运行记录<small>运行开始后将按日呈现命中、执行与异常趋势</small></span>
                </div>
              )}
            </div>
          </div>
          {hasRuntimeDays && (
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
          )}
        </section>
      </div>

    </main>
  )
}
