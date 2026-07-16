import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, Copy, KeyRound, RefreshCw, Save, ShieldCheck, UserRoundCheck } from 'lucide-react'
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
  const [savingConfig, setSavingConfig] = useState(false)
  const [savingCron, setSavingCron] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<{ text: string; kind: 'success' | 'error' } | null>(null)

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

  const persistConfig = async () => {
    if (!username.trim()) { onError('请输入 W3 账号'); return }
    const updated = await apiHub.updateCredentialConfig({
      username: username.trim(),
      ...(password ? { password } : {}),
      login_url: loginUrl.trim(),
    })
    setConfig(updated)
    setPassword('')
    await reloadCredential()
    return updated
  }

  const saveConfig = async () => {
    setSavingConfig(true)
    try {
      const updated = await persistConfig()
      if (updated) setMessage({ text: 'W3 授权配置已加密保存；原登录态已清理，请执行一次登录验证。', kind: 'success' })
    } catch (error) { onError(apiError(error)) }
    finally { setSavingConfig(false) }
  }

  const refreshLogin = async () => {
    setRefreshing(true)
    try {
      const updated = await persistConfig()
      if (!updated) return
      const status = await apiHub.refreshCredential()
      await reloadCredential()
      setUsage(await apiHub.credentialUsage(60))
      setMessage({
        text: status.last_result === 'success' ? 'W3 登录验证成功' : status.message || 'W3 登录验证失败',
        kind: status.last_result === 'success' ? 'success' : 'error',
      })
    } catch (error) { onError(apiError(error)) }
    finally { setRefreshing(false) }
  }

  const saveCron = async () => {
    setSavingCron(true)
    try { await apiHub.setSchedule(cron); await reloadCredential(); setMessage({ text: '授权刷新计划已保存', kind: 'success' }) }
    catch (error) { onError(apiError(error)) }
    finally { setSavingCron(false) }
  }

  const ready = Boolean(credential?.configured && credential.has_session && !credential.expired)

  return (
    <div className="relative min-h-full bg-[var(--color-bg-base)] p-4 xl:h-full xl:min-h-0 xl:overflow-hidden">
      {message && (
        <div className={`absolute left-1/2 top-3 z-20 flex w-[min(680px,calc(100%-32px))] -translate-x-1/2 items-center justify-between rounded-xl border px-4 py-2.5 text-xs shadow-lg ${message.kind === 'success' ? 'border-[#cde8d5] bg-[#e8f5e9] text-[#2d8a4e]' : 'border-[#f2caca] bg-[#fde8e8] text-[#c23b3b]'}`}>
          <span className="flex min-w-0 items-center gap-2">
            {message.kind === 'success' ? <CheckCircle2 size={14} className="shrink-0" /> : <AlertCircle size={14} className="shrink-0" />}
            <span className="truncate">{message.text}</span>
          </span>
          <button onClick={() => setMessage(null)} className="ml-4 shrink-0 font-medium transition-opacity hover:opacity-70">关闭</button>
        </div>
      )}

      <div className="grid min-h-full grid-cols-1 gap-4 xl:h-full xl:min-h-0 xl:grid-cols-12 xl:grid-rows-[minmax(0,1.08fr)_minmax(0,0.92fr)]">
        <Card className={`${cardClass} xl:col-span-7 ${ready ? 'border-teal-600 ring-1 ring-teal-100' : ''}`}>
          <CardHeader className="flex-row items-start justify-between gap-4 p-4 pb-3">
            <CardHeading icon={<KeyRound size={17} />} title="W3 账号授权" description="凭据由平台密钥加密保存，密码不会通过接口回传。" />
            {config && <StatusBadge tone={config.source === 'online' ? 'accent' : 'neutral'}>{config.source === 'online' ? '在线配置' : '环境变量'}</StatusBadge>}
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-4 pt-0">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="W3 账号"><input value={username} onChange={event => setUsername(event.target.value)} className={inputClass} placeholder="工号 / 登录账号" /></Field>
              <Field label="W3 密码"><input type="password" value={password} onChange={event => setPassword(event.target.value)} className={inputClass} placeholder={config?.password_configured ? '留空则保持现有密码' : '请输入密码'} /></Field>
            </div>
            <Field label="登录地址"><input value={loginUrl} onChange={event => setLoginUrl(event.target.value)} className={`${inputClass} font-mono`} placeholder="https://login.huawei.com/..." /></Field>
            <div className="grid grid-cols-1 gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[11px] text-slate-500 sm:grid-cols-2">
              <span>密码状态：<b className={config?.password_configured ? 'font-semibold text-[#2d8a4e]' : 'font-semibold text-slate-500'}>{config?.password_configured ? '已配置' : '未配置'}</b></span>
              <span className="sm:text-right">保存后清理旧 Cookie，避免账号串用</span>
            </div>
            <div className="mt-auto flex flex-wrap gap-2">
              <Button loading={savingConfig} onClick={saveConfig} className={primaryButtonClass}><Save size={14} />保存授权</Button>
              <Button variant="outline" loading={refreshing} onClick={refreshLogin} className={outlineButtonClass}><UserRoundCheck size={14} />保存并验证</Button>
            </div>
          </CardContent>
        </Card>

        <Card className={`${cardClass} xl:col-span-5`}>
          <CardHeader className="p-4 pb-3">
            <CardHeading icon={<CheckCircle2 size={17} />} title="授权稳定性" description="最近 60 次 W3 调用表现，与模型配置页采用相同状态配色。" />
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-4 pt-0">
            <div className="grid grid-cols-4 gap-2">
              <Metric label="总使用" value={usage?.total ?? 0} />
              <Metric label="成功" value={usage?.success ?? 0} tone="success" />
              <Metric label="失败" value={usage?.failed ?? 0} tone="danger" />
              <Metric label="成功率" value={`${usage?.success_rate ?? 0}%`} />
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between gap-3 text-[10px]">
                <span className="font-semibold text-slate-500">最近 60 次授权使用</span>
                <span className="text-right text-slate-400">成功 · 失败 · 自动重登</span>
              </div>
              <CredentialHeatStrip usage={usage} />
            </div>
            <div className="min-h-0 flex-1">
              {!usage?.recent.length ? (
                <div className="grid h-full min-h-24 place-items-center rounded-xl border border-dashed border-slate-200 bg-slate-50/70 text-xs text-slate-400">暂无 W3 使用记录</div>
              ) : (
                <div className="grid h-full min-h-0 grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200">
                  {usage.recent.slice(0, 8).map(record => (
                    <div key={record.id} title={`${record.interface_name} · ${formatTime(record.created_at)}`} className="flex min-w-0 items-center gap-2 bg-white px-2.5 py-1.5 text-[10px]">
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: credentialHeatColor(record) }} />
                      <span className="min-w-0 flex-1 truncate font-medium text-slate-600">{record.interface_name}</span>
                      <span className="shrink-0 tabular-nums text-slate-400">{formatCompactTime(record.created_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className={`${cardClass} xl:col-span-5`}>
          <CardHeader className="p-4 pb-3">
            <CardHeading icon={<RefreshCw size={17} />} title="刷新设置" description="按 Cron 主动更新授权，并保留调用时失效重登兜底。" />
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-4 pt-0">
            <Field label="刷新 Cron（5 段）">
              <div className="flex gap-2">
                <input value={cron} onChange={event => setCron(event.target.value)} className={`${inputClass} font-mono`} />
                <Button variant="outline" loading={savingCron} onClick={saveCron} className={`${outlineButtonClass} shrink-0`}>保存计划</Button>
              </div>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <SmallInfo label="最近获取" value={formatTime(credential?.acquired_at)} />
              <SmallInfo label="下次刷新" value={formatTime(credential?.next_run)} />
            </div>
            <div className="mt-auto flex items-center justify-between rounded-xl bg-[#e8f5e9] px-3 py-2.5 text-[11px] text-[#2d8a4e]">
              <span className="flex items-center gap-2"><RefreshCw size={13} />失效时自动重新登录</span>
              <StatusBadge tone={ready ? 'success' : 'neutral'}>{ready ? '当前可用' : '等待授权'}</StatusBadge>
            </div>
            {credential?.message && credential.last_result === 'failed' && <div className="rounded-xl bg-[#fde8e8] px-3 py-2 text-xs text-[#c23b3b]">{credential.message}</div>}
          </CardContent>
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
  return <div className="flex gap-px">{cells.map((record, index) => { const label = record ? `${record.interface_name} · ${record.ok ? '成功' : '失败'} · ${formatTime(record.created_at)}` : '暂无调用'; return <span key={record?.id ?? `empty-${index}`} role="img" tabIndex={0} aria-label={label} title={label} className="h-4 min-w-0 flex-1 rounded-[2px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500" style={{ background: record ? credentialHeatColor(record) : '#eceef1' }} /> })}</div>
}

function SystemMcpCard({ info }: { info: McpInfo | null }) {
  const endpoint = info ? `${window.location.origin}${info.endpoint}` : ''
  const config = info ? JSON.stringify({ mcpServers: { [info.server_name]: { type: 'streamable-http', url: endpoint, headers: { Authorization: 'Bearer <API_HUB_SYSTEM_MCP_TOKEN>' } } } }) : ''
  return (
    <Card className={`${cardClass} xl:col-span-7`}>
      <CardHeader className="flex-row items-start justify-between gap-4 p-4 pb-3">
        <CardHeading icon={<ShieldCheck size={17} />} title="系统管理 MCP" description="向受信任的 Agent 开放管理能力；Token 只在服务端配置。" />
        {info && <StatusBadge tone={info.token_required ? 'success' : 'warning'}>{info.token_required ? 'Token 已启用' : '未配置，端点已禁用'}</StatusBadge>}
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-4 pt-0">
        <Field label="服务地址">
          <div className="flex gap-2">
            <input readOnly value={endpoint} className={`${inputClass} font-mono text-[11px]`} />
            <Button size="icon" variant="outline" onClick={() => navigator.clipboard.writeText(endpoint)} className={`${outlineButtonClass} shrink-0`} aria-label="复制服务地址"><Copy size={14} /></Button>
          </div>
        </Field>
        <div className="flex items-center justify-between text-[11px]">
          <span className="font-medium text-slate-500">Agent 配置 JSON（请替换占位符）</span>
          <button onClick={() => navigator.clipboard.writeText(config)} className="font-medium text-teal-700 transition-colors hover:text-teal-900">复制配置</button>
        </div>
        <pre className="min-h-24 flex-1 whitespace-pre-wrap break-all rounded-xl bg-slate-900 p-3 font-mono text-[10px] leading-4 text-slate-100 shadow-inner">{config || '正在读取配置…'}</pre>
      </CardContent>
    </Card>
  )
}

const cardClass = 'flex min-h-0 flex-col overflow-hidden rounded-2xl border-slate-200 bg-white shadow-sm transition-all duration-200 hover:shadow-lg'
const inputClass = 'h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-700 outline-none transition-all placeholder:text-slate-400 focus:border-teal-400 focus:ring-2 focus:ring-teal-500/15'
const primaryButtonClass = 'rounded-lg bg-[var(--color-nav-bg)] text-white shadow-sm hover:bg-teal-700 active:bg-teal-800'
const outlineButtonClass = 'rounded-lg border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-800'

function CardHeading({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-teal-100 bg-teal-50 text-teal-600">{icon}</span>
      <div className="min-w-0">
        <CardTitle className="text-[14px] text-slate-800">{title}</CardTitle>
        <CardDescription className="mt-1 text-[11px] leading-4 text-slate-400">{description}</CardDescription>
      </div>
    </div>
  )
}

function StatusBadge({ tone, children }: { tone: 'success' | 'warning' | 'neutral' | 'accent'; children: React.ReactNode }) {
  const style = tone === 'success'
    ? 'bg-[#e8f5e9] text-[#2d8a4e]'
    : tone === 'warning'
      ? 'bg-[#fff8e1] text-[#c9861a]'
      : tone === 'accent'
        ? 'border border-teal-200 bg-teal-50 text-teal-700'
        : 'bg-slate-100 text-slate-500'
  return <span className={`inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-[10px] font-semibold ${style}`}>{children}</span>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-slate-600">{label}</span>{children}</label> }
function Metric({ label, value, tone }: { label: string; value: string | number; tone?: 'success' | 'danger' }) { return <div className="rounded-lg bg-slate-50 px-2.5 py-2"><div className="text-[10px] font-medium text-slate-400">{label}</div><div className={`mt-0.5 text-[17px] font-bold tabular-nums ${tone === 'success' ? 'text-[#2d8a4e]' : tone === 'danger' ? 'text-[#c23b3b]' : 'text-slate-800'}`}>{value}</div></div> }
function SmallInfo({ label, value }: { label: string; value: string }) { return <div className="rounded-xl bg-slate-50 px-3 py-2.5"><div className="text-[10px] font-medium text-slate-400">{label}</div><div className="mt-1 truncate text-xs font-semibold tabular-nums text-slate-700" title={value}>{value}</div></div> }
function credentialHeatColor(record: CredentialUsage['recent'][number]) { return record.relogin ? '#f0a020' : record.ok ? '#40c463' : '#e5484d' }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function formatCompactTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—' }
