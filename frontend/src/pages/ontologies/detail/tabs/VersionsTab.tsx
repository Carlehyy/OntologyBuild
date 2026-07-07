import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/Table'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import { Modal } from '@/components/ui/Modal'
import { GitBranch, RotateCcw, Plus, ChevronLeft, ChevronRight, Search } from 'lucide-react'
import axios from 'axios'

function getToken() { return localStorage.getItem('token') || '' }

const PAGE_SIZE = 10

export default function VersionsTab({ ontologyId }: { ontologyId: string }) {
  const [showCreate, setShowCreate] = useState(false)
  const [rollbackTarget, setRollbackTarget] = useState<any>(null)
  const [detailVersion, setDetailVersion] = useState<any>(null)
  const [page, setPage] = useState(1)
  const [searchText, setSearchText] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['versions', ontologyId, page],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/versions`, {
        headers: { Authorization: `Bearer ${getToken()}` },
        params: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
      })
      return res.data
    },
  })

  const { data: changeLogs } = useQuery({
    queryKey: ['change-logs', ontologyId],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/change-logs`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
  })

  const createMut = useMutation({
    mutationFn: (body: any) => axios.post(`/api/v2/ontologies/${ontologyId}/versions`, body, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['versions', ontologyId] }); setShowCreate(false) },
  })

  const rollbackMut = useMutation({
    mutationFn: (vid: string) => axios.post(`/api/v2/ontologies/${ontologyId}/versions/${vid}/rollback`, {}, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['versions', ontologyId] }); qc.invalidateQueries({ queryKey: ['ontology', ontologyId] }); setRollbackTarget(null) },
  })

  const allVersions = data?.data || []
  const total = data?.total || 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const logs = changeLogs?.data || []

  const filteredVersions = useMemo(() => {
    if (!searchQuery.trim()) return allVersions
    const q = searchQuery.trim().toLowerCase()
    return allVersions.filter((v: any) =>
      (v.version_number || '').toLowerCase().includes(q) ||
      (v.version_label || '').toLowerCase().includes(q) ||
      (v.description || '').toLowerCase().includes(q)
    )
  }, [allVersions, searchQuery])

  const hasData = !isLoading && allVersions.length > 0

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 shrink-0 pb-3">
        <div className="flex items-center gap-2 flex-1">
          <div className="relative flex-1 max-w-xs">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] pointer-events-none" />
            <input
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { setSearchQuery(searchText); setPage(1) } }}
              placeholder="搜索版本号 / 标签…"
              className="w-full border rounded-lg pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
            />
          </div>
          <Button onClick={() => { setSearchQuery(searchText); setPage(1) }} size="sm"><Search size={14} /> 确认</Button>
        </div>
        <Button onClick={() => setShowCreate(true)} size="sm"><Plus size={14} /> 新建版本</Button>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : !hasData && !searchQuery ? (
        <EmptyState title="暂无版本" description="发布您的第一个版本" action={<Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} /> 发布</Button>} />
      ) : (
        <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--color-border)] min-h-0">
          <Table>
            <TableHeader className="sticky top-0 bg-gray-50 z-10">
              <TableRow>
                <TableHead>版本号</TableHead>
                <TableHead>标签</TableHead>
                <TableHead>变更</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredVersions.length === 0 ? (
                <tr><td colSpan={5} className="text-center text-gray-400 py-8 text-sm">无匹配的版本记录</td></tr>
              ) : (
                filteredVersions.map((v: any) => (
                  <TableRow key={v.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <GitBranch size={14} className="text-[var(--color-info)]" />
                        <span className="font-mono font-medium">{v.version_number}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-[var(--color-text-secondary)]">{v.version_label || '-'}</TableCell>
                    <TableCell>
                      <div className="flex gap-2 text-xs">
                        {v.change_summary?.added > 0 && <Badge variant="success">+{v.change_summary.added}</Badge>}
                        {v.change_summary?.modified > 0 && <Badge variant="warning">~{v.change_summary.modified}</Badge>}
                        {v.change_summary?.deleted > 0 && <Badge variant="danger">-{v.change_summary.deleted}</Badge>}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-[var(--color-text-tertiary)]">
                      {v.created_at ? new Date(v.created_at).toLocaleString('zh-CN') : '-'}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Button variant="ghost" size="sm" onClick={() => setDetailVersion(v)}>详情</Button>
                        <Button variant="ghost" size="sm" onClick={() => setRollbackTarget(v)}>
                          <RotateCcw size={12} className="text-[var(--color-warning)]" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}

      {total > 0 && (
        <div className="flex items-center justify-between text-sm shrink-0 pt-3">
          <span className="text-[var(--color-text-tertiary)] text-xs">
            共 {total} 条，第 {page}/{totalPages} 页
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="p-1.5 rounded-lg text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft size={15} />
            </button>
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              const pn = start + i
              if (pn > totalPages) return null
              return (
                <button
                  key={pn}
                  onClick={() => setPage(pn)}
                  className={`w-7 h-7 rounded-lg text-xs font-medium transition-colors ${
                    pn === page
                      ? 'bg-[var(--color-primary)] text-white'
                      : 'text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)]'
                  }`}
                >
                  {pn}
                </button>
              )
            })}
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="p-1.5 rounded-lg text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}

      {logs.length > 0 && (
        <Card className="p-4 mt-3 shrink-0">
          <h4 className="text-sm font-medium mb-3">变更日志</h4>
          <div className="space-y-2 max-h-60 overflow-auto">
            {logs.map((log: any) => (
              <div key={log.id} className="flex items-center gap-3 text-xs py-1 border-b border-[var(--color-border)] last:border-0">
                <Badge variant={log.action === 'create' ? 'success' : log.action === 'delete' ? 'danger' : 'warning'} className="text-[10px]">
                  {log.action}
                </Badge>
                <span className="text-[var(--color-text-secondary)]">{log.object_type}</span>
                <span className="font-medium">{log.object_name || log.object_id?.slice(0, 8)}</span>
                <span className="text-[var(--color-text-tertiary)] ml-auto">{log.created_by_name}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {showCreate && (
        <Modal open={showCreate} onClose={() => setShowCreate(false)} title="发布新版本" size="sm"
          footer={<><Button variant="ghost" onClick={() => setShowCreate(false)}>取消</Button><Button onClick={() => createMut.mutate({})} loading={createMut.isPending}>发布</Button></>}>
          <p className="text-sm text-[var(--color-text-secondary)]">此操作将当前本体的全部内容快照为一个新版本。确定继续？</p>
        </Modal>
      )}

      <ConfirmModal open={!!rollbackTarget} onClose={() => setRollbackTarget(null)}
        onConfirm={() => rollbackTarget && rollbackMut.mutate(rollbackTarget.id)}
        title="确认回滚" variant="danger"
        description={rollbackTarget ? `回滚到 ${rollbackTarget.version_number}，当前未发布的更改将丢失。` : ''}
        loading={rollbackMut.isPending} />

      {detailVersion && (
        <Modal open={!!detailVersion} onClose={() => setDetailVersion(null)} title={`版本 ${detailVersion.version_number}`} size="md"
          footer={<Button onClick={() => setDetailVersion(null)}>关闭</Button>}>
          <div className="space-y-3 text-sm">
            <p><span className="text-[var(--color-text-tertiary)]">标签:</span> {detailVersion.version_label || '-'}</p>
            <p><span className="text-[var(--color-text-tertiary)]">描述:</span> {detailVersion.description || '-'}</p>
            <div className="flex gap-2">
              <Badge variant="success">+{detailVersion.change_summary?.added || 0}</Badge>
              <Badge variant="warning">~{detailVersion.change_summary?.modified || 0}</Badge>
              <Badge variant="danger">-{detailVersion.change_summary?.deleted || 0}</Badge>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
