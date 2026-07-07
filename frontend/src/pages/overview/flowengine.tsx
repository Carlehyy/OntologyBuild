/* ═══════════════════════════════════════════════════════════════
   本体图谱治理闭环 · 3D 场景版
   倾斜椭圆轨道（如行星环）环绕中央本体星球顺时针流转，
   全息底盘做透视地面，节点按远近做景深缩放并被星球遮挡，
   采集 → 建模 → 图谱 → 分析 → 决策 →(反馈优化)→ 采集
   ═══════════════════════════════════════════════════════════════ */
import { useMemo } from 'react'
import { AnimatedNumber } from './cockpit'

export type FlowStage = {
  key: string
  zh: string
  en: string
  Icon: React.ElementType
  color: string
  main: React.ReactNode
  mainLabel: string
  metric: string
  to?: string
}

const VW = 680, VH = 540
const HUB = { x: 340, y: 256 }
const R = 90              // 本体星球半径
const RX = 250, RY = 118  // 倾斜闭环轨道（椭圆 → 3D 环）；RY 略大于球半径，拉开环线与球体上下间距
const START = -90         // 采集在最远端（顶部中心），顺时针流转

function ptOnOrbit(deg: number) {
  const a = (deg * Math.PI) / 180
  const s = Math.sin(a)
  return { x: HUB.x + RX * Math.cos(a), y: HUB.y + RY * s, depth: (s + 1) / 2 } // depth: 0 远(上) .. 1 近(下)
}

export function GovernanceHub({ stages, onNavigate }: { stages: FlowStage[]; onNavigate?: (to: string) => void }) {
  const hub = stages[2]

  const nodes = useMemo(() =>
    stages.map((s, i) => {
      const deg = START + i * 72
      const p = ptOnOrbit(deg)
      return { ...s, i, deg, x: p.x, y: p.y, depth: p.depth, scale: 0.64 + 0.52 * p.depth, front: p.depth >= 0.5 }
    }), [stages])

  // 星球内部实体-关系网格（保留原有精致内核）
  const innerNodes = useMemo(() => Array.from({ length: 24 }, (_, i) => {
    if (i === 0) return { x: HUB.x, y: HUB.y, c: INNER_COLORS[0], rr: 4.6 }
    const band = i % 5
    const a = (i / 24) * Math.PI * 2 + band * 0.34
    const r = 16 + (band / 4) * 54 + (i % 3) * 5
    return {
      x: HUB.x + Math.cos(a) * r,
      y: HUB.y + Math.sin(a) * r * 0.72,
      c: INNER_COLORS[i % INNER_COLORS.length],
      rr: i % 6 === 0 ? 3.3 : 2.4,
    }
  }), [])

  const meshEdges = useMemo(() => innerNodes.slice(1).flatMap((n, i) => {
    const next = innerNodes[(i * 5 + 7) % (innerNodes.length - 1) + 1]
    const hubLine = i % 4 === 0 ? [innerNodes[0], n] : null
    return hubLine ? [[n, next], hubLine] : [[n, next]]
  }), [innerNodes])

  // 椭圆轨道路径：远端上半环、近端下半环、整环（供流光运动）
  const halfArc = (sweep: number) => `M ${(HUB.x - RX).toFixed(1)},${HUB.y} A ${RX} ${RY} 0 0 ${sweep} ${(HUB.x + RX).toFixed(1)},${HUB.y}`
  const backArc = halfArc(1)
  const frontArc = halfArc(0)

  const backNodes = nodes.filter((n) => !n.front)
  const frontNodes = nodes.filter((n) => n.front)
  const feedback = ptOnOrbit(START - 36) // 决策 → 采集 的回流段中点

  // 轨道节点玻璃球（前后层共用）
  const nodeOrb = (n: typeof nodes[number]) => {
    const rr = 15 * n.scale
    return (
      <g key={`orb${n.i}`}>
        <ellipse cx={n.x} cy={n.y + rr * 0.92} rx={rr * 1.15} ry={rr * 0.42} fill={n.color} opacity={0.16 * n.depth + 0.05} filter="url(#softShadow)" />
        <circle cx={n.x} cy={n.y} r={rr} fill="none" stroke={n.color} strokeWidth="1.3">
          <animate attributeName="r" values={`${rr};${rr * 1.95}`} dur="2.8s" repeatCount="indefinite" begin={`${-n.i * 0.5}s`} />
          <animate attributeName="opacity" values="0.5;0" dur="2.8s" repeatCount="indefinite" begin={`${-n.i * 0.5}s`} />
        </circle>
        <circle cx={n.x} cy={n.y} r={rr} fill={`url(#glassNode-${n.i})`} />
        <ellipse cx={n.x - rr * 0.28} cy={n.y - rr * 0.34} rx={rr * 0.34} ry={rr * 0.2} fill="#ffffff" opacity="0.6" transform={`rotate(-38 ${n.x - rr * 0.28} ${n.y - rr * 0.34})`} />
        <circle cx={n.x} cy={n.y} r={rr} fill="none" stroke="rgba(255,255,255,0.42)" strokeWidth="0.8" />
        <text x={n.x} y={n.y - rr - 5} textAnchor="middle" fontSize={7.5 * (0.8 + 0.3 * n.depth)} fontWeight="700" fill={n.color} opacity="0.9">{`0${n.i + 1}`}</text>
      </g>
    )
  }

  // 节点 → 星球内核的能量射线
  const nodeRay = (n: typeof nodes[number]) => {
    const a = Math.atan2(HUB.y - n.y, HUB.x - n.x)
    const sx = n.x + Math.cos(a) * 15 * n.scale, sy = n.y + Math.sin(a) * 15 * n.scale
    const ex = HUB.x - Math.cos(a) * (R + 3), ey = HUB.y - Math.sin(a) * (R + 3)
    return <line key={`ray${n.i}`} x1={sx} y1={sy} x2={ex} y2={ey} stroke={n.color} strokeOpacity={0.1 + 0.16 * n.depth} strokeWidth="1" strokeDasharray="2 5" />
  }

  return (
    <div className="relative w-full h-full flex items-start justify-center overflow-hidden pt-1">
      {/* 严格锁定 viewBox 宽高比：SVG 不横向留边 → HTML 标签与 SVG 节点精确对齐。
          桌面按高度约束（父有定高），窄屏堆叠时按宽度约束（父高度 auto），见 .ck-hub-fit */}
      <div className="ck-hub-fit relative mx-auto" style={{ aspectRatio: `${VW} / ${VH}` }}>
        <svg viewBox={`0 0 ${VW} ${VH}`} className="absolute inset-0 w-full h-full">
          <defs>
            <radialGradient id="glassSphere" cx="36%" cy="26%" r="78%">
              <stop offset="0%" stopColor="#ffffff" stopOpacity="0.98" />
              <stop offset="24%" stopColor="#7fe0ee" stopOpacity="0.92" />
              <stop offset="52%" stopColor="#22b8cf" stopOpacity="0.85" />
              <stop offset="80%" stopColor="#0e7490" stopOpacity="0.92" />
              <stop offset="100%" stopColor="#155e75" stopOpacity="0.95" />
            </radialGradient>
            <radialGradient id="sphereSpec" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#ffffff" stopOpacity="0.9" /><stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="planetCore" cx="50%" cy="50%" r="54%">
              <stop offset="0%" stopColor="#0891b2" stopOpacity="0.14" />
              <stop offset="70%" stopColor="#0891b2" stopOpacity="0.02" />
              <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="floorGlow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#0891b2" stopOpacity="0.30" />
              <stop offset="55%" stopColor="#0d9488" stopOpacity="0.09" />
              <stop offset="100%" stopColor="#eef1f5" stopOpacity="0" />
            </radialGradient>
            <linearGradient id="orbitStroke" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#0d9488" /><stop offset="50%" stopColor="#0891b2" /><stop offset="100%" stopColor="#6366f1" />
            </linearGradient>
            {nodes.map((n, i) => (
              <radialGradient key={i} id={`glassNode-${i}`} cx="38%" cy="32%" r="72%">
                <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
                <stop offset="40%" stopColor={n.color} stopOpacity="0.9" />
                <stop offset="100%" stopColor={n.color} stopOpacity="0.35" />
              </radialGradient>
            ))}
            <filter id="softShadow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="6" /></filter>
            <filter id="planetGlow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="4.5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <clipPath id="ballClip"><circle cx={HUB.x} cy={HUB.y} r={R - 3} /></clipPath>
          </defs>

          {/* 背景漂浮粒子 */}
          {Array.from({ length: 9 }).map((_, i) => (
            <circle key={`bg${i}`} cx={44 + (i * 127) % (VW - 88)} cy={54 + (i * 83) % (VH - 130)} r={i % 3 === 0 ? 1.5 : 1}
              fill="#64748b" opacity={0.15} className={['ck-float-a', 'ck-float-b', 'ck-float-c'][i % 3]} />
          ))}

          {/* ── 全息底盘（透视地面）── */}
          <ellipse cx={HUB.x} cy={HUB.y + 8} rx={RX + 46} ry={(RY + 46 * RY / RX)} fill="url(#floorGlow)" />
          {[0.4, 0.62, 0.82, 1].map((k, i) => (
            <ellipse key={`ring${i}`} cx={HUB.x} cy={HUB.y} rx={RX * k} ry={RY * k} fill="none" stroke="rgba(100,116,139,0.12)" strokeWidth="1" />
          ))}
          {Array.from({ length: 12 }).map((_, i) => {
            const a = (i / 12) * Math.PI * 2
            return <line key={`spk${i}`} x1={HUB.x} y1={HUB.y} x2={HUB.x + Math.cos(a) * RX} y2={HUB.y + Math.sin(a) * RY} stroke="rgba(100,116,139,0.07)" strokeWidth="1" />
          })}
          {/* 轨道外柔光带 */}
          <ellipse cx={HUB.x} cy={HUB.y} rx={RX} ry={RY} fill="none" stroke="rgba(8,145,178,0.12)" strokeWidth="15" />

          {/* ── 远端半环（星球之后）── */}
          <path d={backArc} fill="none" stroke="rgba(100,116,139,0.14)" strokeWidth="5" strokeLinecap="round" />
          <path d={backArc} fill="none" stroke="#0891b2" strokeOpacity="0.3" strokeWidth="1.4" className="ck-flow" style={{ filter: 'drop-shadow(0 0 3px #0891b2)' }} />
          {backNodes.map(nodeRay)}
          {backNodes.map(nodeOrb)}

          {/* 星球落在底盘的接触阴影 */}
          <ellipse cx={HUB.x} cy={HUB.y + R + 2} rx={R * 0.94} ry={14} fill="#64748b" opacity="0.5" filter="url(#softShadow)" />
          {/* 枢纽脉冲环 */}
          <circle cx={HUB.x} cy={HUB.y} r={R + 8} fill="none" stroke="#0891b2" strokeWidth="1.2">
            <animate attributeName="r" values={`${R + 8};${R + 32}`} dur="3.8s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.4;0" dur="3.8s" repeatCount="indefinite" />
          </circle>

          {/* ── 3D 本体星球（实体-关系-规则内核） ── */}
          <circle cx={HUB.x} cy={HUB.y} r={R} fill="url(#glassSphere)" />
          <circle cx={HUB.x} cy={HUB.y} r={R - 7} fill="url(#planetCore)" />
          <g clipPath="url(#ballClip)">
            <g className="ck-spin-160" style={{ transformOrigin: `${HUB.x}px ${HUB.y}px` }}>
              {[-0.65, -0.32, 0, 0.32, 0.65].map((s) => (
                <ellipse key={`lat${s}`} cx={HUB.x} cy={HUB.y + s * R * 0.48} rx={R * Math.sqrt(1 - Math.abs(s) * 0.18)} ry={R * 0.18}
                  fill="none" stroke="rgba(15,23,42,0.14)" strokeWidth="0.8" />
              ))}
              {[-54, -27, 0, 27, 54].map((deg) => (
                <ellipse key={`lon${deg}`} cx={HUB.x} cy={HUB.y} rx={R * 0.3} ry={R * 0.92}
                  fill="none" stroke="rgba(15,23,42,0.12)" strokeWidth="0.8" transform={`rotate(${deg} ${HUB.x} ${HUB.y})`} />
              ))}
              {meshEdges.map((edge, i) => {
                const [a, b] = edge
                return <line key={`ie${i}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={i % 4 === 0 ? '#d97706' : '#ffffff'} strokeOpacity={i % 4 === 0 ? 0.28 : 0.2} strokeWidth={i % 4 === 0 ? 0.9 : 0.7} />
              })}
              {innerNodes.map((n, i) => (
                <circle key={`in${i}`} cx={n.x} cy={n.y} r={n.rr} fill={n.c} opacity={i === 0 ? 0.98 : 0.86}
                  filter={i === 0 || i % 6 === 0 ? 'url(#planetGlow)' : undefined} />
              ))}
            </g>
          </g>
          <path d={`M ${HUB.x - R * 0.86} ${HUB.y + R * 0.52} A ${R * 0.95} ${R * 0.42} 0 0 0 ${HUB.x + R * 0.82} ${HUB.y + R * 0.5}`}
            fill="none" stroke="rgba(255,255,255,0.7)" strokeWidth="2.2" strokeLinecap="round" style={{ filter: 'blur(0.8px)' }} />
          <path d={`M ${HUB.x - R * 0.8} ${HUB.y - R * 0.15} C ${HUB.x - R * 0.24} ${HUB.y - R * 0.38}, ${HUB.x + R * 0.18} ${HUB.y - R * 0.34}, ${HUB.x + R * 0.74} ${HUB.y - R * 0.1}`}
            fill="none" stroke="rgba(255,255,255,0.18)" strokeWidth="1.2" strokeDasharray="2 5" />
          <ellipse cx={HUB.x - 30} cy={HUB.y - 38} rx={30} ry={17} fill="url(#sphereSpec)" transform={`rotate(-38 ${HUB.x - 30} ${HUB.y - 38})`} />
          <circle cx={HUB.x - 42} cy={HUB.y - 45} r="4" fill="#ffffff" opacity="0.9" />
          <circle cx={HUB.x} cy={HUB.y} r={R} fill="none" stroke="rgba(13,148,136,0.5)" strokeWidth="1.2" />
          <circle cx={HUB.x} cy={HUB.y} r={R + 7} fill="none" stroke="rgba(8,145,178,0.24)" strokeWidth="1" filter="url(#planetGlow)" />

          {/* ── 近端半环 + 流光（星球之前）── */}
          <path d={frontArc} fill="none" stroke="rgba(100,116,139,0.2)" strokeWidth="6" strokeLinecap="round" />
          <path d={frontArc} fill="none" stroke="url(#orbitStroke)" strokeOpacity="0.85" strokeWidth="2" strokeLinecap="round" className="ck-flow"
            style={{ filter: 'drop-shadow(0 0 5px rgba(8,145,178,0.5))' }} />
          {[0, 1, 2].map((k) => (
            <circle key={`bead${k}`} r="2.6" fill="#0e7490" style={{ filter: 'drop-shadow(0 0 3px rgba(8,145,178,0.7))' }}>
              <animateMotion dur="4.6s" repeatCount="indefinite" begin={`${-k * 1.53}s`} path={frontArc} keyPoints="0;1" keyTimes="0;1" calcMode="linear" />
            </circle>
          ))}
          {frontNodes.map(nodeRay)}
          {frontNodes.map(nodeOrb)}
        </svg>

        {/* ── HTML 覆盖层 ── */}
        {/* 中央本体图谱标注（落在轨道下缘之下，避免被下半环线穿过） */}
        <div className="absolute -translate-x-1/2 text-center pointer-events-none"
          style={{ left: `${(HUB.x / VW) * 100}%`, top: `${((HUB.y + RY + 6) / VH) * 100}%` }}>
          <div className="text-[12.5px] font-bold text-[#1a1a2e] leading-none" style={{ textShadow: '0 0 12px rgba(8,145,178,0.5)' }}>本体图谱</div>
          <div className="ck-mono text-[8.5px] text-[#0891b2] mt-1 tracking-wider">{hub?.main} 实体 · {hub?.metric}</div>
        </div>

        {/* 反馈闭环标注 */}
        <div className="absolute -translate-x-1/2 -translate-y-1/2 pointer-events-none"
          style={{ left: `${(feedback.x / VW) * 100}%`, top: `${(feedback.y / VH) * 100}%` }}>
          <span className="text-[8.5px] font-medium px-1.5 py-0.5 rounded-full whitespace-nowrap"
            style={{ background: 'rgba(139,92,246,0.14)', color: '#8b5cf6', border: '1px solid rgba(139,92,246,0.4)' }}>↻ 反馈优化</span>
        </div>

        {/* 环上阶段图标 + 标签（近端向下标注、远端向上标注，均背离星球，避免遮挡） */}
        {nodes.map((n) => {
          const rr = 15 * n.scale
          return (
            <div key={`lbl${n.i}`} onClick={() => n.to && onNavigate?.(n.to)}
              className={`absolute -translate-x-1/2 -translate-y-1/2 group ${n.to ? 'cursor-pointer' : ''}`}
              style={{ left: `${(n.x / VW) * 100}%`, top: `${(n.y / VH) * 100}%`, zIndex: Math.round(n.depth * 10) }}>
              <div className="pointer-events-none flex items-center justify-center">
                <n.Icon size={Math.round(rr * 0.95)} color="#fff" strokeWidth={2.2} style={{ filter: `drop-shadow(0 0 3px ${n.color})` }} />
              </div>
              <div className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap text-center transition-transform group-hover:scale-105"
                style={n.front ? { top: rr + 6 } : { bottom: rr + 6 }}>
                <div className="text-[11px] font-semibold text-[#1a1a2e] leading-none">{n.zh}</div>
                <div className="text-[13.5px] font-bold leading-none mt-0.5" style={{ color: n.color }}>{n.main}</div>
                <div className="text-[8px] text-[#8b8ba3] mt-0.5">{n.mainLabel} · {n.metric}</div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const INNER_COLORS = ['#1a1a2e', '#0891b2', '#8b5cf6', '#16a34a', '#f59e0b', '#0891b2', '#e11d48']

export { AnimatedNumber }
