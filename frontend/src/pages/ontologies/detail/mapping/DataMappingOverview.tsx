import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Background, Controls, Handle, MarkerType, Position, ReactFlow,
  type Edge, type Node, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  AlertCircle, Boxes, ChevronLeft, ChevronRight, CircleDot, Database, Eye,
  GitBranch, Layers3, Loader2, Settings2, Table2, X,
} from 'lucide-react'
import curatedApi from '@/api/v2/curated'
import datasetsApi from '@/api/v2/datasets'
import {
  linkMappingForType, mappingTargetId, userFieldMapping, useMappingData,
  type MappingDataset, type MappingLinkType, type MappingObjectType,
} from './mapping-data'
import './mapping-overview.css'

type TargetSelection = { kind: 'object'; id: string } | { kind: 'relation'; id: string }

function PercentRing({ value, tone = 'teal' }: { value: number; tone?: 'teal' | 'indigo' | 'amber' }) {
  return (
    <div className={`dmo-ring dmo-ring--${tone}`} style={{ '--dmo-progress': `${value * 3.6}deg` } as React.CSSProperties}>
      <strong>{value}%</strong>
    </div>
  )
}

function MiniDonut({ value, label, tone }: { value: number; label: string; tone: 'teal' | 'indigo' | 'amber' }) {
  return (
    <div className="dmo-analysis-donut">
      <PercentRing value={value} tone={tone} />
      <div><strong>{label}</strong><span>{value === 100 ? '已全部完成' : `仍有 ${100 - value}% 待完善`}</span></div>
    </div>
  )
}

type OverviewFlowNodeData = {
  kind: 'source' | 'target'
  title: string
  subtitle: string
  targetKind?: TargetSelection['kind']
}
type OverviewFlowNode = Node<OverviewFlowNodeData, 'overview'>

function OverviewFlowNodeCard({ data }: NodeProps<OverviewFlowNode>) {
  const source = data.kind === 'source'
  return (
    <div className={`dmo-flow-node ${source ? 'dmo-flow-node--source' : 'dmo-flow-node--target'}`}>
      {!source && <Handle type="target" position={Position.Left} className="dmo-flow-handle dmo-flow-handle--target" />}
      {source ? <Database size={16} /> : data.targetKind === 'object' ? <Boxes size={16} /> : <GitBranch size={16} />}
      <span><b>{data.title}</b><small>{data.subtitle}</small></span>
      {source && <Handle type="source" position={Position.Right} className="dmo-flow-handle dmo-flow-handle--source" />}
    </div>
  )
}

const OVERVIEW_NODE_TYPES = { overview: OverviewFlowNodeCard }
const PREVIEW_PAGE_SIZES = [10, 20, 50]

function displayPreviewValue(value: unknown) {
  if (value == null || value === '') return '—'
  if (typeof value === 'object') {
    try { return JSON.stringify(value) }
    catch { return String(value) }
  }
  return String(value)
}

function previewErrorMessage(error: unknown) {
  const value = error as { detail?: unknown; message?: string }
  if (typeof value.detail === 'string') return value.detail
  if (value.detail && typeof value.detail === 'object' && 'message' in value.detail && typeof value.detail.message === 'string') return value.detail.message
  return value.message || '数据预览加载失败，请稍后重试'
}

function DatasetPreviewDialog({ dataset, onClose }: { dataset: MappingDataset; onClose: () => void }) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const offset = (page - 1) * pageSize
  const previewQuery = useQuery({
    queryKey: ['mapping-dataset-preview', dataset.source, dataset.id, page, pageSize],
    queryFn: async () => {
      if (dataset.source === 'curated') {
        const result = await curatedApi.preview(dataset.id, pageSize, offset)
        return {
          columns: result.columns?.length ? result.columns : dataset.columns.map(column => column.name),
          rows: result.rows || [],
          totalRows: result.total_rows ?? dataset.rows ?? result.count ?? 0,
        }
      }
      const result = await datasetsApi.previewLatest(dataset.id, pageSize, offset)
      return {
        columns: result.columns?.length ? result.columns : dataset.columns.map(column => column.name),
        rows: result.rows || [],
        totalRows: result.total_rows ?? dataset.rows ?? 0,
      }
    },
  })

  const columns = previewQuery.data?.columns || []
  const rows = previewQuery.data?.rows || []
  const totalRows = previewQuery.data?.totalRows ?? dataset.rows ?? 0
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize))
  const rangeStart = totalRows ? offset + 1 : 0
  const rangeEnd = Math.min(offset + rows.length, totalRows)
  const compactColumns = columns.length <= 4
  const columnMeta = useMemo(() => new Map(dataset.columns.map(column => [column.name, column])), [dataset.columns])

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  return createPortal(
    <div className="dmo-preview-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <section className="dmo-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="dmo-preview-title">
        <header className="dmo-preview-header">
          <span className={`dmo-preview-dataset-icon dmo-source-icon--${dataset.source}`}><Table2 size={16} /></span>
          <div className="dmo-preview-heading">
            <div><h3 id="dmo-preview-title">{dataset.name}</h3><em>{totalRows.toLocaleString()} 行</em></div>
            <p>{dataset.sourceLabel} · 分页数据预览</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭数据预览" className="dmo-preview-close"><X size={17} /></button>
        </header>

        <div className="dmo-preview-body">
          {previewQuery.isLoading ? (
            <div className="dmo-preview-state"><Loader2 size={18} className="animate-spin" /><span>正在读取数据…</span></div>
          ) : previewQuery.isError ? (
            <div className="dmo-preview-state dmo-preview-state--error"><AlertCircle size={19} /><span>{previewErrorMessage(previewQuery.error)}</span><button type="button" onClick={() => void previewQuery.refetch()}>重新加载</button></div>
          ) : columns.length === 0 || rows.length === 0 ? (
            <div className="dmo-preview-state"><Database size={22} /><span>当前数据集暂无可预览数据</span></div>
          ) : (
            <div className="dmo-preview-table-scroll">
              <table className={`dmo-preview-table ${compactColumns ? 'is-fluid' : 'is-scrollable'}`}>
                <thead><tr><th className="dmo-preview-row-index">#</th>{columns.map(column => {
                  const meta = columnMeta.get(column)
                  return <th key={column} title={column}><span>{meta?.display_name || column}</span>{meta?.display_name && meta.display_name !== column && <small>{column}</small>}</th>
                })}</tr></thead>
                <tbody>{rows.map((row, rowIndex) => (
                  <tr key={`${offset + rowIndex}:${columns.map(column => displayPreviewValue(row[column])).join('|')}`}>
                    <td className="dmo-preview-row-index">{offset + rowIndex + 1}</td>
                    {columns.map(column => {
                      const value = displayPreviewValue(row[column])
                      return <td key={column} title={value}>{value}</td>
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {previewQuery.isFetching && !previewQuery.isLoading && <span className="dmo-preview-fetching"><Loader2 size={11} className="animate-spin" />正在切换页面</span>}
        </div>

        <footer className="dmo-preview-footer">
          <label>每页<select value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1) }} aria-label="数据预览每页显示条数">{PREVIEW_PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}</select>行</label>
          <span className="dmo-preview-range">{rangeStart}–{rangeEnd} / {totalRows.toLocaleString()} 行</span>
          <div className="dmo-preview-pagination">
            <button type="button" onClick={() => setPage(current => Math.max(1, current - 1))} disabled={page <= 1 || previewQuery.isFetching} aria-label="上一页"><ChevronLeft size={14} /></button>
            <span>第 {page} / {totalPages} 页</span>
            <button type="button" onClick={() => setPage(current => Math.min(totalPages, current + 1))} disabled={page >= totalPages || previewQuery.isFetching} aria-label="下一页"><ChevronRight size={14} /></button>
          </div>
        </footer>
      </section>
    </div>,
    document.body,
  )
}

function MappingOverviewFlow({
  datasets, targetKind, targetName, mappedFieldCount,
}: {
  datasets: MappingDataset[]
  targetKind: TargetSelection['kind']
  targetName: string
  mappedFieldCount: number
}) {
  const nodes = useMemo<OverviewFlowNode[]>(() => {
    const sourceGap = 92
    const sourceSpan = (datasets.length - 1) * sourceGap
    return [
      ...datasets.map((dataset, index): OverviewFlowNode => ({
        id: `source:${dataset.id}`,
        type: 'overview',
        position: { x: 24, y: index * sourceGap },
        data: {
          kind: 'source',
          title: dataset.name,
          subtitle: `${dataset.columns.length} 个字段 · ${dataset.rows || 0} 行`,
        },
      })),
      {
        id: `target:${targetKind}:${targetName}`,
        type: 'overview',
        position: { x: 390, y: sourceSpan / 2 },
        data: {
          kind: 'target',
          title: targetName,
          subtitle: `${targetKind === 'object' ? '对象实体' : '实体关系'} · ${mappedFieldCount} 个字段映射`,
          targetKind,
        },
      },
    ]
  }, [datasets, mappedFieldCount, targetKind, targetName])

  const edges = useMemo<Edge[]>(() => datasets.map(dataset => ({
    id: `mapping:${dataset.id}:${targetKind}:${targetName}`,
    source: `source:${dataset.id}`,
    target: `target:${targetKind}:${targetName}`,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#6572c5', width: 16, height: 16 },
    style: { stroke: '#4f91ad', strokeWidth: 1.8 },
  })), [datasets, targetKind, targetName])

  return (
    <div className="dmo-flow-canvas" role="region" aria-label="可缩放和平移的映射画布" data-testid="mapping-overview-canvas">
      <ReactFlow<OverviewFlowNode, Edge>
        nodes={nodes}
        edges={edges}
        nodeTypes={OVERVIEW_NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.28, maxZoom: 1.15 }}
        minZoom={0.35}
        maxZoom={1.8}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll
        zoomOnDoubleClick
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} size={1} color="#d9e0e4" />
        <Controls position="bottom-right" showInteractive={false} />
        <div className="dmo-canvas-help nodrag nopan">滚轮缩放 · 拖动画布</div>
      </ReactFlow>
    </div>
  )
}

export default function DataMappingOverview({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const data = useMappingData(ontologyId, true)
  const [selected, setSelected] = useState<TargetSelection | null>(null)
  const [datasetSearch, setDatasetSearch] = useState('')
  const [targetKind, setTargetKind] = useState<'object' | 'relation'>('object')
  const [previewDataset, setPreviewDataset] = useState<MappingDataset | null>(null)

  const objectMapping = (id: string) => data.mappings.find(mapping => mappingTargetId(mapping) === id)
  const selectedObject = selected?.kind === 'object' ? data.objectTypes.find(item => item.id === selected.id) : undefined
  const selectedRelation = selected?.kind === 'relation' ? data.linkTypes.find(item => item.id === selected.id) : undefined
  const selectedObjectMapping = selectedObject ? objectMapping(selectedObject.id) : undefined
  const selectedLinkMapping = selectedRelation ? linkMappingForType(selectedRelation, data.linkMappings) : undefined
  const selectedMappingExists = Boolean(selectedObjectMapping || selectedLinkMapping)
  const selectedDatasetIds = selectedObjectMapping?.curated_dataset_id
    ? [selectedObjectMapping.curated_dataset_id]
    : selectedLinkMapping
      ? [selectedLinkMapping.edge_dataset_id, selectedLinkMapping.src_dataset_id, selectedLinkMapping.tgt_dataset_id].filter(Boolean) as string[]
      : []
  const selectedDatasets = data.datasets.filter(dataset => selectedDatasetIds.includes(dataset.id))

  const objectInstanceCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const instance of data.objectInstances) counts.set(instance.objectTypeId, (counts.get(instance.objectTypeId) || 0) + 1)
    return counts
  }, [data.objectInstances])
  const linkInstanceCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const instance of data.linkInstances) counts.set(instance.linkTypeId, (counts.get(instance.linkTypeId) || 0) + 1)
    return counts
  }, [data.linkInstances])

  const mappedObjects = data.objectTypes.filter(item => objectMapping(item.id)).length
  const mappedRelations = data.linkTypes.filter(item => linkMappingForType(item, data.linkMappings)).length
  const objectCoverage = data.objectTypes.length ? Math.round(mappedObjects / data.objectTypes.length * 100) : 0
  const relationCoverage = data.linkTypes.length ? Math.round(mappedRelations / data.linkTypes.length * 100) : 0
  const totalTargetFields = data.objectTypes.reduce((sum, item) => sum + item.properties.filter(prop => prop.source !== 'computed').length, 0)
    + data.linkTypes.reduce((sum, item) => sum + (item.properties || []).filter(prop => prop.source !== 'computed').length + 2, 0)
  const mappedFields = data.mappings.reduce((sum, mapping) => sum + Object.keys(userFieldMapping(mapping)).length, 0)
    + data.linkMappings.reduce((sum, mapping) => sum + Object.keys(mapping.field_mapping || {}).length + 2, 0)
  const fieldCoverage = totalTargetFields ? Math.min(100, Math.round(mappedFields / totalTargetFields * 100)) : 0
  const objectWithData = data.objectTypes.filter(item => (objectInstanceCounts.get(item.id) || 0) > 0).length
  const relationWithData = data.linkTypes.filter(item => (linkInstanceCounts.get(item.id) || 0) > 0).length
  const objectCollection = data.objectTypes.length ? Math.round(objectWithData / data.objectTypes.length * 100) : 0
  const relationCollection = data.linkTypes.length ? Math.round(relationWithData / data.linkTypes.length * 100) : 0
  const averageQuality = data.datasets.filter(item => item.quality != null).length
    ? Math.round(data.datasets.filter(item => item.quality != null).reduce((sum, item) => {
      const quality = Number(item.quality)
      return sum + (quality <= 1 ? quality * 100 : quality)
    }, 0) / data.datasets.filter(item => item.quality != null).length)
    : fieldCoverage

  if (data.isLoading) {
    return <div className="dmo-loading"><Loader2 className="animate-spin" size={20} />正在整理映射状态…</div>
  }
  if (data.isError) {
    return <div className="dmo-loading dmo-loading--error"><AlertCircle size={20} />映射状态加载失败，请稍后重试。</div>
  }

  const targetItems: Array<MappingObjectType | MappingLinkType> = targetKind === 'object' ? data.objectTypes : data.linkTypes
  const filteredDatasets = data.datasets.filter(item => item.name.toLowerCase().includes(datasetSearch.toLowerCase()))
  const displayTarget = (item: MappingObjectType | MappingLinkType) => item.displayName || item.name

  return (
    <section className="dmo-card">
      <header className="dmo-hero">
        <div>
          <div className="dmo-eyebrow"><Layers3 size={13} /> DATA MAPPING</div>
          <h2>把本体结构，接到真实数据上</h2>
          <p>查看当前最新发布快照中的映射覆盖、字段配置与数据采集结果；所有变更只能在草稿工作台中维护。</p>
        </div>
        <button className="dmo-primary-button" onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}>
          <Settings2 size={15} />在草稿中配置映射
        </button>
      </header>

      <div className="dmo-workspace">
        <aside className="dmo-pane dmo-sources">
          <div className="dmo-pane__head">
            <div><Database size={15} /><span><b>现有数据源</b><small>已启用的数据资产湖</small></span></div>
            <em>{data.datasets.length}</em>
          </div>
          <label className="dmo-search"><span>⌕</span><input value={datasetSearch} onChange={event => setDatasetSearch(event.target.value)} placeholder="搜索数据集" /></label>
          <div className="dmo-source-list">
            {filteredDatasets.map(dataset => {
              const mappingCount = data.mappings.filter(mapping => mapping.curated_dataset_id === dataset.id).length
                + data.linkMappings.filter(mapping => [mapping.src_dataset_id, mapping.tgt_dataset_id, mapping.edge_dataset_id].includes(dataset.id)).length
              return (
                <div className="dmo-source-item" key={dataset.id}>
                  <span className={`dmo-source-icon dmo-source-icon--${dataset.source}`}><Table2 size={14} /></span>
                  <span><b>{dataset.name}</b><small>{dataset.sourceLabel} · {dataset.rows == null ? '暂无行数' : `${dataset.rows.toLocaleString()} 行`}</small></span>
                  <em data-active={mappingCount > 0}>{mappingCount ? `${mappingCount} 个映射` : '未使用'}</em>
                  <button type="button" className="dmo-source-preview" onClick={() => setPreviewDataset(dataset)} aria-label={`预览数据源 ${dataset.name}`} title="分页预览数据"><Eye size={13} /></button>
                </div>
              )
            })}
            {filteredDatasets.length === 0 && <p className="dmo-list-empty">没有匹配的数据集</p>}
          </div>
        </aside>

        <main className="dmo-pane dmo-canvas">
          <div className="dmo-pane__head">
            <div><CircleDot size={15} /><span><b>映射画布</b><small>选择本体元素查看当前映射</small></span></div>
            <div className="dmo-segmented">
              <button data-active={targetKind === 'object'} onClick={() => { setTargetKind('object'); setSelected(null) }}>对象实体</button>
              <button data-active={targetKind === 'relation'} onClick={() => { setTargetKind('relation'); setSelected(null) }}>实体关系</button>
            </div>
          </div>
          <div className="dmo-target-strip">
            {targetItems.map(item => {
              const mapped = targetKind === 'object'
                ? Boolean(objectMapping(item.id))
                : Boolean(linkMappingForType(item as MappingLinkType, data.linkMappings))
              const isSelected = selected?.kind === targetKind && selected.id === item.id
              return (
                <button key={item.id} data-selected={isSelected} onClick={() => setSelected({ kind: targetKind, id: item.id })}>
                  {targetKind === 'object' ? <Boxes size={12} /> : <GitBranch size={12} />}
                  <span>{displayTarget(item)}</span><i data-mapped={mapped}>{mapped ? '已映射' : '未映射'}</i>
                </button>
              )
            })}
          </div>
          <div className={`dmo-map-stage ${selectedMappingExists && selectedDatasets.length > 0 ? 'is-interactive' : ''}`}>
            {!selected ? (
              <div className="dmo-canvas-empty"><Layers3 size={28} /><b>选择一个对象实体或实体关系</b><span>画布将展示当前已建立的数据映射关系</span></div>
            ) : !selectedMappingExists ? (
              <div className="dmo-canvas-empty dmo-canvas-empty--warning"><AlertCircle size={28} /><b>尚未建立映射</b><span>先创建草稿，再将数据字段连接到草稿本体属性</span><button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}>创建草稿</button></div>
            ) : selectedDatasets.length === 0 ? (
              <div className="dmo-canvas-empty dmo-canvas-empty--warning"><AlertCircle size={28} /><b>发布映射的数据源当前不可见</b><span>映射定义仍保留在当前发布快照中，请检查对应数据资产的可用状态</span></div>
            ) : (
              <MappingOverviewFlow
                key={`${selected.kind}:${selected.id}`}
                datasets={selectedDatasets}
                targetKind={selected.kind}
                targetName={selectedObject?.displayName || selectedObject?.name || selectedRelation?.displayName || selectedRelation?.name || selected.id}
                mappedFieldCount={selected.kind === 'object' ? Object.keys(userFieldMapping(selectedObjectMapping)).length : Object.keys(selectedLinkMapping?.field_mapping || {}).length + 2}
              />
            )}
          </div>
        </main>

        <aside className="dmo-pane dmo-detail">
          <div className="dmo-pane__head"><div><Settings2 size={15} /><span><b>映射详情</b><small>映射覆盖与采集状态</small></span></div></div>
          <div className="dmo-detail-charts" aria-label="映射与采集状态">
            <div className="dmo-analysis-card"><div className="dmo-analysis-card__title"><span><CircleDot size={14} />映射质量分析</span><em>{averageQuality >= 80 ? '良好' : '待完善'}</em></div><MiniDonut value={fieldCoverage} label="字段覆盖率" tone="teal" /><div className="dmo-legend"><span><i className="teal" />已映射 {mappedFields}</span><span><i />未映射 {Math.max(0, totalTargetFields - mappedFields)}</span></div></div>
            <div className="dmo-analysis-card"><div className="dmo-analysis-card__title"><span><Boxes size={14} />对象实体采集状态</span><em>{objectWithData}/{data.objectTypes.length} 有数据</em></div><MiniDonut value={objectCollection} label="已有实例数据" tone="indigo" /><div className="dmo-status-bars"><span><b>映射覆盖</b><i><em style={{ width: `${objectCoverage}%` }} /></i><strong>{objectCoverage}%</strong></span><span><b>数据到达</b><i><em style={{ width: `${objectCollection}%` }} /></i><strong>{objectCollection}%</strong></span></div></div>
            <div className="dmo-analysis-card"><div className="dmo-analysis-card__title"><span><GitBranch size={14} />实体关系采集状态</span><em>{relationWithData}/{data.linkTypes.length} 有数据</em></div><MiniDonut value={relationCollection} label="已有关系数据" tone="amber" /><div className="dmo-status-bars dmo-status-bars--amber"><span><b>映射覆盖</b><i><em style={{ width: `${relationCoverage}%` }} /></i><strong>{relationCoverage}%</strong></span><span><b>数据到达</b><i><em style={{ width: `${relationCollection}%` }} /></i><strong>{relationCollection}%</strong></span></div></div>
          </div>
        </aside>
      </div>
      {previewDataset && <DatasetPreviewDialog key={previewDataset.id} dataset={previewDataset} onClose={() => setPreviewDataset(null)} />}
    </section>
  )
}
