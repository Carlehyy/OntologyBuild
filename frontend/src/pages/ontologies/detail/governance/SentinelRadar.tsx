/* 哨兵 →「值守雷达」:平台正在替你盯什么。
   在线哨兵带雷达脉冲;条件结构化渲染;最近触发列表展示命中与状态。 */
import type { Sentinel, SentinelFiring } from '@/api/sentinelApi'
import { firingStatusMeta, formatScanInterval } from '../tabs/governanceFormat'
import { AlertTriangle, ExternalLink } from 'lucide-react'

const fmtTime = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  : '-'

export default function SentinelRadar({
  sentinels,
  firings,
  onGoVersions,
}: {
  sentinels: Sentinel[]
  firings: SentinelFiring[]
  onGoVersions: () => void
}) {
  return (
    <>
      {sentinels.length === 0 ? (
        <div className="py-3 text-center">
          <p className="text-xs text-gray-400">还没有哨兵。哨兵 = 常驻监听条件 + 命中执行动作,是治理与推演的发动机。</p>
          <button onClick={onGoVersions}
            className="mt-2 inline-flex items-center gap-1 text-xs text-rose-500 hover:underline">
            去版本草稿创建哨兵 <ExternalLink size={11} />
          </button>
        </div>
      ) : (
        <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {sentinels.map(s => {
            const stateMeta = !s.enabled
              ? { label: '停用', dotCls: 'bg-gray-300', textCls: 'text-gray-400', title: '已停用', pulse: false }
              : s.muted
                ? { label: '影子', dotCls: 'bg-amber-400', textCls: 'text-amber-600', title: '影子(只记录不执行)', pulse: false }
                : { label: '在线', dotCls: 'bg-emerald-500 text-emerald-500', textCls: 'text-emerald-600', title: '在线值守中', pulse: true }
            const triggerBits = [
              s.onChange ? '变更触发' : '',
              s.onSchedule && formatScanInterval(s.scanIntervalSeconds)
                ? `${formatScanInterval(s.scanIntervalSeconds)}扫描` : '',
            ].filter(Boolean)
            if (s.lastScannedAt) triggerBits.push(`上次扫描 ${fmtTime(s.lastScannedAt)}`)
            return (
              <div key={s.id} className="rounded-lg border border-gray-200 px-3 py-2.5 transition hover:border-rose-200">
                <div className="flex items-center gap-2 text-xs">
                  <span
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${stateMeta.dotCls} ${stateMeta.pulse ? 'gov-pulse' : ''}`}
                    title={stateMeta.title}
                  />
                  <span className="truncate font-medium text-gray-800">{s.displayName}</span>
                  <span className={`shrink-0 text-[11px] ${stateMeta.textCls}`}>{stateMeta.label}</span>
                  {s.condition && (
                    <code className="flex-1 truncate text-right text-[11px] text-gray-400" title={s.condition}>
                      {s.condition}
                    </code>
                  )}
                </div>
                {triggerBits.length > 0 && (
                  <div className="mt-1 pl-[18px] text-[11px] text-gray-400">{triggerBits.join(' · ')}</div>
                )}
              </div>
            )
          })}
        </div>
      )}
      {firings.length > 0 && (
        <div className="space-y-1 border-t pt-2">
          <p className="mb-1.5 text-xs text-gray-400">最近触发(最多显示 8 条)</p>
          {firings.slice(0, 8).map(f => {
            const statusMeta = firingStatusMeta(f.status)
            const matchedIds = Array.from(new Set(
              (f.matches || []).flatMap(match => Object.values(match || {})),
            )).filter(Boolean)
            return (
              <div key={f.id} className="flex items-center gap-2 py-0.5 text-xs">
                {f.status === 'error'
                  ? <AlertTriangle size={12} className="shrink-0 text-red-400" />
                  : <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusMeta.dotCls}`} />}
                <span className="max-w-[140px] truncate text-gray-600">{f.sentinelName}</span>
                <span className={`rounded px-1 text-[10px] ${statusMeta.pillCls}`} title={f.status}>{statusMeta.label}</span>
                <span
                  className="text-gray-400"
                  title={matchedIds.length ? `命中实例:\n${matchedIds.join('\n')}` : '无命中实例明细'}
                >
                  命中 {f.matchCount}
                </span>
                {f.error && <span className="flex-1 truncate text-red-400" title={f.error}>{f.error}</span>}
                <span className="ml-auto shrink-0 text-gray-400">{fmtTime(f.createdAt)}</span>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
