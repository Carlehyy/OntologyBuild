import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Copy, KeyRound, RefreshCw, Save, ShieldCheck, UserRoundCheck } from 'lucide-react'
import {
  apiError, apiHub, type CredentialConfig, type CredentialStatus,
  type CredentialUsage, type McpInfo,
} from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'

interface Props {
  credential: CredentialStatus | null
  reloadCredential: () => Promise<CredentialStatus>
  onError: (message: string) => void
}

export default function HubOperations({ credential, reloadCredential, onError }: Props) {
  const [config, setConfig] = useState<CredentialConfig | null>(null)
  const [usage, setUsage] = useState<CredentialUsage | null>(null)
  const [systemMcp, setSystemMcp] = useState<McpInfo | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loginUrl, setLoginUrl] = useState('')
  const [cron, setCron] = useState(credential?.cron || '0 */2 * * *')
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    Promise.all([apiHub.credentialConfig(), apiHub.credentialUsage(60), apiHub.systemMcpInfo()])
      .then(([credentialConfig, credentialUsage, mcp]) => {
        setConfig(credentialConfig)
        setUsage(credentialUsage)
        setSystemMcp(mcp)
        setUsername(credentialConfig.username)
        setLoginUrl(credentialConfig.login_url)
      })
      .catch(error => onError(apiError(error)))
  }, [onError])

  const saveConfig = async () => {
    if (!username.trim()) { onError('请输入 W3 账号'); return }
    setSaving(true)
    try {
      const updated = await apiHub.updateCredentialConfig({
        username: username.trim(),
        ...(password ? { password } : {}),
        login_url: loginUrl.trim(),
      })
      setConfig(updated)
      setPassword('')
      await reloadCredential()
      setMessage('W3 授权配置已加密保存；原登录态已清理，请执行一次登录验证。')
    } catch (error) { onError(apiError(error)) }
    finally { setSaving(false) }
  }

  const refreshLogin = async () => {
    setRefreshing(true)
    try {
      const status = await apiHub.refreshCredential()
      await reloadCredential()
      setUsage(await apiHub.credentialUsage(60))
      setMessage(status.last_result === 'success' ? 'W3 登录验证成功' : status.message || 'W3 登录验证失败')
    } catch (error) { onError(apiError(error)) }
    finally { setRefreshing(false) }
  }

  const saveCron = async () => {
    setSaving(true)
    try { await apiHub.setSchedule(cron); await reloadCredential(); setMessage('授权刷新计划已保存') }
    catch (error) { onError(apiError(error)) }
    finally { setSaving(false) }
  }

  const ready = Boolean(credential?.configured && credential.has_session && !credential.expired)

  return (
    <div className="h-full min-h-0 overflow-y-auto p-4">
      <div className="mb-4 flex items-center justify-between">
        <div><h1 className="text-lg font-semibold">授权配置</h1><p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">在线维护 W3 授权、观察稳定性并配置系统管理 MCP。</p></div>
        <span className={`rounded-full px-3 py-1.5 text-xs font-medium ${ready ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]'}`}>{ready ? '授权已就绪' : credential?.configured ? '已配置，待验证' : '尚未配置'}</span>
      </div>
      {message && <div className="mb-4 flex items-center justify-between rounded-md bg-[var(--color-success-bg)] px-4 py-2.5 text-xs text-[var(--color-success)]"><span className="flex items-center gap-2"><CheckCircle2 size={14} />{message}</span><button onClick={() => setMessage('')}>关闭</button></div>}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-start justify-between"><div><CardTitle className="flex items-center gap-2"><KeyRound size={16} />W3 账号授权</CardTitle><CardDescription>凭据使用平台密钥加密保存，密码不会通过接口回传。</CardDescription></div>{config && <span className="rounded-full bg-[var(--color-bg-base)] px-2.5 py-1 text-[10px] text-[var(--color-text-secondary)]">{config.source === 'online' ? '在线配置' : '环境变量'}</span>}</CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="W3 账号"><input value={username} onChange={event => setUsername(event.target.value)} className={inputClass} placeholder="工号 / 登录账号" /></Field>
              <Field label="W3 密码"><input type="password" value={password} onChange={event => setPassword(event.target.value)} className={inputClass} placeholder={config?.password_configured ? '留空则保持现有密码' : '请输入密码'} /></Field>
            </div>
            <Field label="登录地址"><input value={loginUrl} onChange={event => setLoginUrl(event.target.value)} className={`${inputClass} font-mono`} placeholder="https://login.huawei.com/..." /></Field>
            <div className="flex items-center justify-between rounded-md bg-[var(--color-bg-base)] px-3 py-2 text-[11px] text-[var(--color-text-tertiary)]"><span>密码状态：{config?.password_configured ? '已配置' : '未配置'}</span><span>保存后会清理旧 Cookie，防止账号串用</span></div>
            <div className="flex gap-2"><Button loading={saving} onClick={saveConfig}><Save size={14} />保存授权</Button><Button variant="outline" loading={refreshing} onClick={refreshLogin}><UserRoundCheck size={14} />验证登录</Button></div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>授权稳定性</CardTitle><CardDescription>统计启用 W3 的接口调用结果；最近 60 次采用模型配置相同的热力条表达。</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-4 gap-2"><Metric label="总使用" value={usage?.total ?? 0} /><Metric label="成功" value={usage?.success ?? 0} tone="success" /><Metric label="失败" value={usage?.failed ?? 0} tone="danger" /><Metric label="成功率" value={`${usage?.success_rate ?? 0}%`} /></div>
            <div><div className="mb-2 flex items-center justify-between text-[11px]"><span className="font-medium text-[var(--color-text-secondary)]">最近 60 次授权使用</span><span className="text-[var(--color-text-tertiary)]">绿色成功 · 红色失败 · 琥珀自动重登</span></div><CredentialHeatStrip usage={usage} /></div>
            <div className="max-h-44 overflow-y-auto rounded-md border border-[var(--color-border)]">
              {!usage?.recent.length ? <div className="py-12 text-center text-xs text-[var(--color-text-tertiary)]">暂无 W3 使用记录</div> : usage.recent.slice(0, 8).map(record => <div key={record.id} className="flex items-center gap-3 border-b border-[var(--color-border)] px-3 py-2 text-[11px] last:border-0"><span className={`h-2 w-2 rounded-full ${record.ok ? 'bg-emerald-500' : 'bg-red-500'}`} /><span className="min-w-0 flex-1 truncate font-medium">{record.interface_name}</span><span className="text-[var(--color-text-tertiary)]">{record.relogin ? '自动重登 · ' : ''}{formatTime(record.created_at)}</span></div>)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><RefreshCw size={16} />刷新设置</CardTitle><CardDescription>按 Cron 周期主动更新授权，接口执行时仍保留失效重登兜底。</CardDescription></CardHeader>
          <CardContent className="space-y-4"><Field label="刷新 Cron（5 段）"><div className="flex gap-2"><input value={cron} onChange={event => setCron(event.target.value)} className={`${inputClass} font-mono`} /><Button variant="outline" loading={saving} onClick={saveCron}>保存计划</Button></div></Field><div className="grid grid-cols-2 gap-3"><SmallInfo label="最近获取" value={formatTime(credential?.acquired_at)} /><SmallInfo label="下次刷新" value={formatTime(credential?.next_run)} /></div>{credential?.message && credential.last_result === 'failed' && <div className="rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">{credential.message}</div>}</CardContent>
        </Card>

        <SystemMcpCard info={systemMcp} />
      </div>
    </div>
  )
}

function CredentialHeatStrip({ usage }: { usage: CredentialUsage | null }) {
  const cells = useMemo(() => {
    const recent = [...(usage?.recent || [])].reverse()
    const padding = Array.from({ length: Math.max(0, 60 - recent.length) }, () => null)
    return [...padding, ...recent]
  }, [usage])
  return <div className="flex gap-px">{cells.map((record, index) => <span key={record?.id ?? `empty-${index}`} title={record ? `${record.interface_name} · ${record.ok ? '成功' : '失败'} · ${formatTime(record.created_at)}` : '暂无调用'} className="h-5 min-w-0 flex-1 rounded-[2px]" style={{ background: !record ? '#e5e7eb' : record.relogin ? '#f59e0b' : record.ok ? '#22c55e' : '#ef4444' }} />)}</div>
}

function SystemMcpCard({ info }: { info: McpInfo | null }) {
  const endpoint = info ? `${window.location.origin}${info.endpoint}` : ''
  const config = info ? JSON.stringify({ mcpServers: { [info.server_name]: { type: 'streamable-http', url: endpoint, ...(info.token_required ? { headers: { Authorization: `Bearer ${info.token}` } } : {}) } } }, null, 2) : ''
  return <Card><CardHeader className="flex-row items-start justify-between"><div><CardTitle className="flex items-center gap-2"><ShieldCheck size={16} />系统管理 MCP</CardTitle><CardDescription>向受信任的 Agent 开放接口与分组管理能力。</CardDescription></div>{info && <span className={`rounded-full px-2.5 py-1 text-[10px] ${info.token_required ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]'}`}>{info.token_required ? 'Token 已启用' : '未设置 Token'}</span>}</CardHeader><CardContent className="space-y-3"><div className="flex gap-2"><input readOnly value={endpoint} className={`${inputClass} font-mono text-[11px]`} /><Button size="icon" variant="outline" onClick={() => navigator.clipboard.writeText(endpoint)}><Copy size={14} /></Button></div><div className="flex items-center justify-between text-[11px]"><span className="text-[var(--color-text-tertiary)]">Agent 配置 JSON</span><button onClick={() => navigator.clipboard.writeText(config)} className="text-[var(--color-nav-bg)]">复制配置</button></div><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#111827] p-3 font-mono text-[10px] leading-5 text-slate-100">{config || '正在读取配置…'}</pre></CardContent></Card>
}

const inputClass = 'h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs outline-none focus:border-[var(--color-nav-bg)]'
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block"><span className="mb-1.5 block text-xs font-medium">{label}</span>{children}</label> }
function Metric({ label, value, tone }: { label: string; value: string | number; tone?: 'success' | 'danger' }) { return <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"><div className="text-[10px] text-[var(--color-text-tertiary)]">{label}</div><div className={`mt-1 text-lg font-semibold ${tone === 'success' ? 'text-[var(--color-success)]' : tone === 'danger' ? 'text-[var(--color-danger)]' : ''}`}>{value}</div></div> }
function SmallInfo({ label, value }: { label: string; value: string }) { return <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"><div className="text-[10px] text-[var(--color-text-tertiary)]">{label}</div><div className="mt-1 text-xs font-medium">{value}</div></div> }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
