import { useLocation, useParams } from 'react-router-dom'
import UserManagementPanel from './UserManagementPanel'
import AssistantEvalTab from './tabs/AssistantEvalTab'
import AssistantWidgetTab from './tabs/AssistantWidgetTab'
import { useDomainSettings } from './hooks/useDomainSettings'
import DomainSettingsTab from './tabs/DomainSettingsTab'
import MonitoringTab from './tabs/MonitoringTab'

type ActiveTab = 'users' | 'domains' | 'monitoring' | 'assistant-eval' | 'assistant-widget'

const TAB_FROM_PATH: Record<string, ActiveTab> = {
  '/settings': 'users',
  '/settings/': 'users',
  '/settings/users': 'users',
  '/settings/domains': 'domains',
  '/settings/monitoring': 'monitoring',
  '/settings/assistant-eval': 'assistant-eval',
  '/settings/assistant-widget': 'assistant-widget',
}

const TAB_PARAM_MAP: Record<string, ActiveTab> = {
  'users': 'users',
  'domains': 'domains',
  'monitoring': 'monitoring',
  'assistant-eval': 'assistant-eval',
  'assistant-widget': 'assistant-widget',
}

export default function SettingsPage() {
  const location = useLocation()
  const params = useParams<{ tab: string }>()
  // 从 URL path 或 route param 解析当前 tab
  const activeTab: ActiveTab = TAB_PARAM_MAP[params.tab || ''] || TAB_FROM_PATH[location.pathname] || 'users'

  // Keep every capability hook mounted in this order so state survives tab changes.
  const domainSettings = useDomainSettings(activeTab)

  return (
    <div>
      {activeTab === 'users' && <UserManagementPanel />}
      {activeTab === 'domains' && <DomainSettingsTab settings={domainSettings} />}
      {activeTab === 'monitoring' && <MonitoringTab />}
      {activeTab === 'assistant-eval' && <AssistantEvalTab />}
      {activeTab === 'assistant-widget' && <AssistantWidgetTab />}
    </div>
  )
}
