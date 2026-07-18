import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, File, Loader2, Paperclip } from 'lucide-react'
import { eventsApi, formatBytes, type Attachment } from '@/api/events'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function EventAttachmentsModal({
  open,
  eventId,
  onClose,
}: {
  open: boolean
  eventId: string | null
  onClose: () => void
}) {
  const { toast } = useToast()
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const eventQuery = useQuery({
    queryKey: ['event', eventId],
    queryFn: () => eventsApi.get(eventId!),
    enabled: open && Boolean(eventId),
  })
  const attachments = eventQuery.data?.attachments || []

  const download = async (attachment: Attachment) => {
    if (!eventId) return
    setDownloadingId(attachment.id)
    try {
      await eventsApi.downloadAttachment(eventId, attachment)
    } catch (cause: any) {
      toast({
        tone: 'error',
        title: '附件下载失败',
        description: cause?.detail || cause?.message || '请稍后重试',
      })
    } finally {
      setDownloadingId(null)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="事件附件"
      description={eventQuery.data ? `${eventQuery.data.title} · ${eventQuery.data.eventNo}` : '查看并下载事件相关附件'}
      size="lg"
      headerIcon={<Paperclip size={19} className="text-emerald-600" />}
    >
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/70 px-4 py-3">
          <span className="text-sm font-semibold text-slate-700">附件清单</span>
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-700">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            共 <span className="font-semibold tabular-nums">{attachments.length}</span> 个
          </span>
        </div>

        <div className="max-h-[430px] overflow-y-auto">
          {eventQuery.isLoading ? (
            <div className="flex items-center justify-center gap-2 px-6 py-14 text-sm text-slate-400">
              <Loader2 size={17} className="animate-spin" /> 正在加载附件
            </div>
          ) : eventQuery.isError ? (
            <div className="px-6 py-14 text-center text-sm text-red-500">附件清单加载失败，请关闭后重试</div>
          ) : attachments.length === 0 ? (
            <div className="flex flex-col items-center px-6 py-14 text-center">
              <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
                <Paperclip size={20} />
              </span>
              <p className="text-sm font-medium text-slate-600">当前事件没有附件</p>
              <p className="mt-1 text-xs text-slate-400">登记或编辑事件时可以上传多个附件</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {attachments.map(attachment => (
                <div key={attachment.id} className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-slate-50/80">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-700">
                    <File size={17} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-800" title={attachment.filename}>{attachment.filename}</p>
                    <p className="mt-1 text-xs tabular-nums text-slate-400">
                      {formatBytes(attachment.fileSize)} · {formatTime(attachment.createdAt)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void download(attachment)}
                    disabled={downloadingId === attachment.id}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-600 transition-all hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-700 active:scale-[0.98] disabled:opacity-50"
                  >
                    {downloadingId === attachment.id ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    下载
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
