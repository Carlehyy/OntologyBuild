import { useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2, Copy, Download, KeyRound, Network, Pencil,
  Plus, Search, ShieldCheck, Trash2, Upload,
} from 'lucide-react'
import {
  apiError, apiHub, type HubInterface, type McpInfo, type ProxyInfo,
  type ProxyKey, type ProxyKeyPayload,
} from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { ConfirmModal, Modal } from '@/components/ui/Modal'

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
        headers: { Authorization: 'Bearer <API_HUB_MCP_TOKEN>' },
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
          {info && <div className={`rounded-md px-3 py-2 text-[11px] ${info.token_required ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]'}`}>{info.token_required ? '访问 Token 已启用；原值仅保存在服务端。' : '未设置 API_HUB_MCP_TOKEN，MCP 端点当前已禁用。'}</div>}
        </section>
      </div>
    </Modal>
  )
}

export function ProxyKeysModal({ open, onClose, interfaces, onError }: Omit<SharedProps, 'reload'> & { open: boolean; onClose: () => void }) {
  const [info, setInfo] = useState<ProxyInfo | null>(null)
  const [keys, setKeys] = useState<ProxyKey[]>([])
  const [editing, setEditing] = useState<ProxyKey | null | undefined>(undefined)
  const [revealed, setRevealed] = useState('')
  const [deleteKey, setDeleteKey] = useState<ProxyKey | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try {
      const [nextInfo, nextKeys] = await Promise.all([apiHub.proxyInfo(), apiHub.listProxyKeys()])
      setInfo(nextInfo); setKeys(nextKeys)
    } catch (error) { onError(apiError(error)) }
  }
  useEffect(() => {
    if (!open) return
    setEditing(undefined); setRevealed('')
    void load()
  }, [open])

  const saveKey = async (payload: ProxyKeyPayload) => {
    setBusy(true)
    try {
      if (editing?.id) {
        await apiHub.updateProxyKey(editing.id, payload)
        setEditing(undefined)
      } else {
        const created = await apiHub.createProxyKey(payload)
        setRevealed(created.secret || '')
        setEditing(undefined)
      }
      await load()
    } catch (error) { onError(apiError(error)) }
    finally { setBusy(false) }
  }
  const toggle = async (key: ProxyKey) => {
    setBusy(true)
    try {
      await apiHub.updateProxyKey(key.id, { ...keyPayload(key), enabled: !key.enabled })
      await load()
    } catch (error) { onError(apiError(error)) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!deleteKey) return
    setBusy(true)
    try { await apiHub.deleteProxyKey(deleteKey.id); setDeleteKey(null); await load() }
    catch (error) { onError(apiError(error)) }
    finally { setBusy(false) }
  }

  return (
    <>
      <Modal open={open} onClose={onClose} title="调用方管理" description="查看、停用或撤销平台已经生成的调用凭证；日常分享无需在这里手动创建。" size="3xl" footer={<Button variant="outline" onClick={onClose}>关闭</Button>}>
        {revealed ? <SecretView secret={revealed} info={info} onDone={() => setRevealed('')} />
          : editing !== undefined ? <ProxyKeyForm keyValue={editing} interfaces={interfaces} busy={busy} onCancel={() => setEditing(undefined)} onSave={saveKey} />
            : <div className="space-y-4"><div className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-4 py-3"><div className="flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-50 text-violet-700"><ShieldCheck size={17} /></div><div><div className="text-sm font-semibold">{keys.length} 把调用密钥</div><div className="text-[10px] text-[var(--color-text-tertiary)]">{info?.published.length || 0} 个已发布 HTTP 接口</div></div></div><Button size="sm" onClick={() => setEditing(null)}><Plus size={14} />创建密钥</Button></div>{!keys.length ? <div className="rounded-lg border border-dashed border-[var(--color-border)] py-16 text-center text-xs text-[var(--color-text-tertiary)]"><KeyRound size={26} className="mx-auto mb-3 opacity-50" />还没有调用密钥</div> : <div className="max-h-[52vh] space-y-2 overflow-y-auto">{keys.map(key => { const tone = key.status === 'active' ? 'bg-emerald-50 text-emerald-700' : key.status === 'expired' ? 'bg-red-50 text-red-700' : key.status === 'scheduled' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'; return <div key={key.id} className="rounded-lg border border-[var(--color-border)] p-4"><div className="flex items-start justify-between gap-4"><div><div className="flex items-center gap-2"><span className="text-sm font-semibold">{key.name}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${tone}`}>{statusLabel(key.status)}</span></div><div className="mt-1 font-mono text-[11px] text-[var(--color-text-tertiary)]">{key.masked_key}</div></div><div className="flex gap-1"><Button variant="ghost" size="icon-sm" title="编辑" onClick={() => setEditing(key)}><Pencil size={13} /></Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => toggle(key)}>{key.enabled ? '停用' : '启用'}</Button><Button variant="ghost" size="icon-sm" title="撤销" className="text-red-600" onClick={() => setDeleteKey(key)}><Trash2 size={13} /></Button></div></div><div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-[var(--color-text-tertiary)]"><span>{key.scope_all ? '全部已发布接口' : `${key.interface_ids.length} 个指定接口`}</span><span>{key.expires_at ? `有效期至 ${formatTime(key.expires_at)}` : '长期有效'}</span><span>{key.last_used_at ? `最后调用 ${formatTime(key.last_used_at)}` : '尚未调用'}</span></div></div> })}</div>}</div>}
      </Modal>
      <ConfirmModal open={Boolean(deleteKey)} onClose={() => setDeleteKey(null)} onConfirm={remove} loading={busy} variant="danger" title={`撤销密钥“${deleteKey?.name || ''}”？`} description="撤销后调用方将立即无法继续使用，且不能恢复。" confirmText="永久撤销" />
    </>
  )
}

export function SystemDataModal({ open, onClose, interfaces, reload, onError }: SharedProps & { open: boolean; onClose: () => void }) {
  const [name, setName] = useState(defaultBackupName)
  const [mode, setMode] = useState<'full' | 'partial'>('full')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [includeSensitive, setIncludeSensitive] = useState(false)
  const [message, setMessage] = useState('')

  const exportData = async () => {
    const ids = mode === 'partial' ? [...selected] : []
    if (mode === 'partial' && !ids.length) { onError('请至少选择一个接口'); return }
    try {
      const response = await apiHub.exportBackup({ name, mode, ids, include_sensitive: includeSensitive })
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
    <Modal open={open} onClose={onClose} title="系统数据" description="备份或还原接口清单；调用历史、W3 凭据和代理密钥不会写入备份。" size="3xl" footer={<Button variant="outline" onClick={onClose}>关闭</Button>}>
      {message && <div className="mb-4 rounded-md bg-[var(--color-success-bg)] px-3 py-2 text-xs text-[var(--color-success)]">{message}</div>}
      <div className="grid min-h-[430px] grid-cols-2 gap-6">
        <section className="space-y-4 rounded-lg border border-[var(--color-border)] p-4">
          <div><h4 className="text-sm font-semibold">数据备份</h4><p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">将接口配置导出为可迁移 JSON 包。</p></div>
          <input value={name} onChange={event => setName(event.target.value)} className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs outline-none" />
          <div className="grid grid-cols-2 gap-2"><ModeButton active={mode === 'full'} onClick={() => setMode('full')} title="完整备份" subtitle={`${interfaces.length} 个接口`} /><ModeButton active={mode === 'partial'} onClick={() => setMode('partial')} title="部分备份" subtitle={`${selected.size} 个已选`} /></div>
          {mode === 'partial' && <div className="max-h-52 overflow-y-auto rounded-md border border-[var(--color-border)] p-2">{interfaces.map(item => <label key={item.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-[var(--color-bg-hover)]"><input type="checkbox" checked={selected.has(item.id!)} onChange={() => setSelected(current => { const next = new Set(current); if (next.has(item.id!)) next.delete(item.id!); else next.add(item.id!); return next })} /><span className="w-10 font-mono text-[10px]">{item.method}</span><span className="truncate">{item.name}</span></label>)}</div>}
          <label className={`flex items-start gap-2 rounded-md border px-3 py-2 text-[11px] ${includeSensitive ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-[var(--color-border)] text-[var(--color-text-secondary)]'}`}><input type="checkbox" checked={includeSensitive} onChange={event => setIncludeSensitive(event.target.checked)} className="mt-0.5" /><span><strong>包含请求 Header 和 Body 原值</strong><br />默认关闭。开启后备份可能含 API Key、Cookie 或业务敏感数据，请只存放在受控位置。</span></label>
          <Button onClick={exportData}><Download size={14} />导出备份</Button>
        </section>
        <section className="space-y-4 rounded-lg border border-[var(--color-border)] p-4">
          <div><h4 className="text-sm font-semibold">数据还原</h4><p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">以名称、方法和 URL 去重；导入后的 MCP、开放清单和 HTTP 发布状态统一保持关闭，需管理员重新确认。</p></div>
          <label className="flex min-h-64 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border-hover)] bg-[var(--color-bg-base)] text-center hover:border-[var(--color-nav-bg)]"><Upload size={28} className="mb-3 text-[var(--color-text-tertiary)]" /><span className="text-xs font-medium">选择 API-Hub 备份文件</span><span className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">支持 .json</span><input type="file" accept=".json,application/json" className="hidden" onChange={event => { void importData(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
        </section>
      </div>
    </Modal>
  )
}

function ModeButton({ active, onClick, title, subtitle }: { active: boolean; onClick: () => void; title: string; subtitle: string }) { return <button onClick={onClick} className={`rounded-md border p-3 text-left ${active ? 'border-[var(--color-nav-bg)] bg-[var(--color-nav-light)]' : 'border-[var(--color-border)]'}`}><div className={`text-xs font-medium ${active ? 'text-[var(--color-nav-bg)]' : ''}`}>{title}</div><div className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">{subtitle}</div></button> }
function defaultBackupName() { const now = new Date(); const pad = (value: number) => String(value).padStart(2, '0'); return `Backup-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}-${pad(now.getMinutes())}` }
function statusLabel(status: ProxyKey['status']) { return { active: '有效', disabled: '已停用', scheduled: '待生效', expired: '已过期' }[status] }
function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function keyPayload(key: ProxyKey): ProxyKeyPayload { return { name: key.name, enabled: key.enabled, valid_from: key.valid_from, expires_at: key.expires_at, scope_all: key.scope_all, interface_ids: key.interface_ids } }
function toLocalInput(value?: string | null) { if (!value) return ''; const date = new Date(value); const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000); return local.toISOString().slice(0, 16) }
function toIso(value: string) { return value ? new Date(value).toISOString() : null }

function ProxyKeyForm({ keyValue, interfaces, busy, onCancel, onSave }: { keyValue: ProxyKey | null; interfaces: HubInterface[]; busy: boolean; onCancel: () => void; onSave: (payload: ProxyKeyPayload) => void }) {
  const [name, setName] = useState(keyValue?.name || '')
  const [enabled, setEnabled] = useState(keyValue?.enabled ?? true)
  const [validFrom, setValidFrom] = useState(toLocalInput(keyValue?.valid_from))
  const [expiresAt, setExpiresAt] = useState(toLocalInput(keyValue?.expires_at))
  const [scopeAll, setScopeAll] = useState(keyValue?.scope_all || false)
  const [selected, setSelected] = useState<Set<number>>(new Set(keyValue?.interface_ids || []))
  const [error, setError] = useState('')
  const submit = () => {
    if (!name.trim()) { setError('请填写密钥名称'); return }
    if (!scopeAll && !selected.size) { setError('请选择至少一个可调用接口，或授权全部接口'); return }
    onSave({ name: name.trim(), enabled, valid_from: toIso(validFrom), expires_at: toIso(expiresAt), scope_all: scopeAll, interface_ids: [...selected] })
  }
  return <div className="space-y-4"><div className="text-sm font-semibold">{keyValue ? '编辑调用密钥' : '创建调用密钥'}</div><div><label className="mb-1 block text-xs font-medium">密钥名称</label><input value={name} onChange={event => setName(event.target.value)} className="h-10 w-full rounded-md border border-[var(--color-border)] px-3 text-xs" placeholder="例如：订单系统生产环境" /></div><div className="grid grid-cols-2 gap-4"><div><label className="mb-1 block text-xs font-medium">生效时间</label><input type="datetime-local" value={validFrom} onChange={event => setValidFrom(event.target.value)} className="h-10 w-full rounded-md border border-[var(--color-border)] px-3 text-xs" /></div><div><label className="mb-1 block text-xs font-medium">过期时间</label><input type="datetime-local" value={expiresAt} onChange={event => setExpiresAt(event.target.value)} className="h-10 w-full rounded-md border border-[var(--color-border)] px-3 text-xs" /></div></div><div className="flex gap-6"><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} className="accent-teal-600" />立即启用</label><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={scopeAll} onChange={event => setScopeAll(event.target.checked)} className="accent-teal-600" />允许全部已发布接口</label></div><div><div className="mb-2 text-xs font-medium">指定接口权限</div><div className={`max-h-56 overflow-y-auto rounded-md border border-[var(--color-border)] p-2 ${scopeAll ? 'pointer-events-none opacity-40' : ''}`}>{interfaces.map(item => <label key={item.id} className="flex items-center gap-2 rounded px-2 py-2 text-xs hover:bg-[var(--color-bg-hover)]"><input type="checkbox" checked={selected.has(item.id!)} onChange={() => setSelected(current => { const next = new Set(current); if (next.has(item.id!)) next.delete(item.id!); else next.add(item.id!); return next })} /><span className="w-12 font-mono text-[10px] font-semibold">{item.method}</span><span className="min-w-0 flex-1 truncate">{item.name}</span><span className="text-[10px] text-[var(--color-text-tertiary)]">{item.http_enabled ? '已发布' : '未发布'}</span></label>)}</div></div>{error && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}<div className="flex justify-end gap-2"><Button variant="outline" onClick={onCancel}>取消</Button><Button loading={busy} onClick={submit}>{keyValue ? '保存修改' : '创建密钥'}</Button></div></div>
}

function SecretView({ secret, info, onDone }: { secret: string; info: ProxyInfo | null; onDone: () => void }) {
  return <div className="space-y-4"><div className="flex items-center gap-3 rounded-lg bg-emerald-50 p-4 text-emerald-800"><CheckCircle2 size={20} /><div><div className="text-sm font-semibold">密钥创建成功</div><div className="text-[11px]">完整密钥只显示这一次，请立即保存。</div></div></div><div className="flex gap-2"><input readOnly value={secret} className="h-10 min-w-0 flex-1 rounded-md border border-emerald-200 bg-emerald-50 px-3 font-mono text-xs" /><Button onClick={() => navigator.clipboard.writeText(secret)}><Copy size={14} />复制密钥</Button></div><pre className="overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">{`curl '${window.location.origin}${info?.path || '/proxy'}/<公开路径>' \\\n  -H '${info?.key_header || 'X-API-Hub-Key'}: ${secret}'`}</pre><div className="flex justify-end"><Button onClick={onDone}>我已保存</Button></div></div>
}
