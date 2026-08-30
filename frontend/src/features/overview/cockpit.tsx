/* ═══════════════════════════════════════════════════════════════
   本体治理中枢 · 可视化组件库
   星图 / 波形 / 径向仪表 / 信号条 / 迷你趋势 等
   ═══════════════════════════════════════════════════════════════ */
import { useEffect, useMemo, useRef, useState } from 'react'

/* ── 实时时钟 ── */
export function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const pad = (n: number) => String(n).padStart(2, '0')
  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  const ms = pad(Math.floor(now.getMilliseconds() / 10))
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  return { time, ms, date }
}

/* ── 数字滚动 ── */
export function AnimatedNumber({ value, duration = 1100, decimals = 0 }: { value: number; duration?: number; decimals?: number }) {
  const [display, setDisplay] = useState(0)
  const prev = useRef(0)
  useEffect(() => {
    const from = prev.current
    const startTime = performance.now()
    let raf = 0
    const tick = (t: number) => {
      const p = Math.min((t - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - p, 3)
      setDisplay(from + (value - from) * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
      else prev.current = value
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value, duration])
  const out = decimals > 0 ? display.toFixed(decimals) : Math.round(display).toLocaleString()
  return <span className="ck-num">{out}</span>
}

/* ── 微标签 ── */
export function SectionLabel({ children, accent }: { children: React.ReactNode; accent?: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="block w-1 h-3 rounded-full" style={{ background: accent ?? '#0891b2', boxShadow: `0 0 8px ${accent ?? '#0891b2'}` }} />
      <span className="ck-label">{children}</span>
    </div>
  )
}

/* ── 玻璃卡片 ── */
export function GlassCard({ children, className = '', pad = true, hover = false, onClick }: {
  children: React.ReactNode; className?: string; pad?: boolean; hover?: boolean; onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={`ck-panel ${pad ? 'ck-panel-pad' : ''} ${hover ? 'ck-hover' : ''} ${onClick ? 'cursor-pointer' : ''} ${className}`}
    >
      {children}
    </div>
  )
}

/* ── 状态呼吸点 ── */
export function StatusDot({ color = '#16a34a' }: { color?: string }) {
  return <span className="ck-dot ck-blink" style={{ background: color, color, boxShadow: `0 0 10px ${color}` }} />
}

/* ── 迷你趋势线 ── */
export function Sparkline({ data, color, width = 100, height = 34, fill = true }: {
  data: number[]; color: string; width?: number; height?: number; fill?: boolean
}) {
  const max = Math.max(...data), min = Math.min(...data)
  const rng = max - min || 1
  const step = width / (data.length - 1)
  const pts = data.map((d, i) => [i * step, height - 3 - ((d - min) / rng) * (height - 6)] as const)
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
  const area = `${line} L${width},${height} L0,${height} Z`
  const gid = useMemo(() => 'spk' + Math.random().toString(36).slice(2, 8), [])
  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.32" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill && <path d={area} fill={`url(#${gid})`} />}
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2.4" fill={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
    </svg>
  )
}

/* ── 信号强度条 ── */
export function SignalBars({ level, total = 5, color = '#0891b2' }: { level: number; total?: number; color?: string }) {
  return (
    <div className="flex items-end gap-[3px] h-4">
      {Array.from({ length: total }).map((_, i) => (
        <span key={i} className="w-[4px] rounded-sm transition-all"
          style={{
            height: `${28 + i * 18}%`,
            background: i < level ? color : 'rgba(100,116,139,0.18)',
            boxShadow: i < level ? `0 0 6px ${color}80` : 'none',
          }} />
      ))}
    </div>
  )
}

/* ═══ 径向仪表 ═══ */
function polar(cx: number, cy: number, r: number, deg: number) {
  const a = ((deg - 90) * Math.PI) / 180
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as const
}
function arcPath(cx: number, cy: number, r: number, start: number, end: number) {
  const [sx, sy] = polar(cx, cy, r, end)
  const [ex, ey] = polar(cx, cy, r, start)
  const large = end - start <= 180 ? 0 : 1
  return `M ${sx.toFixed(2)} ${sy.toFixed(2)} A ${r} ${r} 0 ${large} 0 ${ex.toFixed(2)} ${ey.toFixed(2)}`
}

export function RadialGauge({ value, label, unit, color = '#0891b2', sub, size = 132 }: {
  value: number; label: string; unit?: string; color?: string; sub?: string; size?: number
}) {
  const START = -130, END = 130
  const [anim, setAnim] = useState(0)
  useEffect(() => {
    const id = setTimeout(() => setAnim(value), 120)
    return () => clearTimeout(id)
  }, [value])
  const cx = size / 2, cy = size / 2, r = size * 0.394
  const track = arcPath(cx, cy, r, START, END)
  const pct = Math.max(0, Math.min(100, anim)) / 100
  const valEnd = START + (END - START) * pct
  const val = arcPath(cx, cy, r, START, valEnd)
  const [tipX, tipY] = polar(cx, cy, r, valEnd)
  const gid = useMemo(() => 'g' + Math.random().toString(36).slice(2, 7), [])
  const ticks = Array.from({ length: 9 })
  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size - 20} viewBox={`0 0 ${size} ${size - 16}`}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.5" />
            <stop offset="100%" stopColor={color} />
          </linearGradient>
        </defs>
        {/* 刻度 */}
        {ticks.map((_, i) => {
          const deg = START + ((END - START) / 8) * i
          const [x1, y1] = polar(cx, cy, r + 8, deg)
          const [x2, y2] = polar(cx, cy, r + 12, deg)
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgba(100,116,139,0.3)" strokeWidth="1.4" />
        })}
        <path d={track} fill="none" stroke="rgba(100,116,139,0.16)" strokeWidth="9" strokeLinecap="round" />
        <path d={val} fill="none" stroke={`url(#${gid})`} strokeWidth="9" strokeLinecap="round"
          style={{ transition: 'all 1s cubic-bezier(0.16,1,0.3,1)', filter: `drop-shadow(0 0 6px ${color}90)` }} />
        <circle cx={tipX} cy={tipY} r="4.5" fill="#fff" style={{ filter: `drop-shadow(0 0 6px ${color})`, transition: 'all 1s cubic-bezier(0.16,1,0.3,1)' }} />
        <text x={cx} y={cy - 2} textAnchor="middle" fontSize={size * 0.197} fontWeight="700" fill="#1a1a2e" className="ck-num">
          {Math.round(anim)}
        </text>
        {unit && <text x={cx} y={cy + size * 0.106} textAnchor="middle" fontSize={Math.max(8, size * 0.076)} fill="#8b8ba3" letterSpacing="0.1em">{unit}</text>}
      </svg>
      <div className="text-center -mt-1">
        <div className="text-[12px] text-[#475569] font-medium">{label}</div>
        {sub && <div className="text-[10.5px] text-[#94a3b8] mt-0.5">{sub}</div>}
      </div>
    </div>
  )
}

/* ═══ 规则命中趋势波形（横向流动 · 实时信号） ═══ */
export function WaveSignal({ height = 150 }: { height?: number }) {
  const W = 560, H = height
  /* 双 tile 周期波：每个 tile 内均为「整数个」正弦周期，因此平移一个 tile 宽度即可无缝循环 */
  const series = useMemo(() => {
    const perTile = 130, n = perTile * 2
    const a: number[] = [], b: number[] = []
    const TAU = Math.PI * 2
    for (let i = 0; i < n; i++) {
      const x = i / perTile // 0..2（tile 为单位）
      a.push(0.5 + 0.25 * Math.sin(TAU * 2 * x + 0.4) + 0.15 * Math.sin(TAU * 5 * x + 1.1) + 0.08 * Math.sin(TAU * 9 * x + 2.3))
      b.push(0.46 + 0.20 * Math.sin(TAU * 1 * x + 2.0) + 0.12 * Math.sin(TAU * 3 * x + 0.5) + 0.06 * Math.sin(TAU * 6 * x + 3.1))
    }
    return { a, b }
  }, [])

  const toPath = (arr: number[], amp: number, base: number) => {
    const totalW = W * 2 // 两个 tile 首尾相接
    const step = totalW / (arr.length - 1)
    const top = arr.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(H - base - v * amp).toFixed(1)}`).join(' ')
    return { line: top, area: `${top} L${totalW},${H} L0,${H} Z` }
  }
  const wa = toPath(series.a, H * 0.52, 18)
  const wb = toPath(series.b, H * 0.4, 14)

  const marks = [
    { x: 0.16, label: '本体建模' },
    { x: 0.46, label: '推理扫描' },
    { x: 0.78, label: '决策执行' },
  ]
  const peaks = [
    { x: 0.3, y: 0.34, tag: '+128 命中' },
    { x: 0.63, y: 0.28, tag: '风险聚集' },
  ]

  return (
    <div className="relative w-full" style={{ height: H }}>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="overflow-visible">
        <defs>
          <linearGradient id="wfA" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0891b2" stopOpacity="0.42" />
            <stop offset="100%" stopColor="#0891b2" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="wfB" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#d97706" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#d97706" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="wfStroke" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#059669" />
            <stop offset="55%" stopColor="#0891b2" />
            <stop offset="100%" stopColor="#6366f1" />
          </linearGradient>
          {/* 只裁剪流动的波，保证第二个 tile 在滑入前不外溢 */}
          <clipPath id="waveClip"><rect x="0" y="0" width={W} height={H} /></clipPath>
        </defs>
        {/* 基线网格（固定） */}
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2={W} y1={H * g} y2={H * g} stroke="rgba(15,23,42,0.07)" strokeWidth="1" strokeDasharray="2 6" />
        ))}
        {/* 流动波层（裁剪到可视窗口；前后两层不同速度形成视差纵深） */}
        <g clipPath="url(#waveClip)">
          {/* 后层（琥珀）· 较慢 */}
          <g className="ck-wave-drift-slow">
            <path d={wb.area} fill="url(#wfB)" />
            <path d={wb.line} fill="none" stroke="#d97706" strokeOpacity="0.5" strokeWidth="1.4" />
          </g>
          {/* 前层（青）· 较快 */}
          <g className="ck-wave-drift">
            <path d={wa.area} fill="url(#wfA)" />
            <path d={wa.line} fill="none" stroke="url(#wfStroke)" strokeWidth="2.2" strokeLinecap="round"
              style={{ filter: 'drop-shadow(0 0 6px rgba(8,145,178,0.45))' }} />
          </g>
        </g>
        {/* 右侧「实时」前沿：新数据自右侧进入、向左流入历史 */}
        <line x1={W - 1.5} x2={W - 1.5} y1="3" y2={H} stroke="rgba(8,145,178,0.4)" strokeWidth="1" strokeDasharray="2 3" />
        <circle cx={W - 1.5} cy={H * 0.3} r="3.4" fill="#1a1a2e" className="ck-blink" style={{ filter: 'drop-shadow(0 0 6px #0891b2)' }} />
      </svg>

      {/* 扫描光 */}
      <div className="absolute inset-y-0 w-14 pointer-events-none"
        style={{ background: 'linear-gradient(90deg, transparent, rgba(8,145,178,0.16), transparent)', animation: 'ckScan 6s linear infinite' }} />

      {/* 事件标注（固定；波在其下方流动，如实时监控标记） */}
      {peaks.map((p, i) => (
        <div key={i} className="absolute -translate-x-1/2 -translate-y-full ck-tip pointer-events-none"
          style={{ left: `${p.x * 100}%`, top: `${p.y * 100}%`, animationDelay: `${i * 1.2}s` }}>
          <span className="text-[9.5px] font-medium text-[#0e7490] px-1.5 py-0.5 rounded"
            style={{ background: 'rgba(255,255,255,0.92)', border: '1px solid rgba(8,145,178,0.32)', whiteSpace: 'nowrap' }}>
            {p.tag}
          </span>
        </div>
      ))}

      {/* 时间轴标注 */}
      <div className="absolute inset-x-0 bottom-0 flex justify-between px-1 text-[9.5px] text-[#a0a8b8]">
        {marks.map((m) => (
          <span key={m.label} className="ck-mono">{m.label}</span>
        ))}
      </div>
    </div>
  )
}

/* ═══ 多层流光数据流丝带（治理数据流 · 无缝横向流动） ═══ */
const RIBBON_COLORS = ['#0891b2', '#059669', '#0891b2', '#0891b2', '#d97706', '#059669', '#6366f1', '#0891b2', '#059669', '#d97706']
export function DataFlowRibbon({ height = 96 }: { height?: number }) {
  const W = 560, H = height
  const bands = useMemo(() => {
    const N = 10, STEP = 8
    return Array.from({ length: N }, (_, b) => {
      const yBase = 0.5 + 0.2 * Math.sin(b * 1.3)
      const amp = 0.1 + 0.09 * Math.abs(Math.sin(b * 0.8))
      const k1 = 1 + (b % 3), k2 = 3 + (b % 4)
      const ph1 = b * 0.9, ph2 = b * 1.7
      const bright = b % 4 === 0
      let d = ''
      for (let x = 0; x <= 2 * W; x += STEP) {
        const t = x / W
        const y = H * (yBase + amp * (Math.sin(2 * Math.PI * k1 * t + ph1) + 0.5 * Math.sin(2 * Math.PI * k2 * t + ph2)))
        d += `${x === 0 ? 'M' : 'L'}${x},${y.toFixed(1)}`
      }
      return { d, c: RIBBON_COLORS[b % RIBBON_COLORS.length], bright, w: bright ? 1.7 : 1, op: bright ? 0.62 : 0.26 }
    })
  }, [H])
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" style={{ height: H }} preserveAspectRatio="none">
      {[0.5].map((g) => (
        <line key={g} x1="0" x2={W} y1={H * g} y2={H * g} stroke="rgba(15,23,42,0.07)" strokeWidth="1" strokeDasharray="2 7" />
      ))}
      <g className="ck-ribbon-drift">
        {bands.map((bd, i) => (
          <path key={i} d={bd.d} fill="none" stroke={bd.c} strokeWidth={bd.w} strokeOpacity={bd.op} strokeLinecap="round"
            style={bd.bright ? { filter: `drop-shadow(0 0 4px ${bd.c}88)` } : undefined} />
        ))}
      </g>
    </svg>
  )
}
