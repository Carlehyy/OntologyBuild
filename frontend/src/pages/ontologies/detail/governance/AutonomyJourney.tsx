/* 自治等级 →「放权旅程」:
   等级路径 stepper(L0 影子 → L1 人审 → L2 自动,当前高亮呼吸),
   近期执行履历点阵时间线(绿=成功 红=失败 灰=被拒绝 琥珀=待办中),
   批准率进度与晋升/降级建议——放权是基于历史执行效果挣来的。 */
import { ArrowDownCircle, ArrowUpCircle, Bolt, Eye, HandMetal } from 'lucide-react'
import type { AutonomyLevelKey, TimelineDot } from './storyModel'
import { buildLevelSteps } from './storyModel'

export interface AutonomyStatLike {
  actionId: string
  actionName: string
  requiresApproval: boolean
  level: AutonomyLevelKey
  shadow: boolean
  sentinels: { id: string; name: string; muted: boolean; enabled: boolean }[]
  decisions: {
    approved: number
    rejected: number
    total: number
    recentCount: number
    recentApprovalRate: number | null
  }
  autoRuns: { total: number; failed: number }
  pending: number
  recommendation: 'promote' | 'demote' | 'observe' | null
  recommendationReason: string | null
  thresholds: { promoteMinDecisions: number; promoteRate: number }
}

const LEVEL_META: Record<AutonomyLevelKey, { label: string; icon: any; cls: string; desc: string }> = {
  L0: { label: 'L0 影子', icon: Eye, cls: 'bg-gray-100 text-gray-600 border-gray-300', desc: '哨兵全部静默,只观察不动手' },
  L1: { label: 'L1 人审', icon: HandMetal, cls: 'bg-blue-50 text-blue-700 border-blue-300', desc: '每次执行等人批准' },
  L2: { label: 'L2 自动', icon: Bolt, cls: 'bg-emerald-50 text-emerald-700 border-emerald-300', desc: '命中即执行' },
}

const DOT_META: Record<string, { cls: string; label: string }> = {
  success: { cls: 'bg-emerald-500', label: '执行成功' },
  failed: { cls: 'bg-red-500', label: '执行失败' },
  rejected: { cls: 'bg-slate-300', label: '被拒绝' },
  pending: { cls: 'bg-amber-300', label: '待审批' },
  executing: { cls: 'bg-sky-400', label: '执行中' },
}

const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)
const fmtTime = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  : '-'

function LevelStepper({ level }: { level: AutonomyLevelKey }) {
  const steps = buildLevelSteps(level)
  return (
    <div className="flex items-center" aria-label={`当前自治等级 ${LEVEL_META[level].label}`}>
      {steps.map((step, index) => {
        const meta = LEVEL_META[step.key]
        return (
          <div key={step.key} className="flex items-center">
            {index > 0 && (
              <span className={`h-px w-5 ${steps[index - 1].reached && step.reached ? 'bg-teal-400' : 'bg-gray-200'}`} />
            )}
            <span
              title={meta.desc}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${
                step.current ? `${meta.cls} gov-level-current` : step.reached ? 'border-teal-200 bg-teal-50/60 text-teal-700' : 'border-gray-200 bg-white text-gray-300'
              }`}
            >
              <meta.icon size={10} /> {step.key}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function TimelineDots({ timeline }: { timeline: TimelineDot[] }) {
  if (!timeline.length) {
    return <span className="text-[11px] text-gray-400">还没有执行履历,第一次执行后会在这里留下轨迹。</span>
  }
  return (
    <div className="flex items-center gap-1.5" data-testid="autonomy-timeline">
      {timeline.map((dot, index) => {
        const meta = DOT_META[dot.status] || DOT_META.pending
        const detail = [
          `${meta.label} · ${fmtTime(dot.at)}`,
          dot.durationMs != null ? `耗时 ${dot.durationMs}ms` : '',
          dot.reason ? `原因:${dot.reason}` : '',
          dot.error ? `错误:${dot.error}` : '',
        ].filter(Boolean).join('\n')
        return (
          <span
            key={dot.id}
            className={`gov-dot-pop h-2.5 w-2.5 rounded-full ${meta.cls} ${dot.status === 'pending' || dot.status === 'executing' ? 'ring-2 ring-amber-100' : ''}`}
            style={{ animationDelay: `${index * 45}ms` }}
            title={detail}
            aria-label={detail}
          />
        )
      })}
      <span className="ml-1 text-[10px] text-gray-400">新 → 旧</span>
    </div>
  )
}

export default function AutonomyJourney({
  stat,
  timeline,
  onGoPending,
  onGoVersions,
}: {
  stat: AutonomyStatLike
  timeline: TimelineDot[]
  onGoPending: () => void
  onGoVersions: () => void
}) {
  const meta = LEVEL_META[stat.level]
  const rate = stat.decisions.recentApprovalRate
  return (
    <div className={`rounded-lg border px-4 py-3 ${
      stat.recommendation === 'promote' ? 'border-emerald-300 bg-emerald-50/40'
      : stat.recommendation === 'demote' ? 'border-red-300 bg-red-50/40' : 'border-gray-200'
    }`}>
      <div className="flex flex-wrap items-center gap-2">
        <LevelStepper level={stat.level} />
        <span className="text-sm font-medium text-gray-800">{stat.actionName}</span>
        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${meta.cls}`} title={meta.desc}>
          <meta.icon size={11} /> {meta.label}
        </span>
        {stat.pending > 0 && (
          <button type="button" onClick={onGoPending}
            title="定位到待审批列表"
            className="rounded bg-blue-100 px-1.5 py-0.5 text-[11px] text-blue-700 transition hover:bg-blue-200">
            {stat.pending} 待审批
          </button>
        )}
        {stat.sentinels.map(sn => (
          <span key={sn.id} className={`rounded px-1.5 text-[10px] ${sn.muted ? 'bg-gray-100 text-gray-400' : 'bg-rose-50 text-rose-500'}`}>
            {sn.name}{sn.muted ? '·影子' : ''}
          </span>
        ))}
        <div className="ml-auto flex gap-1.5">
          {stat.level === 'L1' && (
            <span
              className="inline-flex"
              title={stat.recommendation === 'promote' ? '批准率达标,请在版本草稿中变更后重新发布'
                : `晋升条件:近 ${stat.thresholds.promoteMinDecisions} 次批准率 ≥ ${Math.round(stat.thresholds.promoteRate * 100)}%(当前 ${stat.decisions.recentCount} 次 / ${pct(rate)})`}
            >
              <button onClick={onGoVersions}
                disabled={stat.recommendation !== 'promote'}
                className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] disabled:pointer-events-none ${
                  stat.recommendation === 'promote'
                    ? 'border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700'
                    : 'cursor-not-allowed border-gray-200 text-gray-300'
                }`}>
                <ArrowUpCircle size={12} /> 去草稿晋升
              </button>
            </span>
          )}
          {stat.level === 'L2' && (
            <button onClick={onGoVersions}
              className="inline-flex items-center gap-1 rounded-lg border border-blue-300 px-2 py-1 text-[11px] text-blue-600 hover:bg-blue-50">
              <ArrowDownCircle size={12} /> 去草稿调整
            </button>
          )}
          {stat.level === 'L0' && (
            <span className="text-[11px] text-gray-400">影子观察中</span>
          )}
        </div>
      </div>

      <div className="mt-2.5 flex items-center gap-2">
        <span className="w-20 shrink-0 text-[11px] text-gray-500">近期执行履历</span>
        <TimelineDots timeline={timeline} />
      </div>

      <div className="mt-2 flex items-center gap-2 text-[11px] text-gray-500">
        <span className="w-20 shrink-0">近期批准率</span>
        <div className="relative h-1.5 flex-1 rounded-full bg-gray-100">
          <div className={`h-full rounded-full transition-all duration-700 ${
            rate !== null && rate >= stat.thresholds.promoteRate ? 'bg-emerald-400'
            : rate !== null && rate >= 0.5 ? 'bg-amber-400' : 'bg-red-300'
          }`} style={{ width: `${Math.round((rate ?? 0) * 100)}%` }} />
          <span
            className="absolute inset-y-0 w-px bg-gray-400/70"
            style={{ left: `${stat.thresholds.promoteRate * 100}%` }}
            title={`晋升线 ${Math.round(stat.thresholds.promoteRate * 100)}%`}
          />
        </div>
        <span className="font-mono">{pct(rate)}</span>
        <span className="text-gray-400">({stat.decisions.recentCount}/{stat.thresholds.promoteMinDecisions} 次)</span>
      </div>

      <div className="mt-1.5 text-[11px] text-gray-500">
        累计 批准 {stat.decisions.approved} · 拒绝 {stat.decisions.rejected} · 自动执行 {stat.autoRuns.total}
        {stat.autoRuns.failed > 0 && <span className="text-red-500">(失败 {stat.autoRuns.failed})</span>}
      </div>
      {stat.recommendationReason ? (
        <p className={`mt-1.5 text-[11px] ${
          stat.recommendation === 'promote' ? 'text-emerald-600'
          : stat.recommendation === 'demote' ? 'text-red-600' : 'text-gray-500'
        }`}>{stat.recommendationReason}</p>
      ) : stat.level === 'L1' ? (
        <p className="mt-1.5 text-[11px] text-gray-400">
          晋升条件:近 {stat.thresholds.promoteMinDecisions} 次批准率 ≥ {Math.round(stat.thresholds.promoteRate * 100)}%
          (当前 {stat.decisions.recentCount} 次 · {pct(rate)})
        </p>
      ) : null}
    </div>
  )
}
