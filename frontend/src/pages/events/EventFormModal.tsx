import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, FilePlus2, Paperclip, Trash2, Upload } from 'lucide-react'
import { Modal } from '@/components/ui/Modal'
import { ontologyApi } from '@/api/ontologies'
import { eventsApi, formatBytes, type EventCreateBody, type EventItem } from '@/api/events'

const SEVERITY_OPTIONS = [
  { value: 'info', label: '信息' },
  { value: 'low', label: '低级' },
  { value: 'medium', label: '中级' },
  { value: 'high', label: '高级' },
  { value: 'critical', label: '严重' },
]

const EVENT_TYPE_SUGGESTIONS = [
  '设备异常', '系统告警', '业务异常', '业务变更', '客户反馈', '客户投诉',
  '数据质量', '流程事件', '交付事件', '供应链事件', '安全事件', '合规事件', '观察记录', '其它',
]

const MAX_ATTACHMENT_MB = 200
const MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024
const ATTACHMENT_ACCEPT = '.csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.doc,.pptx,.ppt,.md,.txt'

function fileIdentity(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function errorDetail(cause: any): string {
  if (typeof cause === 'string') return cause
  return cause?.detail || cause?.message || '请稍后重试'
}

function isoToLocalInput(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export default function EventFormModal({
  open,
  onClose,
  editing,
}: {
  open: boolean
  onClose: () => void
  editing?: EventItem | null
}) {
  const queryClient = useQueryClient()
  const isEdit = Boolean(editing)
  const [title, setTitle] = useState('')
  const [eventType, setEventType] = useState('')
  const [severity, setSeverity] = useState('info')
  const [occurredAt, setOccurredAt] = useState('')
  const [ontologyId, setOntologyId] = useState('')
  const [description, setDescription] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [createdEventId, setCreatedEventId] = useState<string | null>(null)
  const [uploadedFileKeys, setUploadedFileKeys] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')

  const { data: ontologyList } = useQuery({
    queryKey: ['events-ontology-options'],
    queryFn: () => ontologyApi.list({ page_size: 100 }),
    enabled: open,
  })
  const ontologyOptions = [
    { value: '', label: '（不关联本体）' },
    ...(ontologyList?.items || []).map(ontology => ({ value: ontology.id, label: ontology.name })),
  ]

  useEffect(() => {
    if (!open) return
    setError('')
    setFiles([])
    setCreatedEventId(null)
    setUploadedFileKeys(new Set())
    if (editing) {
      setTitle(editing.title)
      setEventType(editing.eventType || '')
      setSeverity(editing.severity || 'info')
      setOccurredAt(isoToLocalInput(editing.occurredAt))
      setOntologyId(editing.ontologyId || '')
      setDescription(editing.description || '')
      return
    }
    setTitle('')
    setEventType('')
    setSeverity('info')
    setOccurredAt('')
    setOntologyId('')
    setDescription('')
  }, [editing, open])

  const mutation = useMutation({
    mutationFn: async () => {
      if (!title.trim()) throw new Error('请填写事件标题')
      const oversized = files.find(file => file.size > MAX_ATTACHMENT_BYTES)
      if (oversized) throw new Error(`附件“${oversized.name}”超过单文件 ${MAX_ATTACHMENT_MB}MB 限制`)
      const body: EventCreateBody = {
        title: title.trim(),
        eventType: eventType.trim(),
        severity,
        description,
        occurredAt: occurredAt ? new Date(occurredAt).toISOString() : null,
        ontologyId: ontologyId || null,
      }
      let event: EventItem
      if (isEdit) {
        event = await eventsApi.update(editing!.id, body)
      } else if (createdEventId) {
        event = await eventsApi.update(createdEventId, body)
      } else {
        event = await eventsApi.create(body)
        setCreatedEventId(event.id)
      }
      for (const file of files) {
        const key = fileIdentity(file)
        if (uploadedFileKeys.has(key)) continue
        try {
          await eventsApi.uploadAttachment(event.id, file)
          setUploadedFileKeys(current => new Set(current).add(key))
        } catch (cause) {
          throw new Error(
            `事件信息已保存，但附件“${file.name}”上传失败：${errorDetail(cause)}。可移除该文件后重试。`,
            { cause },
          )
        }
      }
      return event
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events'] })
      onClose()
    },
    onError: (cause: any) => setError(cause?.message || cause?.detail || '保存失败'),
  })

  const addFiles = (incoming: File[]) => {
    const oversized = incoming.filter(file => file.size > MAX_ATTACHMENT_BYTES)
    const accepted = incoming.filter(file => file.size <= MAX_ATTACHMENT_BYTES)
    setError(oversized.length
      ? `已忽略 ${oversized.length} 个超过单文件 ${MAX_ATTACHMENT_MB}MB 限制的附件：${oversized.map(file => file.name).join('、')}`
      : '')
    setFiles(current => {
      const known = new Set(current.map(fileIdentity))
      return [...current, ...accepted.filter(file => !known.has(fileIdentity(file)))]
    })
  }

  const labelClass = 'mb-1.5 block text-sm font-medium text-slate-700'
  const controlClass = 'h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 shadow-sm transition-all placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/15'

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? '编辑事件' : '登记事件'}
      description={isEdit ? undefined : '记录一条业务事件，供后续本体优化挖掘'}
      size="2xl"
      headerIcon={<FilePlus2 size={19} className="text-emerald-600" />}
      footer={(
        <div className="flex w-full justify-center gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className="h-10 rounded-lg border border-slate-200 bg-white px-6 text-sm font-medium text-slate-600 transition-all hover:bg-slate-50 active:scale-[0.98] disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="inline-flex h-10 min-w-24 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-6 text-sm font-medium text-white shadow-sm transition-all hover:bg-emerald-700 active:scale-[0.98] disabled:opacity-50"
          >
            {mutation.isPending && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
            {isEdit ? '保存' : '登记'}
          </button>
        </div>
      )}
    >
      <div className="max-h-[68vh] space-y-5 overflow-y-auto px-1 pb-1 pr-2">
        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
            <AlertTriangle size={15} /> {error}
          </div>
        )}

        <div>
          <label className={labelClass}>事件标题 <span className="text-red-500">*</span></label>
          <input value={title} onChange={event => setTitle(event.target.value)} placeholder="简要描述发生了什么" className={controlClass} />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className={labelClass}>事件类型</label>
            <input
              list="event-type-options"
              value={eventType}
              onChange={event => setEventType(event.target.value)}
              placeholder="选择或输入类型"
              className={controlClass}
            />
            <datalist id="event-type-options">
              {EVENT_TYPE_SUGGESTIONS.map(type => <option key={type} value={type} />)}
            </datalist>
          </div>
          <div>
            <label className={labelClass}>严重程度</label>
            <select value={severity} onChange={event => setSeverity(event.target.value)} className={controlClass}>
              {SEVERITY_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className={labelClass}>发生时间</label>
            <input type="datetime-local" value={occurredAt} onChange={event => setOccurredAt(event.target.value)} className={controlClass} />
          </div>
          <div>
            <label className={labelClass}>关联本体（后续挖掘目标）</label>
            <select value={ontologyId} onChange={event => setOntologyId(event.target.value)} className={controlClass}>
              {ontologyOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
        </div>

        <div>
          <label className={labelClass}>详细描述</label>
          <textarea
            value={description}
            onChange={event => setDescription(event.target.value)}
            rows={4}
            className="w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm leading-6 text-slate-700 shadow-sm transition-all placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/15"
            placeholder="事件的完整经过、背景、影响……"
          />
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <label className="text-sm font-medium text-slate-700">附件（可选，可多选）</label>
              <p className="mt-0.5 text-xs text-slate-400">单文件不超过 {MAX_ATTACHMENT_MB}MB</p>
            </div>
            {files.length > 0 && (
              <span className="text-xs font-medium text-emerald-700">
                已选择 {files.length} 个{uploadedFileKeys.size ? ` · 已上传 ${uploadedFileKeys.size} 个` : ''}
              </span>
            )}
          </div>
          <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-emerald-200 bg-emerald-50/40 px-4 py-4 text-sm font-medium text-emerald-700 transition-all hover:border-emerald-300 hover:bg-emerald-50 focus-within:ring-2 focus-within:ring-emerald-500/20">
            <Upload size={16} /> 选择多个附件
            <input
              type="file"
              multiple
              accept={ATTACHMENT_ACCEPT}
              className="sr-only"
              onChange={event => {
                addFiles(Array.from(event.target.files || []))
                event.currentTarget.value = ''
              }}
            />
          </label>
          {files.length > 0 && (
            <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
              {files.map((file, index) => {
                const uploaded = uploadedFileKeys.has(fileIdentity(file))
                return (
                <div key={fileIdentity(file)} className="group flex items-center gap-3 border-t border-slate-100 px-3 py-2.5 first:border-t-0">
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${uploaded ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>
                    {uploaded ? <CheckCircle2 size={16} /> : <Paperclip size={15} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-700" title={file.name}>{file.name}</p>
                    <p className={`mt-0.5 text-xs ${uploaded ? 'text-emerald-600' : 'text-slate-400'}`}>
                      {formatBytes(file.size)}{uploaded ? ' · 已上传，重试时将自动跳过' : ''}
                    </p>
                  </div>
                  {!uploaded && (
                    <button
                      type="button"
                      onClick={() => setFiles(current => current.filter((_, currentIndex) => currentIndex !== index))}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
                      title={`移除 ${file.name}`}
                      aria-label={`移除 ${file.name}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  )}
                </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
