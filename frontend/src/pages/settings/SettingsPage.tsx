import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useLocation, useParams } from 'react-router-dom'
import { settingsApi, promptApi, domainApi } from '@/api/ontologies'
import { Trash2, Plus, Pencil, X, Check, Sparkles, Search, Loader2, Target, Bot, Wifi, RefreshCw, Workflow } from 'lucide-react'
import {
  EXTRACTION_RULES,
  VALIDATION_RULES,
  loadRuleStates,
  saveRuleStates,
  loadValidationStates,
  saveValidationStates,
  type ExtractionRuleState,
} from '@/utils/extractionRules'
import UserManagementPanel from './UserManagementPanel'

type ActiveTab = 'extraction_rules' | 'users' | 'prompts' | 'agents' | 'workflows' | 'domains'
type AgentInfo = { id: string; name: string; description: string }

const TAB_FROM_PATH: Record<string, ActiveTab> = {
  '/settings': 'extraction_rules',
  '/settings/': 'extraction_rules',
  '/settings/rules': 'extraction_rules',
  '/settings/extraction': 'extraction_rules',
  '/settings/users': 'users',
  '/settings/prompts': 'prompts',
  '/settings/agents': 'agents',
  '/settings/workflows': 'workflows',
  '/settings/domains': 'domains',
}

const TAB_PARAM_MAP: Record<string, ActiveTab> = {
  'extraction': 'extraction_rules',
  'rules': 'extraction_rules',
  'users': 'users',
  'prompts': 'prompts',
  'agents': 'agents',
  'workflows': 'workflows',
  'domains': 'domains',
}

export default function SettingsPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const params = useParams<{ tab: string }>()
  const qc = useQueryClient()
  // 从 URL path 或 route param 解析当前 tab
  const activeTab: ActiveTab = TAB_PARAM_MAP[params.tab || ''] || TAB_FROM_PATH[location.pathname] || 'rules'
  const [ruleValues, setRuleValues] = useState<Record<string, string>>({})
  const [extractStates, setExtractStates] = useState<Record<string, ExtractionRuleState>>(loadRuleStates)
  const [validationStates, setValidationStates] = useState<Record<string, boolean>>(loadValidationStates)
  // Prompts tab state
  const [showPromptModal, setShowPromptModal] = useState(false)
  const [editingPrompt, setEditingPrompt] = useState<any | null>(null)
  const [promptMsg, setPromptMsg] = useState('')
  const [promptName, setPromptName] = useState('')
  const [promptDomain, setPromptDomain] = useState('通用')
  const [promptContent, setPromptContent] = useState('')
  const [promptVersion, setPromptVersion] = useState('1.0')
  const [isGenerating, setIsGenerating] = useState(false)
  const [promptSaving, setPromptSaving] = useState(false)
  const [promptSearch, setPromptSearch] = useState('')
  const [promptDomainFilter, setPromptDomainFilter] = useState('')
  const [deletePromptTarget, setDeletePromptTarget] = useState<any | null>(null)

  // Agent config tab state
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

  // Workflow/n8n config tab state
  const [workflowEnabled, setWorkflowEnabled] = useState(false)
  const [workflowApiUrl, setWorkflowApiUrl] = useState('')
  const [workflowApiKey, setWorkflowApiKey] = useState('')
  const [workflowHasSavedApiKey, setWorkflowHasSavedApiKey] = useState(false)
  const [workflowTimeoutSeconds, setWorkflowTimeoutSeconds] = useState(10)
  const [workflowMsg, setWorkflowMsg] = useState('')
  const [workflowMsgOk, setWorkflowMsgOk] = useState(true)
  const [workflowTesting, setWorkflowTesting] = useState(false)

  // Domain tab state
  const [domainSearch, setDomainSearch] = useState('')
  const [showDomainModal, setShowDomainModal] = useState(false)
  const [editingDomain, setEditingDomain] = useState<any | null>(null)
  const [domainName, setDomainName] = useState('')
  const [domainDescription, setDomainDescription] = useState('')
  const [domainMsg, setDomainMsg] = useState('')
  const [deleteDomainTarget, setDeleteDomainTarget] = useState<any | null>(null)

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['settings-rules'],
    queryFn: async () => {
      const data = await settingsApi.getRules() as any[]
      const vals: Record<string, string> = {}
      data.forEach((r: any) => { vals[r.rule_key] = r.rule_value })
      setRuleValues(vals)
      return data
    },
  })

  const updateMut = useMutation({
    mutationFn: () => settingsApi.updateRules(
      Object.entries(ruleValues).map(([rule_key, rule_value]) => ({ rule_key, rule_value }))
    ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings-rules'] }),
  })

  const { data: prompts = [], isLoading: promptsLoading } = useQuery({
    queryKey: ['prompts'],
    queryFn: () => promptApi.list() as any,
    enabled: activeTab === 'prompts',
  })

  const deletePromptMut = useMutation({
    mutationFn: (id: string) => promptApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prompts'] })
      setDeletePromptTarget(null)
    },
  })

  function openCreatePrompt() {
    setEditingPrompt(null)
    setPromptName(''); setPromptDomain('通用'); setPromptContent(''); setPromptVersion('1.0')
    setPromptMsg(''); setShowPromptModal(true)
  }

  function openEditPrompt(p: any) {
    setEditingPrompt(p)
    setPromptName(p.name); setPromptDomain(p.domain); setPromptContent(p.content); setPromptVersion(p.version || '1.0')
    setPromptMsg(''); setShowPromptModal(true)
  }

  async function handleSavePrompt() {
    if (!promptName.trim() || !promptContent.trim()) return
    setPromptSaving(true)
    try {
      const body = { name: promptName.trim(), domain: promptDomain, content: promptContent.trim(), version: promptVersion }
      if (editingPrompt) {
        await promptApi.update(editingPrompt.id, body)
      } else {
        await promptApi.create(body)
      }
      qc.invalidateQueries({ queryKey: ['prompts'] })
      setShowPromptModal(false)
      setPromptMsg(editingPrompt ? '提示词已更新' : '提示词创建成功')
      setTimeout(() => setPromptMsg(''), 3000)
    } catch (e: any) {
      setPromptMsg(`保存失败：${e?.detail || e?.message || ''}`)
    } finally {
      setPromptSaving(false)
    }
  }

  async function handleGenerateTemplate() {
    if (!promptDomain) return
    setIsGenerating(true)
    try {
      const result = await promptApi.generateTemplate(promptDomain) as any
      setPromptContent(result.content ?? result)
    } catch (e: any) {
      setPromptMsg(`生成失败：${e?.detail || e?.message || ''}`)
    } finally {
      setIsGenerating(false)
    }
  }

  function updateExtractRule(id: string, patch: Partial<ExtractionRuleState>) {
    setExtractStates(prev => {
      const next = { ...prev, [id]: { ...prev[id], ...patch } }
      saveRuleStates(next)
      return next
    })
  }

  function toggleValidationRule(id: string) {
    setValidationStates(prev => {
      const next = { ...prev, [id]: !prev[id] }
      saveValidationStates(next)
      return next
    })
  }

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

  // -- Domain CRUD -------------------------------------------------------
  const { data: domainList = [], isLoading: domainsLoading } = useQuery({
    queryKey: ['domains', domainSearch],
    queryFn: () => domainApi.list(domainSearch || undefined) as any,
    enabled: activeTab === 'domains',
  })

  const createDomainMut = useMutation({
    mutationFn: (body: { name: string; description: string }) => domainApi.create(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['domains'] })
      setShowDomainModal(false)
      setDomainMsg('创建成功')
    },
    onError: (e: any) => setDomainMsg(e?.detail || '创建失败'),
  })

  const updateDomainMut = useMutation({
    mutationFn: ({ id, ...body }: { id: string; name?: string; description?: string }) =>
      domainApi.update(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['domains'] })
      setShowDomainModal(false)
      setEditingDomain(null)
      setDomainMsg('更新成功')
    },
    onError: (e: any) => setDomainMsg(e?.detail || '更新失败'),
  })

  const deleteDomainMut = useMutation({
    mutationFn: (id: string) => domainApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['domains'] })
      setDeleteDomainTarget(null)
      setDomainMsg('删除成功')
    },
    onError: (e: any) => setDomainMsg(e?.detail || '删除失败'),
  })

  function openCreateDomain() {
    setEditingDomain(null)
    setDomainName('')
    setDomainDescription('')
    setShowDomainModal(true)
  }

  function openEditDomain(d: any) {
    setEditingDomain(d)
    setDomainName(d.name)
    setDomainDescription(d.description)
    setShowDomainModal(true)
  }

  function handleSaveDomain() {
    if (!domainName.trim()) {
      setDomainMsg('名称不能为空')
      return
    }
    setDomainMsg('')
    if (editingDomain) {
      updateDomainMut.mutate({ id: editingDomain.id, name: domainName.trim(), description: domainDescription.trim() })
    } else {
      createDomainMut.mutate({ name: domainName.trim(), description: domainDescription.trim() })
    }
  }

  function handleDeleteDomain() {
    if (!deleteDomainTarget) return
    deleteDomainMut.mutate(deleteDomainTarget.id)
  }

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

  return (
    <div>
      {activeTab === 'agents' && (
        <div className="p-6 h-full overflow-auto bg-[var(--color-bg-base)]">
          <div className="max-w-2xl mx-auto space-y-6">
          {/* ── 连接信息 ── */}
          <div className="bg-white border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-4">
              <Bot size={16} className="text-purple-500" />
              <h3 className="text-sm font-semibold">{t('settings.tab_agents')}</h3>
            </div>
            <p className="text-xs text-gray-500 mb-4">{t('settings.agents_desc')}</p>

            <div className="space-y-4">
              {/* URL */}
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('settings.agent_url')}</label>
                <input
                  value={agentUrl}
                  onChange={e => { setAgentUrl(e.target.value); markAgentConfigDirty(e.target.value) }}
                  placeholder={t('settings.agent_url_placeholder')}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                />
              </div>

              {/* Auth toggle */}
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="agent-auth-toggle"
                  checked={agentAuthEnabled}
                  onChange={e => { setAgentAuthEnabled(e.target.checked); markAgentConfigDirty() }}
                  className="rounded"
                />
                <label htmlFor="agent-auth-toggle" className="text-sm text-gray-700">{t('settings.agent_auth_enabled')}</label>
              </div>

              {/* Username (when auth enabled) */}
              {agentAuthEnabled && (
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('settings.agent_username')}</label>
                  <input
                    value={agentUsername}
                    onChange={e => { setAgentUsername(e.target.value); markAgentConfigDirty() }}
                    className="w-full border rounded-lg px-3 py-2 text-sm"
                  />
                </div>
              )}

              {/* Password (when auth enabled) */}
              {agentAuthEnabled && (
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('settings.agent_password')}</label>
                  <input
                    type="password"
                    value={agentPassword}
                    onChange={e => { setAgentPassword(e.target.value); markAgentConfigDirty() }}
                    placeholder={agentHasSavedPassword ? t('settings.agent_password_saved') : t('settings.agent_password_placeholder')}
                    className="w-full border rounded-lg px-3 py-2 text-sm"
                  />
                </div>
              )}

              {/* Test button + message */}
              <div className="flex items-center gap-3">
                <button
                  onClick={handleSaveAgentConfig}
                  disabled={!agentUrl.trim()}
                  className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                >
                  {t('settings.agent_save_config')}
                </button>
                <button
                  onClick={handleTestConnection}
                  disabled={agentTesting || !agentUrl.trim()}
                  className="flex items-center gap-2 px-4 py-2 border rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  {agentTesting ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
                  {agentTesting ? t('settings.testing') : t('settings.test_connection')}
                </button>
                {agentMsg && (
                  <span className={`text-xs ${agentMsgOk ? 'text-green-600' : 'text-red-500'}`}>{agentMsg}</span>
                )}
              </div>
            </div>
          </div>

          {/* ── 选择智能体（保存配置后自动加载，可手动刷新）── */}
          {(agentConnected || targetAgentId || targetAgentName) && (
            <div className="bg-white border rounded-xl p-6">
              <div className="flex items-center gap-2 mb-4">
                <Bot size={16} className="text-purple-500" />
                <h3 className="text-sm font-semibold">{t('settings.select_agent')}</h3>
              </div>

              <div className="mb-4 flex items-center gap-3 flex-wrap">
                <button
                  onClick={handleFetchAgents}
                  disabled={agentFetching || !agentUrl.trim()}
                  className="flex items-center gap-2 px-4 py-2 border rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  {agentFetching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                  {agentFetching ? t('settings.fetching_agents') : t('settings.fetch_agents')}
                </button>
                {agentListDirty && <span className="text-xs text-amber-600">{t('settings.agent_list_dirty')}</span>}
                {agentFetching && <span className="text-xs text-gray-400">{t('settings.auto_fetching_agents')}</span>}
              </div>

              {(targetAgentId || targetAgentName) && (agentFetchedAgents.length === 0 || savedAgentMissingFromList) && (
                <div className="mb-3 rounded-lg border border-purple-200 bg-purple-50 p-3">
                  <p className="text-xs text-purple-600 mb-1">{t('settings.current_saved_agent')}</p>
                  <p className="text-sm font-medium text-gray-900">{targetAgentName || targetAgentId}</p>
                  {targetAgentId && <p className="text-xs text-gray-500 mt-0.5">{targetAgentId}</p>}
                  {savedAgentMissingFromList && <p className="text-xs text-amber-600 mt-1">{t('settings.saved_agent_not_found')}</p>}
                </div>
              )}

              {agentFetchedAgents.length > 0 ? (
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {agentFetchedAgents.map(a => (
                    <label
                      key={a.id}
                      ref={el => { agentRowRefs.current[a.id] = el }}
                      className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                        targetAgentId === a.id ? 'border-purple-500 bg-purple-50' : 'border-gray-200 hover:bg-gray-50'
                      }`}
                    >
                      <input
                        type="radio"
                        name="target-agent"
                        checked={targetAgentId === a.id}
                        onChange={() => handleSelectAgent(a.id, a.name)}
                        className="mt-0.5"
                      />
                      <div>
                        <p className="text-sm font-medium">{a.name}</p>
                        <p className="text-xs text-gray-400">{a.id}</p>
                        {a.description && <p className="text-xs text-gray-500 mt-0.5">{a.description}</p>}
                      </div>
                    </label>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-400">{agentFetching ? t('settings.fetching_agents') : t('settings.select_agent_hint')}</p>
              )}
            </div>
          )}
          </div>
        </div>
      )}

      {activeTab === 'workflows' && (
        <div className="p-6 h-full overflow-auto bg-[var(--color-bg-base)]">
          <div className="max-w-2xl mx-auto space-y-6">
          <div className="bg-white border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-4">
              <Workflow size={16} className="text-blue-500" />
              <h3 className="text-sm font-semibold">{t('settings.tab_workflows')}</h3>
            </div>
            <p className="text-xs text-gray-500 mb-4">{t('settings.workflows_desc')}</p>

            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="workflow-enabled-toggle"
                  checked={workflowEnabled}
                  onChange={e => setWorkflowEnabled(e.target.checked)}
                  className="rounded"
                />
                <label htmlFor="workflow-enabled-toggle" className="text-sm text-gray-700">{t('settings.workflow_enabled')}</label>
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('settings.workflow_url')}</label>
                <input
                  value={workflowApiUrl}
                  onChange={e => setWorkflowApiUrl(e.target.value)}
                  placeholder={t('settings.workflow_url_placeholder')}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                />
                <p className="text-xs text-gray-400 mt-1">{t('settings.workflow_url_hint')}</p>
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('settings.workflow_api_key')}</label>
                <input
                  type="password"
                  value={workflowApiKey}
                  onChange={e => setWorkflowApiKey(e.target.value)}
                  placeholder={workflowHasSavedApiKey ? t('settings.workflow_api_key_saved') : t('settings.workflow_api_key_placeholder')}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('settings.workflow_timeout')}</label>
                <input
                  type="number"
                  min={1}
                  max={120}
                  value={workflowTimeoutSeconds}
                  onChange={e => setWorkflowTimeoutSeconds(Number(e.target.value) || 10)}
                  className="w-40 border rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleTestWorkflowConnection}
                  disabled={workflowTesting}
                  className="px-4 py-2 rounded-lg border text-sm hover:bg-gray-50 disabled:opacity-50 flex items-center gap-2"
                >
                  {workflowTesting ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
                  {workflowTesting ? t('settings.testing') : t('settings.test_connection')}
                </button>
                <button
                  type="button"
                  onClick={handleSaveWorkflowConfig}
                  disabled={workflowTesting}
                  className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
                >
                  {workflowTesting ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                  {t('settings.workflow_save_config')}
                </button>
              </div>

              {workflowMsg && (
                <p className={`text-xs ${workflowMsgOk ? 'text-green-600' : 'text-red-600'}`}>{workflowMsg}</p>
              )}
            </div>
          </div>
          </div>
        </div>
      )}

      {activeTab === 'extraction_rules' && (
        <div className="max-w-2xl space-y-6">
          {/* 置信度阈值设置 */}
          <div className="bg-white border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-4">
              <Target size={16} className="text-blue-500"/>
              <h3 className="text-sm font-semibold">{t('settings.confidenceRules')}</h3>
            </div>
            <div className="space-y-4">
              {isLoading ? <p className="text-gray-400 text-sm">{t('common.loading')}</p> : (rules as any[]).map((r: any) => (
                <div key={r.rule_key} className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">{r.rule_label_cn}</p>
                    <p className="text-xs text-gray-400">{r.rule_label_en}</p>
                  </div>
                  {r.editable ? (
                    <input
                      value={ruleValues[r.rule_key] ?? r.rule_value}
                      onChange={e => setRuleValues(prev => ({ ...prev, [r.rule_key]: e.target.value }))}
                      className="w-24 border rounded-lg px-2 py-1 text-sm text-right"
                    />
                  ) : (
                    <span className="text-sm text-gray-500">{r.rule_value}</span>
                  )}
                </div>
              ))}
              <div className="pt-2 flex justify-end">
                <button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}
                  className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
                  {t('settings.save')}
                </button>
              </div>
            </div>
          </div>

          {/* 抽取约束规则 */}
          <div>
            <h3 className="text-sm font-semibold mb-1">{t('settings.llm_constraints')}</h3>
            <p className="text-xs text-gray-500 mb-3">{t('settings.llm_constraints_desc')}</p>
            <div className="bg-white border rounded-lg divide-y">
              {EXTRACTION_RULES.map(rule => {
                const state = extractStates[rule.id] ?? { enabled: rule.default_enabled, value: rule.default_value }
                return (
                  <div key={rule.id} className="p-4 flex items-start gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">{rule.label_cn}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{rule.description_cn}</p>
                      {rule.has_value && state.enabled && (
                        <div className="flex items-center gap-2 mt-2">
                          <span className="text-xs text-gray-500">
                            {rule.id === 'min_confidence' ? t('settings.min_confidence') : t('settings.min_docs')}
                          </span>
                          <input
                            type="number"
                            min={rule.id === 'min_confidence' ? 0.1 : 2}
                            max={rule.id === 'min_confidence' ? 1 : 10}
                            step={rule.id === 'min_confidence' ? 0.05 : 1}
                            value={state.value ?? rule.default_value}
                            onChange={e => updateExtractRule(rule.id, { value: Number(e.target.value) })}
                            className="w-20 border rounded px-2 py-0.5 text-sm"
                          />
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => updateExtractRule(rule.id, { enabled: !state.enabled })}
                      className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors ${state.enabled ? 'bg-black' : 'bg-gray-200'}`}>
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${state.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                )
              })}
            </div>
            <p className="text-xs text-gray-400 mt-2">{t('settings.docs_hint')}</p>
          </div>

          <div>
            <h3 className="text-sm font-semibold mb-1">{t('settings.quality_rules')}</h3>
            <p className="text-xs text-gray-500 mb-3">{t('settings.quality_rules_desc')}</p>
            <div className="bg-white border rounded-lg divide-y">
              {VALIDATION_RULES.map(rule => {
                const enabled = validationStates[rule.id] ?? true
                return (
                  <div key={rule.id} className="p-4 flex items-start gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">{rule.label_cn}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{rule.description_cn}</p>
                    </div>
                    <button
                      onClick={() => toggleValidationRule(rule.id)}
                      className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors ${enabled ? 'bg-black' : 'bg-gray-200'}`}>
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${enabled ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'prompts' && (
        <div>
          {/* Toolbar */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={promptSearch}
                onChange={e => setPromptSearch(e.target.value)}
                placeholder="按名称 / ID 筛选"
                className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-52"
              />
              {promptSearch && (
                <button onClick={() => setPromptSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
                  <X size={12} />
                </button>
              )}
            </div>
            <select
              value={promptDomainFilter}
              onChange={e => setPromptDomainFilter(e.target.value)}
              className="border rounded-lg px-3 py-1.5 text-sm"
            >
              <option value="">全部领域</option>
              {['供应链', '法律', '医疗', 'HR', '财务', '教育', '通用', '其他'].map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <div className="flex-1" />
            {promptMsg && (
              <span className={`text-xs ${promptMsg.includes('成功') || promptMsg.includes('更新') ? 'text-green-600' : 'text-red-500'}`}>
                {promptMsg}
              </span>
            )}
            <button
              onClick={openCreatePrompt}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm"
            >
              <Plus size={14} /> 新建提示词
            </button>
          </div>

          {/* Table */}
          <div className="border rounded-xl overflow-hidden bg-white">
            {promptsLoading ? (
              <p className="text-center text-gray-400 py-8 text-sm">加载中...</p>
            ) : (prompts as any[]).filter((p: any) => {
              const q = promptSearch.toLowerCase()
              const matchSearch = !q || p.name?.toLowerCase().includes(q) || p.id?.toLowerCase().includes(q)
              const matchDomain = !promptDomainFilter || p.domain === promptDomainFilter
              return matchSearch && matchDomain
            }).length === 0 ? (
              <p className="text-center text-gray-400 py-8 text-sm">
                {(prompts as any[]).length === 0 ? '暂无提示词模版' : '没有匹配的模版'}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">模版 ID</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">名称</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">业务域</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">版本号</th>
                    <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {(prompts as any[])
                    .filter((p: any) => {
                      const q = promptSearch.toLowerCase()
                      const matchSearch = !q || p.name?.toLowerCase().includes(q) || p.id?.toLowerCase().includes(q)
                      const matchDomain = !promptDomainFilter || p.domain === promptDomainFilter
                      return matchSearch && matchDomain
                    })
                    .map((p: any) => (
                      <tr key={p.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 font-mono text-xs text-gray-400" title={p.id}>
                          {p.id?.slice(0, 8)}
                        </td>
                        <td className="px-4 py-3 font-medium text-gray-800 max-w-[200px] truncate">{p.name}</td>
                        <td className="px-4 py-3">
                          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{p.domain}</span>
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">v{p.version}</td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center gap-2 justify-end">
                            <button
                              onClick={() => openEditPrompt(p)}
                              className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black"
                              title="编辑"
                            >
                              <Pencil size={13} />
                            </button>
                            <button
                              onClick={() => setDeletePromptTarget(p)}
                              className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                              title="删除"
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Create / Edit Modal */}
          {showPromptModal && (
            <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6" onClick={() => setShowPromptModal(false)}>
              <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl flex flex-col" style={{ maxHeight: 'calc(100vh - 3rem)' }} onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between px-6 py-4 border-b">
                  <h3 className="font-semibold">{editingPrompt ? '编辑提示词模版' : '新建提示词模版'}</h3>
                  <button onClick={() => setShowPromptModal(false)} className="text-gray-400 hover:text-black"><X size={16} /></button>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">名称 *</label>
                      <input
                        value={promptName}
                        onChange={e => setPromptName(e.target.value)}
                        placeholder="提示词模版名称"
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">业务域 *</label>
                      <select
                        value={promptDomain}
                        onChange={e => setPromptDomain(e.target.value)}
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                      >
                        {['供应链', '法律', '医疗', 'HR', '财务', '教育', '通用', '其他'].map(d => (
                          <option key={d} value={d}>{d}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-xs font-medium text-gray-600">内容 *</label>
                      <button
                        type="button"
                        onClick={handleGenerateTemplate}
                        disabled={isGenerating}
                        className="flex items-center gap-1 px-2.5 py-1 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                      >
                        {isGenerating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
                        {isGenerating ? '生成中...' : '一键生成模版'}
                      </button>
                    </div>
                    <textarea
                      value={promptContent}
                      onChange={e => setPromptContent(e.target.value)}
                      placeholder="输入提示词内容，或点击右上角一键生成..."
                      rows={10}
                      className="w-full border rounded-lg px-3 py-2 text-sm font-mono resize-y"
                    />
                  </div>
                  {promptMsg && showPromptModal && (
                    <p className="text-xs text-red-500">{promptMsg}</p>
                  )}
                </div>
                <div className="flex justify-end gap-3 px-6 py-4 border-t">
                  <button onClick={() => setShowPromptModal(false)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                  <button
                    onClick={handleSavePrompt}
                    disabled={promptSaving || !promptName.trim() || !promptContent.trim()}
                    className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {promptSaving && <Loader2 size={13} className="animate-spin" />}
                    {promptSaving ? '保存中...' : '确认保存'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Delete confirm */}
          {deletePromptTarget && (
            <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
              <div className="bg-white rounded-xl shadow-lg p-6 w-96">
                <h3 className="font-semibold mb-2">删除提示词模版</h3>
                <p className="text-sm text-gray-600 mb-5">
                  确认删除「{deletePromptTarget.name}」？此操作不可撤销。
                </p>
                <div className="flex justify-end gap-3">
                  <button onClick={() => setDeletePromptTarget(null)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                  <button
                    onClick={() => deletePromptMut.mutate(deletePromptTarget.id)}
                    disabled={deletePromptMut.isPending}
                    className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {deletePromptMut.isPending ? '删除中...' : '确认删除'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'users' && (
        <UserManagementPanel />
      )}

      {activeTab === 'domains' && (
        <div>
          {/* Toolbar */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={domainSearch}
                onChange={e => setDomainSearch(e.target.value)}
                placeholder="按名称搜索"
                className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-52"
              />
              {domainSearch && (
                <button onClick={() => setDomainSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
                  <X size={12} />
                </button>
              )}
            </div>
            <div className="flex-1" />
            {domainMsg && (
              <span className={`text-xs ${domainMsg.includes('成功') ? 'text-green-600' : 'text-red-500'}`}>
                {domainMsg}
              </span>
            )}
            <button
              onClick={openCreateDomain}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm hover:bg-gray-800"
            >
              <Plus size={14} /> 新增领域
            </button>
          </div>

          {/* List */}
          <div className="bg-white border rounded-lg overflow-hidden">
            {domainsLoading ? (
              <p className="text-center text-gray-400 py-6 text-sm">加载中...</p>
            ) : (domainList as any[]).length === 0 ? (
              <p className="text-center text-gray-400 py-6 text-sm">暂无领域，点击"新增领域"开始</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">名称</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">描述</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">更新时间</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 w-20">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(domainList as any[]).map((d: any) => (
                    <tr key={d.id} className="border-b last:border-0 hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium">{d.name}</td>
                      <td className="px-4 py-3 text-gray-500 max-w-xs truncate">{d.description || '—'}</td>
                      <td className="px-4 py-3 text-gray-500">
                        {d.updated_at ? new Date(d.updated_at).toLocaleDateString('zh-CN') : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <button onClick={() => openEditDomain(d)} className="text-gray-500 hover:text-black">
                            <Pencil size={14} />
                          </button>
                          <button onClick={() => setDeleteDomainTarget(d)} className="text-red-500 hover:text-red-700">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Create/Edit Modal */}
          {showDomainModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
              <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
                <h3 className="text-lg font-semibold mb-4">
                  {editingDomain ? '编辑领域' : '新增领域'}
                </h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">名称 *</label>
                    <input
                      value={domainName}
                      onChange={e => setDomainName(e.target.value)}
                      placeholder="输入领域名称"
                      className="w-full border rounded-lg px-3 py-2 text-sm"
                      autoFocus
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">描述</label>
                    <textarea
                      value={domainDescription}
                      onChange={e => setDomainDescription(e.target.value)}
                      placeholder="输入领域描述（可选）"
                      rows={3}
                      className="w-full border rounded-lg px-3 py-2 text-sm resize-none"
                    />
                  </div>
                </div>
                <div className="flex justify-end gap-2 mt-6">
                  <button
                    onClick={() => { setShowDomainModal(false); setEditingDomain(null); setDomainMsg('') }}
                    className="px-4 py-1.5 border rounded-lg text-sm text-gray-600"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleSaveDomain}
                    disabled={createDomainMut.isPending || updateDomainMut.isPending}
                    className="px-4 py-1.5 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {createDomainMut.isPending || updateDomainMut.isPending ? '保存中...' : '保存'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Delete Confirm Modal */}
          {deleteDomainTarget && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
              <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
                <h3 className="text-lg font-semibold mb-2">确认删除</h3>
                <p className="text-sm text-gray-500 mb-6">
                  确定要删除领域「{deleteDomainTarget.name}」吗？此操作不可撤销。
                </p>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setDeleteDomainTarget(null)}
                    className="px-4 py-1.5 border rounded-lg text-sm text-gray-600"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleDeleteDomain}
                    disabled={deleteDomainMut.isPending}
                    className="px-4 py-1.5 bg-red-600 text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {deleteDomainMut.isPending ? '删除中...' : '确认删除'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

    </div>
  )
}
