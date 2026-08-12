import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Boxes,
  Code2,
  Orbit,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import {
  apiError,
  worldModelApi,
  type EngineType,
  type WorldModelProjectSummary,
} from '@/api/worldModel'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal, Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'

export const ENGINE_TYPE_OPTIONS: { value: EngineType; label: string; hint: string }[] = [
  { value: 'statistical', label: '统计预测', hint: '基于历史数据的统计/机器学习方法' },
  { value: 'mechanistic', label: '机理仿真', hint: '基于物理定律或业务机理的仿真' },
  { value: 'state_machine', label: '状态机推演', hint: '基于规则与离散状态转移' },
  { value: 'learned', label: '学习型动力学', hint: '从交互数据学习状态转移规律' },
]

export function engineTypeLabel(value: string): string {
  return ENGINE_TYPE_OPTIONS.find(item => item.value === value)?.label ?? value
}

function formatChangedAt(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

interface ProjectFormValue {
  name: string
  description: string
  engine_type: EngineType
}

function ProjectFormModal({
  open,
  title,
  submitText,
  initial,
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  submitText: string
  initial?: WorldModelProjectSummary | null
  onClose: () => void
  onSubmit: (value: ProjectFormValue) => Promise<unknown>
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [engineType, setEngineType] = useState<EngineType>(initial?.engine_type ?? 'statistical')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!name.trim()) {
      setError('请输入模型名称')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSubmit({ name: name.trim(), description: description.trim(), engine_type: engineType })
    } catch (submitError) {
      setError(apiError(submitError))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !saving && onClose()}
      title={title}
      description={initial
        ? '更新模型的名称、引擎类型与说明，不会影响已保存的脚本和版本。'
        : '创建后即可进入开发页编写推演脚本，脚本需实现 simulate(context, actions, horizon) 入口函数。'}
      headerIcon={initial
        ? <Pencil size={18} className="text-teal-600" />
        : <Plus size={19} className="text-teal-600" />}
      size="lg"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>取消</Button>
          <Button
            onClick={submit}
            loading={saving}
            disabled={!name.trim()}
            className="bg-teal-600 text-white hover:bg-teal-700 active:bg-teal-800 disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none"
          >
            {submitText}
          </Button>
        </>
      )}
    >
      <div className="space-y-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="模型名称"
            required
            autoFocus
            maxLength={200}
            value={name}
            onChange={event => setName(event.target.value)}
            placeholder="例如：台区负荷短期推演"
            className="selection:bg-teal-200 selection:text-teal-950 focus:border-teal-500 focus:ring-teal-500/30"
          />
          <div>
            <label className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
              引擎类型<span className="ml-0.5 text-[var(--color-danger)]">*</span>
            </label>
            <select
              value={engineType}
              onChange={event => setEngineType(event.target.value as EngineType)}
              className="h-9 w-full rounded-md border border-[var(--color-border)] bg-white px-3 text-sm text-[var(--color-text-primary)] focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500/30"
            >
              {ENGINE_TYPE_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
              {ENGINE_TYPE_OPTIONS.find(item => item.value === engineType)?.hint}
            </p>
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">模型描述</label>
          <textarea
            value={description}
            onChange={event => setDescription(event.target.value)}
            maxLength={500}
            rows={3}
            placeholder="简要说明该模型推演的业务对象、时域与用途"
            className="w-full resize-none rounded-md border border-[var(--color-border)] bg-white px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] selection:bg-teal-200 selection:text-teal-950 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500/30"
          />
          <p className="mt-1 text-right text-[11px] text-[var(--color-text-tertiary)]">{description.length}/500</p>
        </div>

        {error && <p role="alert" className="text-sm text-[var(--color-danger)]">{error}</p>}
      </div>
    </Modal>
  )
}

function CreateProjectCard({ onCreate }: { onCreate: () => void }) {
  return (
    <button
      type="button"
      onClick={onCreate}
      className="group flex min-h-[190px] flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-slate-200 bg-white/60 px-6 text-center transition-all hover:border-teal-400 hover:bg-teal-50/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-50 text-teal-600 transition-colors group-hover:bg-teal-100">
        <Plus size={22} />
      </span>
      <span className="text-sm font-medium text-slate-600 group-hover:text-teal-700">新建推演模型</span>
      <span className="text-xs text-slate-400">以代码承载演化规律，调试通过后可保存版本</span>
    </button>
  )
}

function ProjectCard({
  item,
  onDevelop,
  onEdit,
  onDelete,
}: {
  item: WorldModelProjectSummary
  onDevelop: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <article className="flex min-h-[190px] flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-sm/50 transition-shadow hover:shadow-md">
      <header className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-600">
          <Orbit size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-[var(--color-text-primary)]" title={item.name}>
            {item.name}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className="inline-flex items-center rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
              {engineTypeLabel(item.engine_type)}
            </span>
            <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] ${
              item.status === 'published'
                ? 'bg-teal-50 text-teal-700'
                : 'bg-amber-50 text-amber-700'
            }`}>
              {item.status === 'published' ? '已发布' : '草稿'}
            </span>
          </div>
        </div>
      </header>

      <p className="mt-3 line-clamp-2 min-h-[2.5rem] text-xs leading-5 text-[var(--color-text-tertiary)]" title={item.description}>
        {item.description || '暂无描述'}
      </p>

      <footer className="mt-auto flex items-center justify-between pt-3 text-[11px] text-slate-400">
        <span>{item.version_count} 个版本 · 更新于 {formatChangedAt(item.updated_at)}</span>
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={onDevelop}
            className="inline-flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-md bg-teal-600 px-2.5 text-xs font-medium text-white transition-colors hover:bg-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
          >
            <Code2 size={13} /> 开发
          </button>
          <button
            type="button"
            onClick={onEdit}
            aria-label={`编辑 ${item.name}`}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
          >
            <Pencil size={13} />
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={`删除 ${item.name}`}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
          >
            <Trash2 size={13} />
          </button>
        </span>
      </footer>
    </article>
  )
}

export default function WorldModelListTab() {
  const [nameFilter, setNameFilter] = useState('')
  const [engineFilter, setEngineFilter] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<WorldModelProjectSummary | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<WorldModelProjectSummary | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { toast } = useToast()

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['world-model-projects'],
    queryFn: () => worldModelApi.listProjects({ size: 500 }),
  })

  const allItems = useMemo(() => data?.items ?? [], [data?.items])
  const filteredItems = useMemo(() => {
    const keyword = nameFilter.trim().toLocaleLowerCase('zh-CN')
    return allItems
      .filter(item => !keyword
        || `${item.name} ${item.description ?? ''}`.toLocaleLowerCase('zh-CN').includes(keyword))
      .filter(item => !engineFilter || item.engine_type === engineFilter)
  }, [allItems, engineFilter, nameFilter])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['world-model-projects'] })

  const createMutation = useMutation({
    mutationFn: (value: ProjectFormValue) => worldModelApi.createProject(value),
    onSuccess: project => {
      refresh()
      setCreateOpen(false)
      toast({ tone: 'success', title: '推演模型已创建', description: '即将进入开发页编写推演脚本。' })
      navigate(`/world-model/models/${project.id}/develop`)
    },
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: ProjectFormValue }) => worldModelApi.updateProject(id, value),
    onSuccess: () => {
      refresh()
      setEditTarget(null)
      toast({ tone: 'success', title: '模型信息已更新' })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => worldModelApi.deleteProject(id),
    onSuccess: () => {
      refresh()
      setDeleteTarget(null)
      toast({ tone: 'success', title: '推演模型已删除', description: '相关脚本与版本记录已一并移除。' })
    },
    onError: error => {
      toast({ tone: 'error', title: '删除失败', description: apiError(error) })
    },
  })

  return (
    <div>
      <section className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50" aria-label="推演模型筛选">
        <div className="relative w-full sm:w-72">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={nameFilter}
            onChange={event => setNameFilter(event.target.value)}
            placeholder="搜索模型名称或描述"
            aria-label="按模型名称或描述筛选"
            className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-8 text-sm text-slate-700 placeholder:text-slate-400 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
          />
          {nameFilter && (
            <button
              type="button"
              onClick={() => setNameFilter('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              aria-label="清除名称筛选"
            >
              <X size={13} />
            </button>
          )}
        </div>
        <select
          value={engineFilter}
          onChange={event => setEngineFilter(event.target.value)}
          aria-label="按引擎类型筛选"
          className="h-9 min-w-36 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
        >
          <option value="">全部引擎类型</option>
          {ENGINE_TYPE_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        {(nameFilter || engineFilter) && (
          <button
            type="button"
            onClick={() => { setNameFilter(''); setEngineFilter('') }}
            className="inline-flex h-9 items-center gap-1 rounded-lg px-2.5 text-xs text-slate-400 hover:bg-slate-50 hover:text-slate-600"
          >
            <X size={13} /> 清除筛选
          </button>
        )}
        <span className="ml-auto hidden text-xs tabular-nums text-slate-400 sm:inline" aria-live="polite">
          {nameFilter || engineFilter
            ? `共 ${filteredItems.length} / ${allItems.length} 个模型`
            : `共 ${allItems.length} 个模型`}
        </span>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-[var(--color-nav-bg)] px-4 text-sm font-medium text-white shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:opacity-90 active:translate-y-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
        >
          <Plus size={15} /> 立即创建
        </button>
      </section>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <CreateProjectCard onCreate={() => setCreateOpen(true)} />

        {isLoading ? (
          <div className="flex min-h-[300px] items-center justify-center rounded-2xl border border-slate-200 bg-white sm:col-span-1 lg:col-span-2 xl:col-span-3">
            <LoadingState message="加载推演模型列表..." />
          </div>
        ) : isError ? (
          <div className="flex min-h-[300px] flex-col items-center justify-center gap-3 rounded-2xl border border-red-100 bg-red-50 px-6 text-center sm:col-span-1 lg:col-span-2 xl:col-span-3" role="alert">
            <p className="text-sm text-red-600">推演模型列表加载失败，请检查网络连接后重试。</p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
            >
              重新加载
            </button>
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="flex min-h-[300px] flex-col items-center justify-center rounded-2xl border border-slate-200 bg-white px-6 text-center sm:col-span-1 lg:col-span-2 xl:col-span-3">
            <Boxes size={28} className="text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-500">{nameFilter || engineFilter ? '没有符合条件的推演模型' : '还没有创建推演模型'}</p>
            <p className="mt-1 text-xs text-slate-400">{nameFilter || engineFilter ? '请调整名称或引擎类型筛选条件' : '点击左侧卡片创建第一个推演模型'}</p>
          </div>
        ) : (
          filteredItems.map(item => (
            <ProjectCard
              key={item.id}
              item={item}
              onDevelop={() => navigate(`/world-model/models/${item.id}/develop`)}
              onEdit={() => setEditTarget(item)}
              onDelete={() => setDeleteTarget(item)}
            />
          ))
        )}
      </div>

      {createOpen && (
        <ProjectFormModal
          open
          title="新建推演模型"
          submitText="创建模型"
          onClose={() => setCreateOpen(false)}
          onSubmit={value => createMutation.mutateAsync(value)}
        />
      )}

      {editTarget && (
        <ProjectFormModal
          open
          title="编辑推演模型"
          submitText="保存修改"
          initial={editTarget}
          onClose={() => setEditTarget(null)}
          onSubmit={value => updateMutation.mutateAsync({ id: editTarget.id, value })}
        />
      )}

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => { if (!deleteMutation.isPending) setDeleteTarget(null) }}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        title={deleteTarget ? `删除「${deleteTarget.name}」？` : '删除推演模型？'}
        description="模型脚本与全部历史版本将被永久移除。此操作无法撤销，请确认你不再需要这些内容。"
        confirmText="删除模型"
        variant="danger"
        loading={deleteMutation.isPending}
      />
    </div>
  )
}
