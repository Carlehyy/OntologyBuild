import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, Clock, Database,
  History, Loader2, RefreshCw, Scale, ShieldCheck, Users,
} from 'lucide-react'

import {
  agentApi,
  type DecisionPerspective,
  type DecisionSimulationRun,
  type DecisionSimulationSummary,
} from '@/api/agent'

interface Props {
  oid: string
  releaseId: string
  conversationId: string | null
  activeRunId: string | null
  running: boolean
}

const statusMeta = {
  running: { label: '推演中', className: 'border-sky-200 bg-sky-50 text-sky-700' },
  succeeded: { label: '已完成', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  failed: { label: '失败', className: 'border-red-200 bg-red-50 text-red-700' },
} as const

const phaseLabel: Record<string, string> = {
  snapshot: '正在固化当前发布版快照',
  planning: '正在编译决策问题与候选方案',
  perspectives: '正在收集独立视角',
  synthesis: '正在汇总分歧与可执行建议',
  complete: '推演完成',
  failed: '推演中断',
}

const confidenceLabel = (value?: string) => (
  value === 'strong' ? '论证较充分' : value === 'moderate' ? '论证中等' : '证据仍偏弱'
)

const disagreementLabel = (value?: string) => (
  value === 'high' ? '分歧较高' : value === 'medium' ? '存在分歧' : '分歧较低'
)

const dateTime = (value?: string | null) => {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0 border-l border-slate-200 pl-3 first:border-l-0 first:pl-0">
      <p className="text-[10px] uppercase tracking-[0.08em] text-slate-400">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-800">{value}</p>
      {detail && <p className="mt-0.5 truncate text-[10px] text-slate-400">{detail}</p>}
    </div>
  )
}

function RunningState({ run }: { run?: DecisionSimulationSummary | DecisionSimulationRun }) {
  const diagnostics = run?.diagnostics || {}
  const phase = String(diagnostics.phase || 'snapshot')
  const completed = Number(diagnostics.perspectiveCompleted || 0)
  const total = Number(diagnostics.perspectiveTotal || 4)
  const current = String(diagnostics.perspectiveCurrent || '')
  const progress = phase === 'snapshot' ? 12
    : phase === 'planning' ? 28
      : phase === 'perspectives' ? 35 + Math.round((completed / Math.max(1, total)) * 45)
        : phase === 'synthesis' ? 90 : 100
  return (
    <div className="flex h-full items-center justify-center bg-slate-50 p-8" data-testid="decision-simulation-running">
      <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-50 text-sky-600">
            <Loader2 size={19} className="animate-spin motion-reduce:animate-none" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-slate-800">{run?.title || '决策推演正在启动'}</h4>
            <p className="mt-0.5 text-xs text-slate-500">{phaseLabel[phase] || '正在处理'}</p>
          </div>
        </div>
        <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-label="推演进度">
          <div className="h-full rounded-full bg-sky-500 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-3 flex items-center justify-between text-[11px] text-slate-400">
          <span>{current || '发布版只读快照'}</span>
          <span>{phase === 'perspectives' ? `${completed}/${total} 个视角` : `${progress}%`}</span>
        </div>
        <p className="mt-5 border-t border-slate-100 pt-4 text-[11px] leading-5 text-slate-500">
          推演运行在隔离快照上。此过程只会写入推演记录，不会修改真实对象、事实、哨兵或执行动作。
        </p>
      </div>
    </div>
  )
}

function EmptyState({ running }: { running: boolean }) {
  if (running) return <RunningState />
  return (
    <div className="flex h-full items-center justify-center bg-slate-50 p-8" data-testid="decision-simulation-empty">
      <div className="max-w-md text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl border border-slate-200 bg-white text-teal-600 shadow-sm">
          <Scale size={21} />
        </div>
        <h4 className="mt-4 text-sm font-semibold text-slate-800">从一个真实决策问题开始</h4>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          在左侧告诉助手“推演……”并说明目标、候选方案或时间范围。引擎会锁定当前发布版，收集多个视角并比较方案。
        </p>
        <div className="mt-4 rounded-lg border border-slate-200 bg-white px-4 py-3 text-left text-[11px] leading-5 text-slate-500">
          示例：推演未来一个月该采取哪种策略，并给出关键假设、风险、早期信号与停止条件。
        </div>
      </div>
    </div>
  )
}

function OptionComparison({ run }: { run: DecisionSimulationRun }) {
  const options = run.evaluation.options || []
  const objectiveLabels = new Map((run.evaluation.objectives || []).map(item => [item.id, item.label]))
  return (
    <section aria-labelledby="decision-options-title">
      <div className="mb-3 flex items-center justify-between">
        <h4 id="decision-options-title" className="flex items-center gap-2 text-xs font-semibold text-slate-800">
          <BarChart3 size={14} className="text-teal-600" />方案比较
        </h4>
        <span className="text-[10px] text-slate-400">保守分 = 加权均分 − 0.35 × 视角离散度</span>
      </div>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        {options.map((option, index) => (
          <div key={option.optionId} className={`px-4 py-3 ${index ? 'border-t border-slate-100' : ''}`}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] font-semibold ${option.rank === 1 ? 'bg-teal-50 text-teal-700' : 'bg-slate-100 text-slate-500'}`}>{option.rank}</span>
                  <span className="truncate text-xs font-medium text-slate-800">{option.label}</span>
                </div>
                <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div className={`h-full rounded-full ${option.rank === 1 ? 'bg-teal-500' : 'bg-slate-400'}`} style={{ width: `${Math.max(0, Math.min(100, option.robustScore))}%` }} />
                </div>
              </div>
              <div className="shrink-0 text-right">
                <p className="text-base font-semibold tabular-nums text-slate-800">{option.robustScore.toFixed(1)}</p>
                <p className="text-[10px] text-slate-400">保守综合分</p>
              </div>
            </div>
            <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
              <span>加权均分 {option.meanScore.toFixed(1)}</span>
              <span>分歧跨度 {option.disagreement.toFixed(1)}</span>
              {Object.entries(option.objectiveScores).map(([key, value]) => (
                <span key={key}>{objectiveLabels.get(key) || key} {value.toFixed(1)}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function PerspectiveCard({ perspective, winnerId }: { perspective: DecisionPerspective; winnerId?: string }) {
  const winner = perspective.optionAssessments.find(item => item.optionId === winnerId)
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h5 className="text-xs font-semibold text-slate-800">{perspective.name}</h5>
          <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-400">{perspective.mission}</p>
        </div>
        <span className="shrink-0 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[9px] text-slate-500">
          证据占比 {Math.round((perspective.evidenceCoverage || 0) * 100)}%
        </span>
      </div>
      <p className="mt-3 text-[11px] leading-5 text-slate-600">{perspective.stance || '该视角未提供立场摘要。'}</p>
      {winner?.rationale && (
        <div className="mt-3 border-l-2 border-teal-200 pl-2.5 text-[10px] leading-4 text-slate-500">
          对推荐方案：{winner.rationale}
        </div>
      )}
      {(perspective.challenges || []).length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {perspective.challenges.slice(0, 3).map(item => (
            <span key={item} className="rounded bg-amber-50 px-1.5 py-1 text-[9px] text-amber-700">{item}</span>
          ))}
        </div>
      )}
    </article>
  )
}

function ResultView({ run }: { run: DecisionSimulationRun }) {
  const recommendation = run.recommendation || {}
  const coverage = run.snapshot.coverage || {}
  const winnerId = recommendation.recommendedOptionId
  const scenarios = useMemo(() => {
    const seen = new Set<string>()
    return run.perspectives.flatMap(item => item.scenarioOutlooks || []).filter(item => {
      const key = `${item.name}:${item.trigger}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    }).slice(0, 6)
  }, [run.perspectives])

  if (run.status === 'running') return <RunningState run={run} />
  if (run.status === 'failed') {
    return (
      <div className="flex h-full items-center justify-center bg-slate-50 p-8">
        <div role="alert" className="max-w-lg rounded-lg border border-red-200 bg-white p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-red-700"><AlertTriangle size={16} />推演未完成</div>
          <p className="mt-2 text-xs leading-5 text-red-600/80">{run.errorMessage || '模型或数据服务返回了不可恢复的错误。'}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="scrollbar-thin h-full overflow-y-auto bg-slate-50" data-testid="decision-simulation-result">
      <div className="mx-auto max-w-5xl space-y-5 p-4 lg:p-5">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.12em] text-teal-600">
                <ShieldCheck size={13} />隔离决策快照
              </div>
              <h3 className="mt-2 text-base font-semibold leading-6 text-slate-900">{run.title}</h3>
              <p className="mt-1.5 text-xs leading-5 text-slate-500">{run.question}</p>
            </div>
            <span className={`rounded-full border px-2.5 py-1 text-[10px] font-medium ${statusMeta[run.status].className}`}>
              {statusMeta[run.status].label}
            </span>
          </div>
          <div className="mt-5 grid grid-cols-2 gap-y-4 border-t border-slate-100 pt-4 sm:grid-cols-4">
            <Metric label="数据截止" value={dateTime(run.snapshot.capturedAt)} detail="冻结后不随线上数据变化" />
            <Metric label="快照范围" value={`${coverage.instanceCount ?? 0} 个实例`} detail={`${coverage.objectTypeCount ?? 0} 类对象 · ${coverage.linkTypeCount ?? 0} 类关系`} />
            <Metric label="独立视角" value={`${run.perspectives.length} 个`} detail={disagreementLabel(run.evaluation.disagreementLevel)} />
            <Metric label="结果强度" value={confidenceLabel(recommendation.confidenceBand)} detail="不是未来概率" />
          </div>
        </section>

        <section className="rounded-xl border border-teal-200 bg-teal-50/70 p-5" aria-labelledby="decision-recommendation-title">
          <div className="flex items-start gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-teal-700 shadow-sm"><Scale size={16} /></div>
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-teal-700">当前建议</p>
              <h4 id="decision-recommendation-title" className="mt-1 text-base font-semibold text-slate-900">{recommendation.recommendedOption || '待人工复核'}</h4>
              <p className="mt-2 text-xs leading-5 text-slate-600">{recommendation.summary}</p>
              {(recommendation.rationale || []).length > 0 && (
                <ul className="mt-3 space-y-1.5">
                  {recommendation.rationale!.map(item => <li key={item} className="flex gap-2 text-[11px] leading-5 text-slate-600"><CheckCircle2 size={12} className="mt-1 shrink-0 text-teal-600" />{item}</li>)}
                </ul>
              )}
            </div>
          </div>
        </section>

        <OptionComparison run={run} />

        <section aria-labelledby="decision-perspectives-title">
          <div className="mb-3 flex items-center justify-between">
            <h4 id="decision-perspectives-title" className="flex items-center gap-2 text-xs font-semibold text-slate-800"><Users size={14} className="text-teal-600" />多视角审议</h4>
            <span className="text-[10px] text-slate-400">角色意见相互独立保存，不按票数计算概率</span>
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {run.perspectives.map(item => <PerspectiveCard key={item.id} perspective={item} winnerId={winnerId} />)}
          </div>
        </section>

        {scenarios.length > 0 && (
          <section aria-labelledby="decision-scenarios-title">
            <h4 id="decision-scenarios-title" className="mb-3 flex items-center gap-2 text-xs font-semibold text-slate-800"><Activity size={14} className="text-teal-600" />可能情景与早期信号</h4>
            <div className="grid gap-3 lg:grid-cols-3">
              {scenarios.map((scenario, index) => (
                <article key={`${scenario.name}-${index}`} className="rounded-lg border border-slate-200 bg-white p-3.5">
                  <div className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-sky-500" /><h5 className="text-xs font-medium text-slate-800">{scenario.name}</h5></div>
                  <p className="mt-2 text-[10px] leading-4 text-slate-500">触发：{scenario.trigger || '尚未定义'}</p>
                  {(scenario.earlySignals || []).slice(0, 3).map(signal => <p key={signal} className="mt-1.5 border-l border-sky-200 pl-2 text-[10px] leading-4 text-slate-500">{signal}</p>)}
                </article>
              ))}
            </div>
          </section>
        )}

        <div className="grid gap-3 lg:grid-cols-2">
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-800"><CheckCircle2 size={14} className="text-emerald-600" />无悔行动</h4>
            <ul className="mt-3 space-y-2">
              {(recommendation.noRegretActions || []).map(item => <li key={item} className="text-[11px] leading-5 text-slate-600">{item}</li>)}
              {!recommendation.noRegretActions?.length && <li className="text-[11px] text-slate-400">暂无</li>}
            </ul>
          </section>
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-800"><AlertTriangle size={14} className="text-amber-600" />停止条件</h4>
            <ul className="mt-3 space-y-2">
              {(recommendation.stopConditions || []).map(item => <li key={item} className="text-[11px] leading-5 text-slate-600">{item}</li>)}
              {!recommendation.stopConditions?.length && <li className="text-[11px] text-slate-400">暂无</li>}
            </ul>
          </section>
        </div>

        <footer className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-[10px] leading-5 text-slate-500">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="flex items-center gap-1.5"><Database size={11} />发布版 {run.ontologyReleaseId || '未绑定'}</span>
            <span className="flex items-center gap-1.5"><Clock size={11} />{dateTime(run.completedAt)}</span>
            <span>校验值 {(run.snapshot.checksum || '').slice(0, 12) || '—'}</span>
            <span>模型 {run.modelName || '—'}</span>
          </div>
          <p className="mt-1.5 text-slate-400">{recommendation.disclaimer || '结果用于辅助决策，不代表自动执行指令。'}</p>
        </footer>
      </div>
    </div>
  )
}

export default function DecisionSimulationView({ oid, releaseId, conversationId, activeRunId, running }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(activeRunId)
  const {
    data: runs = [], isLoading: listLoading, refetch: refetchRuns,
  } = useQuery<DecisionSimulationSummary[]>({
    queryKey: ['decision-simulations', oid, releaseId, conversationId],
    queryFn: () => agentApi.decisionSimulations(oid, {
      releaseId, conversationId: conversationId || undefined, limit: 30,
    }),
    enabled: !!oid && !!releaseId,
    refetchInterval: running ? 1500 : false,
  })

  useEffect(() => {
    if (activeRunId) {
      setSelectedId(activeRunId)
      void refetchRuns()
    }
  }, [activeRunId, refetchRuns])

  useEffect(() => {
    if (!selectedId && runs[0]?.id) setSelectedId(runs[0].id)
  }, [runs, selectedId])

  const effectiveId = selectedId || activeRunId || runs[0]?.id || null
  const { data: run, isLoading: runLoading, refetch: refetchRun } = useQuery<DecisionSimulationRun>({
    queryKey: ['decision-simulation', oid, effectiveId],
    queryFn: () => agentApi.decisionSimulation(oid, effectiveId!),
    enabled: !!oid && !!effectiveId,
    refetchInterval: query => query.state.data?.status === 'running' || running ? 1500 : false,
  })

  if (!oid) return <EmptyState running={false} />

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50">
      <div className="flex h-10 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3">
        <div className="flex min-w-0 items-center gap-2 text-[10px] text-slate-500">
          <History size={12} />
          <select
            aria-label="选择决策推演记录"
            value={effectiveId || ''}
            onChange={event => setSelectedId(event.target.value || null)}
            disabled={!runs.length}
            className="h-7 max-w-[300px] cursor-pointer rounded border border-slate-200 bg-slate-50 px-2 text-[10px] text-slate-600 outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {!runs.length && <option value="">当前会话暂无推演</option>}
            {runs.map(item => <option key={item.id} value={item.id}>{item.title} · {dateTime(item.startedAt)}</option>)}
          </select>
        </div>
        <button
          type="button"
          onClick={() => { void refetchRuns(); if (effectiveId) void refetchRun() }}
          aria-label="刷新决策推演"
          className="flex h-7 w-7 items-center justify-center rounded text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
        >
          <RefreshCw size={12} />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        {(listLoading || runLoading) && effectiveId ? (
          <div className="flex h-full items-center justify-center gap-2 text-xs text-slate-500"><Loader2 size={14} className="animate-spin motion-reduce:animate-none" />正在读取推演记录…</div>
        ) : run ? <ResultView run={run} /> : runs[0]?.status === 'running' ? <RunningState run={runs[0]} /> : <EmptyState running={running} />}
      </div>
    </div>
  )
}
