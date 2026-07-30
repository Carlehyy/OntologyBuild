import type { Sentinel } from '../../../api/sentinelApi'
import { compileSentinelCondition } from './sentinelDefinitionCompiler'
import type {
  SentinelConditionRow,
  SentinelDraft,
} from './sentinelDefinitionModel'

export function sentinelToDraft(sentinel: Sentinel): SentinelDraft {
  return {
    id: sentinel.id,
    displayName: sentinel.displayName,
    description: sentinel.description,
    bindings: (sentinel.bindings || []).map(binding => ({
      alias: binding.alias,
      objectTypeId: binding.objectTypeId,
      filter: binding.filter,
    })),
    links: (sentinel.links || []).map(link => ({ ...link })),
    primaryAlias: sentinel.primaryAlias || sentinel.bindings?.[0]?.alias || 'a',
    condRows: (sentinel.conditionRows || []) as SentinelConditionRow[],
    condLogic: (sentinel.conditionLogic || 'and') as 'and' | 'or',
    advanced: !(sentinel.conditionRows?.length) && !!sentinel.condition,
    conditionRaw: sentinel.condition || '',
    actionIds: sentinel.actionIds || [],
    actionParameters: Object.fromEntries(
      Object.entries(sentinel.actionParameters || {}).map(
        ([actionId, params]) => [actionId, { ...(params || {}) }],
      ),
    ),
    onChange: sentinel.onChange,
    onSchedule: sentinel.onSchedule,
    scanIntervalSeconds: sentinel.scanIntervalSeconds,
    triggerMode: sentinel.triggerMode || 'on_enter',
    muted: !!sentinel.muted,
    enabled: sentinel.enabled,
  }
}

export function sentinelDraftBody(draft: SentinelDraft) {
  return {
    name: draft.displayName,
    displayName: draft.displayName,
    description: draft.description,
    bindings: draft.bindings.map(binding => ({
      alias: binding.alias,
      objectTypeId: binding.objectTypeId,
      filter: binding.filter ?? null,
    })),
    links: draft.links,
    condition: draft.advanced
      ? draft.conditionRaw
      : compileSentinelCondition(draft.condRows, draft.condLogic),
    conditionRows: draft.advanced ? [] : draft.condRows,
    conditionLogic: draft.condLogic,
    primaryAlias: draft.primaryAlias || draft.bindings[0]?.alias,
    actionIds: draft.actionIds,
    actionParameters: draft.actionParameters,
    onChange: draft.onChange,
    onSchedule: draft.onSchedule,
    scanIntervalSeconds: draft.scanIntervalSeconds,
    triggerMode: draft.triggerMode,
    muted: draft.muted,
    enabled: draft.enabled,
    status: 'published',
  }
}
