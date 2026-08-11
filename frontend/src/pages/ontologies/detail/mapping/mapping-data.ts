import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import type { CuratedDataset } from '@/api/v2/curated'
import type { DatasetOverviewItem, DatasetSchemaColumn } from '@/api/v2/datasets'
import type { DatasetVersionSummary } from './mapping-review'

export * from './mapping-review'

export interface MappingProperty {
  id: string
  name: string
  displayName?: string
  type?: string
  required?: boolean
  source?: string
  computed?: boolean
}

export interface MappingObjectType {
  id: string
  name: string
  displayName: string
  primaryKey?: string | null
  properties: MappingProperty[]
}

export interface MappingLinkType {
  id: string
  name: string
  displayName: string
  sourceObjectTypeId: string
  targetObjectTypeId: string
  cardinality: string
  properties?: MappingProperty[]
}

export interface ObjectMappingRecord {
  id: string
  curated_dataset_id: string | null
  dataset_name: string | null
  row_count: number | null
  entity_class: string
  field_mapping: Record<string, string | boolean | unknown>
  status: string
  confidence: number | null
  target_object_type_id: string | null
  binding_mode: 'bound' | 'name_match' | 'auto_create'
  resolved_object_type: { id: string; name: string; display_name: string } | null
  auto_apply_on_review: boolean
  auto_apply_on_version: boolean
  dataset_kind: string | null
  dataset_source: string | null
}

export interface LinkMappingRecord {
  id: string
  relation_type: string
  src_key: string
  tgt_key: string
  src_dataset_id: string | null
  tgt_dataset_id: string | null
  edge_dataset_id: string | null
  field_mapping: Record<string, string | boolean | unknown>
  link_type_id?: string | null
  status?: string
  is_fat: boolean
  auto_apply_on_review: boolean
  auto_apply_on_version: boolean
}

interface MappingWorkspaceResponse {
  objectTypes?: MappingObjectType[]
  linkTypes?: MappingLinkType[]
  mappings?: unknown[]
  linkMappings?: unknown[]
  revision?: string
  editable?: boolean
  workspaceMode?: 'draft' | 'trial' | 'release' | 'archived'
  versionId?: string
  versionNumber?: string
  isCurrentRelease?: boolean
}

export interface MappingDataset {
  id: string
  name: string
  rows: number | null
  quality: number | null
  primaryKeyColumns: string[]
  source: 'curated' | 'manual'
  sourceLabel: string
  /** curated 数据集的审核状态（approved/pending_review/rejected）；人工数据集无审核流，恒为 null。 */
  reviewStatus: string | null
  columns: DatasetSchemaColumn[]
}

export interface ObjectInstanceSummary {
  id: string
  objectTypeId: string
  ontologyReleaseId?: string | null
}

export interface LinkInstanceSummary {
  id: string
  linkTypeId: string
  ontologyReleaseId?: string | null
}

function primaryKeyColumns(value: unknown): string[] {
  if (typeof value !== 'string') return []
  return value.split(',').map(column => column.trim()).filter(Boolean)
}

export function userFieldMapping(mapping: ObjectMappingRecord | undefined): Record<string, string> {
  if (!mapping) return {}
  return Object.fromEntries(
    Object.entries(mapping.field_mapping || {})
      .filter(([key, value]) => !key.startsWith('__') && typeof value === 'string') as Array<[string, string]>,
  )
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/** Normalize both immutable snapshot DTOs (camelCase) and legacy runtime DTOs. */
export function normalizeObjectMapping(value: unknown): ObjectMappingRecord {
  const raw = recordValue(value)
  const fieldMapping = recordValue(raw.field_mapping ?? raw.fieldMapping) as ObjectMappingRecord['field_mapping']
  const targetObjectTypeId = String(raw.target_object_type_id ?? raw.targetObjectTypeId ?? '') || null
  const resolved = recordValue(raw.resolved_object_type)
  const resolvedObjectType = resolved.id ? {
    id: String(resolved.id),
    name: String(resolved.name ?? ''),
    display_name: String(resolved.display_name ?? resolved.displayName ?? ''),
  } : null
  const rawBindingMode = raw.binding_mode
  const bindingMode: ObjectMappingRecord['binding_mode'] = rawBindingMode === 'name_match' || rawBindingMode === 'auto_create'
    ? rawBindingMode
    : targetObjectTypeId ? 'bound' : 'auto_create'
  const curatedDatasetId = String(raw.curated_dataset_id ?? raw.curatedDatasetId ?? '') || null
  return {
    id: String(raw.id ?? ''),
    curated_dataset_id: curatedDatasetId,
    dataset_name: raw.dataset_name == null ? null : String(raw.dataset_name),
    row_count: raw.row_count == null ? null : Number(raw.row_count),
    entity_class: String(raw.entity_class ?? raw.entityClass ?? ''),
    field_mapping: fieldMapping,
    status: String(raw.status ?? 'draft'),
    confidence: raw.confidence == null ? null : Number(raw.confidence),
    target_object_type_id: targetObjectTypeId,
    binding_mode: bindingMode,
    resolved_object_type: resolvedObjectType,
    auto_apply_on_review: Boolean(raw.auto_apply_on_review ?? fieldMapping.__auto_apply_on_review__),
    auto_apply_on_version: Boolean(raw.auto_apply_on_version ?? fieldMapping.__auto_apply_on_version__),
    dataset_kind: raw.dataset_kind == null ? null : String(raw.dataset_kind),
    dataset_source: raw.dataset_source == null ? null : String(raw.dataset_source),
  }
}

/** Normalize both immutable snapshot DTOs (camelCase) and legacy runtime DTOs. */
export function normalizeLinkMapping(value: unknown): LinkMappingRecord {
  const raw = recordValue(value)
  const fieldMapping = recordValue(raw.field_mapping ?? raw.fieldMapping) as LinkMappingRecord['field_mapping']
  const edgeDatasetId = String(raw.edge_dataset_id ?? raw.edgeDatasetId ?? '') || null
  return {
    id: String(raw.id ?? ''),
    relation_type: String(raw.relation_type ?? raw.relationType ?? ''),
    src_key: String(raw.src_key ?? raw.srcKey ?? ''),
    tgt_key: String(raw.tgt_key ?? raw.tgtKey ?? ''),
    src_dataset_id: String(raw.src_dataset_id ?? raw.srcDatasetId ?? '') || null,
    tgt_dataset_id: String(raw.tgt_dataset_id ?? raw.tgtDatasetId ?? '') || null,
    edge_dataset_id: edgeDatasetId,
    field_mapping: fieldMapping,
    link_type_id: String(raw.link_type_id ?? raw.linkTypeId ?? '') || null,
    status: raw.status == null ? undefined : String(raw.status),
    is_fat: Boolean(raw.is_fat ?? edgeDatasetId),
    auto_apply_on_review: Boolean(raw.auto_apply_on_review ?? fieldMapping.__auto_apply_on_review__),
    auto_apply_on_version: Boolean(raw.auto_apply_on_version ?? fieldMapping.__auto_apply_on_version__),
  }
}

export function mappingTargetId(mapping: ObjectMappingRecord): string | null {
  return mapping.target_object_type_id || mapping.resolved_object_type?.id || null
}

export function linkMappingForType(type: MappingLinkType, mappings: LinkMappingRecord[]) {
  return mappings.find(mapping => mapping.link_type_id === type.id)
    || mappings.find(mapping => mapping.relation_type === type.name || mapping.relation_type === type.displayName)
}

// 类型词表归一化与兼容判定已抽取到纯函数模块 mapping-types（可被 node 单测直接
// 加载）；这里 re-export 保持既有导入路径不变。
export { normalizeType, typesCompatible } from '../../mapping/mapping-types'

export function useMappingData(
  ontologyId: string,
  requireCollectionStatus = false,
  versionId: string | null = null,
  enabled = true,
) {
  const structurePath = versionId
    ? `/ontologies/${ontologyId}/versions/${versionId}/workspace`
    : `/ontologies/${ontologyId}/current-release/workspace`
  const sourceKey = versionId || 'current-release'
  const workspaceQuery = useQuery<MappingWorkspaceResponse>({
    queryKey: ['mapping-snapshot', ontologyId, sourceKey],
    enabled,
    queryFn: () => apiClientV2.get(structurePath),
  })
  const objectTypes = useMemo(
    () => workspaceQuery.data?.objectTypes || [], [workspaceQuery.data?.objectTypes])
  const linkTypes = useMemo(
    () => workspaceQuery.data?.linkTypes || [], [workspaceQuery.data?.linkTypes])
  const mappings = useMemo(
    () => (workspaceQuery.data?.mappings || []).map(normalizeObjectMapping),
    [workspaceQuery.data?.mappings],
  )
  const linkMappings = useMemo(
    () => (workspaceQuery.data?.linkMappings || []).map(normalizeLinkMapping),
    [workspaceQuery.data?.linkMappings],
  )
  const currentReleaseId = !versionId ? workspaceQuery.data?.versionId : undefined
  const curatedQuery = useQuery<CuratedDataset[]>({
    queryKey: ['curated-all'],
    enabled,
    queryFn: () => apiClientV2.get('/curated'),
  })
  const manualQuery = useQuery<{ items: DatasetOverviewItem[] }>({
    queryKey: ['manual-datasets-overview'],
    enabled,
    queryFn: () => apiClientV2.get('/datasets/overview'),
  })
  const instancesQuery = useQuery<ObjectInstanceSummary[]>({
    queryKey: ['mapping-object-instances', ontologyId, currentReleaseId],
    enabled: enabled && requireCollectionStatus && !versionId && Boolean(currentReleaseId),
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/instances?expected_release_id=${encodeURIComponent(currentReleaseId || '')}`,
    ),
  })
  const linkInstancesQuery = useQuery<LinkInstanceSummary[]>({
    queryKey: ['mapping-link-instances', ontologyId, currentReleaseId],
    enabled: enabled && requireCollectionStatus && !versionId && Boolean(currentReleaseId),
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/link-instances?expected_release_id=${encodeURIComponent(currentReleaseId || '')}`,
    ),
  })

  const referencedDatasetIds = useMemo(() => {
    const ids = new Set<string>()
    for (const mapping of mappings) {
      if (mapping.curated_dataset_id) ids.add(mapping.curated_dataset_id)
    }
    for (const mapping of linkMappings) {
      for (const datasetId of [
        mapping.src_dataset_id,
        mapping.tgt_dataset_id,
        mapping.edge_dataset_id,
      ]) {
        if (datasetId) ids.add(datasetId)
      }
    }
    return ids
  }, [linkMappings, mappings])

  const datasetBase = useMemo<Omit<MappingDataset, 'columns'>[]>(() => {
    // A curated dataset may have an approved production version while its latest
    // version is awaiting review. Keep already-mapped logical datasets visible in
    // that state so the versioned mapping and its review subscription do not
    // disappear exactly when a reviewer needs to understand the downstream path.
    const approved = (curatedQuery.data || [])
      .filter(item => item.status === 'approved' || referencedDatasetIds.has(item.id))
      .map(item => ({
        id: item.id,
        name: item.name,
        rows: item.row_count,
        quality: item.quality_score,
        primaryKeyColumns: primaryKeyColumns(item.primary_key),
        source: 'curated' as const,
        sourceLabel: '成品数据集',
        reviewStatus: item.status,
      }))
    const manual = (manualQuery.data?.items || [])
      .filter(item => item.source === 'upload' || item.source === 'manual')
      .map(item => ({
        id: item.id,
        name: item.name,
        rows: item.rowcount,
        quality: null,
        primaryKeyColumns: primaryKeyColumns(item.primary_key),
        source: 'manual' as const,
        sourceLabel: '人工数据集',
        reviewStatus: null,
      }))
    const unique = new Map<string, Omit<MappingDataset, 'columns'>>()
    for (const item of [...approved, ...manual]) unique.set(item.id, item)
    return [...unique.values()]
  }, [curatedQuery.data, linkMappings, manualQuery.data, mappings])

  const schemasQuery = useQuery<Record<string, DatasetSchemaColumn[]>>({
    queryKey: ['mapping-dataset-schemas', datasetBase.map(item => item.id).join(',')],
    enabled: enabled && datasetBase.length > 0,
    queryFn: async () => {
      const pairs = await Promise.all(datasetBase.map(async dataset => {
        try {
          const result = await apiClientV2.get<{ columns: DatasetSchemaColumn[] }>(`/datasets/${dataset.id}/schema`)
          return [dataset.id, result.columns || []] as const
        } catch {
          return [dataset.id, []] as const
        }
      }))
      return Object.fromEntries(pairs)
    },
  })

  const datasets = useMemo<MappingDataset[]>(() => datasetBase.map(item => ({
    ...item,
    columns: schemasQuery.data?.[item.id] || [],
  })), [datasetBase, schemasQuery.data])

  // 已灌入版本新鲜度：仅对被映射引用的数据集拉版本列表；失败静默降级（不展示、不阻塞）。
  const referencedIdsKey = useMemo(() => [...referencedDatasetIds].sort(), [referencedDatasetIds])
  const versionsQuery = useQuery<Record<string, DatasetVersionSummary[]>>({
    queryKey: ['mapping-dataset-versions', referencedIdsKey.join(',')],
    enabled: enabled && referencedIdsKey.length > 0,
    queryFn: async () => {
      const pairs = await Promise.all(referencedIdsKey.map(async datasetId => {
        try {
          const result = await apiClientV2.get<DatasetVersionSummary[]>(`/datasets/${datasetId}/versions`)
          return [datasetId, Array.isArray(result) ? result : []] as const
        } catch {
          return [datasetId, []] as const
        }
      }))
      return Object.fromEntries(pairs)
    },
  })
  const datasetVersionLists = useMemo(
    () => versionsQuery.data || {},
    [versionsQuery.data],
  )

  const requiredQueries: Array<{
    isLoading: boolean
    isError: boolean
    refetch: () => Promise<unknown>
  }> = [workspaceQuery, curatedQuery, manualQuery]
  if (requireCollectionStatus) requiredQueries.push(instancesQuery, linkInstancesQuery)

  return {
    objectTypes,
    linkTypes,
    mappings,
    linkMappings,
    objectInstances: instancesQuery.data || [],
    linkInstances: linkInstancesQuery.data || [],
    datasets,
    datasetVersionLists,
    workspaceRevision: workspaceQuery.data?.revision || null,
    workspaceEditable: workspaceQuery.data?.editable ?? null,
    workspaceMode: workspaceQuery.data?.workspaceMode || null,
    isLoading: requiredQueries.some(query => query.isLoading),
    isError: requiredQueries.some(query => query.isError),
    isLoadingSchemas: schemasQuery.isLoading,
    refetch: () => Promise.all(requiredQueries.map(query => query.refetch())),
  }
}
