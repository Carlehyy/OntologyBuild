import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { domainApi } from '@/api/ontologies'


function domainErrorMessage(error: any, fallback: string) {
  const detail = error?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg
  if (detail && typeof detail.message === 'string') return detail.message
  if (typeof error?.message === 'string') return error.message
  return fallback
}


export function useDomainSettings(activeTab: string) {
  const [domainSearch, setDomainSearch] = useState('')
  const [showDomainModal, setShowDomainModal] = useState(false)
  const [editingDomain, setEditingDomain] = useState<any | null>(null)
  const [domainName, setDomainName] = useState('')
  const [domainDescription, setDomainDescription] = useState('')
  const [domainMsg, setDomainMsg] = useState('')
  const [deleteDomainTarget, setDeleteDomainTarget] = useState<any | null>(null)
  const qc = useQueryClient()

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
    onError: (e: any) => setDomainMsg(domainErrorMessage(e, '创建失败')),
  })

  const updateDomainMut = useMutation({
    mutationFn: ({ id, ...body }: { id: string; name?: string; description?: string }) =>
      domainApi.update(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['domains'] })
      qc.invalidateQueries({ queryKey: ['ontologies'] })
      setShowDomainModal(false)
      setEditingDomain(null)
      setDomainMsg('更新成功')
    },
    onError: (e: any) => setDomainMsg(domainErrorMessage(e, '更新失败')),
  })

  const deleteDomainMut = useMutation({
    mutationFn: (id: string) => domainApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['domains'] })
      setDeleteDomainTarget(null)
      setDomainMsg('删除成功')
    },
    onError: (e: any) => setDomainMsg(domainErrorMessage(e, '删除失败')),
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

  return {
    domainList,
    domainsLoading,
    domainSearch,
    setDomainSearch,
    showDomainModal,
    setShowDomainModal,
    editingDomain,
    setEditingDomain,
    domainName,
    setDomainName,
    domainDescription,
    setDomainDescription,
    domainMsg,
    setDomainMsg,
    deleteDomainTarget,
    setDeleteDomainTarget,
    createDomainMut,
    updateDomainMut,
    deleteDomainMut,
    openCreateDomain,
    openEditDomain,
    handleSaveDomain,
    handleDeleteDomain,
  }
}

export type DomainSettingsViewModel = ReturnType<typeof useDomainSettings>
