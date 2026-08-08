// 事件登记域共享的展示元件与格式化助手：列表页与详情抽屉共用，保持唯一事实源。

export const PALETTE = {
  blue: '#3B82F6', teal: '#5EEAD4', gold: '#FCD34D', orange: '#FDBA74',
  red: '#FB7185',
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ─── 级别标签 ────────────────────────────────────────────
export function SeverityBadge({ sev }: { sev: string }) {
  const map: Record<string, { bg: string; text: string; dot: string; label: string; glow: string }> = {
    critical: { bg: 'bg-red-50', text: 'text-red-600', dot: PALETTE.red, label: '严重', glow: 'rgba(251,113,133,0.4)' },
    high: { bg: 'bg-orange-50', text: 'text-orange-600', dot: PALETTE.orange, label: '高级', glow: 'rgba(253,186,116,0.4)' },
    medium: { bg: 'bg-amber-50', text: 'text-amber-600', dot: PALETTE.gold, label: '中级', glow: 'rgba(252,211,77,0.4)' },
    low: { bg: 'bg-teal-50', text: 'text-teal-600', dot: PALETTE.teal, label: '低级', glow: 'rgba(94,234,212,0.4)' },
    info: { bg: 'bg-blue-50', text: 'text-blue-600', dot: PALETTE.blue, label: '信息', glow: 'rgba(59,130,246,0.4)' },
  }
  const c = map[sev] ?? map.info
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 ${c.bg} ${c.text} text-xs font-medium whitespace-nowrap`}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c.dot, boxShadow: `0 0 4px ${c.glow}` }} />{c.label}
    </span>
  )
}
