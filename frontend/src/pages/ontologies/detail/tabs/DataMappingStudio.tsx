import { useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import {
  AlertCircle, ArrowLeftRight, ArrowRight, Boxes, Check, CheckCircle2,
  ChevronDown, ChevronRight, Database, Eye,
  GitBranch, KeyRound, Layers3, Link2, Loader2, Play, Plus, RefreshCw,
  Search, Sparkles, Table2, Trash2, WandSparkles, X,
} from 'lucide-react'
import './data-mapping-studio.css'

type StudioView = 'overview' | 'objects' | 'relations'
type Composer = 'object' | 'relation' | null

interface ObjectProperty {
  id: string
  name: string
  displayName?: string
  type?: string
  source?: string
}

interface ObjectType {
  id: string
  name: string
  displayName: string
  primaryKey?: string | null
  properties: ObjectProperty[]
}

interface LinkType {
  id: string
  name: string
  displayName: string
  sourceObjectTypeId: string
  targetObjectTypeId: string
  cardinality: string
  properties?: ObjectProperty[]
}

interface ObjectMapping {
  id: string
  curated_dataset_id: string | null
  dataset_name: string | null
  row_count: number | null
  entity_class: string
  field_mapping: Record<string, string>
  status: string
  confidence: number | null
  target_object_type_id: string | null
  binding_mode: 'bound' | 'name_match' | 'auto_create'
  resolved_object_type: { id: string; name: string; display_name: string } | null
  auto_apply_on_review: boolean
}

interface LinkMapping {
  id: string
  relation_type: string
  src_key: string
  tgt_key: string
  src_dataset_id: string | null
  tgt_dataset_id: string | null
  edge_dataset_id: string | null
  field_mapping: Record<string, string>
  is_fat: boolean
}

interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
  quality_score: number | null
}

interface DatasetAsset {
  id: string
  name: string
  rows: number | null
  quality: number | null
  primaryKey: string
  source: 'curated' | 'manual'
  sourceLabel: string
}

interface PreviewData {
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
}

interface PreviewResponse {
  columns?: string[]
  rows?: Record<string, unknown>[]
  total_rows?: number
  count?: number
}

interface ProjectionResult {
  total_rows?: number
  formal_projection?: { object_instances?: number; updated_instances?: number }
}

interface SuggestResult {
  entity_class?: string
  entity_class_cn?: string
  primary_key_column?: string
  field_mappings?: Array<{ column_name: string; property_name: string }>
}

interface MappingCreateResult { mapping_id?: string }
interface RelationBuildItem { count?: number }
interface RelationBuildResult { relations?: RelationBuildItem[]; relation_results?: RelationBuildItem[] }

const IGNORE = '__ignore__'
const NEW_PREFIX = '__new__:'
const NEW_TYPE = '__new_type__'
const UNMAPPED = '__unmapped__'

function normalized(value: string) {
  return value.trim().toLowerCase().replace(/[\s_-]+/g, '')
}

function errorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error !== null) {
    const candidate = error as { detail?: unknown; message?: unknown }
    if (typeof candidate.detail === 'string' && candidate.detail) return candidate.detail
    if (typeof candidate.message === 'string' && candidate.message) return candidate.message
  }
  return fallback
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function readableDate() {
  return new Date().toLocaleString('zh-CN', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function sourceTone(source: DatasetAsset['source']) {
  return source === 'curated' ? 'teal' : 'violet'
}

function StatusDot({ tone = 'muted' }: { tone?: 'good' | 'warn' | 'muted' }) {
  return <span className={`dms-status-dot dms-status-dot--${tone}`} />
}

function EmptyState({ icon: Icon, title, description, action }: {
  icon: typeof Database
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="dms-empty">
      <div className="dms-empty__icon"><Icon size={22} /></div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  )
}

function DatasetPreview({ asset, preview, loading, compact = false }: {
  asset: DatasetAsset | null
  preview: PreviewData | null
  loading: boolean
  compact?: boolean
}) {
  if (!asset) {
    return (
      <div className="dms-preview-placeholder">
        <Eye size={20} />
        <span>选择数据集后，在这里核对真实数据</span>
      </div>
    )
  }
  if (loading) {
    return <div className="dms-preview-placeholder"><Loader2 className="animate-spin" size={20} /><span>正在读取样例数据…</span></div>
  }
  if (!preview || preview.columns.length === 0) {
    return <div className="dms-preview-placeholder"><AlertCircle size={20} /><span>当前数据集没有可预览的数据</span></div>
  }

  const visibleColumns = compact ? preview.columns.slice(0, 5) : preview.columns
  const visibleRows = preview.rows.slice(0, compact ? 4 : 8)
  return (
    <div className="dms-preview">
      <div className="dms-preview__meta">
        <div>
          <span className={`dms-pill dms-pill--${sourceTone(asset.source)}`}>{asset.sourceLabel}</span>
          <strong>{asset.name}</strong>
        </div>
        <span>样例 {visibleRows.length} 行 · 共 {preview.total.toLocaleString()} 行</span>
      </div>
      <div className="dms-preview__table-wrap">
        <table className="dms-preview__table">
          <thead><tr>{visibleColumns.map(col => <th key={col}>{col}</th>)}</tr></thead>
          <tbody>
            {visibleRows.map((row, index) => (
              <tr key={index}>{visibleColumns.map(col => <td key={col} title={displayValue(row[col])}>{displayValue(row[col])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {compact && preview.columns.length > visibleColumns.length && (
        <p className="dms-preview__more">另有 {preview.columns.length - visibleColumns.length} 个字段，可在字段映射区完整配置</p>
      )}
    </div>
  )
}

export default function DataMappingStudio({ ontologyId }: { ontologyId: string }) {
  const qc = useQueryClient()
  const [view, setView] = useState<StudioView>('overview')
  const [composer, setComposer] = useState<Composer>(null)
  const [search, setSearch] = useState('')
  const [expandedMapping, setExpandedMapping] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [toast, setToast] = useState<{ tone: 'good' | 'bad'; message: string } | null>(null)

  const { data: objectTypes = [], isLoading: loadingTypes } = useQuery<ObjectType[]>({
    queryKey: ['formal-object-types', ontologyId],
    queryFn: () => apiClientV2.get<ObjectType[]>(`/formal/ontologies/${ontologyId}/object-types`),
  })
  const { data: linkTypes = [] } = useQuery<LinkType[]>({
    queryKey: ['formal-link-types', ontologyId],
    queryFn: () => apiClientV2.get<LinkType[]>(`/formal/ontologies/${ontologyId}/link-types`),
  })
  const { data: mappings = [], isLoading: loadingMappings } = useQuery<ObjectMapping[]>({
    queryKey: ['mappings', ontologyId],
    queryFn: () => apiClientV2.get<ObjectMapping[]>(`/ontologies/${ontologyId}/mappings`),
  })
  const { data: linkMappings = [] } = useQuery<LinkMapping[]>({
    queryKey: ['link-mappings', ontologyId],
    queryFn: () => apiClientV2.get<LinkMapping[]>(`/ontologies/${ontologyId}/link-mappings`),
  })
  const { data: curated = [] } = useQuery<CuratedDataset[]>({
    queryKey: ['curated-all'],
    queryFn: () => apiClientV2.get<CuratedDataset[]>('/curated'),
  })
  const { data: manualOverview } = useQuery<{ items: Array<Record<string, unknown>> }>({
    queryKey: ['manual-datasets-overview'],
    queryFn: () => apiClientV2.get<{ items: Array<Record<string, unknown>> }>('/datasets/overview'),
  })

  const datasets = useMemo<DatasetAsset[]>(() => {
    const approved = curated
      .filter(item => item.status === 'approved')
      .map(item => ({
        id: item.id, name: item.name, rows: item.row_count, quality: item.quality_score,
        primaryKey: '', source: 'curated' as const, sourceLabel: 'Curated',
      }))
    const manual = (manualOverview?.items ?? [])
      .filter(item => (item.source === 'upload' || item.source === 'manual') && item.primary_key)
      .map(item => ({
        id: String(item.id), name: String(item.name), rows: (item.rowcount as number | null) ?? null,
        quality: null, primaryKey: String(item.primary_key), source: 'manual' as const, sourceLabel: '人工数据',
      }))
    return [...approved, ...manual]
  }, [curated, manualOverview])

  const objectName = (id: string | null) => {
    const type = objectTypes.find(item => item.id === id)
    return type?.displayName || type?.name || '未绑定对象'
  }
  const mappingForObject = (id: string) => mappings.find(item =>
    item.target_object_type_id === id || item.resolved_object_type?.id === id)
  const linkMappingForType = (type: LinkType) => linkMappings.find(item => item.relation_type === type.name || item.relation_type === type.displayName)

  const mappedObjects = objectTypes.filter(item => mappingForObject(item.id)).length
  const mappedRelations = linkTypes.filter(item => linkMappingForType(item)).length
  const warningMappings = mappings.filter(item => item.binding_mode !== 'bound').length
  const issueCount = Math.max(0, objectTypes.length - mappedObjects)
    + Math.max(0, linkTypes.length - mappedRelations) + warningMappings
  const objectCoverage = objectTypes.length ? Math.round(mappedObjects / objectTypes.length * 100) : 0
  const relationCoverage = linkTypes.length ? Math.round(mappedRelations / linkTypes.length * 100) : 0

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['mappings', ontologyId] })
    qc.invalidateQueries({ queryKey: ['link-mappings', ontologyId] })
    qc.invalidateQueries({ queryKey: ['formal-object-types', ontologyId] })
    qc.invalidateQueries({ queryKey: ['formal-link-types', ontologyId] })
    qc.invalidateQueries({ queryKey: ['entities', ontologyId] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }

  const applyMapping = async (mapping: ObjectMapping) => {
    setBusyId(mapping.id)
    setToast(null)
    try {
      const result = await apiClientV2.post<ProjectionResult>(`/ontologies/${ontologyId}/mappings/${mapping.id}/apply-from-dataset`)
      const created = result?.formal_projection?.object_instances ?? 0
      const updated = result?.formal_projection?.updated_instances ?? 0
      setToast({ tone: 'good', message: `已完成灌入：新建 ${created} 个实例，更新 ${updated} 个实例。` })
      refreshAll()
    } catch (error: unknown) {
      setToast({ tone: 'bad', message: errorMessage(error, '灌入失败，请检查映射配置。') })
    } finally {
      setBusyId(null)
    }
  }

  const deleteMapping = async (mapping: ObjectMapping) => {
    if (!window.confirm(`删除「${mapping.entity_class}」的数据映射？\n当前实例投影会被撤销；不可变历史事实仍会保留，可供审计。`)) return
    setBusyId(mapping.id)
    try {
      await apiClientV2.delete(`/ontologies/${ontologyId}/mappings/${mapping.id}`)
      setToast({ tone: 'good', message: '映射通道与当前实例投影已撤销，历史事实已保留。' })
      refreshAll()
    } catch (error: unknown) {
      setToast({ tone: 'bad', message: errorMessage(error, '删除失败。') })
    } finally {
      setBusyId(null)
    }
  }

  const deleteLinkMapping = async (mapping: LinkMapping) => {
    if (!window.confirm(`删除关系映射「${mapping.relation_type}」？\n当前关系边投影会被撤销；不可变历史事实仍会保留，可供审计。`)) return
    setBusyId(mapping.id)
    try {
      await apiClientV2.delete(`/ontologies/${ontologyId}/link-mappings/${mapping.id}`)
      setToast({ tone: 'good', message: '关系映射通道与当前关系边投影已撤销，历史事实已保留。' })
      refreshAll()
    } catch (error: unknown) {
      setToast({ tone: 'bad', message: errorMessage(error, '删除失败。') })
    } finally {
      setBusyId(null)
    }
  }

  const openComposer = (kind: Exclude<Composer, null>) => {
    setView(kind === 'object' ? 'objects' : 'relations')
    setComposer(kind)
    setToast(null)
  }

  if (loadingTypes || loadingMappings) {
    return <div className="dms-loading"><Loader2 className="animate-spin" size={20} />正在整理映射状态…</div>
  }

  return (
    <section className="dms-root">
      <header className="dms-hero">
        <div>
          <div className="dms-eyebrow"><Layers3 size={13} /> DATA MAPPING STUDIO</div>
          <h2>把本体结构，接到真实数据上</h2>
          <p>先看覆盖和风险，再配置字段；每一步都能用真实样例核对，不需要在多个页面之间来回切换。</p>
        </div>
        <div className="dms-hero__actions">
          <button className="dms-button dms-button--ghost" onClick={() => refreshAll()}><RefreshCw size={14} />刷新状态</button>
          <button className="dms-button dms-button--primary" onClick={() => openComposer('object')}><Plus size={15} />新建映射</button>
        </div>
      </header>

      <div className="dms-metrics">
        <button className="dms-metric" onClick={() => setView('objects')}>
          <span className="dms-metric__icon dms-metric__icon--teal"><Boxes size={17} /></span>
          <span><small>对象实体覆盖</small><strong>{mappedObjects}<i>/ {objectTypes.length}</i></strong></span>
          <span className="dms-ring" style={{ '--progress': `${objectCoverage * 3.6}deg` } as React.CSSProperties}>{objectCoverage}%</span>
        </button>
        <button className="dms-metric" onClick={() => setView('relations')}>
          <span className="dms-metric__icon dms-metric__icon--indigo"><GitBranch size={17} /></span>
          <span><small>实体关系覆盖</small><strong>{mappedRelations}<i>/ {linkTypes.length}</i></strong></span>
          <span className="dms-ring dms-ring--indigo" style={{ '--progress': `${relationCoverage * 3.6}deg` } as React.CSSProperties}>{relationCoverage}%</span>
        </button>
        <div className="dms-metric">
          <span className="dms-metric__icon dms-metric__icon--blue"><Database size={17} /></span>
          <span><small>可用数据源</small><strong>{datasets.length}<i> 个</i></strong></span>
          <span className="dms-metric__caption">已审批 / 有主键</span>
        </div>
        <div className={`dms-metric ${issueCount ? 'dms-metric--attention' : ''}`}>
          <span className="dms-metric__icon dms-metric__icon--amber"><AlertCircle size={17} /></span>
          <span><small>需要处理</small><strong>{issueCount}<i> 项</i></strong></span>
          <span className="dms-metric__caption">缺失或弱绑定</span>
        </div>
      </div>

      {toast && (
        <div className={`dms-toast dms-toast--${toast.tone}`}>
          {toast.tone === 'good' ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
          <span>{toast.message}</span><button onClick={() => setToast(null)}><X size={13} /></button>
        </div>
      )}

      <nav className="dms-tabs" aria-label="数据映射视图">
        {([
          ['overview', '映射总览', Layers3],
          ['objects', '对象实体', Boxes],
          ['relations', '实体关系', GitBranch],
        ] as const).map(([key, label, Icon]) => (
          <button key={key} data-active={view === key} onClick={() => { setView(key); setComposer(null) }}>
            <Icon size={14} />{label}
            {key === 'objects' && <span>{mappedObjects}/{objectTypes.length}</span>}
            {key === 'relations' && <span>{mappedRelations}/{linkTypes.length}</span>}
          </button>
        ))}
      </nav>

      {view === 'overview' && (
        <Overview
          objectTypes={objectTypes} linkTypes={linkTypes} mappings={mappings} linkMappings={linkMappings}
          datasets={datasets} objectName={objectName} mappingForObject={mappingForObject}
          linkMappingForType={linkMappingForType} onOpen={openComposer} onShowObjects={() => setView('objects')}
          onShowRelations={() => setView('relations')}
        />
      )}

      {view === 'objects' && (
        <div className="dms-view-stack">
          {composer === 'object' && (
            <ObjectComposer ontologyId={ontologyId} objectTypes={objectTypes} datasets={datasets}
              onClose={() => setComposer(null)} onSaved={(message) => { setToast({ tone: 'good', message }); setComposer(null); refreshAll() }} />
          )}
          <MappingInventory
            mappings={mappings} objectTypes={objectTypes} datasets={datasets} search={search}
            onSearch={setSearch} expanded={expandedMapping} onExpand={setExpandedMapping}
            busyId={busyId} onApply={applyMapping} onDelete={deleteMapping} onCreate={() => setComposer('object')}
          />
        </div>
      )}

      {view === 'relations' && (
        <div className="dms-view-stack">
          {composer === 'relation' && (
            <RelationComposer ontologyId={ontologyId} linkTypes={linkTypes} objectTypes={objectTypes}
              mappings={mappings} datasets={datasets} onClose={() => setComposer(null)}
              onSaved={(message) => { setToast({ tone: 'good', message }); setComposer(null); refreshAll() }} />
          )}
          <RelationInventory linkTypes={linkTypes} linkMappings={linkMappings} objectTypes={objectTypes}
            datasets={datasets} busyId={busyId} onDelete={deleteLinkMapping} onCreate={() => setComposer('relation')} />
        </div>
      )}
    </section>
  )
}

function Overview({ objectTypes, linkTypes, mappings, linkMappings, datasets, objectName, mappingForObject,
  linkMappingForType, onOpen, onShowObjects, onShowRelations }: {
  objectTypes: ObjectType[]; linkTypes: LinkType[]; mappings: ObjectMapping[]; linkMappings: LinkMapping[]
  datasets: DatasetAsset[]; objectName: (id: string | null) => string
  mappingForObject: (id: string) => ObjectMapping | undefined
  linkMappingForType: (type: LinkType) => LinkMapping | undefined
  onOpen: (kind: 'object' | 'relation') => void; onShowObjects: () => void; onShowRelations: () => void
}) {
  const datasetName = (id: string | null) => datasets.find(item => item.id === id)?.name || '未选择数据源'
  const visibleObjects = objectTypes.slice(0, 6)
  return (
    <div className="dms-overview-grid">
      <div className="dms-panel dms-panel--wide">
        <div className="dms-panel__head">
          <div><h3>对象实体映射</h3><p>每一行都是一条可持续运行的数据灌入通道</p></div>
          <button className="dms-text-button" onClick={onShowObjects}>查看全部 <ArrowRight size={13} /></button>
        </div>
        {visibleObjects.length === 0 ? (
          <EmptyState icon={Boxes} title="还没有对象实体" description="先在本体建模中定义对象实体，再回来接入数据。" />
        ) : (
          <div className="dms-matrix">
            <div className="dms-matrix__header"><span>本体对象</span><span>数据来源</span><span>字段</span><span>状态</span></div>
            {visibleObjects.map(type => {
              const mapping = mappingForObject(type.id)
              const fieldCount = mapping ? Object.keys(mapping.field_mapping || {}).filter(k => !k.startsWith('__')).length : 0
              return (
                <button key={type.id} className="dms-matrix__row" onClick={() => mapping ? onShowObjects() : onOpen('object')}>
                  <span className="dms-object-label"><i><Boxes size={14} /></i><b>{type.displayName || type.name}</b><small>{type.name}</small></span>
                  <span className={mapping ? '' : 'is-muted'}>{mapping ? datasetName(mapping.curated_dataset_id) : '尚未接入数据'}</span>
                  <span>{mapping ? `${fieldCount} / ${type.properties.length || fieldCount}` : '—'}</span>
                  <span>{mapping ? <em className={`dms-state dms-state--${mapping.binding_mode === 'bound' ? 'good' : 'warn'}`}><StatusDot tone={mapping.binding_mode === 'bound' ? 'good' : 'warn'} />{mapping.binding_mode === 'bound' ? '运行就绪' : '建议确认'}</em> : <em className="dms-state"><StatusDot />待配置</em>}</span>
                </button>
              )
            })}
          </div>
        )}
        {objectTypes.length > 0 && mappings.length === 0 && (
          <button className="dms-inline-cta" onClick={() => onOpen('object')}><Sparkles size={14} />从第一个对象开始配置映射<ArrowRight size={13} /></button>
        )}
      </div>

      <aside className="dms-panel dms-readiness">
        <div className="dms-panel__head"><div><h3>上线前检查</h3><p>系统替你守住容易出错的环节</p></div></div>
        <div className="dms-checklist">
          <div data-done={mappings.length > 0}><i>{mappings.length > 0 ? <Check size={13} /> : '1'}</i><span><b>对象数据已接入</b><small>{mappings.length ? `${mappings.length} 条对象映射` : '至少配置一条对象映射'}</small></span></div>
          <div data-done={mappings.every(m => m.binding_mode === 'bound')}><i>{mappings.length > 0 && mappings.every(m => m.binding_mode === 'bound') ? <Check size={13} /> : '2'}</i><span><b>目标对象已明确绑定</b><small>{mappings.some(m => m.binding_mode !== 'bound') ? '存在按名匹配或自动建类' : '没有漂移风险'}</small></span></div>
          <div data-done={linkTypes.length === 0 || linkMappings.length > 0}><i>{linkTypes.length === 0 || linkMappings.length > 0 ? <Check size={13} /> : '3'}</i><span><b>实体关系已处理</b><small>{linkTypes.length ? `${linkMappings.length} / ${linkTypes.length} 条关系已映射` : '当前本体没有实体关系'}</small></span></div>
        </div>
        <div className="dms-readiness__tip"><KeyRound size={14} /><span><b>身份稳定性优先</b>主键决定同一条业务数据会被“更新”还是“重复新建”。配置时请用样例数据核对唯一性。</span></div>
      </aside>

      <div className="dms-panel dms-panel--full">
        <div className="dms-panel__head">
          <div><h3>实体关系映射</h3><p>连接表的两端外键，决定对象之间如何连成图</p></div>
          <button className="dms-text-button" onClick={onShowRelations}>查看全部 <ArrowRight size={13} /></button>
        </div>
        {linkTypes.length === 0 ? (
          <EmptyState icon={GitBranch} title="还没有实体关系" description="先在本体建模中定义关系，再将连接表的外键映射到关系两端。" />
        ) : (
          <div className="dms-relation-strip">
            {linkTypes.slice(0, 5).map(type => {
              const mapping = linkMappingForType(type)
              return (
                <button key={type.id} className="dms-relation-card" onClick={() => mapping ? onShowRelations() : onOpen('relation')}>
                  <div><span className="dms-node-chip">{objectName(type.sourceObjectTypeId)}</span><ArrowRight size={15} /><span className="dms-node-chip">{objectName(type.targetObjectTypeId)}</span></div>
                  <strong>{type.displayName || type.name}</strong>
                  <small>{mapping ? `${datasetName(mapping.edge_dataset_id)} · ${mapping.src_key} → ${mapping.tgt_key}` : '尚未配置连接数据'}</small>
                  <em className={`dms-state dms-state--${mapping ? 'good' : 'muted'}`}><StatusDot tone={mapping ? 'good' : 'muted'} />{mapping ? '已映射' : '待配置'}</em>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function MappingInventory({ mappings, objectTypes, datasets, search, onSearch, expanded, onExpand,
  busyId, onApply, onDelete, onCreate }: {
  mappings: ObjectMapping[]; objectTypes: ObjectType[]; datasets: DatasetAsset[]; search: string
  onSearch: (value: string) => void; expanded: string | null; onExpand: (id: string | null) => void
  busyId: string | null; onApply: (mapping: ObjectMapping) => void; onDelete: (mapping: ObjectMapping) => void
  onCreate: () => void
}) {
  const filtered = mappings.filter(mapping => `${mapping.entity_class} ${mapping.dataset_name}`.toLowerCase().includes(search.toLowerCase()))
  const objectLabel = (mapping: ObjectMapping) => mapping.resolved_object_type?.display_name
    || objectTypes.find(item => item.id === mapping.target_object_type_id)?.displayName || mapping.entity_class
  return (
    <div className="dms-panel">
      <div className="dms-inventory-head">
        <div><h3>对象映射清单</h3><p>{mappings.length} 条数据通道 · 最近检查 {readableDate()}</p></div>
        <div className="dms-inventory-head__actions">
          <label className="dms-search"><Search size={14} /><input value={search} onChange={e => onSearch(e.target.value)} placeholder="搜索对象或数据集" /></label>
          <button className="dms-button dms-button--primary" onClick={onCreate}><Plus size={14} />配置对象映射</button>
        </div>
      </div>
      {filtered.length === 0 ? (
        <EmptyState icon={Database} title={mappings.length ? '没有匹配的映射' : '还没有对象映射'}
          description={mappings.length ? '换一个关键词试试。' : '从一个对象实体和一份可用数据开始，系统会帮你预匹配字段。'}
          action={!mappings.length && <button className="dms-button dms-button--primary" onClick={onCreate}><Plus size={14} />配置第一条映射</button>} />
      ) : (
        <div className="dms-inventory">
          {filtered.map(mapping => {
            const fields = Object.entries(mapping.field_mapping || {}).filter(([key]) => !key.startsWith('__'))
            const open = expanded === mapping.id
            const asset = datasets.find(item => item.id === mapping.curated_dataset_id)
            return (
              <article key={mapping.id} className="dms-inventory-card">
                <div className="dms-inventory-card__main">
                  <button className="dms-disclosure" onClick={() => onExpand(open ? null : mapping.id)}>{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>
                  <div className="dms-source-target">
                    <span><i className={`dms-source-icon dms-source-icon--${sourceTone(asset?.source || 'curated')}`}><Table2 size={15} /></i><b>{mapping.dataset_name || asset?.name || '数据集'}</b><small>{mapping.row_count?.toLocaleString() || asset?.rows?.toLocaleString() || '—'} 行</small></span>
                    <ArrowRight size={16} />
                    <span><i className="dms-source-icon dms-source-icon--object"><Boxes size={15} /></i><b>{objectLabel(mapping)}</b><small>{mapping.entity_class}</small></span>
                  </div>
                  <div className="dms-inventory-card__stats"><span><b>{fields.length}</b>字段</span><span><b>{Math.round((mapping.confidence ?? .9) * 100)}%</b>置信度</span></div>
                  <em className={`dms-state dms-state--${mapping.binding_mode === 'bound' ? 'good' : 'warn'}`}><StatusDot tone={mapping.binding_mode === 'bound' ? 'good' : 'warn'} />{mapping.binding_mode === 'bound' ? '明确绑定' : mapping.binding_mode === 'name_match' ? '按名匹配' : '自动建类'}</em>
                  <div className="dms-card-actions">
                    <button className="dms-icon-button" title="展开核对字段" onClick={() => onExpand(open ? null : mapping.id)}><Eye size={15} /></button>
                    <button className="dms-button dms-button--dark" disabled={busyId === mapping.id} onClick={() => onApply(mapping)}>{busyId === mapping.id ? <Loader2 className="animate-spin" size={13} /> : <Play size={13} />}灌入本体</button>
                  </div>
                </div>
                {open && (
                  <div className="dms-inventory-card__detail">
                    <div><h4>字段对应</h4><div className="dms-field-chips">{fields.map(([source, target]) => <span key={source}><code>{source}</code><ArrowRight size={11} /><code>{target}</code></span>)}</div></div>
                    <div className="dms-detail-meta"><span><KeyRound size={13} />主键：{mapping.field_mapping?.__primary_key__ || '未标记'}</span><span><RefreshCw size={13} />{mapping.auto_apply_on_review ? '审核通过后自动灌入' : '手动灌入'}</span></div>
                    <button className="dms-danger-button" onClick={() => onDelete(mapping)}><Trash2 size={13} />删除映射</button>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}

function RelationInventory({ linkTypes, linkMappings, objectTypes, datasets, busyId, onDelete, onCreate }: {
  linkTypes: LinkType[]; linkMappings: LinkMapping[]; objectTypes: ObjectType[]; datasets: DatasetAsset[]
  busyId: string | null; onDelete: (mapping: LinkMapping) => void; onCreate: () => void
}) {
  const objectName = (id: string) => objectTypes.find(item => item.id === id)?.displayName || objectTypes.find(item => item.id === id)?.name || '未知对象'
  const typeFor = (mapping: LinkMapping) => linkTypes.find(item => item.name === mapping.relation_type || item.displayName === mapping.relation_type)
  return (
    <div className="dms-panel">
      <div className="dms-inventory-head">
        <div><h3>关系映射清单</h3><p>{linkMappings.length} 条关系通道 · {linkTypes.length} 个关系类型</p></div>
        <button className="dms-button dms-button--primary" onClick={onCreate}><Plus size={14} />配置关系映射</button>
      </div>
      {linkMappings.length === 0 ? (
        <EmptyState icon={GitBranch} title="还没有关系映射" description="用连接表中的两端外键，把已经灌入的对象实例连起来。"
          action={<button className="dms-button dms-button--primary" onClick={onCreate}><GitBranch size={14} />配置第一条关系</button>} />
      ) : (
        <div className="dms-link-list">
          {linkMappings.map(mapping => {
            const type = typeFor(mapping)
            const dataset = datasets.find(item => item.id === mapping.edge_dataset_id)
            return (
              <article key={mapping.id} className="dms-link-row">
                <div className="dms-link-row__diagram"><span>{type ? objectName(type.sourceObjectTypeId) : '源对象'}</span><div><small>{mapping.src_key}</small><ArrowRight size={16} /><b>{mapping.relation_type}</b><ArrowRight size={16} /><small>{mapping.tgt_key}</small></div><span>{type ? objectName(type.targetObjectTypeId) : '目标对象'}</span></div>
                <div className="dms-link-row__source"><i><Table2 size={14} /></i><span><b>{dataset?.name || mapping.edge_dataset_id?.slice(0, 8) || '直连外键'}</b><small>{mapping.is_fat ? `${Object.keys(mapping.field_mapping || {}).length} 个关系属性` : '无关系属性'}</small></span></div>
                <em className="dms-state dms-state--good"><StatusDot tone="good" />已映射</em>
                <button className="dms-icon-button dms-icon-button--danger" disabled={busyId === mapping.id} onClick={() => onDelete(mapping)} title="删除关系映射"><Trash2 size={14} /></button>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ObjectComposer({ ontologyId, objectTypes, datasets, onClose, onSaved }: {
  ontologyId: string; objectTypes: ObjectType[]; datasets: DatasetAsset[]
  onClose: () => void; onSaved: (message: string) => void
}) {
  const [datasetId, setDatasetId] = useState('')
  const [targetTypeId, setTargetTypeId] = useState('')
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [fieldMap, setFieldMap] = useState<Record<string, string>>({})
  const [pkColumn, setPkColumn] = useState('')
  const [autoApply, setAutoApply] = useState(false)
  const [applyNow, setApplyNow] = useState(true)
  const [suggesting, setSuggesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [suggestedClass, setSuggestedClass] = useState('')
  const previewRequest = useRef(0)
  const asset = datasets.find(item => item.id === datasetId) || null
  const target = objectTypes.find(item => item.id === targetTypeId) || null
  const properties = (target?.properties || []).filter(item => item.source !== 'computed')
  const columns = preview?.columns || []

  const createInitialMap = (cols: string[], type: ObjectType | null) => {
    const next: Record<string, string> = {}
    for (const col of cols) {
      const hit = type?.properties.find(prop => normalized(prop.name) === normalized(col) || normalized(prop.displayName || '') === normalized(col))
      next[col] = hit ? hit.name : type ? IGNORE : `${NEW_PREFIX}${col}`
    }
    return next
  }

  const pickDataset = async (id: string) => {
    const requestId = ++previewRequest.current
    setDatasetId(id)
    setPreview(null)
    setFieldMap({})
    setPkColumn('')
    setSuggestedClass('')
    setError('')
    const nextAsset = datasets.find(item => item.id === id)
    if (!nextAsset) { setLoadingPreview(false); return }
    setLoadingPreview(true)
    try {
      const raw = await apiClientV2.get<PreviewResponse>(nextAsset.source === 'manual'
        ? `/datasets/${nextAsset.id}/preview?limit=12`
        : `/curated/${nextAsset.id}/preview?limit=12`)
      if (requestId !== previewRequest.current) return
      const rows = raw.rows || []
      const cols = raw.columns?.length ? raw.columns : (rows[0] ? Object.keys(rows[0]) : [])
      setPreview({ columns: cols, rows, total: raw.total_rows ?? raw.count ?? nextAsset.rows ?? rows.length })
      setFieldMap(createInitialMap(cols, target))
      const declared = nextAsset.primaryKey && !nextAsset.primaryKey.includes(',') && cols.includes(nextAsset.primaryKey) ? nextAsset.primaryKey : ''
      setPkColumn(declared || cols[0] || '')
    } catch (error: unknown) {
      if (requestId === previewRequest.current) setError(errorMessage(error, '读取数据样例失败。'))
    } finally {
      if (requestId === previewRequest.current) setLoadingPreview(false)
    }
  }

  const pickTarget = (id: string) => {
    setTargetTypeId(id)
    const nextTarget = objectTypes.find(item => item.id === id) || null
    setFieldMap(createInitialMap(columns, nextTarget))
  }

  const smartMatch = async () => {
    if (!asset || !preview) return
    setSuggesting(true); setError('')
    try {
      const result = await apiClientV2.post<SuggestResult>(`/ontologies/${ontologyId}/mappings/suggest`, {
        dataset_name: asset.name, columns: preview.columns, sample_rows: preview.rows.slice(0, 3), ontology_domain: '',
      })
      setSuggestedClass(result.entity_class || asset.name)
      let chosen = target
      if (!chosen) {
        chosen = objectTypes.find(item => normalized(item.name) === normalized(result.entity_class || '')
          || normalized(item.displayName) === normalized(result.entity_class_cn || '')) || null
        if (chosen) setTargetTypeId(chosen.id)
      }
      const suggestions = new Map<string, string>((result.field_mappings || []).map(item => [item.column_name, item.property_name]))
      const next: Record<string, string> = {}
      for (const col of preview.columns) {
        const wanted = suggestions.get(col) || col
        const hit = chosen?.properties.find(prop => normalized(prop.name) === normalized(wanted) || normalized(prop.displayName || '') === normalized(wanted) || normalized(prop.name) === normalized(col))
        next[col] = hit ? hit.name : chosen ? `${NEW_PREFIX}${wanted}` : `${NEW_PREFIX}${wanted}`
      }
      setFieldMap(next)
      if (!asset.primaryKey) setPkColumn(result.primary_key_column || pkColumn || preview.columns[0] || '')
    } catch (error: unknown) {
      setError(errorMessage(error, '智能匹配暂时不可用，你仍可手工完成配置。'))
    } finally {
      setSuggesting(false)
    }
  }

  const mappedCount = columns.filter(col => fieldMap[col] && fieldMap[col] !== IGNORE).length
  const save = async () => {
    if (!asset || !preview || !targetTypeId || mappedCount === 0 || !pkColumn) return
    setSaving(true); setError('')
    try {
      const payloadMap: Record<string, string> = {}
      for (const col of columns) {
        const value = fieldMap[col]
        if (!value || value === IGNORE) continue
        payloadMap[col] = value.startsWith(NEW_PREFIX) ? value.slice(NEW_PREFIX.length) : value
      }
      const entityClass = target?.name || suggestedClass || asset.name.replace(/\W+/g, '_')
      const created = await apiClientV2.post<MappingCreateResult>(`/ontologies/${ontologyId}/mappings`, {
        curated_dataset_id: asset.id, entity_class: entityClass, field_mapping: payloadMap,
        primary_key_column: pkColumn,
        confidence: .9, target_object_type_id: targetTypeId === NEW_TYPE ? null : targetTypeId,
        auto_apply_on_review: autoApply,
      })
      let result: ProjectionResult | null = null
      if (applyNow && created.mapping_id) result = await apiClientV2.post<ProjectionResult>(`/ontologies/${ontologyId}/mappings/${created.mapping_id}/apply-from-dataset`)
      const total = result?.total_rows ?? preview.total
      onSaved(applyNow ? `映射已保存并灌入 ${total.toLocaleString()} 行数据。` : '对象映射已保存，可随时执行灌入。')
    } catch (error: unknown) {
      setError(errorMessage(error, '保存映射失败。'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="dms-composer">
      <div className="dms-composer__head"><div><span className="dms-step-badge">对象实体</span><h3>配置对象映射</h3><p>选择数据和目标对象，然后用真实样例逐字段核对。</p></div><button className="dms-icon-button" onClick={onClose}><X size={16} /></button></div>
      <div className="dms-composer__selectors">
        <label><span><b>1</b>选择数据集</span><select value={datasetId} onChange={e => void pickDataset(e.target.value)}><option value="">选择已审批 / 有主键的数据…</option>{datasets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.sourceLabel}{item.rows != null ? ` · ${item.rows.toLocaleString()} 行` : ''}</option>)}</select></label>
        <ArrowRight size={18} />
        <label><span><b>2</b>绑定对象实体</span><select value={targetTypeId} onChange={e => pickTarget(e.target.value)}><option value="">选择本体中的对象实体…</option>{objectTypes.map(item => <option key={item.id} value={item.id}>{item.displayName || item.name}（{item.properties.length} 属性）</option>)}<option value={NEW_TYPE}>＋ 由数据创建新的对象类型</option></select></label>
        <button className="dms-button dms-button--spark" disabled={!preview || suggesting} onClick={smartMatch}>{suggesting ? <Loader2 className="animate-spin" size={14} /> : <WandSparkles size={14} />}智能匹配</button>
      </div>
      {error && <div className="dms-form-error"><AlertCircle size={14} />{error}</div>}
      <div className="dms-composer__workspace">
        <div className="dms-field-map">
          <div className="dms-workspace-title"><div><h4>字段对应</h4><p>源字段与本体属性一一对应；悬停样例即可查看完整值。</p></div><span>{mappedCount}/{columns.length} 已映射</span></div>
          {!preview ? <div className="dms-preview-placeholder"><ArrowLeftRight size={20} /><span>先选择数据集和对象实体</span></div> : (
            <div className="dms-map-table">
              <div className="dms-map-table__head"><span>数据字段 / 样例</span><span>映射</span><span>本体属性</span></div>
              {columns.map(col => {
                const sample = preview.rows.slice(0, 3).map(row => displayValue(row[col])).join(' · ')
                return <div className="dms-map-row" key={col} data-ignored={fieldMap[col] === IGNORE}>
                  <span><b>{col}</b><small title={sample}>{sample || '暂无样例'}</small></span>
                  <i><ArrowRight size={13} /></i>
                  <select value={fieldMap[col] || IGNORE} onChange={e => setFieldMap(map => ({ ...map, [col]: e.target.value }))}>
                    {properties.map(prop => <option key={prop.id} value={prop.name}>{prop.displayName || prop.name} · {prop.type || 'string'}</option>)}
                    <option value={`${NEW_PREFIX}${col}`}>＋ 新建属性「{col}」</option>
                    <option value={IGNORE}>忽略此字段</option>
                  </select>
                  {pkColumn === col && <em><KeyRound size={10} />身份主键</em>}
                </div>
              })}
            </div>
          )}
        </div>
        <div className="dms-composer__preview"><div className="dms-workspace-title"><div><h4>数据核对</h4><p>映射时始终看得到原始数据。</p></div></div><DatasetPreview asset={asset} preview={preview} loading={loadingPreview} compact /></div>
      </div>
      <div className="dms-composer__footer">
        <div className="dms-options">
          <label><span>身份主键</span><select value={pkColumn} disabled={!!asset?.primaryKey && !asset.primaryKey.includes(',')} onChange={e => setPkColumn(e.target.value)}><option value="">选择唯一字段…</option>{columns.map(col => <option key={col} value={col}>{col}</option>)}</select>{asset?.primaryKey && <small>数据契约已锁定</small>}</label>
          <label className="dms-check"><input type="checkbox" checked={autoApply} onChange={e => setAutoApply(e.target.checked)} /><span>数据审核通过后自动灌入</span></label>
          <label className="dms-check"><input type="checkbox" checked={applyNow} onChange={e => setApplyNow(e.target.checked)} /><span>保存后立即灌入</span></label>
        </div>
        <div><button className="dms-button dms-button--ghost" onClick={onClose}>取消</button><button className="dms-button dms-button--primary" disabled={saving || !asset || !targetTypeId || !pkColumn || mappedCount === 0} onClick={save}>{saving ? <Loader2 className="animate-spin" size={14} /> : <CheckCircle2 size={14} />}{saving ? '正在保存…' : applyNow ? '保存并灌入' : '保存映射'}</button></div>
      </div>
    </div>
  )
}

function RelationComposer({ ontologyId, linkTypes, objectTypes, mappings, datasets, onClose, onSaved }: {
  ontologyId: string; linkTypes: LinkType[]; objectTypes: ObjectType[]; mappings: ObjectMapping[]; datasets: DatasetAsset[]
  onClose: () => void; onSaved: (message: string) => void
}) {
  const [typeId, setTypeId] = useState('')
  const [edgeDatasetId, setEdgeDatasetId] = useState('')
  const [srcDatasetId, setSrcDatasetId] = useState('')
  const [tgtDatasetId, setTgtDatasetId] = useState('')
  const [srcKey, setSrcKey] = useState('')
  const [tgtKey, setTgtKey] = useState('')
  const [propMap, setPropMap] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const previewRequest = useRef(0)
  const type = linkTypes.find(item => item.id === typeId) || null
  const asset = datasets.find(item => item.id === edgeDatasetId) || null
  const columns = preview?.columns || []
  const objectName = (id: string) => objectTypes.find(item => item.id === id)?.displayName || objectTypes.find(item => item.id === id)?.name || '未知对象'

  const pickType = (id: string) => {
    setTypeId(id); setSrcKey(''); setTgtKey(''); setPropMap({})
    const next = linkTypes.find(item => item.id === id)
    if (!next) return
    setSrcDatasetId(mappings.find(item => item.target_object_type_id === next.sourceObjectTypeId)?.curated_dataset_id || '')
    setTgtDatasetId(mappings.find(item => item.target_object_type_id === next.targetObjectTypeId)?.curated_dataset_id || '')
  }

  const pickEdgeDataset = async (id: string) => {
    const requestId = ++previewRequest.current
    setEdgeDatasetId(id)
    setPreview(null)
    setSrcKey('')
    setTgtKey('')
    setPropMap({})
    setError('')
    const nextAsset = datasets.find(item => item.id === id)
    if (!nextAsset) { setLoadingPreview(false); return }
    setLoadingPreview(true)
    try {
      const raw = await apiClientV2.get<PreviewResponse>(nextAsset.source === 'manual'
        ? `/datasets/${nextAsset.id}/preview?limit=12`
        : `/curated/${nextAsset.id}/preview?limit=12`)
      if (requestId !== previewRequest.current) return
      const rows = raw.rows || []
      const cols = raw.columns?.length ? raw.columns : (rows[0] ? Object.keys(rows[0]) : [])
      setPreview({ columns: cols, rows, total: raw.total_rows ?? raw.count ?? nextAsset.rows ?? rows.length })
      const next: Record<string, string> = {}
      for (const prop of type?.properties || []) {
        const hit = cols.find(col => normalized(col) === normalized(prop.name) || normalized(col) === normalized(prop.displayName || ''))
        next[prop.name] = hit || UNMAPPED
      }
      setPropMap(next)
    } catch (error: unknown) {
      if (requestId === previewRequest.current) setError(errorMessage(error, '读取连接表失败。'))
    } finally {
      if (requestId === previewRequest.current) setLoadingPreview(false)
    }
  }

  const canSave = !!type && !!asset && !!srcDatasetId && !!tgtDatasetId && !!srcKey && !!tgtKey && srcKey !== tgtKey
  const save = async () => {
    if (!type || !asset || !canSave) return
    setSaving(true); setError('')
    try {
      const fieldMapping: Record<string, string> = {}
      for (const [prop, col] of Object.entries(propMap)) if (col && col !== UNMAPPED) fieldMapping[prop] = col
      await apiClientV2.post(`/ontologies/${ontologyId}/link-mappings`, {
        src_dataset_id: srcDatasetId, tgt_dataset_id: tgtDatasetId, edge_dataset_id: asset.id,
        relation_type: type.name, link_type_id: type.id, src_key: srcKey, tgt_key: tgtKey, field_mapping: fieldMapping,
      })
      const result = await apiClientV2.post<RelationBuildResult>(`/ontologies/${ontologyId}/mappings/build-all`)
      const count = (result.relations || result.relation_results || []).reduce((sum, item) => sum + (item.count || 0), 0)
      onSaved(`关系映射已保存${typeof count === 'number' ? `，已生成 ${count} 条关系边` : '并完成投影'}。`)
    } catch (error: unknown) {
      setError(errorMessage(error, '保存关系映射失败。'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="dms-composer">
      <div className="dms-composer__head"><div><span className="dms-step-badge dms-step-badge--indigo">实体关系</span><h3>配置关系映射</h3><p>先确认关系语义，再用连接表中的两端外键把对象实例连起来。</p></div><button className="dms-icon-button" onClick={onClose}><X size={16} /></button></div>
      <div className="dms-relation-builder">
        <label><span><b>1</b>选择本体关系</span><select value={typeId} onChange={e => pickType(e.target.value)}><option value="">选择实体关系…</option>{linkTypes.map(item => <option key={item.id} value={item.id}>{item.displayName || item.name}（{objectName(item.sourceObjectTypeId)} → {objectName(item.targetObjectTypeId)}）</option>)}</select></label>
        <label><span><b>2</b>选择连接表</span><select value={edgeDatasetId} onChange={e => void pickEdgeDataset(e.target.value)}><option value="">选择含两端外键的数据集…</option>{datasets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.sourceLabel}</option>)}</select></label>
      </div>
      {error && <div className="dms-form-error"><AlertCircle size={14} />{error}</div>}
      {type && (
        <div className="dms-endpoints">
          <div className="dms-endpoint"><span className="dms-endpoint__node"><Boxes size={15} /><b>{objectName(type.sourceObjectTypeId)}</b><small>源对象</small></span><label>连接表外键<select value={srcKey} onChange={e => setSrcKey(e.target.value)}><option value="">选择源端外键…</option>{columns.map(col => <option key={col} value={col}>{col}</option>)}</select></label><label>对象数据集<select value={srcDatasetId} onChange={e => setSrcDatasetId(e.target.value)}><option value="">选择已映射数据…</option>{datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>
          <div className="dms-endpoints__relation"><span>{type.displayName || type.name}</span><ArrowRight size={20} /></div>
          <div className="dms-endpoint"><span className="dms-endpoint__node dms-endpoint__node--target"><Boxes size={15} /><b>{objectName(type.targetObjectTypeId)}</b><small>目标对象</small></span><label>连接表外键<select value={tgtKey} onChange={e => setTgtKey(e.target.value)}><option value="">选择目标端外键…</option>{columns.map(col => <option key={col} value={col}>{col}</option>)}</select></label><label>对象数据集<select value={tgtDatasetId} onChange={e => setTgtDatasetId(e.target.value)}><option value="">选择已映射数据…</option>{datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>
        </div>
      )}
      {srcKey && srcKey === tgtKey && <div className="dms-form-error"><AlertCircle size={14} />关系两端不能使用同一个外键字段。</div>}
      <div className="dms-composer__workspace dms-composer__workspace--relation">
        <div className="dms-field-map"><div className="dms-workspace-title"><div><h4>关系属性</h4><p>外键负责连接，其他列可以写入关系本身。</p></div></div>{!type || !preview ? <div className="dms-preview-placeholder"><GitBranch size={20} /><span>选择关系和连接表后配置</span></div> : (type.properties || []).length === 0 ? <div className="dms-preview-placeholder"><CheckCircle2 size={20} /><span>这个关系没有属性，只需确认两端外键</span></div> : <div className="dms-map-table">{(type.properties || []).map(prop => <div className="dms-map-row" key={prop.id}><span><b>{prop.displayName || prop.name}</b><small>{prop.type || 'string'}</small></span><i><ArrowLeftRight size={13} /></i><select value={propMap[prop.name] || UNMAPPED} onChange={e => setPropMap(map => ({ ...map, [prop.name]: e.target.value }))}><option value={UNMAPPED}>不写入此属性</option>{columns.filter(col => col !== srcKey && col !== tgtKey).map(col => <option key={col} value={col}>{col}</option>)}</select></div>)}</div>}</div>
        <div className="dms-composer__preview"><div className="dms-workspace-title"><div><h4>连接数据核对</h4><p>重点检查两端外键能否对应到对象主键。</p></div></div><DatasetPreview asset={asset} preview={preview} loading={loadingPreview} compact /></div>
      </div>
      <div className="dms-composer__footer"><p className="dms-footer-hint"><Link2 size={13} />保存后会立即投影关系，可到图谱编辑器核对结果。</p><div><button className="dms-button dms-button--ghost" onClick={onClose}>取消</button><button className="dms-button dms-button--primary" disabled={!canSave || saving} onClick={save}>{saving ? <Loader2 className="animate-spin" size={14} /> : <CheckCircle2 size={14} />}{saving ? '保存并投影…' : '保存并生成关系'}</button></div></div>
    </div>
  )
}
