import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Boxes,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Code2,
  Database,
  Loader2,
  Pencil,
  PlugZap,
  Plus,
  Power,
  Search,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'

import { communityApi } from '@/api/community'
import type { McpTool, SuperMcpServer } from '@/api/superAssistant'
import ConfirmDialog from '@/components/ConfirmDialog'
import McpServerDialog from '@/components/mcp/McpServerDialog'
import { useToast } from '@/components/ui/Toast'


type StatusFilter = 'all' | 'open' | 'disabled' | 'success' | 'error' | 'untested'

const errorText = (error: any, fallback = '操作失败') =>
  error?.detail || error?.message || fallback

const transportLabel = (server: SuperMcpServer) => {
  if (server.transport === 'streamable_http') return 'Streamable HTTP'
  return server.transport.toUpperCase()
}

const endpointText = (server: SuperMcpServer) => {
  if (server.builtin_key === 'minio') return '平台内置 MinIO'
  if (server.transport === 'stdio') return [server.command, ...server.args].filter(Boolean).join(' ')
  return server.url
}

const formatTime = (value: string | null) => {
  if (!value) return '尚未测试'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

function MetricCard({ icon: Icon, label, value, hint, tone }: {
  icon: React.ElementType
  label: string
  value: number
  hint: string
  tone: 'teal' | 'sky' | 'amber' | 'violet'
}) {
  const toneClass = {
    teal: 'bg-teal-50 text-teal-700 ring-teal-100',
    sky: 'bg-sky-50 text-sky-700 ring-sky-100',
    amber: 'bg-amber-50 text-amber-700 ring-amber-100',
    violet: 'bg-violet-50 text-violet-700 ring-violet-100',
  }[tone]
  return (
    <article className="rounded-2xl border border-white/80 bg-white/75 p-4 shadow-[0_8px_28px_rgba(15,23,42,0.05)] backdrop-blur-xl">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-slate-500">{label}</p>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">{value}</p>
        </div>
        <span className={`flex h-10 w-10 items-center justify-center rounded-xl ring-1 ${toneClass}`}>
          <Icon size={18} />
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-4 text-slate-400">{hint}</p>
    </article>
  )
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

function OpenSwitch({ server, busy, onToggle }: {
  server: SuperMcpServer
  busy: boolean
  onToggle: () => void
}) {
  const canOpen = server.enabled || (server.last_test_status === 'success' && server.tool_manifest.length > 0)
  const label = server.enabled ? `停用 MCP ${server.name}` : `开放 MCP ${server.name}`
  return (
    <div className="inline-flex items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-label={label}
        aria-checked={server.enabled}
        disabled={busy || !canOpen}
        onClick={onToggle}
        title={!canOpen ? '请先完成连接测试并发现至少一个工具' : label}
        className="relative inline-flex h-11 w-11 shrink-0 touch-manipulation items-center justify-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/40 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-45"
      >
        {busy ? (
          <Loader2 size={15} className="animate-spin text-teal-700 motion-reduce:animate-none" />
        ) : (
          <span aria-hidden="true" className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors motion-reduce:transition-none ${server.enabled ? 'bg-teal-600' : 'bg-slate-300'}`}>
            <span className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform motion-reduce:transition-none ${server.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
          </span>
        )}
      </button>
      <span className={`whitespace-nowrap text-xs font-medium ${server.enabled ? 'text-teal-700' : 'text-slate-500'}`}>{server.enabled ? '已开放' : '已停用'}</span>
    </div>
  )
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
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SuperMcpServer | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [installingMinio, setInstallingMinio] = useState(false)
  const [manifestTarget, setManifestTarget] = useState<SuperMcpServer | null>(null)

  const load = useCallback(async () => {
    try {
      const items = await communityApi.mcpServers()
      setServers(Array.isArray(items) ? items : [])
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
        || (statusFilter === 'open' && server.enabled)
        || (statusFilter === 'disabled' && !server.enabled)
        || (statusFilter === 'success' && server.last_test_status === 'success')
        || (statusFilter === 'error' && server.last_test_status === 'error')
        || (statusFilter === 'untested' && !server.last_test_status)
      return matchesKeyword && matchesStatus
    })
  }, [search, servers, statusFilter])

  const stats = useMemo(() => ({
    total: servers.length,
    open: servers.filter(server => server.enabled).length,
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

  const toggleServer = async (server: SuperMcpServer) => {
    const nextEnabled = !server.enabled
    if (nextEnabled && (server.last_test_status !== 'success' || server.tool_manifest.length === 0)) {
      toast({ tone: 'warning', title: '请先测试 MCP', description: '连接测试通过并发现工具后，才能开放到超级助手。' })
      return
    }
    setTogglingId(server.id)
    try {
      const updated = await communityApi.updateMcpServer(server.id, { enabled: nextEnabled })
      setServers(current => current.map(item => item.id === updated.id ? updated : item))
      toast({
        tone: 'success',
        title: nextEnabled ? 'MCP 已开放' : 'MCP 已停用',
        description: nextEnabled
          ? `「${server.name}」的工具已进入超级助手工具目录。`
          : `「${server.name}」已从超级助手工具目录移除。`,
      })
    } catch (error) {
      toast({ tone: 'error', title: 'MCP 状态更新失败', description: errorText(error, '请稍后重试。') })
    } finally {
      setTogglingId(null)
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

  const installPlatformMinio = async () => {
    setInstallingMinio(true)
    try {
      const server = await communityApi.installPlatformMinio()
      await load()
      toast({ tone: 'success', title: '平台 MinIO MCP 已添加', description: `已发现 ${server.tool_manifest.length} 个工具，默认执行前确认。` })
    } catch (error) {
      toast({ tone: 'error', title: '无法添加平台 MinIO', description: errorText(error, '请联系管理员先完成 MinIO 配置。') })
    } finally {
      setInstallingMinio(false)
    }
  }

  const platformMinio = servers.find(server => server.builtin_key === 'minio')

  const renderActions = (server: SuperMcpServer) => (
    <div className="flex items-center justify-end gap-1">
      <button type="button" onClick={() => void testServer(server)} disabled={testingId === server.id} aria-label={`测试 MCP ${server.name}`} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 disabled:cursor-wait disabled:opacity-50">
        {testingId === server.id ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" /> : <Wrench size={14} />} 测试
      </button>
      {!server.builtin_key && (
        <button type="button" onClick={() => setEditing(server)} aria-label={`编辑 MCP ${server.name}`} className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><Pencil size={14} /></button>
      )}
      <button type="button" onClick={() => setDeleteTarget(server)} aria-label={`删除 MCP ${server.name}`} className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"><Trash2 size={14} /></button>
    </div>
  )

  return (
    <div className="relative min-h-full space-y-5 pb-4">
      <div aria-hidden="true" className="pointer-events-none fixed right-12 top-20 h-64 w-64 rounded-full bg-teal-200/20 blur-3xl" />
      <header className="relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-teal-700"><PlugZap size={14} /> 开放社区</div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">插件社区</h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-6 text-slate-500">统一管理个人 MCP 清单。完成连接测试后开放到超级助手，也可随时停用或调整执行确认策略。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void installPlatformMinio()} disabled={!!platformMinio || installingMinio} title={platformMinio ? '平台 MinIO 已添加' : '管理员完成 MinIO 配置后可直接接入'} className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-sky-200 bg-white/80 px-3.5 text-sm font-medium text-sky-700 shadow-sm transition-colors hover:bg-sky-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-50">
            {installingMinio ? <Loader2 size={15} className="animate-spin motion-reduce:animate-none" /> : platformMinio ? <CheckCircle2 size={15} /> : <Database size={15} />}
            {platformMinio ? 'MinIO 已添加' : '添加平台 MinIO'}
          </button>
          <button type="button" onClick={() => setEditing('new')} className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-teal-700 px-4 text-sm font-medium text-white shadow-sm transition-colors hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2">
            <Plus size={16} /> 添加 MCP
          </button>
        </div>
      </header>

      <section aria-label="MCP 清单统计" className="relative grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard icon={Boxes} label="MCP 总数" value={stats.total} hint="当前用户的全部连接" tone="sky" />
        <MetricCard icon={Power} label="已开放" value={stats.open} hint="已进入助手工具目录" tone="teal" />
        <MetricCard icon={ShieldCheck} label="测试通过" value={stats.healthy} hint="最近一次连接状态正常" tone="amber" />
        <MetricCard icon={Code2} label="已发现工具" value={stats.tools} hint="来自最近一次测试结果" tone="violet" />
      </section>

      <section aria-label="MCP 筛选" className="relative flex flex-col gap-3 rounded-2xl border border-white/80 bg-white/75 p-3 shadow-[0_8px_28px_rgba(15,23,42,0.04)] backdrop-blur-xl sm:flex-row sm:items-center">
        <div className="relative min-w-0 flex-1 sm:max-w-sm">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索名称、地址或工具..." aria-label="搜索 MCP" className="min-h-11 w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-9 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10" />
          {search && <button type="button" onClick={() => setSearch('')} aria-label="清除 MCP 搜索" className="absolute right-1 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"><X size={13} /></button>}
        </div>
        <select value={statusFilter} onChange={event => setStatusFilter(event.target.value as StatusFilter)} aria-label="筛选 MCP 状态" className="min-h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none transition focus:border-teal-500 focus:ring-4 focus:ring-teal-500/10">
          <option value="all">全部状态</option>
          <option value="open">已开放</option>
          <option value="disabled">已停用</option>
          <option value="success">测试通过</option>
          <option value="error">连接异常</option>
          <option value="untested">未测试</option>
        </select>
        <p className="text-xs tabular-nums text-slate-400 sm:ml-auto">显示 {filteredServers.length} / {servers.length} 项</p>
      </section>

      {loading ? (
        <div className="relative flex min-h-64 items-center justify-center rounded-2xl border border-white/80 bg-white/70">
          <div className="text-center"><Loader2 size={24} className="mx-auto animate-spin text-teal-600 motion-reduce:animate-none" /><p className="mt-3 text-sm text-slate-500">正在加载 MCP 清单...</p></div>
        </div>
      ) : filteredServers.length === 0 ? (
        <div className="relative flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white/65 p-8 text-center">
          <PlugZap size={30} className="text-slate-300" />
          <p className="mt-3 text-sm font-medium text-slate-700">{servers.length ? '没有匹配的 MCP Server' : '暂无 MCP Server'}</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">{servers.length ? '调整搜索词或状态筛选后重试。' : '添加配置，测试连接并发现工具后即可开放。'}</p>
          {!servers.length && <button type="button" onClick={() => setEditing('new')} className="mt-4 inline-flex min-h-10 items-center gap-2 rounded-xl bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800"><Plus size={14} /> 添加第一个 MCP</button>}
        </div>
      ) : (
        <>
          <div className="relative hidden overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] md:block">
            <table className="w-full min-w-[1020px] table-fixed text-sm">
              <thead className="border-b border-slate-200 bg-slate-50/95 backdrop-blur">
                <tr>
                  <th className="w-[25%] px-4 py-3 text-left text-xs font-medium text-slate-500">MCP Server</th>
                  <th className="w-[13%] px-4 py-3 text-center text-xs font-medium text-slate-500">传输方式</th>
                  <th className="w-[11%] px-4 py-3 text-center text-xs font-medium text-slate-500">工具</th>
                  <th className="w-[14%] px-4 py-3 text-center text-xs font-medium text-slate-500">连接状态</th>
                  <th className="w-[13%] px-4 py-3 text-center text-xs font-medium text-slate-500">开放状态</th>
                  <th className="w-[11%] px-4 py-3 text-center text-xs font-medium text-slate-500">执行策略</th>
                  <th className="w-[13%] px-4 py-3 text-center text-xs font-medium text-slate-500">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredServers.map(server => (
                  <tr key={server.id} className={`align-middle transition-colors hover:bg-slate-50/80 ${server.enabled ? '' : 'bg-slate-50/30'}`}>
                    <td className="px-4 py-3.5">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${server.builtin_key ? 'bg-violet-50 text-violet-700' : 'bg-sky-50 text-sky-700'}`}><PlugZap size={17} /></span>
                        <div className="min-w-0"><p className="truncate font-medium text-slate-900" title={server.name}>{server.name}</p><p className="mt-0.5 truncate font-mono text-[11px] text-slate-400" title={endpointText(server)}>{endpointText(server) || '尚未配置地址'}</p></div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-center"><span className="inline-flex rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-600">{transportLabel(server)}</span></td>
                    <td className="px-4 py-3.5 text-center"><button type="button" onClick={() => setManifestTarget(server)} aria-label={`查看 ${server.name} 的工具清单`} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><Code2 size={13} /> {server.tool_manifest.length}</button></td>
                    <td className="px-4 py-3.5 text-center"><TestStatus server={server} /><p className="mt-1.5 text-[10px] text-slate-400">{formatTime(server.last_tested_at)}</p></td>
                    <td className="px-4 py-3.5 text-center"><OpenSwitch server={server} busy={togglingId === server.id} onToggle={() => void toggleServer(server)} /></td>
                    <td className="px-4 py-3.5 text-center"><span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium ${server.require_confirmation ? 'bg-amber-50 text-amber-700' : 'bg-teal-50 text-teal-700'}`}>{server.require_confirmation ? '执行前确认' : '自动执行'}</span></td>
                    <td className="px-4 py-3.5">{renderActions(server)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="relative grid gap-3 md:hidden">
            {filteredServers.map(server => (
              <article key={server.id} className="rounded-2xl border border-white/80 bg-white/80 p-4 shadow-[0_8px_28px_rgba(15,23,42,0.05)] backdrop-blur-xl">
                <div className="flex items-start gap-3">
                  <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${server.builtin_key ? 'bg-violet-50 text-violet-700' : 'bg-sky-50 text-sky-700'}`}><PlugZap size={17} /></span>
                  <div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold text-slate-900">{server.name}</h2><p className="mt-1 truncate font-mono text-[10px] text-slate-400">{endpointText(server)}</p></div>
                  <TestStatus server={server} />
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5"><span className="rounded-lg bg-slate-100 px-2 py-1 text-[10px] text-slate-600">{transportLabel(server)}</span><button type="button" onClick={() => setManifestTarget(server)} className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-teal-50 px-2 text-[10px] font-medium text-teal-700"><Code2 size={11} /> {server.tool_manifest.length} 个工具</button><span className="rounded-lg bg-amber-50 px-2 py-1 text-[10px] text-amber-700">{server.require_confirmation ? '执行前确认' : '自动执行'}</span></div>
                {server.last_test_message && <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">{server.last_test_message}</p>}
                <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-2">
                  <OpenSwitch server={server} busy={togglingId === server.id} onToggle={() => void toggleServer(server)} />
                  {renderActions(server)}
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      {editing && <McpServerDialog server={editing === 'new' ? undefined : editing} client={communityApi} defaultEnabled={false} onClose={() => setEditing(null)} onSaved={load} />}
      {manifestTarget && <ToolManifestDialog server={servers.find(item => item.id === manifestTarget.id) || manifestTarget} onClose={() => setManifestTarget(null)} />}
      <ConfirmDialog open={!!deleteTarget} title="删除 MCP Server" message={`确认删除 MCP Server「${deleteTarget?.name || ''}」？相关连接配置和工具清单将一并移除，此操作无法撤销。`} confirmLabel={deleting ? '删除中...' : '确认删除'} onConfirm={() => void removeServer()} onCancel={() => !deleting && setDeleteTarget(null)} />
    </div>
  )
}
