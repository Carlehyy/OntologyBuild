import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2, ChevronLeft, ChevronRight, Eye, FileSpreadsheet,
  KeyRound, Loader2, Plus, Table2, Trash2, Upload, X, XCircle,
} from 'lucide-react'
import datasetsApi, {
  FIELD_TYPE_LABELS,
  type CreateTableResult,
  type DatasetImportJob,
  type DatasetImportStatus,
} from '@/api/v2/datasets'
import { CONTRACT_FIELD_TYPES } from '@/api/v2/pipelines'

const PREVIEW_PAGE_SIZES = [20, 50, 100, 200] as const
const ACCEPTED_EXTENSIONS = ['csv', 'xlsx', 'xls']

interface ColDraft {
  name: string
  displayName: string
  type: string
  pk: boolean
  nullable: boolean
}

const emptyColumn = (): ColDraft => ({
  name: '', displayName: '', type: 'string', pk: false, nullable: true,
})

const fileExtension = (file: File) => file.name.split('.').pop()?.toLowerCase() ?? ''
const withoutExtension = (filename: string) => filename.replace(/\.[^.]+$/, '')
const cellText = (value: unknown) => {
  if (value == null) return ''
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 统一建表流程：上传一个表格自动识别，或直接定义空表。 */
export default function CreateTableModal({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (result: CreateTableResult) => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [blankMode, setBlankMode] = useState(false)
  const [name, setName] = useState('')
  const [columns, setColumns] = useState<ColDraft[]>([])
  const [rows, setRows] = useState<string[][]>([])
  const [rowCount, setRowCount] = useState(0)
  const [sheetName, setSheetName] = useState('')
  const [importJobId, setImportJobId] = useState('')
  const [importStatus, setImportStatus] = useState<DatasetImportStatus | null>(null)
  const [parsing, setParsing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewPage, setPreviewPage] = useState(1)
  const [previewPageSize, setPreviewPageSize] = useState<number>(20)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting && !parsing) onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose, parsing, submitting])

  const previewPages = Math.max(1, Math.ceil(rows.length / previewPageSize))
  const previewRows = useMemo(() => rows.slice(
    (previewPage - 1) * previewPageSize,
    previewPage * previewPageSize,
  ), [previewPage, previewPageSize, rows])

  const applyImportJob = useCallback((job: DatasetImportJob) => {
    setImportStatus(job.status)
    if (job.status === 'ready') {
      const nextColumns = job.columns ?? []
      setColumns(nextColumns.map(column => ({
        name: column.name,
        displayName: column.name,
        type: column.type || 'string',
        pk: false,
        nullable: true,
      })))
      setRows((job.preview_rows ?? []).map(row =>
        nextColumns.map(column => cellText(row[column.name]))))
      setRowCount(job.rowcount ?? 0)
      setSheetName(job.sheet_name || '第一个工作表')
      setPreviewPage(1)
      setPreviewOpen(false)
      setParsing(false)
      setSubmitting(false)
      setNotice(`后端已解析第一个工作表「${job.sheet_name || '第一个工作表'}」，共 ${job.rowcount ?? 0} 行`)
      return
    }
    if (job.status === 'completed' && job.result) {
      setParsing(false)
      setSubmitting(false)
      onCreated(job.result)
      return
    }
    if (job.status === 'failed') {
      setParsing(false)
      setSubmitting(false)
      setError(job.error || '表格解析失败')
    }
  }, [onCreated])

  useEffect(() => {
    if (!importJobId || !['queued', 'parsing', 'import_queued', 'importing'].includes(importStatus ?? '')) return
    let cancelled = false
    const poll = async () => {
      try {
        const job = await datasetsApi.importStatus(importJobId)
        if (!cancelled) applyImportJob(job)
      } catch (pollError) {
        if (!cancelled) {
          const detail = (pollError as { detail?: string; message?: string })?.detail
          setParsing(false)
          setSubmitting(false)
          setError(detail || '无法查询后台导入进度，请稍后重试')
        }
      }
    }
    const timer = window.setInterval(() => { void poll() }, 1200)
    void poll()
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [applyImportJob, importJobId, importStatus])

  const uploadForServerParsing = async (selected: File): Promise<boolean> => {
    setParsing(true)
    setError('')
    setNotice('')
    setFile(selected)
    setBlankMode(false)
    setName(withoutExtension(selected.name))
    setRows([])
    setRowCount(0)
    setColumns([])
    setSheetName('')
    setImportJobId('')
    setImportStatus('uploading')
    try {
      const job = await datasetsApi.startImport(selected)
      setImportJobId(job.job_id)
      applyImportJob(job)
      return true
    } catch (parseError) {
      setFile(null)
      setRows([])
      setRowCount(0)
      setColumns([])
      setImportJobId('')
      setImportStatus(null)
      const detail = (parseError as { detail?: string; message?: string })?.detail
      setError(detail || (parseError instanceof Error ? parseError.message : '表格上传失败'))
      setParsing(false)
      return false
    }
  }

  const acceptFiles = (incoming: File[]) => {
    const accepted = incoming.filter(candidate => ACCEPTED_EXTENSIONS.includes(fileExtension(candidate)))
    if (!accepted.length) {
      setError('仅支持 CSV、XLSX 或 XLS 表格')
      return
    }
    if (accepted[0].size > 200 * 1024 * 1024) {
      setError('文件超过 200 MB，请压缩或拆分后再上传')
      return
    }
    const ignoredMessage = incoming.length > 1
      ? `一次只能保留一个表格，已选用「${accepted[0].name}」，其余 ${incoming.length - 1} 个文件已忽略`
      : ''
    void uploadForServerParsing(accepted[0]).then(success => {
      if (success && ignoredMessage) setNotice(ignoredMessage)
    })
  }

  const removeFile = () => {
    setFile(null)
    setRows([])
    setRowCount(0)
    setColumns([])
    setName('')
    setSheetName('')
    setImportJobId('')
    setImportStatus(null)
    setParsing(false)
    setSubmitting(false)
    setPreviewOpen(false)
    setNotice('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const enterBlankMode = () => {
    removeFile()
    setBlankMode(true)
    setColumns([emptyColumn(), emptyColumn()])
  }

  const setColumn = (index: number, patch: Partial<ColDraft>) => setColumns(current =>
    current.map((column, columnIndex) => columnIndex === index ? { ...column, ...patch } : column))

  const addColumn = () => setColumns(current => [...current, emptyColumn()])
  const removeColumn = (index: number) => setColumns(current =>
    current.filter((_, columnIndex) => columnIndex !== index))

  const validate = () => {
    if (!file && !blankMode) return '请上传一个表格，或选择直接定义空表'
    if (file && importStatus !== 'ready') return '请等待后端完成表格解析'
    if (!name.trim()) return '请填写数据集名称'
    const configured = columns.filter(column => column.name.trim())
    if (!configured.length) return '至少需要定义一列'
    const seen = new Set<string>()
    for (const column of configured) {
      const identifier = column.name.trim()
      if (seen.has(identifier)) return `字段标识「${identifier}」重复`
      seen.add(identifier)
    }
    return ''
  }

  const handleSubmit = async () => {
    const validationError = validate()
    if (validationError) { setError(validationError); return }
    const configured = columns.filter(column => column.name.trim())
    const payload = {
      name: name.trim(),
      columns: configured.map(column => ({
        name: column.name.trim(),
        display_name: column.displayName.trim() || column.name.trim(),
        type: column.type,
        nullable: column.pk ? false : column.nullable,
      })),
      primary_key: configured.filter(column => column.pk).map(column => column.name.trim()).join(','),
    }
    setSubmitting(true)
    setError('')
    try {
      const result = file
        ? await datasetsApi.commitImport(importJobId, payload)
        : await datasetsApi.createTable(payload)
      if (file) {
        applyImportJob(result as DatasetImportJob)
      } else {
        onCreated(result as CreateTableResult)
      }
    } catch (submitError) {
      const detail = (submitError as { detail?: string; message?: string })?.detail
      setError(detail || '创建失败，请检查字段设置后重试')
      setSubmitting(false)
    }
  }

  const hasSource = Boolean(file || blankMode)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[2px]">
      <div className="flex max-h-[86vh] w-[min(96vw,1120px)] flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)]"
        role="dialog" aria-modal="true" aria-labelledby="create-table-title">
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-100 px-5 py-4">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-700"><Table2 size={16} /></span>
          <div className="min-w-0 flex-1">
            <h3 id="create-table-title" className="text-sm font-semibold text-slate-900">在线新建表格</h3>
            <p className="mt-1 text-xs text-slate-400">上传一个现有表格自动识别名称与字段，或直接定义一张空表；创建前可统一检查字段契约。</p>
          </div>
          <button type="button" onClick={onClose} disabled={submitting || parsing}
            className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40"
            aria-label="关闭在线新建表格"><X size={16} /></button>
        </header>

        <main className="min-h-0 flex-1 overflow-auto">
          <section className="border-b border-slate-100 px-5 py-4">
            <div className="mb-2 flex items-center justify-between">
              <div><h4 className="text-xs font-semibold text-slate-700">数据来源</h4><p className="mt-0.5 text-[11px] text-slate-400">最多保留一个 CSV 或 Excel 表格</p></div>
              {!blankMode && <button type="button" onClick={enterBlankMode} className="text-xs font-medium text-teal-700 hover:text-teal-900">没有文件，直接定义空表</button>}
            </div>
            <input ref={fileInputRef} type="file" multiple className="hidden" accept=".csv,.xlsx,.xls"
              onChange={event => { acceptFiles(Array.from(event.target.files ?? [])); event.target.value = '' }} />
            {file ? (
              <div className="flex items-center gap-3 rounded-xl bg-slate-50 px-4 py-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-emerald-100 text-emerald-700"><FileSpreadsheet size={18} /></span>
                <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-slate-800">{file.name}</p><p className="mt-0.5 text-[11px] text-slate-400">{formatBytes(file.size)}{sheetName ? ` · 工作表「${sheetName}」 · ${columns.length} 列 · ${rowCount} 行` : ' · 正在等待后端解析'}</p></div>
                <button type="button" onClick={() => fileInputRef.current?.click()} className="text-xs font-medium text-teal-700 hover:text-teal-900">替换</button>
                <button type="button" onClick={removeFile} className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600" aria-label="移除当前表格"><Trash2 size={14} /></button>
              </div>
            ) : blankMode ? (
              <div className="flex items-center gap-3 rounded-xl bg-teal-50/60 px-4 py-3 text-xs text-teal-800"><CheckCircle2 size={15} />已选择直接定义空表，创建后可在“维护数据”中逐行录入。<button type="button" onClick={() => { setBlankMode(false); setColumns([]) }} className="ml-auto font-medium hover:text-teal-950">重新选择</button></div>
            ) : (
              <div onDragEnter={event => { event.preventDefault(); setDragging(true) }} onDragOver={event => event.preventDefault()}
                onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); acceptFiles(Array.from(event.dataTransfer.files)) }}
                className={`flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-5 py-5 text-center transition ${dragging ? 'border-teal-400 bg-teal-50' : 'border-slate-300 bg-slate-50/50 hover:border-teal-300 hover:bg-teal-50/40'}`}
                onClick={() => fileInputRef.current?.click()}>
                {parsing ? <Loader2 size={20} className="mb-2 animate-spin text-teal-700" /> : <Upload size={20} className="mb-2 text-teal-700" />}
                <p className="text-sm font-medium text-slate-700">{parsing ? '正在上传并等待后端解析…' : '拖入表格，或点击选择文件'}</p>
                <p className="mt-1 text-[11px] text-slate-400">同时选择多个文件时只保留第一个，其余文件自动忽略</p>
              </div>
            )}
          </section>

          {hasSource && (
            <>
              <section className="border-b border-slate-100 px-5 py-4">
                <label className="mb-1.5 block text-xs font-semibold text-slate-700">数据集名称</label>
                <input value={name} onChange={event => setName(event.target.value)} placeholder="例如：设备台账"
                  className="h-9 w-full rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-teal-500 focus:ring-2 focus:ring-teal-500/10" />
              </section>

              <section className="border-b border-slate-100 px-5 py-4">
                <div className="mb-2 flex items-end justify-between gap-4">
                  <div><h4 className="text-xs font-semibold text-slate-700">字段设置</h4><p className="mt-0.5 text-[11px] text-slate-400">中文名用于界面展示；字段标识对应文件表头。主键与非空约束会在创建时校验全部数据。</p></div>
                  {blankMode && <button type="button" onClick={addColumn} className="inline-flex h-7 items-center gap-1 text-xs font-medium text-teal-700 hover:text-teal-900"><Plus size={12} />添加字段</button>}
                </div>
                <div className="overflow-x-auto rounded-xl border border-slate-200">
                  <table className="w-full min-w-[780px] text-xs">
                    <thead className="border-b border-slate-200 bg-slate-50 text-slate-500"><tr>
                      <th className="px-3 py-2 text-left font-medium">中文名</th>
                      <th className="px-3 py-2 text-left font-medium">字段标识</th>
                      <th className="w-40 px-3 py-2 text-left font-medium">数据类型</th>
                      <th className="w-20 px-3 py-2 text-center font-medium">非空</th>
                      <th className="w-20 px-3 py-2 text-center font-medium"><span className="inline-flex items-center gap-1"><KeyRound size={10} className="text-amber-500" />主键</span></th>
                      {blankMode && <th className="w-12" />}
                    </tr></thead>
                    <tbody className="divide-y divide-slate-100">{columns.map((column, index) => (
                      <tr key={`${column.name}-${index}`} className="hover:bg-slate-50/60">
                        <td className="p-1.5"><input value={column.displayName} onChange={event => setColumn(index, { displayName: event.target.value })} placeholder="例如：设备名称" className="h-8 w-full min-w-36 rounded-md border border-slate-200 px-2 outline-none focus:border-teal-500" /></td>
                        <td className="p-1.5"><input value={column.name} readOnly={Boolean(file)} onChange={event => setColumn(index, { name: event.target.value })} placeholder="例如：device_name" title={file ? '上传文件的字段标识来自表头，不可在此修改' : ''} className={`h-8 w-full min-w-36 rounded-md border border-slate-200 px-2 font-mono outline-none focus:border-teal-500 ${file ? 'cursor-not-allowed bg-slate-50 text-slate-500' : ''}`} /></td>
                        <td className="p-1.5"><select value={column.type} onChange={event => setColumn(index, { type: event.target.value })} className="h-8 w-full rounded-md border border-slate-200 bg-white px-2 outline-none focus:border-teal-500">{CONTRACT_FIELD_TYPES.map(type => <option key={type} value={type}>{FIELD_TYPE_LABELS[type] ?? type}（{type}）</option>)}</select></td>
                        <td className="p-1.5 text-center"><input type="checkbox" checked={!column.nullable || column.pk} disabled={column.pk} onChange={event => setColumn(index, { nullable: !event.target.checked })} className="accent-teal-600" aria-label={`${column.name} 非空`} /></td>
                        <td className="p-1.5 text-center"><input type="checkbox" checked={column.pk} onChange={event => setColumn(index, { pk: event.target.checked, nullable: event.target.checked ? false : column.nullable })} className="accent-amber-500" aria-label={`${column.name} 主键`} /></td>
                        {blankMode && <td className="p-1.5 text-center"><button type="button" onClick={() => removeColumn(index)} disabled={columns.length <= 1} className="grid h-7 w-7 place-items-center rounded-md text-slate-300 transition hover:bg-red-50 hover:text-red-600 disabled:opacity-30"><Trash2 size={12} /></button></td>}
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </section>

              {file && (
                <section className="px-5 py-4">
                  <button type="button" onClick={() => setPreviewOpen(current => !current)} disabled={!rows.length} className="inline-flex h-8 items-center gap-1.5 text-xs font-medium text-teal-700 hover:text-teal-900 disabled:opacity-40"><Eye size={13} />{previewOpen ? '收起数据样例' : `查看数据样例（前 ${rows.length} 行）`}</button>
                  {previewOpen && <div className="mt-2 overflow-hidden rounded-xl border border-slate-200">
                    <div className="max-h-72 overflow-auto"><table className="min-w-max text-xs"><thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50"><tr><th className="px-3 py-2 text-left font-medium text-slate-400">#</th>{columns.map(column => <th key={column.name} className="whitespace-nowrap px-3 py-2 text-left font-medium text-slate-600">{column.displayName && column.displayName !== column.name ? `${column.displayName}（${column.name}）` : column.name}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{previewRows.map((row, rowIndex) => <tr key={`${previewPage}-${rowIndex}`} className="hover:bg-slate-50/60"><td className="px-3 py-2 tabular-nums text-slate-300">{(previewPage - 1) * previewPageSize + rowIndex + 1}</td>{columns.map((column, columnIndex) => <td key={column.name} className="whitespace-nowrap px-3 py-2 text-slate-600" title={row[columnIndex]}>{row[columnIndex]}</td>)}</tr>)}</tbody></table></div>
                    <div className="flex items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/70 px-3 py-2 text-xs text-slate-500"><label className="flex items-center gap-1">每页<select value={previewPageSize} onChange={event => { setPreviewPageSize(Number(event.target.value)); setPreviewPage(1) }} className="h-7 rounded-md border border-slate-200 bg-white px-1.5 outline-none">{PREVIEW_PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}</select>条</label><button type="button" onClick={() => setPreviewPage(page => Math.max(1, page - 1))} disabled={previewPage <= 1} className="grid h-7 w-7 place-items-center rounded-md border border-slate-200 bg-white disabled:opacity-30"><ChevronLeft size={12} /></button><span className="min-w-20 text-center tabular-nums">{previewPage} / {previewPages}</span><button type="button" onClick={() => setPreviewPage(page => Math.min(previewPages, page + 1))} disabled={previewPage >= previewPages} className="grid h-7 w-7 place-items-center rounded-md border border-slate-200 bg-white disabled:opacity-30"><ChevronRight size={12} /></button></div>
                  </div>}
                </section>
              )}
            </>
          )}
        </main>

        {(error || notice) && <div className={`mx-5 mb-3 flex shrink-0 items-start gap-2 rounded-lg border px-3 py-2 text-xs ${error ? 'border-red-200 bg-red-50 text-red-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>{error ? <XCircle size={13} className="mt-0.5 shrink-0" /> : <CheckCircle2 size={13} className="mt-0.5 shrink-0" />}<span className="flex-1">{error || notice}</span><button type="button" onClick={() => { setError(''); setNotice('') }}><X size={12} /></button></div>}

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
          <button type="button" onClick={onClose} disabled={submitting} className="h-8 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:bg-slate-100 disabled:opacity-40">取消</button>
          <button type="button" onClick={() => void handleSubmit()} disabled={submitting || parsing || !hasSource} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-teal-700 px-4 text-xs font-medium text-white transition hover:bg-teal-800 disabled:opacity-40">{submitting ? <Loader2 size={12} className="animate-spin" /> : file ? <Upload size={12} /> : <Table2 size={12} />}{file ? '导入并创建' : '创建空表'}</button>
        </footer>
      </div>
    </div>
  )
}
