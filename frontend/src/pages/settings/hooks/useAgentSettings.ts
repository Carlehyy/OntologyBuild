import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { settingsApi } from '@/api/ontologies'

export type AgentInfo = { id: string; name: string; description: string }

export function useAgentSettings(activeTab: string, t: TFunction) {
  const [agentUrl, setAgentUrl] = useState('')
  const [agentAuthEnabled, setAgentAuthEnabled] = useState(false)
  const [agentUsername, setAgentUsername] = useState('')
  const [agentPassword, setAgentPassword] = useState('')
  const [agentHasSavedPassword, setAgentHasSavedPassword] = useState(false)
  const [targetAgentId, setTargetAgentId] = useState('')
  const [targetAgentName, setTargetAgentName] = useState('')
  const [agentMsg, setAgentMsg] = useState('')
  const [agentMsgOk, setAgentMsgOk] = useState(true)
  const [agentTesting, setAgentTesting] = useState(false)
  const [agentConnected, setAgentConnected] = useState(false)
  const [agentFetchedAgents, setAgentFetchedAgents] = useState<AgentInfo[]>([])
  const [agentFetching, setAgentFetching] = useState(false)
  const agentRowRefs = useRef<Record<string, HTMLLabelElement | null>>({})
  const [agentListDirty, setAgentListDirty] = useState(false)

  // ── Agent config: load saved config ──────────────────────────────────
  const { data: agentConfigData } = useQuery({
    queryKey: ['agent-config'],
    queryFn: () => settingsApi.getAgentConfig(),
    enabled: activeTab === 'agents',
  })

  useEffect(() => {
    if (!agentConfigData) return

    const cfg = agentConfigData as any
    const savedBaseUrl = cfg.base_url || ''
    setAgentUrl(savedBaseUrl)
    setAgentAuthEnabled(cfg.auth_enabled || false)
    setAgentUsername(cfg.username || '')
    setAgentPassword('')
    setAgentHasSavedPassword(cfg.has_password || false)
    setTargetAgentId(cfg.target_agent_id || '')
    setTargetAgentName(cfg.target_agent_name || '')
    setAgentConnected(Boolean(savedBaseUrl))
    setAgentListDirty(false)
    setAgentFetchedAgents([])
    setAgentMsg('')

    if (savedBaseUrl) {
      setAgentFetching(true)
      settingsApi.fetchAgents({
        base_url: savedBaseUrl,
        auth_enabled: cfg.auth_enabled || false,
        username: cfg.username || '',
        password: '',
      }).then((res: any) => {
        const agents = res.agents || []
        setAgentFetchedAgents(agents)
        if (agents.length === 0) {
          setAgentMsg(t('settings.no_agents'))
          setAgentMsgOk(false)
        }
      }).catch((e: any) => {
        setAgentMsg(e?.detail || t('settings.connection_failed_agent'))
        setAgentMsgOk(false)
      }).finally(() => {
        setAgentFetching(false)
      })
    }
  }, [agentConfigData, t])

  function markAgentConfigDirty(nextUrl = agentUrl) {
    setAgentConnected(Boolean(nextUrl.trim()))
    setAgentFetchedAgents([])
    setAgentListDirty(true)
    setAgentMsg('')
  }

  useEffect(() => {
    if (activeTab !== 'agents' || !targetAgentId || agentFetchedAgents.length === 0) return

    const selectedRow = agentRowRefs.current[targetAgentId]
    selectedRow?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeTab, targetAgentId, agentFetchedAgents])

  async function fetchAgentList(options?: {
    silent?: boolean;
    base_url?: string;
    auth_enabled?: boolean;
    username?: string;
    password?: string;
  }) {
    const baseUrl = (options?.base_url ?? agentUrl).trim()
    if (!baseUrl) {
      setAgentMsg(t('settings.agent_url_placeholder'))
      setAgentMsgOk(false)
      return
    }

    setAgentFetching(true)
    if (!options?.silent) setAgentMsg('')
    setAgentFetchedAgents([])
    try {
      const res = await settingsApi.fetchAgents({
        base_url: baseUrl,
        auth_enabled: options?.auth_enabled ?? agentAuthEnabled,
        username: options?.username ?? agentUsername,
        password: options?.password ?? agentPassword,
      }) as any
      const agents = res.agents || []
      setAgentFetchedAgents(agents)
      setAgentConnected(true)
      setAgentListDirty(false)
      if (agents.length === 0) {
        setAgentMsg(t('settings.no_agents'))
        setAgentMsgOk(false)
      }
    } catch (e: any) {
      setAgentMsg(e?.detail || t('settings.connection_failed_agent'))
      setAgentMsgOk(false)
      setAgentConnected(true)
    } finally {
      setAgentFetching(false)
    }
  }

  // ── Agent config: save connection fields without forcing a test ──────
  async function handleSaveAgentConfig() {
    if (!agentUrl.trim()) {
      setAgentMsg(t('settings.agent_url_placeholder'))
      setAgentMsgOk(false)
      return
    }

    setAgentMsg('')
    try {
      await settingsApi.updateAgentConfig({
        base_url: agentUrl.trim(),
        auth_enabled: agentAuthEnabled,
        username: agentUsername,
        password: agentPassword,
        target_agent_id: targetAgentId,
        target_agent_name: targetAgentName,
      })
      setAgentMsg(t('settings.agent_saved'))
      setAgentMsgOk(true)
      setAgentPassword('')
      setAgentHasSavedPassword(agentAuthEnabled ? (agentHasSavedPassword || Boolean(agentPassword)) : false)
      setAgentConnected(true)
      setAgentListDirty(false)
      void fetchAgentList({ silent: true })
    } catch (e: any) {
      setAgentMsg(e?.detail || '保存失败')
      setAgentMsgOk(false)
    }
  }

  // ── Agent config: test connection ───────────────────────────────────
  async function handleTestConnection() {
    if (!agentUrl.trim()) {
      setAgentMsg(t('settings.agent_url_placeholder'))
      setAgentMsgOk(false)
      return
    }
    setAgentTesting(true)
    setAgentMsg('')
    setAgentFetchedAgents([])
    try {
      const res = await settingsApi.testAgentConnection({
        base_url: agentUrl.trim(),
        auth_enabled: agentAuthEnabled,
        username: agentUsername,
        password: agentPassword,
      }) as any
      setAgentMsg(res.message)
      setAgentMsgOk(res.ok)
      if (res.ok) {
        setAgentConnected(true)
        setAgentPassword('')
        setAgentHasSavedPassword(agentAuthEnabled ? (agentHasSavedPassword || Boolean(agentPassword)) : false)
        setAgentListDirty(false)
        void fetchAgentList({ silent: true })
      }
    } catch (e: any) {
      setAgentMsg(e?.detail || t('settings.connection_failed_agent'))
      setAgentMsgOk(false)
    } finally {
      setAgentTesting(false)
    }
  }

  // ── Agent config: fetch agent list from QwenPaw ───────────────────
  async function handleFetchAgents() {
    await fetchAgentList()
  }

  // ── Agent config: auto-save agent selection ─────────────────────────
  async function handleSelectAgent(id: string, name: string) {
    setTargetAgentId(id)
    setTargetAgentName(name)
    try {
      await settingsApi.updateAgentConfig({
        base_url: agentUrl.trim(),
        auth_enabled: agentAuthEnabled,
        username: agentUsername,
        password: '',
        target_agent_id: id,
        target_agent_name: name,
      })
      setAgentMsg(t('settings.agent_saved'))
      setAgentMsgOk(true)
    } catch (e: any) {
      setAgentMsg(e?.detail || '保存失败')
      setAgentMsgOk(false)
    }
  }

  const savedAgentMissingFromList = Boolean(
    targetAgentId && agentFetchedAgents.length > 0 && !agentFetchedAgents.some(a => a.id === targetAgentId)
  )

  return {
    agentUrl,
    setAgentUrl,
    agentAuthEnabled,
    setAgentAuthEnabled,
    agentUsername,
    setAgentUsername,
    agentPassword,
    setAgentPassword,
    agentHasSavedPassword,
    targetAgentId,
    targetAgentName,
    agentMsg,
    agentMsgOk,
    agentTesting,
    agentConnected,
    agentFetchedAgents,
    agentFetching,
    agentRowRefs,
    agentListDirty,
    markAgentConfigDirty,
    handleSaveAgentConfig,
    handleTestConnection,
    handleFetchAgents,
    handleSelectAgent,
    savedAgentMissingFromList,
  }
}

export type AgentSettingsViewModel = ReturnType<typeof useAgentSettings>
