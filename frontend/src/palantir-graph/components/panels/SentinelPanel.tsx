import {
  BoltIcon,
  ShieldExclamationIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import {
  createEmptySentinelDraft,
} from './sentinelDefinitionModel'
import { sentinelToDraft } from './sentinelDefinitionMapper'
import { SentinelDefinitionEditor } from './SentinelDefinitionEditor'
import { SentinelDefinitionList } from './SentinelDefinitionList'
import { SentinelFiringHistory } from './SentinelFiringHistory'
import { SentinelRuntimeStatus } from './SentinelRuntimeStatus'
import { useSentinelPanelController } from './useSentinelPanelController'

interface Props {
  isOpen: boolean
  onClose: () => void
}

export default function SentinelPanel({ isOpen, onClose }: Props) {
  const controller = useSentinelPanelController({ isOpen })

  if (!isOpen) return null

  const {
    workspaceMode,
    runtimeAccessible,
    definitionEditable,
    operationalEditable,
    list,
    firings,
    cdcStatus,
    draft,
    setDraft,
    busy,
    operationalBusyId,
    tab,
    setTab,
    error,
    manualRunFiringIds,
    definitionLoadState,
    objectTypes,
    linkTypes,
    actions,
    objectTypeName,
    propertiesOf,
    saveDraft,
    runNow,
    toggleDraftSentinel,
    updateOperationalState,
    removeSentinel,
  } = controller

  return (
    <>
      <div className="fixed inset-0 z-[60] bg-black/30" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 w-[640px] z-[70] glass border-l border-surface-700 animate-slide-in-right flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface-700 flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-rose-500/20 flex items-center justify-center">
              <ShieldExclamationIcon className="w-5 h-5 text-rose-400" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-surface-100">哨兵引擎</h2>
              <p className="text-[11px] text-surface-400">监听对象变化 → 条件判断 → 执行动作</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={runNow} disabled={busy || !runtimeAccessible}
              title={!runtimeAccessible ? '只有当前发布态可以手动触发哨兵' : undefined}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-rose-500/90 hover:bg-rose-500 text-white text-xs disabled:opacity-50">
              <BoltIcon className="w-4 h-4" /> 手动触发
            </button>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-surface-800" aria-label="关闭哨兵引擎" title="关闭哨兵引擎">
              <XMarkIcon className="w-5 h-5 text-surface-400" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-surface-700 text-xs flex-shrink-0">
          {(['list', 'firings'] as const).map(item => (
            <button key={item} onClick={() => setTab(item)}
              className={`px-4 py-2.5 ${tab === item ? 'text-rose-400 border-b-2 border-rose-400' : 'text-surface-400'}`}>
              {item === 'list'
                ? `哨兵 (${definitionLoadState === 'loading' && list.length === 0 ? '…' : list.length})`
                : `触发日志 (${firings.length})`}
            </button>
          ))}
        </div>

        {/* 唯一滚动容器 */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <SentinelRuntimeStatus
            runtimeAccessible={runtimeAccessible}
            workspaceMode={workspaceMode}
            error={error}
            cdcStatus={cdcStatus}
          />

          {tab === 'list' && !draft && (
            <SentinelDefinitionList
              list={list}
              definitionLoadState={definitionLoadState}
              definitionEditable={definitionEditable}
              operationalEditable={operationalEditable}
              operationalBusyId={operationalBusyId}
              objectTypeName={objectTypeName}
              onCreate={() => setDraft(createEmptySentinelDraft())}
              onEdit={sentinel => setDraft(sentinelToDraft(sentinel))}
              onToggleDraft={toggleDraftSentinel}
              onRemove={removeSentinel}
              onUpdateOperationalState={updateOperationalState}
            />
          )}

          {tab === 'list' && draft && (
            <SentinelDefinitionEditor
              draft={draft}
              busy={busy}
              objectTypes={objectTypes}
              linkTypes={linkTypes}
              actions={actions}
              objectTypeName={objectTypeName}
              propertiesOf={propertiesOf}
              onChange={setDraft}
              onSave={saveDraft}
              onCancel={() => setDraft(null)}
            />
          )}

          {tab === 'firings' && (
            <SentinelFiringHistory
              firings={firings}
              manualRunFiringIds={manualRunFiringIds}
            />
          )}
        </div>
      </div>
      <style>{`.inp{background:rgb(30 30 38);border:1px solid rgb(60 60 72);border-radius:6px;padding:5px 8px;color:#e5e5ea;font-size:12px;width:100%}.inp:focus{outline:none;border-color:#fb7185}.inp-inline{background:rgb(38 38 48);border:1px solid rgb(63 63 76);border-radius:6px;padding:3px 7px;color:#e5e5ea;font-size:12px;max-width:160px}.inp-inline:focus{outline:none;border-color:#fb7185}select.inp-inline{cursor:pointer}`}</style>
    </>
  )
}
