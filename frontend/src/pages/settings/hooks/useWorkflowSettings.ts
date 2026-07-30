import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { settingsApi } from '@/api/ontologies'

export function useWorkflowSettings(activeTab: string, t: TFunction) {
  const [workflowEnabled, setWorkflowEnabled] = useState(false)
  const [workflowApiUrl, setWorkflowApiUrl] = useState('')
  const [workflowApiKey, setWorkflowApiKey] = useState('')
  const [workflowHasSavedApiKey, setWorkflowHasSavedApiKey] = useState(false)
  const [workflowTimeoutSeconds, setWorkflowTimeoutSeconds] = useState(10)
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
    setWorkflowApiKey('')
    setWorkflowHasSavedApiKey(Boolean(cfg.has_api_key))
    setWorkflowTimeoutSeconds(cfg.timeout_seconds || 10)
    setWorkflowMsg('')
  }, [workflowConfigData])

  async function handleSaveWorkflowConfig() {
    await handleTestWorkflowConnection()
  }

  async function handleTestWorkflowConnection() {
    if (!workflowApiUrl.trim()) {
      setWorkflowMsg(t('settings.workflow_url_placeholder'))
      setWorkflowMsgOk(false)
      return
    }

    setWorkflowTesting(true)
    setWorkflowMsg('')
    try {
      const res = await settingsApi.testWorkflowConnection({
        enabled: workflowEnabled,
        api_url: workflowApiUrl.trim(),
        api_key: workflowApiKey,
        timeout_seconds: workflowTimeoutSeconds,
      }) as any
      setWorkflowMsg(res.message || (res.ok ? t('settings.connection_success') : t('settings.workflow_connection_failed')))
      setWorkflowMsgOk(Boolean(res.ok))
      if (res.api_base) setWorkflowApiUrl(res.api_base)
      if (res.ok && workflowApiKey) {
        setWorkflowHasSavedApiKey(true)
        setWorkflowApiKey('')
      }
    } catch (e: any) {
      setWorkflowMsg(e?.detail || t('settings.workflow_connection_failed'))
      setWorkflowMsgOk(false)
    } finally {
      setWorkflowTesting(false)
    }
  }

  return {
    workflowEnabled,
    setWorkflowEnabled,
    workflowApiUrl,
    setWorkflowApiUrl,
    workflowApiKey,
    setWorkflowApiKey,
    workflowHasSavedApiKey,
    workflowTimeoutSeconds,
    setWorkflowTimeoutSeconds,
    workflowMsg,
    workflowMsgOk,
    workflowTesting,
    handleSaveWorkflowConfig,
    handleTestWorkflowConnection,
  }
}

export type WorkflowSettingsViewModel = ReturnType<typeof useWorkflowSettings>
