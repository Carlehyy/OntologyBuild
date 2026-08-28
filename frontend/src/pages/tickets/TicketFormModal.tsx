import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Megaphone, Paperclip, Trash2, Upload,
} from 'lucide-react'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { ticketsApi, formatBytes, type TicketItem } from '@/api/tickets'

const MAX_ATTACHMENT_MB = 200
const MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024

function fileIdentity(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function errorDetail(cause: any): string {
  if (typeof cause === 'string') return cause
  return cause?.detail || cause?.message || '请稍后重试'
}

export default function TicketFormModal({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [uploadedFileKeys, setUploadedFileKeys] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    setFiles([])
    setUploadedFileKeys(new Set())
    setTitle('')
    setContent('')
  }, [open])

  const mutation = useMutation({
    mutationFn: async (): Promise<TicketItem> => {
      // 一次报齐全部缺失必填项，避免用户多次试错（对齐事件登记表单）
      const missing: string[] = []
      if (!title.trim()) missing.push('工单标题')
      if (!content.trim()) missing.push('反馈内容')
      if (missing.length) throw new Error(`请完善必填项：${missing.join('、')}`)
      const oversized = files.find(file => file.size > MAX_ATTACHMENT_BYTES)
      if (oversized) throw new Error(`附件“${oversized.name}”超过单文件 ${MAX_ATTACHMENT_MB}MB 限制`)

      const ticket = await ticketsApi.create({ title: title.trim(), content: content.trim() })
      for (const file of files) {
        try {
          await ticketsApi.uploadAttachment(ticket.id, file)
          setUploadedFileKeys(current => new Set(current).add(fileIdentity(file)))
        } catch (cause) {
          throw new Error(
            `工单已提交，但附件“${file.name}”上传失败：${errorDetail(cause)}。可在工单详情中重试或补充。`,
            { cause },
          )
        }
      }
      return ticket
    },
    onSuccess: (ticket) => {
      queryClient.invalidateQueries({ queryKey: ['tickets'] })
      toast({
        tone: 'success',
        title: '工单提交成功',
        description: `工单编号 ${ticket.ticketNo}，当前状态「待处理」`,
      })
      onClose()
    },
    onError: (cause: any) => {
      setError(cause?.message || errorDetail(cause))
      void queryClient.invalidateQueries({ queryKey: ['tickets'] })
    },
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
      title="提交工单"
      description="反馈遇到的 Bug 或不好用的地方，提交后自动进入「待处理」"
      size="2xl"
      headerIcon={<Megaphone size={19} className="text-emerald-600" />}
      disableClose={mutation.isPending}
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
            提交工单
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
          <label htmlFor="ticket-title" className={labelClass}>工单标题 <span className="text-red-500">*</span></label>
          <input
            id="ticket-title"
            required
            aria-required="true"
            value={title}
            onChange={event => setTitle(event.target.value)}
            placeholder="一句话概括你遇到的问题"
            className={controlClass}
          />
        </div>

        <div>
          <label htmlFor="ticket-content" className={labelClass}>反馈内容 <span className="text-red-500">*</span></label>
          <textarea
            id="ticket-content"
            required
            aria-required="true"
            value={content}
            onChange={event => setContent(event.target.value)}
            rows={5}
            className="w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm leading-6 text-slate-700 shadow-sm transition-all placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/15"
            placeholder="问题的完整经过、复现步骤、影响范围……"
          />
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <label className="text-sm font-medium text-slate-700">附件（可选，可多选）</label>
              <p className="mt-0.5 text-xs text-slate-400">
                支持截图、文档、日志等文件，单文件不超过 {MAX_ATTACHMENT_MB}MB
              </p>
            </div>
            {files.length > 0 && (
              <span className="text-right text-xs font-medium text-emerald-700">已选 {files.length} 个</span>
            )}
          </div>
          <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-emerald-200 bg-emerald-50/40 px-4 py-4 text-sm font-medium text-emerald-700 transition-all hover:border-emerald-300 hover:bg-emerald-50 focus-within:ring-2 focus-within:ring-emerald-500/20">
            <Upload size={16} /> 选择多个附件
            <input
              type="file"
              multiple
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
