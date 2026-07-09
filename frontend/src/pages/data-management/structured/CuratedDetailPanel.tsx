import { useEffect, useRef, useState, useCallback } from 'react'
import {
  X, CheckCircle, AlertTriangle, Clock,
  Save, Trash2, Loader2, Pencil, Lock, Plus, Minus, ArrowRight,
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
  approved:       '已审核',
  rejected:       '已拒绝',
}

const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
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

const cellText = (v: any) => (v === null || v === undefined || v === '') ? '' : String(v)

/** 只读数据表（列取所有行键的并集，保持稳定表头） */
function ReadonlyTable({ rows, highlight }: { rows: Record<string, any>[]; highlight?: 'add' | 'del' }) {
  if (!rows.length) return <div className="p-6 text-center text-xs text-gray-400">无数据</div>
  const cols: string[] = []
  const seen = new Set<string>()
  rows.forEach(r => Object.keys(r).forEach(k => { if (!seen.has(k)) { seen.add(k); cols.push(k) } }))
  const tint = highlight === 'add' ? 'bg-green-50/40' : highlight === 'del' ? 'bg-red-50/40' : ''
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs min-w-max">
        <thead className="bg-gray-50 border-b sticky top-0">
          <tr>
            <th className="px-4 py-2 text-gray-400 font-normal text-left w-12">#</th>
            {cols.map(c => <th key={c} className="px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={`border-b ${tint}`}>
              <td className="px-4 py-2 text-gray-300 tabular-nums select-none">{i + 1}</td>
              {cols.map(c => (
                <td key={c} className="px-4 py-2 max-w-[220px]">
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

export default function CuratedDetailPanel({
  datasetId, datasetName, datasetStatus, pipelineName,
  onClose, onStatusChange, onDeleted,
}: Props) {
  const [status, setStatus] = useState(datasetStatus)
  const [view, setView] = useState<View>('changes')

  const [diff, setDiff] = useState<ReviewDiff | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  // 「本次全量」可编辑视图的状态
  const [rows, setRows] = useState<Record<string, any>[]>([])
  const [cols, setCols] = useState<string[]>([])
  const [editingCell, setEditingCell] = useState<CellKey | null>(null)
  const [pendingEdits, setPendingEdits] = useState<Map<CellKey, { old: string; val: string }>>(new Map())
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  const [approving, setApproving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteErr, setDeleteErr] = useState('')

  const isApproved = status === 'approved'

  const loadDiff = useCallback(() => {
    setLoading(true)
    setLoadError('')
    curatedApi.reviewDiff(datasetId, 500)
      .then(res => {
        const d: any = (res as any)?.data ?? res
        setDiff(d as ReviewDiff)
        const cur: Record<string, any>[] = Array.isArray(d?.current?.rows) ? d.current.rows : []
        setRows(cur)
        setCols(cur.length > 0 ? Object.keys(cur[0]) : [])
      })
      .catch(() => setLoadError('数据加载失败'))
      .finally(() => setLoading(false))
  }, [datasetId])

  useEffect(() => { loadDiff() }, [loadDiff])

  useEffect(() => {
    if (editingCell) editInputRef.current?.focus()
  }, [editingCell])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape' && !editingCell) onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, editingCell])

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

  const handleSaveEdits = async () => {
    if (pendingEdits.size === 0) return
    setSaving(true)
    setSaveMsg('')
    try {
      const session = await curatedApi.startReview(datasetId)
      const reviewId = (session as any).review_id ?? (session as any).data?.review_id
      const edits = Array.from(pendingEdits.entries()).map(([key, { old: oldVal, val: newVal }]) => {
        const [rowIdxStr, col] = key.split('::')
        const rowIdx = Number(rowIdxStr)
        const pkCol = cols[0] ?? 'row'
        return { row_pk: String(rows[rowIdx]?.[pkCol] ?? rowIdx), field_name: col, old_value: oldVal, new_value: newVal }
      })
      await curatedApi.saveEdits(reviewId, edits)
      setPendingEdits(new Map())
      setSaveMsg(`已保存 ${edits.length} 处修改`)
      loadDiff()  // 刷新三视角：编辑也计入「变化量」
    } catch {
      setSaveMsg('保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    try {
      await curatedApi.approve(datasetId)
      setStatus('approved')
      onStatusChange(datasetId, 'approved')
    } finally { setApproving(false) }
  }

  const handleReject = async () => {
    setApproving(true)
    try {
      await curatedApi.reject(datasetId)
      setStatus('rejected')
      onStatusChange(datasetId, 'rejected')
    } finally { setApproving(false) }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteErr('')
    try {
      await curatedApi.delete(datasetId)
      onDeleted(datasetId)
    } catch (e: any) {
      const detail = e?.detail ?? e?.data?.detail
      const raw = (detail && typeof detail === 'object' ? detail.message : detail) || e?.message || '删除失败'
      setDeleteErr(raw === 'Admin required' ? '删除数据集需要管理员权限' : String(raw))
      setDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  const hasPending = pendingEdits.size > 0
  const delta = diff?.delta
  const changeCount = delta ? delta.added_count + delta.updated_count + delta.deleted_count : 0
  const pkOf = (row: Record<string, any>) => (diff?.pk?.length ? diff.pk.map(k => cellText(row[k])).join(' / ') : '')

  const VIEW_TABS: [View, string, number | null][] = [
    ['changes', '变化量', delta ? changeCount : null],
    ['previous', '上一版全量', diff?.previous?.total ?? null],
    ['current', '本次全量', diff?.current?.total ?? null],
  ]

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40 flex items-center justify-center p-6" onClick={onClose}>
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
            <button onClick={onClose} className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-black ml-4 shrink-0">
              <X size={16} />
            </button>
          </div>

          {/* Action bar */}
          <div className="flex items-center gap-2 px-6 py-3 border-b bg-gray-50 shrink-0 flex-wrap">
            {status !== 'approved' && (
              <button onClick={handleApprove} disabled={approving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
                {approving ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
                批准
              </button>
            )}
            {status !== 'rejected' && (
              <button onClick={handleReject} disabled={approving}
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
            <button onClick={handleSaveEdits} disabled={!hasPending || saving}
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
                {view === 'current' && '本次待审全量（叠加行级编辑）· 双击单元格可编辑'}
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
              <div className="p-6 text-sm text-red-400">{loadError}</div>
            ) : view === 'changes' ? (
              <ChangesView delta={delta ?? null} prevNo={diff?.previous?.version_no ?? null}
                curNo={diff?.current?.version_no ?? null} pkOf={pkOf} />
            ) : view === 'previous' ? (
              diff?.previous?.version_no == null
                ? <div className="p-8 text-center text-sm text-gray-400">这是首个版本，没有上一版可对照。</div>
                : <ReadonlyTable rows={diff.previous.rows} />
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
                          <th key={col} className="px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap">{col}</th>
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
                            const val = cellVal(rowIdx, col)
                            return (
                              <td key={col}
                                className={`px-4 py-2 max-w-[220px] ${isModified ? 'bg-amber-50' : ''}`}
                                onDoubleClick={() => startEdit(rowIdx, col)}>
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
    </>
  )
}

/** 变化量视图：相对上一版的新增/更新/删除 */
function ChangesView({ delta, prevNo, curNo, pkOf }: {
  delta: ReviewDiff['delta']
  prevNo: number | null
  curNo: number | null
  pkOf: (row: Record<string, any>) => string
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
          <h4 className="text-xs font-medium text-amber-700 mb-1.5 flex items-center gap-1"><Pencil size={11} />更新的行（{updated_count}）</h4>
          <div className="space-y-1.5">
            {delta.updated_sample.map((u, i) => {
              const changed = Object.keys(u.after).filter(k => cellText(u.before[k]) !== cellText(u.after[k]))
              return (
                <div key={i} className="text-xs border rounded-lg p-2 bg-amber-50/40">
                  {pkOf(u.after) && <div className="text-gray-500 mb-1">主键：<span className="font-mono">{pkOf(u.after)}</span></div>}
                  <div className="flex flex-col gap-0.5">
                    {changed.map(k => (
                      <div key={k} className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-gray-500">{k}:</span>
                        <span className="line-through text-red-400">{cellText(u.before[k]) || '—'}</span>
                        <ArrowRight size={11} className="text-gray-400" />
                        <span className="text-green-700 font-medium">{cellText(u.after[k]) || '—'}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {added_count > 0 && (
        <section>
          <h4 className="text-xs font-medium text-green-700 mb-1.5 flex items-center gap-1"><Plus size={12} />新增的行（{added_count}）</h4>
          <div className="border rounded-lg overflow-hidden"><ReadonlyTable rows={delta.added_sample} highlight="add" /></div>
        </section>
      )}

      {deleted_count > 0 && (
        <section>
          <h4 className="text-xs font-medium text-red-600 mb-1.5 flex items-center gap-1"><Minus size={12} />删除的行（{deleted_count}）</h4>
          <div className="border rounded-lg overflow-hidden"><ReadonlyTable rows={delta.deleted_sample} highlight="del" /></div>
        </section>
      )}
    </div>
  )
}
