import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  X, Loader2, KeyRound, Plus, Trash2, Undo2, Save,
  ChevronLeft, ChevronRight, CheckCircle2, XCircle, Pencil,
} from 'lucide-react'
import datasetsApi, { FIELD_TYPE_LABELS, type DatasetOverviewItem } from '@/api/v2/datasets'
import ConfirmDialog from '@/components/ConfirmDialog'

const PAGE_SIZE = 50

type CellMap = Record<string, string>
interface EditableRow {
  orig: CellMap   // 加载时的原值（update/delete 用它定位，主键改值也不丢定位）
  cur: CellMap
  deleted: boolean
}

const toStr = (v: unknown) => {
  if (v == null) return ''
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch { return String(v) }
  }
  return String(v)
}

/** 人工数据集在线维护：声明主键契约 + 改单元格/增删行（保存即生成新版本） */
export default function DatasetEditorModal({ dataset, onClose, onSaved }: {
  dataset: DatasetOverviewItem
  onClose: () => void
  onSaved: () => void
}) {
  const [pk, setPk] = useState(dataset.primary_key || '')
  const pkCols = useMemo(() => pk.split(',').map(s => s.trim()).filter(Boolean), [pk])

  const [columns, setColumns] = useState<string[]>([])
  const [colTypes, setColTypes] = useState<Record<string, string>>({})
  const [rows, setRows] = useState<EditableRow[]>([])
  const [inserts, setInserts] = useState<CellMap[]>([])
  const [versionNo, setVersionNo] = useState(0)
  const [totalRows, setTotalRows] = useState(0)
  const [offset, setOffset] = useState(0)

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [confirmClose, setConfirmClose] = useState(false)

  // 主键契约声明面板
  const [pkPanelOpen, setPkPanelOpen] = useState(false)
  const [pkDraft, setPkDraft] = useState<string[]>([])
  const [declaring, setDeclaring] = useState(false)

  const dirty = inserts.length > 0 || rows.some(r =>
    r.deleted || columns.some(c => r.cur[c] !== r.orig[c]))

  const loadPage = async (off: number) => {
    setLoading(true)
    setError('')
    try {
      const res = await datasetsApi.previewLatest(dataset.id, PAGE_SIZE, off)
      const cols = res.columns ?? []
      setColumns(cols)
      setRows((res.rows ?? []).map(raw => {
        const m: CellMap = {}
        cols.forEach(c => { m[c] = toStr(raw[c]) })
        return { orig: { ...m }, cur: { ...m }, deleted: false }
      }))
      setInserts([])
      setVersionNo(res.version_no ?? 0)
      setTotalRows(res.total_rows ?? 0)
      setOffset(off)
    } catch {
      setError('数据加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void Promise.resolve().then(() => {
      loadPage(0)
      if (!dataset.primary_key) setPkPanelOpen(true)
    })
    // 列类型提示：在线建表返回声明类型，上传的数据集返回按数据推断的类型
    datasetsApi.schema(dataset.id)
      .then(r => setColTypes(Object.fromEntries((r.columns ?? []).map(c => [c.name, c.type]))))
      .catch(() => setColTypes({}))

  }, [dataset.id])

  const switchPage = (off: number) => {
    if (dirty) { setError('有未保存的修改，请先保存或放弃后再翻页'); return }
    loadPage(Math.max(0, off))
  }

  const requestClose = useCallback(() => {
    if (saving) return
    if (dirty) {
      setConfirmClose(true)
      return
    }
    onClose()
  }, [dirty, onClose, saving])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !confirmClose) requestClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [confirmClose, requestClose])

  /* ── 主键契约 ── */
  const togglePkCol = (col: string) =>
    setPkDraft(d => d.includes(col) ? d.filter(c => c !== col) : [...d, col])

  const handleDeclare = async () => {
    if (pkDraft.length === 0) return
    setDeclaring(true)
    setError('')
    try {
      const res = await datasetsApi.declareContract(dataset.id, pkDraft.join(','))
      setPk(res.primary_key)
      setPkPanelOpen(false)
      setInfo(`主键契约已声明：${res.primary_key}（已在 ${res.rows_validated} 行数据上校验通过）`)
      onSaved()
    } catch (err: unknown) {
      const er = err as { detail?: string; message?: string }
      setError(typeof er?.detail === 'string' ? er.detail : (er?.message || '声明失败'))
    } finally {
      setDeclaring(false)
    }
  }

  /* ── 行编辑 ── */
  const setCell = (i: number, col: string, val: string) =>
    setRows(rs => rs.map((r, idx) => idx === i ? { ...r, cur: { ...r.cur, [col]: val } } : r))

  const toggleDelete = (i: number) =>
    setRows(rs => rs.map((r, idx) => idx === i ? { ...r, deleted: !r.deleted } : r))

  const addInsert = () =>
    setInserts(list => [...list, Object.fromEntries(columns.map(c => [c, ''])) as CellMap])

  const setInsertCell = (i: number, col: string, val: string) =>
    setInserts(list => list.map((r, idx) => idx === i ? { ...r, [col]: val } : r))

  const removeInsert = (i: number) =>
    setInserts(list => list.filter((_, idx) => idx !== i))

  const pickKey = (orig: CellMap): Record<string, string> =>
    Object.fromEntries(pkCols.map(c => [c, orig[c] ?? '']))

  const handleSave = async () => {
    const updates = rows
      .filter(r => !r.deleted && columns.some(c => r.cur[c] !== r.orig[c]))
      .map(r => ({
        key: pickKey(r.orig),
        values: Object.fromEntries(columns.filter(c => r.cur[c] !== r.orig[c]).map(c => [c, r.cur[c]])),
      }))
    const deletes = rows.filter(r => r.deleted).map(r => ({ key: pickKey(r.orig) }))
    const insertOps = inserts
      .filter(row => Object.values(row).some(v => v !== ''))
      .map(values => ({ values }))
    if (!updates.length && !deletes.length && !insertOps.length) {
      setInfo('没有需要保存的修改')
      return
    }
    setSaving(true)
    setError('')
    setInfo('')
    try {
      const res = await datasetsApi.editRows(dataset.id, {
        base_version_no: versionNo,
        updates, deletes, inserts: insertOps,
      })
      setInfo(`已保存为 v${res.version_no}（共 ${res.rowcount} 行：修改 ${res.updated}、新增 ${res.inserted}、删除 ${res.deleted}）`)
      onSaved()
      await loadPage(offset)
    } catch (err: unknown) {
      const er = err as { detail?: string | { message?: string }; message?: string }
      const msg = typeof er?.detail === 'object'
        ? (er.detail?.message || '数据已被更新，请刷新后重试')
        : (typeof er?.detail === 'string' ? er.detail : (er?.message || '保存失败'))
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  const canEdit = pkCols.length > 0
  const pageEnd = Math.min(offset + rows.length, totalRows)

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-[min(96vw,1100px)] h-[85vh] flex flex-col overflow-hidden"
        role="dialog" aria-modal="true" aria-labelledby="dataset-editor-title">
        {/* 头部 */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b">
          <Pencil size={16} className="text-[var(--color-nav-bg)] shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 id="dataset-editor-title" className="font-semibold text-sm truncate">{dataset.name}</h3>
              <span className="text-xs text-gray-400">v{versionNo} · 共 {totalRows} 行</span>
              {pkCols.length > 0 ? (
                <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border bg-amber-50 text-amber-700 border-amber-200"
                  title="主键契约：定位行、保证实例身份稳定；被本体映射绑定后锁定">
                  <KeyRound size={10} /> 主键：{pk}
                </span>
              ) : (
                <span className="text-xs px-1.5 py-0.5 rounded border bg-gray-50 text-gray-500 border-gray-200">
                  未声明主键
                </span>
              )}
              {pkCols.length > 0 && (
                <button onClick={() => { setPkDraft(pkCols); setPkPanelOpen(v => !v) }}
                  className="text-xs text-gray-400 hover:text-gray-700 underline">
                  修改
                </button>
              )}
            </div>
            <p className="text-xs text-gray-400 mt-0.5">
              {canEdit
                ? '点击单元格直接修改；保存后整体生成一个新版本，历史可回溯'
                : '先声明主键契约才能修改/删除行（否则行没有稳定身份）；声明后还可直接被本体映射灌入'}
            </p>
          </div>
          <button onClick={requestClose} disabled={saving}
            className="text-gray-400 hover:text-gray-700 shrink-0 disabled:opacity-40"
            aria-label="关闭数据维护窗口"><X size={16} /></button>
        </div>

        {/* 主键契约声明面板 */}
        {pkPanelOpen && (
          <div className="px-5 py-3 border-b bg-amber-50/50 space-y-2">
            <p className="text-xs text-gray-600">
              <KeyRound size={11} className="inline mr-1 text-amber-600" />
              选择能唯一标识一行的列作为主键（可多选组成复合主键，将校验：列存在 · 值非空 · 组合唯一）
            </p>
            <div className="flex items-center gap-1.5 flex-wrap">
              {columns.map(c => {
                const idx = pkDraft.indexOf(c)
                return (
                  <button key={c} onClick={() => togglePkCol(c)}
                    className={`text-xs px-2 py-1 rounded-lg border font-mono transition-colors ${
                      idx >= 0
                        ? 'bg-amber-100 border-amber-300 text-amber-800'
                        : 'bg-white border-gray-200 text-gray-600 hover:border-amber-300'
                    }`}>
                    {idx >= 0 && <span className="mr-1 text-[10px] bg-amber-600 text-white rounded px-1">{idx + 1}</span>}
                    {c}
                  </button>
                )
              })}
              {columns.length === 0 && <span className="text-xs text-gray-400">暂无数据列（先上传数据）</span>}
            </div>
            <div className="flex items-center gap-2">
              <button onClick={handleDeclare} disabled={declaring || pkDraft.length === 0}
                className="flex items-center gap-1 text-xs px-3 py-1.5 bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90 disabled:opacity-50">
                {declaring ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />}
                声明主键{pkDraft.length > 0 ? `（${pkDraft.join(', ')}）` : ''}
              </button>
              {pk && (
                <button onClick={() => setPkPanelOpen(false)} className="text-xs text-gray-400 hover:text-gray-600">取消</button>
              )}
            </div>
          </div>
        )}

        {/* 提示条 */}
        {(error || info) && (
          <div className={`mx-5 mt-3 px-3 py-2 rounded-lg border text-xs flex items-center gap-2 ${
            error ? 'bg-red-50 border-red-200 text-red-600' : 'bg-green-50 border-green-200 text-green-700'
          }`}>
            {error ? <XCircle size={13} className="shrink-0" /> : <CheckCircle2 size={13} className="shrink-0" />}
            <span className="flex-1">{error || info}</span>
            <button onClick={() => { setError(''); setInfo('') }} className="text-gray-400 hover:text-gray-600"><X size={12} /></button>
          </div>
        )}

        {/* 数据表 */}
        <div className="flex-1 overflow-auto px-5 py-3">
          {loading ? (
            <p className="text-xs text-gray-400 flex items-center gap-1 p-6"><Loader2 size={13} className="animate-spin" /> 加载数据...</p>
          ) : columns.length === 0 ? (
            <p className="text-xs text-gray-400 p-6">暂无数据。请先通过「上传新版本」导入表格。</p>
          ) : (
            <table className="text-xs w-full min-w-max border-separate border-spacing-0">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="bg-gray-50 border-b border-r px-2 py-1.5 text-gray-400 font-normal w-8">#</th>
                  {columns.map(c => (
                    <th key={c} className="bg-gray-50 border-b px-3 py-1.5 text-left font-medium text-gray-600 whitespace-nowrap">
                      {c}
                      {pkCols.includes(c) && <KeyRound size={9} className="inline ml-1 text-amber-500" />}
                      {colTypes[c] && colTypes[c] !== 'string' && (
                        <span className="ml-1 px-1 py-px bg-gray-100 rounded text-[10px] text-gray-400 font-normal"
                          title={`该列类型：${colTypes[c]}（${FIELD_TYPE_LABELS[colTypes[c]] ?? colTypes[c]}）`}>
                          {FIELD_TYPE_LABELS[colTypes[c]] ?? colTypes[c]}
                        </span>
                      )}
                    </th>
                  ))}
                  <th className="bg-gray-50 border-b px-2 py-1.5 w-10" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className={r.deleted ? 'opacity-40' : ''}>
                    <td className="border-b border-r px-2 py-0.5 text-gray-300 text-center">{offset + i + 1}</td>
                    {columns.map(c => {
                      const changed = r.cur[c] !== r.orig[c]
                      return (
                        <td key={c} className="border-b p-0">
                          <input
                            value={r.cur[c]}
                            disabled={!canEdit || r.deleted}
                            onChange={e => setCell(i, c, e.target.value)}
                            className={`w-full min-w-[7rem] px-3 py-1.5 bg-transparent outline-none focus:bg-blue-50/60 disabled:cursor-not-allowed ${
                              changed ? 'bg-amber-50 text-amber-900' : 'text-gray-700'
                            } ${r.deleted ? 'line-through' : ''}`}
                          />
                        </td>
                      )
                    })}
                    <td className="border-b px-1 text-center">
                      {canEdit && (
                        <button onClick={() => toggleDelete(i)}
                          className={`p-1 rounded ${r.deleted ? 'text-gray-400 hover:text-gray-700' : 'text-gray-300 hover:text-red-500 hover:bg-red-50'}`}
                          title={r.deleted ? '撤销删除' : '删除该行'}
                          aria-label={r.deleted ? `撤销删除第 ${offset + i + 1} 行` : `删除第 ${offset + i + 1} 行`}>
                          {r.deleted ? <Undo2 size={12} /> : <Trash2 size={12} />}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {inserts.map((row, i) => (
                  <tr key={`ins-${i}`} className="bg-green-50/40">
                    <td className="border-b border-r px-2 py-0.5 text-green-500 text-center">新</td>
                    {columns.map(c => (
                      <td key={c} className="border-b p-0">
                        <input
                          value={row[c]}
                          placeholder={pkCols.includes(c) ? '主键，必填' : ''}
                          onChange={e => setInsertCell(i, c, e.target.value)}
                          className="w-full min-w-[7rem] px-3 py-1.5 bg-transparent outline-none focus:bg-blue-50/60 text-gray-700 placeholder:text-amber-400/70"
                        />
                      </td>
                    ))}
                    <td className="border-b px-1 text-center">
                      <button onClick={() => removeInsert(i)} className="p-1 rounded text-gray-300 hover:text-red-500" title="移除该新增行">
                        <Trash2 size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* 底部操作栏 */}
        <div className="flex items-center gap-2 px-5 py-3 border-t bg-gray-50/60">
          <button onClick={addInsert} disabled={loading || columns.length === 0}
            className="flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg bg-white hover:bg-gray-50 text-gray-600 disabled:opacity-50">
            <Plus size={12} /> 新增行
          </button>
          <div className="flex items-center gap-1 ml-2 text-xs text-gray-400">
            <button onClick={() => switchPage(offset - PAGE_SIZE)} disabled={offset === 0 || loading}
              className="p-1 rounded hover:bg-gray-100 disabled:opacity-30"><ChevronLeft size={13} /></button>
            <span>{totalRows === 0 ? '0' : `${offset + 1}–${pageEnd}`} / {totalRows} 行</span>
            <button onClick={() => switchPage(offset + PAGE_SIZE)} disabled={pageEnd >= totalRows || loading}
              className="p-1 rounded hover:bg-gray-100 disabled:opacity-30"><ChevronRight size={13} /></button>
          </div>
          {dirty && <span className="text-xs text-amber-600">有未保存的修改</span>}
          <div className="ml-auto flex items-center gap-2">
            <button onClick={requestClose} disabled={saving}
              className="text-xs px-3 py-1.5 border rounded-lg bg-white hover:bg-gray-50 text-gray-600 disabled:opacity-40">
              关闭
            </button>
            <button onClick={handleSave} disabled={saving || !dirty}
              className="flex items-center gap-1.5 text-xs px-3.5 py-1.5 bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90 disabled:opacity-50">
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              保存为新版本
            </button>
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={confirmClose}
        title="放弃未保存的修改？"
        message="当前页面有尚未保存的新增、修改或删除。关闭后这些改动将无法恢复。"
        confirmLabel="放弃修改并关闭"
        onCancel={() => setConfirmClose(false)}
        onConfirm={onClose}
      />
    </div>
  )
}
