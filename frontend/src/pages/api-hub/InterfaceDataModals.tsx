import { useEffect, useMemo, useState } from 'react'
import { Copy, Download, Network, Search, Upload } from 'lucide-react'
import { apiError, apiHub, type HubInterface, type McpInfo } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'

interface SharedProps {
  interfaces: HubInterface[]
  reload: () => Promise<HubInterface[]>
  onError: (message: string) => void
}

export function OpenInterfacesModal({ open, onClose, interfaces, reload, onError }: SharedProps & { open: boolean; onClose: () => void }) {
  const [search, setSearch] = useState('')
  const [info, setInfo] = useState<McpInfo | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  useEffect(() => {
    if (!open) return
    apiHub.mcpInfo().then(setInfo).catch(error => onError(apiError(error)))
  }, [onError, open])

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return keyword ? interfaces.filter(item => `${item.name} ${item.url} ${item.group_name}`.toLowerCase().includes(keyword)) : interfaces
  }, [interfaces, search])
  const endpoint = info ? `${window.location.origin}${info.endpoint}` : ''
  const mcpConfig = info ? JSON.stringify({
    mcpServers: {
      [info.server_name]: {
        type: 'streamable-http',
        url: endpoint,
        ...(info.token_required ? { headers: { Authorization: `Bearer ${info.token}` } } : {}),
      },
    },
  }, null, 2) : ''

  const toggle = async (item: HubInterface) => {
    if (!item.id) return
    setBusyId(item.id)
    try { await apiHub.setOpen(item.id, !item.open_enabled); await reload() }
    catch (error) { onError(apiError(error)) }
    finally { setBusyId(null) }
  }

  return (
    <Modal open={open} onClose={onClose} title="开放接口" description="选择允许 Agent 通过统一 MCP 发现和调用的接口。" size="3xl" footer={<Button variant="outline" onClick={onClose}>完成</Button>}>
      <div className="grid max-h-[68vh] min-h-[480px] grid-cols-[1.1fr_0.9fr] gap-5">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-[var(--color-border)]">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--color-border)] px-3">
            <label className="flex h-8 flex-1 items-center gap-2 rounded-md bg-[var(--color-bg-base)] px-2.5">
              <Search size={13} className="text-[var(--color-text-tertiary)]" />
              <input value={search} onChange={event => setSearch(event.target.value)} className="min-w-0 flex-1 bg-transparent text-xs outline-none" placeholder="搜索接口" />
            </label>
            <span className="ml-3 text-[11px] text-[var(--color-text-tertiary)]">已开放 {interfaces.filter(item => item.open_enabled).length}/{interfaces.length}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {!filtered.length ? <div className="py-20 text-center text-xs text-[var(--color-text-tertiary)]">暂无匹配接口</div> : filtered.map(item => (
              <div key={item.id} className="flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-[var(--color-bg-hover)]">
                <span className="w-12 rounded bg-[var(--color-bg-base)] py-1 text-center font-mono text-[10px] font-bold">{item.method}</span>
                <div className="min-w-0 flex-1"><div className="truncate text-xs font-medium">{item.name}</div><div className="truncate text-[10px] text-[var(--color-text-tertiary)]">{item.group_name || '默认分组'} · {item.url}</div></div>
                <button disabled={busyId === item.id} aria-label={`${item.open_enabled ? '移出' : '加入'}开放清单：${item.name}`} aria-pressed={item.open_enabled} onClick={() => toggle(item)} className={`relative h-5 w-9 rounded-full transition-colors disabled:opacity-40 ${item.open_enabled ? 'bg-[var(--color-nav-bg)]' : 'bg-[var(--color-border-hover)]'}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${item.open_enabled ? 'translate-x-[18px]' : 'translate-x-0.5'}`} /></button>
              </div>
            ))}
          </div>
        </section>
        <section className="space-y-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-4">
          <div className="flex items-start gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-nav-light)] text-[var(--color-nav-bg)]"><Network size={17} /></div><div><h4 className="text-sm font-semibold">统一调用 MCP</h4><p className="mt-0.5 text-[11px] leading-5 text-[var(--color-text-tertiary)]">工具面固定为 list_open_interfaces 与 call_open_interface，接口增减即时生效。</p></div></div>
          <div><label className="mb-1.5 block text-[11px] text-[var(--color-text-tertiary)]">Streamable HTTP 地址</label><div className="flex gap-2"><input readOnly value={endpoint} className="h-9 min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-white px-3 font-mono text-[11px]" /><Button variant="outline" size="icon" onClick={() => navigator.clipboard.writeText(endpoint)}><Copy size={14} /></Button></div></div>
          <div><div className="mb-1.5 flex items-center justify-between"><label className="text-[11px] text-[var(--color-text-tertiary)]">Agent 配置 JSON</label><button onClick={() => navigator.clipboard.writeText(mcpConfig)} className="text-[11px] text-[var(--color-nav-bg)]">复制配置</button></div><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#111827] p-3 font-mono text-[10px] leading-5 text-slate-100">{mcpConfig || '正在读取配置…'}</pre></div>
          {info && <div className={`rounded-md px-3 py-2 text-[11px] ${info.token_required ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]'}`}>{info.token_required ? '访问 Token 已启用' : '当前未设置 API_HUB_MCP_TOKEN，生产环境建议启用。'}</div>}
        </section>
      </div>
    </Modal>
  )
}

export function SystemDataModal({ open, onClose, interfaces, reload, onError }: SharedProps & { open: boolean; onClose: () => void }) {
  const [name, setName] = useState(defaultBackupName)
  const [mode, setMode] = useState<'full' | 'partial'>('full')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [message, setMessage] = useState('')

  const exportData = async () => {
    const ids = mode === 'partial' ? [...selected] : []
    if (mode === 'partial' && !ids.length) { onError('请至少选择一个接口'); return }
    try {
      const response = await apiHub.exportBackup({ name, mode, ids })
      const url = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = url; link.download = `${name || 'Backup'}.json`; link.click()
      URL.revokeObjectURL(url)
      setMessage(`已导出 ${mode === 'full' ? interfaces.length : ids.length} 个接口`)
    } catch (error) { onError(apiError(error)) }
  }
  const importData = async (file?: File) => {
    if (!file) return
    try {
      const result = await apiHub.importBackup(JSON.parse(await file.text()))
      await reload()
      setMessage(`还原完成：导入 ${result.imported} 个，跳过 ${result.skipped} 个`)
    } catch (error) { onError(error instanceof SyntaxError ? '备份文件不是有效 JSON' : apiError(error)) }
  }

  return (
    <Modal open={open} onClose={onClose} title="系统数据" description="备份或还原接口清单；调用历史与授权凭据不会写入备份。" size="3xl" footer={<Button variant="outline" onClick={onClose}>关闭</Button>}>
      {message && <div className="mb-4 rounded-md bg-[var(--color-success-bg)] px-3 py-2 text-xs text-[var(--color-success)]">{message}</div>}
      <div className="grid min-h-[430px] grid-cols-2 gap-6">
        <section className="space-y-4 rounded-lg border border-[var(--color-border)] p-4">
          <div><h4 className="text-sm font-semibold">数据备份</h4><p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">将接口配置导出为可迁移 JSON 包。</p></div>
          <input value={name} onChange={event => setName(event.target.value)} className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs outline-none" />
          <div className="grid grid-cols-2 gap-2"><ModeButton active={mode === 'full'} onClick={() => setMode('full')} title="完整备份" subtitle={`${interfaces.length} 个接口`} /><ModeButton active={mode === 'partial'} onClick={() => setMode('partial')} title="部分备份" subtitle={`${selected.size} 个已选`} /></div>
          {mode === 'partial' && <div className="max-h-52 overflow-y-auto rounded-md border border-[var(--color-border)] p-2">{interfaces.map(item => <label key={item.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-[var(--color-bg-hover)]"><input type="checkbox" checked={selected.has(item.id!)} onChange={() => setSelected(current => { const next = new Set(current); if (next.has(item.id!)) next.delete(item.id!); else next.add(item.id!); return next })} /><span className="w-10 font-mono text-[10px]">{item.method}</span><span className="truncate">{item.name}</span></label>)}</div>}
          <Button onClick={exportData}><Download size={14} />导出备份</Button>
        </section>
        <section className="space-y-4 rounded-lg border border-[var(--color-border)] p-4">
          <div><h4 className="text-sm font-semibold">数据还原</h4><p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">以名称、方法和 URL 去重，已有接口不会被覆盖。</p></div>
          <label className="flex min-h-64 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border-hover)] bg-[var(--color-bg-base)] text-center hover:border-[var(--color-nav-bg)]"><Upload size={28} className="mb-3 text-[var(--color-text-tertiary)]" /><span className="text-xs font-medium">选择 API-Hub 备份文件</span><span className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">支持 .json</span><input type="file" accept=".json,application/json" className="hidden" onChange={event => { void importData(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
        </section>
      </div>
    </Modal>
  )
}

function ModeButton({ active, onClick, title, subtitle }: { active: boolean; onClick: () => void; title: string; subtitle: string }) { return <button onClick={onClick} className={`rounded-md border p-3 text-left ${active ? 'border-[var(--color-nav-bg)] bg-[var(--color-nav-light)]' : 'border-[var(--color-border)]'}`}><div className={`text-xs font-medium ${active ? 'text-[var(--color-nav-bg)]' : ''}`}>{title}</div><div className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">{subtitle}</div></button> }
function defaultBackupName() { const now = new Date(); const pad = (value: number) => String(value).padStart(2, '0'); return `Backup-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}-${pad(now.getMinutes())}` }
