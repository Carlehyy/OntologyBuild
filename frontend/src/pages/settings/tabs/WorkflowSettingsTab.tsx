import type { TFunction } from 'i18next'
import { Check, Loader2, Wifi, Workflow } from 'lucide-react'
import type { WorkflowSettingsViewModel } from '../hooks/useWorkflowSettings'

type WorkflowSettingsTabProps = {
  settings: WorkflowSettingsViewModel
  t: TFunction
}

export default function WorkflowSettingsTab({ settings, t }: WorkflowSettingsTabProps) {
  const {
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
  } = settings

  return (
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
  )
}
