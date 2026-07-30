import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown, ChevronRight, Download, FileArchive, FileText, Folder,
  FolderOpen, Loader2, RefreshCw, Trash2, Upload, X,
} from 'lucide-react'
import {
  downloadStewardFile, getStewardFileBlob, stewardApi,
  type StewardArtifact,
} from '@/api/steward'

type WorkspacePreview = {
  kind: 'empty' | 'loading' | 'text' | 'image' | 'pdf' | 'unsupported' | 'error'
  text?: string
  url?: string
  message?: string
  truncated?: boolean
}

interface StewardTreeDirectory {
  name: string
  path: string
  directories: Map<string, StewardTreeDirectory>
  files: StewardArtifact[]
}

type StewardTreeRow =
  | { type: 'directory'; key: string; name: string; path: string; depth: number; childCount: number }
  | { type: 'file'; key: string; file: StewardArtifact; name: string; depth: number }

const stewardDirectoryPaths = (files: StewardArtifact[]) => {
  const paths = new Set<string>()
  files.forEach(file => {
    const parts = (file.relativePath || file.filename).split('/').filter(Boolean)
    parts.slice(0, -1).forEach((_, index) => paths.add(parts.slice(0, index + 1).join('/')))
  })
  return paths
}

const buildStewardTreeRows = (files: StewardArtifact[], collapsed: Set<string>): StewardTreeRow[] => {
  const root: StewardTreeDirectory = { name: '', path: '', directories: new Map(), files: [] }
  files.forEach(file => {
    const parts = (file.relativePath || file.filename).split('/').filter(Boolean)
    let cursor = root
    parts.slice(0, -1).forEach(part => {
      const path = cursor.path ? `${cursor.path}/${part}` : part
      if (!cursor.directories.has(part)) {
        cursor.directories.set(part, { name: part, path, directories: new Map(), files: [] })
      }
      cursor = cursor.directories.get(part)!
    })
    cursor.files.push(file)
  })

  const rows: StewardTreeRow[] = []
  const visit = (directory: StewardTreeDirectory, depth: number) => {
    [...directory.directories.values()]
      .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
      .forEach(child => {
        rows.push({
          type: 'directory',
          key: `dir-${child.path}`,
          name: child.name,
          path: child.path,
          depth,
          childCount: child.directories.size + child.files.length,
        })
        if (!collapsed.has(child.path)) visit(child, depth + 1)
      })
    directory.files
      .sort((left, right) => left.filename.localeCompare(right.filename, 'zh-CN'))
      .forEach(file => rows.push({
        type: 'file',
        key: file.id,
        file,
        name: (file.relativePath || file.filename).split('/').pop() || file.filename,
        depth,
      }))
  }
  visit(root, 0)
  return rows
}

export default function WorkspaceModal({ conversationId, onClose, formatBytes, errorText }: {
  conversationId: string
  onClose: () => void
  formatBytes: (value: number) => string
  errorText: (error: unknown, fallback: string) => string
}) {
  const [files, setFiles] = useState<StewardArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [preview, setPreview] = useState<WorkspacePreview>({ kind: 'empty' })
  const inputRef = useRef<HTMLInputElement>(null)
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set())
  const knownDirectoryPaths = useRef(new Set<string>())

  const allDirectoryPaths = useMemo(() => stewardDirectoryPaths(files), [files])
  const treeRows = useMemo(() => buildStewardTreeRows(files, collapsedDirs), [files, collapsedDirs])

  useEffect(() => {
    const known = knownDirectoryPaths.current
    setCollapsedDirs(current => {
      const next = new Set([...current].filter(path => allDirectoryPaths.has(path)))
      allDirectoryPaths.forEach(path => {
        if (!known.has(path)) next.add(path)
      })
      return next
    })
    knownDirectoryPaths.current = new Set(allDirectoryPaths)
  }, [allDirectoryPaths])

  const reload = useCallback(() => {
    setLoading(true)
    return stewardApi.files(conversationId)
      .then(rows => {
        const nextFiles = Array.isArray(rows) ? rows : []
        setFiles(nextFiles)
        setSelectedId(current => current && nextFiles.some(file => file.id === current)
          ? current
          : nextFiles[0]?.id || null)
      })
      .catch(err => setError(errorText(err, '会话文件加载失败')))
      .finally(() => setLoading(false))
  }, [conversationId])

  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0)
    return () => window.clearTimeout(timer)
  }, [reload])

  const selectedFile = files.find(file => file.id === selectedId) || null

  useEffect(() => {
    if (!selectedFile) {
      setPreview({ kind: 'empty' })
      return
    }
    let cancelled = false
    let objectUrl = ''
    const loadPreview = async () => {
      setPreview({ kind: 'loading' })
      try {
        const mime = (selectedFile.mimeType || '').toLowerCase()
        if (mime.startsWith('image/')) {
          const blob = await getStewardFileBlob(conversationId, selectedFile.id)
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setPreview({ kind: 'image', url: objectUrl })
          return
        }
        if (mime === 'application/pdf' || selectedFile.filename.toLowerCase().endsWith('.pdf')) {
          const blob = await getStewardFileBlob(conversationId, selectedFile.id)
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setPreview({ kind: 'pdf', url: objectUrl })
          return
        }
        const result = await stewardApi.filePreview(conversationId, selectedFile.id)
        if (cancelled) return
        if (result.content) {
          setPreview({ kind: 'text', text: result.content, truncated: result.truncated })
        } else {
          setPreview({
            kind: 'unsupported',
            message: selectedFile.extractError || '此文件暂无可用的在线预览，可下载后使用本地应用打开。',
          })
        }
      } catch (err: unknown) {
        if (!cancelled) setPreview({ kind: 'error', message: errorText(err, '文件预览加载失败') })
      }
    }
    void loadPreview()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [conversationId, selectedFile])

  const uploadFiles = async (selected: FileList | null) => {
    if (!selected?.length) return
    setUploading(true); setError('')
    try {
      for (const file of Array.from(selected)) await stewardApi.uploadFile(conversationId, file)
      await reload()
    } catch (err: unknown) {
      setError(errorText(err, '上传失败'))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const remove = async (id: string) => {
    setError('')
    try {
      await stewardApi.deleteFile(conversationId, id)
      await reload()
    } catch (err: unknown) {
      setError(errorText(err, '删除失败'))
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-5" onClick={onClose}>
      <div className="flex h-[76vh] min-h-[520px] w-[1040px] max-w-full flex-col overflow-hidden rounded-2xl bg-white shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-5 py-3.5">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold"><FolderOpen size={16} className="text-teal-600" />会话隔离空间</h3>
            <p className="mt-0.5 text-[11px] text-gray-400">上传件和网页下载件仅在此会话可见；打包不包含浏览器登录态</p>
          </div>
          <button aria-label="关闭会话文件" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X size={17} /></button>
        </div>
        {error && <div className="border-b bg-red-50 px-5 py-2 text-xs text-red-600">{error}</div>}
        <div className="flex min-h-0 flex-1">
          <aside className="flex w-[300px] shrink-0 flex-col border-r border-slate-200 bg-slate-50/70">
            <div className="grid grid-cols-2 gap-2 border-b border-slate-200 p-3">
              <input ref={inputRef} type="file" multiple className="hidden"
                accept=".doc,.docx,.ppt,.pptx,.xls,.xlsx,.pdf,.md,.txt,.csv,.json,.xml,.png,.jpg,.jpeg,.webp"
                onChange={e => void uploadFiles(e.target.files)} />
              <button onClick={() => inputRef.current?.click()} disabled={uploading}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-teal-700 px-3 py-2 text-xs font-medium text-white transition hover:bg-teal-800 disabled:opacity-50">
                {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} 上传文件
              </button>
              <button onClick={() => void downloadStewardFile(conversationId, undefined, `data-steward-${conversationId.slice(0, 8)}.zip`)}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 transition hover:border-teal-200 hover:bg-teal-50">
                <FileArchive size={13} /> 一键打包
              </button>
            </div>
            <div className="flex items-center justify-between px-3 pb-2 pt-3">
              <span className="text-xs font-semibold text-slate-600">文件树 <span className="font-normal text-slate-400">({files.length})</span></span>
              <button onClick={() => void reload()} aria-label="刷新会话文件" className="rounded-md p-1 text-slate-400 transition hover:bg-white hover:text-teal-700"><RefreshCw size={13} /></button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 pt-1">
              {loading ? <div className="py-16 text-center text-sm text-slate-400">加载中…</div> : files.length === 0 ? (
                <div className="mx-1 rounded-xl border border-dashed border-slate-300 bg-white/70 px-3 py-12 text-center text-xs text-slate-400">当前会话还没有文件</div>
              ) : (
                <div>
                  {treeRows.map(row => row.type === 'directory' ? (
                    <button
                      key={row.key}
                      type="button"
                      aria-expanded={!collapsedDirs.has(row.path)}
                      aria-label={`${row.name}，下一级 ${row.childCount} 项`}
                      onClick={() => setCollapsedDirs(current => {
                        const next = new Set(current)
                        if (next.has(row.path)) next.delete(row.path); else next.add(row.path)
                        return next
                      })}
                      className="flex h-8 w-full items-center gap-1.5 rounded-md pr-2 text-left text-xs font-medium text-slate-600 transition-colors hover:bg-white"
                      style={{ paddingLeft: `${8 + row.depth * 16}px` }}
                    >
                      {collapsedDirs.has(row.path) ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                      {collapsedDirs.has(row.path)
                        ? <Folder size={14} className="shrink-0 text-amber-500" />
                        : <FolderOpen size={14} className="shrink-0 text-amber-500" />}
                      <span className="min-w-0 flex-1 truncate">{row.name}</span>
                      <span aria-hidden="true" className="ml-auto shrink-0 tabular-nums text-[10px] font-medium text-slate-400">
                        {row.childCount}
                      </span>
                    </button>
                  ) : (
                    <button
                      key={row.key}
                      type="button"
                      onClick={() => setSelectedId(row.file.id)}
                      title={row.file.relativePath || row.file.filename}
                      className={`flex min-h-9 w-full items-center gap-2 rounded-md py-1.5 pr-2 text-left transition-colors ${selectedId === row.file.id
                        ? 'bg-teal-50 text-teal-800'
                        : 'text-slate-600 hover:bg-white'}`}
                      style={{ paddingLeft: `${25 + row.depth * 16}px` }}
                    >
                      <FileText size={14} className={selectedId === row.file.id ? 'shrink-0 text-teal-600' : 'shrink-0 text-slate-400'} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium">{row.name}</span>
                        <span className="mt-0.5 block text-[9px] text-slate-400">{formatBytes(row.file.size)}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </aside>

          <section className="flex min-w-0 flex-1 flex-col bg-white">
            {selectedFile ? (
              <>
                <div className="flex min-h-[58px] items-center gap-3 border-b border-slate-200 px-4 py-2.5">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700"><FileText size={16} /></div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-800" title={selectedFile.filename}>{selectedFile.filename}</p>
                    <p className="mt-0.5 truncate text-[11px] text-slate-400">
                      {selectedFile.source === 'download' ? '网页下载' : selectedFile.source === 'generated' ? '管家创建' : selectedFile.source === 'edited' ? '管家编辑' : '用户上传'} · {formatBytes(selectedFile.size)}
                      {selectedFile.extractedChars > 0 ? ` · 已解析 ${selectedFile.extractedChars.toLocaleString()} 字` : ''}
                    </p>
                  </div>
                  <button title="下载文件" onClick={() => void downloadStewardFile(conversationId, selectedFile.id, selectedFile.filename)}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700">
                    <Download size={13} /> 下载
                  </button>
                  <button title="删除文件" onClick={() => void remove(selectedFile.id)}
                    className="flex items-center gap-1.5 rounded-lg border border-red-100 px-2.5 py-1.5 text-xs text-red-500 transition hover:bg-red-50">
                    <Trash2 size={13} /> 删除
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-hidden bg-slate-50/50 p-3">
                  <div className="h-full overflow-hidden rounded-xl border border-slate-200 bg-white">
                    {preview.kind === 'loading' ? (
                      <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-400"><Loader2 size={15} className="animate-spin" />正在生成预览…</div>
                    ) : preview.kind === 'image' && preview.url ? (
                      <div className="flex h-full items-center justify-center overflow-auto p-4"><img src={preview.url} alt={selectedFile.filename} className="max-h-full max-w-full object-contain" /></div>
                    ) : preview.kind === 'pdf' && preview.url ? (
                      <iframe title={`${selectedFile.filename} 预览`} src={preview.url} className="h-full w-full border-0" />
                    ) : preview.kind === 'text' ? (
                      <div className="h-full overflow-auto p-4">
                        <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-6 text-slate-700">{preview.text}</pre>
                        {preview.truncated && <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-amber-600">预览内容较长，当前仅展示前 60,000 个字符。</p>}
                      </div>
                    ) : preview.kind === 'unsupported' || preview.kind === 'error' ? (
                      <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-sm text-slate-400">
                        <FileText size={30} className="opacity-35" />
                        <p>{preview.message}</p>
                      </div>
                    ) : null}
                  </div>
                </div>
              </>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-400">
                <FolderOpen size={32} className="opacity-30" />
                <p>从左侧文件树选择文件后查看内容</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
