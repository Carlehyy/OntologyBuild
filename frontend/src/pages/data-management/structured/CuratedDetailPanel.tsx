import { Fragment, useEffect, useMemo, useRef, useState, useCallback } from 'react'
import {
  X, CheckCircle, AlertTriangle, Clock,
  Save, Loader2, Pencil, Plus, Minus, KeyRound, RefreshCw, Table2,
  ChevronLeft, ChevronRight, Download, FileSpreadsheet, LockKeyhole,
  Info,
} from 'lucide-react'
import curatedApi, { type ReviewDiff } from '@/api/v2/curated'
import datasetsApi, { FIELD_TYPE_LABELS, type DatasetSchemaColumn } from '@/api/v2/datasets'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Props {
  datasetId: string
  datasetName: string
  datasetStatus: string
  pipelineName: string
  onClose: () => void
  onStatusChange: (id: string, status: string) => void
}

const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审核',
  pending:        '待审核',
  in_review:      '待审核',
  approved:       '已审核',
  rejected:       '已审核',
}

const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending:        'bg-yellow-50 text-yellow-700 border-yellow-200',
  in_review:      'bg-yellow-50 text-yellow-700 border-yellow-200',
  approved:       'bg-green-50 text-green-700 border-green-200',
  rejected:       'bg-green-50 text-green-700 border-green-200',
}

const STATUS_ICON = (status: string) => {
  if (status === 'approved' || status === 'rejected') return <CheckCircle size={13} className="text-green-500" />
  return <Clock size={13} className="text-yellow-400" />
}

const isPendingReview = (status: string) => (
  status === 'pending_review' || status === 'pending' || status === 'in_review'
)

type CellKey = `${number}::${string}`
type View = 'changes' | 'previous' | 'current'
type DataRow = Record<string, unknown>

const cellText = (value: unknown) => {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

const cellComparable = (row: DataRow, column: string) => {
  if (!Object.prototype.hasOwnProperty.call(row, column)) return 'missing:'
  const value = row[column]
  return `present:${value === undefined ? 'undefined' : cellText(value)}`
}

const REVIEW_PAGE_SIZES = [20, 50, 100, 200, 500, 1000] as const
const DEFAULT_REVIEW_PAGE_SIZE = 50

const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function errorText(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback
  const e = error as { detail?: unknown; data?: { detail?: unknown }; message?: unknown }
  const detail = e.detail ?? e.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return typeof e.message === 'string' && e.message.trim() ? e.message : fallback
}

function columnsFromRows(rows: DataRow[], preferred: string[] = []): string[] {
  const columns: string[] = []
  const seen = new Set<string>()
  const add = (column: string) => {
    if (!seen.has(column)) {
      seen.add(column)
      columns.push(column)
    }
  }
  preferred.forEach(add)
  rows.forEach(row => Object.keys(row).forEach(add))
  return columns
}

const columnDisplayText = (name: string, schemaColumn?: DatasetSchemaColumn) => {
  const displayName = schemaColumn?.display_name?.trim()
  return displayName && displayName !== name ? `${displayName}（${name}）` : name
}

function ColumnLabel({
  name, primaryKeys, schemaColumn,
}: {
  name: string
  primaryKeys: string[]
  schemaColumn?: DatasetSchemaColumn
}) {
  const isPrimaryKey = primaryKeys.includes(name)
  const isRequired = isPrimaryKey || schemaColumn?.nullable === false
  const displayName = schemaColumn?.display_name?.trim()
  const hasChineseName = Boolean(displayName && displayName !== name)
  return (
    <span className="inline-flex items-center gap-1.5" title={hasChineseName ? `字段标识：${name}` : undefined}>
      <span>{columnDisplayText(name, schemaColumn)}</span>
      {!hasChineseName && (
        <span
          className="rounded border border-slate-200 bg-white px-1 py-0.5 text-[9px] font-normal text-slate-400"
          title="来源流水线没有提供独立的中文显示名，当前沿用字段标识；这不是字段读取错误"
        >沿用字段标识</span>
      )}
      {isPrimaryKey && (
        <span className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-medium text-amber-700" title="主键列：已校验全量非空，用于稳定识别数据行">
          <KeyRound size={8} /> 主键 · 非空
        </span>
      )}
      {!isPrimaryKey && isRequired && (
        <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[9px] font-medium text-rose-600">非空</span>
      )}
      {schemaColumn?.type && (
        <span className="rounded bg-slate-200/70 px-1.5 py-0.5 text-[9px] font-normal text-slate-500">
          {FIELD_TYPE_LABELS[schemaColumn.type] ?? schemaColumn.type}
        </span>
      )}
    </span>
  )
}

/** 只读数据表（列取所有行键的并集，保持稳定表头） */
function ReadonlyTable({ rows, highlight, primaryKeys = [], startIndex = 0, schemaColumns = {}, fillAvailable = false }: {
  rows: DataRow[]
  highlight?: 'add' | 'del'
  primaryKeys?: string[]
  startIndex?: number
  schemaColumns?: Record<string, DatasetSchemaColumn>
  fillAvailable?: boolean
}) {
  if (!rows.length) return <div className={`grid place-items-center text-xs text-slate-400 ${fillAvailable ? 'h-full rounded-xl border border-slate-200 bg-slate-50/30' : 'p-6'}`}>无数据</div>
  const cols = columnsFromRows(rows, [...primaryKeys, ...Object.keys(schemaColumns)])
  const tint = highlight === 'add' ? 'bg-green-50/40' : highlight === 'del' ? 'bg-red-50/40' : ''
  return (
    <div className={fillAvailable ? 'h-full max-w-full overflow-auto rounded-xl border border-slate-200 bg-white' : 'max-w-full overflow-x-auto'}>
      <table className="w-full min-w-max border-separate border-spacing-0 text-xs">
        <thead className="sticky top-0 z-20 bg-slate-50">
          <tr>
            <th className="sticky left-0 z-30 w-12 border-b border-r border-slate-200 bg-slate-50 px-3 py-2.5 text-center font-normal text-slate-400">#</th>
            {cols.map(c => (
              <th key={c} className="min-w-[150px] whitespace-nowrap border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-left font-medium text-slate-700">
                <ColumnLabel name={c} primaryKeys={primaryKeys} schemaColumn={schemaColumns[c]} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={`${tint} transition-colors hover:bg-slate-50/70`}>
              <td className="sticky left-0 z-10 border-b border-r border-slate-100 bg-white px-3 py-2.5 text-center tabular-nums text-slate-300 select-none">{startIndex + i + 1}</td>
              {cols.map(c => (
                <td key={c} className={`max-w-[320px] border-b border-slate-100 px-4 py-2.5 ${primaryKeys.includes(c) ? 'bg-amber-50/45 font-mono' : ''}`}>
                  <span className="block truncate text-slate-700" title={cellText(row[c])}>
                    {cellText(row[c]) || <span className="text-slate-300">—</span>}
                  </span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 更新行按「旧行 → 新行」成对展示；每条新行紧跟对应旧行，宽表可水平滚动。 */
function UpdatedRowsTable({
  updates, primaryKeys, schemaColumns,
}: {
  updates: Array<{ before: DataRow; after: DataRow }>
  primaryKeys: string[]
  schemaColumns: Record<string, DatasetSchemaColumn>
}) {
  if (!updates.length) return <div className="p-6 text-center text-xs text-gray-400">无更新行</div>
  const allRows = updates.flatMap(update => [update.before, update.after])
  const columns = columnsFromRows(allRows, [...primaryKeys, ...Object.keys(schemaColumns)])

  return (
    <div className="max-w-full overflow-x-auto rounded-lg border border-amber-200 bg-white">
      <table className="w-full min-w-max text-xs">
        <thead className="sticky top-0 z-10 border-b bg-slate-50">
          <tr>
            <th className="sticky left-0 z-20 min-w-[170px] border-r bg-slate-50 px-3 py-2 text-left font-medium text-slate-500">对比行 / 变更列</th>
            {columns.map(column => (
              <th key={column} className="min-w-[140px] whitespace-nowrap px-4 py-2 text-left font-medium text-slate-600">
                <ColumnLabel name={column} primaryKeys={primaryKeys} schemaColumn={schemaColumns[column]} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {updates.map((update, index) => {
            const changedColumns = new Set(
              columns.filter(column => cellComparable(update.before, column) !== cellComparable(update.after, column)),
            )
            const changedColumnLabels = Array.from(changedColumns)
              .map(column => columnDisplayText(column, schemaColumns[column]))
              .join('、')
            return (
              <Fragment key={`${primaryKeys.map(key => cellText(update.after[key])).join('::') || 'row'}-${index}`}>
                <tr className="border-t border-amber-200 bg-red-50/35">
                  <td className="sticky left-0 z-[5] min-w-[170px] border-r border-amber-100 bg-red-50 px-3 py-2 align-top">
                    <span className="inline-flex rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700">更新前</span>
                    <span className="ml-1 text-[10px] text-slate-400">#{index + 1}</span>
                    <span className="mt-1 block max-w-[150px] truncate text-[9px] text-red-600" title={`变更列：${changedColumnLabels}`}>
                      变更列：{changedColumnLabels}
                    </span>
                  </td>
                  {columns.map(column => {
                    const changed = changedColumns.has(column)
                    return (
                      <td key={column} className={`max-w-[280px] px-4 py-2 ${changed ? 'bg-red-100/70 text-red-700' : 'text-slate-600'} ${primaryKeys.includes(column) ? 'font-mono' : ''}`}>
                        <span className={`block truncate ${changed ? 'line-through decoration-red-300' : ''}`} title={cellText(update.before[column])}>
                          {cellText(update.before[column]) || <span className="text-slate-300">—</span>}
                        </span>
                      </td>
                    )
                  })}
                </tr>
                <tr className="border-b border-amber-200 bg-emerald-50/35">
                  <td className="sticky left-0 z-[5] min-w-[170px] border-r border-amber-100 bg-emerald-50 px-3 py-2 align-top">
                    <span className="inline-flex rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">更新后</span>
                    <span className="ml-1 text-[9px] text-emerald-600">绿色为新值</span>
                  </td>
                  {columns.map(column => {
                    const changed = changedColumns.has(column)
                    return (
                      <td key={column} className={`max-w-[280px] px-4 py-2 ${changed ? 'bg-emerald-100/80 font-semibold text-emerald-800 ring-1 ring-inset ring-emerald-200' : 'text-slate-600'} ${primaryKeys.includes(column) ? 'font-mono' : ''}`}>
                        <span className="block truncate" title={cellText(update.after[column])}>
                          {cellText(update.after[column]) || <span className="text-slate-300">—</span>}
                        </span>
                        {changed && <span className="mt-0.5 block text-[9px] font-medium text-emerald-600">已变更</span>}
                      </td>
                    )
                  })}
                </tr>
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function CuratedDetailPanel({
  datasetId, datasetName, datasetStatus, pipelineName,
  onClose, onStatusChange,
}: Props) {
  const [status, setStatus] = useState(datasetStatus)
  const [view, setView] = useState<View>(() => isPendingReview(datasetStatus) ? 'changes' : 'current')

  const [diff, setDiff] = useState<ReviewDiff | null>(null)
  const [schemaColumns, setSchemaColumns] = useState<Record<string, DatasetSchemaColumn>>({})
  const [schemaLoadError, setSchemaLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [selectedReviewId, setSelectedReviewId] = useState<string | undefined>()
  const [pageOffset, setPageOffset] = useState(0)
  const [pageSize, setPageSize] = useState<number>(DEFAULT_REVIEW_PAGE_SIZE)
  const [switchingReview, setSwitchingReview] = useState(false)
  const [exporting, setExporting] = useState<'csv' | 'xlsx' | null>(null)
  const [exportMessage, setExportMessage] = useState('')

  // 「本次全量」可编辑视图的状态
  const [rows, setRows] = useState<DataRow[]>([])
  const [editingCell, setEditingCell] = useState<CellKey | null>(null)
  const [pendingEdits, setPendingEdits] = useState<Map<CellKey, { old: string; val: string }>>(new Map())
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const [actionError, setActionError] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  const [approving, setApproving] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)

  const reviewPending = isPendingReview(status)
  const hasPending = pendingEdits.size > 0
  const requestClose = useCallback(() => {
    if (editingCell) {
      // 先触发 input.onBlur 把当前值纳入 pendingEdits；用户再次关闭时再确认放弃。
      editInputRef.current?.blur()
      return
    }
    if (hasPending) setShowCloseConfirm(true)
    else onClose()
  }, [editingCell, hasPending, onClose])

  const loadDiff = useCallback(() => {
    setLoading(true)
    setLoadError('')
    curatedApi.reviewDiff(datasetId, pageSize, pageOffset, selectedReviewId)
      .then(res => {
        const wrapped = res as ReviewDiff & { data?: ReviewDiff }
        const d = wrapped.data ?? res
        setDiff(d)
        const cur: DataRow[] = Array.isArray(d.current?.rows) ? d.current.rows : []
        setRows(cur)
      })
      .catch((error: unknown) => setLoadError(errorText(error, '数据加载失败，请稍后重试')))
      .finally(() => setLoading(false))
  }, [datasetId, pageOffset, pageSize, selectedReviewId])

  useEffect(() => { void Promise.resolve().then(loadDiff) }, [loadDiff])

  useEffect(() => {
    setSchemaLoadError('')
    datasetsApi.schema(datasetId)
      .then(result => setSchemaColumns(Object.fromEntries(
        (result.columns ?? []).map(column => [column.name, column]),
      )))
      .catch(() => {
        setSchemaColumns({})
        setSchemaLoadError('字段中文名加载失败，当前暂以字段标识展示')
      })
  }, [datasetId])

  useEffect(() => {
    if (editingCell) editInputRef.current?.focus()
  }, [editingCell])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape' && !editingCell) requestClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [requestClose, editingCell])

  const cellVal = (rowIdx: number, col: string) => {
    const key: CellKey = `${rowIdx}::${col}`
    return pendingEdits.get(key)?.val ?? cellText(rows[rowIdx]?.[col])
  }

  const startEdit = (rowIdx: number, col: string) => {
    setEditingCell(`${rowIdx}::${col}`)
    setSaveMsg('')
  }

  const commitEdit = (rowIdx: number, col: string, newVal: string) => {
    const key: CellKey = `${rowIdx}::${col}`
    const oldVal = cellText(rows[rowIdx]?.[col])
    if (newVal === oldVal) {
      setPendingEdits(prev => { const m = new Map(prev); m.delete(key); return m })
    } else {
      setPendingEdits(prev => new Map(prev).set(key, { old: oldVal, val: newVal }))
    }
    setEditingCell(null)
  }

  /** 新建审核时再次核对版本，防止“页面加载后恰好入湖新版本”导致批准未看过的数据。 */
  const reviewIdForLoadedVersion = async (): Promise<string> => {
    // 已决定的 review 是不可变审计记录；这里只复用当前版本仍待决定的审核。
    if (diff?.review?.id && diff.review.status === 'pending') return diff.review.id
    const session = await curatedApi.startReview(datasetId)
    const loadedVersionId = diff?.current?.dataset_version_id
    if (loadedVersionId && session.dataset_version_id && loadedVersionId !== session.dataset_version_id) {
      throw new Error('数据集刚刚产生了新版本，当前页面不是最新快照。请关闭本面板并重新打开后再审核。')
    }
    return session.review_id
  }

  const handleSaveEdits = async () => {
    if (pendingEdits.size === 0) return
    if (diff?.review?.stale) {
      setSaveMsg('保存失败：审核绑定的版本已经过期，请切换到最新版本后重新编辑')
      return
    }
    if (!diff?.pk?.length) {
      setSaveMsg('保存失败：数据集未声明主键，无法可靠定位待编辑行')
      return
    }
    setSaving(true)
    setSaveMsg('')
    setActionError('')
    try {
      const reviewId = await reviewIdForLoadedVersion()
      const edits = Array.from(pendingEdits.entries()).map(([key, { old: oldVal, val: newVal }]) => {
        const [rowIdxStr, col] = key.split('::')
        const rowIdx = Number(rowIdxStr)
        const pkColumns = diff.pk
        const pkValues = pkColumns.map(pkColumn => cellText(rows[rowIdx]?.[pkColumn]))
        const rowPk = diff.row_pk_encoding === 'json-array' || pkColumns.length > 1
          ? JSON.stringify(pkValues)
          : pkValues[0]
        return { row_pk: rowPk, field_name: col, old_value: oldVal, new_value: newVal }
      })
      await curatedApi.saveEdits(reviewId, edits)
      if (!selectedReviewId) {
        setPageOffset(0)
        setSelectedReviewId(reviewId)
      }
      setPendingEdits(new Map())
      setSaveMsg(`已保存 ${edits.length} 处修改`)
      if (selectedReviewId === reviewId) loadDiff()
      // 首次创建 review 时 setSelectedReviewId 会触发绑定该审核版本的重载。
    } catch (error: unknown) {
      setSaveMsg(`保存失败：${errorText(error, '请重试')}`)
    } finally {
      setSaving(false)
    }
  }

  const handleApprove = async () => {
    if (!reviewPending) return
    if (diff?.review?.stale) {
      setActionError('该审核仅对应旧版本，不能批准最新数据。请先切换并审阅最新版本。')
      return
    }
    setApproving(true)
    setActionError('')
    try {
      const reviewId = await reviewIdForLoadedVersion()
      const result = await curatedApi.approveReview(reviewId) as {
        mapping_dispatch?: { status?: string; error?: string }
      }
      setStatus('approved')
      setView('current')
      setDiff(previous => previous?.review
        ? { ...previous, review: { ...previous.review, status: 'approved' } }
        : previous)
      onStatusChange(datasetId, 'approved')
      if (result?.mapping_dispatch?.status === 'failed') {
        setActionError(result.mapping_dispatch.error || '数据已批准，但自动灌入本体失败，请检查映射任务。')
      }
    } catch (error: unknown) {
      setActionError(`批准失败：${errorText(error, '请稍后重试')}`)
    } finally { setApproving(false) }
  }

  const handleReject = async () => {
    if (!reviewPending) return
    if (diff?.review?.stale) {
      setActionError('该审核仅对应旧版本，不能拒绝最新数据。请先切换并审阅最新版本。')
      return
    }
    setApproving(true)
    setActionError('')
    try {
      const reviewId = await reviewIdForLoadedVersion()
      await curatedApi.rejectReview(reviewId)
      setStatus('rejected')
      setView('current')
      setDiff(previous => previous?.review
        ? { ...previous, review: { ...previous.review, status: 'rejected' } }
        : previous)
      onStatusChange(datasetId, 'rejected')
    } catch (error: unknown) {
      setActionError(`拒绝失败：${errorText(error, '请稍后重试')}`)
    } finally { setApproving(false) }
  }

  const handleSwitchToLatestReview = async () => {
    if (hasPending) {
      setActionError('存在未保存的本地修改。请先关闭并放弃这些旧版本修改，再切换到最新版本。')
      return
    }
    setSwitchingReview(true)
    setActionError('')
    try {
      const session = await curatedApi.startReview(datasetId)
      setLoading(true)
      setPageOffset(0)
      setSelectedReviewId(session.review_id)
    } catch (error: unknown) {
      setActionError(`切换最新版本失败：${errorText(error, '请稍后重试')}`)
    } finally {
      setSwitchingReview(false)
    }
  }

  const handleExport = async (format: 'csv' | 'xlsx') => {
    if (reviewPending) return
    setExporting(format)
    setExportMessage('')
    setActionError('')
    try {
      const blob = await curatedApi.export(datasetId, format)
      downloadBlob(blob, `${datasetName}.${format}`)
      setExportMessage(`已导出当前已审核版本的全部 ${(diff?.current?.total ?? 0).toLocaleString()} 行数据`)
    } catch (error: unknown) {
      setActionError(`${format.toUpperCase()} 导出失败：${errorText(error, '请稍后重试')}`)
    } finally {
      setExporting(null)
    }
  }

  const delta = diff?.delta
  const changeCount = delta ? delta.added_count + delta.updated_count + delta.deleted_count : 0
  const primaryKeys = diff?.pk ?? []
  const canEditRows = reviewPending && primaryKeys.length > 0
  const reviewIsStale = Boolean(diff?.review?.stale)
  const pagedView = view === 'current' ? diff?.current : view === 'previous' ? diff?.previous : null
  const cols = useMemo(
    () => columnsFromRows(rows, [...primaryKeys, ...Object.keys(schemaColumns)]),
    [primaryKeys, rows, schemaColumns],
  )
  const pageStart = pagedView?.total ? (pagedView.offset ?? pageOffset) + 1 : 0
  const pageEnd = pagedView
    ? Math.min((pagedView.offset ?? pageOffset) + pagedView.rows.length, pagedView.total)
    : 0
  const totalRows = diff?.current?.total ?? 0
  const pagedTotal = pagedView?.total ?? 0
  const currentPage = pagedTotal ? Math.floor((pagedView?.offset ?? pageOffset) / pageSize) + 1 : 1
  const totalPages = Math.max(1, Math.ceil(pagedTotal / pageSize))

  const switchPage = (nextOffset: number) => {
    if (hasPending || editingCell) {
      setActionError('请先结束编辑并保存或放弃本页修改，再切换审核分页。')
      return
    }
    setPageOffset(Math.max(0, nextOffset))
  }

  const changePageSize = (nextSize: number) => {
    if (hasPending || editingCell) {
      setActionError('请先结束编辑并保存或放弃本页修改，再调整分页大小。')
      return
    }
    setPageSize(nextSize)
    setPageOffset(0)
  }

  const VIEW_TABS: [View, string, number | null][] = [
    ['changes', '变化量', delta ? changeCount : null],
    ['previous', '上一版本全量', diff?.previous?.total ?? null],
    ['current', '本次接受后全量', diff?.current?.total ?? null],
  ]

  return (
    <>
      <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[2px]" onClick={requestClose}>
        <div
          className="z-50 flex h-[78vh] max-h-[760px] min-h-[520px] w-[min(96vw,1440px)] flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)]"
          onClick={e => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="curated-review-title"
        >
          {/* Header */}
          <div className="flex shrink-0 items-start gap-3 border-b border-slate-100 px-5 py-4">
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-700">
              <Table2 size={15} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="curated-review-title" className="truncate text-sm font-semibold text-slate-900">{datasetName}</h2>
                <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${STATUS_STYLE[status] || 'border-gray-200 bg-gray-100 text-gray-600'}`}>
                  {STATUS_ICON(status)}
                  {STATUS_LABEL[status] || status}
                </span>
                <span className="text-xs tabular-nums text-slate-400">
                  v{diff?.current?.version_no ?? '—'} · {totalRows.toLocaleString()} 行
                </span>
                {primaryKeys.length > 0 && (
                  <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700" title="主键列已校验非空">
                    <LockKeyhole size={10} /> 主键：{primaryKeys.join('、')}
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-slate-400">
                来源流水线：{pipelineName} · {reviewPending ? '核对变化并完成审核，当前版本支持行级修改。' : '当前版本仅供查看，可分页浏览或导出全量数据。'}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {!reviewPending && (
                <>
                  <button type="button" onClick={() => void handleExport('csv')} disabled={Boolean(exporting)}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98] disabled:opacity-50">
                    {exporting === 'csv' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />} 导出 CSV
                  </button>
                  <button type="button" onClick={() => void handleExport('xlsx')} disabled={Boolean(exporting)}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98] disabled:opacity-50">
                    {exporting === 'xlsx' ? <Loader2 size={12} className="animate-spin" /> : <FileSpreadsheet size={12} />} 导出 Excel
                  </button>
                </>
              )}
              <button onClick={requestClose} className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30" aria-label="关闭审核详情">
                <X size={16} />
              </button>
            </div>
          </div>

          {/* 待审核时才提供决定与编辑；审核一经完成，详情永久只读。 */}
          {reviewPending ? (
          <div className="flex items-center gap-2 px-6 py-3 border-b bg-amber-50/40 shrink-0 flex-wrap">
            <span className="mr-1 text-xs font-medium text-amber-800">发现新数据，请完成审核</span>
            <button onClick={handleApprove} disabled={approving || saving || Boolean(editingCell) || hasPending || reviewIsStale || loading}
              title={reviewIsStale ? '审核版本已过期，请先切换到最新版本' : (editingCell || hasPending) ? '请先结束编辑并保存或放弃本地修改，再作出审核决定' : '批准当前审核版本'}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
              {approving ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
              通过审核
            </button>
            <button onClick={handleReject} disabled={approving || saving || Boolean(editingCell) || hasPending || reviewIsStale || loading}
              title={reviewIsStale ? '审核版本已过期，请先切换到最新版本' : (editingCell || hasPending) ? '请先结束编辑并保存或放弃本地修改，再作出审核决定' : '拒绝当前审核版本'}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 bg-white text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50">
              {approving ? <Loader2 size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
              拒绝本次数据
            </button>

            <div className="flex-1" />

            {hasPending && (
              <span className="text-xs text-amber-600 flex items-center gap-1">
                <Pencil size={11} /> {pendingEdits.size} 处未保存
              </span>
            )}
            {saveMsg && (
              <span className={`text-xs ${saveMsg.includes('失败') ? 'text-red-500' : 'text-green-600'}`}>{saveMsg}</span>
            )}
            <button onClick={handleSaveEdits} disabled={!hasPending || saving || Boolean(editingCell) || reviewIsStale || !canEditRows}
              title={reviewIsStale ? '审核版本已过期，不能保存修改' : !canEditRows ? '数据集未声明主键，不能进行行级编辑' : editingCell ? '请先按 Enter 或点击空白处结束当前单元格编辑' : '保存本地行编辑'}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-200 bg-white rounded-lg hover:bg-gray-100 disabled:opacity-40">
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              保存编辑
            </button>
          </div>
          ) : (
            <div className="flex shrink-0 items-center gap-2 border-b border-emerald-100 bg-emerald-50/55 px-5 py-3 text-xs text-emerald-800">
              <CheckCircle size={14} className="shrink-0" />
              <span className="font-medium">当前没有新数据需要审核</span>
              <span className="text-emerald-700/75">以下展示最近一次已完成审核的数据版本，内容只读；导出不受当前分页限制。</span>
            </div>
          )}

          {reviewIsStale && (
            <div className="flex shrink-0 items-start gap-3 border-b border-amber-200 bg-amber-50 px-6 py-3 text-xs text-amber-900">
              <AlertTriangle size={15} className="mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold">审核版本已过期，所有审核写操作已禁用</p>
                <p className="mt-0.5 text-amber-700">
                  当前面板审阅的是 v{diff?.current?.version_no ?? '—'}，数据集最新版本为 v{diff?.review?.latest_version_no ?? '—'}。
                  为避免用旧快照批准新数据，请切换后重新核对变化。
                </p>
              </div>
              <button
                type="button"
                onClick={handleSwitchToLatestReview}
                disabled={switchingReview}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-amber-300 bg-white px-3 py-1.5 font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
              >
                {switchingReview ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                切换到最新版本
              </button>
            </div>
          )}

          {actionError && (
            <div className="flex shrink-0 items-start gap-2 border-b border-red-100 bg-red-50 px-6 py-2 text-xs text-red-700">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span className="flex-1">{actionError}</span>
              <button type="button" onClick={() => setActionError('')} className="text-red-400 hover:text-red-700" aria-label="关闭错误提示">×</button>
            </div>
          )}

          {exportMessage && (
            <div className="mx-5 mt-3 flex shrink-0 items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700" role="status">
              <CheckCircle size={13} className="shrink-0" />
              <span className="flex-1">{exportMessage}</span>
              <button type="button" onClick={() => setExportMessage('')} className="text-emerald-500 hover:text-emerald-800" aria-label="关闭导出提示">×</button>
            </div>
          )}

          {schemaLoadError && (
            <div className="flex shrink-0 items-center gap-2 border-b border-amber-100 bg-amber-50 px-6 py-2 text-xs text-amber-700">
              <AlertTriangle size={13} /> {schemaLoadError}
            </div>
          )}

          {!loading && !loadError && diff && reviewPending && primaryKeys.length === 0 && (
            <div className="flex shrink-0 items-start gap-2 border-b border-sky-100 bg-sky-50/70 px-5 py-2.5 text-xs text-sky-800">
              <Info size={13} className="mt-0.5 shrink-0" />
              <span>
                当前流水线采用无主键模式，可以正常审核。系统会按整行内容比较，因此字段变化会显示为“删除旧行 + 新增新行”，无法归并成更新行；为避免修改错行，行级编辑也会关闭。
              </span>
            </div>
          )}

          {/* View switcher */}
          {!loading && !loadError && (
            <div className="flex shrink-0 items-center gap-1 border-b border-slate-100 bg-white px-5 py-2.5">
              {reviewPending ? VIEW_TABS.map(([v, label, count]) => (
                <button key={v} onClick={() => {
                  if ((hasPending || editingCell) && v !== view) {
                    setActionError('请先结束编辑并保存或放弃本页修改，再切换审核视图。')
                    return
                  }
                  setView(v)
                  if (v !== 'changes') setPageOffset(0)
                }}
                  className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                    view === v ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'}`}>
                  {label}{count !== null && <span className={`ml-1 ${view === v ? 'text-gray-300' : 'text-gray-400'}`}>{count}</span>}
                </button>
              )) : (
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-700">
                  <Table2 size={13} className="text-teal-700" /> 已审核全量数据
                </span>
              )}
              {reviewPending && (
                <span className="ml-2 text-[11px] text-gray-400">
                  {view === 'changes' && '相对上一版的新增/更新/删除，聚焦本次改动'}
                  {view === 'previous' && '上一版本完整数据（分页查看，用于对照）'}
                  {view === 'current' && (canEditRows ? '如果接受本次变化，数据集将呈现的完整数据 · 双击非主键单元格可编辑' : '如果接受本次变化，数据集将呈现的完整数据 · 无主键模式下仅可审核查看')}
                </span>
              )}
            </div>
          )}

          {/* Body */}
          <div className={`min-h-0 flex-1 ${reviewPending && view === 'changes' ? 'overflow-auto' : 'overflow-hidden px-5 py-3'}`}>
            {loading ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-400">
                <Loader2 size={16} className="animate-spin" /> 加载中...
              </div>
            ) : loadError ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-red-600">
                <AlertTriangle size={24} className="opacity-70" />
                <p className="font-medium">审核数据加载失败</p>
                <p className="max-w-lg text-xs text-red-500">{loadError}</p>
                <button type="button" onClick={loadDiff} className="mt-1 inline-flex items-center gap-1 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs hover:bg-red-50">
                  <RefreshCw size={12} /> 重新加载
                </button>
              </div>
            ) : !reviewPending ? (
              <ReadonlyTable
                rows={diff?.current?.rows ?? []}
                primaryKeys={primaryKeys}
                startIndex={diff?.current?.offset ?? pageOffset}
                schemaColumns={schemaColumns}
                fillAvailable
              />
            ) : view === 'changes' ? (
              <ChangesView delta={delta ?? null} prevNo={diff?.previous?.version_no ?? null}
                curNo={diff?.current?.version_no ?? null} primaryKeys={primaryKeys} schemaColumns={schemaColumns} />
            ) : view === 'previous' ? (
              diff?.previous?.version_no == null
                ? <div className="grid h-full place-items-center rounded-xl border border-slate-200 bg-slate-50/30 text-sm text-gray-400">这是首个版本，没有上一版可对照。</div>
                : <ReadonlyTable rows={diff.previous.rows} primaryKeys={primaryKeys} startIndex={diff.previous.offset ?? pageOffset} schemaColumns={schemaColumns} fillAvailable />
            ) : (
              /* current — editable */
              rows.length === 0 ? (
                <div className="grid h-full place-items-center rounded-xl border border-slate-200 bg-slate-50/30 text-sm text-gray-400">暂无数据行</div>
              ) : (
                <div className="h-full overflow-auto rounded-xl border border-slate-200 bg-white">
                  <table className="w-full text-xs min-w-max">
                    <thead className="bg-gray-50 border-b sticky top-0">
                      <tr>
                        <th className="px-4 py-2 text-gray-400 font-normal text-left w-12">#</th>
                        {cols.map(col => (
                          <th key={col} className="min-w-[130px] px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap">
                            <ColumnLabel name={col} primaryKeys={primaryKeys} schemaColumn={schemaColumns[col]} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, rowIdx) => (
                        <tr key={rowIdx} className="border-b hover:bg-blue-50/30 transition-colors">
                          <td className="px-4 py-2 text-gray-300 tabular-nums select-none">{(diff?.current?.offset ?? pageOffset) + rowIdx + 1}</td>
                          {cols.map(col => {
                            const key: CellKey = `${rowIdx}::${col}`
                            const isEditing = editingCell === key
                            const isModified = pendingEdits.has(key)
                            const isPrimaryKey = primaryKeys.includes(col)
                            const val = cellVal(rowIdx, col)
                            return (
                              <td key={col}
                                className={`px-4 py-2 max-w-[220px] ${isModified ? 'bg-amber-50' : ''} ${isPrimaryKey ? 'bg-amber-50/50 font-mono' : ''}`}
                                onDoubleClick={() => { if (canEditRows && !isPrimaryKey) startEdit(rowIdx, col) }}
                                title={isPrimaryKey
                                  ? '主键用于稳定识别数据行，不支持在审核阶段直接修改'
                                  : canEditRows ? '双击编辑' : '未声明主键，无法可靠定位行，因此禁用编辑'}>
                                {isEditing ? (
                                  <input ref={editInputRef} defaultValue={val}
                                    onBlur={e => commitEdit(rowIdx, col, e.target.value)}
                                    onKeyDown={e => {
                                      if (e.key === 'Enter') commitEdit(rowIdx, col, e.currentTarget.value)
                                      if (e.key === 'Escape') setEditingCell(null)
                                    }}
                                    className="w-full border border-blue-400 rounded px-1.5 py-0.5 outline-none bg-white text-xs min-w-[80px]"
                                    onClick={e => e.stopPropagation()} />
                                ) : (
                                  <span className={`block truncate cursor-default ${isModified ? 'text-amber-700 font-medium' : 'text-gray-700'}`} title={val}>
                                    {val || <span className="text-gray-300">—</span>}
                                  </span>
                                )}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}
          </div>

          {reviewPending ? (
            <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
              {view === 'changes' ? (
                <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
                  <Info size={12} className="text-teal-700" />
                  变化量基于两个完整版本计算；更新前使用红色，更新后使用绿色，具体变更列会单独标出。
                </span>
              ) : (
                <>
                  <label className="flex items-center gap-1.5 text-xs text-slate-500">
                    每页
                    <select value={pageSize} onChange={event => changePageSize(Number(event.target.value))}
                      className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-teal-500"
                      aria-label="待审核数据每页显示条数">
                      {REVIEW_PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}
                    </select>
                    条
                  </label>
                  <div className="flex items-center gap-1 text-xs text-slate-500">
                    <button type="button" onClick={() => switchPage(pageOffset - pageSize)} disabled={pageOffset <= 0 || loading}
                      aria-label="上一页"
                      className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white transition hover:border-teal-200 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98] disabled:opacity-35">
                      <ChevronLeft size={13} />
                    </button>
                    <span className="min-w-52 text-center tabular-nums">
                      第 {currentPage} / {totalPages} 页 · {pagedTotal ? `${pageStart.toLocaleString()}–${pageEnd.toLocaleString()}` : 0} / {pagedTotal.toLocaleString()} 行
                    </span>
                    <button type="button" onClick={() => switchPage(pageOffset + pageSize)} disabled={!pagedView?.has_more || loading}
                      aria-label="下一页"
                      className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white transition hover:border-teal-200 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98] disabled:opacity-35">
                      <ChevronRight size={13} />
                    </button>
                  </div>
                </>
              )}
              {hasPending && (
                <span className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-medium text-amber-700">
                  {pendingEdits.size} 处修改尚未保存
                </span>
              )}
              <button type="button" onClick={requestClose}
                className="ml-auto h-8 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98]">
                关闭
              </button>
            </div>
          ) : (
            <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
              <label className="flex items-center gap-1.5 text-xs text-slate-500">
                每页
                <select value={pageSize} onChange={event => changePageSize(Number(event.target.value))}
                  className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-teal-500"
                  aria-label="已审核数据每页显示条数">
                  {REVIEW_PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}
                </select>
                条
              </label>
              <div className="flex items-center gap-1 text-xs text-slate-500">
                <button type="button" onClick={() => switchPage(pageOffset - pageSize)} disabled={pageOffset <= 0 || loading}
                  aria-label="上一页"
                  className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white transition hover:border-teal-200 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98] disabled:opacity-35">
                  <ChevronLeft size={13} />
                </button>
                <span className="min-w-52 text-center tabular-nums">
                  第 {currentPage} / {totalPages} 页 · {pagedTotal ? `${pageStart.toLocaleString()}–${pageEnd.toLocaleString()}` : 0} / {pagedTotal.toLocaleString()} 行
                </span>
                <button type="button" onClick={() => switchPage(pageOffset + pageSize)} disabled={!diff?.current?.has_more || loading}
                  aria-label="下一页"
                  className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white transition hover:border-teal-200 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98] disabled:opacity-35">
                  <ChevronRight size={13} />
                </button>
              </div>
              <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                <LockKeyhole size={11} /> 只读模式，不会修改数据
              </span>
              <button type="button" onClick={requestClose}
                className="ml-auto h-8 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 active:scale-[0.98]">
                关闭
              </button>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={showCloseConfirm}
        title="放弃未保存的修改？"
        message={`当前还有 ${pendingEdits.size} 处行级修改尚未保存。关闭后这些本地修改将丢失，已成功保存到审核会话的修改不会受影响。`}
        confirmLabel="放弃修改并关闭"
        onConfirm={() => { setShowCloseConfirm(false); onClose() }}
        onCancel={() => setShowCloseConfirm(false)}
      />
    </>
  )
}

/** 变化量视图：相对上一版的新增/更新/删除 */
function ChangesView({ delta, prevNo, curNo, primaryKeys, schemaColumns }: {
  delta: ReviewDiff['delta']
  prevNo: number | null
  curNo: number | null
  primaryKeys: string[]
  schemaColumns: Record<string, DatasetSchemaColumn>
}) {
  if (!delta) return <div className="p-8 text-center text-sm text-gray-400">暂无版本数据</div>
  const { added_count, updated_count, deleted_count, unchanged_count } = delta
  const noChange = added_count + updated_count + deleted_count === 0

  return (
    <div className="p-4 space-y-4">
      {/* 摘要 */}
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <span className="text-gray-500">
          {prevNo == null ? '首个版本（全部为新增）' : `v${prevNo} → v${curNo}`}
        </span>
        {primaryKeys.length ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-[10px] text-slate-600">
            <KeyRound size={9} /> 按主键 {primaryKeys.join('、')} 识别同一行
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-md bg-sky-50 px-2 py-1 text-[10px] text-sky-700">
            <Info size={9} /> 无主键 · 按整行比较，不单独识别更新
          </span>
        )}
        <span className="inline-flex items-center gap-1 text-green-600"><Plus size={12} />新增 {added_count}</span>
        <span className="inline-flex items-center gap-1 text-amber-600"><Pencil size={11} />更新 {updated_count}</span>
        <span className="inline-flex items-center gap-1 text-red-500"><Minus size={12} />删除 {deleted_count}</span>
        <span className="text-gray-400">未变 {unchanged_count}</span>
        {delta.sample_truncated && <span className="text-gray-400">（每类仅展示部分样本）</span>}
      </div>

      {noChange && <div className="p-6 text-center text-sm text-gray-400">与上一版相比没有数据变化。</div>}

      {updated_count > 0 && (
        <section>
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <h4 className="flex items-center gap-1 text-xs font-medium text-amber-700"><Pencil size={11} />更新的行（{updated_count}）</h4>
            <span className="text-[10px] text-slate-400">每条“更新后”紧跟对应“更新前”；绿色单元格为变更值</span>
          </div>
          <UpdatedRowsTable updates={delta.updated_sample} primaryKeys={primaryKeys} schemaColumns={schemaColumns} />
        </section>
      )}

      {added_count > 0 && (
        <section>
          <h4 className="text-xs font-medium text-green-700 mb-1.5 flex items-center gap-1"><Plus size={12} />新增的行（{added_count}）</h4>
          <div className="overflow-hidden rounded-lg border"><ReadonlyTable rows={delta.added_sample} highlight="add" primaryKeys={primaryKeys} schemaColumns={schemaColumns} /></div>
        </section>
      )}

      {deleted_count > 0 && (
        <section>
          <h4 className="text-xs font-medium text-red-600 mb-1.5 flex items-center gap-1"><Minus size={12} />删除的行（{deleted_count}）</h4>
          <div className="overflow-hidden rounded-lg border"><ReadonlyTable rows={delta.deleted_sample} highlight="del" primaryKeys={primaryKeys} schemaColumns={schemaColumns} /></div>
        </section>
      )}

      {delta.sample_truncated && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            本次变化超过接口单次明细上限，当前已完整展示接口返回的明细，但并非全部 {changeCountText(delta)} 行。
            为避免误判，请结合“上一版全量 / 本次全量”核对；后端需要提供变化明细分页后才能可靠展示全部记录。
          </span>
        </div>
      )}
    </div>
  )
}

function changeCountText(delta: NonNullable<ReviewDiff['delta']>): string {
  return String(delta.added_count + delta.updated_count + delta.deleted_count)
}
