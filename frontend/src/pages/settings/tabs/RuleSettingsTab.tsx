import type { TFunction } from 'i18next'
import { Target } from 'lucide-react'
import {
  EXTRACTION_RULES,
  VALIDATION_RULES,
} from '@/utils/extractionRules'
import type { RuleSettingsViewModel } from '../hooks/useRuleSettings'

type RuleSettingsTabProps = {
  settings: RuleSettingsViewModel
  t: TFunction
}

export default function RuleSettingsTab({ settings, t }: RuleSettingsTabProps) {
  const {
    rules,
    isLoading,
    ruleValues,
    setRuleValues,
    extractStates,
    validationStates,
    updateMut,
    updateExtractRule,
    toggleValidationRule,
  } = settings

  return (
        <div className="max-w-2xl space-y-6">
          {/* 置信度阈值设置 */}
          <div className="bg-white border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-4">
              <Target size={16} className="text-blue-500"/>
              <h3 className="text-sm font-semibold">{t('settings.rules')}</h3>
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
  )
}
