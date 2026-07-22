import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  Code2,
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

const transportLabel = (server: SuperMcpServer) => {
  if (server.transport === 'streamable_http') return 'Streamable HTTP'
  return server.transport.toUpperCase()
}

const endpointText = (server: SuperMcpServer) => {
  if (server.transport === 'stdio') return [server.command, ...server.args].filter(Boolean).join(' ')
  return server.url
}

function TestStatus({ server }: { server: SuperMcpServer }) {
  if (server.last_test_status === 'success') {
    return <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700"><CheckCircle2 size={12} /> 已通过</span>
  }
  if (server.last_test_status === 'error') {
    return <span className="inline-flex items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-[11px] font-medium text-red-700"><CircleAlert size={12} /> 异常</span>
  }
  return <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-500"><Clock3 size={12} /> 未测试</span>
}

function ToolManifestDialog({ server, onClose }: { server: SuperMcpServer; onClose: () => void }) {
  const tools = server.tool_manifest || []
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-[2px]" onMouseDown={onClose}>
      <section role="dialog" aria-modal="true" aria-labelledby="mcp-tools-title" onMouseDown={event => event.stopPropagation()} className="flex max-h-[82dvh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_28px_90px_rgba(15,23,42,0.24)]">
        <header className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <h2 id="mcp-tools-title" className="truncate text-base font-semibold text-slate-900">{server.name} · 工具清单</h2>
            <p className="mt-1 text-xs text-slate-500">最近一次测试发现 {tools.length} 个工具</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭工具清单" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><X size={17} /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {tools.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-200 p-10 text-center">
              <Code2 size={26} className="mx-auto text-slate-300" />
              <p className="mt-3 text-sm font-medium text-slate-600">暂无可用工具</p>
              <p className="mt-1 text-xs text-slate-400">请先关闭窗口并执行连接测试。</p>
            </div>
          ) : (
            <div className="space-y-3">
              {tools.map((tool: McpTool) => (
                <article key={tool.name} className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                  <div className="flex items-start gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-teal-700 ring-1 ring-slate-200"><Code2 size={15} /></span>
                    <div className="min-w-0 flex-1">
                      <h3 className="break-all font-mono text-xs font-semibold text-slate-800">{tool.name}</h3>
                      <p className="mt-1 text-xs leading-5 text-slate-500">{tool.description || '暂无工具描述'}</p>
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
      toast({ tone: 'success', title: 'MCP Server 已删除', description: `「${deleteTarget.name}」已从清单移除。` })
      setDeleteTarget(null)
    } catch (error) {
      toast({ tone: 'error', title: '删除失败', description: errorText(error, '请稍后重试。') })
    } finally {
      setDeleting(false)
    }
  }

  const renderActions = (server: SuperMcpServer) => (
    <div className="flex items-center justify-end gap-1">
      <button type="button" onClick={() => void testServer(server)} disabled={testingId === server.id} aria-label={`测试 MCP ${server.name}`} title="测试连接" className="flex h-10 w-10 items-center justify-center rounded-lg text-teal-700 transition-colors hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 disabled:cursor-wait disabled:opacity-50">
        {testingId === server.id ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" /> : <Wrench size={14} />}
      </button>
      <button type="button" onClick={() => setEditing(server)} aria-label={`编辑 MCP ${server.name}`} className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><Pencil size={14} /></button>
      <button type="button" onClick={() => setDeleteTarget(server)} aria-label={`删除 MCP ${server.name}`} className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"><Trash2 size={14} /></button>
    </div>
  )

  return (
    <div className="space-y-4 pb-4">
      <h1 className="sr-only">插件社区</h1>

      <section aria-label="MCP 筛选与操作" className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 xl:flex-nowrap">
        <div className="relative w-full sm:w-64 xl:w-72 xl:flex-none">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder="搜索名称、地址或工具..."
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
        <div className="flex min-h-64 items-center justify-center rounded-2xl border border-slate-200 bg-white">
          <div className="text-center"><Loader2 size={24} className="mx-auto animate-spin text-teal-600 motion-reduce:animate-none" /><p className="mt-3 text-sm text-slate-500">正在加载 MCP 清单...</p></div>
        </div>
      ) : filteredServers.length === 0 ? (
        <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 p-8 text-center text-slate-400">
          <PlugZap size={30} className="text-slate-300" />
          <p className="mt-3 text-sm font-medium text-slate-700">{servers.length ? '没有匹配的 MCP Server' : '暂无 MCP Server'}</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">{servers.length ? '调整搜索词或状态筛选后重试。' : '登记 MCP 配置并完成连接测试后，即可查看其工具清单。'}</p>
          {!servers.length && <button type="button" onClick={() => setEditing('new')} className="mt-4 inline-flex min-h-10 items-center gap-2 rounded-xl bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800"><Plus size={14} /> 添加第一个 MCP</button>}
        </div>
      ) : (
        <>
          <div className="hidden overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] md:block">
            <table className="w-full min-w-[780px] table-fixed text-sm">
              <thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50/95 backdrop-blur">
                <tr>
                  <th className="w-[34%] px-4 py-2.5 text-left text-xs font-medium text-slate-600">MCP Server</th>
                  <th className="w-[18%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">传输方式</th>
                  <th className="w-[16%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">工具</th>
                  <th className="w-[17%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">连接状态</th>
                  <th className="w-[15%] px-4 py-2.5 text-center text-xs font-medium text-slate-600">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredServers.map(server => (
                  <tr key={server.id} className="align-middle transition-colors hover:bg-slate-50/80">
                    <td className="px-4 py-3">
                      <div className="min-w-0"><p className="truncate font-medium text-slate-900" title={server.name}>{server.name}</p><p className="mt-0.5 truncate font-mono text-[11px] text-slate-400" title={endpointText(server)}>{endpointText(server) || '尚未配置地址'}</p></div>
                    </td>
                    <td className="px-4 py-3 text-center"><span className="inline-flex rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-600">{transportLabel(server)}</span></td>
                    <td className="px-4 py-3 text-center"><button type="button" onClick={() => setManifestTarget(server)} aria-label={`查看 ${server.name} 的工具清单`} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><Code2 size={13} /> 共 {server.tool_manifest.length} 个</button></td>
                    <td className="px-4 py-3 text-center"><TestStatus server={server} /></td>
                    <td className="px-4 py-3">{renderActions(server)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 border-t border-slate-100 bg-slate-50/50 px-4 py-2.5 text-xs tabular-nums text-slate-500">
              <span>共 {stats.total} 个 MCP Server</span>
              <span><strong className="font-medium text-emerald-700">{stats.healthy}</strong> 个测试通过</span>
              <span><strong className="font-medium text-slate-700">{stats.tools}</strong> 个已发现工具</span>
            </div>
          </div>

          <div className="grid gap-3 md:hidden">
            {filteredServers.map(server => (
              <article key={server.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_28px_rgba(15,23,42,0.04)]">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold text-slate-900">{server.name}</h2><p className="mt-1 truncate font-mono text-[10px] text-slate-400">{endpointText(server)}</p></div>
                  <TestStatus server={server} />
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5"><span className="rounded-lg bg-slate-100 px-2 py-1 text-[10px] text-slate-600">{transportLabel(server)}</span><button type="button" onClick={() => setManifestTarget(server)} className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-teal-50 px-2 text-[10px] font-medium text-teal-700"><Code2 size={11} /> 共 {server.tool_manifest.length} 个工具</button></div>
                {server.last_test_message && <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">{server.last_test_message}</p>}
                <div className="mt-3 flex justify-end border-t border-slate-100 pt-2">{renderActions(server)}</div>
              </article>
            ))}
            <p className="px-1 text-right text-xs tabular-nums text-slate-400">共 {stats.total} 项 · {stats.healthy} 项测试通过 · {stats.tools} 个工具</p>
          </div>
        </>
      )}

      {editing && <McpServerDialog server={editing === 'new' ? undefined : editing} client={communityApi} onClose={() => setEditing(null)} onSaved={load} />}
      {manifestTarget && <ToolManifestDialog server={servers.find(item => item.id === manifestTarget.id) || manifestTarget} onClose={() => setManifestTarget(null)} />}
      <ConfirmDialog open={!!deleteTarget} title="删除 MCP Server" message={`确认删除 MCP Server「${deleteTarget?.name || ''}」？相关连接配置和工具清单将一并移除，此操作无法撤销。`} confirmLabel={deleting ? '删除中...' : '确认删除'} onConfirm={() => void removeServer()} onCancel={() => !deleting && setDeleteTarget(null)} />
    </div>
  )
}
