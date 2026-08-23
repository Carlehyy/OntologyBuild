/**
 * 三维场景管理页 — 卡片网格展示全部场景（参照本体管理页）。
 *
 * 草稿态负责场景生成、发布态对外生效；支持新增/编辑基本信息/
 * 快照克隆/删除。点击卡片进入详情页（三标签）。
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Boxes, Pencil, Plus, Trash2, Copy } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import type { SceneSummary, SceneStatus } from '@/types/scene'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { SceneFormModal } from '../components/SceneFormModal'

function formatUpdatedAt(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}

function StatusBadge({ status }: { status: SceneStatus }) {
  const published = status === 'published'
  return (
    <span
      className={
        'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ' +
        (published
          ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]'
          : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300')
      }
    >
      {published ? '已发布' : '草稿'}
    </span>
  )
}

export default function SceneListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [keyword, setKeyword] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<SceneSummary | null>(null)
  const [cloningId, setCloningId] = useState<string | null>(null)
  const [removingId, setRemovingId] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['scenes'],
    queryFn: () => scenesApi.list({ page_size: 200 }),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['scenes'] })

  const createMutation = useMutation({
    mutationFn: (value: { name: string; description: string }) =>
      scenesApi.create({ name: value.name, description: value.description }),
    onSuccess: () => { invalidate(); setCreateOpen(false); toast({ tone: 'success', title: '场景已创建（草稿态）' }) },
    onError: () => toast({ tone: 'error', title: '创建失败' }),
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: { name: string; description: string } }) =>
      scenesApi.updateBasicInfo(id, value),
    onSuccess: () => { invalidate(); setEditing(null); toast({ tone: 'success', title: '基本信息已更新' }) },
    onError: () => toast({ tone: 'error', title: '更新失败' }),
  })
  const cloneMutation = useMutation({
    mutationFn: (id: string) => scenesApi.clone(id),
    onSuccess: created => {
      invalidate(); setCloningId(null)
      toast({ tone: 'success', title: '已克隆为新的草稿态场景' })
      navigate('/scenes/' + created.id)
    },
    onError: () => { setCloningId(null); toast({ tone: 'error', title: '克隆失败' }) },
  })
  const removeMutation = useMutation({
    mutationFn: (id: string) => scenesApi.remove(id),
    onSuccess: () => { invalidate(); setRemovingId(null); toast({ tone: 'success', title: '场景已删除' }) },
    onError: () => { setRemovingId(null); toast({ tone: 'error', title: '删除失败' }) },
  })

  const items = listQuery.data?.items ?? []
  const filtered = useMemo(
    () => items.filter(item => item.name.toLowerCase().includes(keyword.trim().toLowerCase())),
    [items, keyword],
  )
  const removing = items.find(item => item.id === removingId) ?? null

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">三维场景</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            白模三维场景管理与建模：草稿态负责场景生成，发布态正式投入使用
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-56">
            <Input
              value={keyword}
              onChange={event => setKeyword(event.target.value)}
              placeholder="搜索场景名称"
              aria-label="搜索场景名称"
            />
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus size={16} /> 新建场景
          </Button>
        </div>
      </div>

      {listQuery.isLoading ? (
        <LoadingState />
      ) : listQuery.isError ? (
        <EmptyState title="场景列表加载失败" description="请稍后重试" />
      ) : filtered.length === 0 ? (
        <EmptyState
          title={keyword ? '没有匹配的场景' : '还没有任何三维场景'}
          description={keyword ? '换个关键词试试' : '新建一个场景，或通过场景助手对话生成'}
          action={!keyword
            ? <Button onClick={() => setCreateOpen(true)}>新建场景</Button>
            : undefined}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map(scene => (
            <article
              key={scene.id}
              className="group flex cursor-pointer flex-col rounded-xl border border-[var(--color-border)] bg-card p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:border-teal-400 hover:shadow-md"
              onClick={() => navigate('/scenes/' + scene.id)}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300">
                    <Boxes size={18} />
                  </span>
                  <div>
                    <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{scene.name}</h3>
                    <StatusBadge status={scene.status} />
                  </div>
                </div>
              </div>
              <p className="mt-2 line-clamp-2 min-h-[32px] text-xs leading-5 text-[var(--color-text-secondary)]">
                {scene.description || '暂无描述'}
              </p>
              <p className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
                当前 v{scene.current_version_no}
                {scene.published_version_no != null ? ' · 已发布 v' + scene.published_version_no : ' · 未发布'}
                {' · 更新于 '}{formatUpdatedAt(scene.updated_at)}
              </p>
              <div className="mt-3 flex items-center gap-1.5 border-t border-[var(--color-border)] pt-2.5" onClick={event => event.stopPropagation()}>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 hover:text-teal-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  onClick={() => setEditing(scene)}
                >
                  <Pencil size={13} /> 编辑
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 hover:text-teal-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  disabled={cloneMutation.isPending && cloneMutation.variables === scene.id}
                  onClick={() => setCloningId(scene.id)}
                >
                  <Copy size={13} /> 克隆
                </button>
                <button
                  type="button"
                  className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950"
                  onClick={() => setRemovingId(scene.id)}
                >
                  <Trash2 size={13} /> 删除
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      <SceneFormModal
        open={createOpen}
        title="新建场景"
        onClose={() => setCreateOpen(false)}
        onSubmit={async value => { await createMutation.mutateAsync(value) }}
      />
      <SceneFormModal
        open={editing != null}
        title="编辑场景信息"
        initial={editing ? { name: editing.name, description: editing.description } : null}
        onClose={() => setEditing(null)}
        onSubmit={async value => {
          if (!editing) return
          await updateMutation.mutateAsync({ id: editing.id, value })
        }}
      />
      <ConfirmModal
        open={cloningId != null}
        onClose={() => setCloningId(null)}
        onConfirm={() => { if (cloningId) cloneMutation.mutate(cloningId) }}
        title="克隆场景"
        description={'将克隆当前生效定义生成一个全新的草稿态场景（名称自动加“-副本”），版本历史从 v1 开始。原场景不受影响。'}
        confirmText="确认克隆"
        loading={cloneMutation.isPending}
      />
      <ConfirmModal
        open={removingId != null}
        onClose={() => setRemovingId(null)}
        onConfirm={() => { if (removingId) removeMutation.mutate(removingId) }}
        title="删除场景"
        variant="danger"
        description={'确定删除「' + (removing?.name ?? '') + '」吗？其全部版本定义与运行日志将一并删除，操作不可恢复。'}
        confirmText="确认删除"
        loading={removeMutation.isPending}
      />
    </div>
  )
}
