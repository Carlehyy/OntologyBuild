import { useCallback, useEffect, useState } from 'react'
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

    Promise.all([reloadInterfaces(), reloadCredential()])
      .catch(error => setError(apiError(error)))
      .finally(() => setLoading(false))
  }, [reloadCredential, reloadInterfaces])

  if (tab === 'operations') return <Navigate to="/api-hub/authorization" replace />
  if (!['interfaces', 'history', 'authorization'].includes(tab)) return <Navigate to="/api-hub/interfaces" replace />

  return (
    <div className="relative h-full min-h-0 bg-[var(--color-bg-base)]">
      {error && <div className="absolute left-1/2 top-3 z-50 flex min-w-[360px] -translate-x-1/2 items-center justify-between rounded-md bg-[var(--color-danger-bg)] px-4 py-2.5 text-xs text-[var(--color-danger)] shadow-md"><span>{error}</span><button onClick={() => setError('')}>关闭</button></div>}
      {loading ? <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-tertiary)]">正在加载接口代理…</div>
        : tab === 'interfaces' ? <InterfaceManager interfaces={interfaces} reload={reloadInterfaces} onError={setError} />
          : tab === 'history' ? <RunHistory />
            : <HubOperations credential={credential} reloadCredential={reloadCredential} onError={setError} />}
    </div>
  )
}
