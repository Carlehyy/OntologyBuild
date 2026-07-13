import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle, ArrowRight, Boxes, CheckCircle2, CircleDot, Database,
  GitBranch, Layers3, Loader2, Settings2, Table2,
} from 'lucide-react'
import {
  linkMappingForType, mappingTargetId, userFieldMapping, useMappingData,
  type MappingLinkType, type MappingObjectType,
} from './mapping-data'
import './mapping-overview.css'

type TargetSelection = { kind: 'object'; id: string } | { kind: 'relation'; id: string }

function PercentRing({ value, tone = 'teal' }: { value: number; tone?: 'teal' | 'indigo' | 'amber' }) {
  return (
    <div className={`dmo-ring dmo-ring--${tone}`} style={{ '--dmo-progress': `${value * 3.6}deg` } as React.CSSProperties}>
      <strong>{value}%</strong>
    </div>
  )
}

function MiniDonut({ value, label, tone }: { value: number; label: string; tone: 'teal' | 'indigo' | 'amber' }) {
  return (
    <div className="dmo-analysis-donut">
      <PercentRing value={value} tone={tone} />
      <div><strong>{label}</strong><span>{value === 100 ? '已全部完成' : `仍有 ${100 - value}% 待完善`}</span></div>
    </div>
  )
}

export default function DataMappingOverview({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const data = useMappingData(ontologyId, true)
  const [selected, setSelected] = useState<TargetSelection | null>(null)
  const [datasetSearch, setDatasetSearch] = useState('')
  const [targetKind, setTargetKind] = useState<'object' | 'relation'>('object')

  const objectMapping = (id: string) => data.mappings.find(mapping => mappingTargetId(mapping) === id)
  const selectedObject = selected?.kind === 'object' ? data.objectTypes.find(item => item.id === selected.id) : undefined
  const selectedRelation = selected?.kind === 'relation' ? data.linkTypes.find(item => item.id === selected.id) : undefined
  const selectedObjectMapping = selectedObject ? objectMapping(selectedObject.id) : undefined
  const selectedLinkMapping = selectedRelation ? linkMappingForType(selectedRelation, data.linkMappings) : undefined
  const selectedDatasetIds = selectedObjectMapping?.curated_dataset_id
    ? [selectedObjectMapping.curated_dataset_id]
    : selectedLinkMapping
      ? [selectedLinkMapping.edge_dataset_id, selectedLinkMapping.src_dataset_id, selectedLinkMapping.tgt_dataset_id].filter(Boolean) as string[]
      : []
  const selectedDatasets = data.datasets.filter(dataset => selectedDatasetIds.includes(dataset.id))

  const objectInstanceCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const instance of data.objectInstances) counts.set(instance.objectTypeId, (counts.get(instance.objectTypeId) || 0) + 1)
    return counts
  }, [data.objectInstances])
  const linkInstanceCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const instance of data.linkInstances) counts.set(instance.linkTypeId, (counts.get(instance.linkTypeId) || 0) + 1)
    return counts
  }, [data.linkInstances])

  const mappedObjects = data.objectTypes.filter(item => objectMapping(item.id)).length
  const mappedRelations = data.linkTypes.filter(item => linkMappingForType(item, data.linkMappings)).length
  const objectCoverage = data.objectTypes.length ? Math.round(mappedObjects / data.objectTypes.length * 100) : 0
  const relationCoverage = data.linkTypes.length ? Math.round(mappedRelations / data.linkTypes.length * 100) : 0
  const totalTargetFields = data.objectTypes.reduce((sum, item) => sum + item.properties.filter(prop => prop.source !== 'computed').length, 0)
    + data.linkTypes.reduce((sum, item) => sum + (item.properties || []).filter(prop => prop.source !== 'computed').length + 2, 0)
  const mappedFields = data.mappings.reduce((sum, mapping) => sum + Object.keys(userFieldMapping(mapping)).length, 0)
    + data.linkMappings.reduce((sum, mapping) => sum + Object.keys(mapping.field_mapping || {}).length + 2, 0)
  const fieldCoverage = totalTargetFields ? Math.min(100, Math.round(mappedFields / totalTargetFields * 100)) : 0
  const objectWithData = data.objectTypes.filter(item => (objectInstanceCounts.get(item.id) || 0) > 0).length
  const relationWithData = data.linkTypes.filter(item => (linkInstanceCounts.get(item.id) || 0) > 0).length
  const objectCollection = data.objectTypes.length ? Math.round(objectWithData / data.objectTypes.length * 100) : 0
  const relationCollection = data.linkTypes.length ? Math.round(relationWithData / data.linkTypes.length * 100) : 0
  const averageQuality = data.datasets.filter(item => item.quality != null).length
    ? Math.round(data.datasets.filter(item => item.quality != null).reduce((sum, item) => {
      const quality = Number(item.quality)
      return sum + (quality <= 1 ? quality * 100 : quality)
    }, 0) / data.datasets.filter(item => item.quality != null).length)
    : fieldCoverage

  if (data.isLoading) {
    return <div className="dmo-loading"><Loader2 className="animate-spin" size={20} />正在整理映射状态…</div>
  }
  if (data.isError) {
    return <div className="dmo-loading dmo-loading--error"><AlertCircle size={20} />映射状态加载失败，请稍后重试。</div>
  }

  const targetItems: Array<MappingObjectType | MappingLinkType> = targetKind === 'object' ? data.objectTypes : data.linkTypes
  const filteredDatasets = data.datasets.filter(item => item.name.toLowerCase().includes(datasetSearch.toLowerCase()))
  const displayTarget = (item: MappingObjectType | MappingLinkType) => item.displayName || item.name

  return (
    <section className="dmo-card">
      <header className="dmo-hero">
        <div>
          <div className="dmo-eyebrow"><Layers3 size={13} /> DATA MAPPING</div>
          <h2>把本体结构，接到真实数据上</h2>
          <p>查看映射覆盖、字段配置与数据采集结果；所有配置在独立工作台中集中维护。</p>
        </div>
        <button className="dmo-primary-button" onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}>
          <Settings2 size={15} />在草稿中配置映射
        </button>
      </header>

      <div className="dmo-workspace">
        <aside className="dmo-pane dmo-sources">
          <div className="dmo-pane__head">
            <div><Database size={15} /><span><b>现有数据源</b><small>已启用的数据资产湖</small></span></div>
            <em>{data.datasets.length}</em>
          </div>
          <label className="dmo-search"><span>⌕</span><input value={datasetSearch} onChange={event => setDatasetSearch(event.target.value)} placeholder="搜索数据集" /></label>
          <div className="dmo-source-list">
            {filteredDatasets.map(dataset => {
              const mappingCount = data.mappings.filter(mapping => mapping.curated_dataset_id === dataset.id).length
                + data.linkMappings.filter(mapping => [mapping.src_dataset_id, mapping.tgt_dataset_id, mapping.edge_dataset_id].includes(dataset.id)).length
              return (
                <div className="dmo-source-item" key={dataset.id}>
                  <span className={`dmo-source-icon dmo-source-icon--${dataset.source}`}><Table2 size={14} /></span>
                  <span><b>{dataset.name}</b><small>{dataset.sourceLabel} · {dataset.rows == null ? '暂无行数' : `${dataset.rows.toLocaleString()} 行`}</small></span>
                  <em data-active={mappingCount > 0}>{mappingCount ? `${mappingCount} 个映射` : '未使用'}</em>
                </div>
              )
            })}
            {filteredDatasets.length === 0 && <p className="dmo-list-empty">没有匹配的数据集</p>}
          </div>
        </aside>

        <main className="dmo-pane dmo-canvas">
          <div className="dmo-pane__head">
            <div><CircleDot size={15} /><span><b>映射画布</b><small>选择本体元素查看当前映射</small></span></div>
            <div className="dmo-segmented">
              <button data-active={targetKind === 'object'} onClick={() => { setTargetKind('object'); setSelected(null) }}>对象实体</button>
              <button data-active={targetKind === 'relation'} onClick={() => { setTargetKind('relation'); setSelected(null) }}>实体关系</button>
            </div>
          </div>
          <div className="dmo-target-strip">
            {targetItems.map(item => {
              const mapped = targetKind === 'object'
                ? Boolean(objectMapping(item.id))
                : Boolean(linkMappingForType(item as MappingLinkType, data.linkMappings))
              const isSelected = selected?.kind === targetKind && selected.id === item.id
              return (
                <button key={item.id} data-selected={isSelected} onClick={() => setSelected({ kind: targetKind, id: item.id })}>
                  {targetKind === 'object' ? <Boxes size={12} /> : <GitBranch size={12} />}
                  <span>{displayTarget(item)}</span><i data-mapped={mapped}>{mapped ? '已映射' : '未映射'}</i>
                </button>
              )
            })}
          </div>
          <div className="dmo-map-stage">
            {!selected ? (
              <div className="dmo-canvas-empty"><Layers3 size={28} /><b>选择一个对象实体或实体关系</b><span>画布将展示当前已建立的数据映射关系</span></div>
            ) : selectedDatasets.length === 0 ? (
              <div className="dmo-canvas-empty dmo-canvas-empty--warning"><AlertCircle size={28} /><b>尚未建立映射</b><span>先创建草稿，再将数据字段连接到草稿本体属性</span><button onClick={() => navigate(`/ontologies/${ontologyId}?tab=versions`)}>创建草稿</button></div>
            ) : (
              <div className="dmo-flow">
                <div className="dmo-flow__sources">
                  {selectedDatasets.map(dataset => <div className="dmo-flow-node dmo-flow-node--source" key={dataset.id}><Database size={16} /><span><b>{dataset.name}</b><small>{dataset.columns.length} 个字段 · {dataset.rows || 0} 行</small></span></div>)}
                </div>
                <div className="dmo-flow__line"><span /><ArrowRight size={18} /></div>
                <div className="dmo-flow-node dmo-flow-node--target">
                  {selected?.kind === 'object' ? <Boxes size={16} /> : <GitBranch size={16} />}
                  <span><b>{selectedObject?.displayName || selectedObject?.name || selectedRelation?.displayName || selectedRelation?.name}</b><small>{selected?.kind === 'object' ? '对象实体' : '实体关系'} · {selected?.kind === 'object' ? `${Object.keys(userFieldMapping(selectedObjectMapping)).length} 个字段映射` : `${Object.keys(selectedLinkMapping?.field_mapping || {}).length + 2} 个字段映射`}</small></span>
                </div>
              </div>
            )}
          </div>
        </main>

        <aside className="dmo-pane dmo-detail">
          <div className="dmo-pane__head"><div><Settings2 size={15} /><span><b>映射详情</b><small>字段覆盖与数据来源</small></span></div></div>
          {!selected ? <div className="dmo-detail-empty"><CircleDot size={24} /><span>点击画布中的本体元素<br />查看映射详情</span></div> : (
            <div className="dmo-detail-body">
              <div className="dmo-detail-title">
                <span className={selectedDatasets.length ? 'is-mapped' : ''}>{selectedDatasets.length ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}</span>
                <div><b>{selectedObject?.displayName || selectedObject?.name || selectedRelation?.displayName || selectedRelation?.name}</b><small>{selectedDatasets.length ? `来源：${selectedDatasets.map(item => item.name).join('、')}` : '尚未配置数据来源'}</small></div>
              </div>
              <div className="dmo-detail-section"><h4>字段映射情况</h4>
                {(selectedObject?.properties || selectedRelation?.properties || []).filter(prop => prop.source !== 'computed').map(prop => {
                  const objectMap = userFieldMapping(selectedObjectMapping)
                  const sourceField = selectedObject
                    ? Object.entries(objectMap).find(([, target]) => target === prop.name)?.[0]
                    : selectedLinkMapping?.field_mapping?.[prop.name]
                  return <div className="dmo-field-row" key={prop.id || prop.name}><span><b>{prop.displayName || prop.name}</b><small>{prop.type || 'string'}</small></span><em data-mapped={Boolean(sourceField)}>{sourceField || '未映射'}</em></div>
                })}
                {selectedRelation && <><div className="dmo-field-row"><span><b>源对象外键</b><small>关系端点</small></span><em data-mapped={Boolean(selectedLinkMapping)}>{selectedLinkMapping?.src_key || '未映射'}</em></div><div className="dmo-field-row"><span><b>目标对象外键</b><small>关系端点</small></span><em data-mapped={Boolean(selectedLinkMapping)}>{selectedLinkMapping?.tgt_key || '未映射'}</em></div></>}
              </div>
            </div>
          )}
        </aside>
      </div>

      <section className="dmo-analysis">
        <div className="dmo-analysis__head"><div><b>映射与采集状态</b><span>映射已建立不等于已经有数据，两个状态分开统计</span></div><em>基于当前映射与实例投影</em></div>
        <div className="dmo-analysis-grid">
          <div className="dmo-analysis-card"><div className="dmo-analysis-card__title"><span><CircleDot size={14} />映射质量分析</span><em>{averageQuality >= 80 ? '良好' : '待完善'}</em></div><MiniDonut value={fieldCoverage} label="字段覆盖率" tone="teal" /><div className="dmo-legend"><span><i className="teal" />已映射 {mappedFields}</span><span><i />未映射 {Math.max(0, totalTargetFields - mappedFields)}</span></div></div>
          <div className="dmo-analysis-card"><div className="dmo-analysis-card__title"><span><Boxes size={14} />对象实体采集状态</span><em>{objectWithData}/{data.objectTypes.length} 有数据</em></div><MiniDonut value={objectCollection} label="已有实例数据" tone="indigo" /><div className="dmo-status-bars"><span><b>映射覆盖</b><i><em style={{ width: `${objectCoverage}%` }} /></i><strong>{objectCoverage}%</strong></span><span><b>数据到达</b><i><em style={{ width: `${objectCollection}%` }} /></i><strong>{objectCollection}%</strong></span></div></div>
          <div className="dmo-analysis-card"><div className="dmo-analysis-card__title"><span><GitBranch size={14} />实体关系采集状态</span><em>{relationWithData}/{data.linkTypes.length} 有数据</em></div><MiniDonut value={relationCollection} label="已有关系数据" tone="amber" /><div className="dmo-status-bars dmo-status-bars--amber"><span><b>映射覆盖</b><i><em style={{ width: `${relationCoverage}%` }} /></i><strong>{relationCoverage}%</strong></span><span><b>数据到达</b><i><em style={{ width: `${relationCollection}%` }} /></i><strong>{relationCollection}%</strong></span></div></div>
        </div>
      </section>
    </section>
  )
}
