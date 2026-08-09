/* 治理 Hero:进页 3 秒回答“有没有待办、哨兵是否在线、自动化是否健康”。
   ⓪ KPI 总览条(整卡可点,平滑滚动到区块);
   ⓪ 近 7 日执行心电图(命中/成功/失败迷你柱图);
   ⓪ 2.5D 等距流程图:数据→实例→哨兵→动作→事实,真实计数驱动。 */
import {
  Database, Boxes, ShieldAlert, Rocket, ScrollText,
  HandMetal, Loader2,
} from 'lucide-react'
import type { GovernanceKpis } from '../tabs/governanceFormat'
import type { DailySparkDatum } from './storyModel'

export interface HeroFlowCounts {
  datasetsBound: number
  instances: number
  sentinelsOnline: number
  sentinelsTotal: number
  pendingCount: number
  autoRuns: number
  factsTotal: number
}

function KpiCard({ icon: Icon, iconCls, label, value, detail, onClick }: {
  icon: any; iconCls: string; label: string; value: string; detail: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-3 rounded-xl border bg-white px-4 py-3 text-left transition hover:border-gray-300 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
    >
      <Icon size={16} className={`${iconCls} shrink-0`} />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-xl font-semibold tabular-nums text-gray-800 whitespace-nowrap">{value}</span>
          <span className="text-xs text-gray-400 whitespace-nowrap">{label}</span>
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-gray-400" title={detail}>{detail}</span>
      </span>
    </button>
  )
}

function DailySpark({ data }: { data: DailySparkDatum[] }) {
  const max = Math.max(1, ...data.map(day => Math.max(day.fired + day.firedError, day.runSuccess + day.runFailed)))
  const totalEvents = data.reduce((sum, day) => sum + day.fired + day.firedError + day.runSuccess + day.runFailed, 0)
  return (
    <div data-testid="governance-daily-spark" className="flex h-full flex-col">
      <div className="flex items-center justify-between text-[11px] text-gray-400">
        <span>近 7 日执行心电图</span>
        <span className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-rose-400" />哨兵命中</span>
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />执行成功</span>
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-red-400" />失败</span>
        </span>
      </div>
      {totalEvents === 0 ? (
        <div className="flex h-24 items-center justify-center text-[11px] text-gray-300">
          近 7 日暂无哨兵命中或动作执行记录
        </div>
      ) : (
        <div className="mt-2 flex h-24 items-end gap-1.5">
          {data.map(day => (
            <div
              key={day.date}
              className="flex flex-1 items-end justify-center gap-0.5"
              title={`${day.date} · 命中 ${day.fired}(异常 ${day.firedError}) · 成功 ${day.runSuccess} · 失败 ${day.runFailed}`}
            >
              <span
                className="gov-bar w-1.5 rounded-sm bg-rose-300"
                style={{ height: day.fired ? `${Math.max(8, (day.fired / max) * 100)}%` : 0 }}
              />
              <span
                className="gov-bar w-1.5 rounded-sm bg-emerald-400"
                style={{ height: day.runSuccess ? `${Math.max(8, (day.runSuccess / max) * 100)}%` : 0 }}
              />
              <span
                className="gov-bar w-1.5 rounded-sm bg-red-400"
                style={{ height: day.runFailed ? `${Math.max(8, (day.runFailed / max) * 100)}%` : 0 }}
              />
            </div>
          ))}
        </div>
      )}
      <div className="mt-1 flex justify-between text-[10px] text-gray-300">
        <span>{data[0]?.date?.slice(5)}</span>
        <span>{data[data.length - 1]?.date?.slice(5)}</span>
      </div>
    </div>
  )
}

interface IsoNode {
  key: string
  icon: any
  label: string
  value: string
  tone: string
  x: number
  y: number
  z: number
  onClick?: () => void
}

function IsoFlow({ counts, onNavigate }: {
  counts: HeroFlowCounts
  onNavigate: (section: 'pending' | 'autonomy' | 'sentinels' | 'facts') => void
}) {
  const nodes: IsoNode[] = [
    { key: 'data', icon: Database, label: '数据资产', value: `${counts.datasetsBound} 已绑定`, tone: 'text-sky-600 border-sky-200 bg-sky-50/90', x: 2, y: 46, z: 0 },
    { key: 'instances', icon: Boxes, label: '实例', value: `${counts.instances} 个`, tone: 'text-blue-600 border-blue-200 bg-blue-50/90', x: 23, y: 34, z: 10 },
    { key: 'sentinels', icon: ShieldAlert, label: '哨兵', value: `${counts.sentinelsOnline}/${counts.sentinelsTotal} 在线`, tone: 'text-rose-600 border-rose-200 bg-rose-50/90', x: 44, y: 22, z: 20, onClick: () => onNavigate('sentinels') },
    { key: 'actions', icon: Rocket, label: '动作', value: `${counts.pendingCount} 待裁决 · ${counts.autoRuns} 自动`, tone: 'text-amber-600 border-amber-200 bg-amber-50/90', x: 65, y: 10, z: 30, onClick: () => onNavigate('autonomy') },
    { key: 'facts', icon: ScrollText, label: '事实', value: `${counts.factsTotal} 条留痕`, tone: 'text-indigo-600 border-indigo-200 bg-indigo-50/90', x: 83, y: 0, z: 40, onClick: () => onNavigate('facts') },
  ]
  const center = (node: IsoNode) => ({ x: node.x + 8, y: node.y + 20 })
  return (
    <div data-testid="governance-iso-flow" className="gov-iso-stage relative h-52 overflow-hidden rounded-xl border border-slate-200 bg-gradient-to-br from-slate-50 via-white to-teal-50/40">
      <div
        className="gov-iso-plane absolute inset-x-8 top-6 bottom-[-30%]"
        style={{
          backgroundImage:
            'linear-gradient(to right, rgba(100,116,139,.12) 1px, transparent 1px), linear-gradient(to bottom, rgba(100,116,139,.12) 1px, transparent 1px)',
          backgroundSize: '26px 26px',
        }}
      >
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {nodes.slice(0, -1).map((node, index) => {
            const from = center(node)
            const to = center(nodes[index + 1])
            return (
              <line
                key={node.key}
                x1={from.x} y1={from.y} x2={to.x} y2={to.y}
                className="gov-iso-edge"
                stroke="rgba(13,148,136,.55)"
                strokeWidth="0.7"
              />
            )
          })}
        </svg>
        {nodes.map((node, index) => (
          <div
            key={node.key}
            className="gov-iso-node absolute"
            style={{ left: `${node.x}%`, top: `${node.y}%`, transform: `translateZ(${node.z}px)` }}
          >
            <div className="gov-iso-float" data-order={index + 1}>
              <button
                type="button"
                onClick={node.onClick}
                disabled={!node.onClick}
                title={node.onClick ? `查看${node.label}详情` : node.label}
                className={`flex w-24 flex-col items-center gap-0.5 rounded-xl border px-2 py-2 shadow-[0_10px_24px_rgba(15,23,42,0.12)] backdrop-blur transition-shadow enabled:hover:shadow-[0_16px_32px_rgba(15,23,42,0.18)] disabled:cursor-default ${node.tone}`}
                style={{ transform: 'rotateZ(32deg) rotateX(-55deg)', transformOrigin: 'center' }}
              >
                <node.icon size={15} />
                <span className="text-[10px] font-medium">{node.label}</span>
                <span className="text-[10px] tabular-nums opacity-80">{node.value}</span>
              </button>
            </div>
          </div>
        ))}
      </div>
      <p className="absolute left-3 top-2.5 text-[11px] font-medium text-slate-500">
        本体执行链 · 数据如何变成动作与事实
      </p>
    </div>
  )
}

export default function ExecutionHero({
  kpis,
  dailySpark,
  flowCounts,
  isRefreshing,
  onNavigate,
}: {
  kpis: GovernanceKpis
  dailySpark: DailySparkDatum[]
  flowCounts: HeroFlowCounts
  isRefreshing: boolean
  onNavigate: (section: 'pending' | 'autonomy' | 'sentinels' | 'facts') => void
}) {
  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)
  return (
    <div className="space-y-3">
      <div data-testid="governance-kpi-strip" className="gov-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard icon={HandMetal} iconCls="text-blue-500" label="待审批"
          value={String(kpis.pendingCount)}
          detail={kpis.pendingCount > 0 ? '需要人工裁决' : '全部已处理'}
          onClick={() => onNavigate('pending')} />
        <KpiCard icon={ShieldAlert} iconCls="text-rose-500" label="哨兵在线"
          value={kpis.sentinelsTotal > 0 ? `${kpis.sentinelsOnline}/${kpis.sentinelsTotal}` : '—'}
          detail={kpis.sentinelsTotal === 0
            ? '尚未配置哨兵'
            : kpis.sentinelsMuted + kpis.sentinelsDisabled > 0
              ? `影子 ${kpis.sentinelsMuted} · 停用 ${kpis.sentinelsDisabled}`
              : '全部在线'}
          onClick={() => onNavigate('sentinels')} />
        <KpiCard icon={Rocket} iconCls="text-amber-500" label="自治动作"
          value={kpis.actionsTotal > 0 ? String(kpis.actionsTotal) : '—'}
          detail={kpis.actionsTotal === 0
            ? '尚未配置动作'
            : `自动 ${kpis.levelCounts.L2} · 人审 ${kpis.levelCounts.L1} · 影子 ${kpis.levelCounts.L0}`}
          onClick={() => onNavigate('autonomy')} />
        <KpiCard icon={ScrollText} iconCls="text-indigo-500" label="决策批准率"
          value={kpis.approvalRate !== null ? pct(kpis.approvalRate) : '—'}
          detail={kpis.decisionsTotal > 0
            ? `累计 ${kpis.decisionsTotal} 次(批准 ${kpis.decisionsApproved} · 拒绝 ${kpis.decisionsRejected})`
            : '暂无人工决策'}
          onClick={() => onNavigate('autonomy')} />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1.35fr]">
        <div className="relative rounded-xl border bg-white px-4 py-3">
          {isRefreshing && (
            <span className="absolute right-3 top-3 inline-flex items-center gap-1 text-[10px] text-teal-600">
              <Loader2 size={10} className="animate-spin" /> 同步中
            </span>
          )}
          <DailySpark data={dailySpark} />
        </div>
        <IsoFlow counts={flowCounts} onNavigate={onNavigate} />
      </div>
    </div>
  )
}
