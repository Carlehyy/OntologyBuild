import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion, useReducedMotion } from 'motion/react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import {
  Activity, CheckCircle2, ChevronRight, CircleAlert, Database,
  GitBranch, Layers, Loader2, Sparkles, Workflow, Zap,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import { AnimatedNumber } from '@/components/motion-ui/animated-number'
import { SPRING_LAYOUT } from '@/components/motion-ui/ease'
import {
  CHART_AMBER, CHART_AXIS, CHART_BLUE, CHART_SERIES_PALETTE, CHART_TEAL, CHART_VIOLET,
} from '@/lib/echartsTheme'
import type { OntologyDetail } from '@/types/ontology'
import RuntimeTrendChart from './RuntimeTrendChart'
import VersionEvolutionCard from './VersionEvolutionCard'
import {
  buildMiniBarOption, buildMiniCategoryBarOption, buildMiniDonutOption, buildMiniSegmentBarOption,
} from '../governance/charts'
import {
  describeRuntimeRange, normalizeRuntimeRange,
  resolveRuntimeRange, RUNTIME_DIMENSION_DEFAULT, RUNTIME_DIMENSION_OPTIONS,
  type RuntimeDimension, type RuntimeRange,
} from './runtimeSummaryRange'
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

/** runtime-summary 显式时间窗接口的按日桶，形状与 daily7d 一致。 */
interface RuntimeSummary {
  start: string
  end: string
  days: {
    date: string
    firings: { fired: number; error: number }
    actionRuns: { success: number; failed: number }
  }[]
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

/* KPI 数字滚动与卡片入场交给 beUI 动效体系（AnimatedNumber + SPRING_LAYOUT），
   两者的 prefers-reduced-motion 行为均由组件内部尊重。 */
function KpiCell({ index, reduce, onClick, children }: {
  index: number
  reduce: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <motion.button
      type="button"
      className="kpi-cell"
      onClick={onClick}
      initial={reduce ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...SPRING_LAYOUT, delay: Math.min(index * 0.05, 0.2) }}
    >
      {children}
    </motion.button>
  )
}

function KpiSpark({ option, label }: { option: EChartsOption; label: string }) {
  return (
    <div className="kpi-spark" role="img" aria-label={label}>
      <ReactECharts option={option} style={{ width: '100%', height: '100%' }} opts={{ renderer: 'canvas' }} notMerge />
    </div>
  )
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

function PanelTitle({ title, sub, action }: { title: string; sub?: React.ReactNode; action?: React.ReactNode }) {
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
  // 运行汇总时间维度：默认近7天走 overview 自带的 daily7d 桶（不额外发请求）；
  // 其余维度走 runtime-summary 显式时间窗接口。
  const [runtimeDimension, setRuntimeDimension] = useState<RuntimeDimension>(RUNTIME_DIMENSION_DEFAULT)
  const [customRange, setCustomRange] = useState<RuntimeRange>(() => (
    resolveRuntimeRange(RUNTIME_DIMENSION_DEFAULT, new Date(), { start: '', end: '' })
  ))
  const runtimeRange = useMemo(
    () => resolveRuntimeRange(runtimeDimension, new Date(), customRange),
    [runtimeDimension, customRange],
  )

  const overviewQuery = useQuery<Overview>({
    queryKey: ['formal-overview', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/overview`) as Promise<Overview>,
    refetchInterval: 30000,
  })
  const runtimeSummaryQuery = useQuery<RuntimeSummary>({
    queryKey: ['formal-runtime-summary', ontologyId, runtimeRange.start, runtimeRange.end],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/runtime-summary?start=${runtimeRange.start}&end=${runtimeRange.end}`,
    ) as Promise<RuntimeSummary>,
    enabled: runtimeDimension !== 'last7',
    refetchInterval: 30000,
  })

  const reduce = useReducedMotion() ?? false

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
  // KPI 迷你图：数据全部来自 overview 现有只读字段；空数据不渲染图表，卡片保持收敛。
  const structureSpark = buildMiniCategoryBarOption([
    { value: ov.model.objectTypes, color: CHART_TEAL },
    { value: ov.model.linkTypes, color: CHART_BLUE },
    { value: ov.model.actions, color: CHART_VIOLET },
    { value: ov.model.functions, color: CHART_AMBER },
  ])
  const sourceSpark = ov.data.instances > 0
    ? buildMiniDonutOption(sourceEntries.map(([, count], index) => ({
      value: count, color: CHART_SERIES_PALETTE[index % CHART_SERIES_PALETTE.length],
    })))
    : null
  const mappingRest = Math.max(
    0, ov.data.mappings.total - ov.data.mappings.bound - ov.data.mappings.nameMatch - ov.data.mappings.autoCreate,
  )
  const mappingSpark = ov.data.mappings.total > 0
    ? buildMiniSegmentBarOption([
      { value: ov.data.mappings.bound, color: CHART_TEAL },
      { value: ov.data.mappings.nameMatch, color: CHART_BLUE },
      { value: ov.data.mappings.autoCreate, color: CHART_VIOLET },
      { value: mappingRest, color: CHART_AXIS },
    ])
    : null
  const factTopEntries = [...factParts].sort(([, a], [, b]) => b - a).slice(0, 4)
  const factsSpark = ov.facts.total > 0
    ? buildMiniBarOption(factTopEntries.map(([, value]) => value), CHART_VIOLET)
    : null
  // 近7天用 overview 自带的 daily7d 桶；其余维度信 runtime-summary 的显式时间窗，
  // 两者都只呈现后端按日返回的数据，没有就如实呈现空态，绝不把汇总堆到某一天。
  const runtimeDays = runtimeDimension === 'last7'
    ? ov.runtime.daily7d ?? []
    : runtimeSummaryQuery.data?.days ?? []
  const runtimePending = runtimeDimension !== 'last7' && runtimeSummaryQuery.isPending
  const selectedRuntime = runtimeDays.reduce((summary, day) => ({
    fired: summary.fired + day.firings.fired,
    error: summary.error + day.firings.error,
    success: summary.success + day.actionRuns.success,
    failed: summary.failed + day.actionRuns.failed,
  }), { fired: 0, error: 0, success: 0, failed: 0 })
  const selectedRuntimeTotal = selectedRuntime.fired + selectedRuntime.error
    + selectedRuntime.success + selectedRuntime.failed
  const runtimeRangeLabelText = describeRuntimeRange(runtimeDimension, runtimeRange)

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
          <KpiCell index={0} reduce={reduce} onClick={() => onGoGroup('design')}>
            <span className="kpi-label">当前发布结构</span>
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong><AnimatedNumber value={ov.model.objectTypes} duration={0.9} /><small>对象实体</small></strong>
            <p>关系 {ov.model.linkTypes} · 动作 {ov.model.actions} · 函数 {ov.model.functions} · 哨兵 {ov.model.sentinels.total}</p>
            <KpiSpark option={structureSpark} label="当前发布结构构成迷你柱状图：对象、关系、动作、函数数量" />
            {ov.model.actions === 0 && (
              <em className="kpi-status is-neutral"><CircleAlert size={15} />暂无动作类型</em>
            )}
            <em className="kpi-status is-blue"><Layers size={15} />结构冻结于版本，演进可对照</em>
          </KpiCell>
          <KpiCell index={1} reduce={reduce} onClick={() => onGoGroup('data')}>
            <span className="kpi-label">当前实例投影</span>
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong><AnimatedNumber value={ov.data.instances} duration={0.9} /><small>对象实例</small></strong>
            <p>链接实例 {ov.data.linkInstances}</p>
            {sourceSpark && (
              <KpiSpark option={sourceSpark} label={`实例来源占比迷你环形图：${sourceEntries.map(([source, count]) => `${SOURCE_LABEL[source] || source} ${count}`).join('、')}`} />
            )}
            <em className="kpi-status is-neutral">
              {sourceEntries.map(([source, count]) => `${SOURCE_LABEL[source] || source} ${count}`).join(' · ') || '暂无实例数据'}
            </em>
            <em className="kpi-status is-teal"><Database size={15} />实例与当前结构对账，来源可追</em>
          </KpiCell>
          <KpiCell index={2} reduce={reduce} onClick={() => onGoGroup('data-mapping')}>
            <span className="kpi-label">数据映射</span>
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong><AnimatedNumber value={ov.data.mappings.bound} duration={0.9} /><small>/ {ov.data.mappings.total} 已显式绑定</small></strong>
            <p>名称匹配 {ov.data.mappings.nameMatch} · 数据自建 {ov.data.mappings.autoCreate}</p>
            {mappingSpark && (
              <KpiSpark option={mappingSpark} label="数据映射绑定状态迷你分段条：已绑定、名称匹配、数据自建与余量" />
            )}
            <em className={`kpi-status ${ov.data.mappings.total === 0 ? 'is-neutral' : ov.data.mappings.bound === ov.data.mappings.total ? '' : 'is-warning'}`}>
              {ov.data.mappings.total === 0
                ? <><Database size={15} />暂无映射记录</>
                : ov.data.mappings.bound === ov.data.mappings.total
                ? <><CheckCircle2 size={15} />当前未发现未绑定映射</>
                : <><CircleAlert size={15} />{ov.data.mappings.total - ov.data.mappings.bound} 条映射待确认</>}
            </em>
          </KpiCell>
          <KpiCell index={3} reduce={reduce} onClick={() => onGoGroup('governance')}>
            <span className="kpi-label">当前版本事实流</span>
            <ChevronRight size={13} className="kpi-go" aria-hidden="true" />
            <strong><AnimatedNumber value={ov.facts.total} duration={0.9} /><small>条</small></strong>
            <p>{factParts.slice(0, 3).map(([kind, value]) => `${FACT_KIND_LABEL[kind] || kind} ${value}`).join(' · ') || '尚无事实记录'}</p>
            {factsSpark && (
              <KpiSpark option={factsSpark} label="当前版本事实流按类型迷你柱状图" />
            )}
            <em className="kpi-status is-purple"><GitBranch size={15} />追加式留痕，可回放与追溯</em>
          </KpiCell>
        </section>

        <section className="overview-panel runtime-summary">
          <PanelTitle
            title="运行汇总"
            sub={(
              <span className="runtime-dimension">
                <select
                  className="runtime-dimension-select"
                  value={runtimeDimension}
                  aria-label="运行汇总时间维度"
                  onChange={event => setRuntimeDimension(event.target.value as RuntimeDimension)}
                >
                  {RUNTIME_DIMENSION_OPTIONS.map(option => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                {runtimeDimension === 'custom' && (
                  <span className="runtime-custom-range">
                    <input
                      type="date"
                      value={customRange.start}
                      max={customRange.end}
                      aria-label="自定义开始日期"
                      onChange={event => setCustomRange(
                        range => normalizeRuntimeRange({ ...range, start: event.target.value }),
                      )}
                    />
                    <span aria-hidden="true">–</span>
                    <input
                      type="date"
                      value={customRange.end}
                      min={customRange.start}
                      aria-label="自定义结束日期"
                      onChange={event => setCustomRange(
                        range => normalizeRuntimeRange({ ...range, end: event.target.value }),
                      )}
                    />
                  </span>
                )}
              </span>
            )}
            action={<button className="overview-link-button" onClick={() => onGoGroup('governance')}>查看运行记录 <ChevronRight size={15} /></button>}
          />
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
            <div className={`runtime-trend-chart ${selectedRuntimeTotal === 0 && !runtimePending ? 'is-empty' : ''}`} role="img" aria-label={`${runtimeRangeLabelText} 每日运行趋势`}>
              <RuntimeTrendChart days={runtimeDays} rangeLabel={runtimeRangeLabelText} />
              {runtimePending && (
                <div className="runtime-empty-note">
                  <Loader2 size={17} className="spin" />
                  <span>正在读取所选时段的运行记录…</span>
                </div>
              )}
              {!runtimePending && selectedRuntimeTotal === 0 && (
                <div className="runtime-empty-note">
                  <Sparkles size={17} />
                  <span>所选时段暂无运行记录<small>运行开始后将按日呈现命中、执行与异常趋势</small></span>
                </div>
              )}
            </div>
          </div>
        </section>
      </div>

    </main>
  )
}
