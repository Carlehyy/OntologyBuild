import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { apiError, apiHub, type CredentialStatus, type HubInterface } from '@/api/apiHub'
import InterfaceManager from './InterfaceManager'
import RunHistory from './RunHistory'
import HubOperations from './HubOperations'

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
    setLoading(true)
    const request = tab === 'interfaces'
      ? reloadInterfaces()
      : tab === 'authorization'
        ? reloadCredential()
        : Promise.resolve()
    request.catch(error => setError(apiError(error))).finally(() => setLoading(false))
  }, [reloadCredential, reloadInterfaces, tab])

  useEffect(() => {
    if (!error) return undefined
    const timer = window.setTimeout(() => setError(''), 4000)
    return () => window.clearTimeout(timer)
  }, [error])

  if (tab === 'operations') return <Navigate to="/api-hub/authorization" replace />
  if (!['interfaces', 'history', 'authorization'].includes(tab)) return <Navigate to="/api-hub/interfaces" replace />

  return (
    <div
      className="relative h-full min-h-0 bg-[var(--color-bg-base)]"
      style={{
        '--color-primary': '#059669',
        '--color-primary-hover': '#047857',
        '--color-primary-active': '#115e59',
        '--color-primary-light': '#d1fae5',
        '--color-border-active': '#059669',
      } as CSSProperties}
    >
      {error && (
        <div role="alert" aria-live="assertive" className="absolute left-1/2 top-[42%] z-50 flex w-[min(560px,calc(100%-32px))] -translate-x-1/2 -translate-y-1/2 items-center justify-between rounded-xl border border-[#f2caca] bg-white/95 px-4 py-3 text-xs text-[var(--color-danger)] shadow-[0_18px_52px_rgba(15,23,42,0.16)] backdrop-blur-xl animate-fade-in">
          <span className="leading-5">{error}</span>
          <button type="button" onClick={() => setError('')} className="ml-4 shrink-0 rounded-md px-2 py-1 font-medium transition-colors hover:bg-slate-100/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500">关闭</button>
        </div>
      )}
      {loading ? <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-tertiary)]">正在加载接口代理…</div>
        : tab === 'interfaces' ? <InterfaceManager interfaces={interfaces} reload={reloadInterfaces} onError={setError} />
          : tab === 'history' ? <RunHistory />
            : <HubOperations credential={credential} reloadCredential={reloadCredential} onError={setError} />}
    </div>
  )
}

