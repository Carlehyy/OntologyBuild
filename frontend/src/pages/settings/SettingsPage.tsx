import { useTranslation } from 'react-i18next'
import { useLocation, useParams } from 'react-router-dom'
import UserManagementPanel from './UserManagementPanel'
import MinioSettingsPanel from './components/MinioSettingsPanel'
import { useRuleSettings } from './hooks/useRuleSettings'
import { usePromptSettings } from './hooks/usePromptSettings'
import { useAgentSettings } from './hooks/useAgentSettings'
import { useWorkflowSettings } from './hooks/useWorkflowSettings'
import { useDomainSettings } from './hooks/useDomainSettings'
import AgentSettingsTab from './tabs/AgentSettingsTab'
import WorkflowSettingsTab from './tabs/WorkflowSettingsTab'
import RuleSettingsTab from './tabs/RuleSettingsTab'
import PromptSettingsTab from './tabs/PromptSettingsTab'
import DomainSettingsTab from './tabs/DomainSettingsTab'

type ActiveTab = 'extraction_rules' | 'users' | 'prompts' | 'agents' | 'workflows' | 'minio' | 'domains'

const TAB_FROM_PATH: Record<string, ActiveTab> = {
  '/settings': 'extraction_rules',
  '/settings/': 'extraction_rules',
  '/settings/rules': 'extraction_rules',
  '/settings/extraction': 'extraction_rules',
  '/settings/users': 'users',
  '/settings/prompts': 'prompts',
  '/settings/agents': 'agents',
  '/settings/workflows': 'workflows',
  '/settings/minio': 'minio',
  '/settings/domains': 'domains',
}

const TAB_PARAM_MAP: Record<string, ActiveTab> = {
  'extraction': 'extraction_rules',
  'rules': 'extraction_rules',
  'users': 'users',
  'prompts': 'prompts',
  'agents': 'agents',
  'workflows': 'workflows',
  'minio': 'minio',
  'domains': 'domains',
}

export default function SettingsPage() {
  const { t } = useTranslation()
  const location = useLocation()
  const params = useParams<{ tab: string }>()
  // 从 URL path 或 route param 解析当前 tab
  const activeTab: ActiveTab = TAB_PARAM_MAP[params.tab || ''] || TAB_FROM_PATH[location.pathname] || 'rules'

  // Keep every capability hook mounted in this order so state survives tab changes.
  const ruleSettings = useRuleSettings()
  const promptSettings = usePromptSettings(activeTab)
  const agentSettings = useAgentSettings(activeTab, t)
  const workflowSettings = useWorkflowSettings(activeTab, t)
  const domainSettings = useDomainSettings(activeTab)

  return (
    <div>
      {activeTab === 'agents' && <AgentSettingsTab settings={agentSettings} t={t} />}
      {activeTab === 'workflows' && <WorkflowSettingsTab settings={workflowSettings} t={t} />}
      {activeTab === 'minio' && <MinioSettingsPanel />}
      {activeTab === 'extraction_rules' && <RuleSettingsTab settings={ruleSettings} t={t} />}
      {activeTab === 'prompts' && <PromptSettingsTab settings={promptSettings} />}
      {activeTab === 'users' && <UserManagementPanel />}
      {activeTab === 'domains' && <DomainSettingsTab settings={domainSettings} />}
    </div>
  )
}
