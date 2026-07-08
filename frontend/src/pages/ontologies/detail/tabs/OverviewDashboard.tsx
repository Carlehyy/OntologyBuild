import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'
import {
  Boxes, Database, Activity, ScrollText, GitBranch, HandMetal,
  AlertTriangle, Info, ChevronRight, ShieldCheck, Loader2, Sparkles,
} from 'lucide-react'

/* 本体驾驶舱：一屏回答四个问题——
   模型建得怎么样 / 数据进来了吗 / 平台在替我做什么 / 一切都可追溯吗 */

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
  health: { level: 'warn' | 'info' | 'action'; message: string; hint: string }[]
}

interface FactRow {
  id: string; subjectLabel: string; propertyName: string; value: unknown
  kind: string; source: string; recordedAt: string | null
}

const KIND_META: Record<string, { label: string; cls: string }> = {
  property: { label: '属性', cls: 'bg-blue-50 text-blue-600 border-blue-200' },
  derived: { label: '派生', cls: 'bg-purple-50 text-purple-600 border-purple-200' },
  link: { label: '链接', cls: 'bg-cyan-50 text-cyan-600 border-cyan-200' },
  object: { label: '存在', cls: 'bg-red-50 text-red-600 border-red-200' },
  decision: { label: '决策', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  absence: { label: '缺席', cls: 'bg-gray-100 text-gray-500 border-gray-300' },
}

const SOURCE_LABEL: Record<string, string> = {
  manual: '手工', pipeline: '管道灌入', collector: '采集器', import: '导入', action: '动作',
}

export default function OverviewDashboard({ ontologyId, onGoGroup }: {
  ontologyId: string
  onGoGroup: (group: string) => void
}) {
  const navigate = useNavigate()

  const { data: ov, isLoading } = useQuery<Overview>({
    queryKey: ['formal-overview', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/overview`) as any,
    refetchInterval: 30000,
  })
  const { data: facts = [] } = useQuery<FactRow[]>({
    queryKey: ['recent-facts', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/facts/recent?limit=12`) as any,
    refetchInterval: 30000,
  })

  if (isLoading || !ov) {
    return <div className="flex items-center justify-center py-16 text-gray-400">
      <Loader2 className="animate-spin mr-2" size={18} /> 加载驾驶舱…
    </div>
  }

  const fmtTime = (iso?: string | null) => iso
    ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '-'
  const fmtVal = (v: unknown) => {
    if (v === null || v === undefined) return '∅'
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
    return s.length > 24 ? s.slice(0, 24) + '…' : s
  }
  const rate = ov.runtime.decisions.recentApprovalRate

  return (
    <div className="space-y-4">
      {/* 待审批横幅：决策等着人做，永远放最上面 */}
      {ov.runtime.pendingApprovals > 0 && (
        <button
          onClick={() => onGoGroup('governance')}
          className="w-full flex items-center gap-3 px-4 py-3 rounded-xl border border-blue-300 bg-blue-50 hover:bg-blue-100 transition-colors text-left"
        >
          <HandMetal size={18} className="text-blue-500 shrink-0" />
          <span className="text-sm text-blue-800 font-medium">
            {ov.runtime.pendingApprovals} 个动作等待你的审批
          </span>
          <span className="text-xs text-blue-500">批准 / 拒绝都会记录为决策事实，可回放分析</span>
          <ChevronRight size={16} className="ml-auto text-blue-400 shrink-0" />
        </button>
      )}

      {/* 四张核心指标卡：建模 → 数据 → 运行 → 事实 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <button onClick={() => navigate(`/ontologies/${ontologyId}/graph`)}
          className="group text-left rounded-xl border bg-white p-4 hover:border-violet-300 hover:shadow-sm transition-all">
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <Boxes size={14} className="text-violet-500" /> 本体模型
            <ChevronRight size={13} className="ml-auto opacity-0 group-hover:opacity-100 text-violet-400" />
          </div>
          <p className="text-2xl font-semibold text-gray-800">{ov.model.objectTypes}
            <span className="text-xs font-normal text-gray-400 ml-1">对象实体</span></p>
          <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">
            关系 {ov.model.linkTypes} · 动作 {ov.model.actions}
            {ov.model.actionsRequiringApproval > 0 && <span className="text-amber-600">（{ov.model.actionsRequiringApproval} 需审批）</span>}
            <br />函数 {ov.model.functions} · 哨兵 {ov.model.sentinels.total}
            {ov.model.sentinels.muted > 0 && <span className="text-gray-400">（{ov.model.sentinels.muted} 影子）</span>}
          </p>
        </button>

        <button onClick={() => onGoGroup('data')}
          className="group text-left rounded-xl border bg-white p-4 hover:border-blue-300 hover:shadow-sm transition-all">
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <Database size={14} className="text-blue-500" /> 实例数据
            <ChevronRight size={13} className="ml-auto opacity-0 group-hover:opacity-100 text-blue-400" />
          </div>
          <p className="text-2xl font-semibold text-gray-800">{ov.data.instances}
            <span className="text-xs font-normal text-gray-400 ml-1">实例</span></p>
          <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">
            {Object.entries(ov.data.instancesBySource).map(([s, n]) =>
              `${SOURCE_LABEL[s] ?? s} ${n}`).join(' · ') || '暂无数据'}
            <br />链接 {ov.data.linkInstances} · 映射 {ov.data.mappings.total}
            {ov.data.mappings.bound > 0 && <span className="text-emerald-600">（{ov.data.mappings.bound} 已绑定）</span>}
          </p>
        </button>

        <button onClick={() => onGoGroup('governance')}
          className="group text-left rounded-xl border bg-white p-4 hover:border-emerald-300 hover:shadow-sm transition-all">
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <Activity size={14} className="text-emerald-500" /> 运行（近 7 天）
            <ChevronRight size={13} className="ml-auto opacity-0 group-hover:opacity-100 text-emerald-400" />
          </div>
          <p className="text-2xl font-semibold text-gray-800">{ov.runtime.firings7d.fired}
            <span className="text-xs font-normal text-gray-400 ml-1">次哨兵命中</span></p>
          <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">
            动作执行 {ov.runtime.actionRuns7d.success} 成功
            {ov.runtime.actionRuns7d.failed > 0 && <span className="text-red-500"> / {ov.runtime.actionRuns7d.failed} 失败</span>}
            {ov.runtime.firings7d.error > 0 && <><br /><span className="text-red-500">⚠ {ov.runtime.firings7d.error} 次哨兵评估出错</span></>}
            {rate !== null && <><br />人工批准率 <span className={rate >= 0.95 ? 'text-emerald-600' : 'text-amber-600'}>{Math.round(rate * 100)}%</span>（近 {Math.min(ov.runtime.decisions.total, 20)} 次）</>}
          </p>
        </button>

        <button onClick={() => onGoGroup('governance')}
          className="group text-left rounded-xl border bg-white p-4 hover:border-indigo-300 hover:shadow-sm transition-all">
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <ScrollText size={14} className="text-indigo-500" /> 事实流
            <ChevronRight size={13} className="ml-auto opacity-0 group-hover:opacity-100 text-indigo-400" />
          </div>
          <p className="text-2xl font-semibold text-gray-800">{ov.facts.total}
            <span className="text-xs font-normal text-gray-400 ml-1">条事实</span></p>
          <div className="flex flex-wrap gap-1 mt-1.5">
            {Object.entries(ov.facts.byKind).map(([k, n]) => (
              <span key={k} className={`text-[10px] px-1.5 py-0.5 rounded border ${KIND_META[k]?.cls ?? KIND_META.property.cls}`}>
                {KIND_META[k]?.label ?? k} {n}
              </span>
            ))}
            {ov.facts.total === 0 && <span className="text-xs text-gray-400">追加式全量留痕，尚无记录</span>}
          </div>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* 健康检查：告诉用户下一步该做什么 */}
        <div className="rounded-xl border bg-white p-4">
          <div className="flex items-center gap-2 mb-3">
            <ShieldCheck size={15} className="text-emerald-500" />
            <p className="text-sm font-medium text-gray-700">健康检查</p>
            <span className="text-xs text-gray-400">下一步该做什么</span>
          </div>
          {ov.health.length === 0 ? (
            <p className="text-xs text-gray-400 py-4 text-center">✓ 一切正常：模型、数据、哨兵、审批都在正轨上</p>
          ) : (
            <div className="space-y-2">
              {ov.health.map((h, i) => (
                <div key={i} className={`flex items-start gap-2.5 rounded-lg px-3 py-2 border text-xs ${
                  h.level === 'warn' ? 'bg-amber-50 border-amber-200'
                  : h.level === 'action' ? 'bg-blue-50 border-blue-200'
                  : 'bg-gray-50 border-gray-200'
                }`}>
                  {h.level === 'warn'
                    ? <AlertTriangle size={13} className="text-amber-500 shrink-0 mt-0.5" />
                    : h.level === 'action'
                      ? <HandMetal size={13} className="text-blue-500 shrink-0 mt-0.5" />
                      : <Info size={13} className="text-gray-400 shrink-0 mt-0.5" />}
                  <div>
                    <p className={`font-medium ${h.level === 'warn' ? 'text-amber-800' : h.level === 'action' ? 'text-blue-800' : 'text-gray-600'}`}>{h.message}</p>
                    <p className="text-gray-500 mt-0.5">{h.hint}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          {/* 实例分布 top 类型 */}
          {ov.data.topTypes.length > 0 && (
            <div className="mt-4 pt-3 border-t">
              <p className="text-xs text-gray-400 mb-2">实例分布（按对象实体）</p>
              <div className="space-y-1.5">
                {ov.data.topTypes.map(t => {
                  const max = Math.max(...ov.data.topTypes.map(x => x.count), 1)
                  return (
                    <div key={t.id} className="flex items-center gap-2 text-xs">
                      <span className="w-24 truncate text-gray-600" title={t.name}>{t.name}</span>
                      <div className="flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                        <div className="h-full rounded-full bg-blue-400/70" style={{ width: `${(t.count / max) * 100}%` }} />
                      </div>
                      <span className="w-10 text-right text-gray-500 font-mono">{t.count}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* 最近事实：世界刚刚发生了什么（追加式、全溯源） */}
        <div className="rounded-xl border bg-white p-4">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles size={15} className="text-indigo-500" />
            <p className="text-sm font-medium text-gray-700">最近发生了什么</p>
            <span className="text-xs text-gray-400">事实流 · 追加不修改</span>
            <button onClick={() => onGoGroup('governance')}
              className="ml-auto text-xs text-indigo-500 hover:underline">全部 →</button>
          </div>
          {facts.length === 0 ? (
            <p className="text-xs text-gray-400 py-4 text-center">
              还没有事实。保存图谱 / 灌入数据 / 执行动作后，每个变化都会在这里留痕。
            </p>
          ) : (
            <div className="space-y-1">
              {facts.map(f => (
                <div key={f.id} className="flex items-center gap-2 text-xs py-1 border-b border-gray-50 last:border-0">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] shrink-0 ${KIND_META[f.kind]?.cls ?? KIND_META.property.cls}`}>
                    {KIND_META[f.kind]?.label ?? f.kind}
                  </span>
                  <span className="text-gray-600 truncate max-w-[120px]" title={f.subjectLabel}>{f.subjectLabel}</span>
                  <span className="font-mono text-gray-400 truncate">{f.propertyName}</span>
                  <span className="text-gray-300">=</span>
                  <span className="font-mono text-gray-700 truncate flex-1">{fmtVal(f.value)}</span>
                  <span className="text-gray-400 shrink-0">{fmtTime(f.recordedAt)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 底部快捷入口 */}
      <div className="flex items-center gap-2 text-xs text-gray-400 px-1">
        <GitBranch size={13} />
        建模 / 哨兵 / 动作 / 审批的完整操作都在
        <button onClick={() => navigate(`/ontologies/${ontologyId}/graph`)}
          className="text-violet-600 hover:underline font-medium">图谱编辑器</button>
        里；这里是给"治理与推演"看的驾驶舱。
      </div>
    </div>
  )
}
