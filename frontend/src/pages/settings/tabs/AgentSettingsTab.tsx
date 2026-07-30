import type { TFunction } from 'i18next'
import { Bot, Loader2, RefreshCw, Wifi } from 'lucide-react'
import type { AgentSettingsViewModel } from '../hooks/useAgentSettings'

type AgentSettingsTabProps = {
  settings: AgentSettingsViewModel
  t: TFunction
}

export default function AgentSettingsTab({ settings, t }: AgentSettingsTabProps) {
  const {
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
  } = settings

  return (
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
  )
}
