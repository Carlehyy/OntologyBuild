import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { settingsApi } from '@/api/ontologies'

export function useWorkflowSettings(activeTab: string, t: TFunction) {
  const [workflowEnabled, setWorkflowEnabled] = useState(false)
  const [workflowApiUrl, setWorkflowApiUrl] = useState('')
  const [workflowHasSavedApiKey, setWorkflowHasSavedApiKey] = useState(false)
  const [workflowTimeoutSeconds, setWorkflowTimeoutSeconds] = useState(30)
  const [workflowMsg, setWorkflowMsg] = useState('')
  const [workflowMsgOk, setWorkflowMsgOk] = useState(true)
  const [workflowTesting, setWorkflowTesting] = useState(false)

  // -- Workflow/n8n config: load saved config ---------------------------
  const { data: workflowConfigData } = useQuery({
    queryKey: ['workflow-config'],
    queryFn: () => settingsApi.getWorkflowConfig(),
    enabled: activeTab === 'workflows',
  })

  useEffect(() => {
    const cfg = workflowConfigData as any
    if (!cfg) return
    setWorkflowEnabled(Boolean(cfg.enabled))
    setWorkflowApiUrl(cfg.api_url || '')
    setWorkflowHasSavedApiKey(Boolean(cfg.has_api_key))
    setWorkflowTimeoutSeconds(cfg.timeout_seconds || 30)
    setWorkflowMsg('')
  }, [workflowConfigData])

  async function handleTestWorkflowConnection() {
    setWorkflowTesting(true)
    setWorkflowMsg('')
    try {
      const res = await settingsApi.testWorkflowConnection({
        enabled: workflowEnabled,
        api_url: workflowApiUrl.trim(),
        api_key: '',
        timeout_seconds: workflowTimeoutSeconds,
      }) as any
      setWorkflowMsg(res.message || (res.ok ? t('settings.connection_success') : t('settings.workflow_connection_failed')))
      setWorkflowMsgOk(Boolean(res.ok))
      if (res.api_base) setWorkflowApiUrl(res.api_base)
    } catch (e: any) {
      setWorkflowMsg(e?.detail || t('settings.workflow_connection_failed'))
      setWorkflowMsgOk(false)
    } finally {
      setWorkflowTesting(false)
    }
  }

  return {
    workflowEnabled,
    workflowApiUrl,
    workflowHasSavedApiKey,
    workflowTimeoutSeconds,
    workflowMsg,
    workflowMsgOk,
    workflowTesting,
    handleTestWorkflowConnection,
  }
}

export type WorkflowSettingsViewModel = ReturnType<typeof useWorkflowSettings>
