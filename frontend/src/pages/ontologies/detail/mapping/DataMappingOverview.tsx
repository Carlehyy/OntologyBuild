import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle, AlertTriangle, ArrowRight, Boxes, CheckCircle2, ChevronLeft,
  ChevronRight, Database, Eye, GitBranch, Link2, Loader2, RefreshCw, Search,
  Table2, Workflow, X,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import curatedApi from '@/api/v2/curated'
import datasetsApi from '@/api/v2/datasets'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import {
  appliedDatasetVersionId, appliedLinkVersionId, datasetReviewState,
  formatFullDateTime, formatShortDateTime, isReviewIssue, linkMappingForType,
  mappingTargetId, matchAppliedVersion, typesCompatible, userFieldMapping,
  useMappingData, type DatasetReviewState, type MappingDataset,
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
  mappingId: string | null
  selection: TargetSelection
  kind: TargetSelection['kind']
  name: string
  technicalName: string
  datasets: MappingDataset[]
  datasetIds: string[]
  /** 每个引用数据集"最近一次灌入所用版本"的 id（取自映射快照，可能为 null）。 */
  appliedVersionByDataset: Record<string, string | null>
  mappingExists: boolean
  mappedFields: number
  totalFields: number
  instanceCount: number
  status: MappingRowStatus
  fieldPairs: FieldPair[]
  missingFields: string[]
}

interface MappingReconcileResult {
  total_entities?: number
  total_relations?: number
  sentinel_dispatch?: { evaluated?: number; fired?: number }
}

const STATUS_COPY: Record<MappingRowStatus, { label: string; detail: string }> = {
  ready: { label: '已连通', detail: '映射完整，且已产出实例数据' },
  'no-data': { label: '暂无实例数据', detail: '配置已发布，但当前还没有实例数据' },
  incomplete: { label: '字段待补齐', detail: '仍有本体字段没有对应的数据列' },
  'type-risk': { label: '字段类型风险', detail: '部分来源字段与本体字段类型不一致' },
  unmapped: { label: '未配置', detail: '本体元素尚未连接任何真实数据' },
  'missing-source': { label: '数据源不可见', detail: '发布映射引用的数据资产当前不可用' },
}

/** 审核异常徽标文案与禁用原因（approved / na 不展示徽标——正常态不打扰）。 */
const REVIEW_BADGE_COPY: Record<Exclude<DatasetReviewState, 'approved' | 'na'>, { label: string; reason: string }> = {
  rejected: { label: '已拒绝', reason: '数据集已拒绝，仅保留审计，不可预览' },
  pending_review: { label: '待审核', reason: '数据集待审核，批准后可预览' },
}

function ReviewBadge({ state, className = '' }: { state: DatasetReviewState; className?: string }) {
  if (!isReviewIssue(state)) return null
  const copy = REVIEW_BADGE_COPY[state]
  return <em className={`dmo-review-badge ${className}`} data-state={state}>{copy.label}</em>
}

/** 一行内多个引用数据集取最严重的审核异常（rejected 优先于 pending_review）。 */
function worstReviewState(datasets: MappingDataset[]): DatasetReviewState {
  const states = datasets.map(datasetReviewState)
  if (states.includes('rejected')) return 'rejected'
  if (states.includes('pending_review')) return 'pending_review'
  return states.includes('approved') ? 'approved' : 'na'
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

function mappingOperationError(error: unknown) {
  const value = error as { detail?: unknown; message?: unknown }
  if (typeof value.detail === 'string') return value.detail
  if (
    value.detail && typeof value.detail === 'object'
    && 'message' in value.detail && typeof value.detail.message === 'string'
  ) return value.detail.message
  return typeof value.message === 'string' ? value.message : '重新灌入失败，请稍后重试'
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
  const rangeStart = rows.length ? offset + 1 : 0
  const rangeEnd = rows.length ? Math.min(offset + rows.length, totalRows) : 0
  const reviewState = datasetReviewState(dataset)
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
            <div><h3 id="dmo-preview-title">{dataset.name}</h3><em>{totalRows.toLocaleString()} 行</em><ReviewBadge state={reviewState} /></div>
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
          <span className="dmo-preview-range">{rangeStart === 0 ? `0 / ${totalRows.toLocaleString()}` : `${rangeStart}–${rangeEnd} / ${totalRows.toLocaleString()}`} 行</span>
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
  const [reconcileTarget, setReconcileTarget] = useState<MappingRow | null>(null)
  const [reconcilingMappingId, setReconcilingMappingId] = useState<string | null>(null)
  const [reconcileFeedback, setReconcileFeedback] = useState<{
    mappingId: string
    tone: 'success' | 'error'
    message: string
  } | null>(null)

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
    const appliedVersionByDataset: Record<string, string | null> = {}
    if (mapping?.curated_dataset_id) {
      appliedVersionByDataset[mapping.curated_dataset_id] = appliedDatasetVersionId(mapping.field_mapping)
    }
    return {
      key: `object:${item.id}`,
      mappingId: mapping?.id || null,
      selection: { kind: 'object', id: item.id },
      kind: 'object',
      name: item.displayName || item.name,
      technicalName: item.name,
      datasets,
      datasetIds,
      appliedVersionByDataset,
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
    for (const [target, rawSource] of Object.entries(mapping?.field_mapping || {})) {
      if (target.startsWith('__') || typeof rawSource !== 'string') continue
      const source = rawSource
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
    const mappedTargets = new Set(
      Object.keys(mapping?.field_mapping || {}).filter(target => !target.startsWith('__')),
    )
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
    const appliedVersionByDataset: Record<string, string | null> = {}
    for (const datasetId of datasetIds) {
      appliedVersionByDataset[datasetId] = appliedLinkVersionId(mapping?.field_mapping, {
        srcDatasetId: mapping?.src_dataset_id,
        tgtDatasetId: mapping?.tgt_dataset_id,
        edgeDatasetId: mapping?.edge_dataset_id,
      }, datasetId)
    }
    return {
      key: `relation:${item.id}`,
      mappingId: mapping?.id || null,
      selection: { kind: 'relation', id: item.id },
      kind: 'relation',
      name: item.displayName || item.name,
      technicalName: item.name,
      datasets,
      datasetIds,
      appliedVersionByDataset,
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
  const usedDatasets = mappingRows.flatMap(row => row.datasets)
    .filter((dataset, index, list) => list.findIndex(candidate => candidate.id === dataset.id) === index)
  const usedCuratedCount = usedDatasets.filter(dataset => dataset.source === 'curated').length
  const usedManualCount = usedDatasets.length - usedCuratedCount
  const reviewIssueCount = usedDatasets.filter(dataset => isReviewIssue(datasetReviewState(dataset))).length
  const issueSummary = (['unmapped', 'missing-source', 'type-risk', 'incomplete', 'no-data'] as const)
    .map(status => ({ status, label: STATUS_COPY[status].label, count: mappingRows.filter(row => row.status === status).length }))
    .filter(item => item.count > 0)
  const selectedQuality = selectedRow?.datasets.filter(dataset => dataset.quality != null).length
    ? Math.round(selectedRow.datasets.filter(dataset => dataset.quality != null).reduce((sum, dataset) => {
      const quality = Number(dataset.quality)
      return sum + (quality <= 1 ? quality * 100 : quality)
    }, 0) / selectedRow.datasets.filter(dataset => dataset.quality != null).length)
    : null
  const openMappingWorkspace = (selection?: TargetSelection) => {
    const params = new URLSearchParams({ view: 'mapping' })
    if (selection) params.set('focus', `${selection.kind}:${selection.id}`)
    navigate(`/ontologies/${ontologyId}/graph?${params.toString()}`)
  }
  const reconcileApprovedData = async (mappingId: string) => {
    setReconcilingMappingId(mappingId)
    setReconcileFeedback(null)
    try {
      const result = await apiClientV2.post<MappingReconcileResult>(
        `/ontologies/${ontologyId}/mappings/${mappingId}/apply-from-dataset`,
      )
      await data.refetch()
      const evaluated = result.sentinel_dispatch?.evaluated ?? 0
      const fired = result.sentinel_dispatch?.fired ?? 0
      setReconcileFeedback({
        mappingId,
        tone: 'success',
        message: `灌入完成：对象实例 ${result.total_entities ?? 0} 条、关系实例 ${result.total_relations ?? 0} 条已更新；哨兵评估 ${evaluated} 次、触发 ${fired} 次。`,
      })
    } catch (error) {
      setReconcileFeedback({
        mappingId,
        tone: 'error',
        message: mappingOperationError(error),
      })
    } finally {
      setReconcilingMappingId(null)
    }
  }

  const allHealthy = issueRows.length === 0 && reviewIssueCount === 0
  const reviewOnlyIssue = issueRows.length === 0 && reviewIssueCount > 0
  const readinessTitle = allHealthy
    ? '当前数据链路可用'
    : reviewOnlyIssue
      ? `数据链路已连通，但 ${reviewIssueCount} 个来源数据集审核状态异常`
      : `${issueRows.length} 个本体元素需要处理`
  const readinessSub = allHealthy
    ? `对象与关系均已连接真实数据，共产出 ${totalInstances.toLocaleString()} 条实例`
    : reviewOnlyIssue
      ? '已拒绝/待审核的数据集不可预览与重新灌入；已产出实例不受影响'
      : [
          ...issueSummary.map(item => `${item.label} ${item.count}`),
          ...(reviewIssueCount > 0 ? [`审核异常 ${reviewIssueCount}`] : []),
        ].join(' · ')

  if (data.isLoading) {
    return <div className="dmo-loading"><Loader2 className="animate-spin" size={20} />正在整理映射状态…</div>
  }
  if (data.isError) {
    return <div className="dmo-loading dmo-loading--error"><AlertCircle size={20} />映射状态加载失败，请稍后重试。</div>
  }

  return (
    <section className="dmo-card">
      <header className="dmo-summary">
        <div className={`dmo-readiness ${allHealthy ? 'is-ready' : ''}`}>
          <span>{allHealthy ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}</span>
          <div>
            <b>{readinessTitle}</b>
            <small>{readinessSub}</small>
          </div>
        </div>
        <div className="dmo-kpis" aria-label="映射关键指标">
          <div><span>可用链路</span><b>{readyCount}<i> / {mappingRows.length}</i></b></div>
          {totalTargetFields > 0 ? (
            <div><span>字段已连接</span><b>{fieldCoverage}<i>%</i></b><small>{mappedFields} / {totalTargetFields} 个字段</small></div>
          ) : (
            <div><span>字段已连接</span><b>—</b><small>暂无可连接字段</small></div>
          )}
          <div><span>实例已产出</span><b>{totalInstances.toLocaleString()}</b><small>对象与关系实例</small></div>
          <div><span>来源数据资产</span><b>{usedDatasetIds.size}</b><small>{usedDatasetIds.size > 0 ? `成品 ${usedCuratedCount} · 人工 ${usedManualCount}` : '尚未连接数据资产'}</small></div>
        </div>
        <button type="button" className="dmo-primary-button" onClick={() => openMappingWorkspace()}>
          <Workflow size={15} />查看字段级映射
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
                  <button type="button" key={value} data-active={mappingFilter === value} aria-pressed={mappingFilter === value} onClick={() => setMappingFilter(value)}>{label}</button>
                ))}
              </div>
              <label className="dmo-search">
                <Search size={14} />
                <input value={mappingSearch} onChange={event => setMappingSearch(event.target.value)} placeholder="搜索本体元素或数据集" aria-label="搜索本体元素或数据集" />
              </label>
            </div>
          </div>
          <div className="dmo-table-head" aria-hidden="true">
            <span>本体元素</span><span>真实数据来源</span><span>字段连接</span><span>实例产出</span><span>当前状态</span><i />
          </div>
          <div className="dmo-row-list">
            {filteredRows.map(row => {
              const active = selectedRow?.key === row.key
              const rowReview = worstReviewState(row.datasets)
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
                    <span><b title={row.name}>{row.name}</b><small title={row.technicalName}>{row.kind === 'object' ? '对象实体' : '实体关系'} · {row.technicalName}</small></span>
                  </span>
                  <span className="dmo-dataset-cell">
                    {row.datasets.length > 0
                      ? <><b title={row.datasets[0].name}>{row.datasets[0].name}</b><small>{row.datasets.length > 1 ? `另有 ${row.datasets.length - 1} 个数据集` : row.datasets[0].sourceLabel}<ReviewBadge state={rowReview} /></small></>
                      : <><b>—</b><small>{row.mappingExists ? '引用资产不可见' : '尚未选择数据集'}</small></>}
                  </span>
                  <span className="dmo-field-cell">
                    <b>{row.mappedFields} / {row.totalFields}</b>
                    <i><em style={{ width: `${row.totalFields ? Math.min(100, row.mappedFields / row.totalFields * 100) : 100}%` }} /></i>
                  </span>
                  <span className="dmo-instance-cell">
                    {row.mappingExists
                      ? <><b>{row.instanceCount.toLocaleString()}</b><small>条</small></>
                      : <b className="is-empty" title="尚未建立映射，暂无实例">—</b>}
                  </span>
                  <span className="dmo-status" data-status={row.status}>{row.status === 'ready' && <CheckCircle2 size={12} />}{STATUS_COPY[row.status].label}</span>
                  <ArrowRight size={14} className="dmo-row-arrow" />
                </button>
              )
            })}
            {filteredRows.length === 0 && mappingRows.length === 0 && (
              <div className="dmo-list-empty dmo-list-empty--onboarding">
                <Boxes size={22} />
                <b>该本体还没有对象实体或实体关系</b>
                <span>请先在「本体结构」中完成建模，再回到这里连接真实数据。</span>
                <button type="button" onClick={() => navigate(`/ontologies/${ontologyId}?tab=design`)}>前往本体结构</button>
              </div>
            )}
            {filteredRows.length === 0 && mappingRows.length > 0 && <div className="dmo-list-empty"><Search size={20} /><span>没有符合条件的映射</span></div>}
          </div>
        </main>

        <aside className="dmo-inspector" aria-label="选中映射的数据血缘详情">
          <div className="dmo-inspector-head">
            <div><b>数据血缘详情</b><small>数据如何进入当前本体元素</small></div>
            <button type="button" onClick={() => selectedRow && openMappingWorkspace(selectedRow.selection)} aria-label="查看该元素字段映射" title="在字段级映射视图中查看该元素"><Workflow size={15} /></button>
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
                    const review = datasetReviewState(dataset)
                    const applied = matchAppliedVersion(
                      data.datasetVersionLists[dataset.id] || [],
                      selectedRow.appliedVersionByDataset[dataset.id] ?? null,
                    )
                    const appliedAt = applied ? formatShortDateTime(applied.processedAt) : ''
                    const previewBlocked = isReviewIssue(review)
                    return (
                      <div className="dmo-source-item" key={dataset.id}>
                        <div className="dmo-source-row">
                          <span className={`dmo-source-icon dmo-source-icon--${dataset.source}`}><Table2 size={14} /></span>
                          <span>
                            <i className="dmo-source-name"><b title={dataset.name}>{dataset.name}</b><ReviewBadge state={review} /></i>
                            <small title={applied ? `已灌入版本 v${applied.versionNo}，产出于 ${formatFullDateTime(applied.processedAt)}` : undefined}>
                              {dataset.sourceLabel} · {dataset.rows == null ? '暂无行数' : `${dataset.rows.toLocaleString()} 行`}{quality == null ? '' : ` · 质量 ${quality}%`}{applied ? ` · 已灌入 v${applied.versionNo}${appliedAt ? ` · ${appliedAt}` : ''}` : ''}
                            </small>
                          </span>
                          <button
                            type="button"
                            onClick={() => setPreviewDataset(dataset)}
                            aria-label={`预览数据源 ${dataset.name}`}
                            disabled={previewBlocked}
                            title={isReviewIssue(review) ? REVIEW_BADGE_COPY[review].reason : `预览数据源 ${dataset.name}`}
                          ><Eye size={14} />预览</button>
                        </div>
                        {review === 'rejected' && (
                          <p className="dmo-source-warning" role="note">当前版本已被拒绝，仅保留审计追溯；不可预览与重新灌入。</p>
                        )}
                      </div>
                    )
                  })}
                  {selectedQuality != null && <p>来源数据平均质量 <b>{selectedQuality}%</b></p>}
                </section>
              )}

              {selectedRow.kind === 'object' && selectedRow.mappingId && (() => {
                const boundReview = worstReviewState(selectedRow.datasets)
                const reviewBlocked = isReviewIssue(boundReview)
                const busy = reconcilingMappingId === selectedRow.mappingId
                return (
                  <section className="dmo-reconcile">
                    <div>
                      <b>重新灌入已批准数据</b>
                      <small>只读取当前映射绑定的最新已批准版本，按当前发布结构重写对象与关系实例，并触发一次哨兵评估。</small>
                    </div>
                    <button
                      type="button"
                      disabled={reconcilingMappingId !== null || reviewBlocked}
                      aria-busy={busy}
                      title={reviewBlocked ? '来源数据集已拒绝/待审核，不能灌入' : undefined}
                      onClick={() => setReconcileTarget(selectedRow)}
                    >
                      {busy
                        ? <Loader2 size={13} className="animate-spin" />
                        : <RefreshCw size={13} />}
                      {busy ? '正在灌入…' : '立即灌入'}
                    </button>
                    {reviewBlocked && (
                      <p className="dmo-reconcile-note" data-tone="error" role="note">
                        <AlertCircle size={13} /><span>来源数据集{isReviewIssue(boundReview) ? REVIEW_BADGE_COPY[boundReview].label : ''}，不能灌入；请先完成数据审核。</span>
                    </p>
                    )}
                    {!reviewBlocked && reconcileFeedback?.mappingId === selectedRow.mappingId && (
                      <p
                        className="dmo-reconcile-note"
                        role={reconcileFeedback.tone === 'error' ? 'alert' : 'status'}
                        data-tone={reconcileFeedback.tone}
                      >
                        {reconcileFeedback.tone === 'success'
                          ? <CheckCircle2 size={13} />
                          : <AlertCircle size={13} />}
                        <span>{reconcileFeedback.message}</span>
                      </p>
                    )}
                  </section>
                )
              })()}

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

              {selectedRow.status !== 'ready' && selectedRow.status !== 'no-data' && (() => {
                const nextStepCopy: Record<string, string> = {
                  unmapped: '该元素尚未连接数据资产。建立映射需在草稿版本中进行：前往图谱页，基于当前发布创建草稿后即可配置。',
                  incomplete: '仍有字段未连接数据列。可先查看字段级映射定位缺口；补齐需在草稿版本中进行。',
                  'type-risk': '部分来源字段与本体字段类型不一致，灌入后可能产生异常值。可查看字段级映射核对；修改需在草稿版本中进行。',
                  'missing-source': '映射引用的数据资产当前不可见，可能被删除或已失效。需在草稿版本中重新绑定数据资产。',
                }
                return (
                  <section className="dmo-next-step">
                    <div><b>下一步</b><small>{nextStepCopy[selectedRow.status]}</small></div>
                    <div className="dmo-next-step-actions">
                      {selectedRow.status !== 'unmapped' && (
                        <button type="button" onClick={() => openMappingWorkspace(selectedRow.selection)}>查看字段映射<ArrowRight size={13} /></button>
                      )}
                      <button type="button" className="dmo-next-step-draft" onClick={() => navigate(`/ontologies/${ontologyId}/graph`)}>前往图谱页创建草稿</button>
                    </div>
                  </section>
                )
              })()}
            </div>
          ) : (
            <div className="dmo-inspector-empty"><Link2 size={24} /><b>暂无可检查的本体元素</b><span>请先在本体结构中创建对象实体或实体关系</span></div>
          )}
        </aside>
      </div>
      {previewDataset && <DatasetPreviewDialog key={previewDataset.id} dataset={previewDataset} onClose={() => setPreviewDataset(null)} />}
      <Modal
        open={reconcileTarget !== null}
        onClose={() => setReconcileTarget(null)}
        title="确认重新灌入数据"
        size="sm"
        footer={(
          <>
            <Button type="button" variant="outline" onClick={() => setReconcileTarget(null)}>取消</Button>
            <Button
              type="button"
              onClick={() => {
                const target = reconcileTarget
                setReconcileTarget(null)
                if (target?.mappingId) void reconcileApprovedData(target.mappingId)
              }}
            >确认灌入</Button>
          </>
        )}
      >
        <p className="dmo-reconcile-confirm-text">
          将从绑定数据集的最新已批准版本读取数据，重写「{reconcileTarget?.name}」的对象与关系实例；灌入后按当前发布结构触发哨兵评估。已存在的实例会被新版本数据覆盖更新。
        </p>
      </Modal>
    </section>
  )
}
