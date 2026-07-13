import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import {
  CheckCircle, Loader2, GitBranch, Trash2, ExternalLink, Play,
} from 'lucide-react'

/* ============ 类型 ============ */

interface LinkTypeT {
  id: string
  name: string
  displayName: string
  sourceObjectTypeId: string
  targetObjectTypeId: string
  cardinality: string
  properties?: { id: string; name: string; displayName?: string; type?: string }[]
}
interface ObjectTypeT { id: string; name: string; displayName: string }
interface ObjMappingT {
  id: string; curated_dataset_id: string | null; entity_class: string; target_object_type_id: string | null
}
interface CuratedDatasetT { id: string; name: string; status: string; row_count: number | null }
interface ManualDatasetT { id: string; name: string; primary_key: string }
interface LinkMappingRowT {
  id: string; relation_type: string; src_key: string; tgt_key: string
  src_dataset_id: string | null; tgt_dataset_id: string | null
  edge_dataset_id: string | null; field_mapping: Record<string, string>; is_fat: boolean
  auto_apply_on_version: boolean
}

const UNMAPPED = '__unmapped__'

/* ============ 关系映射面板（连接表 → 带属性的边） ============ */

export default function LinkMappingPanel({ ontologyId, onDone }: {
  ontologyId: string; onDone: () => void
}) {
  const [linkTypeId, setLinkTypeId] = useState('')
  const [edgeDatasetId, setEdgeDatasetId] = useState('')
  const [srcDatasetId, setSrcDatasetId] = useState('')
  const [tgtDatasetId, setTgtDatasetId] = useState('')
  const [srcKey, setSrcKey] = useState('')
  const [tgtKey, setTgtKey] = useState('')
  const [propMap, setPropMap] = useState<Record<string, string>>({})  // propName -> edgeCol | UNMAPPED
  const [edgeCols, setEdgeCols] = useState<string[]>([])
  const [loadingCols, setLoadingCols] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [doneMsg, setDoneMsg] = useState<any>(null)
  const [autoApplyOnVersion, setAutoApplyOnVersion] = useState(false)
  const [policySavingId, setPolicySavingId] = useState('')

  const { data: linkTypes = [] } = useQuery<LinkTypeT[]>({
    queryKey: ['formal-link-types', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/link-types`) as any,
  })
  const { data: objectTypes = [] } = useQuery<ObjectTypeT[]>({
    queryKey: ['formal-object-types', ontologyId],
    queryFn: () => apiClientV2.get(`/formal/ontologies/${ontologyId}/object-types`) as any,
  })
  const { data: objMappings = [] } = useQuery<ObjMappingT[]>({
    queryKey: ['mappings', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/mappings`) as any,
  })
  const { data: linkMappings = [], refetch: refetchLinks } = useQuery<LinkMappingRowT[]>({
    queryKey: ['link-mappings', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/link-mappings`) as any,
  })
  const { data: curated = [] } = useQuery<CuratedDatasetT[]>({
    queryKey: ['curated-all'],
    queryFn: () => apiClientV2.get('/curated') as any,
  })
  const { data: manualOverview } = useQuery<{ items: Array<Record<string, unknown>> }>({
    queryKey: ['manual-datasets-overview'],
    queryFn: () => apiClientV2.get('/datasets/overview') as any,
  })

  const approvedDatasets = (curated as CuratedDatasetT[]).filter(d => d.status === 'approved')
  const manualDatasets: ManualDatasetT[] = ((manualOverview?.items ?? []) as Array<Record<string, unknown>>)
    .filter(d => (d.source === 'upload' || d.source === 'manual') && d.primary_key)
    .map(d => ({ id: String(d.id), name: String(d.name), primary_key: String(d.primary_key) }))
  const manualIds = useMemo(() => new Set(manualDatasets.map(d => d.id)), [manualDatasets])
  const datasetName = (id: string | null) =>
    !id ? '—'
      : approvedDatasets.find(d => d.id === id)?.name
      ?? manualDatasets.find(d => d.id === id)?.name ?? id.slice(0, 8)

  const linkType = useMemo(() => linkTypes.find(l => l.id === linkTypeId) || null, [linkTypes, linkTypeId])
  const linkProps = linkType?.properties ?? []
  const otName = (id: string) => objectTypes.find(o => o.id === id)?.displayName
    || objectTypes.find(o => o.id === id)?.name || '未知对象'

  /** 选定关系类型：按两端对象类型自动预解析端点数据集，并重置边属性映射 */
  const handlePickLinkType = (id: string) => {
    setLinkTypeId(id)
    setDoneMsg(null)
    setError('')
    const lt = linkTypes.find(l => l.id === id)
    if (!lt) return
    const srcM = objMappings.find(m => m.target_object_type_id === lt.sourceObjectTypeId)
    const tgtM = objMappings.find(m => m.target_object_type_id === lt.targetObjectTypeId)
    setSrcDatasetId(srcM?.curated_dataset_id || '')
    setTgtDatasetId(tgtM?.curated_dataset_id || '')
    const pm: Record<string, string> = {}
    for (const p of lt.properties ?? []) pm[p.name] = UNMAPPED
    setPropMap(pm)
  }

  /** 选连接表 → 预览列 */
  const handlePickEdge = async (id: string) => {
    setEdgeDatasetId(id)
    setSrcKey(''); setTgtKey('')
    setEdgeCols([])
    setDoneMsg(null); setError('')
    if (!id) return
    setLoadingCols(true)
    try {
      const preview: any = await apiClientV2.get(manualIds.has(id)
        ? `/datasets/${id}/preview?limit=5`
        : `/curated/${id}/preview?limit=5`)
      const cols: string[] = preview.columns?.length
        ? preview.columns
        : (preview.rows?.length > 0 ? Object.keys(preview.rows[0]) : [])
      setEdgeCols(cols)
      // 边属性名与连接表列名重合时自动预映射
      setPropMap(prev => {
        const next = { ...prev }
        for (const p of linkProps) {
          const hit = cols.find(c => c.toLowerCase() === p.name.toLowerCase()
            || c.toLowerCase() === (p.displayName || '').toLowerCase())
          if (hit) next[p.name] = hit
        }
        return next
      })
    } catch (e: any) {
      setError(e?.detail || e?.message || '读取连接表列失败')
    } finally {
      setLoadingCols(false)
    }
  }

  const allDatasets = [
    ...approvedDatasets.map(d => ({ id: d.id, name: d.name, group: '成品数据集' })),
    ...manualDatasets.map(d => ({ id: d.id, name: d.name, group: '人工数据集' })),
  ]

  const canSave = !!linkType && !!edgeDatasetId && !!srcDatasetId && !!tgtDatasetId && !!srcKey && !!tgtKey
    && srcKey !== tgtKey
  const hasManualLinkDependency = [srcDatasetId, tgtDatasetId, edgeDatasetId]
    .some(id => !!id && manualIds.has(id))

  const handleSave = async () => {
    if (!linkType || !canSave) return
    setSaving(true); setError(''); setDoneMsg(null)
    try {
      const fieldMapping: Record<string, string> = {}
      for (const [prop, col] of Object.entries(propMap)) {
        if (col && col !== UNMAPPED) fieldMapping[prop] = col
      }
      await apiClientV2.post(`/ontologies/${ontologyId}/link-mappings`, {
        src_dataset_id: srcDatasetId,
        tgt_dataset_id: tgtDatasetId,
        edge_dataset_id: edgeDatasetId,
        relation_type: linkType.name,
        link_type_id: linkType.id,
        src_key: srcKey,
        tgt_key: tgtKey,
        field_mapping: fieldMapping,
        auto_apply_on_version: autoApplyOnVersion,
      })
      // 关系映射经 build-all 投影成 LinkInstance
      const built: any = await apiClientV2.post(`/ontologies/${ontologyId}/mappings/build-all`)
      setDoneMsg({
        edges: (built?.relations || built?.relation_results || []).reduce?.(
          (n: number, r: any) => n + (r.count || 0), 0) ?? null,
        props: Object.keys(fieldMapping),
      })
      setLinkTypeId(''); setEdgeDatasetId(''); setSrcKey(''); setTgtKey('')
      setEdgeCols([]); setPropMap({}); setAutoApplyOnVersion(false)
      refetchLinks()
      onDone()
    } catch (e: any) {
      setError(e?.detail || e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string, rel: string) => {
    if (!window.confirm(`删除关系映射「${rel}」？\n已投影的关系边与历史事实会保留，只是不再有这条灌入通道。`)) return
    await apiClientV2.delete(`/ontologies/${ontologyId}/link-mappings/${id}`)
    refetchLinks()
  }

  const updateAutomation = async (mapping: LinkMappingRowT, enabled: boolean) => {
    setPolicySavingId(mapping.id); setError('')
    try {
      await apiClientV2.put(
        `/ontologies/${ontologyId}/link-mappings/${mapping.id}/automation`,
        { auto_apply_on_version: enabled },
      )
      await refetchLinks()
    } catch (e: any) {
      setError(e?.detail || e?.message || '更新自动对账策略失败')
    } finally {
      setPolicySavingId('')
    }
  }

  return (
    <div className="border rounded-xl bg-white p-4 space-y-4">
      <div className="flex items-center gap-2">
        <GitBranch size={15} className="text-cyan-600" />
        <p className="text-sm font-medium">关系映射：连接表 → 带属性的关系边</p>
      </div>

      {doneMsg && (
        <div className="flex items-center gap-2 flex-wrap text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <CheckCircle size={13} />
          已灌入关系边{doneMsg.edges != null ? `：${doneMsg.edges} 条` : ''}
          {doneMsg.props?.length ? `，边属性 ${doneMsg.props.join('、')}` : ''}
          <a href={`#/ontologies/${ontologyId}/graph`}
            className="ml-auto inline-flex items-center gap-1 text-green-800 underline hover:no-underline">
            在图谱编辑器查看 <ExternalLink size={11} />
          </a>
        </div>
      )}

      {/* 现有关系映射 */}
      {linkMappings.length > 0 && (
        <div className="space-y-1.5">
          {linkMappings.map(lm => (
            <div key={lm.id}
              className="flex items-center gap-2 text-xs border rounded-lg px-3 py-2 bg-gray-50/60">
              <span className="font-medium text-gray-700">{lm.relation_type}</span>
              {lm.is_fat ? (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border bg-cyan-50 border-cyan-200 text-cyan-700">
                  <GitBranch size={10} /> 连接表 · {Object.keys(lm.field_mapping || {}).length} 个边属性
                </span>
              ) : (
                <span className="px-1.5 py-0.5 rounded border bg-gray-100 border-gray-200 text-gray-500">直连外键</span>
              )}
              <span className="text-gray-400 font-mono">
                {lm.is_fat ? `${datasetName(lm.edge_dataset_id)}[${lm.src_key},${lm.tgt_key}]` : `${lm.src_key}=${lm.tgt_key}`}
              </span>
              {[lm.src_dataset_id, lm.tgt_dataset_id, lm.edge_dataset_id]
                .some(id => !!id && manualIds.has(id)) && (
                <label className="inline-flex items-center gap-1 text-gray-500">
                  <input type="checkbox" checked={lm.auto_apply_on_version}
                    disabled={policySavingId === lm.id}
                    onChange={e => void updateAutomation(lm, e.target.checked)}
                    className="accent-emerald-600" />
                  人工版本后自动对账
                </label>
              )}
              <button onClick={() => handleDelete(lm.id, lm.relation_type)}
                className="ml-auto text-gray-400 hover:text-red-500" title="删除关系映射">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {error && <p className="text-xs text-red-500">{error}</p>}

      {linkTypes.length === 0 ? (
        <p className="text-xs text-gray-400">
          图谱中还没有实体关系。请先在
          <a href={`#/ontologies/${ontologyId}/graph`} className="text-cyan-700 underline mx-1">图谱编辑器</a>
          拖线创建关系、并为它定义边属性，再回这里绑定连接表数据。
        </p>
      ) : (
        <div className="space-y-4">
          {/* ① 选关系类型 */}
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">①</span>
              <label className="text-xs font-medium text-gray-600">要给哪条实体关系灌数据？</label>
            </div>
            <div className="pl-7">
              <select value={linkTypeId} onChange={e => handlePickLinkType(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm">
                <option value="">选择实体关系...</option>
                {linkTypes.map(lt => (
                  <option key={lt.id} value={lt.id}>
                    {lt.displayName || lt.name}（{otName(lt.sourceObjectTypeId)} → {otName(lt.targetObjectTypeId)}）
                  </option>
                ))}
              </select>
              {linkType && (
                <p className="text-[11px] text-gray-400 mt-1">
                  边属性 schema：{linkProps.length
                    ? linkProps.map(p => p.displayName || p.name).join('、')
                    : '（未定义边属性——可只建立连接，或先去图谱编辑器加属性）'}
                </p>
              )}
            </div>
          </div>

          {linkType && (
            <>
              {/* ② 选连接表 */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">②</span>
                  <label className="text-xs font-medium text-gray-600">关系数据来自哪张连接表？</label>
                  {loadingCols && <Loader2 size={12} className="animate-spin text-gray-400" />}
                </div>
                <div className="pl-7">
                  <select value={edgeDatasetId} onChange={e => void handlePickEdge(e.target.value)}
                    className="w-full border rounded-lg px-3 py-2 text-sm">
                    <option value="">选择连接表（含两端外键 + 属性列）...</option>
                    <optgroup label="成品数据集">
                      {approvedDatasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </optgroup>
                    {manualDatasets.length > 0 && (
                      <optgroup label="人工数据集">
                        {manualDatasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                      </optgroup>
                    )}
                  </select>
                </div>
              </div>

              {/* ③ 两端外键列 + 端点数据集 */}
              {edgeCols.length > 0 && hasManualLinkDependency && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">③</span>
                    <label className="text-xs font-medium text-gray-600">连接表的两端外键列</label>
                  </div>
                  <div className="pl-7 space-y-2">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-gray-500 w-24 flex-shrink-0">源 {otName(linkType.sourceObjectTypeId)} FK</span>
                      <select value={srcKey} onChange={e => setSrcKey(e.target.value)}
                        className="border rounded px-2 py-1 text-xs flex-1 text-blue-700">
                        <option value="">选择列...</option>
                        {edgeCols.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <select value={srcDatasetId} onChange={e => setSrcDatasetId(e.target.value)}
                        className="border rounded px-2 py-1 text-xs flex-1">
                        <option value="">源端点数据集...</option>
                        {allDatasets.map(d => <option key={'s' + d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-gray-500 w-24 flex-shrink-0">目标 {otName(linkType.targetObjectTypeId)} FK</span>
                      <select value={tgtKey} onChange={e => setTgtKey(e.target.value)}
                        className="border rounded px-2 py-1 text-xs flex-1 text-blue-700">
                        <option value="">选择列...</option>
                        {edgeCols.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <select value={tgtDatasetId} onChange={e => setTgtDatasetId(e.target.value)}
                        className="border rounded px-2 py-1 text-xs flex-1">
                        <option value="">目标端点数据集...</option>
                        {allDatasets.map(d => <option key={'t' + d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    </div>
                    {srcKey && srcKey === tgtKey && (
                      <p className="text-[11px] text-red-500">两端外键不能是同一列</p>
                    )}
                  </div>
                </div>
              )}
              {edgeCols.length > 0 && (
                <div className="pl-7">
                  <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                    <input type="checkbox" checked={autoApplyOnVersion}
                      onChange={e => setAutoApplyOnVersion(e.target.checked)}
                      className="accent-emerald-600" />
                    人工端点或连接表发布新版本后自动全量对账本体
                  </label>
                </div>
              )}

              {/* ④ 边属性映射 */}
              {edgeCols.length > 0 && linkProps.length > 0 && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400 w-5 text-center flex-shrink-0">④</span>
                    <label className="text-xs font-medium text-gray-600">边属性 ← 连接表列</label>
                  </div>
                  <div className="pl-7 space-y-1">
                    {linkProps.map(p => (
                      <div key={p.id} className="flex items-center gap-2 text-xs">
                        <span className="text-gray-600 w-32 truncate flex-shrink-0" title={p.name}>
                          {p.displayName || p.name}
                          {p.type && <span className="text-gray-400 ml-1">{p.type}</span>}
                        </span>
                        <span className="text-gray-300 flex-shrink-0">←</span>
                        <select value={propMap[p.name] ?? UNMAPPED}
                          onChange={e => setPropMap(m => ({ ...m, [p.name]: e.target.value }))}
                          className={`flex-1 border rounded px-2 py-1 text-xs ${
                            (propMap[p.name] ?? UNMAPPED) === UNMAPPED ? 'text-gray-400' : 'text-cyan-700'}`}>
                          <option value={UNMAPPED}>— 不灌 —</option>
                          {edgeCols.filter(c => c !== srcKey && c !== tgtKey).map(c =>
                            <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="pl-7 flex gap-2">
                <button onClick={() => void handleSave()} disabled={!canSave || saving}
                  className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                  {saving ? '保存并灌入中...' : '保存并灌入关系'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
