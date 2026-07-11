import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import type { CuratedDataset } from '@/api/v2/curated'
import type { DatasetOverviewItem, DatasetSchemaColumn } from '@/api/v2/datasets'

export interface MappingProperty {
  id: string
  name: string
  displayName?: string
  type?: string
  required?: boolean
  source?: string
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
}

export interface LinkMappingRecord {
  id: string
  relation_type: string
  src_key: string
  tgt_key: string
  src_dataset_id: string | null
  tgt_dataset_id: string | null
  edge_dataset_id: string | null
  field_mapping: Record<string, string>
  link_type_id?: string | null
  status?: string
  is_fat: boolean
}

export interface MappingDataset {
  id: string
  name: string
  rows: number | null
  quality: number | null
  primaryKeyColumns: string[]
  source: 'curated' | 'manual'
  sourceLabel: string
  columns: DatasetSchemaColumn[]
}

export interface ObjectInstanceSummary {
  id: string
  objectTypeId: string
}

export interface LinkInstanceSummary {
  id: string
  linkTypeId: string
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

export function mappingTargetId(mapping: ObjectMappingRecord): string | null {
  return mapping.target_object_type_id || mapping.resolved_object_type?.id || null
}

export function linkMappingForType(type: MappingLinkType, mappings: LinkMappingRecord[]) {
  return mappings.find(mapping => mapping.link_type_id === type.id)
    || mappings.find(mapping => mapping.relation_type === type.name || mapping.relation_type === type.displayName)
}

export function normalizeType(value?: string): string {
  const type = (value || 'string').trim().toLowerCase().replace(/\(.*\)/, '')
  if (['string', 'text', 'varchar', 'char', 'uuid'].includes(type)) return 'string'
  if (['int', 'integer', 'bigint', 'smallint', 'number', 'float', 'double', 'decimal', 'decimal128'].includes(type)) return 'number'
  if (['date', 'datetime', 'timestamp', 'time'].includes(type)) return 'datetime'
  if (['bool', 'boolean'].includes(type)) return 'boolean'
  if (['array', 'list', 'set'].includes(type)) return 'array'
  if (['json', 'object', 'map'].includes(type)) return 'json'
  return type
}

export function typesCompatible(source?: string, target?: string): boolean {
  return normalizeType(source) === normalizeType(target)
}

export function useMappingData(ontologyId: string, requireCollectionStatus = false) {
  const objectTypesQuery = useQuery<MappingObjectType[]>({
    queryKey: ['formal-object-types', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/object-types`),
  })
  const linkTypesQuery = useQuery<MappingLinkType[]>({
    queryKey: ['formal-link-types', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/link-types`),
  })
  const mappingsQuery = useQuery<ObjectMappingRecord[]>({
    queryKey: ['mappings', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/mappings`),
  })
  const linkMappingsQuery = useQuery<LinkMappingRecord[]>({
    queryKey: ['link-mappings', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/link-mappings`),
  })
  const curatedQuery = useQuery<CuratedDataset[]>({
    queryKey: ['curated-all'],
    queryFn: () => apiClientV2.get('/curated'),
  })
  const manualQuery = useQuery<{ items: DatasetOverviewItem[] }>({
    queryKey: ['manual-datasets-overview'],
    queryFn: () => apiClientV2.get('/datasets/overview'),
  })
  const instancesQuery = useQuery<ObjectInstanceSummary[]>({
    queryKey: ['mapping-object-instances', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/instances`),
  })
  const linkInstancesQuery = useQuery<LinkInstanceSummary[]>({
    queryKey: ['mapping-link-instances', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/link-instances`),
  })

  const datasetBase = useMemo<Omit<MappingDataset, 'columns'>[]>(() => {
    const approved = (curatedQuery.data || [])
      .filter(item => item.status === 'approved')
      .map(item => ({
        id: item.id,
        name: item.name,
        rows: item.row_count,
        quality: item.quality_score,
        primaryKeyColumns: primaryKeyColumns(item.primary_key),
        source: 'curated' as const,
        sourceLabel: '成品数据集',
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
      }))
    const unique = new Map<string, Omit<MappingDataset, 'columns'>>()
    for (const item of [...approved, ...manual]) unique.set(item.id, item)
    return [...unique.values()]
  }, [curatedQuery.data, manualQuery.data])

  const schemasQuery = useQuery<Record<string, DatasetSchemaColumn[]>>({
    queryKey: ['mapping-dataset-schemas', datasetBase.map(item => item.id).join(',')],
    enabled: datasetBase.length > 0,
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

  const requiredQueries: Array<{
    isLoading: boolean
    isError: boolean
    refetch: () => Promise<unknown>
  }> = [objectTypesQuery, linkTypesQuery, mappingsQuery, linkMappingsQuery, curatedQuery, manualQuery]
  if (requireCollectionStatus) requiredQueries.push(instancesQuery, linkInstancesQuery)

  return {
    objectTypes: objectTypesQuery.data || [],
    linkTypes: linkTypesQuery.data || [],
    mappings: mappingsQuery.data || [],
    linkMappings: linkMappingsQuery.data || [],
    objectInstances: instancesQuery.data || [],
    linkInstances: linkInstancesQuery.data || [],
    datasets,
    isLoading: requiredQueries.some(query => query.isLoading),
    isError: requiredQueries.some(query => query.isError),
    isLoadingSchemas: schemasQuery.isLoading,
    refetch: () => Promise.all(requiredQueries.map(query => query.refetch())),
  }
}
