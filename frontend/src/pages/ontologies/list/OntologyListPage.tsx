import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { domainApi, ontologyApi } from '@/api/ontologies'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal, Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import type { OntologyListItem } from '@/types/ontology'
import type { LucideIcon } from 'lucide-react'
import {
  Boxes,
  AlertCircle,
  Building2,
  Cpu,
  Database,
  Factory,
  GraduationCap,
  HeartPulse,
  Landmark,
  Network,
  Pencil,
  Plus,
  Scale,
  Search,
  ShieldCheck,
  ShoppingCart,
  Trash2,
  Users,
  X,
  Zap,
} from 'lucide-react'

interface DomainItem {
  id: string
  name: string
  description: string
}

interface OntologyFormValue {
  name: string
  domain: string
  description: string
  icon: string
}

interface IconOption {
  key: string
  label: string
  icon: LucideIcon
  tone: string
}

const ICON_OPTIONS: IconOption[] = [
  { key: 'network', label: '通用本体', icon: Network, tone: 'bg-teal-50 text-teal-600' },
  { key: 'boxes', label: '产品知识', icon: Boxes, tone: 'bg-blue-50 text-blue-600' },
  { key: 'shopping-cart', label: '采购电商', icon: ShoppingCart, tone: 'bg-emerald-50 text-emerald-600' },
  { key: 'factory', label: '生产制造', icon: Factory, tone: 'bg-orange-50 text-orange-600' },
  { key: 'heart-pulse', label: '医疗健康', icon: HeartPulse, tone: 'bg-rose-50 text-rose-600' },
  { key: 'landmark', label: '金融财务', icon: Landmark, tone: 'bg-amber-50 text-amber-600' },
  { key: 'scale', label: '法律合规', icon: Scale, tone: 'bg-violet-50 text-violet-600' },
  { key: 'graduation-cap', label: '教育培训', icon: GraduationCap, tone: 'bg-indigo-50 text-indigo-600' },
  { key: 'cpu', label: '科技研发', icon: Cpu, tone: 'bg-cyan-50 text-cyan-600' },
  { key: 'zap', label: '能源动力', icon: Zap, tone: 'bg-yellow-50 text-yellow-600' },
  { key: 'shield-check', label: '风控安全', icon: ShieldCheck, tone: 'bg-red-50 text-red-600' },
  { key: 'users', label: '组织客户', icon: Users, tone: 'bg-sky-50 text-sky-600' },
  { key: 'database', label: '数据资产', icon: Database, tone: 'bg-purple-50 text-purple-600' },
  { key: 'building-2', label: '企业架构', icon: Building2, tone: 'bg-lime-50 text-lime-700' },
]

const DEFAULT_ICON = ICON_OPTIONS[0]

function iconOption(key?: string) {
  return ICON_OPTIONS.find(option => option.key === key) ?? DEFAULT_ICON
}

function errorMessage(error: unknown, fallback: string) {
  if (!error || typeof error !== 'object') return fallback
  const candidate = error as { detail?: unknown; message?: unknown }
  if (typeof candidate.detail === 'string') return candidate.detail
  if (Array.isArray(candidate.detail)) {
    const first = candidate.detail[0] as { msg?: unknown } | undefined
    if (typeof first?.msg === 'string') return `导入文件格式不正确：${first.msg}`
  }
  if (candidate.detail && typeof candidate.detail === 'object') {
    const detail = candidate.detail as { message?: unknown }
    if (typeof detail.message === 'string') return detail.message
  }
  if (typeof candidate.message === 'string') return candidate.message
  return fallback
}

function formatChangedAt(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

function OntologyAvatar({ icon, size = 'md' }: { icon?: string; size?: 'md' | 'lg' }) {
  const option = iconOption(icon)
  const Icon = option.icon
  return (
    <div
      className={`${size === 'lg' ? 'h-14 w-14 rounded-2xl' : 'h-11 w-11 rounded-xl'} ${option.tone} flex shrink-0 items-center justify-center`}
      aria-hidden="true"
    >
      <Icon size={size === 'lg' ? 26 : 21} strokeWidth={1.8} />
    </div>
  )
}

function OntologyFormModal({
  open,
  title,
  submitText,
  domains,
  initial,
  onClose,
  onSubmit,
  onManageDomains,
}: {
  open: boolean
  title: string
  submitText: string
  domains: DomainItem[]
  initial?: OntologyListItem | null
  onClose: () => void
  onSubmit: (value: OntologyFormValue) => Promise<unknown>
  onManageDomains: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [domain, setDomain] = useState(initial?.domain ?? domains[0]?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [icon, setIcon] = useState(initial?.icon ?? DEFAULT_ICON.key)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const initialDomain = initial?.domain
  const availableDomains = initialDomain && !domains.some(item => item.name === initialDomain)
    ? [{ id: `legacy-${initialDomain}`, name: initialDomain, description: '' }, ...domains]
    : domains
  const selectedDomain = domain || availableDomains[0]?.name || ''

  const submit = async () => {
    if (!name.trim()) {
      setError('请输入本体名称')
      return
    }
    if (!selectedDomain) {
      setError('请选择所属领域')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSubmit({
        name: name.trim(),
        domain: selectedDomain,
        description: description.trim(),
        icon,
      })
    } catch (submitError) {
      setError(errorMessage(submitError, `${submitText}失败，请稍后重试`))
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
        ? '更新本体的名称、领域与说明，不会影响已经维护的结构和版本。'
        : '填写基本信息后即可使用，后续可在详情页维护结构与版本。'}
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
            disabled={!name.trim() || !selectedDomain}
            className="bg-teal-600 text-white hover:bg-teal-700 active:bg-teal-800"
          >
            {submitText}
          </Button>
        </>
      )}
    >
      <div className="space-y-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="本体名称"
            required
            autoFocus
            maxLength={200}
            value={name}
            onChange={event => setName(event.target.value)}
            placeholder="例如：供应链知识本体"
            className="selection:bg-teal-200 selection:text-teal-950 focus:border-teal-500 focus:ring-teal-500/30"
          />
          <div>
            <label className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
              所属领域<span className="ml-0.5 text-[var(--color-danger)]">*</span>
            </label>
            {availableDomains.length > 0 ? (
              <select
                value={selectedDomain}
                onChange={event => setDomain(event.target.value)}
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-white px-3 text-sm text-[var(--color-text-primary)] focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500/30"
              >
                {availableDomains.map(item => <option key={item.id} value={item.name}>{item.name}</option>)}
              </select>
            ) : (
              <button
                type="button"
                onClick={onManageDomains}
                className="flex h-9 w-full items-center justify-between rounded-md border border-dashed border-amber-300 bg-amber-50 px-3 text-sm text-amber-700"
              >
                暂无可用领域 <span className="font-medium">前往设置</span>
              </button>
            )}
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">本体描述</label>
          <textarea
            value={description}
            onChange={event => setDescription(event.target.value)}
            maxLength={500}
            rows={3}
            placeholder="简要说明本体覆盖的业务范围和用途"
            className="w-full resize-none rounded-md border border-[var(--color-border)] bg-white px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] selection:bg-teal-200 selection:text-teal-950 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500/30"
          />
          <p className="mt-1 text-right text-[11px] text-[var(--color-text-tertiary)]">{description.length}/500</p>
        </div>

        <fieldset>
          <legend className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">本体图标</legend>
          <div className="grid grid-cols-7 gap-2 max-sm:grid-cols-5">
            {ICON_OPTIONS.map(option => {
              const Icon = option.icon
              const selected = icon === option.key
              return (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setIcon(option.key)}
                  title={option.label}
                  aria-label={option.label}
                  aria-pressed={selected}
                  className={`flex aspect-square items-center justify-center rounded-xl border transition-all ${
                    selected
                      ? 'border-teal-500 bg-teal-50 text-teal-700 shadow-sm ring-2 ring-teal-100'
                      : 'border-slate-200 bg-white text-slate-500 hover:border-teal-300 hover:bg-slate-50'
                  }`}
                >
                  <Icon size={20} strokeWidth={1.8} />
                </button>
              )
            })}
          </div>
          <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">所选图标将作为本体头像，可随时编辑。</p>
        </fieldset>

        {error && (
          <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-red-100 bg-red-50/70 px-3.5 py-3 text-sm text-red-700">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span className="leading-5">{error}</span>
          </div>
        )}
      </div>
    </Modal>
  )
}

function CreateOntologyCard({
  onCreate,
  onImport,
  importing,
}: {
  onCreate: () => void
  onImport: () => void
  importing: boolean
}) {
  return (
    <article className="group flex min-h-[256px] flex-col items-center justify-center rounded-2xl border border-dashed border-teal-300 bg-gradient-to-br from-teal-50/80 via-white to-cyan-50/60 p-6 text-center transition-all hover:-translate-y-0.5 hover:border-teal-500 hover:shadow-lg">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-teal-600 text-white shadow-md shadow-teal-600/20 transition-transform group-hover:scale-105">
        <Plus size={25} />
      </div>
      <h3 className="text-base font-semibold text-slate-800">新建本体</h3>
      <p className="mt-2 max-w-[210px] text-xs leading-5 text-slate-500">快速创建本体模型</p>
      <div className="mt-5 flex items-center justify-center gap-2">
        <button
          type="button"
          onClick={onCreate}
          className="rounded-lg border border-teal-200 bg-white px-3 py-1.5 text-xs font-medium text-teal-700 shadow-sm transition-colors hover:border-teal-300 hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
        >
          立即创建
        </button>
        <button
          type="button"
          disabled={importing}
          onClick={onImport}
          className="rounded-lg border border-teal-200 bg-white px-3 py-1.5 text-xs font-medium text-teal-700 shadow-sm transition-colors hover:border-teal-300 hover:bg-teal-50 disabled:cursor-wait disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
          aria-busy={importing}
        >
          {importing ? '正在导入' : '本地导入'}
        </button>
      </div>
    </article>
  )
}

function OntologyCard({
  item,
  onEdit,
  onDetail,
  onView,
  onDelete,
}: {
  item: OntologyListItem
  onEdit: () => void
  onDetail: () => void
  onView: () => void
  onDelete: () => void
}) {
  return (
    <article className="group flex min-h-[256px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white transition-all duration-200 hover:-translate-y-0.5 hover:border-teal-200 hover:shadow-lg">
      <div className="flex flex-col p-4 pb-2.5">
        <div className="flex min-h-11 items-start gap-3 overflow-hidden">
          <OntologyAvatar icon={item.icon} />
          <div className="flex min-h-11 min-w-0 flex-1 flex-col justify-center overflow-hidden">
            <div className="flex min-w-0 items-center overflow-hidden">
              <button
                type="button"
                onClick={onDetail}
                className="min-w-0 flex-1 truncate text-left text-[15px] font-semibold leading-5 text-slate-800 transition-colors hover:text-teal-700"
                title={item.name}
              >
                {item.name}
              </button>
            </div>
            <div className="mt-1 flex min-w-0 items-center gap-1.5">
              <span className="inline-flex min-w-0 max-w-full truncate rounded-md border border-teal-100 bg-teal-50 px-2 py-0.5 text-[11px] font-medium leading-4 text-teal-700">
                {item.domain || '未设置领域'}
              </span>
              <span className="inline-flex shrink-0 rounded-md border border-violet-100 bg-violet-50 px-2 py-0.5 font-mono text-[11px] font-medium leading-4 text-violet-600">
                {item.current_release_version || item.version || 'v0'}
              </span>
            </div>
          </div>
        </div>

        <p
          className="mt-4 min-h-[44px] text-sm leading-[22px] text-slate-500"
          style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
          title={item.description || '暂无描述'}
        >
          {item.description || '暂无描述'}
        </p>

        <div className="mt-3 grid grid-cols-4 gap-1.5">
          {[
            { label: '对象实体', value: item.entity_count ?? 0 },
            { label: '实体关系', value: item.relation_count ?? 0 },
            { label: '执行动作', value: item.action_count ?? 0 },
            { label: '哨兵引擎', value: item.sentinel_count ?? 0 },
          ].map(metric => (
            <div key={metric.label} className="min-w-0 rounded-xl bg-slate-50 px-0.5 py-2.5 text-center">
              <p className="whitespace-nowrap text-[10px] font-medium text-slate-400">{metric.label}</p>
              <p className="mt-0.5 text-lg font-semibold tabular-nums text-slate-800">{metric.value}</p>
            </div>
          ))}
        </div>
      </div>

      <footer className="mt-auto flex min-h-11 items-center gap-0.5 border-t border-slate-100 px-4 py-1.5">
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap rounded-lg bg-slate-100 px-1.5 py-1.5 text-[11px] font-medium text-slate-700 transition-colors hover:bg-teal-50 hover:text-teal-700"
        >
          <Pencil size={12} /> 编辑
        </button>
        <button
          type="button"
          onClick={onView}
          className="inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap rounded-lg bg-slate-100 px-1.5 py-1.5 text-[11px] font-medium text-slate-700 transition-colors hover:bg-teal-50 hover:text-teal-700"
        >
          <Search size={12} /> 查看
        </button>
        <span className="ml-auto shrink-0 whitespace-nowrap text-[11px] tabular-nums text-slate-400" title={`创建时间：${new Date(item.created_at).toLocaleString('zh-CN')}`}>
          {formatChangedAt(item.created_at)}
        </span>
        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-red-50 hover:text-red-500"
          title="删除本体"
          aria-label={`删除本体 ${item.name}`}
        >
          <Trash2 size={14} />
        </button>
      </footer>
    </article>
  )
}

export default function OntologyListPage({ defaultCreateOpen = false }: { defaultCreateOpen?: boolean }) {
  const [nameFilter, setNameFilter] = useState('')
  const [domainFilter, setDomainFilter] = useState('')
  const [createOpen, setCreateOpen] = useState(defaultCreateOpen)
  const [editTarget, setEditTarget] = useState<OntologyListItem | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<OntologyListItem | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { toast } = useToast()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['ontologies'],
    queryFn: () => ontologyApi.list({ page_size: 1000 }),
  })
  const { data: configuredDomains = [] } = useQuery<DomainItem[]>({
    queryKey: ['domains'],
    queryFn: () => domainApi.list(),
  })

  const allItems = useMemo(() => data?.items ?? [], [data?.items])
  const domains = useMemo(() => {
    const byName = new Map<string, DomainItem>()
    configuredDomains.forEach(item => byName.set(item.name, item))
    // Historical ontology domains remain filterable even if a setting was
    // later renamed or removed.
    allItems.forEach(item => {
      if (item.domain && !byName.has(item.domain)) {
        byName.set(item.domain, { id: `legacy-${item.domain}`, name: item.domain, description: '' })
      }
    })
    return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  }, [allItems, configuredDomains])

  const filteredItems = useMemo(() => {
    const keyword = nameFilter.trim().toLocaleLowerCase('zh-CN')
    return [...allItems]
      .filter(item => !keyword || item.name.toLocaleLowerCase('zh-CN').includes(keyword))
      .filter(item => !domainFilter || item.domain === domainFilter)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  }, [allItems, domainFilter, nameFilter])

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['ontologies'] })
    queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  const createMutation = useMutation({
    mutationFn: (value: OntologyFormValue) => ontologyApi.create(value),
    onSuccess: () => {
      refresh()
      setCreateOpen(false)
      toast({ tone: 'success', title: '本体已创建', description: '现在可以继续维护对象实体、实体关系与版本。' })
      if (defaultCreateOpen) navigate('/ontologies', { replace: true })
    },
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: OntologyFormValue }) => ontologyApi.update(id, value),
    onSuccess: () => {
      refresh()
      setEditTarget(null)
      toast({ tone: 'success', title: '本体信息已更新' })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => ontologyApi.delete(id),
    onSuccess: () => {
      refresh()
      setDeleteTarget(null)
      toast({ tone: 'success', title: '本体已删除', description: '相关结构、映射与版本数据已一并移除。' })
    },
    onError: error => {
      toast({ tone: 'error', title: '本体删除失败', description: errorMessage(error, '请稍后重试。') })
    },
  })
  const importMutation = useMutation({
    mutationFn: (body: unknown) => ontologyApi.importStructure(body),
    onSuccess: result => {
      refresh()
      toast({ tone: 'success', title: '本体导入完成', description: `已导入「${result.ontology.name}」，即将打开详情。` })
      navigate(`/ontologies/${result.ontology.id}`)
    },
  })

  const handleImportFile = async (file?: File) => {
    if (!file) return
    if (!file.name.toLocaleLowerCase().endsWith('.json')) {
      toast({ tone: 'error', title: '无法导入本体', description: '请选择 JSON 格式的本体结构文件。' })
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      toast({ tone: 'error', title: '文件超过大小限制', description: '本体结构文件不能超过 5 MB。' })
      return
    }
    try {
      const text = await file.text()
      let body: unknown
      try {
        body = JSON.parse(text)
      } catch {
        toast({ tone: 'error', title: '文件内容无法识别', description: '文件不是有效的 JSON，请重新选择本体结构导出文件。' })
        return
      }
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        throw new Error('文件根节点必须是 JSON 对象')
      }
      await importMutation.mutateAsync(body)
    } catch (error: unknown) {
      toast({
        tone: 'error',
        title: '本体导入失败',
        description: errorMessage(error, '请确认文件来自本体结构 JSON 导出。'),
      })
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const openCreate = () => setCreateOpen(true)
  const closeCreate = () => {
    setCreateOpen(false)
    if (defaultCreateOpen) navigate('/ontologies', { replace: true })
  }

  return (
    <div className="min-h-full">
      <section className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50" aria-label="本体筛选">
        <div className="relative w-full sm:w-72">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={nameFilter}
            onChange={event => setNameFilter(event.target.value)}
            placeholder="搜索本体名称"
            aria-label="按本体名称筛选"
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
          value={domainFilter}
          onChange={event => setDomainFilter(event.target.value)}
          aria-label="按所属领域筛选"
          className="h-9 min-w-36 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
        >
          <option value="">全部领域</option>
          {domains.map(item => <option key={item.id} value={item.name}>{item.name}</option>)}
        </select>
        {(nameFilter || domainFilter) && (
          <button
            type="button"
            onClick={() => { setNameFilter(''); setDomainFilter('') }}
            className="inline-flex h-9 items-center gap-1 rounded-lg px-2.5 text-xs text-slate-400 hover:bg-slate-50 hover:text-slate-600"
          >
            <X size={13} /> 清除筛选
          </button>
        )}
        <button
          type="button"
          onClick={openCreate}
          className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-lg bg-[var(--color-nav-bg)] px-4 text-sm font-medium text-white shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:opacity-90 active:translate-y-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
        >
          <Plus size={15} /> 立即创建
        </button>
      </section>

      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        className="sr-only"
        aria-label="选择本体结构 JSON 文件"
        onChange={event => void handleImportFile(event.target.files?.[0])}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <CreateOntologyCard
          onCreate={openCreate}
          onImport={() => {
            if (fileInputRef.current) fileInputRef.current.value = ''
            fileInputRef.current?.click()
          }}
          importing={importMutation.isPending}
        />

        {isLoading ? (
          <div className="flex min-h-[300px] items-center justify-center rounded-2xl border border-slate-200 bg-white sm:col-span-1 lg:col-span-2 xl:col-span-3">
            <LoadingState message="加载本体列表..." />
          </div>
        ) : isError ? (
          <div className="flex min-h-[300px] items-center justify-center rounded-2xl border border-red-100 bg-red-50 px-6 text-center text-sm text-red-600 sm:col-span-1 lg:col-span-2 xl:col-span-3">
            本体列表加载失败，请刷新页面后重试。
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="flex min-h-[300px] flex-col items-center justify-center rounded-2xl border border-slate-200 bg-white px-6 text-center sm:col-span-1 lg:col-span-2 xl:col-span-3">
            <Network size={28} className="text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-500">{nameFilter || domainFilter ? '没有符合条件的本体' : '还没有创建本体'}</p>
            <p className="mt-1 text-xs text-slate-400">{nameFilter || domainFilter ? '请调整名称或领域筛选条件' : '点击左侧卡片创建第一个本体'}</p>
          </div>
        ) : (
          filteredItems.map(item => (
            <OntologyCard
              key={item.id}
              item={item}
              onEdit={() => setEditTarget(item)}
              onDetail={() => navigate(`/ontologies/${item.id}`)}
              onView={() => navigate(`/ontologies/${item.id}`)}
              onDelete={() => setDeleteTarget(item)}
            />
          ))
        )}
      </div>

      <div className="mt-5 flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-teal-50 text-teal-600" aria-hidden="true">
          <Network size={14} strokeWidth={1.8} />
        </span>
        <p className="text-xs font-medium tracking-wide text-slate-500">
          让业务知识形成统一语言、让数据治理不再成为孤岛
        </p>
      </div>

      {createOpen && (
        <OntologyFormModal
          open
          title="新建本体"
          submitText="创建本体"
          domains={configuredDomains}
          onClose={closeCreate}
          onSubmit={value => createMutation.mutateAsync(value)}
          onManageDomains={() => navigate('/settings/domains')}
        />
      )}

      {editTarget && (
        <OntologyFormModal
          open
          title="编辑本体"
          submitText="保存修改"
          domains={configuredDomains}
          initial={editTarget}
          onClose={() => setEditTarget(null)}
          onSubmit={value => updateMutation.mutateAsync({ id: editTarget.id, value })}
          onManageDomains={() => navigate('/settings/domains')}
        />
      )}

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => { if (!deleteMutation.isPending) setDeleteTarget(null) }}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        title={deleteTarget ? `删除「${deleteTarget.name}」？` : '删除本体？'}
        description="本体结构、数据映射与版本记录将被永久移除。此操作无法撤销，请确认你不再需要这些内容。"
        confirmText="删除本体"
        variant="danger"
        loading={deleteMutation.isPending}
      />
    </div>
  )
}
