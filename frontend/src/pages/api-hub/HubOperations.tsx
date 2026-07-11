import { useEffect, useMemo, useState } from 'react'
import { Archive, CheckCircle2, Copy, Download, KeyRound, Network, RefreshCw, ShieldCheck, Upload } from 'lucide-react'
import { apiError, apiHub, type CredentialStatus, type HubInterface, type McpInfo } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'

interface Props {
  interfaces: HubInterface[]
  credential: CredentialStatus | null
  reloadInterfaces: () => Promise<HubInterface[]>
  reloadCredential: () => Promise<CredentialStatus>
  onError: (message: string) => void
}

export default function HubOperations({ interfaces, credential, reloadInterfaces, reloadCredential, onError }: Props) {
  const [cron, setCron] = useState(credential?.cron || '0 */2 * * *')
  const [refreshing, setRefreshing] = useState(false)
  const [savingCron, setSavingCron] = useState(false)
  const [publicMcp, setPublicMcp] = useState<McpInfo | null>(null)
  const [systemMcp, setSystemMcp] = useState<McpInfo | null>(null)
  const [backupName, setBackupName] = useState(defaultBackupName)
  const [backupMode, setBackupMode] = useState<'full' | 'partial'>('full')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [message, setMessage] = useState('')

  useEffect(() => {
    Promise.all([apiHub.mcpInfo(), apiHub.systemMcpInfo()])
      .then(([publicInfo, systemInfo]) => { setPublicMcp(publicInfo); setSystemMcp(systemInfo) })
      .catch(error => onError(apiError(error)))
  }, [onError])

  const refreshLogin = async () => {
    setRefreshing(true)
    try {
      const status = await apiHub.refreshCredential()
      await reloadCredential()
      setMessage(status.last_result === 'success' ? 'W3 登录态刷新成功' : status.message || '登录失败')
    } catch (error) { onError(apiError(error)) }
    finally { setRefreshing(false) }
  }
  const saveCron = async () => {
    setSavingCron(true)
    try { await apiHub.setSchedule(cron); await reloadCredential(); setMessage('刷新计划已保存') }
    catch (error) { onError(apiError(error)) }
    finally { setSavingCron(false) }
  }
  const setOpen = async (item: HubInterface) => {
    try { await apiHub.setOpen(item.id!, !item.open_enabled); await reloadInterfaces() }
    catch (error) { onError(apiError(error)) }
  }

  const exportBackup = async () => {
    const ids = backupMode === 'partial' ? [...selected] : []
    if (backupMode === 'partial' && !ids.length) { onError('请至少选择一个接口'); return }
    try {
      const response = await apiHub.exportBackup({ name: backupName, mode: backupMode, ids })
      const url = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = url
      link.download = `${backupName || 'Backup'}.json`
      link.click()
      URL.revokeObjectURL(url)
      setMessage(`已导出${backupMode === 'full' ? '全部' : ids.length}个接口`)
    } catch (error) { onError(apiError(error)) }
  }

  const importBackup = async (file?: File) => {
    if (!file) return
    try {
      const payload = JSON.parse(await file.text())
      const result = await apiHub.importBackup(payload)
      await reloadInterfaces()
      setMessage(`还原完成：导入 ${result.imported} 个，跳过 ${result.skipped} 个`)
    } catch (error) { onError(error instanceof SyntaxError ? '备份文件不是有效的 JSON' : apiError(error)) }
  }

  const configured = Boolean(credential?.configured)
  const ready = Boolean(credential?.has_session && !credential?.expired)

  return (
    <div className="h-full min-h-0 overflow-y-auto p-4">
      {message && <div className="mb-4 flex items-center justify-between rounded-md bg-[var(--color-success-bg)] px-4 py-2.5 text-xs text-[var(--color-success)]"><span className="flex items-center gap-2"><CheckCircle2 size={14} />{message}</span><button onClick={() => setMessage('')}>关闭</button></div>}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-start justify-between">
            <div><CardTitle className="flex items-center gap-2"><KeyRound size={16} />W3 登录态</CardTitle><CardDescription>统一维护 Cookie，并在失效时透明重登。</CardDescription></div>
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${ready ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : configured ? 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]' : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'}`}>{ready ? '已就绪' : configured ? '待登录' : '未配置'}</span>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <Info label="最近获取" value={formatTime(credential?.acquired_at)} />
              <Info label="下次刷新" value={formatTime(credential?.next_run)} />
            </div>
            {!configured && <div className="rounded-md bg-[var(--color-warning-bg)] px-3 py-2 text-xs leading-5 text-[var(--color-warning)]">在后端 .env 中配置 W3_USERNAME 与 W3_PASSWORD 后即可启用；不需要 W3 的接口不受影响。</div>}
            {credential?.message && credential.last_result === 'failed' && <div className="rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">{credential.message}</div>}
            <div className="flex gap-2"><Button loading={refreshing} onClick={refreshLogin}><RefreshCw size={14} />立即刷新登录</Button></div>
            <div className="border-t border-[var(--color-border)] pt-4">
              <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-primary)]">定时刷新 Cron（5 段）</label>
              <div className="flex gap-2"><input value={cron} onChange={event => setCron(event.target.value)} className="h-9 min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-xs outline-none" /><Button variant="outline" loading={savingCron} onClick={saveCron}>保存计划</Button></div>
              <p className="mt-1.5 text-[11px] text-[var(--color-text-tertiary)]">例如：0 */2 * * * 表示每 2 小时刷新一次。</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Network size={16} />开放接口清单</CardTitle><CardDescription>被开放的接口可通过统一 MCP 的两个稳定工具发现和调用。</CardDescription></CardHeader>
          <CardContent>
            <div className="max-h-[330px] overflow-y-auto rounded-md border border-[var(--color-border)]">
              {!interfaces.length ? <div className="py-16 text-center text-xs text-[var(--color-text-tertiary)]">暂无接口</div> : interfaces.map(item => (
                <div key={item.id} className="flex items-center gap-3 border-b border-[var(--color-border)] px-3 py-2.5 last:border-0">
                  <span className="w-14 rounded bg-[var(--color-bg-base)] px-2 py-1 text-center font-mono text-[10px] font-semibold">{item.method}</span>
                  <div className="min-w-0 flex-1"><div className="truncate text-xs font-medium text-[var(--color-text-primary)]">{item.name}</div><div className="truncate text-[10px] text-[var(--color-text-tertiary)]">{item.group_name || '默认分组'} · {item.url}</div></div>
                  <button aria-label={`${item.open_enabled ? '移出' : '加入'}开放清单：${item.name}`} aria-pressed={item.open_enabled} onClick={() => setOpen(item)} className={`relative h-5 w-9 rounded-full transition-colors ${item.open_enabled ? 'bg-[var(--color-nav-bg)]' : 'bg-[var(--color-border-hover)]'}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${item.open_enabled ? 'translate-x-[18px]' : 'translate-x-0.5'}`} /></button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <McpCard title="统一调用 MCP" description="供 Agent 发现并调用已加入开放清单的接口。" info={publicMcp} />
        <McpCard title="系统管理 MCP" description="供受信任的 Agent 管理接口、分组并执行调用。" info={systemMcp} />

        <Card className="xl:col-span-2">
          <CardHeader><CardTitle className="flex items-center gap-2"><Archive size={16} />备份与还原</CardTitle><CardDescription>只迁移接口清单；调用历史、设置和 W3 登录态不会进入备份。</CardDescription></CardHeader>
          <CardContent className="grid gap-6 lg:grid-cols-2">
            <div className="space-y-3">
              <label className="block text-xs font-medium">导出接口清单</label>
              <input value={backupName} onChange={event => setBackupName(event.target.value)} className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs outline-none" />
              <div className="flex gap-2"><ModeButton active={backupMode === 'full'} onClick={() => setBackupMode('full')} title="完整备份" subtitle={`${interfaces.length} 个接口`} /><ModeButton active={backupMode === 'partial'} onClick={() => setBackupMode('partial')} title="部分备份" subtitle={`${selected.size} 个已选`} /></div>
              {backupMode === 'partial' && <div className="max-h-44 overflow-y-auto rounded-md border border-[var(--color-border)] p-2">{interfaces.map(item => <label key={item.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-[var(--color-bg-hover)]"><input type="checkbox" checked={selected.has(item.id!)} onChange={() => setSelected(current => { const next = new Set(current); if (next.has(item.id!)) next.delete(item.id!); else next.add(item.id!); return next })} /><span className="font-mono text-[10px]">{item.method}</span><span className="truncate">{item.name}</span></label>)}</div>}
              <Button onClick={exportBackup}><Download size={14} />导出 JSON</Button>
            </div>
            <div className="space-y-3">
              <label className="block text-xs font-medium">还原接口清单</label>
              <label className="flex min-h-40 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border-hover)] bg-[var(--color-bg-base)] text-center hover:border-[var(--color-nav-bg)]">
                <Upload size={24} className="mb-2 text-[var(--color-text-tertiary)]" />
                <span className="text-xs font-medium text-[var(--color-text-primary)]">选择 API-Hub 备份文件</span>
                <span className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">重复的“名称 + 方法 + URL”会自动跳过</span>
                <input type="file" accept=".json,application/json" className="hidden" onChange={event => { void importBackup(event.target.files?.[0]); event.currentTarget.value = '' }} />
              </label>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function McpCard({ title, description, info }: { title: string; description: string; info: McpInfo | null }) {
  const endpoint = info ? `${window.location.origin}${info.endpoint}` : ''
  const config = useMemo(() => info ? JSON.stringify({ mcpServers: { [info.server_name]: { type: 'streamable-http', url: endpoint, ...(info.token_required ? { headers: { Authorization: `Bearer ${info.token}` } } : {}) } } }, null, 2) : '', [endpoint, info])
  return <Card><CardHeader className="flex-row items-start justify-between"><div><CardTitle className="flex items-center gap-2"><ShieldCheck size={16} />{title}</CardTitle><CardDescription>{description}</CardDescription></div>{info && <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${info.token_required ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]'}`}>{info.token_required ? 'Token 已启用' : '未设置 Token'}</span>}</CardHeader><CardContent>{!info ? <div className="py-10 text-center text-xs text-[var(--color-text-tertiary)]">正在读取 MCP 配置…</div> : <div className="space-y-3"><div><label className="mb-1 block text-[11px] text-[var(--color-text-tertiary)]">Streamable HTTP 地址</label><div className="flex gap-2"><input readOnly value={endpoint} className="h-9 min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-[11px]" /><Button size="icon" variant="outline" onClick={() => navigator.clipboard.writeText(endpoint)}><Copy size={14} /></Button></div></div><div><div className="mb-1 flex items-center justify-between"><label className="text-[11px] text-[var(--color-text-tertiary)]">Agent 配置</label><button onClick={() => navigator.clipboard.writeText(config)} className="text-[11px] text-[var(--color-nav-bg)]">复制 JSON</button></div><pre className="max-h-36 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#111827] p-3 font-mono text-[10px] leading-4 text-slate-100">{config}</pre></div>{!info.token_required && <p className="text-[11px] text-[var(--color-warning)]">生产环境建议配置对应的 API_HUB_*_MCP_TOKEN。</p>}</div>}</CardContent></Card>
}

function Info({ label, value }: { label: string; value: string }) { return <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"><div className="text-[10px] text-[var(--color-text-tertiary)]">{label}</div><div className="mt-1 font-medium text-[var(--color-text-primary)]">{value}</div></div> }
function ModeButton({ active, onClick, title, subtitle }: { active: boolean; onClick: () => void; title: string; subtitle: string }) { return <button onClick={onClick} className={`flex-1 rounded-md border p-3 text-left ${active ? 'border-[var(--color-nav-bg)] bg-[var(--color-nav-light)]' : 'border-[var(--color-border)]'}`}><div className={`text-xs font-medium ${active ? 'text-[var(--color-nav-bg)]' : ''}`}>{title}</div><div className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">{subtitle}</div></button> }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function defaultBackupName() { const now = new Date(); const pad = (value: number) => String(value).padStart(2, '0'); return `Backup-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}-${pad(now.getMinutes())}` }
