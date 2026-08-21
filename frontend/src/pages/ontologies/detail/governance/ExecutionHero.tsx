/* 治理 Hero(参考数据任务池的简洁排版):
   ⓪ KPI 总览区 —— 左侧四个小卡片(2×2),右侧近 7 日执行心电图;
   ⓪ 近 7 日执行心电图(命中/成功/失败迷你柱图,含零数据空态)。
   执行链全景为独立组件 ChainPanorama(@xyflow/react)。 */
import {
  HandMetal, Loader2, Rocket, ScrollText, ShieldAlert,
} from 'lucide-react'
import type { GovernanceKpis } from '../tabs/governanceFormat'
import type { DailySparkDatum } from './storyModel'

function StatCell({ icon: Icon, iconCls, label, value, detail, onClick }: {
  icon: any; iconCls: string; label: string; value: string; detail: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative rounded-xl border bg-white px-4 py-3 text-left transition hover:border-teal-200 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
    >
      <span className="flex items-center justify-between gap-2">
        <span className="text-xs text-gray-400">{label}</span>
        <Icon size={13} className={`${iconCls} opacity-70 transition group-hover:opacity-100`} />
      </span>
      <span className="mt-0.5 block text-xl font-semibold tabular-nums text-gray-800">{value}</span>
      <span className="mt-0.5 block truncate text-[11px] text-gray-400" title={detail}>{detail}</span>
    </button>
  )
}

function DailySpark({ data, isRefreshing }: { data: DailySparkDatum[]; isRefreshing: boolean }) {
  const max = Math.max(1, ...data.map(day => Math.max(day.fired + day.firedError, day.runSuccess + day.runFailed)))
  const totalEvents = data.reduce((sum, day) => sum + day.fired + day.firedError + day.runSuccess + day.runFailed, 0)
  return (
    <div data-testid="governance-daily-spark" className="relative flex h-full flex-col">
      {isRefreshing && (
        <span className="absolute -top-1 right-0 inline-flex items-center gap-1 text-[10px] text-teal-600">
          <Loader2 size={10} className="animate-spin" /> 同步中
        </span>
      )}
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

/** 治理顶部总览:左侧四个 KPI 小卡片(2×2:待审批·决策批准率 / 哨兵在线·自治动作),
   右侧近 7 日执行心电图(与两行卡片等高),整体位于本体执行链上方。 */
export function KpiOverviewGrid({
  kpis,
  dailySpark,
  isRefreshing,
  onNavigate,
}: {
  kpis: GovernanceKpis
  dailySpark: DailySparkDatum[]
  isRefreshing: boolean
  onNavigate: (section: 'board') => void
}) {
  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <div data-testid="governance-kpi-strip" className="grid grid-cols-2 content-start gap-4">
        <StatCell icon={HandMetal} iconCls="text-blue-500" label="待审批"
          value={String(kpis.pendingCount)}
          detail={kpis.pendingCount > 0 ? '需要人工裁决' : '全部已处理'}
          onClick={() => onNavigate('board')} />
        <StatCell icon={ScrollText} iconCls="text-indigo-500" label="决策批准率"
          value={kpis.approvalRate !== null ? pct(kpis.approvalRate) : '—'}
          detail={kpis.decisionsTotal > 0
            ? `累计 ${kpis.decisionsTotal} 次(批准 ${kpis.decisionsApproved} · 拒绝 ${kpis.decisionsRejected})`
            : '暂无人工决策'}
          onClick={() => onNavigate('board')} />
        <StatCell icon={ShieldAlert} iconCls="text-rose-500" label="哨兵在线"
          value={kpis.sentinelsTotal > 0 ? `${kpis.sentinelsOnline}/${kpis.sentinelsTotal}` : '—'}
          detail={kpis.sentinelsTotal === 0
            ? '尚未配置哨兵'
            : kpis.sentinelsMuted + kpis.sentinelsDisabled > 0
              ? `影子 ${kpis.sentinelsMuted} · 停用 ${kpis.sentinelsDisabled}`
              : '全部在线'}
          onClick={() => onNavigate('board')} />
        <StatCell icon={Rocket} iconCls="text-amber-500" label="自治动作"
          value={kpis.actionsTotal > 0 ? String(kpis.actionsTotal) : '—'}
          detail={kpis.actionsTotal === 0
            ? '尚未配置动作'
            : `自动 ${kpis.levelCounts.L2} · 人审 ${kpis.levelCounts.L1} · 影子 ${kpis.levelCounts.L0}`}
          onClick={() => onNavigate('board')} />
      </div>
      <div className="rounded-xl border bg-white p-4">
        <DailySpark data={dailySpark} isRefreshing={isRefreshing} />
      </div>
    </div>
  )
}
