import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { settingsApi } from '@/api/ontologies'
import {
  loadRuleStates,
  saveRuleStates,
  loadValidationStates,
  saveValidationStates,
  type ExtractionRuleState,
} from '@/utils/extractionRules'

export function useRuleSettings() {
  const [ruleValues, setRuleValues] = useState<Record<string, string>>({})
  const [extractStates, setExtractStates] = useState<Record<string, ExtractionRuleState>>(loadRuleStates)
  const [validationStates, setValidationStates] = useState<Record<string, boolean>>(loadValidationStates)
  const qc = useQueryClient()

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

  return {
    rules,
    isLoading,
    ruleValues,
    setRuleValues,
    extractStates,
    validationStates,
    updateMut,
    updateExtractRule,
    toggleValidationRule,
  }
}

export type RuleSettingsViewModel = ReturnType<typeof useRuleSettings>
