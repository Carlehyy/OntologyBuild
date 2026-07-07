import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, ShieldCheck, PlugZap, AlertTriangle, RefreshCw } from 'lucide-react'
import { mcpApi, type McpInterface } from '@/api/mcp'

const methodClass: Record<string, string> = {
  GET: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  POST: 'bg-blue-50 text-blue-700 border-blue-200',
  PUT: 'bg-amber-50 text-amber-700 border-amber-200',
  DELETE: 'bg-red-50 text-red-700 border-red-200',
}

function reasonOf(item: McpInterface) {
  if (item.excluded) return item.exclude_reason || '系统策略排除'
  if (!item.supported) return item.unsupported_reason || '暂不支持'
  return ''
}

export default function OpenInterfacesPage() {
  const qc = useQueryClient()
  const [keyword, setKeyword] = useState('')

  const listQuery = useQuery({ queryKey: ['mcp-interfaces'], queryFn: mcpApi.listInterfaces })
  const infoQuery = useQuery({ queryKey: ['mcp-info'], queryFn: mcpApi.info })

  const toggleMut = useMutation({
    mutationFn: ({ operationId, open }: { operationId: string; open: boolean }) => mcpApi.setOpen(operationId, open),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp-interfaces'] })
      qc.invalidateQueries({ queryKey: ['mcp-info'] })
    },
  })

  const items = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    const all = listQuery.data?.items || []
    if (!kw) return all
    return all.filter(item => [
      item.operation_id,
      item.method,
      item.path,
      item.summary,
      item.description,
      ...(item.tags || []),
    ].join(' ').toLowerCase().includes(kw))
  }, [listQuery.data, keyword])

  const endpoint = infoQuery.data?.endpoint || '/mcp'
  const mcpConfig = JSON.stringify({
    mcpServers: {
      ontoprompt: {
        command: 'npx',
        args: ['-y', 'mcp-remote', endpoint, '--header', 'Authorization: Bearer <OpenOntology user JWT>'],
      },
    },
  }, null, 2)

  return (
    <div className="p-6 h-full overflow-auto bg-[var(--color-bg-base)]">
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
              <PlugZap className="w-6 h-6 text-blue-600" /> 开放接口
            </h1>
            <p className="text-sm text-gray-500 mt-2">
              后端接口清单来自 FastAPI OpenAPI，新增接口挂载后会自动出现在这里。勾选后通过 MCP 的 list_open_interfaces / call_open_interface 对外调用。
            </p>
          </div>
          <button
            className="px-3 py-2 rounded-lg border bg-white hover:bg-gray-50 flex items-center gap-2"
            onClick={() => { qc.invalidateQueries({ queryKey: ['mcp-interfaces'] }); qc.invalidateQueries({ queryKey: ['mcp-info'] }) }}
          >
            <RefreshCw className="w-4 h-4" /> 刷新
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border p-4 lg:col-span-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-gray-800 font-medium">
                <ShieldCheck className="w-5 h-5 text-emerald-600" /> MCP 暴露策略
              </div>
              <span className="text-sm text-gray-500">已开放 {listQuery.data?.enabled_count || 0} / {listQuery.data?.total || 0}</span>
            </div>
            <ul className="mt-3 text-sm text-gray-600 space-y-1 list-disc pl-5">
              <li>MCP endpoint 要求调用方提供 OpenOntology 用户 Bearer JWT，不绕过原有鉴权。</li>
              <li>认证、用户、设置、健康检查和 MCP 管理接口默认不可开放。</li>
              <li>文件上传 / multipart 接口第一版暂不支持 MCP 调用。</li>
            </ul>
          </div>
          <div className="bg-white rounded-xl border p-4">
            <div className="text-sm font-medium text-gray-800">MCP 配置示例</div>
            <pre className="mt-3 text-xs bg-gray-950 text-gray-100 rounded-lg p-3 overflow-auto max-h-40">{mcpConfig}</pre>
          </div>
        </div>

        <div className="bg-white rounded-xl border">
          <div className="p-4 border-b flex items-center gap-3">
            <div className="relative flex-1">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className="w-full pl-9 pr-3 py-2 border rounded-lg outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="搜索接口名称、路径、方法或标签"
                value={keyword}
                onChange={e => setKeyword(e.target.value)}
              />
            </div>
          </div>

          {listQuery.isLoading ? (
            <div className="p-8 text-center text-gray-500">加载接口清单中…</div>
          ) : listQuery.error ? (
            <div className="p-8 text-center text-red-600">加载失败，请确认当前用户是否为 admin。</div>
          ) : (
            <div className="divide-y">
              {items.map(item => {
                const disabled = item.excluded || !item.supported || toggleMut.isPending
                const reason = reasonOf(item)
                return (
                  <div key={item.operation_id} className="p-4 hover:bg-gray-50 transition-colors">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className={`text-xs border rounded px-2 py-0.5 font-semibold ${methodClass[item.method] || 'bg-gray-50 text-gray-700 border-gray-200'}`}>{item.method}</span>
                          <span className="font-medium text-gray-900 truncate">{item.summary || item.operation_id}</span>
                          {item.enabled && <span className="text-xs rounded-full bg-blue-50 text-blue-700 px-2 py-0.5">已开放</span>}
                          {reason && <span className="text-xs rounded-full bg-gray-100 text-gray-600 px-2 py-0.5 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{reason}</span>}
                        </div>
                        <div className="mt-2 font-mono text-sm text-gray-600 break-all">{item.path}</div>
                        <div className="mt-1 text-xs text-gray-400 break-all">operation_id: {item.operation_id}</div>
                        {!!item.tags?.length && <div className="mt-2 flex gap-1 flex-wrap">{item.tags.map(tag => <span key={tag} className="text-xs px-2 py-0.5 bg-gray-100 rounded">{tag}</span>)}</div>}
                      </div>
                      <button
                        className={`px-3 py-2 rounded-lg text-sm font-medium ${item.enabled ? 'bg-red-50 text-red-700 hover:bg-red-100' : 'bg-blue-600 text-white hover:bg-blue-700'} disabled:opacity-50 disabled:cursor-not-allowed`}
                        disabled={disabled}
                        onClick={() => toggleMut.mutate({ operationId: item.operation_id, open: !item.enabled })}
                      >
                        {item.enabled ? '取消开放' : '开放'}
                      </button>
                    </div>
                  </div>
                )
              })}
              {!items.length && <div className="p-8 text-center text-gray-500">没有匹配的接口</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
