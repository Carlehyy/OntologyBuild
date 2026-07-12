import { useMemo, useState } from 'react'
import {
  Download, File, FileCode2, FilePlus2, Loader2, Pencil, Save, Trash2, X,
} from 'lucide-react'
import { explorationApi, type BxAttachment, type BxWorkspaceText } from '@/api/exploration'

const formatSize = (n: number) => n < 1024 ? `${n} B`
  : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB`
    : `${(n / 1024 / 1024).toFixed(1)} MB`

const errorText = (error: unknown, fallback: string) => {
  if (!error || typeof error !== 'object') return fallback
  const value = error as { detail?: string | { message?: string }; message?: string }
  const detail = value.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return value.message || fallback
}

export default function FileWorkspaceDrawer({ sessionId, files, onFilesChange, onClose }: {
  sessionId: string
  files: BxAttachment[]
  onFilesChange: (files: BxAttachment[]) => void
  onClose: () => void
}) {
  const [loadingId, setLoadingId] = useState('')
  const [editor, setEditor] = useState<BxWorkspaceText | null>(null)
  const [draft, setDraft] = useState('')
  const [newPath, setNewPath] = useState('')
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirmId, setConfirmId] = useState('')
  const [error, setError] = useState('')

  const sorted = useMemo(() => [...files].sort((a, b) =>
    Date.parse(b.updatedAt || b.createdAt) - Date.parse(a.updatedAt || a.createdAt)), [files])

  const download = async (file: BxAttachment) => {
    setError(''); setLoadingId(file.id)
    try {
      const blob = await explorationApi.downloadAttachment(sessionId, file.id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = file.filename
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(errorText(e, '文件下载失败'))
    } finally { setLoadingId('') }
  }

  const edit = async (file: BxAttachment) => {
    setError(''); setLoadingId(file.id)
    try {
      const text = await explorationApi.attachmentContent(sessionId, file.id)
      setEditor(text); setDraft(text.content); setCreating(false)
    } catch (e) {
      setError(errorText(e, '文件读取失败'))
    } finally { setLoadingId('') }
  }

  const save = async () => {
    if (!editor || saving) return
    setSaving(true); setError('')
    try {
      const updated = await explorationApi.updateWorkspaceText(sessionId, editor.id, {
        content: draft, expectedVersion: editor.version,
      })
      onFilesChange(files.map(f => f.id === updated.id ? updated : f))
      setEditor({ ...editor, content: draft, version: updated.version, sha256: updated.sha256 })
    } catch (e) {
      setError(errorText(e, '文件保存失败'))
    } finally { setSaving(false) }
  }

  const create = async () => {
    if (!newPath.trim() || saving) return
    setSaving(true); setError('')
    try {
      const created = await explorationApi.createWorkspaceText(sessionId, {
        path: newPath.trim(), content: draft,
      })
      onFilesChange([...files, created])
      setCreating(false); setNewPath(''); setDraft('')
      await edit(created)
    } catch (e) {
      setError(errorText(e, '文件创建失败'))
    } finally { setSaving(false) }
  }

  const remove = async (file: BxAttachment) => {
    setLoadingId(file.id); setError('')
    try {
      await explorationApi.deleteAttachment(sessionId, file.id)
      onFilesChange(files.filter(f => f.id !== file.id))
      if (editor?.id === file.id) setEditor(null)
      setConfirmId('')
    } catch (e) {
      setError(errorText(e, '文件删除失败'))
    } finally { setLoadingId('') }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/25" onClick={onClose}>
      <section
        data-testid="workspace-drawer"
        aria-label="会话文件空间"
        className="flex h-full w-[560px] max-w-[94vw] flex-col bg-[var(--color-bg-elevated)] shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <header className="flex items-start justify-between border-b border-[var(--color-border)] px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">会话文件空间</h2>
            <p className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
              {files.length} 个文件 · 与其他探索会话隔离 · 可下载；文本文件支持版本化编辑
            </p>
          </div>
          <button aria-label="关闭会话文件空间" onClick={onClose}
            className="rounded-md p-1.5 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]">
            <X size={16} />
          </button>
        </header>

        <div className="flex min-h-0 flex-1">
          <div className="w-[46%] overflow-y-auto border-r border-[var(--color-border)] p-3">
            <button
              onClick={() => { setCreating(true); setEditor(null); setNewPath('notes/业务记录.md'); setDraft('') }}
              className="mb-3 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-teal-300 px-3 py-2 text-xs font-medium text-teal-700 hover:bg-teal-50"
            >
              <FilePlus2 size={14} /> 新建文本文件
            </button>
            {sorted.length === 0 && (
              <div className="px-2 py-8 text-center text-xs leading-5 text-[var(--color-text-tertiary)]">
                当前会话还没有文件。可从对话输入框上传，或新建一份 Markdown 记录。
              </div>
            )}
            <div className="space-y-1">
              {sorted.map(file => (
                <div key={file.id} className={`group rounded-lg px-2.5 py-2 transition-colors ${editor?.id === file.id ? 'bg-teal-50' : 'hover:bg-[var(--color-bg-hover)]'}`}>
                  <div className="flex items-start gap-2">
                    {file.editable ? <FileCode2 size={15} className="mt-0.5 shrink-0 text-teal-600" />
                      : <File size={15} className="mt-0.5 shrink-0 text-slate-500" />}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium text-[var(--color-text-primary)]" title={file.relativePath || file.filename}>
                        {file.relativePath || file.filename}
                      </div>
                      <div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
                        {formatSize(file.fileSize)} · v{file.version} · {file.source === 'agent' ? 'Agent 生成' : file.source === 'user' ? '用户创建' : '用户上传'}
                      </div>
                      {file.status === 'failed' && <div className="mt-1 text-[10px] text-amber-700">文本未解析，仍可下载原文件</div>}
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-1 pl-[23px]">
                    {file.editable && <button onClick={() => void edit(file)} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-teal-700 hover:bg-white"><Pencil size={11} />编辑</button>}
                    <button onClick={() => void download(file)} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-slate-600 hover:bg-white">
                      {loadingId === file.id ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />}下载
                    </button>
                    {confirmId === file.id ? (
                      <>
                        <button onClick={() => void remove(file)} className="rounded bg-rose-600 px-1.5 py-1 text-[10px] text-white">确认删除</button>
                        <button onClick={() => setConfirmId('')} className="rounded px-1.5 py-1 text-[10px] text-slate-500">取消</button>
                      </>
                    ) : (
                      <button onClick={() => setConfirmId(file.id)} className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-slate-500 hover:bg-rose-50 hover:text-rose-600"><Trash2 size={11} />删除</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex min-w-0 flex-1 flex-col p-4">
            {creating ? (
              <>
                <label className="mb-1 text-[11px] font-medium text-[var(--color-text-secondary)]">会话内相对路径</label>
                <input value={newPath} onChange={e => setNewPath(e.target.value)}
                  className="mb-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2.5 py-2 text-xs outline-none focus:border-teal-500" />
                <textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder="输入文件内容…"
                  className="min-h-0 flex-1 resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500" />
                <button onClick={() => void create()} disabled={!newPath.trim() || saving}
                  className="mt-3 inline-flex h-8 items-center justify-center gap-1.5 rounded-md bg-teal-600 px-3 text-xs font-medium text-white hover:bg-teal-700 disabled:opacity-40">
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <FilePlus2 size={13} />}创建文件
                </button>
              </>
            ) : editor ? (
              <>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-semibold text-[var(--color-text-primary)]">{editor.relativePath}</div>
                    <div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">当前版本 v{editor.version} · 保存时执行乐观并发校验</div>
                  </div>
                  <button onClick={() => void save()} disabled={saving || draft === editor.content}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-teal-600 px-3 text-xs font-medium text-white hover:bg-teal-700 disabled:opacity-40">
                    {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}保存
                  </button>
                </div>
                <textarea value={draft} onChange={e => setDraft(e.target.value)}
                  className="min-h-0 flex-1 resize-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500" />
              </>
            ) : (
              <div className="flex h-full flex-col items-center justify-center text-center text-xs leading-5 text-[var(--color-text-tertiary)]">
                <FileCode2 size={24} className="mb-2 text-slate-300" />
                选择文本文件进行查看和编辑，或直接下载 Office/PDF 等原文件。
              </div>
            )}
            {error && <div className="mt-2 rounded-md bg-rose-50 px-2.5 py-2 text-[11px] text-rose-700">{error}</div>}
          </div>
        </div>
      </section>
    </div>
  )
}
