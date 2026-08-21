/* 待审批 → 紧凑裁决列表。
   每行是摘要(动作、触发来源、目标、参数、时间),
   点击行打开「起因 → 判定 → 后果」详情弹窗(PendingStoryDialog);
   行尾保留快捷批准/拒绝,协议与文案不变。 */
import {
  AlertTriangle, CheckCircle2, Eye, Loader2, XCircle,
} from 'lucide-react'
import { readableTargetSummary } from '../tabs/governanceFormat'
import type {
  PendingLogLike, WorkspaceActionLike,
} from './storyModel'

export interface PendingLog extends PendingLogLike {
  objectTypeId?: string | null
  status?: string | null
}

export type WorkspaceAction = WorkspaceActionLike

const fmtTime = (iso?: string | null) => iso
  ? new Date(iso).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  : '-'

const fmtVal = (v: unknown) => {
  if (v === null || v === undefined) return '∅'
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
  return s.length > 30 ? `${s.slice(0, 30)}…` : s
}

function TriggerSourceChip({ source }: { source?: string | null }) {
  const meta = source === 'sentinel'
    ? { label: '哨兵触发', cls: 'border-rose-200 bg-rose-50 text-rose-600' }
    : source === 'manual'
      ? { label: '人工发起', cls: 'border-blue-200 bg-blue-50 text-blue-600' }
      : { label: '系统触发', cls: 'border-gray-200 bg-gray-50 text-gray-500' }
  return <span className={`rounded border px-1.5 py-0.5 text-[11px] ${meta.cls}`}>{meta.label}</span>
}

export default function PendingStoryList({
  pending,
  canDecide,
  busyId,
  onOpenDetail,
  onApprove,
  onReject,
}: {
  pending: PendingLog[]
  canDecide: boolean
  busyId: string | null
  onOpenDetail: (log: PendingLog) => void
  onApprove: (log: PendingLog) => void
  onReject: (log: PendingLog) => void
}) {
  return (
    <div className="space-y-2.5">
      {pending.map(log => {
        const paramEntries = Object.entries(log.parameters || {})
        const busy = busyId === log.id
        return (
          <div
            key={log.id}
            className="rounded-lg border border-blue-200 bg-blue-50/50 transition-colors hover:border-teal-300 hover:bg-white"
          >
            <div
              role="button"
              tabIndex={0}
              aria-label={`查看待审批详情:${log.actionName || log.actionId}`}
              onClick={() => onOpenDetail(log)}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onOpenDetail(log)
                }
              }}
              className="cursor-pointer rounded-lg px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
            >
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-gray-800">{log.actionName || log.actionId}</p>
                    <TriggerSourceChip source={log.triggerSource ?? (log.actorId ? 'manual' : 'sentinel')} />
                    {log.status === 'executing' && (
                      <span
                        className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600"
                        title="上次批准后的执行未完成(持久检查点),再次批准将幂等继续执行"
                      >
                        执行中 · 可重试批准
                      </span>
                    )}
                    <span className="inline-flex items-center gap-1 text-[11px] text-teal-600">
                      <Eye size={11} /> 前因后果
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
                    {log.objectInstanceId && (
                      <span title={`实例 ID:${log.objectInstanceId}`}>
                        目标 <span className="font-medium text-gray-700">{readableTargetSummary(log, `${log.objectInstanceId.slice(0, 10)}…`)}</span>
                      </span>
                    )}
                    {paramEntries.length === 0 ? (
                      <span className="text-gray-400">无参数</span>
                    ) : (
                      <>
                        {paramEntries.slice(0, 3).map(([k, v]) => (
                          <span key={k} className="rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[11px] text-gray-500">
                            {k}={fmtVal(v)}
                          </span>
                        ))}
                        {paramEntries.length > 3 && (
                          <span className="text-[11px] text-gray-400" title={JSON.stringify(log.parameters, null, 2)}>
                            共 {paramEntries.length} 个参数
                          </span>
                        )}
                      </>
                    )}
                    <span className="text-gray-400">{fmtTime(log.executedAt)}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
                  <button
                    onClick={event => { event.stopPropagation(); onApprove(log) }}
                    disabled={busy || !canDecide}
                    title={canDecide ? undefined : '仅管理员可执行审批'}
                    className="flex shrink-0 items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {busy ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                    批准并执行
                  </button>
                  <button
                    onClick={event => { event.stopPropagation(); onReject(log) }}
                    disabled={busy || !canDecide}
                    title={canDecide ? undefined : '仅管理员可执行审批'}
                    className="flex shrink-0 items-center gap-1 rounded-lg border border-red-300 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
                  >
                    <XCircle size={12} /> 拒绝
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      })}
      {pending.length > 0 && (
        <p className="flex items-center gap-1.5 pt-1 text-[11px] text-gray-400">
          <AlertTriangle size={11} />
          点击任意条目打开「前因后果」详情弹窗:数据变化、哨兵判定依据与执行效果,看明白再裁决。
        </p>
      )}
    </div>
  )
}
