import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import {
  CheckCircle, Loader2, Plus, Play, Database, ChevronDown, ChevronRight,
  Link2, Sparkles, Trash2, ExternalLink, Wand2, RefreshCw,
} from 'lucide-react'

/* ============ 类型 ============ */

interface Mapping {
  id: string
  curated_dataset_id: string | null
  dataset_name: string | null
  row_count: number | null
  entity_class: string
  field_mapping: Record<string, any>
  status: string
  confidence: number | null
  created_at: string | null
  target_object_type_id: string | null
  binding_mode: 'bound' | 'name_match' | 'auto_create'
  resolved_object_type: { id: string; name: string; display_name: string } | null
  auto_apply_on_review: boolean
}

interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
  quality_score: number | null
}

interface FormalObjectType {
  id: string
  name: string
  displayName: string
  primaryKey?: string | null
  properties: { id: string; name: string; displayName?: string; type?: string; source?: string }[]
}

const IGNORE = '__ignore__'
const NEW_PREFIX = '__new__:'

/* ============ 绑定徽章 ============ */

function BindingChip({ mapping }: { mapping: Mapping }) {
  if (mapping.binding_mode === 'bound') {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border bg-emerald-50 border-emerald-200 text-emerald-700"
        title="人工绑定：数据灌入图谱中这个对象实体">
        <Link2 size={11} />
        绑定「{mapping.resolved_object_type?.display_name || mapping.resolved_object_type?.name}」
      </span>
    )
  }
  if (mapping.binding_mode === 'name_match') {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border bg-blue-50 border-blue-200 text-blue-700"
        title="按名字自动匹配到图谱中的同名实体（建议改为显式绑定，防止将来改名后漂移）">
        ≈ 同名匹配「{mapping.resolved_object_type?.display_name || mapping.resolved_object_type?.name}」
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border bg-gray-50 border-gray-200 text-gray-500"
      title="图谱中没有同名实体：灌入时将由数据自动生成一个新类型">
      <Sparkles size={11} />
      将新建「{mapping.entity_class}」
    </span>
  )
}

/* ============ 已有映射行（含维护区） ============ */

function MappingRow({ mapping, ontologyId, objectTypes, onChanged }: {
  mapping: Mapping; ontologyId: string; objectTypes: FormalObjectType[]; onChanged: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyResult, setApplyResult] = useState<any>(null)
  const [applyError, setApplyError] = useState('')
  const [savingMaint, setSavingMaint] = useState(false)

  const handleApply = async () => {
    setApplying(true)
    setApplyError('')
    setApplyResult(null)
    try {
      const res: any = await apiClientV2.post(
        `/ontologies/${ontologyId}/mappings/${mapping.id}/apply-from-dataset`
      )
      setApplyResult(res)
      onChanged()
    } catch (e: any) {
      setApplyError(e?.detail || e?.message || '执行失败')
    } finally {
      setApplying(false)
    }
  }

  const updateMapping = async (patch: Record<string, unknown>) => {
    setSavingMaint(true)
    setApplyError('')
    try {
      await apiClientV2.put(`/ontologies/${ontologyId}/mappings/${mapping.id}`, patch)
      onChanged()
    } catch (e: any) {
      setApplyError(e?.detail || e?.message || '更新失败')
    } finally {
      setSavingMaint(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(`删除映射「${mapping.entity_class}」？\n已灌入的实例和历史事实会保留，只是不再有这条灌入通道。`)) return
    try {
      await apiClientV2.delete(`/ontologies/${ontologyId}/mappings/${mapping.id}`)
      onChanged()
    } catch (e: any) {
      setApplyError(e?.detail || e?.message || '删除失败')
    }
  }

  const fieldEntries = Object.entries(mapping.field_mapping || {}).filter(
    ([k]) => !k.startsWith('__')
  )
  const proj = applyResult?.formal_projection

  return (
    <div className="border rounded-xl bg-white overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-gray-400 hover:text-gray-700 flex-shrink-0"
          title={expanded ? '收起' : '展开字段映射与维护'}
        >
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <Database size={15} className="text-blue-500 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm truncate">{mapping.entity_class}</span>
            <BindingChip mapping={mapping} />
            {mapping.auto_apply_on_review && (
              <span className="text-xs px-2 py-0.5 rounded border bg-violet-50 border-violet-200 text-violet-700"
                title="curated 数据审核通过后自动灌入本体">
                审核后自动灌入
              </span>
            )}
          </div>
          <p className="text-xs text-gray-400 truncate mt-0.5">
            {mapping.dataset_name ?? mapping.curated_dataset_id?.slice(0, 8)}
            {mapping.row_count != null && ` · ${mapping.row_count.toLocaleString()} 行`}
          </p>
        </div>
        <button
          onClick={handleApply}
          disabled={applying}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-xs hover:bg-gray-800 disabled:opacity-50 flex-shrink-0"
          title="读取数据集最新版本（含行级审核修正），灌入本体并触发哨兵"
        >
          {applying ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {applying ? '灌入中...' : '灌入本体'}
        </button>
      </div>

      {applyResult && (
        <div className="mx-4 mb-3 flex items-center gap-2 flex-wrap text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <CheckCircle size={13} className="flex-shrink-0" />
          <span>
            灌入完成：共 {applyResult.total_rows ?? 0} 行
            {proj && ` → 新建实例 ${proj.object_instances ?? 0}、更新 ${proj.updated_instances ?? 0}`}
            {proj && (proj.object_types ?? 0) > 0 && `、新建类型 ${proj.object_types}`}
            {proj && (proj.link_instances ?? 0) > 0 && `、新建链接 ${proj.link_instances}`}
          </span>
          <a href={`#/ontologies/${ontologyId}/graph`}
            className="ml-auto inline-flex items-center gap-1 text-green-800 underline hover:no-underline flex-shrink-0">
            在图谱编辑器查看 <ExternalLink size={11} />
          </a>
        </div>
      )}
      {applyError && (
        <div className="mx-4 mb-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {applyError}
        </div>
      )}

      {expanded && (
        <div className="border-t mx-4 mb-3 pt-3 space-y-3">
          {fieldEntries.length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-2">字段映射（数据列 → 实体属性）</p>
              <div className="grid grid-cols-2 gap-1.5">
                {fieldEntries.map(([col, prop]) => (
                  <div key={col} className="flex items-center gap-1.5 text-xs bg-gray-50 rounded px-2 py-1">
                    <span className="font-mono text-gray-500">{col}</span>
                    <span className="text-gray-300">→</span>
                    <span className="font-mono text-blue-600">{String(prop)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 维护区：改绑定 / 自动灌入开关 / 删除 */}
          <div className="rounded-lg bg-gray-50 border px-3 py-2.5 space-y-2">
            <p className="text-xs font-medium text-gray-500">映射维护</p>
            <div className="flex items-center gap-2 flex-wrap">
              <label className="text-xs text-gray-500">灌入到：</label>
              <select
                value={mapping.target_object_type_id ?? ''}
                disabled={savingMaint}
                onChange={e => void updateMapping({ target_object_type_id: e.target.value || null })}
                className="border rounded-lg px-2 py-1 text-xs bg-white max-w-[220px]"
                title="人工绑定目标对象实体；留空则按名字匹配，匹配不到时由数据新建类型"
              >
                <option value="">（自动：按名匹配 / 数据新建）</option>
                {objectTypes.map(ot => (
                  <option key={ot.id} value={ot.id}>{ot.displayName || ot.name}</option>
                ))}
              </select>
              <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer ml-2">
                <input
                  type="checkbox"
                  checked={mapping.auto_apply_on_review}
                  disabled={savingMaint}
                  onChange={e => void updateMapping({ auto_apply_on_review: e.target.checked })}
                  className="accent-violet-600"
                />
                审核通过后自动灌入
              </label>
              <button
                onClick={handleDelete}
                className="ml-auto inline-flex items-center gap-1 text-xs text-red-500 hover:text-red-700 px-2 py-1"
                title="删除这条映射（不影响已灌入的实例与历史事实）"
              >
                <Trash2 size={12} /> 删除映射
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ============ 新建映射（绑定优先的向导） ============ */

function LinkDatasetPanel({ ontologyId, objectTypes, onDone }: {
  ontologyId: string; objectTypes: FormalObjectType[]; onDone: () => void
}) {
  const [selectedId, setSelectedId] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [suggestion, setSuggestion] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 绑定与字段映射编辑状态
  const [targetTypeId, setTargetTypeId] = useState('')      // '' = 由数据新建
  const [autoMatched, setAutoMatched] = useState(false)
  const [columns, setColumns] = useState<string[]>([])
  const [colMap, setColMap] = useState<Record<string, string>>({})  // column -> propertyName | __ignore__ | __new__:name
  const [pkColumn, setPkColumn] = useState('')
  const [autoApply, setAutoApply] = useState(false)
  const [applyNow, setApplyNow] = useState(true)
  const [doneMsg, setDoneMsg] = useState<any>(null)

  const { data: datasets = [], isLoading } = useQuery<CuratedDataset[]>({
    queryKey: ['curated-all'],
    queryFn: () => apiClientV2.get('/curated') as any,
  })
  const approvedDatasets = (datasets as CuratedDataset[]).filter(d => d.status === 'approved')

  const boundType = useMemo(
    () => objectTypes.find(ot => ot.id === targetTypeId) || null,
    [objectTypes, targetTypeId])
  const boundProps = useMemo(
    () => (boundType?.properties || []).filter(p => p.source !== 'computed'),
    [boundType])

  /** 名字归一后匹配已有实体（大小写/前后空格不敏感） */
  const matchTypeByName = (name: string) => {
    const n = (name || '').trim().toLowerCase()
    if (!n) return null
    return objectTypes.find(ot =>
      ot.name?.trim().toLowerCase() === n || ot.displayName?.trim().toLowerCase() === n) || null
  }

  const handleSuggest = async () => {
    if (!selectedId) return
    setSuggesting(true)
    setError('')
    setSuggestion(null)
    setDoneMsg(null)
    try {
      const ds = approvedDatasets.find(d => d.id === selectedId)
      const preview: any = await apiClientV2.get(`/curated/${selectedId}/preview?limit=5`)
      const cols: string[] = preview.rows?.length > 0 ? Object.keys(preview.rows[0]) : []
      const res: any = await apiClientV2.post(`/ontologies/${ontologyId}/mappings/suggest`, {
        dataset_name: ds?.name ?? '',
        columns: cols,
        sample_rows: preview.rows?.slice(0, 3) ?? [],
        ontology_domain: '',
      })
      setSuggestion(res)
      setColumns(cols)
      setPkColumn(res.primary_key_column || cols[0] || '')
      // 初始字段映射 = LLM 建议
      const init: Record<string, string> = {}
      for (const fm of res.field_mappings ?? []) init[fm.column_name] = fm.property_name
      for (const c of cols) if (!(c in init)) init[c] = c
      // 自动匹配画布中的同名实体 → 预选绑定，并把建议属性对齐到该类型的属性名
      const matched = matchTypeByName(res.entity_class) || matchTypeByName(res.entity_class_cn)
      if (matched) {
        setTargetTypeId(matched.id)
        setAutoMatched(true)
        const typeProps = matched.properties.filter(p => p.source !== 'computed')
        for (const c of cols) {
          const want = (init[c] || c).toLowerCase()
          const hit = typeProps.find(p =>
            p.name.toLowerCase() === want || (p.displayName || '').toLowerCase() === want
            || p.name.toLowerCase() === c.toLowerCase())
          init[c] = hit ? hit.name : `${NEW_PREFIX}${init[c] || c}`
        }
      } else {
        setTargetTypeId('')
        setAutoMatched(false)
      }
      setColMap(init)
    } catch (e: any) {
      setError(e?.detail || e?.message || '自动建议失败')
    } finally {
      setSuggesting(false)
    }
  }

  /** 切换绑定目标时，把每列重新对齐到目标类型的属性 */
  const handlePickType = (typeId: string) => {
    setTargetTypeId(typeId)
    setAutoMatched(false)
    const t = objectTypes.find(o => o.id === typeId)
    setColMap(prev => {
      const next: Record<string, string> = {}
      for (const c of columns) {
        const raw = (prev[c] || '').replace(NEW_PREFIX, '')
        if (prev[c] === IGNORE) { next[c] = IGNORE; continue }
        if (!t) { next[c] = raw || c; continue }
        const props = t.properties.filter(p => p.source !== 'computed')
        const hit = props.find(p =>
          p.name.toLowerCase() === (raw || c).toLowerCase()
          || (p.displayName || '').toLowerCase() === (raw || c).toLowerCase()
          || p.name.toLowerCase() === c.toLowerCase())
        next[c] = hit ? hit.name : `${NEW_PREFIX}${raw || c}`
      }
      return next
    })
  }

  const handleSave = async () => {
    if (!suggestion) return
    setSaving(true)
    setError('')
    setDoneMsg(null)
    try {
      const fieldMapping: Record<string, string> = {}
      for (const c of columns) {
        const v = colMap[c]
        if (!v || v === IGNORE) continue
        fieldMapping[c] = v.startsWith(NEW_PREFIX) ? v.slice(NEW_PREFIX.length) : v
      }
      if (pkColumn) fieldMapping['__primary_key__'] = pkColumn
      const created: any = await apiClientV2.post(`/ontologies/${ontologyId}/mappings`, {
        curated_dataset_id: selectedId,
        entity_class: boundType ? (boundType.name || suggestion.entity_class) : suggestion.entity_class,
        field_mapping: fieldMapping,
        confidence: 0.9,
        target_object_type_id: targetTypeId || null,
        auto_apply_on_review: autoApply,
      })
      let applied: any = null
      if (applyNow && created?.mapping_id) {
        applied = await apiClientV2.post(
          `/ontologies/${ontologyId}/mappings/${created.mapping_id}/apply-from-dataset`)
      }
      setDoneMsg({ applied })
      setSuggestion(null)
      setSelectedId('')
      onDone()
    } catch (e: any) {
      setError(e?.detail || e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const mappedCount = columns.filter(c => colMap[c] && colMap[c] !== IGNORE).length

  return (
    <div className="border rounded-xl bg-white p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Wand2 size={15} className="text-violet-500" />
        <p className="text-sm font-medium">关联 Curated 数据集 → 灌入本体</p>
      </div>

      {doneMsg && (
        <div className="flex items-center gap-2 flex-wrap text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <CheckCircle size={13} />
          映射已创建{doneMsg.applied ? `，并已灌入：${doneMsg.applied.total_rows ?? 0} 行 → 新建实例 ${doneMsg.applied.formal_projection?.object_instances ?? 0}、更新 ${doneMsg.applied.formal_projection?.updated_instances ?? 0}` : '（尚未灌入，可在下方列表点「灌入本体」）'}
          <a href={`#/ontologies/${ontologyId}/graph`}
            className="ml-auto inline-flex items-center gap-1 text-green-800 underline hover:no-underline">
            在图谱编辑器查看 <ExternalLink size={11} />
          </a>
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-gray-400">加载中...</p>
      ) : approvedDatasets.length === 0 ? (
        <p className="text-xs text-gray-400">暂无已审批的 Curated Dataset。请先在 数据 → 管道 → Curated 中完成审批。</p>
      ) : (
        <>
          {/* ① 选数据集 */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">①</span>
            <select
              value={selectedId}
              onChange={e => { setSelectedId(e.target.value); setSuggestion(null); setDoneMsg(null) }}
              className="flex-1 border rounded-lg px-3 py-2 text-sm"
            >
              <option value="">选择已审批的 Curated 数据集...</option>
              {approvedDatasets.map(d => (
                <option key={d.id} value={d.id}>
                  {d.name}{d.row_count != null ? ` (${d.row_count.toLocaleString()} 行)` : ''}
                </option>
              ))}
            </select>
            {selectedId && !suggestion && (
              <button
                onClick={handleSuggest}
                disabled={suggesting}
                className="flex items-center gap-1.5 px-3.5 py-2 border border-black rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50 flex-shrink-0"
              >
                {suggesting ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {suggesting ? '分析中...' : '智能分析'}
              </button>
            )}
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}

          {suggestion && (
            <div className="space-y-4">
              {/* ② 绑定目标对象实体 */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">②</span>
                  <label className="text-xs font-medium text-gray-600">灌入到哪个对象实体？</label>
                  {autoMatched && (
                    <span className="text-[11px] text-emerald-600 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
                      已自动匹配画布中的同名实体
                    </span>
                  )}
                </div>
                <div className="pl-7">
                  <select
                    value={targetTypeId}
                    onChange={e => handlePickType(e.target.value)}
                    className="w-full border rounded-lg px-3 py-2 text-sm"
                  >
                    <option value="">🆕 由数据新建类型：{suggestion.entity_class}{suggestion.entity_class_cn ? `（${suggestion.entity_class_cn}）` : ''}</option>
                    {objectTypes.map(ot => (
                      <option key={ot.id} value={ot.id}>
                        🔗 绑定已有实体：{ot.displayName || ot.name}
                      </option>
                    ))}
                  </select>
                  <p className="text-[11px] text-gray-400 mt-1">
                    {targetTypeId
                      ? '数据将灌入这个实体：已有属性定义（含派生属性/主键）原样保留，数据里新出现的列会追加为新属性。'
                      : '图谱中没有匹配的实体时，会按数据自动生成一个新类型（之后也可以在这里改绑定）。'}
                  </p>
                </div>
              </div>

              {/* ③ 字段映射 */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">③</span>
                  <label className="text-xs font-medium text-gray-600">字段映射</label>
                  <span className="text-[11px] text-gray-400">{mappedCount}/{columns.length} 列已映射</span>
                </div>
                <div className="pl-7 space-y-1">
                  {columns.map(col => (
                    <div key={col} className="flex items-center gap-2 text-xs">
                      <span className="font-mono text-gray-600 w-36 truncate flex-shrink-0" title={col}>{col}</span>
                      <span className="text-gray-300 flex-shrink-0">→</span>
                      {boundType ? (
                        <select
                          value={colMap[col] ?? IGNORE}
                          onChange={e => setColMap(m => ({ ...m, [col]: e.target.value }))}
                          className={`flex-1 border rounded px-2 py-1 text-xs ${
                            colMap[col] === IGNORE ? 'text-gray-400' :
                            colMap[col]?.startsWith(NEW_PREFIX) ? 'text-violet-600' : 'text-blue-700'
                          }`}
                        >
                          {boundProps.map(p => (
                            <option key={p.id} value={p.name}>
                              {p.displayName ? `${p.displayName}（${p.name}）` : p.name}
                            </option>
                          ))}
                          <option value={`${NEW_PREFIX}${col}`}>➕ 新建属性「{col}」</option>
                          <option value={IGNORE}>— 忽略此列 —</option>
                        </select>
                      ) : (
                        <input
                          value={colMap[col] === IGNORE ? '' : (colMap[col] ?? col)}
                          placeholder="忽略此列"
                          onChange={e => setColMap(m => ({ ...m, [col]: e.target.value || IGNORE }))}
                          className="flex-1 border rounded px-2 py-1 text-xs font-mono text-blue-700"
                        />
                      )}
                      {pkColumn === col && (
                        <span className="text-[10px] text-amber-600 bg-amber-50 border border-amber-200 rounded px-1 flex-shrink-0">主键</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* ④ 主键 + 开关 */}
              <div className="pl-7 flex items-center gap-4 flex-wrap text-xs">
                <label className="flex items-center gap-1.5 text-gray-600">
                  主键列（去重键）：
                  <select value={pkColumn} onChange={e => setPkColumn(e.target.value)}
                    className="border rounded px-2 py-1 text-xs">
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
                <label className="inline-flex items-center gap-1.5 text-gray-600 cursor-pointer">
                  <input type="checkbox" checked={autoApply} onChange={e => setAutoApply(e.target.checked)}
                    className="accent-violet-600" />
                  审核通过后自动灌入
                </label>
                <label className="inline-flex items-center gap-1.5 text-gray-600 cursor-pointer">
                  <input type="checkbox" checked={applyNow} onChange={e => setApplyNow(e.target.checked)}
                    className="accent-emerald-600" />
                  保存后立即灌入
                </label>
              </div>

              <div className="pl-7 flex gap-2">
                <button
                  onClick={handleSave}
                  disabled={saving || mappedCount === 0}
                  className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                >
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle size={13} />}
                  {saving ? (applyNow ? '保存并灌入中...' : '保存中...') : (applyNow ? '保存并灌入本体' : '保存映射')}
                </button>
                <button
                  onClick={() => void handleSuggest()}
                  disabled={suggesting}
                  className="flex items-center gap-1.5 px-3 py-2 text-sm border rounded-lg hover:bg-gray-50"
                >
                  <RefreshCw size={13} /> 重新分析
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

/* ============ Tab 主体 ============ */

export default function CuratedDatasetsTab({ ontologyId }: { ontologyId: string }) {
  const qc = useQueryClient()
  const [showLink, setShowLink] = useState(false)

  const { data: mappings = [], isLoading } = useQuery<Mapping[]>({
    queryKey: ['mappings', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/mappings`) as any,
  })

  const { data: objectTypes = [] } = useQuery<FormalObjectType[]>({
    queryKey: ['formal-object-types', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/object-types`) as any,
  })

  const handleChanged = () => {
    qc.invalidateQueries({ queryKey: ['mappings', ontologyId] })
    qc.invalidateQueries({ queryKey: ['formal-object-types', ontologyId] })
    qc.invalidateQueries({ queryKey: ['entities', ontologyId] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }

  const handleLinkDone = () => {
    handleChanged()
  }

  if (isLoading) return <div className="text-gray-400 text-sm py-8 text-center">加载中...</div>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          把已审批的 Curated 数据灌入本体：优先<b>绑定图谱中已有的对象实体</b>（先建模、再灌数据），也可由数据自动生成类型。
        </p>
        <button
          onClick={() => setShowLink(v => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm hover:bg-gray-50 flex-shrink-0"
        >
          <Plus size={14} />
          关联数据集
        </button>
      </div>

      {showLink && (
        <LinkDatasetPanel ontologyId={ontologyId} objectTypes={objectTypes as FormalObjectType[]} onDone={handleLinkDone} />
      )}

      {(mappings as Mapping[]).length === 0 && !showLink ? (
        <div className="border-2 border-dashed rounded-xl py-16 text-center text-gray-400">
          <Database size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">尚未关联任何 Curated Dataset</p>
          <p className="text-xs mt-1">点击右上角"关联数据集"，选择数据并绑定要灌入的对象实体</p>
        </div>
      ) : (
        <div className="space-y-2">
          {(mappings as Mapping[]).map(m => (
            <MappingRow key={m.id} mapping={m} ontologyId={ontologyId}
              objectTypes={objectTypes as FormalObjectType[]} onChanged={handleChanged} />
          ))}
        </div>
      )}
    </div>
  )
}
