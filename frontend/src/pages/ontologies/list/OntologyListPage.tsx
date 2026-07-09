import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/Table'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import type { OntologyListItem } from '@/types/ontology'
import { Plus, Search, X, Network, FileText, GitBranch } from 'lucide-react'

const statusMap: Record<string, { label: string; variant: "default" | "success" | "warning" | "danger" | "info" | "outline" | "secondary" }> = {
  draft: { label: "草稿", variant: "warning" },
  review: { label: "审核中", variant: "info" },
  published: { label: "已发布", variant: "success" },
}

export default function OntologyListPage() {
  const [nameFilter, setNameFilter] = useState('')
  const [domainFilter, setDomainFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { t } = useTranslation()

  const { data, isLoading } = useQuery({
    queryKey: ['ontologies'],
    queryFn: () => ontologyApi.list({ page_size: 1000 }) as any,
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => ontologyApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ontologies'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      setDeleteTarget(null)
    },
  })

  const allItems: OntologyListItem[] = data?.items ?? []

  const domains = useMemo(() => {
    const d = new Set(allItems.map(o => o.domain).filter(Boolean))
    return Array.from(d)
  }, [allItems])

  const filteredItems = useMemo(() => {
    let list = allItems
    if (nameFilter.trim())
      list = list.filter(o => o.name.toLowerCase().includes(nameFilter.trim().toLowerCase()))
    if (domainFilter)
      list = list.filter(o => o.domain === domainFilter)
    if (statusFilter)
      list = list.filter(o => o.status === statusFilter)
    return list
  }, [allItems, nameFilter, domainFilter, statusFilter])

  const hasFilters = nameFilter || domainFilter || statusFilter
  const totalCount = allItems.length

  return (
    <div className="space-y-6">
      {/* Filters + 新建按钮 */}
      <Card className="p-4">
        <div className="flex flex-wrap gap-3 items-end">
          <div className="w-72">
            <Input
              placeholder="搜索本体名称..."
              value={nameFilter}
              onChange={e => setNameFilter(e.target.value)}
              className="h-9"
            />
          </div>
          <select
            value={domainFilter}
            onChange={e => setDomainFilter(e.target.value)}
            className="h-9 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-sm text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
          >
            <option value="">全部领域</option>
            {domains.map(d => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="h-9 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-sm text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
          >
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="review">审核中</option>
            <option value="published">已发布</option>
          </select>
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={() => { setNameFilter(''); setDomainFilter(''); setStatusFilter('') }}>
              <X size={14} /> 清除
            </Button>
          )}
          <div className="ml-auto">
            <button
              onClick={() => navigate('/ontologies/new')}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium text-white bg-[var(--color-nav-bg)] hover:opacity-90 transition-colors shadow-sm"
            >
              <Plus size={14} />
              新建本体
            </button>
          </div>
        </div>
        {hasFilters && (
          <p className="text-xs text-[var(--color-text-tertiary)] mt-2">
            筛选结果：{filteredItems.length} / {totalCount}
          </p>
        )}
      </Card>

      {/* Table */}
      <Card>
        {isLoading ? (
          <LoadingState message="加载本体列表..." />
        ) : filteredItems.length === 0 ? (
          <EmptyState
            title={hasFilters ? "无匹配结果" : "暂无本体"}
            description={hasFilters ? "尝试调整筛选条件" : "创建您的第一个本体项目"}
            action={!hasFilters && (
              <Button onClick={() => navigate('/ontologies/new')} size="sm">
                <Plus size={14} /> 新建本体
              </Button>
            )}
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead className="text-center">领域</TableHead>
                <TableHead className="text-center">状态</TableHead>
                <TableHead className="text-center">版本</TableHead>
                <TableHead className="text-center">构建方式</TableHead>
                <TableHead className="text-center">实体</TableHead>
                <TableHead className="text-center">关系</TableHead>
                <TableHead className="text-center">创建时间</TableHead>
                <TableHead className="text-center">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredItems.map((item) => {
                const status = statusMap[item.status] || { label: item.status, variant: "outline" as const }
                return (
                  <TableRow key={item.id} className="cursor-pointer" onClick={() => navigate(`/ontologies/${item.id}`)}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Network size={16} className="text-[var(--color-text-tertiary)]" />
                        <span className="font-medium text-[var(--color-text-primary)]">{item.name}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-center text-[var(--color-text-secondary)]">{item.domain}</TableCell>
                    <TableCell className="text-center"><Badge variant={status.variant}>{status.label}</Badge></TableCell>
                    <TableCell className="text-center font-mono text-xs text-[var(--color-text-tertiary)]">{item.version}</TableCell>
                    <TableCell className="text-center">
                      {item.build_mode === 'pipeline_mapping' ? (
                        <span className="inline-flex items-center gap-1 text-xs text-[var(--color-info)]">
                          <GitBranch size={12} /> Pipeline
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs text-[var(--color-warning)]">
                          <FileText size={12} /> LLM
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-center font-mono text-xs text-[var(--color-text-secondary)]">
                      {item.entity_count ?? 0}
                    </TableCell>
                    <TableCell className="text-center font-mono text-xs text-[var(--color-text-secondary)]">
                      {item.relation_count ?? 0}
                    </TableCell>
                    <TableCell className="text-center text-xs text-[var(--color-text-tertiary)]">
                      {new Date(item.created_at).toLocaleDateString('zh-CN')}
                    </TableCell>
                    <TableCell className="text-center">
                      <div className="flex items-center justify-center gap-2" onClick={e => e.stopPropagation()}>
                        <Button variant="ghost" size="icon-sm" onClick={() => navigate(`/ontologies/${item.id}`)} title="查看">
                          <Search size={14} />
                        </Button>
                        <Button variant="ghost" size="icon-sm" onClick={() => setDeleteTarget({ id: item.id, name: item.name })} title="删除">
                          <X size={14} className="text-[var(--color-danger)]" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
        title="确认删除"
        description={deleteTarget ? `确定要删除本体「${deleteTarget.name}」吗？此操作不可撤销。` : ''}
        variant="danger"
        loading={deleteMut.isPending}
      />
    </div>
  )
}
