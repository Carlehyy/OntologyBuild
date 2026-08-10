/* 治理 Hero(参考数据任务池的简洁排版):
   ⓪ KPI 极简统计条(小 label + 大数字 + 副标题,整格可点平滑滚动);
   ⓪ 本体执行链 —— 手绘 SVG 等距图:数据资产→实例→哨兵→动作→事实,
   真实计数驱动,坐标完全可控,无 CSS 3D 裁剪问题;
   ⓪ 近 7 日执行心电图(命中/成功/失败迷你柱图,含零数据空态)。 */
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

function StatCell({ icon: Icon, iconCls, label, value, detail, onClick }: {
  icon: any; iconCls: string; label: string; value: string; detail: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative px-4 py-1.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 rounded-lg"
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

/* ═══ SVG 等距流程图 ═══ */

interface IsoTone {
  top: string
  left: string
  right: string
  text: string
}

interface IsoNodeDef {
  key: string
  icon: any
  label: string
  value: string
  tone: IsoTone
  onClick?: () => void
}

const SLAB_HALF = 42
const SLAB_RISE = 13
const SLAB_DEPTH = 9

/** 单个等距节点:顶面平行四边形 + 左右两个侧面,图标嵌于顶面,文字在下方。 */
function IsoSlab({ cx, cy, node, order }: {
  cx: number
  cy: number
  node: IsoNodeDef
  order: number
}) {
  const { tone } = node
  const topFace = `${cx - SLAB_HALF},${cy} ${cx},${cy - SLAB_RISE} ${cx + SLAB_HALF},${cy} ${cx},${cy + SLAB_RISE}`
  const leftFace = `${cx - SLAB_HALF},${cy} ${cx},${cy + SLAB_RISE} ${cx},${cy + SLAB_RISE + SLAB_DEPTH} ${cx - SLAB_HALF},${cy + SLAB_DEPTH}`
  const rightFace = `${cx},${cy + SLAB_RISE} ${cx + SLAB_HALF},${cy} ${cx + SLAB_HALF},${cy + SLAB_DEPTH} ${cx},${cy + SLAB_RISE + SLAB_DEPTH}`
  const labelY = cy + SLAB_RISE + SLAB_DEPTH + 16
  const Tag = node.onClick ? 'g' : 'g'
  return (
    <g
      className="gov-iso-float"
      data-order={order}
      role={node.onClick ? 'button' : undefined}
      tabIndex={node.onClick ? 0 : undefined}
      aria-label={node.onClick ? `查看${node.label}详情` : node.label}
      onClick={node.onClick}
      onKeyDown={node.onClick
        ? event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              node.onClick!()
            }
          }
        : undefined}
      style={node.onClick ? { cursor: 'pointer', outline: 'none' } : undefined}
    >
      <title>{node.onClick ? `查看${node.label}详情` : node.label}</title>
      <Tag>
        <polygon points={topFace} fill={tone.top} stroke={tone.right} strokeWidth="1" />
        <polygon points={leftFace} fill={tone.left} opacity="0.85" />
        <polygon points={rightFace} fill={tone.right} opacity="0.7" />
        <g transform={`translate(${cx - 7} ${cy - 7})`} style={{ color: tone.text }}>
          <node.icon size={14} />
        </g>
        <text x={cx} y={labelY} textAnchor="middle" fontSize="11" fontWeight="600" fill={tone.text}>
          {node.label}
        </text>
        <text x={cx} y={labelY + 13} textAnchor="middle" fontSize="10" fill="#64748b">
          {node.value}
        </text>
      </Tag>
    </g>
  )
}

function IsoFlowSvg({ counts, onNavigate }: {
  counts: HeroFlowCounts
  onNavigate: (section: 'pending' | 'autonomy' | 'sentinels' | 'facts') => void
}) {
  const nodes: IsoNodeDef[] = [
    { key: 'data', icon: Database, label: '数据资产', value: `${counts.datasetsBound} 已绑定`, tone: { top: '#f0f9ff', left: '#bae6fd', right: '#7dd3fc', text: '#0369a1' } },
    { key: 'instances', icon: Boxes, label: '实例', value: `${counts.instances} 个`, tone: { top: '#eff6ff', left: '#bfdbfe', right: '#93c5fd', text: '#1d4ed8' } },
    { key: 'sentinels', icon: ShieldAlert, label: '哨兵', value: `${counts.sentinelsOnline}/${counts.sentinelsTotal} 在线`, tone: { top: '#fff1f2', left: '#fecdd3', right: '#fda4af', text: '#be123c' }, onClick: () => onNavigate('sentinels') },
    { key: 'actions', icon: Rocket, label: '动作', value: `${counts.pendingCount} 待裁决 · ${counts.autoRuns} 自动`, tone: { top: '#fffbeb', left: '#fde68a', right: '#fcd34d', text: '#b45309' }, onClick: () => onNavigate('autonomy') },
    { key: 'facts', icon: ScrollText, label: '事实', value: `${counts.factsTotal} 条留痕`, tone: { top: '#eef2ff', left: '#c7d2fe', right: '#a5b4fc', text: '#4338ca' }, onClick: () => onNavigate('facts') },
  ]
  const positions = nodes.map((_, index) => ({
    cx: 66 + index * 106,
    cy: 138 - index * 26,
  }))
  return (
    <div data-testid="governance-iso-flow" className="relative">
      <p className="text-[11px] font-medium text-slate-500">
        本体执行链 · 数据如何变成动作与事实
      </p>
      <svg
        viewBox="0 0 540 190"
        className="mt-1 h-auto w-full"
        role="img"
        aria-label="本体执行链流程图:数据资产、实例、哨兵、动作、事实"
      >
        <defs>
          <linearGradient id="gov-iso-floor" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f8fafc" />
            <stop offset="100%" stopColor="#f0fdfa" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width="540" height="190" rx="10" fill="url(#gov-iso-floor)" />
        {nodes.slice(0, -1).map((_, index) => {
          const from = positions[index]
          const to = positions[index + 1]
          return (
            <line
              key={nodes[index].key}
              x1={from.cx + SLAB_HALF - 4}
              y1={from.cy - 2}
              x2={to.cx - SLAB_HALF + 4}
              y2={to.cy + 2}
              className="gov-iso-edge"
              stroke="#0d9488"
              strokeOpacity="0.55"
              strokeWidth="1.4"
            />
          )
        })}
        {nodes.map((node, index) => (
          <IsoSlab
            key={node.key}
            cx={positions[index].cx}
            cy={positions[index].cy}
            node={node}
            order={index + 1}
          />
        ))}
      </svg>
    </div>
  )
}

export function KpiStatBar({
  kpis,
  onNavigate,
}: {
  kpis: GovernanceKpis
  onNavigate: (section: 'pending' | 'autonomy' | 'sentinels' | 'facts') => void
}) {
  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`)
  return (
    <div data-testid="governance-kpi-strip" className="rounded-xl border bg-white px-2 py-2">
      <div className="grid grid-cols-2 divide-x divide-slate-100 lg:grid-cols-4">
        <StatCell icon={HandMetal} iconCls="text-blue-500" label="待审批"
          value={String(kpis.pendingCount)}
          detail={kpis.pendingCount > 0 ? '需要人工裁决' : '全部已处理'}
          onClick={() => onNavigate('pending')} />
        <StatCell icon={ShieldAlert} iconCls="text-rose-500" label="哨兵在线"
          value={kpis.sentinelsTotal > 0 ? `${kpis.sentinelsOnline}/${kpis.sentinelsTotal}` : '—'}
          detail={kpis.sentinelsTotal === 0
            ? '尚未配置哨兵'
            : kpis.sentinelsMuted + kpis.sentinelsDisabled > 0
              ? `影子 ${kpis.sentinelsMuted} · 停用 ${kpis.sentinelsDisabled}`
              : '全部在线'}
          onClick={() => onNavigate('sentinels')} />
        <StatCell icon={Rocket} iconCls="text-amber-500" label="自治动作"
          value={kpis.actionsTotal > 0 ? String(kpis.actionsTotal) : '—'}
          detail={kpis.actionsTotal === 0
            ? '尚未配置动作'
            : `自动 ${kpis.levelCounts.L2} · 人审 ${kpis.levelCounts.L1} · 影子 ${kpis.levelCounts.L0}`}
          onClick={() => onNavigate('autonomy')} />
        <StatCell icon={ScrollText} iconCls="text-indigo-500" label="决策批准率"
          value={kpis.approvalRate !== null ? pct(kpis.approvalRate) : '—'}
          detail={kpis.decisionsTotal > 0
            ? `累计 ${kpis.decisionsTotal} 次(批准 ${kpis.decisionsApproved} · 拒绝 ${kpis.decisionsRejected})`
            : '暂无人工决策'}
          onClick={() => onNavigate('autonomy')} />
      </div>
    </div>
  )
}

/** 主舞台右侧的情境栏:本体执行链(SVG)在上、执行心电图在下,随滚动吸附。 */
export function ExecutionContextRail({
  dailySpark,
  flowCounts,
  isRefreshing,
  onNavigate,
}: {
  dailySpark: DailySparkDatum[]
  flowCounts: HeroFlowCounts
  isRefreshing: boolean
  onNavigate: (section: 'pending' | 'autonomy' | 'sentinels' | 'facts') => void
}) {
  return (
    <div className="rounded-xl border bg-white p-4">
      <IsoFlowSvg counts={flowCounts} onNavigate={onNavigate} />
      <div className="mt-3 border-t border-slate-100 pt-3">
        <DailySpark data={dailySpark} isRefreshing={isRefreshing} />
      </div>
    </div>
  )
}
