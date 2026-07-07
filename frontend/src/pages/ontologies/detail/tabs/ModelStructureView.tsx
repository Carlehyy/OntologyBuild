import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'
import { Box, GitBranch, Bolt, FunctionSquare, ExternalLink, KeyRound, Cpu } from 'lucide-react'

/* 模型结构（只读速览）：正规本体（图谱编辑器同一份数据）。
   编辑请去图谱编辑器——这里回答"模型长什么样、每类有多少数据"。 */

interface OT { id: string; name: string; displayName: string; primaryKey?: string | null
  properties: { id: string; name: string; displayName?: string; type?: string; source?: string; functionId?: string }[] }
interface LT { id: string; name: string; displayName: string; sourceObjectTypeId: string; targetObjectTypeId: string; cardinality: string }
interface ACT { id: string; name: string; displayName: string; objectTypeId: string; requiresApproval?: boolean; rules?: any[] }
interface FN { id: string; name: string; displayName: string; functionType: string; language: string; enabled: boolean }
interface INST { id: string; objectTypeId: string }

export default function ModelStructureView({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const base = `/formal/ontologies/${ontologyId}`
  const { data: ots = [] } = useQuery<OT[]>({ queryKey: ['ms-ot', ontologyId], queryFn: () => apiClientV2.get(`${base}/object-types`) as any })
  const { data: lts = [] } = useQuery<LT[]>({ queryKey: ['ms-lt', ontologyId], queryFn: () => apiClientV2.get(`${base}/link-types`) as any })
  const { data: acts = [] } = useQuery<ACT[]>({ queryKey: ['ms-act', ontologyId], queryFn: () => apiClientV2.get(`${base}/actions`) as any })
  const { data: fns = [] } = useQuery<FN[]>({ queryKey: ['ms-fn', ontologyId], queryFn: () => apiClientV2.get(`${base}/functions`) as any })
  const { data: insts = [] } = useQuery<INST[]>({ queryKey: ['ms-inst', ontologyId], queryFn: () => apiClientV2.get(`${base}/instances`) as any })

  const instCount: Record<string, number> = {}
  for (const i of insts) instCount[i.objectTypeId] = (instCount[i.objectTypeId] || 0) + 1
  const otName = (id: string) => {
    const o = ots.find(x => x.id === id)
    return o ? (o.displayName || o.name) : id.slice(0, 8)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">正规本体的只读速览（与图谱编辑器同一份数据）。要修改请
          <button onClick={() => navigate(`/ontologies/${ontologyId}/graph`)}
            className="text-violet-600 hover:underline font-medium mx-1">打开图谱编辑器</button>。
        </p>
      </div>

      {/* 对象实体 */}
      <div className="rounded-xl border bg-white p-4">
        <div className="flex items-center gap-2 mb-3">
          <Box size={15} className="text-violet-500" />
          <p className="text-sm font-medium text-gray-700">对象实体（{ots.length}）</p>
        </div>
        {ots.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">还没有对象实体——去图谱编辑器创建，或在「关联数据集」由数据生成。</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {ots.map(ot => {
              const pkProp = ot.properties.find(p => p.id === ot.primaryKey || p.name === ot.primaryKey)
              const computed = ot.properties.filter(p => p.source === 'computed')
              return (
                <div key={ot.id} className="rounded-lg border border-gray-200 px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-800">{ot.displayName || ot.name}</span>
                    <span className="text-[10px] text-gray-400 font-mono">{ot.name}</span>
                    <span className="ml-auto text-xs text-gray-500">{instCount[ot.id] || 0} 实例</span>
                  </div>
                  <div className="flex items-center gap-2 mt-1 text-[11px] text-gray-500 flex-wrap">
                    <span>{ot.properties.length} 属性</span>
                    {computed.length > 0 && <span className="text-purple-500 inline-flex items-center gap-0.5"><Cpu size={10} />{computed.length} 派生</span>}
                    {pkProp
                      ? <span className="inline-flex items-center gap-0.5 text-amber-600"><KeyRound size={10} />{pkProp.name}</span>
                      : <span className="text-red-400">无主键</span>}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 实体关系 */}
      <div className="rounded-xl border bg-white p-4">
        <div className="flex items-center gap-2 mb-3">
          <GitBranch size={15} className="text-cyan-500" />
          <p className="text-sm font-medium text-gray-700">实体关系（{lts.length}）</p>
        </div>
        {lts.length === 0 ? (
          <p className="text-xs text-gray-400 py-3 text-center">还没有实体关系。</p>
        ) : (
          <div className="space-y-1">
            {lts.map(lt => (
              <div key={lt.id} className="flex items-center gap-2 text-xs py-1">
                <span className="text-gray-700">{otName(lt.sourceObjectTypeId)}</span>
                <span className="text-cyan-500 font-medium">—{lt.displayName || lt.name}→</span>
                <span className="text-gray-700">{otName(lt.targetObjectTypeId)}</span>
                <span className="ml-auto text-gray-400 font-mono">{lt.cardinality}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 动作与函数 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded-xl border bg-white p-4">
          <div className="flex items-center gap-2 mb-3">
            <Bolt size={15} className="text-amber-500" />
            <p className="text-sm font-medium text-gray-700">可执行动作（{acts.length}）</p>
          </div>
          {acts.length === 0 ? <p className="text-xs text-gray-400 py-2 text-center">暂无</p> : (
            <div className="space-y-1">
              {acts.map(a => (
                <div key={a.id} className="flex items-center gap-2 text-xs py-1">
                  <span className="text-gray-700">{a.displayName || a.name}</span>
                  <span className="text-gray-400">@ {otName(a.objectTypeId)}</span>
                  <span className="text-gray-400">{(a.rules || []).length} 规则</span>
                  {a.requiresApproval && (
                    <span className="ml-auto text-[10px] px-1.5 rounded bg-blue-50 text-blue-600 border border-blue-200">需人工审批</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="rounded-xl border bg-white p-4">
          <div className="flex items-center gap-2 mb-3">
            <FunctionSquare size={15} className="text-emerald-500" />
            <p className="text-sm font-medium text-gray-700">函数（{fns.length}）</p>
          </div>
          {fns.length === 0 ? <p className="text-xs text-gray-400 py-2 text-center">暂无</p> : (
            <div className="space-y-1">
              {fns.map(f => (
                <div key={f.id} className="flex items-center gap-2 text-xs py-1">
                  <span className="text-gray-700">{f.displayName || f.name}</span>
                  <span className="text-gray-400">{f.functionType}</span>
                  <span className={`ml-auto text-[10px] px-1.5 rounded border ${
                    f.language === 'expression'
                      ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                      : 'bg-amber-50 text-amber-600 border-amber-200'
                  }`} title={f.language === 'expression' ? '后端权威执行（派生/校验/哨兵可用）' : '仅前端模拟执行'}>
                    {f.language}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <button onClick={() => navigate(`/ontologies/${ontologyId}/graph`)}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-dashed border-violet-300 text-violet-600 text-sm hover:bg-violet-50 transition-colors">
        <ExternalLink size={14} /> 打开图谱编辑器修改模型
      </button>
    </div>
  )
}
