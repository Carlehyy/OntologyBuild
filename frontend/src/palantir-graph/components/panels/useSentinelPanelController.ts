import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { v4 as uuidv4 } from 'uuid'
import { apiClientV2 } from '../../../api/client'
import {
  sentinelApi,
  type Sentinel,
  type SentinelCdcStatus,
  type SentinelFiring,
} from '../../../api/sentinelApi'
import { useOntologyStore } from '../../store/ontologyStore'
import { sentinelDraftBody } from './sentinelDefinitionMapper'
import type {
  DefinitionLoadState,
  SentinelDraft,
} from './sentinelDefinitionModel'

export type SentinelPanelTab = 'list' | 'firings'

interface UseSentinelPanelControllerOptions {
  isOpen: boolean
}

const errorText = (error: any) => (
  typeof error?.detail === 'string'
    ? error.detail
    : (error?.detail?.message || error?.message || '请求失败')
)

export function useSentinelPanelController({
  isOpen,
}: UseSentinelPanelControllerOptions) {
  const { id: ontologyId } = useParams<{ id: string }>()
  const { ontology } = useOntologyStore()
  const workspaceMode = useOntologyStore(state => state.workspaceMode)
  const workspaceVersionId = useOntologyStore(
    state => state.workspaceVersionId,
  )
  const workspaceSentinels = useOntologyStore(
    state => state.workspaceSentinels,
  )
  const workspaceTrialRun = useOntologyStore(
    state => state.workspaceTrialRun,
  )
  const revision = useOntologyStore(state => state.revision)
  const runtimeAccessible = workspaceMode === 'runtime'
  const definitionEditable = workspaceMode === 'draft'
  const operationalEditable = runtimeAccessible
  const [list, setList] = useState<Sentinel[]>([])
  const [firings, setFirings] = useState<SentinelFiring[]>([])
  const [cdcStatus, setCdcStatus] = useState<SentinelCdcStatus | null>(null)
  const [draft, setDraft] = useState<SentinelDraft | null>(null)
  const [busy, setBusy] = useState(false)
  const [operationalBusyId, setOperationalBusyId] = useState<string | null>(
    null,
  )
  const [tab, setTab] = useState<SentinelPanelTab>('list')
  const [error, setError] = useState<string | null>(null)
  const [manualRunFiringIds, setManualRunFiringIds] = useState<Set<string>>(
    () => new Set(),
  )
  const [definitionLoadState, setDefinitionLoadState] =
    useState<DefinitionLoadState>('idle')

  const objectTypes = ontology?.objectTypes || []
  const linkTypes = ontology?.linkTypes || []
  const actions = ontology?.actions || []

  const objectTypeName = (id: string) => (
    objectTypes.find(objectType => objectType.id === id)?.displayName
    || '未选择'
  )

  const propertiesOf = (objectTypeId: string) => (
    objectTypes.find(objectType => objectType.id === objectTypeId)?.properties
    || []
  )

  const refresh = async () => {
    if (!ontologyId) return
    if (!runtimeAccessible) {
      setList(workspaceSentinels)
      setCdcStatus(null)
      setDefinitionLoadState('ready')
      const results = workspaceMode === 'trial'
        ? (workspaceTrialRun?.result?.sentinels || [])
        : []
      setFirings(results.map((item, index): SentinelFiring => ({
        id: `trial-${item.id || index}`,
        sentinelId: item.id || `trial-${index}`,
        sentinelName: item.name || '未命名哨兵',
        triggerSource: 'trial',
        status: (item.errors || []).length > 0
          ? 'error'
          : item.skipped ? 'skipped' : 'evaluated',
        matchCount: item.matched || 0,
        matches: [],
        entered: [],
        left: [],
        actionResults: [],
        error: (item.errors || []).join('；') || undefined,
        durationMs: 0,
      })))
      setError(null)
      return
    }

    // 失败必须可见：静默吞错会把"后端挂了"伪装成"没有哨兵"。
    setDefinitionLoadState('loading')
    setError(null)
    const definitionRequest = sentinelApi.list(ontologyId)
    // 辅助运行信息不能阻塞发布定义展示；allSettled 也避免任一失败
    // 让已成功的请求结果被整体丢弃。
    const auxiliaryRequests = Promise.allSettled([
      sentinelApi.firings(ontologyId),
      sentinelApi.cdcStatus(ontologyId),
    ])
    const loadErrors: string[] = []
    try {
      const sentinels = await definitionRequest
      setList((sentinels || []) as Sentinel[])
      setDefinitionLoadState('ready')
    } catch (requestError: any) {
      setList([])
      setDefinitionLoadState('error')
      loadErrors.push(`加载哨兵定义失败：${errorText(requestError)}`)
    }

    const [firingsResult, cdcResult] = await auxiliaryRequests
    if (firingsResult.status === 'fulfilled') {
      setFirings((firingsResult.value || []) as SentinelFiring[])
    } else {
      loadErrors.push(`加载触发日志失败：${errorText(firingsResult.reason)}`)
    }
    if (cdcResult.status === 'fulfilled') {
      setCdcStatus(cdcResult.value)
    } else {
      loadErrors.push(`加载变化执行链状态失败：${errorText(cdcResult.reason)}`)
    }
    setError(loadErrors.length > 0 ? loadErrors.join('；') : null)
  }

  useEffect(() => {
    if (isOpen) void refresh()
  }, [
    isOpen,
    ontologyId,
    runtimeAccessible,
    workspaceSentinels,
    workspaceTrialRun,
  ])

  useEffect(() => {
    if (!isOpen) setManualRunFiringIds(new Set())
  }, [isOpen])

  useEffect(() => {
    if (!definitionEditable) setDraft(null)
  }, [definitionEditable])

  const saveDraft = async () => {
    if (!ontologyId || !draft || !definitionEditable) return
    setBusy(true)
    try {
      const body: any = sentinelDraftBody(draft)
      if (!workspaceVersionId) throw new Error('缺少草稿版本标识')
      const id = draft.id || uuidv4()
      const previous = list.find(item => item.id === id)
      const nextSentinel: Sentinel = {
        ...(previous || {} as Sentinel),
        ...body,
        id,
        ontologyId,
        name: previous?.name || draft.displayName,
        status: 'draft',
      }
      const nextList = previous
        ? list.map(item => item.id === id ? nextSentinel : item)
        : [...list, nextSentinel]
      const result = await apiClientV2.put<{ revision: string }>(
        `/ontologies/${ontologyId}/versions/${workspaceVersionId}/workspace/mappings`,
        { baseRevision: revision, sentinels: nextList },
      )
      setList(nextList)
      useOntologyStore.setState({
        workspaceSentinels: nextList,
        revision: result.revision,
      })
      setDraft(null)
      setError(null)
      await refresh()
    } catch (requestError: any) {
      setError(`保存哨兵失败：${errorText(requestError)}`)
    } finally {
      setBusy(false)
    }
  }

  const runNow = async () => {
    if (!ontologyId || !runtimeAccessible) return
    setBusy(true)
    setManualRunFiringIds(new Set())
    try {
      const manualRunStartedAt = Date.now()
      const previousIds = new Set(firings.map(item => item.id))
      const result = await sentinelApi.run(ontologyId)
      let currentIds = (result?.firings || [])
        .map(item => item.id)
        .filter((id): id is string => !!id)
      // Rolling upgrades may briefly pair this frontend with an older backend
      // whose otherwise-compatible run summary has no firing ids. In that
      // window, identify only newly-created manual records rather than marking
      // an arbitrary historical "manual" card as the current run.
      if (currentIds.length === 0) {
        const latest = await sentinelApi.firings(ontologyId)
        currentIds = (latest || [])
          .filter(item => {
            const createdAt = item.createdAt
              ? new Date(item.createdAt).getTime()
              : Number.NaN
            return (
              item.triggerSource === 'manual'
              && !previousIds.has(item.id)
              && Number.isFinite(createdAt)
              && createdAt >= manualRunStartedAt - 30_000
            )
          })
          .map(item => item.id)
      }
      setManualRunFiringIds(new Set(currentIds))
      setError(null)
      await refresh()
      setTab('firings')
    } catch (requestError: any) {
      setError(`手动触发失败：${errorText(requestError)}`)
    } finally {
      setBusy(false)
    }
  }

  const toggleDraftSentinel = async (sentinel: Sentinel) => {
    if (!ontologyId || !definitionEditable) return
    try {
      if (!workspaceVersionId) throw new Error('缺少草稿版本标识')
      const nextList = list.map(item => (
        item.id === sentinel.id
          ? { ...item, enabled: !item.enabled }
          : item
      ))
      const result = await apiClientV2.put<{ revision: string }>(
        `/ontologies/${ontologyId}/versions/${workspaceVersionId}/workspace/mappings`,
        { baseRevision: revision, sentinels: nextList },
      )
      setList(nextList)
      useOntologyStore.setState({
        workspaceSentinels: nextList,
        revision: result.revision,
      })
      setError(null)
    } catch (requestError: any) {
      setError(`切换启停失败：${errorText(requestError)}`)
    }
  }

  const updateOperationalState = async (
    sentinel: Sentinel,
    patch: { enabled?: boolean; muted?: boolean },
  ) => {
    if (!ontologyId || !operationalEditable) return
    if (!sentinel.releaseId) {
      await refresh()
      setError('运行态列表缺少发布版本标识，已自动刷新；请确认后端发布状态')
      return
    }
    setOperationalBusyId(sentinel.id)
    try {
      const updated = await sentinelApi.updateOperationalState(
        ontologyId,
        sentinel.id,
        {
          ...patch,
          expectedReleaseId: sentinel.releaseId,
          expectedGeneration: sentinel.enableGeneration ?? 0,
        },
      )
      setList(items => items.map(
        item => item.id === updated.id ? updated : item,
      ))
      setError(null)
    } catch (requestError: any) {
      const code = requestError?.detail?.code
      if (
        code === 'release_context_changed'
        || code === 'current_release_missing'
        || code === 'current_release_invalid'
        || code === 'builtin_sentinel_generation_conflict'
        || code === 'builtin_sentinel_not_in_current_release'
        || code === 'builtin_sentinel_not_operational'
      ) {
        await refresh()
      }
      setError(`修改运行状态失败：${errorText(requestError)}`)
    } finally {
      setOperationalBusyId(null)
    }
  }

  const removeSentinel = async (sentinel: Sentinel) => {
    if (
      !ontologyId
      || !definitionEditable
      || !confirm('删除该哨兵？触发历史会保留。')
    ) return
    try {
      if (!workspaceVersionId) throw new Error('缺少草稿版本标识')
      const nextList = list.filter(item => item.id !== sentinel.id)
      const result = await apiClientV2.put<{ revision: string }>(
        `/ontologies/${ontologyId}/versions/${workspaceVersionId}/workspace/mappings`,
        { baseRevision: revision, sentinels: nextList },
      )
      setList(nextList)
      useOntologyStore.setState({
        workspaceSentinels: nextList,
        revision: result.revision,
      })
      setError(null)
    } catch (requestError: any) {
      setError(`删除失败：${errorText(requestError)}`)
    }
  }

  return {
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
  }
}

export type SentinelPanelController = ReturnType<
  typeof useSentinelPanelController
>
