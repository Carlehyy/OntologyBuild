import type { SentinelFiring } from '../../../api/sentinelApi'

const STATUS_LABELS: Record<string, string> = {
  fired: '已执行',
  pending: '待处理',
  no_change: '本轮无动作',
  no_match: '未命中',
  muted: '静默记录',
  error: '执行错误',
  skipped: '无可执行动作',
}

export const sentinelFiringStatusLabel = (status: string) =>
  STATUS_LABELS[status] || status

const keyPreview = (keys: string[]) => {
  const visible = keys.slice(0, 3).join('、')
  return keys.length > 3 ? `${visible}…（共 ${keys.length} 项）` : visible
}

export function SentinelFiringSummary({ firing }: { firing: SentinelFiring }) {
  const entered = firing.entered || []
  const left = firing.left || []
  const hasDelta = entered.length > 0 || left.length > 0
  const noChangeExplanation = firing.status === 'no_change'
    ? hasDelta
      ? '检测到边沿变化，但按当前触发模式本轮未执行动作'
      : '命中集合未变化，本轮未重复执行动作'
    : null

  return (
    <div
      className="mt-1.5 space-y-1 text-[11px]"
      data-testid={`sentinel-firing-delta-${firing.id}`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-300">
          新进入 {entered.length}
        </span>
        <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">
          已离开 {left.length}
        </span>
        {!hasDelta && (
          <span className="rounded border border-slate-600/60 bg-slate-700/40 px-1.5 py-0.5 text-slate-400">
            边沿无变化
          </span>
        )}
      </div>
      {entered.length > 0 && (
        <div className="truncate text-emerald-300/80" title={entered.join('、')}>
          进入：{keyPreview(entered)}
        </div>
      )}
      {left.length > 0 && (
        <div className="truncate text-amber-300/80" title={left.join('、')}>
          离开：{keyPreview(left)}
        </div>
      )}
      {noChangeExplanation && (
        <div className="text-slate-400">{noChangeExplanation}</div>
      )}
    </div>
  )
}
