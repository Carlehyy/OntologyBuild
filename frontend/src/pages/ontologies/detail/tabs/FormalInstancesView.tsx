import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  Box,
  Boxes,
  ChevronLeft,
  ChevronRight,
  Database,
  KeyRound,
  Link2,
  Loader2,
  RefreshCw,
  Search,
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'

interface ReleaseSummary {
  id: string
  version: string
  publishedAt?: string | null
}

interface SchemaProperty {
  id: string
  name: string
  displayName?: string
  type?: string
  required?: boolean
  source?: string
}

interface AssociatedDataset {
  id: string
  name: string
  kind?: string | null
  roles: string[]
  available: boolean
}

interface ObjectTypeNode {
  id: string
  name: string
  displayName?: string
  description?: string
  primaryKey?: string | null
  properties: SchemaProperty[]
  instanceCount: number
  associatedDatasets: AssociatedDataset[]
}

interface LinkTypeNode {
  id: string
  name: string
  displayName?: string
  description?: string
  sourceObjectTypeId: string
  targetObjectTypeId: string
  cardinality?: string
  properties: SchemaProperty[]
  instanceCount: number
  associatedDatasets: AssociatedDataset[]
}

interface InstanceCatalog {
  release: ReleaseSummary
  objectTypes: ObjectTypeNode[]
  linkTypes: LinkTypeNode[]
}

interface ObjectRow {
  id: string
  objectTypeId: string
  properties: Record<string, unknown>
  computed: Record<string, unknown>
  createdAt: string
  updatedAt: string
}

interface EndpointSummary {
  label: string
}

interface LinkRow {
  id: string
  linkTypeId: string
  sourceObjectId: string
  targetObjectId: string
  sourceObject?: EndpointSummary | null
  targetObject?: EndpointSummary | null
  properties: Record<string, unknown>
}

interface InstancePage<T> {
  release: ReleaseSummary
  items: T[]
  total: number
  page: number
  pageSize: number
}

interface DataColumn {
  name: string
  label: string
  type?: string
  primary?: boolean
  required?: boolean
  computed?: boolean
  runtime?: boolean
}

type Selection = { kind: 'object' | 'link'; id: string }

const CARDINALITY_LABEL: Record<string, string> = {
  'one-to-one': '一对一',
  'one-to-many': '一对多',
  'many-to-one': '多对一',
  'many-to-many': '多对多',
}

function errorMessage(error: unknown) {
  if (!error || typeof error !== 'object') return '数据加载失败，请稍后重试'
  const candidate = error as { detail?: unknown; message?: unknown }
  if (typeof candidate.detail === 'string') return candidate.detail
  if (candidate.detail && typeof candidate.detail === 'object' && 'message' in candidate.detail) {
    return String((candidate.detail as { message: unknown }).message)
  }
  return typeof candidate.message === 'string' ? candidate.message : '数据加载失败，请稍后重试'
}

function typeLabel(item: { name: string; displayName?: string }) {
  return item.displayName || item.name
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function columnsFor(
  properties: SchemaProperty[],
  primaryKey: string | null | undefined,
  rows: Array<ObjectRow | LinkRow>,
  kind: Selection['kind'],
) {
  const schemaColumns: DataColumn[] = properties.map(property => ({
    name: property.name,
    label: property.displayName || property.name,
    type: property.type,
    primary: kind === 'object' && (property.id === primaryKey || property.name === primaryKey),
    required: property.required,
    computed: property.source === 'computed',
  }))
  schemaColumns.sort((left, right) => {
    const weight = (column: DataColumn) => column.primary ? 0 : column.required ? 1 : column.computed ? 3 : 2
    return weight(left) - weight(right)
  })

  // Object tables are the published object schema rendered as data. Instance
  // JSON can still contain legacy projection keys, but those are not ontology
  // properties and must not silently become business columns.
  if (kind === 'object') return schemaColumns

  const schemaNames = new Set(schemaColumns.map(column => column.name))
  const runtimeColumns = new Map<string, DataColumn>()
  rows.forEach(row => {
    Object.keys(row.properties || {}).forEach(name => {
      if (!schemaNames.has(name)) {
        runtimeColumns.set(`stored:${name}`, { name, label: name, runtime: true })
      }
    })
  })
  return [...schemaColumns, ...Array.from(runtimeColumns.values())]
}

export default function FormalInstancesView({ ontologyId }: { ontologyId: string }) {
  const base = `/formal/ontologies/${ontologyId}/instance-browser`
  const [selection, setSelection] = useState<Selection | null>(null)
  const [treeKeyword, setTreeKeyword] = useState('')
  const [draftKeyword, setDraftKeyword] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const catalogQuery = useQuery<InstanceCatalog>({
    queryKey: ['instance-browser-catalog', ontologyId],
    queryFn: () => apiClientV2.get(`${base}/catalog`),
  })
  const catalog = catalogQuery.data

  useEffect(() => {
    if (!catalog) return
    const selectionExists = selection?.kind === 'object'
      ? catalog.objectTypes.some(item => item.id === selection.id)
      : selection?.kind === 'link'
        ? catalog.linkTypes.some(item => item.id === selection.id)
        : false
    if (selectionExists) return
    const firstObject = catalog.objectTypes.find(item => item.instanceCount > 0) || catalog.objectTypes[0]
    const firstLink = catalog.linkTypes.find(item => item.instanceCount > 0) || catalog.linkTypes[0]
    setSelection(firstObject
      ? { kind: 'object', id: firstObject.id }
      : firstLink ? { kind: 'link', id: firstLink.id } : null)
  }, [catalog, selection])

  const dataQuery = useQuery<InstancePage<ObjectRow | LinkRow>>({
    queryKey: ['instance-browser-page', ontologyId, selection?.kind, selection?.id, page, pageSize, keyword],
    enabled: Boolean(selection),
    placeholderData: (previousData, previousQuery) => {
      const previousKey = previousQuery?.queryKey
      return previousKey?.[2] === selection?.kind && previousKey?.[3] === selection?.id
        ? previousData
        : undefined
    },
    queryFn: () => {
      if (!selection) throw new Error('请先选择对象实体或实体关系')
      return apiClientV2.get(`${base}/${selection.kind === 'object' ? 'objects' : 'links'}`, {
        params: {
          [selection.kind === 'object' ? 'object_type_id' : 'link_type_id']: selection.id,
          page,
          page_size: pageSize,
          keyword: keyword || undefined,
        },
      })
    },
  })

  const activeObject = selection?.kind === 'object'
    ? catalog?.objectTypes.find(item => item.id === selection.id) || null
    : null
  const activeLink = selection?.kind === 'link'
    ? catalog?.linkTypes.find(item => item.id === selection.id) || null
    : null
  const selectedType = activeObject || activeLink
  const rows = dataQuery.data?.items || []
  const total = dataQuery.data?.total || 0
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const rangeStart = total ? (page - 1) * pageSize + 1 : 0
  const rangeEnd = Math.min(page * pageSize, total)

  const columns = useMemo(() => columnsFor(
    selectedType?.properties || [],
    activeObject?.primaryKey,
    rows,
    selection?.kind || 'object',
  ), [activeObject?.primaryKey, rows, selectedType?.properties, selection?.kind])

  const normalizedTreeKeyword = treeKeyword.trim().toLocaleLowerCase()
  const filteredObjects = (catalog?.objectTypes || []).filter(item =>
    `${item.displayName || ''} ${item.name}`.toLocaleLowerCase().includes(normalizedTreeKeyword))
  const filteredLinks = (catalog?.linkTypes || []).filter(item =>
    `${item.displayName || ''} ${item.name}`.toLocaleLowerCase().includes(normalizedTreeKeyword))

  const selectType = (next: Selection) => {
    setSelection(next)
    setPage(1)
    setDraftKeyword('')
    setKeyword('')
  }

  const applySearch = () => {
    setPage(1)
    setKeyword(draftKeyword.trim())
  }

  const clearSearch = () => {
    setDraftKeyword('')
    setKeyword('')
    setPage(1)
  }

  const refresh = async () => {
    await Promise.all([
      catalogQuery.refetch(),
      selection ? dataQuery.refetch() : Promise.resolve(),
    ])
  }

  if (catalogQuery.isLoading) {
    return (
      <div className="flex h-full min-h-[420px] items-center justify-center gap-2 text-sm text-slate-400">
        <Loader2 size={18} className="animate-spin text-teal-600" />
        正在读取当前发布版本的实例目录…
      </div>
    )
  }

  if (catalogQuery.isError) {
    return (
      <div className="flex h-full min-h-[420px] items-center justify-center p-8">
        <div className="max-w-md text-center">
          <AlertCircle size={30} className="mx-auto text-red-400" />
          <p className="mt-3 text-sm font-medium text-slate-800">实例目录加载失败</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">{errorMessage(catalogQuery.error)}</p>
          <button
            type="button"
            onClick={() => void catalogQuery.refetch()}
            className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-lg bg-teal-600 px-3 text-xs font-medium text-white hover:bg-teal-700"
          >
            <RefreshCw size={12} /> 重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(230px,280px)_minmax(0,1fr)] overflow-hidden bg-white">
      <aside className="flex min-h-0 flex-col border-r border-slate-200 bg-slate-50/70">
        <div className="shrink-0 border-b border-slate-200 px-4 py-3.5">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <Database size={15} className="text-teal-600" />
                实体模型目录
              </div>
            </div>
            <span className="rounded-md border border-teal-100 bg-teal-50 px-1.5 py-0.5 text-[10px] font-medium text-teal-700">
              已发布
            </span>
          </div>
          <label className="mt-3 flex h-8 items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 focus-within:border-teal-400 focus-within:ring-2 focus-within:ring-teal-100">
            <Search size={12} className="text-slate-400" />
            <input
              value={treeKeyword}
              onChange={event => setTreeKeyword(event.target.value)}
              placeholder="筛选实体或关系"
              className="min-w-0 flex-1 bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400"
            />
          </label>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto p-2.5" aria-label="实例类型目录">
          <TreeSection
            title="对象实体"
            count={catalog?.objectTypes.length || 0}
            icon={<Box size={13} />}
          >
            {filteredObjects.map(item => (
              <TypeTreeButton
                key={item.id}
                active={selection?.kind === 'object' && selection.id === item.id}
                label={typeLabel(item)}
                code={item.name}
                count={item.instanceCount}
                onClick={() => selectType({ kind: 'object', id: item.id })}
              />
            ))}
          </TreeSection>

          <TreeSection
            title="实体关系"
            count={catalog?.linkTypes.length || 0}
            icon={<Link2 size={13} />}
          >
            {filteredLinks.map(item => (
              <TypeTreeButton
                key={item.id}
                active={selection?.kind === 'link' && selection.id === item.id}
                label={`${objectTypeName(catalog, item.sourceObjectTypeId)} → ${objectTypeName(catalog, item.targetObjectTypeId)}`}
                code={typeLabel(item) === item.name ? item.name : `${typeLabel(item)} · ${item.name}`}
                count={item.instanceCount}
                onClick={() => selectType({ kind: 'link', id: item.id })}
              />
            ))}
          </TreeSection>

          {!filteredObjects.length && !filteredLinks.length && (
            <p className="px-3 py-8 text-center text-xs text-slate-400">没有匹配的类型</p>
          )}
        </nav>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
        <header className="shrink-0 border-b border-slate-200 bg-white px-5 py-3.5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold tracking-[-0.02em] text-slate-950">
                  {selectedType ? typeLabel(selectedType) : '实例数据'}
                </h2>
                {selectedType && (
                  <span className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-500">
                    {selectedType.name}
                  </span>
                )}
                <span className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${
                  selection?.kind === 'link'
                    ? 'bg-violet-50 text-violet-700'
                    : 'bg-blue-50 text-blue-700'
                }`}>
                  {selection?.kind === 'link' ? '关系数据集' : '对象数据集'}
                </span>
                {selectedType && (
                  <DatasetAssociationPopover
                    key={`${selection?.kind}:${selection?.id}`}
                    datasets={selectedType.associatedDatasets || []}
                  />
                )}
              </div>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">
                {activeLink
                  ? `${objectTypeName(catalog, activeLink.sourceObjectTypeId)} → ${objectTypeName(catalog, activeLink.targetObjectTypeId)} · ${CARDINALITY_LABEL[activeLink.cardinality || ''] || activeLink.cardinality || '关系'}`
                  : activeObject?.description || '当前发布对象类型对应的实时实例投影'}
              </p>
            </div>
          </div>
        </header>

        <form
          className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/50 px-5 py-2.5"
          onSubmit={event => {
            event.preventDefault()
            applySearch()
          }}
        >
          <label className="flex h-8 min-w-64 max-w-md flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 focus-within:border-teal-400 focus-within:ring-2 focus-within:ring-teal-100">
            <Search size={12} className="text-slate-400" />
            <input
              value={draftKeyword}
              onChange={event => setDraftKeyword(event.target.value)}
              placeholder={selection?.kind === 'link' ? '搜索关系端点或属性值' : '搜索外部 ID 或属性值'}
              className="min-w-0 flex-1 bg-transparent text-xs text-slate-700 outline-none placeholder:text-slate-400"
            />
          </label>
          <button
            type="submit"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-teal-600 px-3 text-xs font-medium text-white hover:bg-teal-700"
          >
            <Search size={12} /> 查询
          </button>
          {keyword && (
            <button
              type="button"
              onClick={clearSearch}
              className="h-8 rounded-lg px-2.5 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-800"
            >
              清除
            </button>
          )}
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={catalogQuery.isFetching || dataQuery.isFetching}
            className="ml-auto inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 active:translate-y-px disabled:cursor-wait disabled:opacity-50"
          >
            <RefreshCw size={12} className={catalogQuery.isFetching || dataQuery.isFetching ? 'animate-spin' : ''} />
            刷新
          </button>
        </form>

        {dataQuery.isError && (
          <div className="m-4 flex shrink-0 items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertCircle size={14} />
            <span className="flex-1">{errorMessage(dataQuery.error)}</span>
            <button type="button" onClick={() => void dataQuery.refetch()} className="font-medium hover:underline">重试</button>
          </div>
        )}

        <div className="relative min-h-0 flex-1 overflow-auto bg-white">
          {dataQuery.isLoading ? (
            <div className="flex h-full min-h-64 items-center justify-center gap-2 text-xs text-slate-400">
              <Loader2 size={16} className="animate-spin text-teal-600" /> 正在加载实例数据…
            </div>
          ) : !selectedType ? (
            <EmptyData icon={<Boxes size={28} />} title="当前发布版本还没有对象实体或实体关系" />
          ) : !rows.length && !dataQuery.isError ? (
            <EmptyData
              icon={selection?.kind === 'link' ? <Link2 size={28} /> : <Box size={28} />}
              title={keyword ? '没有匹配的实例数据' : '该类型还没有实例数据'}
              note={keyword ? '请调整查询条件后重试' : '这里展示数据映射、采集或动作写入后的当前态投影'}
            />
          ) : rows.length ? (
            <InstanceTable
              kind={selection?.kind || 'object'}
              rows={rows}
              columns={columns}
              catalog={catalog}
              linkType={activeLink}
            />
          ) : null}
        </div>

        <footer className="flex h-12 shrink-0 items-center justify-between border-t border-slate-200 bg-slate-50/70 px-5">
          <div className="flex items-center gap-3 text-[11px] text-slate-400">
            <span className="tabular-nums">{total ? `显示 ${rangeStart}–${rangeEnd} / ${total} 条` : '暂无记录'}</span>
            <span className="hidden text-slate-300 xl:inline">完整字段值可在表格内横向、纵向滚动查看</span>
          </div>
          <div className="flex items-center gap-2">
            <label className="mr-2 flex h-8 items-center gap-2 text-[11px] text-slate-500">
              每页
              <select
                value={pageSize}
                onChange={event => {
                  setPageSize(Number(event.target.value))
                  setPage(1)
                }}
                className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700 outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100"
              >
                {[20, 50, 100].map(size => <option key={size} value={size}>{size} 条</option>)}
              </select>
            </label>
            <button
              type="button"
              disabled={page <= 1 || dataQuery.isFetching}
              onClick={() => setPage(current => current - 1)}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
              aria-label="上一页"
            >
              <ChevronLeft size={14} />
            </button>
            <span className="min-w-16 text-center text-xs tabular-nums text-slate-500">{page} / {pages}</span>
            <button
              type="button"
              disabled={page >= pages || dataQuery.isFetching}
              onClick={() => setPage(current => current + 1)}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-35"
              aria-label="下一页"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}

function TreeSection({
  title,
  count,
  icon,
  children,
}: {
  title: string
  count: number
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="mb-4">
      <div className="flex items-center gap-1.5 px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
        {icon}
        <span>{title}</span>
        <span className="ml-auto tabular-nums">{count}</span>
      </div>
      <div className="mt-1 space-y-1">{children}</div>
    </section>
  )
}

function TypeTreeButton({
  active,
  label,
  code,
  count,
  onClick,
}: {
  active: boolean
  label: string
  code: string
  count: number
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`group flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${
        active
          ? 'border-teal-200 bg-white text-teal-800 shadow-sm'
          : 'border-transparent text-slate-600 hover:border-slate-200 hover:bg-white hover:text-slate-900'
      }`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? 'bg-teal-500' : 'bg-slate-300 group-hover:bg-slate-400'}`} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium" title={label}>{label}</span>
        <span className="mt-0.5 block truncate font-mono text-[9px] text-slate-400" title={code}>{code}</span>
      </span>
      <span className={`min-w-6 rounded-md px-1.5 py-0.5 text-center text-[10px] tabular-nums ${
        active ? 'bg-teal-50 text-teal-700' : 'bg-slate-100 text-slate-500'
      }`}>
        {count}
      </span>
    </button>
  )
}

function objectTypeName(catalog: InstanceCatalog | undefined, objectTypeId: string) {
  const objectType = catalog?.objectTypes.find(item => item.id === objectTypeId)
  return objectType ? typeLabel(objectType) : objectTypeId
}

const DATASET_KIND_LABEL: Record<string, string> = {
  curated: '成品数据集',
  structured: '结构化数据集',
  semi: '半结构化数据集',
  unstructured: '非结构化数据集',
}

function DatasetAssociationPopover({ datasets }: { datasets: AssociatedDataset[] }) {
  const [open, setOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!popoverRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  return (
    <div ref={popoverRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(current => !current)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={`inline-flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${
          open
            ? 'border-teal-200 bg-teal-50 text-teal-700'
            : 'border-slate-200 bg-white text-slate-600 hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700'
        }`}
      >
        <Database size={12} />
        关联{datasets.length}个数据集
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="关联数据集"
          className="absolute left-0 top-full z-50 mt-2 w-[340px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg shadow-slate-200/70"
        >
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <div>
              <p className="text-xs font-semibold text-slate-800">关联数据集</p>
              <p className="mt-0.5 text-[10px] text-slate-400">基于当前发布版本的数据映射</p>
            </div>
            <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] tabular-nums text-slate-500">
              {datasets.length} 个
            </span>
          </div>
          <div className="max-h-72 overflow-y-auto">
            {datasets.length ? (
              <div className="divide-y divide-slate-100">
                {datasets.map(dataset => (
                  <div key={dataset.id} className="flex items-start gap-3 px-4 py-3 transition hover:bg-slate-50">
                    <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                      dataset.available ? 'bg-teal-50 text-teal-600' : 'bg-amber-50 text-amber-600'
                    }`}>
                      <Database size={15} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-slate-700" title={dataset.name}>
                        {dataset.name}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400">
                        <span>{dataset.kind ? DATASET_KIND_LABEL[dataset.kind] || dataset.kind : '历史数据集'}</span>
                        {dataset.roles.map(role => (
                          <span key={role} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">{role}</span>
                        ))}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="px-6 py-10 text-center">
                <Database size={22} className="mx-auto text-slate-300" />
                <p className="mt-2 text-xs font-medium text-slate-500">尚未关联数据集</p>
                <p className="mt-1 text-[10px] text-slate-400">可在“数据映射”中查看当前配置</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function InstanceTable({
  kind,
  rows,
  columns,
  catalog,
  linkType,
}: {
  kind: Selection['kind']
  rows: Array<ObjectRow | LinkRow>
  columns: DataColumn[]
  catalog?: InstanceCatalog
  linkType?: LinkTypeNode | null
}) {
  return (
    <table className="w-max min-w-full border-separate border-spacing-0 text-left text-xs">
      <thead className="sticky top-0 z-20 bg-slate-50/95 text-slate-500 backdrop-blur">
        <tr>
          {kind === 'link' && (
            <>
              <HeaderCell sticky>
                <EndpointHeader
                  label={objectTypeName(catalog, linkType?.sourceObjectTypeId || '')}
                  side="source"
                />
              </HeaderCell>
              <HeaderCell>
                <EndpointHeader
                  label={objectTypeName(catalog, linkType?.targetObjectTypeId || '')}
                  side="target"
                />
              </HeaderCell>
            </>
          )}
          {columns.map((column, index) => (
            <HeaderCell
              key={`${column.computed ? 'computed' : 'stored'}:${column.name}:${index}`}
              sticky={kind === 'object' && index === 0}
            >
              <div className="flex min-w-40 items-center gap-1.5">
                <span className="font-medium text-slate-600">{column.label}</span>
                {column.primary && (
                  <span className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold text-amber-700" title="主键字段">
                    <KeyRound size={8} /> PK
                  </span>
                )}
                {!column.primary && column.required && (
                  <span className="rounded bg-red-50 px-1 py-0.5 text-[9px] font-semibold text-red-600" title="非空字段">非空</span>
                )}
                {column.computed && (
                  <span className="rounded bg-violet-50 px-1 py-0.5 text-[9px] text-violet-600">派生</span>
                )}
                {column.runtime && (
                  <span className="rounded bg-slate-200 px-1 py-0.5 text-[9px] text-slate-500">运行字段</span>
                )}
              </div>
              <div className="mt-1 flex items-center gap-1.5 font-mono text-[9px] font-normal text-slate-400">
                <span>{column.name}</span>
                {column.type && <span>· {column.type}</span>}
              </div>
            </HeaderCell>
          ))}
          {kind === 'object' ? (
            <>
              <HeaderCell>创建时间</HeaderCell>
              <HeaderCell>更新时间</HeaderCell>
            </>
          ) : null}
        </tr>
      </thead>
      <tbody>
        {rows.map(row => kind === 'object'
          ? <ObjectDataRow key={row.id} row={row as ObjectRow} columns={columns} />
          : <LinkDataRow key={row.id} row={row as LinkRow} columns={columns} />)}
      </tbody>
    </table>
  )
}

function EndpointHeader({ label, side }: { label: string; side: 'source' | 'target' }) {
  const source = side === 'source'
  return (
    <div className="min-w-48">
      <div className="flex items-center gap-2">
        <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
          source
            ? 'border-sky-200 bg-sky-50 text-sky-800'
            : 'border-violet-200 bg-violet-50 text-violet-800'
        }`}>
          {source ? '源端' : '目标端'}
        </span>
        <span className="font-medium text-slate-700">{label}</span>
      </div>
      <div className="mt-1 text-[9px] font-normal text-slate-400">对象业务标识</div>
    </div>
  )
}

function HeaderCell({ children, sticky = false }: { children: React.ReactNode; sticky?: boolean }) {
  return (
    <th scope="col" className={`border-b border-r border-slate-200 px-4 py-2.5 align-top font-medium ${
      sticky ? 'sticky left-0 z-30 min-w-60 bg-slate-50/95' : 'min-w-48'
    }`}>
      {children}
    </th>
  )
}

function ObjectDataRow({ row, columns }: { row: ObjectRow; columns: DataColumn[] }) {
  return (
    <tr className="group align-top hover:bg-teal-50/30">
      {columns.map((column, index) => (
        <DataCell
          key={`${column.computed ? 'computed' : 'stored'}:${column.name}:${index}`}
          sticky={index === 0}
        >
          <FullValue value={column.computed
            ? row.computed?.[column.name] ?? row.properties?.[column.name]
            : row.properties?.[column.name]} />
        </DataCell>
      ))}
      <DataCell><span className="whitespace-nowrap text-slate-500">{formatDate(row.createdAt)}</span></DataCell>
      <DataCell><span className="whitespace-nowrap text-slate-500">{formatDate(row.updatedAt)}</span></DataCell>
    </tr>
  )
}

function LinkDataRow({
  row,
  columns,
}: {
  row: LinkRow
  columns: DataColumn[]
}) {
  return (
    <tr className="group align-top hover:bg-teal-50/30">
      <DataCell sticky>
        <EndpointCell endpoint={row.sourceObject} />
      </DataCell>
      <DataCell>
        <EndpointCell endpoint={row.targetObject} />
      </DataCell>
      {columns.map((column, index) => (
        <DataCell key={`${column.name}:${index}`}><FullValue value={row.properties?.[column.name]} /></DataCell>
      ))}
    </tr>
  )
}

function DataCell({ children, sticky = false }: { children: React.ReactNode; sticky?: boolean }) {
  return (
    <td className={`max-w-[32rem] border-b border-r border-slate-100 px-4 py-3 align-top leading-5 text-slate-600 ${
      sticky ? 'sticky left-0 z-10 min-w-60 bg-white group-hover:bg-[#f5fcfa]' : 'min-w-48'
    }`}>
      {children}
    </td>
  )
}

function EndpointCell({ endpoint }: { endpoint?: EndpointSummary | null }) {
  return (
    <div className="min-w-56 max-w-96">
      <div className="font-medium text-slate-800">{endpoint?.label || '端点实例不可用'}</div>
    </div>
  )
}

function FullValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-slate-300">—</span>
  }
  if (typeof value === 'object') {
    return (
      <pre className="m-0 max-w-[30rem] whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-slate-600">
        {JSON.stringify(value, null, 2)}
      </pre>
    )
  }
  return <span className="block max-w-[30rem] whitespace-pre-wrap break-words text-slate-700">{String(value)}</span>
}

function EmptyData({
  icon,
  title,
  note,
}: {
  icon: React.ReactNode
  title: string
  note?: string
}) {
  return (
    <div className="flex h-full min-h-64 items-center justify-center p-8 text-center">
      <div>
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 text-slate-300">
          {icon}
        </div>
        <p className="mt-3 text-sm font-medium text-slate-600">{title}</p>
        {note && <p className="mt-1 text-xs text-slate-400">{note}</p>}
      </div>
    </div>
  )
}
