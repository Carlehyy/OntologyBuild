import { useTranslation } from 'react-i18next'
import { useLocation, useParams } from 'react-router-dom'
import UserManagementPanel from './UserManagementPanel'
import { useAgentSettings } from './hooks/useAgentSettings'
import { useDomainSettings } from './hooks/useDomainSettings'
import AgentSettingsTab from './tabs/AgentSettingsTab'
import DomainSettingsTab from './tabs/DomainSettingsTab'
import MonitoringTab from './tabs/MonitoringTab'

type ActiveTab = 'users' | 'agents' | 'domains' | 'monitoring'

const TAB_FROM_PATH: Record<string, ActiveTab> = {
  '/settings': 'users',
  '/settings/': 'users',
  '/settings/users': 'users',
  '/settings/agents': 'agents',
  '/settings/domains': 'domains',
  '/settings/monitoring': 'monitoring',
}

const TAB_PARAM_MAP: Record<string, ActiveTab> = {
  'users': 'users',
  'agents': 'agents',
  'domains': 'domains',
  'monitoring': 'monitoring',
}

export default function SettingsPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const params = useParams<{ tab: string }>()
  // 从 URL path 或 route param 解析当前 tab
  const activeTab: ActiveTab = TAB_PARAM_MAP[params.tab || ''] || TAB_FROM_PATH[location.pathname] || 'users'

  // Keep every capability hook mounted in this order so state survives tab changes.
  const agentSettings = useAgentSettings(activeTab, t)
  const domainSettings = useDomainSettings(activeTab)

  return (
    <div>
      {activeTab === 'agents' && <AgentSettingsTab settings={agentSettings} t={t} />}
      {activeTab === 'users' && <UserManagementPanel />}
      {activeTab === 'domains' && <DomainSettingsTab settings={domainSettings} />}
      {activeTab === 'monitoring' && <MonitoringTab />}
    </div>
  )
}
