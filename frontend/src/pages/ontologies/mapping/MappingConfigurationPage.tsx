import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow,
  useEdgesState, useNodesState,
  type Connection, type Edge, type Node, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  AlertCircle, ArrowLeft, ArrowRight, BookOpen, Boxes, Check, CheckCircle2,
  ChevronDown, ChevronRight, Database, Eye, GitBranch, KeyRound, LayoutGrid,
  Link2, Loader2, Plus, Save, Search, Table2, Trash2, X,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import {
  linkMappingForType, mappingTargetId, normalizeType, typesCompatible, userFieldMapping,
  useMappingData, type LinkMappingRecord, type MappingDataset, type MappingLinkType,
  type MappingObjectType, type MappingProperty, type ObjectMappingRecord,
} from '../detail/mapping/mapping-data'
import './mapping-configuration.css'

type DatasetNodeData = {
  kind: 'dataset'
  dataset: MappingDataset
  onPreview: (datasetId: string) => void
}
type TargetNodeData = {
  kind: 'object' | 'relation'
  object?: MappingObjectType
  relation?: MappingLinkType
  sourceProperty?: MappingProperty
  targetProperty?: MappingProperty
}
type MappingNode = Node<DatasetNodeData | TargetNodeData>

const REL_SOURCE = '__relation_source__'
const REL_TARGET = '__relation_target__'

function typeLabel(type?: string) {
  const normalized = normalizeType(type)
  return { string: '文本', number: '数字', datetime: '时间', boolean: '布尔', array: '数组', json: 'JSON' }[normalized] || type || '文本'
}

function targetLaneX() {
  return window.innerWidth < 1400 ? 410 : 650
}

function DatasetCanvasNode({ data }: NodeProps<Node<DatasetNodeData>>) {
  const dataset = data.dataset
  return (
    <div className="dmc-node dmc-node--dataset">
      <div className="dmc-node__stripe" />
      <header><span><Table2 size={15} /></span><div><b>{dataset.name}</b><small>{dataset.sourceLabel} · {dataset.rows ?? 0} 行</small></div><button className="nodrag" onClick={() => data.onPreview(dataset.id)} title="预览数据"><Eye size={13} /></button></header>
      <div className="dmc-node__fields">
        {dataset.columns.map(column => <div key={column.name} className="dmc-node-field">
          {dataset.primaryKeyColumns.includes(column.name) ? <KeyRound size={11} className="is-key" /> : <span className="dmc-field-spacer" />}
          <span title={column.name}>{column.name}</span><em>{typeLabel(column.type)}</em>
          <Handle type="source" position={Position.Right} id={column.name} className="dmc-handle dmc-handle--source" />
        </div>)}
        {dataset.columns.length === 0 && <div className="dmc-node-empty">暂未识别到字段</div>}
      </div>
    </div>
  )
}

function ObjectCanvasNode({ data }: NodeProps<Node<TargetNodeData>>) {
  const object = data.object!
  const properties = object.properties.filter(property => property.source !== 'computed')
  return (
    <div className="dmc-node dmc-node--object">
      <div className="dmc-node__stripe" />
      <header><span><Boxes size={15} /></span><div><b>{object.displayName || object.name}</b><small>对象实体 · {properties.length} 个属性</small></div></header>
      <div className="dmc-node__fields">
        {properties.map(property => <div key={property.id || property.name} className="dmc-node-field">
          <Handle type="target" position={Position.Left} id={property.name} className="dmc-handle dmc-handle--target" />
          {object.primaryKey === property.name ? <KeyRound size={11} className="is-key" /> : <span className="dmc-field-spacer" />}
          <span title={property.displayName || property.name}>{property.displayName || property.name}</span>{property.required && <i>*</i>}<em>{typeLabel(property.type)}</em>
        </div>)}
      </div>
    </div>
  )
}

function RelationCanvasNode({ data }: NodeProps<Node<TargetNodeData>>) {
  const relation = data.relation!
  const properties = (relation.properties || []).filter(property => property.source !== 'computed')
  return (
    <div className="dmc-node dmc-node--relation">
      <div className="dmc-node__stripe" />
      <header><span><GitBranch size={15} /></span><div><b>{relation.displayName || relation.name}</b><small>实体关系 · {relation.cardinality}</small></div></header>
      <div className="dmc-relation-endpoints"><span>源对象</span><ArrowRight size={11} /><span>目标对象</span></div>
      <div className="dmc-node__fields">
        <div className="dmc-node-field dmc-node-field--endpoint"><Handle type="target" position={Position.Left} id={REL_SOURCE} className="dmc-handle dmc-handle--target" /><Link2 size={11} /><span>源对象外键</span><em>{typeLabel(data.sourceProperty?.type)}</em></div>
        <div className="dmc-node-field dmc-node-field--endpoint"><Handle type="target" position={Position.Left} id={REL_TARGET} className="dmc-handle dmc-handle--target" /><Link2 size={11} /><span>目标对象外键</span><em>{typeLabel(data.targetProperty?.type)}</em></div>
        {properties.map(property => <div key={property.id || property.name} className="dmc-node-field"><Handle type="target" position={Position.Left} id={property.name} className="dmc-handle dmc-handle--target" /><span className="dmc-field-spacer" /><span>{property.displayName || property.name}</span><em>{typeLabel(property.type)}</em></div>)}
      </div>
    </div>
  )
}

const nodeTypes = { dataset: DatasetCanvasNode, object: ObjectCanvasNode, relation: RelationCanvasNode }

interface PreviewResponse { columns: string[]; rows: Record<string, unknown>[]; total_rows: number }
interface SaveIssue { title: string; detail: string }
interface DesiredObjectMapping { datasetId: string; object: MappingObjectType; fieldMapping: Record<string, string> }
interface DesiredLinkMapping {
  relation: MappingLinkType
  srcDatasetId: string
  tgtDatasetId: string
  edgeDatasetId: string | null
  srcKey: string
  tgtKey: string
  fieldMapping: Record<string, string>
}

function errorMessage(error: unknown) {
  if (typeof error === 'object' && error !== null) {
    const candidate = error as { detail?: unknown; message?: unknown }
    if (typeof candidate.detail === 'string') return candidate.detail
    if (typeof candidate.detail === 'object' && candidate.detail !== null && 'message' in candidate.detail) return String((candidate.detail as { message: unknown }).message)
    if (typeof candidate.message === 'string') return candidate.message
  }
  return '保存失败，请检查映射配置后重试。'
}

function sameObjectDefinition(existing: ObjectMappingRecord, desired: DesiredObjectMapping) {
  return existing.curated_dataset_id === desired.datasetId
    && mappingTargetId(existing) === desired.object.id
    && JSON.stringify(userFieldMapping(existing)) === JSON.stringify(desired.fieldMapping)
}

function sameLinkDefinition(existing: LinkMappingRecord, desired: DesiredLinkMapping) {
  return existing.src_dataset_id === desired.srcDatasetId
    && existing.tgt_dataset_id === desired.tgtDatasetId
    && existing.edge_dataset_id === desired.edgeDatasetId
    && existing.src_key === desired.srcKey
    && existing.tgt_key === desired.tgtKey
    && JSON.stringify(existing.field_mapping || {}) === JSON.stringify(desired.fieldMapping)
}

function estimatedNodeHeight(node: MappingNode) {
  if (node.measured?.height) return node.measured.height
  if (node.data.kind === 'dataset') return 68 + node.data.dataset.columns.length * 33
  if (node.data.kind === 'relation') return 68 + (2 + (node.data.relation?.properties?.length || 0)) * 33
  return 68 + (node.data.object?.properties.length || 0) * 33
}

function nextLaneY(nodes: MappingNode[], lane: 'dataset' | 'target') {
  return nodes
    .filter(node => lane === 'dataset' ? node.data.kind === 'dataset' : node.data.kind !== 'dataset')
    .reduce((bottom, node) => Math.max(bottom, node.position.y + estimatedNodeHeight(node) + 36), 55)
}

export default function MappingConfigurationPage() {
  const { id: ontologyId = '' } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const versionId = searchParams.get('versionId')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const data = useMappingData(ontologyId, false, versionId)
  const [nodes, setNodes, onNodesChange] = useNodesState<MappingNode>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [leftSearch, setLeftSearch] = useState('')
  const [rightSearch, setRightSearch] = useState('')
  const [rightKind, setRightKind] = useState<'object' | 'relation'>('object')
  const [expandedAssets, setExpandedAssets] = useState<Set<string>>(new Set())
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveIssues, setSaveIssues] = useState<SaveIssue[]>([])
  const [notice, setNotice] = useState<{ tone: 'good' | 'bad' | 'warn'; text: string } | null>(null)
  const [tutorialStep, setTutorialStep] = useState<number | null>(() => localStorage.getItem(`mapping-tutorial:${ontologyId}`) ? null : 0)
  const [tutorialBounds, setTutorialBounds] = useState<{ left: number; top: number; width: number; height: number } | null>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)

  const previewQuery = useQuery<PreviewResponse>({
    queryKey: ['mapping-config-preview', selectedDatasetId],
    enabled: Boolean(selectedDatasetId),
    queryFn: () => apiClientV2.get(`/datasets/${selectedDatasetId}/preview?limit=8`),
  })

  const objectById = useMemo(() => new Map(data.objectTypes.map(item => [item.id, item])), [data.objectTypes])
  const datasetById = useMemo(() => new Map(data.datasets.map(item => [item.id, item])), [data.datasets])

  const targetProperty = useCallback((targetNode: MappingNode, handleId: string | null | undefined): MappingProperty | undefined => {
    if (!handleId || targetNode.data.kind === 'dataset') return undefined
    if (targetNode.data.kind === 'object') return targetNode.data.object?.properties.find(property => property.name === handleId)
    if (handleId === REL_SOURCE) return targetNode.data.sourceProperty
    if (handleId === REL_TARGET) return targetNode.data.targetProperty
    return targetNode.data.relation?.properties?.find(property => property.name === handleId)
  }, [])

  const addDatasetNode = useCallback((dataset: MappingDataset, position?: { x: number; y: number }) => {
    const nodeId = `dataset:${dataset.id}`
    if (nodes.some(node => node.id === nodeId)) return
    setNodes(current => [...current, {
      id: nodeId, type: 'dataset', position: position || { x: 60, y: nextLaneY(current, 'dataset') },
      data: { kind: 'dataset', dataset, onPreview: setSelectedDatasetId },
    }])
    setDirty(true)
  }, [nodes, setNodes])

  const addTargetNode = useCallback((kind: 'object' | 'relation', id: string, position?: { x: number; y: number }) => {
    const nodeId = `${kind}:${id}`
    if (nodes.some(node => node.id === nodeId)) return
    if (kind === 'object') {
      const object = objectById.get(id)
      if (!object) return
      setNodes(current => [...current, { id: nodeId, type: 'object', position: position || { x: targetLaneX(), y: nextLaneY(current, 'target') }, data: { kind: 'object', object } }])
    } else {
      const relation = data.linkTypes.find(item => item.id === id)
      if (!relation) return
      const sourceObject = objectById.get(relation.sourceObjectTypeId)
      const targetObject = objectById.get(relation.targetObjectTypeId)
      setNodes(current => [...current, {
        id: nodeId, type: 'relation', position: position || { x: targetLaneX(), y: nextLaneY(current, 'target') },
        data: {
          kind: 'relation', relation,
          sourceProperty: sourceObject?.properties.find(property => property.name === sourceObject.primaryKey) || sourceObject?.properties[0],
          targetProperty: targetObject?.properties.find(property => property.name === targetObject.primaryKey) || targetObject?.properties[0],
        },
      }])
    }
    setDirty(true)
  }, [data.linkTypes, nodes, objectById, setNodes])

  useEffect(() => {
    if (initialized.current || data.isLoading || data.isLoadingSchemas) return
    initialized.current = true
    const nextNodes: MappingNode[] = []
    const nextEdges: Edge[] = []
    const usedDatasets = new Set<string>()
    const usedObjects = new Set<string>()
    const usedRelations = new Set<string>()

    for (const mapping of data.mappings) {
      const objectId = mappingTargetId(mapping)
      if (!objectId || !mapping.curated_dataset_id) continue
      const dataset = datasetById.get(mapping.curated_dataset_id)
      const object = objectById.get(objectId)
      if (!dataset || !object) continue
      usedDatasets.add(dataset.id); usedObjects.add(object.id)
      for (const [source, target] of Object.entries(userFieldMapping(mapping))) {
        nextEdges.push({ id: `object:${mapping.id}:${source}:${target}`, source: `dataset:${dataset.id}`, target: `object:${object.id}`, sourceHandle: source, targetHandle: target, type: 'smoothstep', animated: false })
      }
    }
    for (const relation of data.linkTypes) {
      const mapping = linkMappingForType(relation, data.linkMappings)
      if (!mapping) continue
      usedRelations.add(relation.id)
      const sourceDatasetId = mapping.edge_dataset_id || mapping.src_dataset_id
      const targetDatasetId = mapping.edge_dataset_id || mapping.tgt_dataset_id
      if (sourceDatasetId) usedDatasets.add(sourceDatasetId)
      if (targetDatasetId) usedDatasets.add(targetDatasetId)
      if (sourceDatasetId) nextEdges.push({ id: `relation:${mapping.id}:source`, source: `dataset:${sourceDatasetId}`, target: `relation:${relation.id}`, sourceHandle: mapping.src_key, targetHandle: REL_SOURCE, type: 'smoothstep' })
      if (targetDatasetId) nextEdges.push({ id: `relation:${mapping.id}:target`, source: `dataset:${targetDatasetId}`, target: `relation:${relation.id}`, sourceHandle: mapping.tgt_key, targetHandle: REL_TARGET, type: 'smoothstep' })
      if (mapping.edge_dataset_id) for (const [property, column] of Object.entries(mapping.field_mapping || {})) {
        nextEdges.push({ id: `relation:${mapping.id}:${property}`, source: `dataset:${mapping.edge_dataset_id}`, target: `relation:${relation.id}`, sourceHandle: column, targetHandle: property, type: 'smoothstep' })
      }
    }

    let datasetY = 55
    ;[...usedDatasets].forEach(datasetId => {
      const dataset = datasetById.get(datasetId)
      if (dataset) {
        const node: MappingNode = { id: `dataset:${dataset.id}`, type: 'dataset', position: { x: 60, y: datasetY }, data: { kind: 'dataset', dataset, onPreview: setSelectedDatasetId } }
        nextNodes.push(node); datasetY += estimatedNodeHeight(node) + 36
      }
    })
    let targetY = 55
    ;[...usedObjects].forEach(objectId => {
      const object = objectById.get(objectId)
      if (object) {
        const node: MappingNode = { id: `object:${object.id}`, type: 'object', position: { x: targetLaneX(), y: targetY }, data: { kind: 'object', object } }
        nextNodes.push(node); targetY += estimatedNodeHeight(node) + 36
      }
    })
    ;[...usedRelations].forEach(relationId => {
      const relation = data.linkTypes.find(item => item.id === relationId)
      if (!relation) return
      const sourceObject = objectById.get(relation.sourceObjectTypeId)
      const targetObject = objectById.get(relation.targetObjectTypeId)
      const node: MappingNode = { id: `relation:${relation.id}`, type: 'relation', position: { x: targetLaneX(), y: targetY }, data: { kind: 'relation', relation, sourceProperty: sourceObject?.properties.find(property => property.name === sourceObject.primaryKey) || sourceObject?.properties[0], targetProperty: targetObject?.properties.find(property => property.name === targetObject.primaryKey) || targetObject?.properties[0] } }
      nextNodes.push(node); targetY += estimatedNodeHeight(node) + 36
    })
    setNodes(nextNodes); setEdges(nextEdges)
  }, [data, datasetById, objectById, ontologyId, setEdges, setNodes])

  useEffect(() => {
    if (!dirty) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warnBeforeUnload)
    return () => window.removeEventListener('beforeunload', warnBeforeUnload)
  }, [dirty])

  useEffect(() => {
    if (tutorialStep === null || !pageRef.current) { setTutorialBounds(null); return }
    const page = pageRef.current
    const updateBounds = () => {
      const rect = page.getBoundingClientRect()
      setTutorialBounds({ left: rect.left, top: rect.top, width: rect.width, height: rect.height })
    }
    updateBounds()
    const observer = new ResizeObserver(updateBounds)
    observer.observe(page)
    window.addEventListener('resize', updateBounds)
    return () => { observer.disconnect(); window.removeEventListener('resize', updateBounds) }
  }, [tutorialStep])

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return
    const sourceNode = nodes.find(node => node.id === connection.source)
    const targetNode = nodes.find(node => node.id === connection.target)
    if (!sourceNode || !targetNode || sourceNode.data.kind !== 'dataset' || targetNode.data.kind === 'dataset') {
      setNotice({ tone: 'warn', text: '请从左侧数据字段连接到右侧本体属性。' }); return
    }
    const sourceColumn = sourceNode.data.dataset.columns.find(column => column.name === connection.sourceHandle)
    const property = targetProperty(targetNode, connection.targetHandle)
    if (!sourceColumn || !property || !typesCompatible(sourceColumn.type, property.type)) {
      setNotice({ tone: 'bad', text: `类型不兼容：${sourceColumn?.name || '源字段'}（${typeLabel(sourceColumn?.type)}）不能连接到 ${property?.displayName || property?.name || '目标属性'}（${typeLabel(property?.type)}）。` }); return
    }
    const duplicateTarget = edges.some(edge => edge.target === connection.target && edge.targetHandle === connection.targetHandle)
    const duplicateSourceInTarget = edges.some(edge => edge.source === connection.source && edge.target === connection.target && edge.sourceHandle === connection.sourceHandle)
    if (duplicateTarget || duplicateSourceInTarget) {
      setNotice({ tone: 'warn', text: duplicateTarget ? '该本体属性已经建立映射，请先删除原连线。' : '同一数据字段不能重复映射到同一个本体元素。' }); return
    }
    setEdges(current => [...current, { ...connection, id: `draft:${Date.now()}:${Math.random().toString(36).slice(2)}`, type: 'smoothstep' } as Edge])
    setDirty(true); setNotice(null)
  }, [edges, nodes, setEdges, targetProperty])

  const invalidEdges = useMemo(() => edges.filter(edge => {
    const sourceNode = nodes.find(node => node.id === edge.source)
    const targetNode = nodes.find(node => node.id === edge.target)
    if (!sourceNode || !targetNode || sourceNode.data.kind !== 'dataset' || targetNode.data.kind === 'dataset') return true
    const sourceColumn = sourceNode.data.dataset.columns.find(column => column.name === edge.sourceHandle)
    return !sourceColumn || !typesCompatible(sourceColumn.type, targetProperty(targetNode, edge.targetHandle)?.type)
  }), [edges, nodes, targetProperty])

  const desiredObjectMappings = useMemo<DesiredObjectMapping[]>(() => {
    const groups = new Map<string, DesiredObjectMapping>()
    for (const edge of edges) {
      if (!edge.source.startsWith('dataset:') || !edge.target.startsWith('object:') || !edge.sourceHandle || !edge.targetHandle) continue
      const datasetId = edge.source.slice('dataset:'.length)
      const objectId = edge.target.slice('object:'.length)
      const object = objectById.get(objectId)
      if (!object) continue
      const key = `${datasetId}:${objectId}`
      const group = groups.get(key) || { datasetId, object, fieldMapping: {} }
      group.fieldMapping[edge.sourceHandle] = edge.targetHandle
      groups.set(key, group)
    }
    return [...groups.values()]
  }, [edges, objectById])

  const desiredLinkMappings = useMemo(() => {
    const desired: DesiredLinkMapping[] = []
    const issues: SaveIssue[] = []
    for (const relationNode of nodes.filter(node => node.data.kind === 'relation')) {
      const relation = (relationNode.data as TargetNodeData).relation!
      const relationEdges = edges.filter(edge => edge.target === relationNode.id)
      if (relationEdges.length === 0) continue
      const sourceEdge = relationEdges.find(edge => edge.targetHandle === REL_SOURCE)
      const targetEdge = relationEdges.find(edge => edge.targetHandle === REL_TARGET)
      if (!sourceEdge?.sourceHandle || !targetEdge?.sourceHandle) {
        issues.push({ title: relation.displayName || relation.name, detail: '实体关系必须同时配置源对象外键和目标对象外键。' }); continue
      }
      const sourceWireDatasetId = sourceEdge.source.slice('dataset:'.length)
      const targetWireDatasetId = targetEdge.source.slice('dataset:'.length)
      const isFat = sourceWireDatasetId === targetWireDatasetId
      const endpointDataset = (objectId: string) => desiredObjectMappings.find(item => item.object.id === objectId)?.datasetId
        || data.mappings.find(item => mappingTargetId(item) === objectId)?.curated_dataset_id || null
      const srcDatasetId = isFat ? endpointDataset(relation.sourceObjectTypeId) : sourceWireDatasetId
      const tgtDatasetId = isFat ? endpointDataset(relation.targetObjectTypeId) : targetWireDatasetId
      if (!srcDatasetId || !tgtDatasetId) {
        issues.push({ title: relation.displayName || relation.name, detail: '请先为关系两端的对象实体配置数据映射。' }); continue
      }
      const fieldMapping: Record<string, string> = {}
      for (const edge of relationEdges) if (edge.targetHandle && edge.sourceHandle && ![REL_SOURCE, REL_TARGET].includes(edge.targetHandle)) fieldMapping[edge.targetHandle] = edge.sourceHandle
      desired.push({ relation, srcDatasetId, tgtDatasetId, edgeDatasetId: isFat ? sourceWireDatasetId : null, srcKey: sourceEdge.sourceHandle, tgtKey: targetEdge.sourceHandle, fieldMapping })
    }
    return { desired, issues }
  }, [data.mappings, desiredObjectMappings, edges, nodes])

  const saveAll = async () => {
    const issues: SaveIssue[] = [...desiredLinkMappings.issues]
    if (invalidEdges.length) issues.push({ title: '字段类型不兼容', detail: `存在 ${invalidEdges.length} 条历史或草稿连线类型不一致，请删除后重新连接。` })
    for (const desired of desiredObjectMappings) {
      const dataset = datasetById.get(desired.datasetId)
      if (!dataset?.primaryKeyColumns.length) issues.push({ title: desired.object.displayName || desired.object.name, detail: `数据集「${dataset?.name || desired.datasetId}」尚未声明资产主键，不能保存对象映射。` })
    }
    if (issues.length) { setSaveIssues(issues); setNotice({ tone: 'bad', text: '当前草稿还有需要处理的问题，尚未写入数据库。' }); return }

    setSaving(true); setSaveIssues([]); setNotice(null)
    try {
      if (versionId) {
        const mappings = desiredObjectMappings.map(desired => {
          const existing = data.mappings.find(item => item.curated_dataset_id === desired.datasetId && mappingTargetId(item) === desired.object.id)
          return {
            id: existing?.id || crypto.randomUUID(),
            curatedDatasetId: desired.datasetId,
            entityClass: desired.object.name,
            targetObjectTypeId: desired.object.id,
            fieldMapping: desired.fieldMapping,
            status: 'draft', confidence: 1,
          }
        })
        const linkMappings = desiredLinkMappings.desired.map(desired => {
          const existing = linkMappingForType(desired.relation, data.linkMappings)
          return {
            id: existing?.id || crypto.randomUUID(),
            srcDatasetId: desired.srcDatasetId, tgtDatasetId: desired.tgtDatasetId,
            edgeDatasetId: desired.edgeDatasetId,
            relationType: desired.relation.name, linkTypeId: desired.relation.id,
            srcKey: desired.srcKey, tgtKey: desired.tgtKey,
            fieldMapping: desired.fieldMapping, status: 'draft',
          }
        })
        await apiClientV2.put(
          `/ontologies/${ontologyId}/versions/${versionId}/workspace/mappings`,
          { baseRevision: data.workspaceRevision, mappings, linkMappings },
        )
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['mappings', ontologyId, versionId] }),
          queryClient.invalidateQueries({ queryKey: ['link-mappings', ontologyId, versionId] }),
          queryClient.invalidateQueries({ queryKey: ['mapping-workspace-meta', ontologyId, versionId] }),
        ])
        setDirty(false)
        setNotice({ tone: 'good', text: `草稿映射已保存：${mappings.length} 个对象映射、${linkMappings.length} 个关系映射。` })
        return
      }
      const desiredLinksByType = new Map(desiredLinkMappings.desired.map(item => [item.relation.id, item]))
      for (const relation of data.linkTypes) {
        const existing = linkMappingForType(relation, data.linkMappings)
        const desired = desiredLinksByType.get(relation.id)
        if (existing && (!desired || !sameLinkDefinition(existing, desired))) await apiClientV2.delete(`/ontologies/${ontologyId}/link-mappings/${existing.id}`)
      }

      for (const existing of data.mappings) {
        const targetId = mappingTargetId(existing)
        if (!targetId) continue
        const desired = desiredObjectMappings.find(item => item.datasetId === existing.curated_dataset_id && item.object.id === targetId)
        if (!desired) await apiClientV2.delete(`/ontologies/${ontologyId}/mappings/${existing.id}`)
      }
      for (const desired of desiredObjectMappings) {
        const existing = data.mappings.find(item => item.curated_dataset_id === desired.datasetId && mappingTargetId(item) === desired.object.id)
        if (existing) {
          if (!sameObjectDefinition(existing, desired)) await apiClientV2.put(`/ontologies/${ontologyId}/mappings/${existing.id}`, { entity_class: desired.object.name, target_object_type_id: desired.object.id, field_mapping: desired.fieldMapping, ignored_fields: [] })
        } else {
          await apiClientV2.post(`/ontologies/${ontologyId}/mappings`, { curated_dataset_id: desired.datasetId, entity_class: desired.object.name, target_object_type_id: desired.object.id, field_mapping: desired.fieldMapping, ignored_fields: [], confidence: 1 })
        }
      }
      for (const desired of desiredLinkMappings.desired) {
        const existing = linkMappingForType(desired.relation, data.linkMappings)
        if (!existing || !sameLinkDefinition(existing, desired)) await apiClientV2.post(`/ontologies/${ontologyId}/link-mappings`, { src_dataset_id: desired.srcDatasetId, tgt_dataset_id: desired.tgtDatasetId, edge_dataset_id: desired.edgeDatasetId, relation_type: desired.relation.name, link_type_id: desired.relation.id, src_key: desired.srcKey, tgt_key: desired.tgtKey, field_mapping: desired.fieldMapping })
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['mappings', ontologyId] }),
        queryClient.invalidateQueries({ queryKey: ['link-mappings', ontologyId] }),
      ])
      setDirty(false); setNotice({ tone: 'good', text: `配置已保存：${desiredObjectMappings.length} 个对象映射、${desiredLinkMappings.desired.length} 个关系映射已同步到数据库。` })
    } catch (error) {
      setNotice({ tone: 'bad', text: errorMessage(error) })
    } finally { setSaving(false) }
  }

  const autoLayout = () => {
    setNodes(current => {
      let leftY = 55; let rightY = 55
      return current.map(node => {
        const datasetLane = node.data.kind === 'dataset'
        const y = datasetLane ? leftY : rightY
        if (datasetLane) leftY += estimatedNodeHeight(node) + 36
        else rightY += estimatedNodeHeight(node) + 36
        return { ...node, position: { x: datasetLane ? 60 : targetLaneX(), y } }
      })
    })
  }
  const clearCanvas = () => { if (nodes.length && window.confirm('清空画布会把现有映射标记为待删除，只有点击“保存配置”后才会同步数据库。')) { setNodes([]); setEdges([]); setDirty(true); setSelectedDatasetId(null) } }
  const leaveWorkspace = () => {
    if (dirty && !window.confirm('当前还有未保存的映射更改，离开后这些前端草稿会丢失。确定离开吗？')) return
    navigate(versionId
      ? `/ontologies/${ontologyId}?tab=versions`
      : `/ontologies/${ontologyId}?tab=data-mapping`)
  }
  const closeTutorial = () => { localStorage.setItem(`mapping-tutorial:${ontologyId}`, 'seen'); setTutorialStep(null) }
  const tutorial = [
    { icon: Database, title: '从左侧选择数据资产', text: '这里仅展示数据资产湖中已启用的成品数据集与人工数据集。点击“+”把需要配置的数据集放到画布。' },
    { icon: Boxes, title: '从右侧选择本体元素', text: '对象实体与实体关系会分别标识配置进度。把需要维护的元素加入画布即可查看全部属性。' },
    { icon: Link2, title: '拖动端点建立字段连线', text: '从数据字段右侧圆点拖到本体属性左侧圆点。文本、数字、时间、布尔、数组等类型必须兼容。' },
    { icon: Eye, title: '预览数据，最后统一保存', text: '点击数据集的眼睛可在底部核对实例。所有操作先保存在当前前端草稿，只有右上角“保存配置”才会写入数据库。' },
  ]

  if (data.isLoading) return <div className="dmc-page-loading"><Loader2 className="animate-spin" />正在加载映射工作台…</div>
  if (data.isError) return <div className="dmc-page-loading dmc-page-loading--error"><AlertCircle />映射工作台加载失败，请返回后重试。</div>

  const filteredDatasets = data.datasets.filter(item => item.name.toLowerCase().includes(leftSearch.toLowerCase()))
  const filteredTargets = (rightKind === 'object' ? data.objectTypes : data.linkTypes).filter(item => `${item.displayName} ${item.name}`.toLowerCase().includes(rightSearch.toLowerCase()))
  const mappedTargetHandles = new Set(edges.map(edge => `${edge.target}:${edge.targetHandle}`))

  return (
    <div className="dmc-page" ref={pageRef}>
      <header className="dmc-header">
        <div className="dmc-brand"><button onClick={leaveWorkspace} aria-label="返回数据映射"><ArrowLeft size={16} /></button><span><Link2 size={18} /></span><div><b>数据映射配置</b><small>对象实体、实体关系与数据资产字段映射</small></div></div>
        <label className="dmc-global-search"><Search size={14} /><input placeholder="搜索画布节点、数据集或本体属性…" onChange={event => { setLeftSearch(event.target.value); setRightSearch(event.target.value) }} /></label>
        <div className="dmc-header-actions"><button onClick={() => setTutorialStep(0)} title="新手教程"><BookOpen size={15} /></button><button onClick={autoLayout} title="自动布局"><LayoutGrid size={15} /></button><button onClick={clearCanvas} title="清空画布"><Trash2 size={15} /></button><span className="dmc-divider" /><button className="dmc-save" disabled={!dirty || saving} onClick={saveAll}>{saving ? <Loader2 className="animate-spin" size={15} /> : <Save size={15} />}{saving ? '正在保存…' : dirty ? '保存配置' : '已保存'}</button></div>
      </header>

      {notice && <div className={`dmc-notice dmc-notice--${notice.tone}`}>{notice.tone === 'good' ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}<span>{notice.text}</span><button onClick={() => setNotice(null)}><X size={13} /></button></div>}
      {saveIssues.length > 0 && <div className="dmc-issues"><div><AlertCircle size={15} /><b>保存前请处理以下问题</b><button onClick={() => setSaveIssues([])}><X size={13} /></button></div>{saveIssues.map((issue, index) => <p key={`${issue.title}:${index}`}><strong>{issue.title}</strong><span>{issue.detail}</span></p>)}</div>}

      <div className="dmc-workbench">
        <aside className="dmc-sidebar dmc-sidebar--left">
          <div className="dmc-sidebar-title"><Database size={15} /><div><b>数据资产湖</b><small>已启用数据集</small></div><em>{data.datasets.length}</em></div>
          <label className="dmc-side-search"><Search size={13} /><input value={leftSearch} onChange={event => setLeftSearch(event.target.value)} placeholder="搜索数据集或字段" /></label>
          <div className="dmc-side-list">
            {filteredDatasets.map(dataset => {
              const expanded = expandedAssets.has(dataset.id)
              const added = nodes.some(node => node.id === `dataset:${dataset.id}`)
              return <div className="dmc-asset" key={dataset.id}><div className="dmc-asset-main"><button onClick={() => setExpandedAssets(current => { const next = new Set(current); if (expanded) next.delete(dataset.id); else next.add(dataset.id); return next })}>{expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button><span className={`dmc-asset-icon dmc-asset-icon--${dataset.source}`}><Table2 size={13} /></span><span><b>{dataset.name}</b><small>{dataset.sourceLabel} · {dataset.rows ?? 0} 行 · {dataset.columns.length} 字段</small></span><button className="dmc-eye" onClick={() => setSelectedDatasetId(dataset.id)} title="预览数据"><Eye size={12} /></button><button className="dmc-add" disabled={added} onClick={() => addDatasetNode(dataset)}>{added ? <Check size={12} /> : <Plus size={12} />}</button></div>{expanded && <div className="dmc-asset-columns">{dataset.columns.map(column => <span key={column.name}>{dataset.primaryKeyColumns.includes(column.name) ? <KeyRound size={9} /> : <i />}<b>{column.name}</b><em>{typeLabel(column.type)}</em></span>)}</div>}</div>
            })}
          </div>
          <div className="dmc-sidebar-foot"><span><Database size={11} />成品 {data.datasets.filter(item => item.source === 'curated').length}</span><span><Table2 size={11} />人工 {data.datasets.filter(item => item.source === 'manual').length}</span></div>
        </aside>

        <main className="dmc-canvas-wrap">
          <ReactFlow<MappingNode, Edge>
            nodes={nodes} edges={edges} nodeTypes={nodeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
            onEdgesDelete={() => setDirty(true)} onNodesDelete={() => setDirty(true)}
            fitView fitViewOptions={{ padding: .18 }} minZoom={.3} maxZoom={1.5}
            deleteKeyCode={['Backspace', 'Delete']} connectionLineStyle={{ stroke: '#109486', strokeWidth: 2 }}
          >
            <Background gap={18} size={1} color="#dce3e7" />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap position="bottom-right" nodeColor={node => node.type === 'dataset' ? '#19a393' : node.type === 'relation' ? '#d99a32' : '#6570c8'} maskColor="rgba(248,250,251,.75)" />
            <div className="dmc-canvas-stats">节点 <b>{nodes.length}</b><span />字段映射 <b>{edges.length}</b><span />{dirty ? <em>有未保存更改</em> : <i>已与数据库同步</i>}</div>
          </ReactFlow>

          {selectedDatasetId && <section className="dmc-preview-panel"><header><div><Eye size={14} /><span><b>{datasetById.get(selectedDatasetId)?.name}</b><small>实例数据预览 · 仅用于映射核对</small></span></div><button onClick={() => setSelectedDatasetId(null)}><X size={14} /></button></header>{previewQuery.isLoading ? <div className="dmc-preview-loading"><Loader2 className="animate-spin" />正在读取数据…</div> : previewQuery.data?.columns?.length ? <div className="dmc-preview-table"><table><thead><tr>{previewQuery.data.columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{previewQuery.data.rows.map((row, index) => <tr key={index}>{previewQuery.data!.columns.map(column => <td key={column} title={String(row[column] ?? '')}>{row[column] == null || row[column] === '' ? '—' : typeof row[column] === 'object' ? JSON.stringify(row[column]) : String(row[column])}</td>)}</tr>)}</tbody></table><p>显示 {previewQuery.data.rows.length} 行 · 共 {previewQuery.data.total_rows?.toLocaleString() || 0} 行</p></div> : <div className="dmc-preview-loading"><AlertCircle />当前数据集暂无可预览数据</div>}</section>}
        </main>

        <aside className="dmc-sidebar dmc-sidebar--right">
          <div className="dmc-sidebar-title"><Boxes size={15} /><div><b>本体清单</b><small>对象实体与实体关系</small></div><em>{data.objectTypes.length + data.linkTypes.length}</em></div>
          <div className="dmc-kind-tabs"><button data-active={rightKind === 'object'} onClick={() => setRightKind('object')}>对象实体 <span>{data.objectTypes.length}</span></button><button data-active={rightKind === 'relation'} onClick={() => setRightKind('relation')}>实体关系 <span>{data.linkTypes.length}</span></button></div>
          <label className="dmc-side-search"><Search size={13} /><input value={rightSearch} onChange={event => setRightSearch(event.target.value)} placeholder="搜索本体元素或属性" /></label>
          <div className="dmc-side-list">
            {filteredTargets.map(target => {
              const nodeId = `${rightKind}:${target.id}`
              const added = nodes.some(node => node.id === nodeId)
              const properties = rightKind === 'object' ? (target as MappingObjectType).properties.filter(item => item.source !== 'computed') : (target as MappingLinkType).properties || []
              const total = properties.length + (rightKind === 'relation' ? 2 : 0)
              const mapped = [...mappedTargetHandles].filter(key => key.startsWith(`${nodeId}:`)).length
              return <div className="dmc-target-item" key={target.id}><span className={`dmc-target-icon dmc-target-icon--${rightKind}`}>{rightKind === 'object' ? <Boxes size={14} /> : <GitBranch size={14} />}</span><span><b>{target.displayName || target.name}</b><small>{rightKind === 'object' ? '对象实体' : '实体关系'} · {mapped}/{total} 已映射</small></span><em data-complete={total > 0 && mapped === total} data-partial={mapped > 0 && mapped < total}>{mapped === 0 ? '未配置' : mapped === total ? '已完成' : '配置中'}</em><button disabled={added} onClick={() => addTargetNode(rightKind, target.id)}>{added ? <Check size={12} /> : <Plus size={12} />}</button></div>
            })}
          </div>
          <div className="dmc-unmapped-summary"><AlertCircle size={13} /><span><b>{(rightKind === 'object' ? data.objectTypes : data.linkTypes).filter(target => !nodes.some(node => node.id === `${rightKind}:${target.id}`) && (rightKind === 'object' ? !data.mappings.some(mapping => mappingTargetId(mapping) === target.id) : !linkMappingForType(target as MappingLinkType, data.linkMappings))).length} 个尚未配置</b><small>加入画布后可建立字段映射</small></span></div>
        </aside>
      </div>

      {tutorialStep !== null && <div className="dmc-tutorial" style={tutorialBounds || undefined} role="dialog" aria-modal="true"><div className="dmc-tutorial-card"><header><div><span><BookOpen size={15} /></span><div><b>数据映射快速入门</b><small>第 {tutorialStep + 1} 步，共 {tutorial.length} 步</small></div></div><button onClick={closeTutorial}><X size={15} /></button></header><main>{(() => { const StepIcon = tutorial[tutorialStep].icon; return <><span><StepIcon size={27} /></span><h3>{tutorial[tutorialStep].title}</h3><p>{tutorial[tutorialStep].text}</p></> })()}</main><footer><div>{tutorial.map((_, index) => <button key={index} data-active={index === tutorialStep} onClick={() => setTutorialStep(index)} />)}</div><span>{tutorialStep > 0 && <button onClick={() => setTutorialStep(step => (step || 1) - 1)}>上一步</button>}<button className="dmc-tutorial-next" onClick={() => tutorialStep === tutorial.length - 1 ? closeTutorial() : setTutorialStep(step => (step || 0) + 1)}>{tutorialStep === tutorial.length - 1 ? '开始配置' : '下一步'}<ArrowRight size={13} /></button></span></footer></div></div>}
    </div>
  )
}
