// 工单域共享的展示元件：状态徽章。列表页与详情抽屉共用，保持唯一事实源。
import { TICKET_STATUS_META, type TicketStatus } from '@/api/tickets'

export function StatusBadge({ status }: { status: TicketStatus }) {
  const meta = TICKET_STATUS_META[status] ?? TICKET_STATUS_META.pending
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium ${meta.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  )
}
