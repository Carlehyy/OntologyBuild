import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/Table'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { Modal } from '@/components/ui/Modal'
import { Plus, Trash2, BookOpen } from 'lucide-react'
import axios from 'axios'

function getToken() { return localStorage.getItem('token') || '' }

export default function VocabularyTab({ ontologyId }: { ontologyId: string }) {
  const [showCreate, setShowCreate] = useState(false)
  const [search, setSearch] = useState('')
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['vocabulary', ontologyId, search],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/vocabulary?q=${encodeURIComponent(search)}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
  })

  const createMut = useMutation({
    mutationFn: (body: any) => axios.post(`/api/v2/ontologies/${ontologyId}/vocabulary`, body, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vocabulary', ontologyId] }); setShowCreate(false) },
  })

  const deleteMut = useMutation({
    mutationFn: (vid: string) => axios.delete(`/api/v2/ontologies/${ontologyId}/vocabulary/${vid}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['vocabulary', ontologyId] }),
  })

  const vocab = data?.data || []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1">
          <Input placeholder="搜索词表..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <Button onClick={() => setShowCreate(true)} size="sm"><Plus size={14} /> 新增词条</Button>
      </div>

      {isLoading ? <LoadingState /> : vocab.length === 0 ? (
        <EmptyState title="暂无词表" description="添加同义词和别名" action={<Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} /> 新增</Button>} />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标准词</TableHead>
                <TableHead>同义词</TableHead>
                <TableHead>缩写</TableHead>
                <TableHead>类型</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {vocab.map((v: any) => (
                <TableRow key={v.id}>
                  <TableCell className="font-medium text-sm">{v.canonical}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {(v.synonyms || []).map((s: string) => <Badge key={s} variant="secondary" className="text-[10px]">{s}</Badge>)}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs font-mono text-[var(--color-text-tertiary)]">
                    {(v.abbreviations || []).join(', ')}
                  </TableCell>
                  <TableCell>{v.entity_type ? <Badge variant="outline" className="text-[10px]">{v.entity_type}</Badge> : '-'}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon-sm" onClick={() => deleteMut.mutate(v.id)}>
                      <Trash2 size={12} className="text-[var(--color-danger)]" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {showCreate && (
        <VocabCreateModal onClose={() => setShowCreate(false)} onSubmit={(b: any) => createMut.mutate(b)} loading={createMut.isPending} />
      )}
    </div>
  )
}

function VocabCreateModal({ onClose, onSubmit, loading }: { onClose: () => void; onSubmit: (b: any) => void; loading: boolean }) {
  const [canonical, setCanonical] = useState('')
  const [synonyms, setSynonyms] = useState('')

  const handleSubmit = () => {
    onSubmit({
      canonical,
      synonyms: synonyms.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    })
  }

  return (
    <Modal open onClose={onClose} title="新增词条" size="sm"
      footer={<><Button variant="ghost" onClick={onClose}>取消</Button><Button onClick={handleSubmit} loading={loading}>创建</Button></>}>
      <div className="space-y-3">
        <Input label="标准词" value={canonical} onChange={e => setCanonical(e.target.value)} required />
        <Input label="同义词（逗号分隔）" value={synonyms} onChange={e => setSynonyms(e.target.value)} />
      </div>
    </Modal>
  )
}
