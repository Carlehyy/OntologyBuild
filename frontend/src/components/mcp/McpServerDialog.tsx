import { useEffect, useId, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'

import type { McpManagementClient } from '@/api/community'
import type { McpTransport, SuperMcpServer } from '@/api/superAssistant'
import { useToast } from '@/components/ui/Toast'


const errorText = (error: any, fallback = '操作失败') =>
  error?.detail || error?.message || fallback


export default function McpServerDialog({
  server,
  client,
  onClose,
  onSaved,
}: {
  server?: SuperMcpServer
  client: McpManagementClient
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { toast } = useToast()
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLElement>(null)
  const clientConfigRef = useRef<HTMLTextAreaElement>(null)
  const [name, setName] = useState(server?.name || '')
  const [transport, setTransport] = useState<McpTransport>(server?.transport || 'streamable_http')
  const [url, setUrl] = useState(server?.url || '')
  const [command, setCommand] = useState(server?.command || '')
  const [args, setArgs] = useState(JSON.stringify(server?.args || [], null, 2))
  const [headers, setHeaders] = useState('')
  const [env, setEnv] = useState('')
  const [clientConfig, setClientConfig] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const previousFocus = document.activeElement as HTMLElement | null
    const selector = 'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(selector))
    focusables()[0]?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previousFocus?.focus()
    }
  }, [onClose])

  useEffect(() => {
    const textarea = clientConfigRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [clientConfig])

  const parseStringMap = (value: string, label: string): Record<string, string> | undefined => {
    if (!value.trim()) return undefined
    const parsed = JSON.parse(value)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(item => typeof item !== 'string')) {
      throw new Error(`${label} 必须是字符串键值 JSON 对象`)
    }
    return parsed as Record<string, string>
  }

  const applyClientConfig = () => {
    setError('')
    try {
      const parsed = JSON.parse(clientConfig)
      const collection = parsed?.mcpServers || parsed
      if (!collection || Array.isArray(collection) || typeof collection !== 'object') {
        throw new Error('配置必须包含 mcpServers 对象')
      }
      const entries = Object.entries(collection)
      if (entries.length !== 1) throw new Error('请每次粘贴一个 MCP Server 配置')
      const [configName, raw] = entries[0] as [string, any]
      if (!raw || Array.isArray(raw) || typeof raw !== 'object') throw new Error('MCP Server 配置无效')
      if (!server) setName(configName)

      if (raw.command) {
        const parsedArgs = Array.isArray(raw.args) ? raw.args.map(String) : []
        const remoteIndex = parsedArgs.findIndex((item: string) => item === 'mcp-remote' || item.startsWith('mcp-remote@'))
        const remoteUrl = remoteIndex >= 0
          ? parsedArgs.slice(remoteIndex + 1).find((item: string) => /^https?:\/\//.test(item))
          : undefined
        if (/^(?:.*[\\/])?npx(?:\.cmd)?$/i.test(String(raw.command)) && remoteUrl) {
          setTransport('streamable_http')
          setUrl(remoteUrl)
          setCommand('')
          setArgs('[]')
        } else {
          setTransport('stdio')
          setCommand(String(raw.command))
          setArgs(JSON.stringify(parsedArgs, null, 2))
          setUrl('')
        }
        setEnv(raw.env ? JSON.stringify(raw.env, null, 2) : '')
        setHeaders('')
        return
      }
      if (!raw.url) throw new Error('配置需要 command 或 url')
      const rawTransport = String(raw.transport || 'streamable_http').toLowerCase().replace(/[ -]/g, '_')
      setTransport(rawTransport === 'sse' ? 'sse' : 'streamable_http')
      setUrl(String(raw.url))
      setHeaders(raw.headers ? JSON.stringify(raw.headers, null, 2) : '')
      setCommand('')
      setArgs('[]')
      setEnv('')
    } catch (parseError) {
      setError(errorText(parseError, '无法解析 MCP 配置'))
    }
  }

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const normalizedName = name.trim()
      if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(normalizedName)) {
        throw new Error('名称只能包含字母、数字、下划线和连字符，且必须以字母或数字开头')
      }
      const parsedHeaders = parseStringMap(headers, 'Headers')
      const parsedEnv = parseStringMap(env, 'env')
      const parsedArgs = JSON.parse(args || '[]')
      if (!Array.isArray(parsedArgs) || parsedArgs.some(item => typeof item !== 'string')) {
        throw new Error('args 必须是字符串 JSON 数组')
      }
      if (transport === 'stdio' && !command.trim()) throw new Error('stdio 传输必须填写 command')
      if (transport !== 'stdio' && !url.trim()) throw new Error('HTTP 传输必须填写 URL')
      const changedTransport = !!server && server.transport !== transport
      const connection = transport === 'stdio'
        ? { transport, url: '', command: command.trim(), args: parsedArgs }
        : { transport, url: url.trim(), command: null, args: [] }
      if (server) {
        await client.updateMcpServer(server.id, {
          ...connection,
          ...(parsedHeaders ? { headers: parsedHeaders } : changedTransport && transport === 'stdio' ? { headers: {} } : {}),
          ...(parsedEnv ? { env: parsedEnv } : changedTransport && transport !== 'stdio' ? { env: {} } : {}),
        })
      } else {
        await client.createMcpServer({
          name: normalizedName,
          ...connection,
          headers: parsedHeaders || {},
          env: parsedEnv || {},
          enabled: false,
          require_confirmation: true,
        })
      }
      await onSaved()
      toast({
        tone: 'success',
        title: server ? 'MCP 配置已更新' : 'MCP Server 已添加',
        description: '配置已登记；建议立即执行连接测试以刷新工具清单。',
      })
      onClose()
    } catch (saveError) {
      setError(errorText(saveError, '保存失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-[2px]" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onMouseDown={event => event.stopPropagation()}
        className="flex max-h-[88dvh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_28px_90px_rgba(15,23,42,0.24)]"
      >
        <header className="flex shrink-0 items-start justify-between border-b border-slate-200 px-5 py-4 sm:px-6">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-slate-900">{server ? `编辑 MCP：${server.name}` : '添加 MCP Server'}</h2>
            <p id={descriptionId} className="mt-1 text-xs leading-5 text-slate-500">支持粘贴客户端 JSON，也可直接配置 stdio、SSE 或 Streamable HTTP。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭添加 MCP 弹窗" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500">
            <X size={17} />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-5 [scrollbar-gutter:stable] sm:p-6">
          {!server && (
            <details className="rounded-xl border border-slate-200 bg-slate-50/70" open>
              <summary className="cursor-pointer px-4 py-3 text-xs font-medium text-slate-700">粘贴 MCP 客户端 JSON</summary>
              <div className="space-y-3 border-t border-slate-200 p-4">
                <textarea
                  ref={clientConfigRef}
                  value={clientConfig}
                  onChange={event => setClientConfig(event.target.value)}
                  rows={8}
                  aria-label="MCP 客户端 JSON"
                  placeholder={'{\n  "mcpServers": {\n    "api-hub": {\n      "command": "npx",\n      "args": ["-y", "mcp-remote", "https://example.com/mcp"]\n    }\n  }\n}'}
                  className="w-full resize-none overflow-hidden rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-700 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10"
                />
                <button type="button" onClick={applyClientConfig} disabled={!clientConfig.trim()} className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-teal-700 transition-colors hover:border-teal-200 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-45">
                  解析并填入下方表单
                </button>
              </div>
            </details>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-xs font-medium text-slate-600">
              名称 <span className="text-red-500">*</span>
              <input value={name} disabled={!!server} onChange={event => setName(event.target.value)} placeholder="knowledge_search" className="mt-1.5 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono text-sm text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10 disabled:bg-slate-100 disabled:text-slate-500" />
            </label>
            <label className="block text-xs font-medium text-slate-600">
              传输方式 <span className="text-red-500">*</span>
              <select value={transport} onChange={event => setTransport(event.target.value as McpTransport)} className="mt-1.5 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10">
                <option value="streamable_http">Streamable HTTP（推荐）</option>
                <option value="sse">SSE（旧版兼容）</option>
                <option value="stdio">stdio（启动本地进程）</option>
              </select>
            </label>
          </div>

          {transport === 'stdio' ? (
            <>
              <label className="block text-xs font-medium text-slate-600">
                command <span className="text-red-500">*</span>
                <input value={command} onChange={event => setCommand(event.target.value)} placeholder="npx" className="mt-1.5 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono text-sm text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10" />
              </label>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="block text-xs font-medium text-slate-600">
                  args JSON
                  <textarea value={args} onChange={event => setArgs(event.target.value)} rows={5} placeholder={'["-y", "@example/mcp-server"]'} className="mt-1.5 w-full resize-y rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10" />
                </label>
                <label className="block text-xs font-medium text-slate-600">
                  env JSON
                  <textarea value={env} onChange={event => setEnv(event.target.value)} rows={5} placeholder={server ? `留空保持现有环境变量（${server.env_names.join(', ') || '无'}）` : '{\n  "API_KEY": "…"\n}'} className="mt-1.5 w-full resize-y rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10" />
                </label>
              </div>
              <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">stdio 会在后端容器内启动进程，部署方必须显式启用并允许该 command。环境变量会加密存储且不会回显。</p>
            </>
          ) : (
            <>
              <label className="block text-xs font-medium text-slate-600">
                MCP URL <span className="text-red-500">*</span>
                <input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://mcp.example.com/mcp" className="mt-1.5 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10" />
                <span className="mt-1.5 block text-[11px] font-normal leading-4 text-slate-500">公网地址可直接连接；生产环境会拒绝环回、内网和链路本地地址。</span>
              </label>
              <label className="block text-xs font-medium text-slate-600">
                请求头 JSON
                <textarea value={headers} onChange={event => setHeaders(event.target.value)} rows={4} placeholder={server ? `留空保持现有请求头（${server.header_names.join(', ') || '无'}）` : '{\n  "Authorization": "Bearer …"\n}'} className="mt-1.5 w-full resize-y rounded-xl border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10" />
              </label>
            </>
          )}

          {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs leading-5 text-red-700">{error}</p>}
        </div>

        <footer className="flex shrink-0 justify-end gap-3 border-t border-slate-200 bg-slate-50/70 px-5 py-4 sm:px-6">
          <button type="button" onClick={onClose} className="min-h-10 min-w-24 rounded-xl border border-slate-200 bg-white px-4 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400">取消</button>
          <button type="button" onClick={save} disabled={busy || !name.trim() || (transport === 'stdio' ? !command.trim() : !url.trim())} className="inline-flex min-h-10 min-w-24 items-center justify-center gap-2 rounded-xl bg-teal-700 px-4 text-xs font-medium text-white transition-colors hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-45">
            {busy && <Loader2 size={13} className="animate-spin motion-reduce:animate-none" />} 保存
          </button>
        </footer>
      </section>
    </div>
  )
}
