import { useCallback, useEffect, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { apiError, apiHub, type HubInterface } from '@/api/apiHub'
import InterfaceManager from './InterfaceManager'
import RunHistory from './RunHistory'

export default function ApiHubPage() {
  const { tab = 'interfaces' } = useParams()
  const [interfaces, setInterfaces] = useState<HubInterface[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const reloadInterfaces = useCallback(async () => {
    const items = await apiHub.listInterfaces()
    setInterfaces(items)
    return items
  }, [])

  useEffect(() => {
    setLoading(true)
    const request = tab === 'interfaces'
      ? reloadInterfaces()
      : Promise.resolve()
    request.catch(error => setError(apiError(error))).finally(() => setLoading(false))
  }, [reloadInterfaces, tab])

  useEffect(() => {
    if (!error) return undefined
    const timer = window.setTimeout(() => setError(''), 4000)
    return () => window.clearTimeout(timer)
  }, [error])

  if (tab === 'operations' || tab === 'authorization') return <Navigate to="/api-hub/interfaces" replace />
  if (!['interfaces', 'history'].includes(tab)) return <Navigate to="/api-hub/interfaces" replace />

  return (
    <div className="relative h-full min-h-0 bg-[var(--color-bg-base)]">
      {error && (
        <div role="alert" aria-live="assertive" className="absolute left-1/2 top-[42%] z-50 flex w-[min(560px,calc(100%-32px))] -translate-x-1/2 -translate-y-1/2 items-center justify-between rounded-xl border border-[color-mix(in_srgb,var(--color-danger)_30%,transparent)] bg-popover px-4 py-3 text-xs text-[var(--color-danger)] shadow-[var(--shadow-lg)] backdrop-blur-xl animate-fade-in">
          <span className="leading-5">{error}</span>
          <button type="button" onClick={() => setError('')} className="ml-4 shrink-0 rounded-md px-2 py-1 font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">关闭</button>
        </div>
      )}
      {loading ? <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-tertiary)]">正在加载接口代理…</div>
        : tab === 'interfaces' ? <InterfaceManager interfaces={interfaces} reload={reloadInterfaces} onError={setError} />
          : <RunHistory />}
    </div>
  )
}
