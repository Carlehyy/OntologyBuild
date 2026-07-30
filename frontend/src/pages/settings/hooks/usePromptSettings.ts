import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { promptApi } from '@/api/ontologies'

export function usePromptSettings(activeTab: string) {
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
  const qc = useQueryClient()

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

  return {
    prompts,
    promptsLoading,
    showPromptModal,
    setShowPromptModal,
    editingPrompt,
    promptMsg,
    promptName,
    setPromptName,
    promptDomain,
    setPromptDomain,
    promptContent,
    setPromptContent,
    isGenerating,
    promptSaving,
    promptSearch,
    setPromptSearch,
    promptDomainFilter,
    setPromptDomainFilter,
    deletePromptTarget,
    setDeletePromptTarget,
    deletePromptMut,
    openCreatePrompt,
    openEditPrompt,
    handleSavePrompt,
    handleGenerateTemplate,
  }
}

export type PromptSettingsViewModel = ReturnType<typeof usePromptSettings>
