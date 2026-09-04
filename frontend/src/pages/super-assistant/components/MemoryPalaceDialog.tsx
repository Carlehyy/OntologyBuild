import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertCircle, Brain, CheckCircle2, Clock, Eye, FileArchive, FileText, Loader2, Pencil, RefreshCw, Trash2, Upload, X,
} from 'lucide-react'

import {
  superAssistantApi,
  type PalaceFile,
  type PalaceFilePreview,
  type PalaceGraph,
  type PalaceImportResult,
} from '@/api/superAssistant'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import PalaceGraphPanel from './PalaceGraphPanel'

interface MemoryPalaceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** 与会话附件（SuperAssistantPage ATTACH_ACCEPT）保持同一白名单口径 */
const PALACE_ACCEPT = '.csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.doc,.pptx,.ppt,.md,.txt'

const STATUS_META: Record<string, { label: string; className: string }> = {
  pending: { label: '待抽取', className: 'bg-slate-100 text-slate-500' },
  building: { label: '抽取中', className: 'bg-amber-50 text-amber-600' },
  built: { label: '已建图', className: 'bg-teal-50 text-teal-700' },
  failed: { label: '失败', className: 'bg-red-50 text-red-600' },
}

type PalaceTab = 'files' | 'graph'

interface PalaceEditorState {
  fileId: string
  filename: string
  loading: boolean
  /** 非空表示该文件不可在线编辑（格式不支持 / 内容超限），仅展示原因 */
  unsupported: string | null
  draft: string
  initial: string
  saving: boolean
}

interface PalacePreviewState {
  fileId: string
  loading: boolean
  data: PalaceFilePreview | null
}

const formatSize = (size: number) => {
  if (!Number.isFinite(size) || size <= 0) return '0 B'
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

/** apiClientV2 失败时 reject 的是响应体（FastAPI {detail}），优先透出其中文 detail（如 429 配额提示） */
const palaceError = (err: unknown, fallback: string): string => {
  const candidate = err as { detail?: unknown; message?: unknown } | null
  const text = candidate?.detail ?? candidate?.message
  return typeof text === 'string' && text ? text : fallback
}

export default function MemoryPalaceDialog({ open, onOpenChange }: MemoryPalaceDialogProps) {
  const { toast } = useToast()
  const [activeTab, setActiveTab] = useState<PalaceTab>('files')
  const [files, setFiles] = useState<PalaceFile[]>([])
  const [graph, setGraph] = useState<PalaceGraph | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<PalacePreviewState | null>(null)
  const [editor, setEditor] = useState<PalaceEditorState | null>(null)
  const [importResult, setImportResult] = useState<PalaceImportResult | null>(null)
  const [showSkipped, setShowSkipped] = useState(false)
  const [replaceTarget, setReplaceTarget] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const zipInputRef = useRef<HTMLInputElement>(null)
  const replaceInputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [fileRows, graphData] = await Promise.all([
        superAssistantApi.palaceFiles(),
        superAssistantApi.palaceGraph(),
      ])
      setFiles(fileRows)
      setGraph(graphData)
      setError(null)
    } catch (err) {
      setError(palaceError(err, '记忆宫殿加载失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void refresh()
  }, [open, refresh])

  // 弹窗关闭时清掉一次性视图状态，重开时从「文件库」页签起步
  useEffect(() => {
    if (!open) {
      setActiveTab('files')
      setError(null)
      setPreview(null)
      setEditor(null)
      setImportResult(null)
      setShowSkipped(false)
      setReplaceTarget(null)
    }
  }, [open])

  // 抽取进行中的轻量轮询：全部定格后自动停
  useEffect(() => {
    if (!open) return
    if (!files.some(item => item.status === 'pending' || item.status === 'building')) return
    const timer = setInterval(() => { void refresh() }, 4000)
    return () => clearInterval(timer)
  }, [open, files, refresh])

  const editorDirty = editor !== null && !editor.loading && editor.unsupported === null && editor.draft !== editor.initial

  /** 编辑器有未保存修改时先确认；返回是否允许离开（同时关闭编辑器） */
  const requestLeaveEditor = () => {
    if (!editor) return true
    if (editorDirty && !window.confirm('当前编辑内容尚未保存，确定离开吗？离开后未保存的修改将丢失。')) return false
    setEditor(null)
    return true
  }

  const handleTabChange = (value: PalaceTab) => {
    if (value === activeTab) return
    if (!requestLeaveEditor()) return
    setActiveTab(value)
  }

  const handleDialogOpenChange = (next: boolean) => {
    if (!next && !requestLeaveEditor()) return
    onOpenChange(next)
  }

  const handlePanelError = useCallback((err: unknown, fallback: string) => {
    setError(palaceError(err, fallback))
  }, [])

  const handleUpload = async (list: FileList | null) => {
    if (!list || list.length === 0) return
    setBusy(true)
    try {
      for (const file of Array.from(list)) {
        await superAssistantApi.uploadPalaceFile(file)
      }
      await refresh()
    } catch (err) {
      setError(palaceError(err, '上传失败'))
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const handleImportZip = async (list: FileList | null) => {
    const archive = list?.[0]
    if (!archive) return
    setBusy(true)
    try {
      const result = await superAssistantApi.importPalaceZip(archive)
      setImportResult(result)
      setShowSkipped(false)
      await refresh()
    } catch (err) {
      setError(palaceError(err, 'ZIP 导入失败'))
    } finally {
      setBusy(false)
      if (zipInputRef.current) zipInputRef.current.value = ''
    }
  }

  const handleReplace = async (list: FileList | null) => {
    const file = list?.[0]
    const targetId = replaceTarget
    if (!file || !targetId) return
    setBusy(true)
    try {
      await superAssistantApi.replacePalaceFile(targetId, file)
      toast({ tone: 'success', title: '文件已替换', description: '新文件将自动重新抽取实体与关系。' })
      await refresh()
    } catch (err) {
      setError(palaceError(err, '替换失败'))
    } finally {
      setBusy(false)
      setReplaceTarget(null)
      if (replaceInputRef.current) replaceInputRef.current.value = ''
    }
  }

  const handleDelete = async (fileId: string) => {
    setBusy(true)
    try {
      await superAssistantApi.deletePalaceFile(fileId)
      await refresh()
    } catch (err) {
      setError(palaceError(err, '删除失败'))
    } finally {
      setBusy(false)
    }
  }

  const handleRebuild = async (fileId: string) => {
    setBusy(true)
    try {
      await superAssistantApi.rebuildPalaceFile(fileId)
      await refresh()
    } catch (err) {
      setError(palaceError(err, '重建失败'))
    } finally {
      setBusy(false)
    }
  }

  const handlePreview = async (file: PalaceFile) => {
    if (!requestLeaveEditor()) return
    if (preview?.fileId === file.id) {
      setPreview(null)
      return
    }
    setPreview({ fileId: file.id, loading: true, data: null })
    try {
      const data = await superAssistantApi.palaceFilePreview(file.id)
      setPreview({ fileId: file.id, loading: false, data })
    } catch (err) {
      setPreview(null)
      setError(palaceError(err, '预览加载失败'))
    }
  }

  const handleEdit = async (file: PalaceFile) => {
    if (!requestLeaveEditor()) return
    setPreview(null)
    setEditor({ fileId: file.id, filename: file.filename, loading: true, unsupported: null, draft: '', initial: '', saving: false })
    try {
      const data = await superAssistantApi.palaceFilePreview(file.id)
      setEditor(prev => {
        if (!prev || prev.fileId !== file.id) return prev
        if (!data.previewable) return { ...prev, loading: false, unsupported: '该格式暂不支持在线编辑，仅 md/txt 可编辑。' }
        if (data.truncated) return { ...prev, loading: false, unsupported: '文件内容超出在线编辑上限，请本地编辑后重新上传。' }
        return { ...prev, loading: false, draft: data.content, initial: data.content }
      })
    } catch (err) {
      setEditor(prev => (!prev || prev.fileId !== file.id)
        ? prev
        : { ...prev, loading: false, unsupported: palaceError(err, '内容加载失败') })
    }
  }

  const handleSaveContent = async () => {
    if (!editor || editor.loading || editor.unsupported !== null) return
    setEditor(prev => prev ? { ...prev, saving: true } : prev)
    try {
      await superAssistantApi.updatePalaceFileContent(editor.fileId, editor.draft)
      setEditor(null)
      toast({ tone: 'success', title: '已保存，图谱重建已排队', description: '新内容将自动重新抽取实体与关系。' })
      await refresh()
    } catch (err) {
      setEditor(prev => prev ? { ...prev, saving: false } : prev)
      setError(palaceError(err, '保存失败'))
    }
  }

  const building = files.some(item => item.status === 'pending' || item.status === 'building')

  return (
    <Dialog open={open} onOpenChange={handleDialogOpenChange}>
      <DialogContent className="flex max-h-[85dvh] w-[min(94vw,58rem)] flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Brain size={16} className="text-teal-700" /> 记忆宫殿
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs leading-5 text-[var(--color-text-tertiary)]">
          上传的文档会沉淀为跨会话的长期知识：系统自动抽取实体与关系构建知识图谱，超级助手在所有会话中都可检索引用。
        </p>

        <div className="flex items-center justify-between gap-2">
          <div role="tablist" aria-label="记忆宫殿视图" className="flex w-fit items-center gap-0.5 rounded-lg bg-[var(--color-bg-hover)] p-0.5">
            {([['files', '文件库'], ['graph', '知识图谱']] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                id={`palace-tab-${value}`}
                aria-selected={activeTab === value}
                aria-controls={`palace-panel-${value}`}
                onClick={() => handleTabChange(value)}
                className={`flex h-7 items-center rounded-md px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] ${
                  activeTab === value
                    ? 'bg-white text-[var(--color-text-primary)] shadow-sm'
                    : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {building && (
            <span className="flex items-center gap-1 text-xs text-amber-600">
              <Loader2 size={12} className="animate-spin" /> 图谱构建中…
            </span>
          )}
        </div>

        {error && (
          <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{error}</p>
        )}

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
          {activeTab === 'graph' ? (
            <div role="tabpanel" id="palace-panel-graph" aria-labelledby="palace-tab-graph" className="flex min-h-0 flex-1 flex-col">
              <PalaceGraphPanel
                graph={graph}
                loading={loading}
                hasFiles={files.length > 0}
                onRefresh={() => { void refresh() }}
                onError={handlePanelError}
              />
            </div>
          ) : (
            <section
              role="tabpanel"
              id="palace-panel-files"
              aria-labelledby="palace-tab-files"
              aria-label="记忆宫殿文件库"
              data-testid="super-assistant-palace-files"
            >
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <input
                  ref={inputRef}
                  data-testid="palace-file-input"
                  type="file"
                  multiple
                  accept={PALACE_ACCEPT}
                  className="hidden"
                  onChange={event => { void handleUpload(event.target.files) }}
                />
                <input
                  ref={zipInputRef}
                  data-testid="palace-zip-input"
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={event => { void handleImportZip(event.target.files) }}
                />
                <input
                  ref={replaceInputRef}
                  data-testid="palace-replace-input"
                  type="file"
                  accept={PALACE_ACCEPT}
                  className="hidden"
                  onChange={event => { void handleReplace(event.target.files) }}
                />
                <Button size="sm" onClick={() => inputRef.current?.click()} loading={busy}>
                  上传文档
                </Button>
                <Button size="sm" variant="outline" onClick={() => zipInputRef.current?.click()} disabled={busy}>
                  <FileArchive size={13} /> 导入 ZIP
                </Button>
                <Button size="sm" variant="outline" onClick={() => { void refresh() }} disabled={loading || busy} loading={loading}>
                  刷新
                </Button>
                <span className="text-xs text-[var(--color-text-tertiary)]">我的文档（{files.length}）</span>
              </div>

              {importResult && (
                <div
                  role="status"
                  data-testid="palace-import-result"
                  className="mb-2 flex flex-col gap-1 rounded-lg bg-teal-50 px-3 py-2 text-xs text-teal-800"
                >
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={13} className="shrink-0" />
                    <span className="flex-1">导入 {importResult.created.length} 个，跳过 {importResult.skipped.length} 个</span>
                    {importResult.skipped.length > 0 && (
                      <button
                        type="button"
                        onClick={() => setShowSkipped(value => !value)}
                        aria-expanded={showSkipped}
                        className="rounded-md px-1.5 py-0.5 text-[11px] text-teal-700 transition-colors hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                      >
                        {showSkipped ? '收起跳过原因' : '查看跳过原因'}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setImportResult(null)}
                      aria-label="关闭导入结果"
                      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-teal-600 transition-colors hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                    >
                      <X size={12} />
                    </button>
                  </div>
                  {showSkipped && importResult.skipped.length > 0 && (
                    <ul className="ml-5 list-disc space-y-0.5 text-[11px] text-teal-700">
                      {importResult.skipped.map(item => (
                        <li key={item.filename}>{item.filename}：{item.reason}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {files.length === 0 ? (
                <div className="flex flex-col items-center gap-1 rounded-xl border border-dashed border-[var(--color-border)] px-4 py-6 text-center">
                  <FileText size={18} className="text-[var(--color-text-tertiary)]" />
                  <p className="text-xs text-[var(--color-text-tertiary)]">
                    还没有上传文档。上传后自动抽取实体与关系，生成属于你的知识图谱。
                  </p>
                </div>
              ) : (
                <ul className="space-y-1">
                  {files.map(file => {
                    const status = STATUS_META[file.status] ?? STATUS_META.pending
                    const extracting = file.status === 'pending' || file.status === 'building'
                    return (
                      <li
                        key={file.id}
                        data-palace-file={file.id}
                        className="group rounded-lg transition-colors hover:bg-[var(--color-bg-hover)]"
                      >
                        <div className="flex items-center gap-2 px-2 py-1.5">
                          <FileText size={14} className="shrink-0 text-[var(--color-text-tertiary)]" />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm text-[var(--color-text-primary)]" title={file.error || file.filename}>
                              {file.filename}
                            </p>
                            <p className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                              {formatSize(file.size)} · 解析 {file.extractedChars} 字符
                              {file.status === 'built' && ` · ${file.entityCount} 实体 / ${file.relationCount} 关系`}
                              {file.status === 'failed' && ' · 抽取失败，可重建'}
                            </p>
                          </div>
                          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${status.className}`}>
                            {file.status === 'building' && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
                            {file.status === 'built' && <CheckCircle2 size={10} className="mr-0.5 inline" />}
                            {file.status === 'failed' && <AlertCircle size={10} className="mr-0.5 inline" />}
                            {file.status === 'pending' && <Clock size={10} className="mr-0.5 inline" />}
                            {status.label}
                          </span>
                          <span className="flex shrink-0 items-center gap-0.5">
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => { void handlePreview(file) }}
                              disabled={busy}
                              title={`预览 ${file.filename}`}
                              aria-label={`预览 ${file.filename}`}
                              className="h-6 w-6 text-slate-400 hover:text-[var(--color-text-primary)]"
                            >
                              <Eye size={12} />
                            </Button>
                            {file.editable && (
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                onClick={() => { void handleEdit(file) }}
                                disabled={busy || extracting}
                                title={extracting ? '抽取完成后才能编辑' : `编辑 ${file.filename}`}
                                aria-label={`编辑 ${file.filename}`}
                                className="h-6 w-6 text-slate-400 hover:text-[var(--color-text-primary)]"
                              >
                                <Pencil size={12} />
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => {
                                setReplaceTarget(file.id)
                                replaceInputRef.current?.click()
                              }}
                              disabled={busy || extracting}
                              title={extracting ? '抽取完成后才能替换' : `用新文件替换 ${file.filename}`}
                              aria-label={`替换 ${file.filename}`}
                              className="h-6 w-6 text-slate-400 hover:text-[var(--color-text-primary)]"
                            >
                              <Upload size={12} />
                            </Button>
                            {(file.status === 'failed' || file.status === 'built') && (
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                onClick={() => { void handleRebuild(file.id) }}
                                disabled={busy}
                                title={`重建 ${file.filename} 的知识图谱`}
                                aria-label={`重建 ${file.filename} 的知识图谱`}
                                className="h-6 w-6 text-slate-400 hover:bg-amber-50 hover:text-amber-600"
                              >
                                <RefreshCw size={12} />
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => { void handleDelete(file.id) }}
                              disabled={busy}
                              title={`删除 ${file.filename}`}
                              aria-label={`删除 ${file.filename}`}
                              className="h-6 w-6 text-slate-400 hover:bg-red-50 hover:text-red-600"
                            >
                              <Trash2 size={12} />
                            </Button>
                          </span>
                        </div>

                        {preview?.fileId === file.id && (
                          <div data-testid="palace-file-preview" className="mx-2 mb-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-2">
                            {preview.loading ? (
                              <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
                                <Loader2 size={12} className="animate-spin" /> 预览加载中…
                              </p>
                            ) : !preview.data || !preview.data.previewable ? (
                              <p className="text-xs text-[var(--color-text-tertiary)]">该格式暂不支持预览，仅文本类内容可预览。</p>
                            ) : (
                              <>
                                <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[var(--color-bg-elevated)] p-2 font-mono text-xs leading-5 text-[var(--color-text-primary)]">
                                  {preview.data.content}
                                </pre>
                                {preview.data.truncated && (
                                  <p className="mt-1 text-[11px] text-amber-600">内容已截断，仅展示前 60000 字符。</p>
                                )}
                              </>
                            )}
                          </div>
                        )}

                        {editor?.fileId === file.id && (
                          <div data-testid="palace-file-editor" className="mx-2 mb-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-2">
                            {editor.loading ? (
                              <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
                                <Loader2 size={12} className="animate-spin" /> 正在加载文件内容…
                              </p>
                            ) : editor.unsupported ? (
                              <p className="text-xs text-[var(--color-text-tertiary)]">{editor.unsupported}</p>
                            ) : (
                              <>
                                <textarea
                                  aria-label={`编辑 ${editor.filename} 内容`}
                                  value={editor.draft}
                                  spellCheck={false}
                                  onChange={event => setEditor(prev => prev ? { ...prev, draft: event.target.value } : prev)}
                                  className="min-h-[240px] w-full resize-y rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-2 font-mono text-xs leading-5 text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
                                />
                                <div className="mt-2 flex items-center gap-2">
                                  <Button
                                    size="sm"
                                    onClick={() => { void handleSaveContent() }}
                                    loading={editor.saving}
                                    disabled={!editorDirty}
                                  >
                                    保存并重建图谱
                                  </Button>
                                  <Button variant="outline" size="sm" onClick={requestLeaveEditor} disabled={editor.saving}>
                                    取消
                                  </Button>
                                  {editorDirty && <span className="text-[11px] text-amber-600">有未保存修改</span>}
                                </div>
                              </>
                            )}
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
