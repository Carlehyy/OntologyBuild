import type { SentinelCdcStatus } from '../../../api/sentinelApi'

type WorkspaceMode = 'runtime' | 'draft' | 'trial' | 'release' | 'archived'

interface SentinelRuntimeStatusProps {
  runtimeAccessible: boolean
  workspaceMode: WorkspaceMode
  error: string | null
  cdcStatus: SentinelCdcStatus | null
}

export function SentinelRuntimeStatus({
  runtimeAccessible,
  workspaceMode,
  error,
  cdcStatus,
}: SentinelRuntimeStatusProps) {
  return (
    <>
      {!runtimeAccessible && (
        <div role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-200">
          {workspaceMode === 'draft'
            ? '草稿态可编辑哨兵定义，但不会评估条件或执行动作。'
            : workspaceMode === 'trial'
              ? '正在查看冻结的哨兵定义和隔离试跑评估；触发与修改均不可操作。'
              : '哨兵定义完整可见；历史或归档版本不读取当前正式触发记录，也不可修改。'}
        </div>
      )}
      {runtimeAccessible && (
        <div role="status" className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs leading-5 text-sky-200">
          当前定义来自不可变发布快照。发布态只允许幂等启停与静默控制；结构修改请进入草稿版本。
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}
      {runtimeAccessible && cdcStatus && (
        <div role="status" className={`rounded-lg border px-3 py-2 text-xs ${
          !cdcStatus.healthy
            ? 'border-red-500/40 bg-red-500/10 text-red-200'
            : !cdcStatus.quiescent
              ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
              : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
        }`}>
          <div className="font-medium">
            {!cdcStatus.healthy
              ? '变化执行链异常'
              : !cdcStatus.quiescent
                ? '变化执行链处理中'
                : '变化执行链正常'}
          </div>
          <div className="mt-1 text-[10px] opacity-80">
            Worker：{cdcStatus.worker_alive ? '运行中' : '未运行'} ·
            held {cdcStatus.durable.held || 0} ·
            pending {cdcStatus.durable.pending || 0} ·
            processing {cdcStatus.durable.processing || 0} ·
            retry {cdcStatus.durable.retry || 0} ·
            dead {cdcStatus.durable.dead || 0}
          </div>
          {(cdcStatus.last_error || cdcStatus.last_errors[0]?.error) && (
            <div className="mt-1 break-all text-[10px]">
              最近错误：{cdcStatus.last_error || cdcStatus.last_errors[0]?.error}
            </div>
          )}
        </div>
      )}
    </>
  )
}
