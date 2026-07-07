import { createPortal } from 'react-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  X, Paperclip, Download, Trash2, Pencil, Archive, ArchiveRestore,
  Clock, User, Globe, Hash, FileText, History, Plug,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { LoadingState } from '@/components/ui/LoadingState'
import {
  eventsApi, formatBytes, SEVERITY_META, AUDIT_ACTION_LABEL,
  type EventItem, type Attachment, type AuditEntry,
} from '@/api/events'

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function Field({ icon, label, children }: { icon?: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] mb-0.5">
        {icon}{label}
      </div>
      <div className="text-sm text-[var(--color-text-primary)] break-words">{children}</div>
    </div>
  )
}

export default function EventDetailDrawer({
  open, eventId, onClose, onEdit,
}: {
  open: boolean
  eventId: string | null
  onClose: () => void
  onEdit: (ev: EventItem) => void
}) {
  const qc = useQueryClient()
  const { data: ev, isLoading } = useQuery<EventItem>({
    queryKey: ['event', eventId],
    queryFn: () => eventsApi.get(eventId!),
    enabled: open && !!eventId,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['event', eventId] })
    qc.invalidateQueries({ queryKey: ['events'] })
    qc.invalidateQueries({ queryKey: ['event-stats'] })
  }

  const statusMut = useMutation({
    mutationFn: (status: string) => eventsApi.changeStatus(eventId!, status),
    onSuccess: refresh,
  })
  const uploadMut = useMutation({
    mutationFn: (file: File) => eventsApi.uploadAttachment(eventId!, file),
    onSuccess: refresh,
  })
  const delAttMut = useMutation({
    mutationFn: (attId: string) => eventsApi.deleteAttachment(eventId!, attId),
    onSuccess: refresh,
  })

  if (!open) return null

  const sev = ev ? (SEVERITY_META[ev.severity] || SEVERITY_META.info) : null
  const isApi = ev?.sourceType === 'api'

  return createPortal(
    <div className="fixed inset-0 z-[80] flex justify-end">
      <div className="absolute inset-0 bg-[var(--color-bg-overlay)]" onClick={onClose} />
      <div className="relative w-full max-w-2xl bg-white shadow-xl border-l border-[var(--color-border)] flex flex-col anim-drawer-in">
        {/* 头部 */}
        <div className="shrink-0 px-6 py-4 border-b border-[var(--color-border)] flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-mono text-[var(--color-text-tertiary)]">{ev?.eventNo}</span>
              {ev && (
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                  isApi ? 'bg-[var(--color-primary-light)] text-[var(--color-primary)]' : 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
                }`}>
                  {isApi ? <Plug size={11} /> : <User size={11} />}{ev.sourceLabel}
                </span>
              )}
              {sev && <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${sev.cls}`}>{sev.label}</span>}
              {ev?.status === 'archived' && (
                <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-[var(--color-warning-bg)] text-[var(--color-warning)]">已归档</span>
              )}
            </div>
            <h2 className="text-lg font-semibold text-[var(--color-text-primary)] truncate">{ev?.title}</h2>
          </div>
          <button onClick={onClose} className="shrink-0 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]">
            <X size={20} />
          </button>
        </div>

        {/* 操作条 */}
        {ev && (
          <div className="shrink-0 px-6 py-2.5 border-b border-[var(--color-border)] flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => onEdit(ev)}>
              <Pencil size={13} /> 编辑
            </Button>
            {ev.status === 'active' ? (
              <Button size="sm" variant="ghost" onClick={() => statusMut.mutate('archived')} loading={statusMut.isPending}>
                <Archive size={13} /> 归档
              </Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => statusMut.mutate('active')} loading={statusMut.isPending}>
                <ArchiveRestore size={13} /> 恢复
              </Button>
            )}
          </div>
        )}

        {/* 主体 */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {isLoading || !ev ? (
            <LoadingState />
          ) : (
            <>
              {/* 基本信息 */}
              <section className="grid grid-cols-2 gap-x-6 gap-y-4">
                <Field icon={<FileText size={12} />} label="事件类型">{ev.eventType || '—'}</Field>
                <Field icon={<Clock size={12} />} label="发生时间">{fmt(ev.occurredAt)}</Field>
                <Field icon={<Clock size={12} />} label="登记时间">{fmt(ev.recordedAt)}</Field>
                <Field icon={<User size={12} />} label="上报人 / 来源">
                  {ev.reporterName || '—'}
                  <span className="text-xs text-[var(--color-text-tertiary)] ml-1">({ev.reporterType})</span>
                </Field>
                {isApi && (
                  <>
                    <Field icon={<Plug size={12} />} label="来源系统">{ev.sourceSystem || '—'}</Field>
                    <Field icon={<Hash size={12} />} label="外部单号">{ev.sourceRef || '—'}</Field>
                    <Field icon={<Globe size={12} />} label="来源 IP">{ev.clientIp || '—'}</Field>
                  </>
                )}
                {ev.subjectRef && <Field icon={<Hash size={12} />} label="关联对象">{ev.subjectRef}</Field>}
                {ev.confidence != null && <Field label="置信度">{ev.confidence}</Field>}
                {ev.tags?.length > 0 && (
                  <div className="col-span-2">
                    <div className="text-xs text-[var(--color-text-tertiary)] mb-1">标签</div>
                    <div className="flex flex-wrap gap-1.5">
                      {ev.tags.map(t => (
                        <span key={t} className="px-2 py-0.5 rounded-full text-xs bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">{t}</span>
                      ))}
                    </div>
                  </div>
                )}
              </section>

              {ev.description && (
                <section>
                  <h4 className="text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide mb-2">详细描述</h4>
                  <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">{ev.description}</p>
                </section>
              )}

              {ev.payload && Object.keys(ev.payload).length > 0 && (
                <section>
                  <h4 className="text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide mb-2">结构化数据</h4>
                  <pre className="text-xs font-mono bg-[var(--color-bg-base)] border border-[var(--color-border)] rounded-md p-3 overflow-x-auto">
                    {JSON.stringify(ev.payload, null, 2)}
                  </pre>
                </section>
              )}

              {/* 附件 */}
              <section>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide">
                    附件 {ev.attachments?.length ? `(${ev.attachments.length})` : ''}
                  </h4>
                  <label className="inline-flex items-center gap-1 text-xs text-[var(--color-primary)] cursor-pointer hover:underline">
                    <Paperclip size={12} /> 上传
                    <input type="file" className="hidden"
                      onChange={e => { const f = e.target.files?.[0]; if (f) uploadMut.mutate(f); e.currentTarget.value = '' }} />
                  </label>
                </div>
                {ev.attachments?.length ? (
                  <div className="space-y-1.5">
                    {ev.attachments.map((a: Attachment) => (
                      <div key={a.id} className="flex items-center gap-2 text-sm bg-[var(--color-bg-hover)] rounded-md px-3 py-2">
                        <Paperclip size={14} className="text-[var(--color-text-tertiary)] shrink-0" />
                        <span className="truncate flex-1">{a.filename}</span>
                        <span className="text-xs text-[var(--color-text-tertiary)]">{formatBytes(a.fileSize)}</span>
                        <button onClick={() => eventsApi.downloadAttachment(ev.id, a)}
                          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-primary)]" title="下载">
                          <Download size={14} />
                        </button>
                        <button onClick={() => delAttMut.mutate(a.id)}
                          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)]" title="删除">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-[var(--color-text-tertiary)]">暂无附件</p>
                )}
              </section>

              {/* 审计轨迹 */}
              <section>
                <h4 className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-tertiary)] uppercase tracking-wide mb-3">
                  <History size={13} /> 审计轨迹（溯源）
                </h4>
                <div className="space-y-0">
                  {(ev.auditTrail || []).map((a: AuditEntry, i: number) => (
                    <div key={a.id} className="flex gap-3">
                      <div className="flex flex-col items-center">
                        <div className="w-2 h-2 rounded-full bg-[var(--color-primary)] mt-1.5" />
                        {i < (ev.auditTrail!.length - 1) && <div className="w-px flex-1 bg-[var(--color-border)]" />}
                      </div>
                      <div className="pb-4 min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-[var(--color-text-primary)]">
                            {AUDIT_ACTION_LABEL[a.action] || a.action}
                          </span>
                          <span className="text-xs text-[var(--color-text-tertiary)]">
                            {a.actorName || a.actorType} · {fmt(a.createdAt)}
                          </span>
                          {a.ip && <span className="text-xs text-[var(--color-text-tertiary)]">IP {a.ip}</span>}
                        </div>
                        {a.note && <div className="text-xs text-[var(--color-text-secondary)] mt-0.5">{a.note}</div>}
                        {a.changes && (
                          <div className="mt-1 space-y-0.5">
                            {Object.entries(a.changes).map(([field, v]) => (
                              <div key={field} className="text-xs text-[var(--color-text-tertiary)] font-mono">
                                {field}: <span className="line-through opacity-70">{JSON.stringify(v.from)}</span>
                                {' → '}<span className="text-[var(--color-text-secondary)]">{JSON.stringify(v.to)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
