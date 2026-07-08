import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import { Boxes, Loader2 } from 'lucide-react'

/* 实例浏览（正规本体）：图谱编辑器同一份运行时数据。
   带类型过滤 + 来源徽章；每行是投影，全部历史在事实流里。 */

interface OT { id: string; name: string; displayName: string; primaryKey?: string | null
  properties: { id: string; name: string; source?: string }[] }
interface INST { id: string; objectTypeId: string; properties: Record<string, unknown>
  computed?: Record<string, unknown>; source?: string; externalId?: string | null; updatedAt?: string }

const SOURCE_CLS: Record<string, string> = {
  manual: 'bg-amber-50 text-amber-600 border-amber-200',
  pipeline: 'bg-blue-50 text-blue-600 border-blue-200',
  collector: 'bg-teal-50 text-teal-600 border-teal-200',
  action: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  import: 'bg-gray-50 text-gray-500 border-gray-200',
}
const SOURCE_LABEL: Record<string, string> = {
  manual: '手工', pipeline: '管道', collector: '采集', action: '动作', import: '导入',
}

export default function FormalInstancesView({ ontologyId }: { ontologyId: string }) {
  const [typeId, setTypeId] = useState('')
  const base = `/formal/ontologies/${ontologyId}`

  const { data: ots = [] } = useQuery<OT[]>({
    queryKey: ['fi-ot', ontologyId],
    queryFn: () => apiClientV2.get(`${base}/object-types`) as any,
  })
  const { data: insts = [], isLoading } = useQuery<INST[]>({
    queryKey: ['fi-inst', ontologyId, typeId],
    queryFn: () => apiClientV2.get(`${base}/instances${typeId ? `?object_type_id=${typeId}` : ''}`) as any,
  })

  const activeType = ots.find(o => o.id === typeId) || null
  // 列：选中类型时用其属性（前 6 个 stored）；未选时通用（名称+类型）
  const cols = useMemo(() => {
    if (!activeType) return []
    return activeType.properties.filter(p => p.source !== 'computed').slice(0, 6).map(p => p.name)
  }, [activeType])
  const otName = (id: string) => {
    const o = ots.find(x => x.id === id)
    return o ? (o.displayName || o.name) : id.slice(0, 8)
  }
  const displayName = (i: INST) => {
    const p = i.properties || {}
    return String(p.name ?? p.flight_no ?? p.id ?? Object.values(p)[0] ?? i.id.slice(0, 8))
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={() => setTypeId('')}
          className={`px-2.5 py-1 rounded-full text-xs border ${!typeId ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-gray-200 text-gray-500 hover:text-gray-700'}`}>
          全部（{insts.length}）
        </button>
        {ots.map(ot => (
          <button key={ot.id} onClick={() => setTypeId(ot.id)}
            className={`px-2.5 py-1 rounded-full text-xs border ${typeId === ot.id ? 'bg-blue-50 border-blue-300 text-blue-700' : 'border-gray-200 text-gray-500 hover:text-gray-700'}`}>
            {ot.displayName || ot.name}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-10 text-gray-400"><Loader2 className="animate-spin" size={18} /></div>
      ) : insts.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl py-14 text-center text-gray-400">
          <Boxes size={30} className="mx-auto mb-2 opacity-30" />
          <p className="text-sm">还没有实例数据</p>
          <p className="text-xs mt-1">到「数据映射」灌入 curated 数据，或在图谱编辑器手工录入</p>
        </div>
      ) : (
        <div className="rounded-xl border bg-white overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-gray-50/60 text-gray-500">
                <th className="text-left px-3 py-2 font-medium">实例</th>
                {!activeType && <th className="text-left px-3 py-2 font-medium">类型</th>}
                {cols.map(c => <th key={c} className="text-left px-3 py-2 font-medium font-mono">{c}</th>)}
                <th className="text-left px-3 py-2 font-medium">来源</th>
                <th className="text-left px-3 py-2 font-medium">更新时间</th>
              </tr>
            </thead>
            <tbody>
              {insts.slice(0, 200).map(i => (
                <tr key={i.id} className="border-b border-gray-50 hover:bg-gray-50/50">
                  <td className="px-3 py-2 text-gray-800 font-medium max-w-[160px] truncate">{displayName(i)}</td>
                  {!activeType && <td className="px-3 py-2 text-gray-500">{otName(i.objectTypeId)}</td>}
                  {cols.map(c => (
                    <td key={c} className="px-3 py-2 text-gray-600 font-mono max-w-[140px] truncate">
                      {i.properties?.[c] === undefined || i.properties?.[c] === null ? '—' : String(i.properties[c])}
                    </td>
                  ))}
                  <td className="px-3 py-2">
                    <span className={`px-1.5 py-0.5 rounded border text-[10px] ${SOURCE_CLS[i.source || 'manual'] ?? SOURCE_CLS.import}`}
                      title={i.externalId ? `外部ID: ${i.externalId}` : undefined}>
                      {SOURCE_LABEL[i.source || 'manual'] ?? i.source}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-400">
                    {i.updatedAt ? new Date(i.updatedAt).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {insts.length > 200 && (
            <p className="text-center text-[11px] text-gray-400 py-2">仅显示前 200 条（共 {insts.length}）</p>
          )}
        </div>
      )}
      <p className="text-[11px] text-gray-400 px-1">
        表格是当前态投影；每个属性的完整变更历史（谁改的、因何而改、可回放）在图谱编辑器的「属性溯源」抽屉里。
      </p>
    </div>
  )
}
