import { Fragment, useEffect, useRef, useState, useCallback } from 'react'
import {
  X, CheckCircle, AlertTriangle, Clock,
  Save, Trash2, Loader2, Pencil, Lock, Plus, Minus, KeyRound, RefreshCw,
} from 'lucide-react'
import curatedApi, { type ReviewDiff } from '@/api/v2/curated'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Props {
  datasetId: string
  datasetName: string
  datasetStatus: string
  pipelineName: string
  onClose: () => void
  onStatusChange: (id: string, status: string) => void
  onDeleted: (id: string) => void
}

const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审核',
  pending:        '待审核',
  in_review:      '审核中',
  approved:       '已审核',
  rejected:       '已拒绝',
}

const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending:        'bg-yellow-50 text-yellow-700 border-yellow-200',
  in_review:      'bg-blue-50 text-blue-700 border-blue-200',
  approved:       'bg-green-50 text-green-700 border-green-200',
  rejected:       'bg-red-50 text-red-600 border-red-200',
}

const STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={13} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={13} className="text-red-400" />
  return <Clock size={13} className="text-yellow-400" />
}

type CellKey = `${number}::${string}`
type View = 'changes' | 'previous' | 'current'
type DataRow = Record<string, unknown>

const cellText = (value: unknown) => (value === null || value === undefined || value === '') ? '' : String(value)

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

function ColumnLabel({ name, primaryKeys }: { name: string; primaryKeys: string[] }) {
  const isPrimaryKey = primaryKeys.includes(name)
  return (
    <span className="inline-flex items-center gap-1.5">
      <span>{name}</span>
      {isPrimaryKey && (
        <span className="inline-flex items-center gap-0.5 rounded border border-amber-200 bg-amber-50 px-1 py-0.5 text-[9px] font-medium text-amber-700" title="主键列：用于逐行识别新增、更新和删除">
          <KeyRound size={8} /> 主键
        </span>
      )}
    </span>
  )
}

/** 只读数据表（列取所有行键的并集，保持稳定表头） */
function ReadonlyTable({ rows, highlight, primaryKeys = [] }: {
  rows: DataRow[]
  highlight?: 'add' | 'del'
  primaryKeys?: string[]
}) {
  if (!rows.length) return <div className="p-6 text-center text-xs text-gray-400">无数据</div>
  const cols = columnsFromRows(rows, primaryKeys)
  const tint = highlight === 'add' ? 'bg-green-50/40' : highlight === 'del' ? 'bg-red-50/40' : ''
  return (
    <div className="max-w-full overflow-x-auto">
      <table className="w-full text-xs min-w-max">
        <thead className="bg-gray-50 border-b sticky top-0">
          <tr>
            <th className="px-4 py-2 text-gray-400 font-normal text-left w-12">#</th>
            {cols.map(c => (
              <th key={c} className="min-w-[130px] px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap">
                <ColumnLabel name={c} primaryKeys={primaryKeys} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={`border-b ${tint}`}>
              <td className="px-4 py-2 text-gray-300 tabular-nums select-none">{i + 1}</td>
              {cols.map(c => (
                <td key={c} className={`px-4 py-2 max-w-[260px] ${primaryKeys.includes(c) ? 'bg-amber-50/50 font-mono' : ''}`}>
                  <span className="block truncate text-gray-700" title={cellText(row[c])}>
                    {cellText(row[c]) || <span className="text-gray-300">—</span>}
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
  updates, primaryKeys,
}: {
  updates: Array<{ before: DataRow; after: DataRow }>
  primaryKeys: string[]
}) {
  if (!updates.length) return <div className="p-6 text-center text-xs text-gray-400">无更新行</div>
  const allRows = updates.flatMap(update => [update.before, update.after])
  const columns = columnsFromRows(allRows, primaryKeys)

  return (
    <div className="max-w-full overflow-x-auto rounded-lg border border-amber-200 bg-white">
      <table className="w-full min-w-max text-xs">
        <thead className="sticky top-0 z-10 border-b bg-slate-50">
          <tr>
            <th className="sticky left-0 z-20 min-w-[92px] border-r bg-slate-50 px-3 py-2 text-left font-medium text-slate-500">对比行</th>
            {columns.map(column => (
              <th key={column} className="min-w-[140px] whitespace-nowrap px-4 py-2 text-left font-medium text-slate-600">
                <ColumnLabel name={column} primaryKeys={primaryKeys} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {updates.map((update, index) => {
            const changedColumns = new Set(
              columns.filter(column => cellText(update.before[column]) !== cellText(update.after[column])),
            )
            return (
              <Fragment key={`${primaryKeys.map(key => cellText(update.after[key])).join('::') || 'row'}-${index}`}>
                <tr className="border-t border-amber-200 bg-red-50/35">
                  <td className="sticky left-0 z-[5] border-r border-amber-100 bg-red-50 px-3 py-2 align-top">
                    <span className="inline-flex rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700">更新前</span>
                    <span className="ml-1 text-[10px] text-slate-400">#{index + 1}</span>
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
                  <td className="sticky left-0 z-[5] border-r border-amber-100 bg-emerald-50 px-3 py-2 align-top">
                    <span className="inline-flex rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">更新后</span>
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
  onClose, onStatusChange, onDeleted,
}: Props) {
  const [status, setStatus] = useState(datasetStatus)
  const [view, setView] = useState<View>('changes')

  const [diff, setDiff] = useState<ReviewDiff | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [selectedReviewId, setSelectedReviewId] = useState<string | undefined>()
  const [switchingReview, setSwitchingReview] = useState(false)

  // 「本次全量」可编辑视图的状态
  const [rows, setRows] = useState<DataRow[]>([])
  const [cols, setCols] = useState<string[]>([])
  const [editingCell, setEditingCell] = useState<CellKey | null>(null)
  const [pendingEdits, setPendingEdits] = useState<Map<CellKey, { old: string; val: string }>>(new Map())
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const [actionError, setActionError] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  const [approving, setApproving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [deleteErr, setDeleteErr] = useState('')

  const isApproved = status === 'approved'
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
    curatedApi.reviewDiff(datasetId, 500, selectedReviewId)
      .then(res => {
        const wrapped = res as ReviewDiff & { data?: ReviewDiff }
        const d = wrapped.data ?? res
        setDiff(d)
        const cur: DataRow[] = Array.isArray(d.current?.rows) ? d.current.rows : []
        setRows(cur)
        setCols(columnsFromRows(cur, Array.isArray(d?.pk) ? d.pk : []))
      })
      .catch((error: unknown) => setLoadError(errorText(error, '数据加载失败，请稍后重试')))
      .finally(() => setLoading(false))
  }, [datasetId, selectedReviewId])

  useEffect(() => { void Promise.resolve().then(loadDiff) }, [loadDiff])

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
    // 已决定的 review 是不可变审计记录。撤回批准/改判时必须为同一数据版本
    // 新建 pending review，不能把已 approved/rejected 的记录拿来重复决定。
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
      if (!selectedReviewId) setSelectedReviewId(reviewId)
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
    if (diff?.review?.stale) {
      setActionError('该审核仅对应旧版本，不能批准最新数据。请先切换并审阅最新版本。')
      return
    }
    setApproving(true)
    setActionError('')
    try {
      const reviewId = await reviewIdForLoadedVersion()
      await curatedApi.approveReview(reviewId)
      setStatus('approved')
      setDiff(previous => previous?.review
        ? { ...previous, review: { ...previous.review, status: 'approved' } }
        : previous)
      onStatusChange(datasetId, 'approved')
    } catch (error: unknown) {
      setActionError(`批准失败：${errorText(error, '请稍后重试')}`)
    } finally { setApproving(false) }
  }

  const handleReject = async () => {
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
      setSelectedReviewId(session.review_id)
    } catch (error: unknown) {
      setActionError(`切换最新版本失败：${errorText(error, '请稍后重试')}`)
    } finally {
      setSwitchingReview(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteErr('')
    try {
      await curatedApi.delete(datasetId)
      onDeleted(datasetId)
    } catch (error: unknown) {
      const e = error as { detail?: unknown; data?: { detail?: unknown }; message?: unknown }
      const detail = e?.detail ?? e?.data?.detail
      const nestedMessage = detail && typeof detail === 'object' && 'message' in detail
        ? (detail as { message?: unknown }).message
        : undefined
      const raw = typeof detail === 'string'
        ? detail
        : typeof nestedMessage === 'string'
          ? nestedMessage
          : typeof e?.message === 'string' ? e.message : '删除失败'
      setDeleteErr(raw === 'Admin required' ? '删除数据集需要管理员权限' : String(raw))
      setDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  const delta = diff?.delta
  const changeCount = delta ? delta.added_count + delta.updated_count + delta.deleted_count : 0
  const primaryKeys = diff?.pk ?? []
  const canEditRows = primaryKeys.length > 0
  const reviewIsStale = Boolean(diff?.review?.stale)

  const VIEW_TABS: [View, string, number | null][] = [
    ['changes', '变化量', delta ? changeCount : null],
    ['previous', '上一版全量', diff?.previous?.total ?? null],
    ['current', '本次全量', diff?.current?.total ?? null],
  ]

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40 flex items-center justify-center p-6" onClick={requestClose}>
        <div
          className="bg-white rounded-xl shadow-2xl z-50 flex flex-col w-full max-w-5xl"
          style={{ maxHeight: 'calc(100vh - 3rem)' }}
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start justify-between px-6 py-4 border-b shrink-0">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="font-semibold text-base truncate">{datasetName}</h2>
                <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[status] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                  {STATUS_ICON(status)}
                  {STATUS_LABEL[status] || status}
                </span>
              </div>
              <p className="text-xs text-gray-400 mt-0.5">来自管道：{pipelineName}</p>
            </div>
            <button onClick={requestClose} className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-black ml-4 shrink-0" aria-label="关闭审核详情">
              <X size={16} />
            </button>
          </div>

          {/* Action bar */}
          <div className="flex items-center gap-2 px-6 py-3 border-b bg-gray-50 shrink-0 flex-wrap">
            {status !== 'approved' && (
              <button onClick={handleApprove} disabled={approving || saving || Boolean(editingCell) || hasPending || reviewIsStale || loading}
                title={reviewIsStale ? '审核版本已过期，请先切换到最新版本' : (editingCell || hasPending) ? '请先结束编辑并保存或放弃本地修改，再作出审核决定' : '批准当前审核版本'}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
                {approving ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
                批准
              </button>
            )}
            {status !== 'rejected' && (
              <button onClick={handleReject} disabled={approving || saving || Boolean(editingCell) || hasPending || reviewIsStale || loading}
                title={reviewIsStale ? '审核版本已过期，请先切换到最新版本' : (editingCell || hasPending) ? '请先结束编辑并保存或放弃本地修改，再作出审核决定' : '拒绝当前审核版本'}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50">
                {approving ? <Loader2 size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
                {isApproved ? '撤回审批（拒绝）' : '拒绝'}
              </button>
            )}

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
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border rounded-lg hover:bg-gray-100 disabled:opacity-40">
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              保存编辑
            </button>
            <button
              onClick={() => { setDeleteErr(''); setShowDeleteConfirm(true) }}
              disabled={isApproved}
              title={isApproved ? '已审批通过的数据集不可删除；如需删除请先「撤回审批（拒绝）」' : '删除整个数据集及其全部历史版本'}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 text-red-500 rounded-lg hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed">
              {isApproved ? <Lock size={12} /> : <Trash2 size={12} />} 删除
            </button>
          </div>

          {deleteErr && (
            <div className="px-6 py-2 text-xs text-red-600 bg-red-50 border-b border-red-100 shrink-0">{deleteErr}</div>
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

          {/* View switcher */}
          {!loading && !loadError && (
            <div className="flex items-center gap-1 px-6 py-2 border-b bg-white shrink-0">
              {VIEW_TABS.map(([v, label, count]) => (
                <button key={v} onClick={() => setView(v)}
                  className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                    view === v ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'}`}>
                  {label}{count !== null && <span className={`ml-1 ${view === v ? 'text-gray-300' : 'text-gray-400'}`}>{count}</span>}
                </button>
              ))}
              <span className="ml-2 text-[11px] text-gray-400">
                {view === 'changes' && '相对上一版的新增/更新/删除，聚焦本次改动'}
                {view === 'previous' && '上一版本的完整数据（对照用）'}
                {view === 'current' && (canEditRows ? '本次待审全量（叠加行级编辑）· 双击非主键单元格可编辑' : '本次待审全量 · 未声明主键，无法可靠进行行级编辑')}
              </span>
            </div>
          )}

          {/* Body */}
          <div className="flex-1 overflow-auto">
            {loading ? (
              <div className="flex items-center justify-center h-48 text-gray-400 text-sm gap-2">
                <Loader2 size={16} className="animate-spin" /> 加载中...
              </div>
            ) : loadError ? (
              <div className="flex h-48 flex-col items-center justify-center gap-2 p-6 text-center text-sm text-red-600">
                <AlertTriangle size={24} className="opacity-70" />
                <p className="font-medium">审核数据加载失败</p>
                <p className="max-w-lg text-xs text-red-500">{loadError}</p>
                <button type="button" onClick={loadDiff} className="mt-1 inline-flex items-center gap-1 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs hover:bg-red-50">
                  <RefreshCw size={12} /> 重新加载
                </button>
              </div>
            ) : view === 'changes' ? (
              <ChangesView delta={delta ?? null} prevNo={diff?.previous?.version_no ?? null}
                curNo={diff?.current?.version_no ?? null} primaryKeys={primaryKeys} />
            ) : view === 'previous' ? (
              diff?.previous?.version_no == null
                ? <div className="p-8 text-center text-sm text-gray-400">这是首个版本，没有上一版可对照。</div>
                : <ReadonlyTable rows={diff.previous.rows} primaryKeys={primaryKeys} />
            ) : (
              /* current — editable */
              rows.length === 0 ? (
                <div className="p-8 text-center text-sm text-gray-400">暂无数据行</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs min-w-max">
                    <thead className="bg-gray-50 border-b sticky top-0">
                      <tr>
                        <th className="px-4 py-2 text-gray-400 font-normal text-left w-12">#</th>
                        {cols.map(col => (
                          <th key={col} className="min-w-[130px] px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap">
                            <ColumnLabel name={col} primaryKeys={primaryKeys} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, rowIdx) => (
                        <tr key={rowIdx} className="border-b hover:bg-blue-50/30 transition-colors">
                          <td className="px-4 py-2 text-gray-300 tabular-nums select-none">{rowIdx + 1}</td>
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
        </div>
      </div>

      <ConfirmDialog
        open={showDeleteConfirm}
        title="删除数据集"
        message={`确认删除「${datasetName}」？将永久删除该数据集及其全部历史版本，不可恢复。若已被流水线或本体映射引用，删除会被拦截。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />

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
function ChangesView({ delta, prevNo, curNo, primaryKeys }: {
  delta: ReviewDiff['delta']
  prevNo: number | null
  curNo: number | null
  primaryKeys: string[]
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
          <UpdatedRowsTable updates={delta.updated_sample} primaryKeys={primaryKeys} />
        </section>
      )}

      {added_count > 0 && (
        <section>
          <h4 className="text-xs font-medium text-green-700 mb-1.5 flex items-center gap-1"><Plus size={12} />新增的行（{added_count}）</h4>
          <div className="overflow-hidden rounded-lg border"><ReadonlyTable rows={delta.added_sample} highlight="add" primaryKeys={primaryKeys} /></div>
        </section>
      )}

      {deleted_count > 0 && (
        <section>
          <h4 className="text-xs font-medium text-red-600 mb-1.5 flex items-center gap-1"><Minus size={12} />删除的行（{deleted_count}）</h4>
          <div className="overflow-hidden rounded-lg border"><ReadonlyTable rows={delta.deleted_sample} highlight="del" primaryKeys={primaryKeys} /></div>
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
