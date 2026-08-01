import type { TFunction } from 'i18next'
import { Loader2, LockKeyhole, Wifi, Workflow } from 'lucide-react'
import type { WorkflowSettingsViewModel } from '../hooks/useWorkflowSettings'

type WorkflowSettingsTabProps = {
  settings: WorkflowSettingsViewModel
  t: TFunction
}

export default function WorkflowSettingsTab({ settings, t }: WorkflowSettingsTabProps) {
  const {
    workflowEnabled,
    workflowApiUrl,
    workflowHasSavedApiKey,
    workflowTimeoutSeconds,
    workflowMsg,
    workflowMsgOk,
    workflowTesting,
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

            <div className="mb-4 flex items-start gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800">
              <LockKeyhole size={14} className="mt-0.5 shrink-0" />
              <span>{t('settings.workflow_environment_managed')}</span>
            </div>

            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="workflow-enabled-toggle"
                  checked={workflowEnabled}
                  disabled
                  className="rounded"
                />
                <label htmlFor="workflow-enabled-toggle" className="text-sm text-gray-700">{t('settings.workflow_enabled')}</label>
              </div>

              <div>
                <label htmlFor="workflow-api-url" className="block text-xs text-gray-500 mb-1">{t('settings.workflow_url')}</label>
                <input
                  id="workflow-api-url"
                  value={workflowApiUrl}
                  readOnly
                  aria-readonly="true"
                  className="w-full border rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700"
                />
                <p className="text-xs text-gray-400 mt-1">{t('settings.workflow_url_managed_hint')}</p>
              </div>

              <div>
                <label htmlFor="workflow-api-key" className="block text-xs text-gray-500 mb-1">{t('settings.workflow_api_key')}</label>
                <input
                  id="workflow-api-key"
                  type="password"
                  value=""
                  readOnly
                  aria-readonly="true"
                  placeholder={workflowHasSavedApiKey
                    ? t('settings.workflow_api_key_managed')
                    : t('settings.workflow_api_key_unavailable')}
                  className="w-full border rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700"
                />
              </div>

              <div>
                <label htmlFor="workflow-timeout-seconds" className="block text-xs text-gray-500 mb-1">{t('settings.workflow_timeout')}</label>
                <input
                  id="workflow-timeout-seconds"
                  type="number"
                  min={1}
                  max={120}
                  value={workflowTimeoutSeconds}
                  readOnly
                  aria-readonly="true"
                  className="w-40 border rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700"
                />
              </div>

              <div>
                <button
                  type="button"
                  onClick={handleTestWorkflowConnection}
                  disabled={workflowTesting}
                  className="px-4 py-2 rounded-lg border text-sm hover:bg-gray-50 disabled:opacity-50 flex items-center gap-2"
                >
                  {workflowTesting ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
                  {workflowTesting ? t('settings.testing') : t('settings.test_connection')}
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
