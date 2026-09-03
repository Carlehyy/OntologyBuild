import { useCallback, useEffect, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import {
  AlertCircle, Brain, CheckCircle2, Clock, FileText, Loader2, RefreshCw, Trash2, Upload,
} from 'lucide-react'

import { superAssistantApi, type PalaceFile, type PalaceGraph } from '@/api/superAssistant'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { palaceGraphOption } from './palaceGraphOption'

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

const formatSize = (size: number) => {
  if (!Number.isFinite(size) || size <= 0) return '0 B'
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

export default function MemoryPalaceDialog({ open, onOpenChange }: MemoryPalaceDialogProps) {
  const [files, setFiles] = useState<PalaceFile[]>([])
  const [graph, setGraph] = useState<PalaceGraph | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

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
      setError(err instanceof Error ? err.message : '记忆宫殿加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void refresh()
  }, [open, refresh])

  // 抽取进行中的轻量轮询：全部定格后自动停
  useEffect(() => {
    if (!open) return
    if (!files.some(item => item.status === 'pending' || item.status === 'building')) return
    const timer = setInterval(() => { void refresh() }, 4000)
    return () => clearInterval(timer)
  }, [open, files, refresh])

  const handleUpload = async (list: FileList | null) => {
    if (!list || list.length === 0) return
    setBusy(true)
    try {
      for (const file of Array.from(list)) {
        await superAssistantApi.uploadPalaceFile(file)
      }
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const handleDelete = async (fileId: string) => {
    setBusy(true)
    try {
      await superAssistantApi.deletePalaceFile(fileId)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
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
      setError(err instanceof Error ? err.message : '重建失败')
    } finally {
      setBusy(false)
    }
  }

  const building = files.some(item => item.status === 'pending' || item.status === 'building')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] w-[min(94vw,58rem)] flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Brain size={16} className="text-teal-700" /> 记忆宫殿
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs leading-5 text-[var(--color-text-tertiary)]">
          上传的文档会沉淀为跨会话的长期知识：系统自动抽取实体与关系构建知识图谱，超级助手在所有会话中都可检索引用。
        </p>

        <div className="flex items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={PALACE_ACCEPT}
            className="hidden"
            onChange={event => { void handleUpload(event.target.files) }}
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
            className="flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-white transition-opacity hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] disabled:opacity-60"
            style={{ background: 'var(--color-nav-bg)' }}
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} 上传文档
          </button>
          <button
            type="button"
            onClick={() => { void refresh() }}
            disabled={loading || busy}
            className="flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] disabled:opacity-60"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : undefined} /> 刷新
          </button>
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
          {/* 文件库 */}
          <section aria-label="记忆宫殿文件库" data-testid="super-assistant-palace-files">
            <div className="flex items-center justify-between pb-1.5">
              <h3 className="text-xs font-medium text-[var(--color-text-secondary)]">我的文档（{files.length}）</h3>
            </div>
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
                  return (
                    <li
                      key={file.id}
                      data-palace-file={file.id}
                      className="group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-[var(--color-bg-hover)]"
                    >
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
                        {(file.status === 'failed' || file.status === 'built') && (
                          <button
                            type="button"
                            onClick={() => { void handleRebuild(file.id) }}
                            disabled={busy}
                            title={`重建 ${file.filename} 的知识图谱`}
                            aria-label={`重建 ${file.filename} 的知识图谱`}
                            className="flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-amber-50 hover:text-amber-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 disabled:opacity-50"
                          >
                            <RefreshCw size={12} />
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => { void handleDelete(file.id) }}
                          disabled={busy}
                          title={`删除 ${file.filename}`}
                          aria-label={`删除 ${file.filename}`}
                          className="flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-50"
                        >
                          <Trash2 size={12} />
                        </button>
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          {/* 知识图谱 */}
          <section
            aria-label="记忆宫殿知识图谱"
            data-testid="super-assistant-palace-graph"
            className="flex min-h-[300px] flex-1 flex-col"
          >
            <div className="flex items-center justify-between pb-1.5">
              <h3 className="text-xs font-medium text-[var(--color-text-secondary)]">
                知识图谱
                {graph && graph.totals.entities > 0 && (
                  <span className="ml-1 tabular-nums text-[var(--color-text-tertiary)]">
                    （{graph.totals.entities} 实体 / {graph.totals.relations} 关系{graph.truncated ? '，按提及数展示前 ' + graph.nodes.length : ''}）
                  </span>
                )}
              </h3>
            </div>
            {!graph ? (
              <div className="flex flex-1 items-center justify-center rounded-xl border border-[var(--color-border)] text-xs text-[var(--color-text-tertiary)]">
                <Loader2 size={14} className="mr-1.5 animate-spin" /> 图谱加载中…
              </div>
            ) : !graph.available ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-[var(--color-border)] px-4 py-8 text-center">
                <AlertCircle size={18} className="text-amber-500" />
                <p className="text-xs text-[var(--color-text-tertiary)]">图谱服务暂不可用，文件库不受影响，可稍后刷新重试。</p>
              </div>
            ) : graph.nodes.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center">
                <Brain size={18} className="text-[var(--color-text-tertiary)]" />
                <p className="text-xs text-[var(--color-text-tertiary)]">
                  {files.length === 0
                    ? '上传文档后，这里会长出你的知识图谱。'
                    : '文档抽取完成后，这里会展示知识图谱。'}
                </p>
              </div>
            ) : (
              <div className="h-[360px] overflow-hidden rounded-xl border border-[var(--color-border)]">
                <ReactECharts
                  option={palaceGraphOption(graph)}
                  notMerge
                  style={{ height: '100%', width: '100%' }}
                />
              </div>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  )
}
