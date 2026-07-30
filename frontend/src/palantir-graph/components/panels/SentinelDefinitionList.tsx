import {
  PlusIcon,
  TrashIcon,
} from '@heroicons/react/24/outline'
import type { Sentinel } from '../../../api/sentinelApi'
import type { DefinitionLoadState } from './sentinelDefinitionModel'

interface SentinelDefinitionListProps {
  list: Sentinel[]
  definitionLoadState: DefinitionLoadState
  definitionEditable: boolean
  operationalEditable: boolean
  operationalBusyId: string | null
  objectTypeName: (id: string) => string
  onCreate: () => void
  onEdit: (sentinel: Sentinel) => void
  onToggleDraft: (sentinel: Sentinel) => Promise<void>
  onRemove: (sentinel: Sentinel) => Promise<void>
  onUpdateOperationalState: (
    sentinel: Sentinel,
    patch: { enabled?: boolean; muted?: boolean },
  ) => Promise<void>
}

export function SentinelDefinitionList({
  list,
  definitionLoadState,
  definitionEditable,
  operationalEditable,
  operationalBusyId,
  objectTypeName,
  onCreate,
  onEdit,
  onToggleDraft,
  onRemove,
  onUpdateOperationalState,
}: SentinelDefinitionListProps) {
  return (
    <>
      {definitionLoadState === 'loading' && list.length === 0 && (
        <p role="status" className="text-center text-xs text-surface-400 py-6">
          正在加载当前发布的哨兵定义…
        </p>
      )}
      {definitionEditable && (
        <button onClick={onCreate}
          className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg border border-dashed border-surface-600 text-surface-300 hover:border-rose-400 hover:text-rose-400 text-sm">
          <PlusIcon className="w-4 h-4" /> 新建哨兵
        </button>
      )}
      {list.map(sentinel => (
        <div key={sentinel.id} className="rounded-lg border border-surface-700 bg-surface-800/40 p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${sentinel.enabled ? 'bg-emerald-400' : 'bg-surface-500'}`} />
              <span className="text-sm text-surface-100">{sentinel.displayName}</span>
              {sentinel.muted && (
                <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                  静默
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              {definitionEditable && (
                <>
                  <button onClick={() => onEdit(sentinel)}
                    className="text-[11px] text-surface-400 hover:text-rose-400 px-1">编辑</button>
                  <button onClick={() => void onToggleDraft(sentinel)}
                    className="text-[11px] text-surface-400 hover:text-emerald-400 px-1">{sentinel.enabled ? '停用' : '启用'}</button>
                  <button onClick={() => void onRemove(sentinel)}
                    className="p-1 text-surface-400 hover:text-red-400"><TrashIcon className="w-3.5 h-3.5" /></button>
                </>
              )}
              {operationalEditable && (
                <>
                  <button
                    disabled={operationalBusyId === sentinel.id}
                    onClick={() => void onUpdateOperationalState(
                      sentinel,
                      { enabled: !sentinel.enabled },
                    )}
                    className="text-[11px] text-surface-400 hover:text-emerald-400 disabled:cursor-wait disabled:opacity-40 px-1">
                    {sentinel.enabled ? '停用' : '启用'}
                  </button>
                  <button
                    disabled={operationalBusyId === sentinel.id}
                    onClick={() => void onUpdateOperationalState(
                      sentinel,
                      { muted: !sentinel.muted },
                    )}
                    className="text-[11px] text-surface-400 hover:text-amber-300 disabled:cursor-wait disabled:opacity-40 px-1">
                    {sentinel.muted ? '解除静默' : '静默'}
                  </button>
                </>
              )}
            </div>
          </div>
          <div className="mt-2 text-[11px] text-surface-400 space-y-0.5">
            <div>监听：{(sentinel.bindings || []).map(binding => `${objectTypeName(binding.objectTypeId)}(${binding.alias})`).join('、')}</div>
            {sentinel.condition && <div>条件：<code className="text-amber-300">{sentinel.condition}</code></div>}
            <div>动作：{sentinel.actionIds?.length || 0} 个 · 时机：{[sentinel.onChange && '变化', sentinel.onSchedule && `扫描${sentinel.scanIntervalSeconds}s`].filter(Boolean).join(' / ') || '仅手动'}</div>
          </div>
        </div>
      ))}
      {definitionLoadState === 'ready' && list.length === 0 && (
        <p className="text-center text-xs text-surface-500 py-6">还没有哨兵</p>
      )}
    </>
  )
}
