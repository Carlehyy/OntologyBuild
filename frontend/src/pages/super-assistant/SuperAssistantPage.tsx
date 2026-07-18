import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Bot, Check, ChevronRight, CircleAlert, FileCode2, FileText, Folder,
  Loader2, Menu, MessageSquare, PanelRight, Pencil, PlugZap, Plus,
  Save, Send, Settings2, ShieldCheck, Square, Trash2, Upload, User,
  Wrench, X,
} from 'lucide-react'

import { modelApi } from '@/api/ontologies'
import {
  superAssistantApi,
  type SkillFile,
  type SuperConversation,
  type SuperMcpServer,
  type SuperMessage,
  type SuperSkill,
  type ToolStep,
} from '@/api/superAssistant'
import { useToast } from '@/components/ui/Toast'
import type { ModelConfig } from '@/types/ontology'


const errorText = (error: any, fallback = '操作失败') =>
  error?.detail || error?.message || fallback


function EmptyState() {
  return (
    <div className="mx-auto flex max-w-xl flex-1 flex-col items-center justify-center px-6 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 ring-1 ring-teal-100">
        <Bot size={27} strokeWidth={1.8} />
      </div>
      <h1 className="mt-5 text-xl font-semibold tracking-tight text-[var(--color-text-primary)]">超级助手</h1>
      <p className="mt-2 max-w-md text-sm leading-6 text-[var(--color-text-secondary)]">
        独立于本体业务的通用助手。它会按需读取你的目录型 Skill，并在获得确认后调用已连接的 MCP 工具。
      </p>
      <div className="mt-6 grid w-full grid-cols-1 gap-2 text-left sm:grid-cols-3">
        {[
          ['目录型 Skill', 'SKILL.md + scripts / references / assets'],
          ['外部 MCP', 'Streamable HTTP，工具清单按连接发现'],
          ['执行确认', '外部工具默认先展示参数再执行'],
        ].map(([title, description]) => (
          <div key={title} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
            <p className="text-xs font-medium text-[var(--color-text-primary)]">{title}</p>
            <p className="mt-1 text-[11px] leading-5 text-[var(--color-text-tertiary)]">{description}</p>
          </div>
        ))}
      </div>
    </div>
  )
}


function ToolSteps({ steps }: { steps: ToolStep[] }) {
  if (!steps?.length) return null
  return (
    <div className="mb-2 space-y-1.5">
      {steps.map((step, index) => (
        <details key={`${step.toolName}-${index}`} className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)]">
          <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 px-3 text-xs text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-500">
            {step.status === 'running' || step.status === 'awaiting_confirmation'
              ? <Loader2 size={13} className="animate-spin text-teal-600" />
              : step.status === 'success'
                ? <Check size={13} className="text-emerald-600" />
                : <CircleAlert size={13} className="text-amber-600" />}
            <span className="font-medium">{step.toolName}</span>
            <span className="ml-auto text-[10px] text-[var(--color-text-tertiary)]">{step.status}</span>
            <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
          </summary>
          <pre className="max-h-48 overflow-auto border-t border-[var(--color-border)] p-3 text-[11px] leading-5 text-[var(--color-text-secondary)] whitespace-pre-wrap break-all">
            {step.preview || JSON.stringify(step.arguments || {}, null, 2)}
          </pre>
        </details>
      ))}
    </div>
  )
}


function ChatMessage({ message }: { message: SuperMessage }) {
  const assistant = message.role === 'assistant'
  return (
    <article className={`flex gap-3 ${assistant ? '' : 'justify-end'}`}>
      {assistant && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700 ring-1 ring-teal-100">
          <Bot size={16} />
        </div>
      )}
      <div className={`min-w-0 ${assistant ? 'w-full max-w-3xl' : 'max-w-[82%]'}`}>
        {assistant && <ToolSteps steps={message.steps} />}
        <div className={assistant
          ? 'prose prose-sm max-w-none text-[var(--color-text-primary)] prose-headings:text-[var(--color-text-primary)] prose-p:text-[var(--color-text-primary)] prose-code:break-words'
          : 'rounded-2xl rounded-br-md bg-[var(--color-nav-bg)] px-4 py-2.5 text-sm leading-6 text-white'}>
          {assistant ? (
            message.content
              ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
              : <span className="inline-flex items-center gap-2 text-sm text-[var(--color-text-tertiary)]"><Loader2 size={14} className="animate-spin" /> 正在思考…</span>
          ) : message.content}
        </div>
        {message.status === 'error' && (
          <p role="alert" className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--color-danger)]"><CircleAlert size={12} /> 生成失败</p>
        )}
        {message.status === 'cancelled' && (
          <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">已停止生成</p>
        )}
      </div>
      {!assistant && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
          <User size={16} />
        </div>
      )}
    </article>
  )
}


interface PendingConfirmation {
  toolRunId: string
  toolName: string
  serverName: string
  arguments: Record<string, unknown>
}

function ConfirmationCard({ pending, busy, onDecision }: {
  pending: PendingConfirmation
  busy: boolean
  onDecision: (decision: 'approve' | 'deny') => void
}) {
  return (
    <div role="alert" className="ml-11 max-w-3xl rounded-xl border border-amber-200 bg-amber-50/80 p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700"><ShieldCheck size={18} /></div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-amber-950">等待执行确认</p>
          <p className="mt-1 text-xs leading-5 text-amber-800">
            MCP「{pending.serverName}」请求调用 <span className="font-mono font-medium">{pending.toolName}</span>
          </p>
          <pre className="mt-3 max-h-40 overflow-auto rounded-lg border border-amber-200 bg-white/80 p-3 text-[11px] leading-5 text-slate-700 whitespace-pre-wrap break-all">
            {JSON.stringify(pending.arguments, null, 2)}
          </pre>
          <div className="mt-3 flex gap-2">
            <button type="button" disabled={busy} onClick={() => onDecision('deny')}
              className="min-h-10 rounded-lg border border-amber-300 px-4 text-xs font-medium text-amber-900 transition-colors hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50">
              拒绝
            </button>
            <button type="button" disabled={busy} onClick={() => onDecision('approve')}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-amber-700 px-4 text-xs font-medium text-white transition-colors hover:bg-amber-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:opacity-50">
              {busy && <Loader2 size={13} className="animate-spin" />} 确认执行
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function ConversationSidebar({ conversations, selectedId, open, onClose, onCreate, onSelect, onDelete }: {
  conversations: SuperConversation[]
  selectedId: string | null
  open: boolean
  onClose: () => void
  onCreate: () => void
  onSelect: (id: string) => void
  onDelete: (conversation: SuperConversation) => void
}) {
  return (
    <>
      {open && <button aria-label="关闭会话列表" className="fixed inset-0 z-30 bg-black/30 lg:hidden" onClick={onClose} />}
      <aside className={`${open ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg-elevated)] transition-transform lg:static lg:z-auto lg:w-64 lg:translate-x-0`}>
        <div className="flex h-14 items-center justify-between border-b border-[var(--color-border)] px-3">
          <p className="text-sm font-semibold text-[var(--color-text-primary)]">会话</p>
          <div className="flex gap-1">
            <button type="button" onClick={onCreate} aria-label="新建会话" title="新建会话"
              className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500">
              <Plus size={17} />
            </button>
            <button type="button" onClick={onClose} aria-label="关闭会话列表"
              className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] lg:hidden"><X size={17} /></button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {conversations.length === 0 ? (
            <div className="px-3 py-10 text-center">
              <MessageSquare size={22} className="mx-auto text-[var(--color-text-tertiary)]" />
              <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">发送第一条消息后创建会话</p>
            </div>
          ) : conversations.map(conversation => (
            <div key={conversation.id} className={`group mb-1 flex items-center rounded-lg ${selectedId === conversation.id ? 'bg-teal-50 text-teal-900' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'}`}>
              <button type="button" onClick={() => { onSelect(conversation.id); onClose() }}
                className="min-w-0 flex-1 px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-500">
                <p className="truncate text-xs font-medium">{conversation.title}</p>
                <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">{new Date(conversation.updated_at).toLocaleString()}</p>
              </button>
              <button type="button" onClick={() => onDelete(conversation)} aria-label={`删除会话 ${conversation.title}`}
                className="mr-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-md opacity-0 transition-all hover:bg-red-50 hover:text-red-600 focus:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 group-hover:opacity-100">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}


function DialogShell({ title, description, wide = false, onClose, children }: {
  title: string
  description?: string
  wide?: boolean
  onClose: () => void
  children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/35 p-4" onMouseDown={onClose}>
      <section role="dialog" aria-modal="true" aria-labelledby="sa-dialog-title"
        onMouseDown={event => event.stopPropagation()}
        className={`flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-2xl ${wide ? 'max-w-5xl' : 'max-w-xl'}`}>
        <header className="flex items-start justify-between border-b border-[var(--color-border)] px-5 py-4">
          <div>
            <h2 id="sa-dialog-title" className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h2>
            {description && <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{description}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"
            className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><X size={17} /></button>
        </header>
        {children}
      </section>
    </div>
  )
}


function SkillCreateDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => Promise<void> }) {
  const { toast } = useToast()
  const [name, setName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [description, setDescription] = useState('')
  const [triggers, setTriggers] = useState('')
  const [instructions, setInstructions] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(name) || !displayName.trim()) {
      setError('请填写合法的 name 和显示名称')
      return
    }
    setBusy(true); setError('')
    try {
      await superAssistantApi.createSkill({
        name,
        display_name: displayName.trim(),
        description: description.trim(),
        triggers: triggers.split(',').map(item => item.trim()).filter(Boolean),
        instructions,
        enabled: true,
      })
      await onSaved()
      toast({ tone: 'success', title: 'Skill 已创建', description: '已生成标准目录和 SKILL.md' })
      onClose()
    } catch (error) { setError(errorText(error, '创建失败')) } finally { setBusy(false) }
  }

  return (
    <DialogShell title="新建目录型 Skill" description="系统会创建 Skill 文件夹和必需的 SKILL.md；之后可继续添加 scripts、references、assets 等文件。" onClose={onClose}>
      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-xs text-[var(--color-text-secondary)]">name <span className="text-red-500">*</span>
            <input value={name} onChange={event => setName(event.target.value)} placeholder="research_helper"
              className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          </label>
          <label className="text-xs text-[var(--color-text-secondary)]">显示名称 <span className="text-red-500">*</span>
            <input value={displayName} onChange={event => setDisplayName(event.target.value)} placeholder="研究助手"
              className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          </label>
        </div>
        <label className="block text-xs text-[var(--color-text-secondary)]">描述
          <textarea value={description} onChange={event => setDescription(event.target.value)} rows={2}
            className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">触发词
          <input value={triggers} onChange={event => setTriggers(event.target.value)} placeholder="调研, 研究, summarize（逗号分隔）"
            className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">初始指令
          <textarea value={instructions} onChange={event => setInstructions(event.target.value)} rows={8} placeholder="# 工作流程&#10;1. …"
            className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
        </label>
        {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
      </div>
      <footer className="flex justify-end gap-2 border-t border-[var(--color-border)] px-5 py-4">
        <button onClick={onClose} className="min-h-10 rounded-lg px-4 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">取消</button>
        <button onClick={save} disabled={busy} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50">
          {busy && <Loader2 size={13} className="animate-spin" />} 创建
        </button>
      </footer>
    </DialogShell>
  )
}


function SkillEditor({ skill, onClose, onSaved }: { skill: SuperSkill; onClose: () => void; onSaved: () => Promise<void> }) {
  const { toast } = useToast()
  const [files, setFiles] = useState<SkillFile[]>(skill.manifest)
  const [revision, setRevision] = useState(skill.revision)
  const [selectedPath, setSelectedPath] = useState('SKILL.md')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [newPath, setNewPath] = useState('')
  const [error, setError] = useState('')

  const loadFile = useCallback(async (path: string) => {
    setLoading(true); setError('')
    try {
      const result = await superAssistantApi.skillFile(skill.id, path)
      setContent(result.content); setSelectedPath(path)
    } catch (error) { setError(errorText(error, '读取文件失败')) } finally { setLoading(false) }
  }, [skill.id])

  useEffect(() => { void loadFile('SKILL.md') }, [loadFile])

  const save = async () => {
    setSaving(true); setError('')
    try {
      const result: any = await superAssistantApi.putSkillFile(skill.id, selectedPath, content)
      setFiles(result.manifest || files)
      setRevision(result.revision || revision)
      await onSaved()
      toast({ tone: 'success', title: '文件已保存', description: `${selectedPath} · revision ${result.revision}` })
    } catch (error) { setError(errorText(error, '保存失败')) } finally { setSaving(false) }
  }

  const startNewFile = () => {
    const path = newPath.trim().replace(/^\/+/, '')
    if (!path || path.includes('..')) { setError('请输入 Skill 目录内的合法相对路径'); return }
    setSelectedPath(path); setContent(''); setNewPath(''); setLoading(false); setError('')
  }

  const removeFile = async () => {
    if (selectedPath === 'SKILL.md' || !window.confirm(`确定删除 ${selectedPath}？`)) return
    try {
      await superAssistantApi.deleteSkillFile(skill.id, selectedPath)
      const next = await superAssistantApi.skillFiles(skill.id)
      setFiles(next); await loadFile('SKILL.md'); await onSaved()
      toast({ tone: 'success', title: '文件已删除' })
    } catch (error) { setError(errorText(error, '删除失败')) }
  }

  return (
    <DialogShell wide title={`编辑 Skill：${skill.display_name}`} description={`${skill.name} · revision ${revision} · ${files.length} 个文件`} onClose={onClose}>
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="flex min-h-40 flex-col border-b border-[var(--color-border)] bg-[var(--color-bg-base)] md:border-b-0 md:border-r">
          <div className="border-b border-[var(--color-border)] p-3">
            <label className="text-[11px] text-[var(--color-text-secondary)]">新建相对路径</label>
            <div className="mt-1 flex gap-1.5">
              <input value={newPath} onChange={event => setNewPath(event.target.value)} onKeyDown={event => event.key === 'Enter' && startNewFile()}
                placeholder="references/guide.md" className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-white px-2 text-xs outline-none focus:border-teal-500" />
              <button onClick={startNewFile} aria-label="新建文件" className="flex h-10 w-10 items-center justify-center rounded-md border border-[var(--color-border)] bg-white hover:bg-teal-50"><Plus size={14} /></button>
            </div>
          </div>
          <div className="max-h-52 flex-1 overflow-y-auto p-2 md:max-h-none">
            {files.map(file => (
              <button key={file.path} onClick={() => file.editable && void loadFile(file.path)} disabled={!file.editable}
                className={`mb-1 flex min-h-9 w-full items-center gap-2 rounded-md px-2 text-left text-xs ${selectedPath === file.path ? 'bg-teal-100 text-teal-900' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'} disabled:cursor-not-allowed disabled:opacity-45`}>
                {file.path.includes('/') ? <FileCode2 size={13} /> : <FileText size={13} />}
                <span className="min-w-0 flex-1 truncate">{file.path}</span>
                <span className="text-[9px] text-[var(--color-text-tertiary)]">{Math.ceil(file.size / 1024)}K</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="flex min-h-0 flex-col">
          <div className="flex min-h-12 items-center justify-between border-b border-[var(--color-border)] px-4">
            <span className="truncate font-mono text-xs text-[var(--color-text-secondary)]">{selectedPath}</span>
            <div className="flex gap-1">
              {selectedPath !== 'SKILL.md' && (
                <button onClick={removeFile} aria-label="删除当前文件" className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-tertiary)] hover:bg-red-50 hover:text-red-600"><Trash2 size={14} /></button>
              )}
              <button onClick={save} disabled={saving || loading} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal-700 px-3 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50">
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} 保存
              </button>
            </div>
          </div>
          {loading ? (
            <div className="flex flex-1 items-center justify-center"><Loader2 size={20} className="animate-spin text-teal-600" /></div>
          ) : (
            <textarea aria-label={`编辑 ${selectedPath}`} value={content} onChange={event => setContent(event.target.value)} spellCheck={false}
              className="min-h-[360px] flex-1 resize-none bg-[var(--color-bg-elevated)] p-4 font-mono text-xs leading-6 text-[var(--color-text-primary)] outline-none focus:ring-2 focus:ring-inset focus:ring-teal-500" />
          )}
          {error && <p role="alert" className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">{error}</p>}
        </div>
      </div>
    </DialogShell>
  )
}


function McpDialog({ server, onClose, onSaved }: {
  server?: SuperMcpServer
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { toast } = useToast()
  const [name, setName] = useState(server?.name || '')
  const [url, setUrl] = useState(server?.url || '')
  const [headers, setHeaders] = useState('')
  const [enabled, setEnabled] = useState(server?.enabled ?? true)
  const [confirmation, setConfirmation] = useState(server?.require_confirmation ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setBusy(true); setError('')
    try {
      let parsedHeaders: Record<string, string> | undefined
      if (headers.trim()) {
        const parsed = JSON.parse(headers)
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(value => typeof value !== 'string')) {
          throw new Error('Headers 必须是字符串键值 JSON 对象')
        }
        parsedHeaders = parsed
      }
      if (server) {
        await superAssistantApi.updateMcpServer(server.id, {
          url, enabled, require_confirmation: confirmation,
          ...(parsedHeaders ? { headers: parsedHeaders } : {}),
        })
      } else {
        await superAssistantApi.createMcpServer({
          name, url, headers: parsedHeaders || {}, enabled, require_confirmation: confirmation,
        })
      }
      await onSaved()
      toast({ tone: 'success', title: server ? 'MCP 配置已更新' : 'MCP Server 已添加', description: '请执行连接测试以发现工具清单' })
      onClose()
    } catch (error) { setError(errorText(error, '保存失败')) } finally { setBusy(false) }
  }

  return (
    <DialogShell title={server ? `编辑 MCP：${server.name}` : '添加 MCP Server'} description="首版支持 Streamable HTTP；请求头会加密存储且不会回显。" onClose={onClose}>
      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        <label className="block text-xs text-[var(--color-text-secondary)]">名称 <span className="text-red-500">*</span>
          <input value={name} disabled={!!server} onChange={event => setName(event.target.value)} placeholder="knowledge_search"
            className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100 disabled:opacity-60" />
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">Streamable HTTP URL <span className="text-red-500">*</span>
          <input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://mcp.example.com/mcp"
            className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          <span className="mt-1 block text-[10px] leading-4 text-[var(--color-text-tertiary)]">服务域名需由部署方加入 SUPER_ASSISTANT_MCP_ALLOWED_HOSTS。</span>
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">请求头 JSON
          <textarea value={headers} onChange={event => setHeaders(event.target.value)} rows={4} placeholder={server ? `留空保持现有请求头（${server.header_names.join(', ') || '无'}）` : '{\n  "Authorization": "Bearer …"\n}'}
            className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
        </label>
        <label className="flex min-h-11 items-center justify-between rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)]">
          启用此 Server
          <input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} className="h-4 w-4 accent-teal-700" />
        </label>
        <label className="flex min-h-11 items-center justify-between rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)]">
          每次工具调用前要求确认
          <input type="checkbox" checked={confirmation} onChange={event => setConfirmation(event.target.checked)} className="h-4 w-4 accent-teal-700" />
        </label>
        {!confirmation && <p className="rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800">关闭确认会允许模型直接执行该 Server 的所有工具，请仅对完全可信、只读的服务使用。</p>}
        {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
      </div>
      <footer className="flex justify-end gap-2 border-t border-[var(--color-border)] px-5 py-4">
        <button onClick={onClose} className="min-h-10 rounded-lg px-4 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">取消</button>
        <button onClick={save} disabled={busy || !name || !url} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50">
          {busy && <Loader2 size={13} className="animate-spin" />} 保存
        </button>
      </footer>
    </DialogShell>
  )
}


function ConfigurationPanel({ open, onClose, skills, servers, refreshSkills, refreshServers }: {
  open: boolean
  onClose: () => void
  skills: SuperSkill[]
  servers: SuperMcpServer[]
  refreshSkills: () => Promise<void>
  refreshServers: () => Promise<void>
}) {
  const { toast } = useToast()
  const [tab, setTab] = useState<'skills' | 'mcp'>('skills')
  const [creatingSkill, setCreatingSkill] = useState(false)
  const [editingSkill, setEditingSkill] = useState<SuperSkill | null>(null)
  const [editingMcp, setEditingMcp] = useState<SuperMcpServer | 'new' | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)

  const importZip = async (file?: File) => {
    if (!file) return
    try {
      await superAssistantApi.importSkill(file)
      await refreshSkills()
      toast({ tone: 'success', title: 'Skill 文件夹已导入', description: file.name })
    } catch (error) { toast({ tone: 'error', title: '导入失败', description: errorText(error) }) }
    if (uploadRef.current) uploadRef.current.value = ''
  }

  const toggleSkill = async (skill: SuperSkill) => {
    try { await superAssistantApi.updateSkill(skill.id, { enabled: !skill.enabled }); await refreshSkills() }
    catch (error) { toast({ tone: 'error', title: '更新失败', description: errorText(error) }) }
  }

  const removeSkill = async (skill: SuperSkill) => {
    if (!window.confirm(`确定删除 Skill「${skill.display_name}」及其整个文件夹？`)) return
    try { await superAssistantApi.deleteSkill(skill.id); await refreshSkills(); toast({ tone: 'success', title: 'Skill 已删除' }) }
    catch (error) { toast({ tone: 'error', title: '删除失败', description: errorText(error) }) }
  }

  const testServer = async (server: SuperMcpServer) => {
    setTestingId(server.id)
    try {
      const result = await superAssistantApi.testMcpServer(server.id)
      await refreshServers()
      toast({ tone: result.ok ? 'success' : 'error', title: result.ok ? 'MCP 连接成功' : 'MCP 连接失败', description: result.message })
    } catch (error) { toast({ tone: 'error', title: 'MCP 测试失败', description: errorText(error) }) }
    finally { setTestingId(null) }
  }

  const removeServer = async (server: SuperMcpServer) => {
    if (!window.confirm(`确定删除 MCP Server「${server.name}」？`)) return
    try { await superAssistantApi.deleteMcpServer(server.id); await refreshServers(); toast({ tone: 'success', title: 'MCP Server 已删除' }) }
    catch (error) { toast({ tone: 'error', title: '删除失败', description: errorText(error) }) }
  }

  return (
    <>
      {open && <button aria-label="关闭配置" className="fixed inset-0 z-30 bg-black/30 xl:hidden" onClick={onClose} />}
      <aside className={`${open ? 'translate-x-0' : 'translate-x-full xl:hidden'} fixed inset-y-0 right-0 z-40 flex w-[min(390px,94vw)] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg-elevated)] transition-transform xl:static xl:z-auto xl:w-[360px]`}>
        <div className="flex h-14 items-center justify-between border-b border-[var(--color-border)] px-4">
          <div className="flex items-center gap-2"><Settings2 size={16} className="text-teal-700" /><span className="text-sm font-semibold text-[var(--color-text-primary)]">助手配置</span></div>
          <button onClick={onClose} aria-label="关闭配置" className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><X size={17} /></button>
        </div>
        <div className="grid grid-cols-2 border-b border-[var(--color-border)] p-1.5">
          <button onClick={() => setTab('skills')} className={`min-h-10 rounded-lg text-xs font-medium ${tab === 'skills' ? 'bg-teal-50 text-teal-800' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'}`}>Skills <span className="ml-1 text-[10px]">{skills.length}</span></button>
          <button onClick={() => setTab('mcp')} className={`min-h-10 rounded-lg text-xs font-medium ${tab === 'mcp' ? 'bg-teal-50 text-teal-800' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'}`}>MCP <span className="ml-1 text-[10px]">{servers.length}</span></button>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          {tab === 'skills' ? (
            <>
              <div className="mb-3 flex gap-2">
                <button onClick={() => setCreatingSkill(true)} className="inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-teal-700 px-3 text-xs font-medium text-white hover:bg-teal-800"><Plus size={13} /> 新建</button>
                <button onClick={() => uploadRef.current?.click()} className="inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"><Upload size={13} /> 导入 ZIP</button>
                <input ref={uploadRef} type="file" accept=".zip,application/zip" className="hidden" onChange={event => void importZip(event.target.files?.[0])} />
              </div>
              <p className="mb-3 text-[10px] leading-4 text-[var(--color-text-tertiary)]">Skill 是完整目录；ZIP 根目录或唯一外层目录必须包含 SKILL.md。</p>
              <div className="space-y-2">
                {skills.length === 0 && <div className="rounded-xl border border-dashed border-[var(--color-border)] p-6 text-center text-xs text-[var(--color-text-tertiary)]"><Folder size={22} className="mx-auto mb-2" />暂无 Skill</div>}
                {skills.map(skill => (
                  <div key={skill.id} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
                    <div className="flex items-start gap-2">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700"><Folder size={16} /></div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-semibold text-[var(--color-text-primary)]">{skill.display_name}</p>
                        <p className="mt-0.5 truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">{skill.name} · r{skill.revision} · {skill.manifest.length} files</p>
                      </div>
                      <input type="checkbox" aria-label={`${skill.enabled ? '停用' : '启用'} ${skill.display_name}`} checked={skill.enabled} onChange={() => void toggleSkill(skill)} className="mt-1 h-4 w-4 accent-teal-700" />
                    </div>
                    <p className="mt-2 line-clamp-2 text-[11px] leading-5 text-[var(--color-text-secondary)]">{skill.description || '暂无描述'}</p>
                    <div className="mt-2 flex justify-end gap-1">
                      <button onClick={() => setEditingSkill(skill)} className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-[11px] text-[var(--color-text-secondary)] hover:bg-teal-50 hover:text-teal-800"><Pencil size={12} /> 文件</button>
                      <button onClick={() => void removeSkill(skill)} aria-label={`删除 ${skill.display_name}`} className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-red-50 hover:text-red-600"><Trash2 size={12} /></button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <button onClick={() => setEditingMcp('new')} className="mb-3 inline-flex min-h-10 w-full items-center justify-center gap-1.5 rounded-lg bg-teal-700 px-3 text-xs font-medium text-white hover:bg-teal-800"><Plus size={13} /> 添加 MCP Server</button>
              <p className="mb-3 text-[10px] leading-4 text-[var(--color-text-tertiary)]">保存后执行连接测试，成功发现的工具才会进入助手工具目录。</p>
              <div className="space-y-2">
                {servers.length === 0 && <div className="rounded-xl border border-dashed border-[var(--color-border)] p-6 text-center text-xs text-[var(--color-text-tertiary)]"><PlugZap size={22} className="mx-auto mb-2" />暂无 MCP Server</div>}
                {servers.map(server => (
                  <div key={server.id} className="rounded-xl border border-[var(--color-border)] p-3">
                    <div className="flex items-start gap-2">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sky-50 text-sky-700"><PlugZap size={16} /></div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5"><p className="truncate text-xs font-semibold text-[var(--color-text-primary)]">{server.name}</p><span className={`h-2 w-2 rounded-full ${server.last_test_status === 'success' ? 'bg-emerald-500' : server.last_test_status === 'error' ? 'bg-red-500' : 'bg-slate-300'}`} /></div>
                        <p className="mt-1 truncate text-[10px] text-[var(--color-text-tertiary)]">{server.url}</p>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <span className="rounded bg-[var(--color-bg-base)] px-1.5 py-0.5 text-[9px] text-[var(--color-text-tertiary)]">{server.tool_manifest.length} tools</span>
                      <span className="rounded bg-[var(--color-bg-base)] px-1.5 py-0.5 text-[9px] text-[var(--color-text-tertiary)]">{server.require_confirmation ? '执行前确认' : '自动执行'}</span>
                      {!server.enabled && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">已停用</span>}
                    </div>
                    {server.last_test_message && <p className="mt-2 line-clamp-2 text-[10px] leading-4 text-[var(--color-text-tertiary)]">{server.last_test_message}</p>}
                    <div className="mt-2 flex justify-end gap-1">
                      <button onClick={() => void testServer(server)} disabled={testingId === server.id} className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-[11px] text-teal-700 hover:bg-teal-50 disabled:opacity-50">{testingId === server.id ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />} 测试</button>
                      <button onClick={() => setEditingMcp(server)} aria-label={`编辑 MCP ${server.name}`} className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]"><Pencil size={12} /></button>
                      <button onClick={() => void removeServer(server)} aria-label={`删除 MCP ${server.name}`} className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-red-50 hover:text-red-600"><Trash2 size={12} /></button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </aside>
      {creatingSkill && <SkillCreateDialog onClose={() => setCreatingSkill(false)} onSaved={refreshSkills} />}
      {editingSkill && <SkillEditor skill={editingSkill} onClose={() => setEditingSkill(null)} onSaved={refreshSkills} />}
      {editingMcp && <McpDialog server={editingMcp === 'new' ? undefined : editingMcp} onClose={() => setEditingMcp(null)} onSaved={refreshServers} />}
    </>
  )
}


export default function SuperAssistantPage() {
  const { toast } = useToast()
  const [conversations, setConversations] = useState<SuperConversation[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SuperMessage[]>([])
  const [models, setModels] = useState<ModelConfig[]>([])
  const [skills, setSkills] = useState<SuperSkill[]>([])
  const [servers, setServers] = useState<SuperMcpServer[]>([])
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [pending, setPending] = useState<PendingConfirmation | null>(null)
  const [decisionBusy, setDecisionBusy] = useState(false)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [configOpen, setConfigOpen] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(min-width: 1280px)').matches,
  )
  const [loading, setLoading] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const refreshConversations = useCallback(async () => {
    const data = await superAssistantApi.conversations()
    setConversations(data)
    return data
  }, [])
  const refreshSkills = useCallback(async () => setSkills(await superAssistantApi.skills()), [])
  const refreshServers = useCallback(async () => setServers(await superAssistantApi.mcpServers()), [])

  useEffect(() => {
    let alive = true
    Promise.all([
      superAssistantApi.conversations(),
      modelApi.list(),
      superAssistantApi.skills(),
      superAssistantApi.mcpServers(),
    ]).then(([conversationRows, modelRows, skillRows, serverRows]) => {
      if (!alive) return
      setConversations(conversationRows)
      setModels(modelRows.filter(model => model.config_type === 'llm' && model.enabled !== false))
      setSkills(skillRows); setServers(serverRows)
      if (conversationRows[0]) setSelectedId(conversationRows[0].id)
    }).catch(error => toast({ tone: 'error', title: '超级助手加载失败', description: errorText(error) }))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [toast])

  useEffect(() => {
    if (!selectedId) { setMessages([]); return }
    let alive = true
    superAssistantApi.messages(selectedId).then(data => { if (alive) setMessages(data) })
      .catch(error => toast({ tone: 'error', title: '会话消息加载失败', description: errorText(error) }))
    return () => { alive = false }
  }, [selectedId, toast])

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, pending])

  const selectedConversation = conversations.find(item => item.id === selectedId) || null
  const selectedModelId = selectedConversation?.model_config_id || models.find(model => model.is_default)?.id || models[0]?.id || ''

  const createConversation = async () => {
    try {
      const item = await superAssistantApi.createConversation({ model_config_id: selectedModelId || null })
      setConversations(current => [item, ...current]); setSelectedId(item.id); setMessages([])
      return item
    } catch (error) { toast({ tone: 'error', title: '新建会话失败', description: errorText(error) }); return null }
  }

  const deleteConversation = async (conversation: SuperConversation) => {
    if (!window.confirm(`确定删除会话「${conversation.title}」？`)) return
    try {
      await superAssistantApi.deleteConversation(conversation.id)
      const next = conversations.filter(item => item.id !== conversation.id)
      setConversations(next)
      if (selectedId === conversation.id) { setSelectedId(next[0]?.id || null); setMessages([]) }
      toast({ tone: 'success', title: '会话已删除' })
    } catch (error) { toast({ tone: 'error', title: '删除失败', description: errorText(error) }) }
  }

  const changeModel = async (modelId: string) => {
    if (!selectedId) return
    try {
      const updated = await superAssistantApi.updateConversation(selectedId, { model_config_id: modelId || null })
      setConversations(current => current.map(item => item.id === updated.id ? updated : item))
    } catch (error) { toast({ tone: 'error', title: '模型切换失败', description: errorText(error) }) }
  }

  const send = async () => {
    const message = input.trim()
    if (!message || running) return
    let conversation = selectedConversation
    if (!conversation) conversation = await createConversation()
    if (!conversation) return
    const conversationId = conversation.id
    const now = new Date().toISOString()
    const tempUserId = `user-${Date.now()}`
    const tempAssistantId = `assistant-${Date.now()}`
    setInput(''); setRunning(true); setStopping(false); setPending(null)
    setMessages(current => [...current,
      { id: tempUserId, conversation_id: conversationId, role: 'user', content: message, status: 'complete', steps: [], token_usage: {}, created_at: now },
      { id: tempAssistantId, conversation_id: conversationId, role: 'assistant', content: '', status: 'streaming', steps: [], token_usage: {}, created_at: now },
    ])
    try {
      await superAssistantApi.streamChat(conversationId, { message, model_config_id: selectedModelId || null }, ({ event, data }) => {
        if (event === 'text_delta') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, content: item.content + String(data.delta || '') } : item))
        } else if (event === 'tool_start') {
          const step: ToolStep = { toolName: data.toolName, status: 'running', arguments: data.arguments }
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, steps: [...item.steps, step] } : item))
        } else if (event === 'tool_confirmation_required') {
          setPending({ toolRunId: data.toolRunId, toolName: data.toolName, serverName: data.serverName, arguments: data.arguments || {} })
          setMessages(current => current.map(item => item.id === tempAssistantId
            ? { ...item, steps: item.steps.map((step, index) => index === item.steps.length - 1 ? { ...step, status: 'awaiting_confirmation' } : step) }
            : item))
        } else if (event === 'tool_result') {
          setPending(current => current?.toolRunId === data.toolRunId ? null : current)
          setMessages(current => current.map(item => item.id === tempAssistantId
            ? { ...item, steps: item.steps.map((step, index) => index === item.steps.length - 1 ? { ...step, status: data.status, preview: data.preview } : step) }
            : item))
        } else if (event === 'message_end') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? {
            ...item, content: data.message?.content || item.content, steps: data.message?.steps || item.steps,
            token_usage: data.message?.tokenUsage || {}, status: 'complete',
          } : item))
        } else if (event === 'cancelled') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, status: 'cancelled' } : item))
        } else if (event === 'error') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, content: data.message || '生成失败', status: 'error' } : item))
          toast({ tone: 'error', title: '生成失败', description: data.message })
        }
      })
    } catch (error) {
      setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, content: errorText(error, '生成失败'), status: 'error' } : item))
      toast({ tone: 'error', title: '生成失败', description: errorText(error) })
    } finally {
      setRunning(false); setStopping(false); setPending(null)
      try {
        const [messageRows] = await Promise.all([superAssistantApi.messages(conversationId), refreshConversations()])
        setMessages(messageRows)
      } catch { /* optimistic state remains usable */ }
      window.setTimeout(() => textareaRef.current?.focus(), 0)
    }
  }

  const stop = async () => {
    if (!selectedId || stopping) return
    setStopping(true)
    try { await superAssistantApi.cancel(selectedId) }
    catch (error) { setStopping(false); toast({ tone: 'error', title: '停止失败', description: errorText(error) }) }
  }

  const decide = async (decision: 'approve' | 'deny') => {
    if (!pending) return
    setDecisionBusy(true)
    try { await superAssistantApi.decideToolRun(pending.toolRunId, decision); setPending(null) }
    catch (error) { toast({ tone: 'error', title: '确认失败', description: errorText(error) }) }
    finally { setDecisionBusy(false) }
  }

  const canSend = input.trim().length > 0 && !running && models.length > 0
  const placeholder = models.length ? '输入消息；Shift + Enter 换行' : '请先到“模型配置”启用一个文本 LLM'

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-[var(--color-bg-base)]">
      <ConversationSidebar conversations={conversations} selectedId={selectedId} open={sessionsOpen} onClose={() => setSessionsOpen(false)} onCreate={() => void createConversation()} onSelect={setSelectedId} onDelete={conversation => void deleteConversation(conversation)} />

      <section className="flex min-w-0 flex-1 flex-col bg-[var(--color-bg-elevated)]">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--color-border)] px-3 sm:px-4">
          <button onClick={() => setSessionsOpen(true)} aria-label="打开会话列表" className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] lg:hidden"><Menu size={18} /></button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{selectedConversation?.title || '新的超级助手会话'}</p>
            <p className="mt-0.5 hidden text-[10px] text-[var(--color-text-tertiary)] sm:block">独立助手 · Skill 渐进披露 · MCP 工具确认</p>
          </div>
          <label className="sr-only" htmlFor="super-assistant-model">会话模型</label>
          <select id="super-assistant-model" value={selectedModelId} onChange={event => void changeModel(event.target.value)} disabled={!selectedId || running}
            className="h-10 max-w-48 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 text-xs text-[var(--color-text-secondary)] outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100 disabled:opacity-60">
            {models.length === 0 && <option value="">无可用模型</option>}
            {models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.models?.[0]}</option>)}
          </select>
          <button onClick={() => setConfigOpen(value => !value)} aria-label="打开助手配置" title="助手配置"
            className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><PanelRight size={18} /></button>
        </header>

        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {loading ? (
            <div className="flex flex-1 items-center justify-center"><Loader2 size={22} className="animate-spin text-teal-600" /></div>
          ) : messages.length === 0 ? <EmptyState /> : (
            <div className="mx-auto w-full max-w-4xl space-y-7 px-4 py-6 sm:px-8">
              {messages.map(message => <ChatMessage key={message.id} message={message} />)}
              {pending && <ConfirmationCard pending={pending} busy={decisionBusy} onDecision={decision => void decide(decision)} />}
              <div ref={messagesEndRef} />
            </div>
          )}
        </main>

        <footer className="shrink-0 border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3 sm:p-4">
          <div className="mx-auto max-w-4xl">
            <div className="flex items-end gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-base)] p-2 shadow-sm focus-within:border-teal-500 focus-within:ring-2 focus-within:ring-teal-100">
              <textarea ref={textareaRef} value={input} onChange={event => setInput(event.target.value)} rows={1} placeholder={placeholder} disabled={running || models.length === 0}
                onKeyDown={event => {
                  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (canSend) void send() }
                }}
                className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2.5 text-sm leading-5 text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-60" />
              {running ? (
                <button onClick={() => void stop()} disabled={stopping} aria-label="停止生成" title="停止生成"
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--color-text-primary)] text-white hover:opacity-90 disabled:opacity-50">{stopping ? <Loader2 size={15} className="animate-spin" /> : <Square size={14} fill="currentColor" />}</button>
              ) : (
                <button onClick={() => void send()} disabled={!canSend} aria-label="发送消息" title="发送消息"
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-teal-700 text-white transition-colors hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40"><Send size={16} /></button>
              )}
            </div>
            <p className="mt-1.5 text-center text-[10px] text-[var(--color-text-tertiary)]">超级助手可能出错；重要结果请核验。外部 MCP 工具默认需要你的确认。</p>
          </div>
        </footer>
      </section>

      <ConfigurationPanel open={configOpen} onClose={() => setConfigOpen(false)} skills={skills} servers={servers} refreshSkills={refreshSkills} refreshServers={refreshServers} />
    </div>
  )
}
