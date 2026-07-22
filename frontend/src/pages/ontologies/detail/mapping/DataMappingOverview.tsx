import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle, AlertTriangle, ArrowRight, Boxes, CheckCircle2, ChevronLeft,
  ChevronRight, Database, Eye, GitBranch, Link2, Loader2, Search, Settings2,
  Table2, X,
} from 'lucide-react'
import curatedApi from '@/api/v2/curated'
import datasetsApi from '@/api/v2/datasets'
import {
  linkMappingForType, mappingTargetId, typesCompatible, userFieldMapping, useMappingData,
  type MappingDataset,
} from './mapping-data'
import './mapping-overview.css'

type TargetSelection = { kind: 'object'; id: string } | { kind: 'relation'; id: string }
const PREVIEW_PAGE_SIZES = [10, 20, 50]

type MappingRowStatus = 'ready' | 'no-data' | 'incomplete' | 'type-risk' | 'unmapped' | 'missing-source'
type MappingFilter = 'all' | 'issue' | 'object' | 'relation'

interface FieldPair {
  source: string
  target: string
  sourceType?: string
  targetType?: string
  compatible?: boolean
}

interface MappingRow {
  key: string
  selection: TargetSelection
  kind: TargetSelection['kind']
  name: string
  technicalName: string
  datasets: MappingDataset[]
  datasetIds: string[]
  mappingExists: boolean
  mappedFields: number
  totalFields: number
  instanceCount: number
  status: MappingRowStatus
  fieldPairs: FieldPair[]
  missingFields: string[]
}

const STATUS_COPY: Record<MappingRowStatus, { label: string; detail: string }> = {
  ready: { label: '已连通', detail: '映射完整，且已产出实例数据' },
  'no-data': { label: '映射后无数据', detail: '配置已发布，但当前还没有实例数据' },
  incomplete: { label: '字段待补齐', detail: '仍有本体字段没有对应的数据列' },
  'type-risk': { label: '字段类型风险', detail: '部分来源字段与本体字段类型不一致' },
  unmapped: { label: '未配置', detail: '本体元素尚未连接任何真实数据' },
  'missing-source': { label: '数据源不可见', detail: '发布映射引用的数据资产当前不可用' },
}

function resolveRowStatus(
  mappingExists: boolean,
  datasetIds: string[],
  datasets: MappingDataset[],
  mappedFields: number,
  totalFields: number,
  instanceCount: number,
  fieldPairs: FieldPair[],
): MappingRowStatus {
  if (!mappingExists) return 'unmapped'
  if (datasetIds.length === 0 || datasets.length !== datasetIds.length) return 'missing-source'
  if (fieldPairs.some(pair => pair.compatible === false)) return 'type-risk'
  if (mappedFields < totalFields) return 'incomplete'
  if (instanceCount === 0) return 'no-data'
  return 'ready'
}

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

export default function DataMappingOverview({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const data = useMappingData(ontologyId, true)
  const [selected, setSelected] = useState<TargetSelection | null>(null)
  const [mappingSearch, setMappingSearch] = useState('')
  const [mappingFilter, setMappingFilter] = useState<MappingFilter>('all')
  const [previewDataset, setPreviewDataset] = useState<MappingDataset | null>(null)

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

  const objectRows: MappingRow[] = data.objectTypes.map(item => {
    const mapping = data.mappings.find(candidate => mappingTargetId(candidate) === item.id)
    const fieldMapping = userFieldMapping(mapping)
    const targetFields = item.properties.filter(property => property.source !== 'computed' && !property.computed)
    const datasetIds = mapping?.curated_dataset_id ? [mapping.curated_dataset_id] : []
    const datasets = data.datasets.filter(dataset => datasetIds.includes(dataset.id))
    const fieldPairs = Object.entries(fieldMapping).map(([source, target]): FieldPair => {
      const sourceField = datasets.flatMap(dataset => dataset.columns).find(column => column.name === source)
      const targetField = targetFields.find(property => property.id === target || property.name === target)
      return {
        source,
        target: targetField?.displayName || targetField?.name || target,
        sourceType: sourceField?.type,
        targetType: targetField?.type,
        compatible: sourceField && targetField ? typesCompatible(sourceField.type, targetField.type) : undefined,
      }
    })
    const mappedTargets = new Set(Object.values(fieldMapping))
    const missingFields = targetFields
      .filter(property => !mappedTargets.has(property.id) && !mappedTargets.has(property.name))
      .map(property => property.displayName || property.name)
    const instanceCount = objectInstanceCounts.get(item.id) || 0
    const mappingExists = Boolean(mapping)
    return {
      key: `object:${item.id}`,
      selection: { kind: 'object', id: item.id },
      kind: 'object',
      name: item.displayName || item.name,
      technicalName: item.name,
      datasets,
      datasetIds,
      mappingExists,
      mappedFields: fieldPairs.length,
      totalFields: targetFields.length,
      instanceCount,
      status: resolveRowStatus(mappingExists, datasetIds, datasets, fieldPairs.length, targetFields.length, instanceCount, fieldPairs),
      fieldPairs,
      missingFields,
    }
  })

  const relationRows: MappingRow[] = data.linkTypes.map(item => {
    const mapping = linkMappingForType(item, data.linkMappings)
    const targetFields = (item.properties || []).filter(property => property.source !== 'computed' && !property.computed)
    const datasetIds = [...new Set([
      mapping?.edge_dataset_id,
      mapping?.src_dataset_id,
      mapping?.tgt_dataset_id,
    ].filter((id): id is string => Boolean(id)))]
    const datasets = data.datasets.filter(dataset => datasetIds.includes(dataset.id))
    const fieldPairs: FieldPair[] = []
    if (mapping?.src_key) fieldPairs.push({ source: mapping.src_key, target: '源对象关联键' })
    if (mapping?.tgt_key) fieldPairs.push({ source: mapping.tgt_key, target: '目标对象关联键' })
    for (const [target, source] of Object.entries(mapping?.field_mapping || {})) {
      const sourceField = datasets.flatMap(dataset => dataset.columns).find(column => column.name === source)
      const targetField = targetFields.find(property => property.id === target || property.name === target)
      fieldPairs.push({
        source,
        target: targetField?.displayName || targetField?.name || target,
        sourceType: sourceField?.type,
        targetType: targetField?.type,
        compatible: sourceField && targetField ? typesCompatible(sourceField.type, targetField.type) : undefined,
      })
    }
    const mappedTargets = new Set(Object.keys(mapping?.field_mapping || {}))
    const missingFields = [
      ...(!mapping?.src_key ? ['源对象关联键'] : []),
      ...(!mapping?.tgt_key ? ['目标对象关联键'] : []),
      ...targetFields
        .filter(property => !mappedTargets.has(property.id) && !mappedTargets.has(property.name))
        .map(property => property.displayName || property.name),
    ]
    const instanceCount = linkInstanceCounts.get(item.id) || 0
    const mappingExists = Boolean(mapping)
    const totalFields = targetFields.length + 2
    return {
      key: `relation:${item.id}`,
      selection: { kind: 'relation', id: item.id },
      kind: 'relation',
      name: item.displayName || item.name,
      technicalName: item.name,
      datasets,
      datasetIds,
      mappingExists,
      mappedFields: fieldPairs.length,
      totalFields,
      instanceCount,
      status: resolveRowStatus(mappingExists, datasetIds, datasets, fieldPairs.length, totalFields, instanceCount, fieldPairs),
      fieldPairs,
      missingFields,
    }
  })

  const mappingRows = [...objectRows, ...relationRows]
  const issueRows = mappingRows.filter(row => row.status !== 'ready')
  const selectedRow = mappingRows.find(row => row.kind === selected?.kind && row.selection.id === selected?.id)
    || issueRows[0]
    || mappingRows[0]
  const normalizedSearch = mappingSearch.trim().toLowerCase()
  const filteredRows = mappingRows.filter(row => {
    if (mappingFilter === 'issue' && row.status === 'ready') return false
    if (mappingFilter === 'object' && row.kind !== 'object') return false
    if (mappingFilter === 'relation' && row.kind !== 'relation') return false
    if (!normalizedSearch) return true
    return [row.name, row.technicalName, ...row.datasets.map(dataset => dataset.name)]
      .some(value => value.toLowerCase().includes(normalizedSearch))
  })
  const totalTargetFields = mappingRows.reduce((sum, row) => sum + row.totalFields, 0)
  const mappedFields = mappingRows.reduce((sum, row) => sum + row.mappedFields, 0)
  const fieldCoverage = totalTargetFields ? Math.min(100, Math.round(mappedFields / totalTargetFields * 100)) : 0
  const readyCount = mappingRows.filter(row => row.status === 'ready').length
  const totalInstances = mappingRows.reduce((sum, row) => sum + row.instanceCount, 0)
  const usedDatasetIds = new Set(mappingRows.flatMap(row => row.datasets.map(dataset => dataset.id)))
  const issueSummary = [
    { status: 'unmapped', label: '未配置' },
    { status: 'missing-source', label: '数据源不可见' },
    { status: 'type-risk', label: '类型风险' },
    { status: 'incomplete', label: '字段待补齐' },
    { status: 'no-data', label: '未产出数据' },
  ].map(item => ({ ...item, count: mappingRows.filter(row => row.status === item.status).length }))
    .filter(item => item.count > 0)
  const selectedQuality = selectedRow?.datasets.filter(dataset => dataset.quality != null).length
    ? Math.round(selectedRow.datasets.filter(dataset => dataset.quality != null).reduce((sum, dataset) => {
      const quality = Number(dataset.quality)
      return sum + (quality <= 1 ? quality * 100 : quality)
    }, 0) / selectedRow.datasets.filter(dataset => dataset.quality != null).length)
    : null
  const openMappingWorkspace = () => navigate(`/ontologies/${ontologyId}/graph?view=mapping`)

  if (data.isLoading) {
    return <div className="dmo-loading"><Loader2 className="animate-spin" size={20} />正在整理映射状态…</div>
  }
  if (data.isError) {
    return <div className="dmo-loading dmo-loading--error"><AlertCircle size={20} />映射状态加载失败，请稍后重试。</div>
  }

  return (
    <section className="dmo-card">
      <header className="dmo-summary">
        <div className={`dmo-readiness ${issueRows.length === 0 ? 'is-ready' : ''}`}>
          <span>{issueRows.length === 0 ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}</span>
          <div>
            <b>{issueRows.length === 0 ? '当前数据链路可用' : `${issueRows.length} 个本体元素需要处理`}</b>
            <small>
              {issueRows.length === 0
                ? `对象与关系均已连接真实数据，共产出 ${totalInstances.toLocaleString()} 条实例`
                : issueSummary.map(item => `${item.label} ${item.count}`).join(' · ')}
            </small>
          </div>
        </div>
        <div className="dmo-kpis" aria-label="映射关键指标">
          <div><span>可用链路</span><b>{readyCount}<i> / {mappingRows.length}</i></b></div>
          <div><span>字段已连接</span><b>{fieldCoverage}<i>%</i></b><small>{mappedFields} / {totalTargetFields} 个字段</small></div>
          <div><span>实例已产出</span><b>{totalInstances.toLocaleString()}</b><small>对象与关系实例</small></div>
          <div><span>数据资产在用</span><b>{usedDatasetIds.size}<i> / {data.datasets.length}</i></b><small>当前发布版本</small></div>
        </div>
        <button type="button" className="dmo-primary-button" onClick={openMappingWorkspace}>
          <Settings2 size={15} />数据映射
        </button>
      </header>

      <div className="dmo-workspace">
        <main className="dmo-register">
          <div className="dmo-register-head">
            <div className="dmo-register-title">
              <b>映射结果清单</b>
              <small>从本体出发，检查每个对象与关系是否真正获得了数据</small>
            </div>
            <div className="dmo-register-actions">
              <div className="dmo-filters" aria-label="筛选映射结果">
                {([
                  ['all', `全部 ${mappingRows.length}`],
                  ['issue', `待处理 ${issueRows.length}`],
                  ['object', `对象 ${objectRows.length}`],
                  ['relation', `关系 ${relationRows.length}`],
                ] as Array<[MappingFilter, string]>).map(([value, label]) => (
                  <button type="button" key={value} data-active={mappingFilter === value} onClick={() => setMappingFilter(value)}>{label}</button>
                ))}
              </div>
              <label className="dmo-search">
                <Search size={14} />
                <input value={mappingSearch} onChange={event => setMappingSearch(event.target.value)} placeholder="搜索本体元素或数据集" />
              </label>
            </div>
          </div>
          <div className="dmo-table-head" aria-hidden="true">
            <span>本体元素</span><span>真实数据来源</span><span>字段连接</span><span>实例产出</span><span>当前状态</span><i />
          </div>
          <div className="dmo-row-list">
            {filteredRows.map(row => {
              const active = selectedRow?.key === row.key
              return (
                <button
                  type="button"
                  className="dmo-map-row"
                  data-selected={active}
                  key={row.key}
                  onClick={() => setSelected(row.selection)}
                  aria-pressed={active}
                >
                  <span className="dmo-target-cell">
                    <i>{row.kind === 'object' ? <Boxes size={15} /> : <GitBranch size={15} />}</i>
                    <span><b>{row.name}</b><small>{row.kind === 'object' ? '对象实体' : '实体关系'} · {row.technicalName}</small></span>
                  </span>
                  <span className="dmo-dataset-cell">
                    {row.datasets.length > 0
                      ? <><b>{row.datasets[0].name}</b><small>{row.datasets.length > 1 ? `另有 ${row.datasets.length - 1} 个数据集` : row.datasets[0].sourceLabel}</small></>
                      : <><b>—</b><small>{row.mappingExists ? '引用资产不可见' : '尚未选择数据集'}</small></>}
                  </span>
                  <span className="dmo-field-cell">
                    <b>{row.mappedFields} / {row.totalFields}</b>
                    <i><em style={{ width: `${row.totalFields ? Math.min(100, row.mappedFields / row.totalFields * 100) : 100}%` }} /></i>
                  </span>
                  <span className="dmo-instance-cell"><b>{row.instanceCount.toLocaleString()}</b><small>条</small></span>
                  <span className="dmo-status" data-status={row.status}>{row.status === 'ready' && <CheckCircle2 size={12} />}{STATUS_COPY[row.status].label}</span>
                  <ArrowRight size={14} className="dmo-row-arrow" />
                </button>
              )
            })}
            {filteredRows.length === 0 && <div className="dmo-list-empty"><Search size={20} /><span>没有符合条件的映射</span></div>}
          </div>
        </main>

        <aside className="dmo-inspector" aria-label="选中映射的数据血缘详情">
          <div className="dmo-inspector-head">
            <div><b>数据血缘详情</b><small>数据如何进入当前本体元素</small></div>
            <button type="button" onClick={openMappingWorkspace} aria-label="配置当前映射"><Settings2 size={15} /></button>
          </div>
          {selectedRow ? (
            <div className="dmo-inspector-body">
              <section className="dmo-selection-title">
                <span>{selectedRow.kind === 'object' ? <Boxes size={17} /> : <GitBranch size={17} />}</span>
                <div><b>{selectedRow.name}</b><small>{selectedRow.kind === 'object' ? '对象实体' : '实体关系'} · {selectedRow.technicalName}</small></div>
                <em className="dmo-status" data-status={selectedRow.status}>{STATUS_COPY[selectedRow.status].label}</em>
              </section>

              <section className="dmo-status-note" data-status={selectedRow.status}>
                {selectedRow.status === 'ready' ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
                <div><b>{STATUS_COPY[selectedRow.status].detail}</b><small>{selectedRow.instanceCount.toLocaleString()} 条实例可供实例查询、业务探索与治理规则使用</small></div>
              </section>

              <section className="dmo-lineage">
                <h3><Link2 size={14} />数据链路</h3>
                <div className="dmo-lineage-route">
                  <span className="dmo-route-sources">
                    {selectedRow.datasets.length > 0
                      ? selectedRow.datasets.map(dataset => <i key={dataset.id}><Database size={12} />{dataset.name}</i>)
                      : <i data-empty="true"><Database size={12} />未连接数据资产</i>}
                  </span>
                  <ArrowRight size={15} />
                  <span className="dmo-route-target">{selectedRow.kind === 'object' ? <Boxes size={12} /> : <GitBranch size={12} />}{selectedRow.name}</span>
                </div>
              </section>

              {selectedRow.datasets.length > 0 && (
                <section className="dmo-source-detail">
                  <h3>来源数据资产</h3>
                  {selectedRow.datasets.map(dataset => {
                    const quality = dataset.quality == null ? null : Math.round(Number(dataset.quality) <= 1 ? Number(dataset.quality) * 100 : Number(dataset.quality))
                    return (
                      <div key={dataset.id}>
                        <span className={`dmo-source-icon dmo-source-icon--${dataset.source}`}><Table2 size={14} /></span>
                        <span><b>{dataset.name}</b><small>{dataset.sourceLabel} · {dataset.rows == null ? '暂无行数' : `${dataset.rows.toLocaleString()} 行`}{quality == null ? '' : ` · 质量 ${quality}%`}</small></span>
                        <button type="button" onClick={() => setPreviewDataset(dataset)} aria-label={`预览数据源 ${dataset.name}`}><Eye size={14} />预览</button>
                      </div>
                    )
                  })}
                  {selectedQuality != null && <p>来源数据平均质量 <b>{selectedQuality}%</b></p>}
                </section>
              )}

              <section className="dmo-fields">
                <h3><span>字段对照</span><em>{selectedRow.mappedFields} / {selectedRow.totalFields}</em></h3>
                {selectedRow.fieldPairs.length > 0 ? (
                  <div className="dmo-field-pairs">
                    {selectedRow.fieldPairs.map((pair, index) => (
                      <div key={`${pair.source}:${pair.target}:${index}`} data-risk={pair.compatible === false}>
                        <span><b>{pair.source}</b><small>{pair.sourceType || '来源字段'}</small></span>
                        <ArrowRight size={13} />
                        <span><b>{pair.target}</b><small>{pair.targetType || '本体字段'}</small></span>
                        {pair.compatible === false && <AlertTriangle size={13} />}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="dmo-field-empty">尚无字段连接</div>
                )}
                {selectedRow.missingFields.length > 0 && (
                  <div className="dmo-missing-fields"><b>待连接字段</b><span>{selectedRow.missingFields.map(field => <i key={field}>{field}</i>)}</span></div>
                )}
              </section>

              {selectedRow.status !== 'ready' && (
                <section className="dmo-next-step">
                  <div><b>下一步</b><small>创建或打开草稿版本，补齐这条数据链路后再发布。</small></div>
                  <button type="button" onClick={openMappingWorkspace}>去配置<ArrowRight size={13} /></button>
                </section>
              )}
            </div>
          ) : (
            <div className="dmo-inspector-empty"><Link2 size={24} /><b>暂无可检查的本体元素</b><span>请先在本体结构中创建对象实体或实体关系</span></div>
          )}
        </aside>
      </div>
      {previewDataset && <DatasetPreviewDialog key={previewDataset.id} dataset={previewDataset} onClose={() => setPreviewDataset(null)} />}
    </section>
  )
}
