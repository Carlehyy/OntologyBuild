import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/Table'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { Modal } from '@/components/ui/Modal'
import { Plus, Trash2, TestTube } from 'lucide-react'
import axios from 'axios'

function getToken() { return localStorage.getItem('token') || '' }

const DATA_TYPES = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'integer', label: '整数' },
  { value: 'boolean', label: '布尔' },
  { value: 'date', label: '日期' },
  { value: 'enum', label: '枚举' },
  { value: 'url', label: 'URL' },
  { value: 'email', label: '邮箱' },
]

export default function AttributeSchemaTab({ ontologyId }: { ontologyId: string }) {
  const [showCreate, setShowCreate] = useState(false)
  const [testSchema, setTestSchema] = useState<any>(null)
  const [testValue, setTestValue] = useState('')
  const [testResult, setTestResult] = useState<any>(null)
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['schemas', ontologyId],
    queryFn: async () => {
      const res = await axios.get(`/api/v2/ontologies/${ontologyId}/attribute-schemas`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      return res.data
    },
  })

  const createMut = useMutation({
    mutationFn: (body: any) => axios.post(`/api/v2/ontologies/${ontologyId}/attribute-schemas`, body, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['schemas', ontologyId] }); setShowCreate(false) },
  })

  const deleteMut = useMutation({
    mutationFn: (sid: string) => axios.delete(`/api/v2/ontologies/${ontologyId}/attribute-schemas/${sid}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schemas', ontologyId] }),
  })

  const validateMut = useMutation({
    mutationFn: ({ sid, value }: { sid: string; value: string }) => axios.post(
      `/api/v2/ontologies/${ontologyId}/attribute-schemas/${sid}/validate`,
      { value },
      { headers: { Authorization: `Bearer ${getToken()}` } },
    ),
    onSuccess: (res) => setTestResult(res.data),
  })

  const schemas = data?.data || []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium">属性定义</h3>
        <Button onClick={() => setShowCreate(true)} size="sm"><Plus size={14} /> 新增属性</Button>
      </div>

      {isLoading ? <LoadingState /> : schemas.length === 0 ? (
        <EmptyState title="暂无属性定义" description="定义结构化的属性类型" action={<Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} /> 新增</Button>} />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>约束</TableHead>
                <TableHead>适用</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {schemas.map((s: any) => (
                <TableRow key={s.id}>
                  <TableCell>
                    <div>
                      <span className="font-medium text-sm">{s.display_name}</span>
                      <span className="text-xs text-[var(--color-text-tertiary)] ml-2">{s.name}</span>
                    </div>
                  </TableCell>
                  <TableCell><Badge variant="info">{s.data_type}</Badge></TableCell>
                  <TableCell className="text-xs text-[var(--color-text-secondary)]">
                    {s.constraints?.required && <Badge variant="danger" className="text-[10px] mr-1">必填</Badge>}
                    {s.constraints?.min !== undefined && `≥${s.constraints.min}`}
                    {s.constraints?.max !== undefined && ` ≤${s.constraints.max}`}
                    {s.constraints?.unit && ` ${s.constraints.unit}`}
                    {s.constraints?.enum && `[${s.constraints.enum.slice(0, 3).join(',')}]`}
                  </TableCell>
                  <TableCell className="text-xs">
                    {(s.applies_to_types || []).map((t: string) => <Badge key={t} variant="secondary" className="text-[10px] mr-1">{t}</Badge>)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button variant="ghost" size="icon-sm" onClick={() => { setTestSchema(s); setTestValue(''); setTestResult(null) }} title="测试">
                        <TestTube size={12} />
                      </Button>
                      <Button variant="ghost" size="icon-sm" onClick={() => deleteMut.mutate(s.id)}>
                        <Trash2 size={12} className="text-[var(--color-danger)]" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {/* Create Modal */}
      {showCreate && <SchemaCreateModal onClose={() => setShowCreate(false)} onSubmit={(b: any) => createMut.mutate(b)} loading={createMut.isPending} />}

      {/* Test Modal */}
      {testSchema && (
        <Modal open={!!testSchema} onClose={() => setTestSchema(null)} title={`测试: ${testSchema.display_name}`} size="sm"
          footer={<><Button variant="ghost" onClick={() => setTestSchema(null)}>关闭</Button>
            <Button onClick={() => validateMut.mutate({ sid: testSchema.id, value: testValue })} loading={validateMut.isPending}>验证</Button></>}>
          <div className="space-y-3">
            <Input label="测试值" value={testValue} onChange={e => setTestValue(e.target.value)} />
            {testResult && (
              <div className={`p-3 rounded-lg text-sm ${testResult.valid ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                {testResult.valid ? '✓ 验证通过' : `✗ ${testResult.errors?.join(', ')}`}
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}

function SchemaCreateModal({ onClose, onSubmit, loading }: { onClose: () => void; onSubmit: (b: any) => void; loading: boolean }) {
  const [name, setName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [dataType, setDataType] = useState('string')
  const [required, setRequired] = useState(false)
  const [unit, setUnit] = useState('')

  const handleSubmit = () => {
    onSubmit({
      name, display_name: displayName, data_type: dataType,
      constraints: { required, unit: unit || undefined },
      applies_to_types: ["企业"],
    })
  }

  return (
    <Modal open onClose={onClose} title="新增属性" size="md"
      footer={<><Button variant="ghost" onClick={onClose}>取消</Button><Button onClick={handleSubmit} loading={loading}>创建</Button></>}>
      <div className="space-y-3">
        <Input label="内部名称" value={name} onChange={e => setName(e.target.value)} required />
        <Input label="显示名称" value={displayName} onChange={e => setDisplayName(e.target.value)} required />
        <Select label="数据类型" options={DATA_TYPES} value={dataType} onChange={(e: any) => setDataType(e.target.value)} />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={required} onChange={e => setRequired(e.target.checked)} />
          必填
        </label>
        <Input label="单位" value={unit} onChange={e => setUnit(e.target.value)} />
      </div>
    </Modal>
  )
}
