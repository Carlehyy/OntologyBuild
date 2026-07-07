import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/Table'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { Modal } from '@/components/ui/Modal'
import { Input } from '@/components/ui/Input'
import { Play, FileText, Activity, Zap, AlertTriangle, CheckCircle } from 'lucide-react'
import axios from 'axios'

function getToken() { return localStorage.getItem('token') || '' }

export default function InferenceResultsTab({ ontologyId }: { ontologyId: string }) {
  const [showRun, setShowRun] = useState(false)
  const [selectedRun, setSelectedRun] = useState<any>(null)
  const qc = useQueryClient()

  const { data: runsData, isLoading } = useQuery({
    queryKey: ['inference-runs', ontologyId],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/inference-runs`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
  })

  const { data: resultsData } = useQuery({
    queryKey: ['inference-results', selectedRun?.id],
    queryFn: async () => {
      if (!selectedRun) return { data: [] }
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/inference-runs/${selectedRun.id}/results`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
    enabled: !!selectedRun,
  })

  const { data: shadowData } = useQuery({
    queryKey: ['shadow-runs', ontologyId],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/shadow-runs`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
  })

  const { data: firingsData } = useQuery({
    queryKey: ['action-firings', ontologyId],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/action-firings`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
  })

  const runMut = useMutation({
    mutationFn: (body: any) => axios.post(`/api/v2/ontologies/${ontologyId}/inference-runs`, body, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['inference-runs', ontologyId] }); setShowRun(false) },
  })

  const runs = runsData?.data || []
  const results = resultsData?.data || []
  const shadows = shadowData?.data || []
  const firings = firingsData?.data || []

  return (
    <div className="space-y-6">
      {/* Shadow Runs */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-medium flex items-center gap-2"><AlertTriangle size={18} className="text-[var(--color-warning)]" /> 影子试跑</h3>
        </div>
        {shadows.length === 0 ? (
          <EmptyState title="暂无试跑记录" description="在规则页面试跑规则" />
        ) : (
          <Card>
            <Table>
              <TableHeader>
                <TableRow><TableHead>规则</TableHead><TableHead>检查/命中</TableHead><TableHead>质量分</TableHead><TableHead>判定</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {shadows.map((s: any) => (
                  <TableRow key={s.id}>
                    <TableCell className="text-sm">{s.rule_name}</TableCell>
                    <TableCell className="text-xs">{s.total_entities_checked} / {s.entities_matched}</TableCell>
                    <TableCell className="font-mono text-xs">{(s.quality_score * 100).toFixed(1)}%</TableCell>
                    <TableCell><Badge variant={s.verdict === 'pass' ? 'success' : 'danger'}>{s.verdict === 'pass' ? '通过' : '失败'}</Badge></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>

      {/* Inference Runs */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-medium flex items-center gap-2"><Zap size={18} className="text-[var(--color-info)]" /> 推理运行</h3>
          <Button onClick={() => setShowRun(true)} size="sm"><Play size={14} /> 运行推理</Button>
        </div>
        {runs.length === 0 ? (
          <EmptyState title="暂无推理记录" description="运行推理以生成结果" action={<Button size="sm" onClick={() => setShowRun(true)}><Play size={14} /> 运行</Button>} />
        ) : (
          <Card>
            <Table>
              <TableHeader>
                <TableRow><TableHead>名称</TableHead><TableHead>状态</TableHead><TableHead>检查/命中/动作</TableHead><TableHead className="text-right">操作</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r: any) => (
                  <TableRow key={r.id}>
                    <TableCell className="text-sm font-medium">{r.name}</TableCell>
                    <TableCell><Badge variant={r.status === 'completed' ? 'success' : r.status === 'running' ? 'info' : 'warning'}>{r.status}</Badge></TableCell>
                    <TableCell className="text-xs">{r.total_checked} / {r.total_matched} / {r.total_actions_fired}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setSelectedRun(r)}>查看结果</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>

      {/* Action Firings */}
      {firings.length > 0 && (
        <div>
          <h3 className="text-lg font-medium mb-3 flex items-center gap-2"><Activity size={18} className="text-[var(--color-success)]" /> 动作发射</h3>
          <Card>
            <Table>
              <TableHeader>
                <TableRow><TableHead>动作</TableHead><TableHead>类型</TableHead><TableHead>状态</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {firings.map((f: any) => (
                  <TableRow key={f.id}>
                    <TableCell className="text-sm">{f.action_name}</TableCell>
                    <TableCell><Badge variant="outline" className="text-[10px]">{f.action_type}</Badge></TableCell>
                    <TableCell><Badge variant={f.status === 'sent' ? 'success' : 'warning'} className="text-[10px]">{f.status}</Badge></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </div>
      )}

      {/* Results Detail Modal */}
      {selectedRun && (
        <Modal open={!!selectedRun} onClose={() => setSelectedRun(null)} title={`推理结果: ${selectedRun.name}`} size="lg"
          footer={<Button onClick={() => setSelectedRun(null)}>关闭</Button>}>
          {results.length === 0 ? <EmptyState title="暂无结果" /> : (
            <div className="space-y-2 max-h-96 overflow-auto">
              {results.map((r: any) => (
                <Card key={r.id} className="p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{r.entity_name}</span>
                    <div className="flex items-center gap-2">
                      <Badge variant="info" className="text-[10px]">{r.rule_name}</Badge>
                      <span className="text-xs font-mono">{(r.confidence * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  {r.evidence_chain && r.evidence_chain.length > 0 && (
                    <div className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                      证据: {r.evidence_chain.map((e: any) => e.relation_type).join(', ')}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          )}
        </Modal>
      )}

      {/* Run Modal */}
      {showRun && <RunModal onClose={() => setShowRun(false)} onSubmit={(b: any) => runMut.mutate(b)} loading={runMut.isPending} />}
    </div>
  )
}

function RunModal({ onClose, onSubmit, loading }: { onClose: () => void; onSubmit: (b: any) => void; loading: boolean }) {
  const [name, setName] = useState(`推理-${new Date().toLocaleDateString('zh-CN')}`)
  const [desc, setDesc] = useState('')

  return (
    <Modal open onClose={onClose} title="新建推理运行" size="sm"
      footer={<><Button variant="ghost" onClick={onClose}>取消</Button><Button onClick={() => onSubmit({ name, description: desc })} loading={loading}>运行</Button></>}>
      <div className="space-y-3">
        <Input label="名称" value={name} onChange={e => setName(e.target.value)} required />
        <Input label="描述" value={desc} onChange={e => setDesc(e.target.value)} />
      </div>
    </Modal>
  )
}
