import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { Activity, Clock3, Network, RefreshCw, Route } from 'lucide-react'
import { apiError, apiHub, type CredentialStatus, type HubInterface } from '@/api/apiHub'
import InterfaceManager from './InterfaceManager'
import RunHistory from './RunHistory'
import HubOperations from './HubOperations'

const tabs = [
  { key: 'interfaces', label: '接口管理', icon: Route },
  { key: 'history', label: '调用历史', icon: Clock3 },
  { key: 'operations', label: '登录与发布', icon: Network },
]

export default function ApiHubPage() {
  const { tab = 'interfaces' } = useParams()
  const [interfaces, setInterfaces] = useState<HubInterface[]>([])
  const [credential, setCredential] = useState<CredentialStatus | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const reloadInterfaces = useCallback(async () => {
    const items = await apiHub.listInterfaces()
    setInterfaces(items)
    return items
  }, [])

  const reloadCredential = useCallback(async () => {
    const status = await apiHub.credentialStatus()
    setCredential(status)
    return status
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data hydration
    Promise.all([reloadInterfaces(), reloadCredential()])
      .catch(error => setError(apiError(error)))
      .finally(() => setLoading(false))
  }, [reloadCredential, reloadInterfaces])

  if (!tabs.some(item => item.key === tab)) return <Navigate to="/api-hub/interfaces" replace />

  const statusTone = !credential?.configured
    ? 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]'
    : credential.has_session && !credential.expired
      ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]'
      : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'
  const statusText = !credential?.configured
    ? 'W3 未配置'
    : credential.has_session && !credential.expired
      ? 'W3 登录态已就绪'
      : 'W3 登录态不可用'

  return (
    <div className="h-full min-h-0 flex flex-col bg-[var(--color-bg-base)]">
      <header className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-6 pt-5">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-nav-light)] text-[var(--color-nav-bg)]">
                <Activity size={19} />
              </div>
              <div>
                <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">接口代理</h1>
                <p className="text-xs text-[var(--color-text-tertiary)]">统一纳管、调试与开放第三方 HTTP 接口</p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium ${statusTone}`}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" />{statusText}
            </span>
            <span className="rounded-full border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)]">
              {interfaces.length} 个接口
            </span>
            <button
              onClick={() => Promise.all([reloadInterfaces(), reloadCredential()]).catch(error => setError(apiError(error)))}
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
              title="刷新"
            ><RefreshCw size={15} /></button>
          </div>
        </div>
        <nav className="mt-5 flex gap-6">
          {tabs.map(item => {
            const Icon = item.icon
            const active = item.key === tab
            return (
              <Link
                key={item.key}
                to={`/api-hub/${item.key}`}
                className={`relative flex items-center gap-2 pb-3 text-sm font-medium ${active ? 'text-[var(--color-nav-bg)]' : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'}`}
              >
                <Icon size={15} />{item.label}
                {active && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-[var(--color-nav-bg)]" />}
              </Link>
            )
          })}
        </nav>
      </header>

      {error && (
        <div className="mx-6 mt-3 flex shrink-0 items-center justify-between rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">
          <span>{error}</span><button onClick={() => setError('')}>关闭</button>
        </div>
      )}

      <div className="min-h-0 flex-1">
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-tertiary)]">正在加载接口代理…</div>
        ) : tab === 'interfaces' ? (
          <InterfaceManager interfaces={interfaces} reload={reloadInterfaces} onError={setError} />
        ) : tab === 'history' ? (
          <RunHistory />
        ) : (
          <HubOperations
            interfaces={interfaces}
            credential={credential}
            reloadInterfaces={reloadInterfaces}
            reloadCredential={reloadCredential}
            onError={setError}
          />
        )}
      </div>
    </div>
  )
}
