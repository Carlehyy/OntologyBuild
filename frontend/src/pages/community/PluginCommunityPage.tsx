import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  Code2,
  FileUp,
  Loader2,
  Pencil,
  PlugZap,
  Plus,
  Search,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'

import { communityApi } from '@/api/community'
import type { McpTool, SuperMcpServer } from '@/api/superAssistant'
import ConfirmDialog from '@/components/ConfirmDialog'
import McpServerDialog from '@/components/mcp/McpServerDialog'
import { useToast } from '@/components/ui/Toast'


type StatusFilter = 'all' | 'success' | 'error' | 'untested'

const errorText = (error: any, fallback = '操作失败') =>
  error?.detail || error?.message || fallback

const serverTitle = (server: SuperMcpServer) =>
  server.display_name || server.name

const transportLabel = (server: SuperMcpServer) => {
  if (server.transport === 'streamable_http') return 'Streamable HTTP'
  return server.transport.toUpperCase()
}

const endpointText = (server: SuperMcpServer) => {
  if (server.transport === 'stdio') return [server.command, ...server.args].filter(Boolean).join(' ')
  return server.url
}

const exportable = (server: SuperMcpServer) => server.tool_manifest.length > 0

const exportHint = (server: SuperMcpServer) =>
  server.transport === 'streamable_http'
    ? '生成的接口为单发 JSON-RPC（tools/call）POST 直连该 MCP Server，仅适用于无状态服务；要求握手/会话头的有状态服务可能无法直接调用。'
    : '该传输无法直连导出：生成的接口由平台桥接调用（服务端以原生传输执行 MCP 调用后回传结果），凭据保留在服务端。'

const schemaPlaceholder = (schema: any): unknown => {
  if (!schema || typeof schema !== 'object') return ''
  if ('default' in schema) return schema.default
  switch (schema.type) {
    case 'object': return {}
    case 'array': return []
    case 'integer':
    case 'number': return 0
    case 'boolean': return false
    default: return ''
  }
}

const argumentsTemplate = (inputSchema: Record<string, unknown> | undefined) => {
  const properties = (inputSchema as { properties?: Record<string, unknown> } | undefined)?.properties || {}
  return Object.fromEntries(
    Object.entries(properties).map(([key, schema]) => [key, schemaPlaceholder(schema)]),
  )
}

const jsonRpcExample = (tool: McpTool) => JSON.stringify({
  jsonrpc: '2.0',
  id: 1,
  method: 'tools/call',
  params: { name: tool.name, arguments: argumentsTemplate(tool.input_schema) },
}, null, 2)

function TestStatus({ server }: { server: SuperMcpServer }) {
  if (server.last_test_status === 'success') {
    return <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700"><CheckCircle2 size={12} /> 已通过</span>
  }
  if (server.last_test_status === 'error') {
    return <span className="inline-flex items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-[11px] font-medium text-red-700"><CircleAlert size={12} /> 异常</span>
  }
  return <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-500"><Clock3 size={12} /> 未测试</span>
}

function ActionButton({
  title,
  ariaLabel,
  onClick,
  disabled,
  danger,
  children,
}: {
  title: string
  ariaLabel: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      className={`flex w-11 flex-col items-center gap-0.5 rounded py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 disabled:cursor-not-allowed disabled:opacity-40 ${
        danger
          ? 'text-slate-400 hover:bg-red-50 hover:text-red-600'
          : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'
      }`}
    >
      {children}
    </button>
  )
}

function ToolManifestDialog({ server, onClose }: { server: SuperMcpServer; onClose: () => void }) {
  const tools = server.tool_manifest || []
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-[2px]" onMouseDown={onClose}>
      <section role="dialog" aria-modal="true" aria-labelledby="mcp-tools-title" onMouseDown={event => event.stopPropagation()} className="flex max-h-[82dvh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_28px_90px_rgba(15,23,42,0.24)]">
        <header className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <h2 id="mcp-tools-title" className="truncate text-base font-semibold text-slate-900">{serverTitle(server)} · 工具清单</h2>
            <p className="mt-1 text-xs text-slate-500">最近一次测试发现 {tools.length} 个工具；标识 {server.name}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭工具清单" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><X size={17} /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4 text-xs leading-5 text-slate-600">
            <p className="font-medium text-slate-700">调用方式</p>
            <p className="mt-1.5">协议：MCP JSON-RPC 2.0，方法 <code className="rounded bg-white px-1 font-mono text-[11px] text-teal-700 ring-1 ring-slate-200">tools/call</code>，参数为 <code className="rounded bg-white px-1 font-mono text-[11px] text-teal-700 ring-1 ring-slate-200">{'{ name, arguments }'}</code>。</p>
            {server.transport === 'stdio' ? (
              <p className="mt-1.5">地址：本地进程 <span className="break-all font-mono text-[11px] text-slate-500">{endpointText(server) || '未配置 command'}</span>（stdio 由平台进程托管，不提供直连地址）。</p>
            ) : (
              <p className="mt-1.5">地址：<span className="break-all font-mono text-[11px] text-slate-500">{server.url}</span>（{transportLabel(server)}，需携带已配置的请求头）。</p>
            )}
          </div>
          {tools.length === 0 ? (
            <div className="mt-3 rounded-2xl border border-dashed border-slate-200 p-10 text-center">
              <Code2 size={26} className="mx-auto text-slate-300" />
              <p className="mt-3 text-sm font-medium text-slate-600">暂无可用工具</p>
              <p className="mt-1 text-xs text-slate-400">请先关闭窗口并执行连接测试。</p>
            </div>
          ) : (
            <div className="mt-3 space-y-3">
              {tools.map((tool: McpTool) => (
                <article key={tool.name} className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                  <div className="flex items-start gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-teal-700 ring-1 ring-slate-200"><Code2 size={15} /></span>
                    <div className="min-w-0 flex-1">
                      <h3 className="break-all font-mono text-xs font-semibold text-slate-800">{tool.name}</h3>
                      <p className="mt-1 text-xs leading-5 text-slate-500">{tool.description || '暂无工具描述'}</p>
                      <details className="mt-2 text-xs">
                        <summary className="cursor-pointer font-medium text-teal-700">输入参数（JSON Schema）</summary>
                        <pre className="mt-1.5 max-h-48 overflow-auto rounded-lg bg-white p-3 font-mono text-[11px] leading-4 text-slate-600 ring-1 ring-slate-200">{JSON.stringify(tool.input_schema ?? {}, null, 2)}</pre>
                      </details>
                      <details className="mt-1.5 text-xs">
                        <summary className="cursor-pointer font-medium text-teal-700">请求示例（tools/call）</summary>
                        <pre className="mt-1.5 max-h-56 overflow-auto rounded-lg bg-white p-3 font-mono text-[11px] leading-4 text-slate-600 ring-1 ring-slate-200">{jsonRpcExample(tool)}</pre>
                      </details>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function ExportToolsDialog({ server, onClose, onDone }: { server: SuperMcpServer; onClose: () => void; onDone: () => void }) {
  const { toast } = useToast()
  const tools = server.tool_manifest || []
  const [selected, setSelected] = useState<Set<string>>(() => new Set(tools.map(tool => tool.name)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const toggle = (name: string) => {
    setSelected(current => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const submit = async () => {
    if (!selected.size || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await communityApi.exportMcpTools(server.id, [...selected])
      const parts = [`已生成 ${result.created.length} 个 HTTP 接口`]
      if (result.skipped.length) parts.push(`${result.skipped.length} 个同名已跳过`)
      toast({
        tone: result.created.length ? 'success' : 'warning',
        title: '工具已导出至接口代理',
        description: `${parts.join('，')}；请前往「接口代理 · 接口管理」的「MCP 插件」分组查看。`,
      })
      onDone()
      onClose()
    } catch (exportError) {
      setError(errorText(exportError, '导出失败，请稍后重试。'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-[2px]" onMouseDown={() => !busy && onClose()}>
      <section role="dialog" aria-modal="true" aria-labelledby="mcp-export-title" onMouseDown={event => event.stopPropagation()} className="flex max-h-[82dvh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_28px_90px_rgba(15,23,42,0.24)]">
        <header className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <h2 id="mcp-export-title" className="text-base font-semibold text-slate-900">转接口 · {serverTitle(server)}</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">勾选要生成 HTTP 接口的工具；生成后可在「接口代理 · 接口管理」的「MCP 插件」分组中查看。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="关闭转接口弹窗" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><X size={17} /></button>
        </header>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-5">
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">{exportHint(server)}</p>
          {tools.map(tool => (
            <label key={tool.name} className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-white p-3 text-xs transition-colors hover:border-teal-200">
              <input
                type="checkbox"
                checked={selected.has(tool.name)}
                onChange={() => toggle(tool.name)}
                aria-label={`选择工具 ${tool.name}`}
                className="mt-0.5 h-4 w-4 shrink-0 accent-teal-600"
              />
              <span className="min-w-0">
                <span className="block break-all font-mono font-semibold text-slate-800">{tool.name}</span>
                <span className="mt-0.5 block leading-5 text-slate-500">{tool.description || '暂无工具描述'}</span>
              </span>
            </label>
          ))}
          {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs leading-5 text-red-700">{error}</p>}
        </div>
        <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 bg-slate-50/70 px-5 py-4">
          <div className="flex gap-3 text-xs text-slate-500">
            <button type="button" onClick={() => setSelected(new Set(tools.map(tool => tool.name)))} className="transition-colors hover:text-slate-900">全选</button>
            <button type="button" onClick={() => setSelected(new Set())} className="transition-colors hover:text-slate-900">全不选</button>
          </div>
          <div className="flex gap-3">
            <button type="button" onClick={onClose} disabled={busy} className="min-h-10 min-w-24 rounded-xl border border-slate-200 bg-white px-4 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400">取消</button>
            <button type="button" onClick={() => void submit()} disabled={busy || !selected.size} className="inline-flex min-h-10 min-w-24 items-center justify-center gap-2 rounded-xl bg-teal-700 px-4 text-xs font-medium text-white transition-colors hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-45">
              {busy && <Loader2 size={13} className="animate-spin motion-reduce:animate-none" />} 生成接口
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}

export default function PluginCommunityPage() {
  const { toast } = useToast()
  const [servers, setServers] = useState<SuperMcpServer[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [editing, setEditing] = useState<SuperMcpServer | 'new' | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SuperMcpServer | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [manifestTarget, setManifestTarget] = useState<SuperMcpServer | null>(null)
  const [exportTarget, setExportTarget] = useState<SuperMcpServer | null>(null)

  const load = useCallback(async () => {
    try {
      const items = await communityApi.mcpServers()
      setServers(Array.isArray(items) ? items.filter(item => !item.builtin_key) : [])
    } catch (error) {
      setServers([])
      toast({ tone: 'error', title: 'MCP 清单加载失败', description: errorText(error, '请检查服务连接后重试。') })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { void load() }, [load])

  const filteredServers = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return servers.filter(server => {
      const matchesKeyword = !keyword || [
        server.name,
        server.display_name,
        server.description,
        endpointText(server),
        transportLabel(server),
        ...server.tool_manifest.flatMap(tool => [tool.name, tool.description]),
      ].some(value => String(value || '').toLowerCase().includes(keyword))
      const matchesStatus = statusFilter === 'all'
        || (statusFilter === 'success' && server.last_test_status === 'success')
        || (statusFilter === 'error' && server.last_test_status === 'error')
        || (statusFilter === 'untested' && !server.last_test_status)
      return matchesKeyword && matchesStatus
    })
  }, [search, servers, statusFilter])

  const stats = useMemo(() => ({
    total: servers.length,
    healthy: servers.filter(server => server.last_test_status === 'success').length,
    tools: servers.reduce((sum, server) => sum + server.tool_manifest.length, 0),
  }), [servers])

  const testServer = async (server: SuperMcpServer) => {
    setTestingId(server.id)
    try {
      const result = await communityApi.testMcpServer(server.id)
      await load()
      toast({
        tone: result.ok ? 'success' : 'error',
        title: result.ok ? 'MCP 连接成功' : 'MCP 连接失败',
        description: result.message,
      })
    } catch (error) {
      toast({ tone: 'error', title: 'MCP 测试失败', description: errorText(error, '请稍后重试。') })
    } finally {
      setTestingId(null)
    }
  }

  const removeServer = async () => {
    if (!deleteTarget || deleting) return
    setDeleting(true)
    try {
      await communityApi.deleteMcpServer(deleteTarget.id)
      setServers(current => current.filter(item => item.id !== deleteTarget.id))
      toast({ tone: 'success', title: 'MCP Server 已删除', description: `「${serverTitle(deleteTarget)}」已从清单移除。` })
      setDeleteTarget(null)
    } catch (error) {
      toast({ tone: 'error', title: '删除失败', description: errorText(error, '请稍后重试。') })
    } finally {
      setDeleting(false)
    }
  }

  const renderActions = (server: SuperMcpServer) => (
    <div className="flex items-center justify-center gap-0.5">
      <ActionButton
        title="测试连接并刷新工具清单"
        ariaLabel={`测试 MCP ${serverTitle(server)}`}
        onClick={() => void testServer(server)}
        disabled={testingId === server.id}
      >
        {testingId === server.id
          ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
          : <Wrench size={14} />}
        <span className="text-[10px] leading-3">测试</span>
      </ActionButton>
      <ActionButton
        title={server.tool_manifest.length
          ? '转接口：将工具生成为接口代理的 HTTP 接口'
          : '尚未发现工具，请先执行连接测试'}
        ariaLabel={`转接口 ${serverTitle(server)}`}
        onClick={() => setExportTarget(server)}
        disabled={!exportable(server)}
      >
        <FileUp size={14} />
        <span className="text-[10px] leading-3">转接口</span>
      </ActionButton>
      <ActionButton
        title="编辑名称、描述与连接配置"
        ariaLabel={`编辑 MCP ${serverTitle(server)}`}
        onClick={() => setEditing(server)}
      >
        <Pencil size={14} />
        <span className="text-[10px] leading-3">编辑</span>
      </ActionButton>
      <ActionButton
        title="删除该 MCP Server 及其工具清单"
        ariaLabel={`删除 MCP ${serverTitle(server)}`}
        onClick={() => setDeleteTarget(server)}
        danger
      >
        <Trash2 size={14} />
        <span className="text-[10px] leading-3">删除</span>
      </ActionButton>
    </div>
  )

  return (
    <div className="flex min-h-full flex-col gap-4 md:h-full md:min-h-0">
      <h1 className="sr-only">插件社区</h1>

      <section aria-label="MCP 筛选与操作" className="flex shrink-0 flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 xl:flex-nowrap">
        <div className="relative w-full sm:w-64 xl:w-72 xl:flex-none">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder="搜索名称、标识、地址或工具..."
            aria-label="搜索 MCP"
            className="w-full rounded-xl border border-slate-200 py-2 pl-8 pr-8 text-sm outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10"
          />
          {search && (
            <button type="button" onClick={() => setSearch('')} aria-label="清除 MCP 搜索" className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 transition-colors hover:text-slate-900">
              <X size={12} />
            </button>
          )}
        </div>
        <select
          value={statusFilter}
          onChange={event => setStatusFilter(event.target.value as StatusFilter)}
          aria-label="筛选 MCP 状态"
          className="shrink-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10"
        >
          <option value="all">全部状态</option>
          <option value="success">测试通过</option>
          <option value="error">连接异常</option>
          <option value="untested">未测试</option>
        </select>
        {(search || statusFilter !== 'all') && (
          <button type="button" onClick={() => { setSearch(''); setStatusFilter('all') }} className="shrink-0 px-2 py-1 text-xs text-slate-500 transition-colors hover:text-slate-900">
            清除筛选
          </button>
        )}
        <p className="ml-auto shrink-0 text-xs tabular-nums text-slate-400">显示 {filteredServers.length} / {servers.length} 项</p>
        <button type="button" onClick={() => setEditing('new')} className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-teal-700 px-3.5 py-2 text-sm font-medium text-white transition hover:bg-teal-800 active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2">
          <Plus size={15} /> 添加 MCP
        </button>
      </section>

      {loading ? (
        <div className="flex min-h-64 flex-1 items-center justify-center rounded-2xl border border-slate-200 bg-white">
          <div className="text-center"><Loader2 size={24} className="mx-auto animate-spin text-teal-600 motion-reduce:animate-none" /><p className="mt-3 text-sm text-slate-500">正在加载 MCP 清单...</p></div>
        </div>
      ) : filteredServers.length === 0 ? (
        <div className="flex min-h-64 flex-1 flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 p-8 text-center text-slate-400">
          <PlugZap size={30} className="text-slate-300" />
          <p className="mt-3 text-sm font-medium text-slate-700">{servers.length ? '没有匹配的 MCP Server' : '暂无 MCP Server'}</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">{servers.length ? '调整搜索词或状态筛选后重试。' : '登记 MCP 配置并完成连接测试后，即可查看其工具清单。'}</p>
          {!servers.length && <button type="button" onClick={() => setEditing('new')} className="mt-4 inline-flex min-h-10 items-center gap-2 rounded-xl bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800"><Plus size={14} /> 添加第一个 MCP</button>}
        </div>
      ) : (
        <>
          <section aria-label="MCP Server 清单" className="hidden min-h-64 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] md:flex">
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-full min-w-[960px] table-fixed text-sm">
                <thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50/95 backdrop-blur">
                  <tr>
                    <th className="w-[30%] px-4 py-2.5 text-left text-xs font-medium text-slate-600">MCP Server</th>
                    <th className="w-[14%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">传输方式</th>
                    <th className="w-[13%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">工具</th>
                    <th className="w-[14%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">连接状态</th>
                    <th className="w-[29%] px-2 py-2.5 text-center text-xs font-medium text-slate-600">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {filteredServers.map(server => (
                    <tr key={server.id} className="transition-colors hover:bg-slate-50/80">
                      <td className="px-4 py-3 align-middle">
                        <div className="min-w-0">
                          <p className="truncate font-medium text-slate-900" title={serverTitle(server)}>{serverTitle(server)}</p>
                          <p className="mt-0.5 truncate text-[11px] leading-4 text-slate-400" title={`${server.name} · ${endpointText(server)}`}>
                            <span className="font-mono">{server.name}</span> · <span className="font-mono">{endpointText(server) || '尚未配置地址'}</span>
                          </p>
                          {server.description && <p className="mt-1 line-clamp-1 text-xs leading-4 text-slate-500" title={server.description}>{server.description}</p>}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center align-middle"><span className="inline-flex rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-600">{transportLabel(server)}</span></td>
                      <td className="px-4 py-3 text-center align-middle"><button type="button" onClick={() => setManifestTarget(server)} aria-label={`查看 ${serverTitle(server)} 的工具清单`} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><Code2 size={13} /> 共 {server.tool_manifest.length} 个</button></td>
                      <td className="px-4 py-3 text-center align-middle"><TestStatus server={server} /></td>
                      <td className="px-2 py-2 align-middle">{renderActions(server)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div data-testid="mcp-server-stats" className="flex shrink-0 flex-wrap items-center justify-center gap-x-4 gap-y-1 border-t border-slate-100 bg-slate-50/50 px-4 py-2.5 text-center text-xs tabular-nums text-slate-500">
              <span>共 {stats.total} 个 MCP Server</span>
              <span><strong className="font-medium text-emerald-700">{stats.healthy}</strong> 个测试通过</span>
              <span><strong className="font-medium text-slate-700">{stats.tools}</strong> 个已发现工具</span>
            </div>
          </section>

          <div className="grid gap-3 md:hidden">
            {filteredServers.map(server => (
              <article key={server.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_28px_rgba(15,23,42,0.04)]">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold text-slate-900">{serverTitle(server)}</h2><p className="mt-1 truncate font-mono text-[10px] text-slate-400">{server.name} · {endpointText(server)}</p></div>
                  <TestStatus server={server} />
                </div>
                {server.description && <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-500">{server.description}</p>}
                <div className="mt-3 flex flex-wrap gap-1.5"><span className="rounded-lg bg-slate-100 px-2 py-1 text-[10px] text-slate-600">{transportLabel(server)}</span><button type="button" onClick={() => setManifestTarget(server)} className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-teal-50 px-2 text-[10px] font-medium text-teal-700"><Code2 size={11} /> 共 {server.tool_manifest.length} 个工具</button></div>
                {server.last_test_message && <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">{server.last_test_message}</p>}
                <div className="mt-3 border-t border-slate-100 pt-2">{renderActions(server)}</div>
              </article>
            ))}
            <p className="px-1 text-center text-xs tabular-nums text-slate-400">共 {stats.total} 项 · {stats.healthy} 项测试通过 · {stats.tools} 个工具</p>
          </div>
        </>
      )}

      {editing && <McpServerDialog server={editing === 'new' ? undefined : editing} client={communityApi} onClose={() => setEditing(null)} onSaved={load} />}
      {manifestTarget && <ToolManifestDialog server={servers.find(item => item.id === manifestTarget.id) || manifestTarget} onClose={() => setManifestTarget(null)} />}
      {exportTarget && (
        <ExportToolsDialog
          server={servers.find(item => item.id === exportTarget.id) || exportTarget}
          onClose={() => setExportTarget(null)}
          onDone={load}
        />
      )}
      <ConfirmDialog open={!!deleteTarget} title="删除 MCP Server" message={`确认删除 MCP Server「${deleteTarget ? serverTitle(deleteTarget) : ''}」？相关连接配置和工具清单将一并移除，此操作无法撤销。`} confirmLabel={deleting ? '删除中...' : '确认删除'} onConfirm={() => void removeServer()} onCancel={() => !deleting && setDeleteTarget(null)} />
    </div>
  )
}
