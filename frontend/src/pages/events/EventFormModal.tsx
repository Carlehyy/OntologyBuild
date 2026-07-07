import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Paperclip, X, AlertTriangle } from 'lucide-react'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { ontologyApi } from '@/api/ontologies'
import { eventsApi, formatBytes, type EventItem, type EventCreateBody } from '@/api/events'

const SEVERITY_OPTIONS = [
  { value: 'info', label: '信息' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
  { value: 'critical', label: '严重' },
]

// 常见事件类型建议（datalist：可选可自定义）
const EVENT_TYPE_SUGGESTIONS = [
  '设备异常', '系统告警', '业务异常', '业务变更', '客户反馈', '客户投诉',
  '数据质量', '流程事件', '交付事件', '供应链事件', '安全事件', '合规事件', '观察记录', '其它',
]

// ISO → datetime-local 输入值（本地时区）
function isoToLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function EventFormModal({
  open, onClose, editing,
}: {
  open: boolean
  onClose: () => void
  editing?: EventItem | null
}) {
  const qc = useQueryClient()
  const isEdit = !!editing

  const [title, setTitle] = useState('')
  const [eventType, setEventType] = useState('')
  const [severity, setSeverity] = useState('info')
  const [occurredAt, setOccurredAt] = useState('')
  const [ontologyId, setOntologyId] = useState('')
  const [subjectRef, setSubjectRef] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [payloadText, setPayloadText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [error, setError] = useState('')

  // 关联本体下拉：拉取本体列表
  const { data: ontoList } = useQuery({
    queryKey: ['events-ontology-options'],
    queryFn: () => ontologyApi.list({ page_size: 100 }),
    enabled: open,
  })
  const ontologyOptions = [
    { value: '', label: '（不关联本体）' },
    ...(ontoList?.items || []).map(o => ({ value: o.id, label: o.name })),
  ]

  useEffect(() => {
    if (!open) return
    setError('')
    setFiles([])
    if (editing) {
      setTitle(editing.title)
      setEventType(editing.eventType || '')
      setSeverity(editing.severity || 'info')
      setOccurredAt(isoToLocalInput(editing.occurredAt))
      setOntologyId(editing.ontologyId || '')
      setSubjectRef(editing.subjectRef || '')
      setDescription(editing.description || '')
      setTags((editing.tags || []).join(', '))
      setPayloadText(
        editing.payload && Object.keys(editing.payload).length
          ? JSON.stringify(editing.payload, null, 2)
          : '',
      )
    } else {
      setTitle(''); setEventType(''); setSeverity('info'); setOccurredAt('')
      setOntologyId(''); setSubjectRef(''); setDescription(''); setTags(''); setPayloadText('')
    }
  }, [open, editing])

  const mutation = useMutation({
    mutationFn: async () => {
      if (!title.trim()) throw new Error('请填写事件标题')
      let payload: Record<string, any> = {}
      if (payloadText.trim()) {
        try {
          payload = JSON.parse(payloadText)
        } catch {
          throw new Error('结构化数据不是合法 JSON')
        }
      }
      const body: EventCreateBody = {
        title: title.trim(),
        eventType: eventType.trim(),
        severity,
        description,
        tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean),
        payload,
        occurredAt: occurredAt ? new Date(occurredAt).toISOString() : null,
        ontologyId: ontologyId || null,
        subjectRef: subjectRef.trim() || null,
      }
      const ev = isEdit
        ? await eventsApi.update(editing!.id, body)
        : await eventsApi.create(body)
      if (!isEdit && files.length) {
        for (const f of files) await eventsApi.uploadAttachment(ev.id, f)
      }
      return ev
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['events'] })
      qc.invalidateQueries({ queryKey: ['event-stats'] })
      onClose()
    },
    onError: (e: any) => setError(e?.message || e?.detail || '保存失败'),
  })

  const labelCls = 'block text-sm font-medium text-[var(--color-text-primary)] mb-1.5'

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? '编辑事件' : '登记事件'}
      description={isEdit ? undefined : '记录一条业务事件，供后续本体优化挖掘'}
      size="2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutation.isPending}>取消</Button>
          <Button onClick={() => mutation.mutate()} loading={mutation.isPending}>
            {isEdit ? '保存' : '登记'}
          </Button>
        </>
      }
    >
      <div className="space-y-4 max-h-[68vh] overflow-y-auto pr-1">
        {error && (
          <div className="flex items-center gap-2 text-sm text-[var(--color-danger)] bg-[var(--color-danger-bg)] rounded-md px-3 py-2">
            <AlertTriangle size={15} /> {error}
          </div>
        )}

        <Input label="事件标题" required value={title} onChange={e => setTitle(e.target.value)}
          placeholder="简要描述发生了什么" />

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>事件类型</label>
            <input list="event-type-options" value={eventType} onChange={e => setEventType(e.target.value)}
              placeholder="选择或输入类型"
              className="flex h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-2 text-sm shadow-sm placeholder:text-[var(--color-text-tertiary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]" />
            <datalist id="event-type-options">
              {EVENT_TYPE_SUGGESTIONS.map(t => <option key={t} value={t} />)}
            </datalist>
          </div>
          <Select label="严重程度" options={SEVERITY_OPTIONS} value={severity}
            onChange={e => setSeverity(e.target.value)} />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Input label="发生时间" type="datetime-local" value={occurredAt}
            onChange={e => setOccurredAt(e.target.value)} />
          <Select label="关联本体（后续挖掘目标）" options={ontologyOptions} value={ontologyId}
            onChange={e => setOntologyId(e.target.value)} />
        </div>

        <Input label="关联对象标识（可选）" value={subjectRef} onChange={e => setSubjectRef(e.target.value)}
          placeholder="事件涉及的业务对象，如：订单#123 / 供应商X / 设备A3" />

        <div>
          <label className={labelCls}>详细描述</label>
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3}
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
            placeholder="事件的完整经过、背景、影响……" />
        </div>

        <Input label="标签（逗号分隔）" value={tags} onChange={e => setTags(e.target.value)}
          placeholder="如：投诉, 交付" />

        <div>
          <label className={labelCls}>结构化数据（JSON，可选）</label>
          <textarea value={payloadText} onChange={e => setPayloadText(e.target.value)} rows={4}
            spellCheck={false}
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
            placeholder={'{\n  "region": "华东",\n  "delayDays": 3\n}'} />
        </div>

        {!isEdit && (
          <div>
            <label className={labelCls}>附件（可选）</label>
            <label className="inline-flex items-center gap-2 text-sm px-3 py-1.5 rounded-md border border-dashed border-[var(--color-border)] cursor-pointer hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
              <Paperclip size={14} /> 选择文件
              <input type="file" multiple className="hidden"
                onChange={e => setFiles(prev => [...prev, ...Array.from(e.target.files || [])])} />
            </label>
            {files.length > 0 && (
              <div className="mt-2 space-y-1">
                {files.map((f, i) => (
                  <div key={i} className="flex items-center justify-between text-xs bg-[var(--color-bg-hover)] rounded px-2 py-1">
                    <span className="truncate">{f.name} · {formatBytes(f.size)}</span>
                    <button onClick={() => setFiles(files.filter((_, j) => j !== i))}
                      className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)]">
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}
