/* 待审批详情弹窗(shadcn Dialog):
   点击待审批条目或链路上的待审批节点打开,
   完整呈现「起因 → 判定 → 后果」,底部直接裁决;
   批准/拒绝仍走既有确认弹窗与决策协议,留痕不变。 */
import { HandMetal } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import type {
  FiringLike, SentinelLike, WorkspaceActionLike,
} from './storyModel'
import PendingStoryChapters from './PendingStoryChapters'
import type { PendingLog } from './PendingStoryList'

export default function PendingStoryDialog({
  ontologyId,
  target,
  firing,
  sentinel,
  actionDef,
  objectTypeName,
  canDecide,
  busy,
  onClose,
  onApprove,
  onReject,
}: {
  ontologyId: string
  target: PendingLog | null
  firing: FiringLike | null
  sentinel: SentinelLike | null
  actionDef: WorkspaceActionLike | null
  objectTypeName: (objectTypeId: string) => string
  canDecide: boolean
  busy: boolean
  onClose: () => void
  onApprove: (log: PendingLog) => void
  onReject: (log: PendingLog) => void
}) {
  return (
    <Dialog open={Boolean(target)} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="flex max-h-[86vh] w-[min(94vw,46rem)] flex-col">
        <DialogHeader>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
            <HandMetal size={19} />
          </span>
          <div>
            <DialogTitle>
              {target ? `待审批详情：${target.actionName || target.actionId}` : '待审批详情'}
            </DialogTitle>
            <DialogDescription>
              「起因 → 判定 → 后果」看明白再裁决;批准/拒绝都会写入事实流留痕。
            </DialogDescription>
          </div>
        </DialogHeader>
        {target && (
          <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-3.5">
            <PendingStoryChapters
              ontologyId={ontologyId}
              log={target}
              firing={firing}
              sentinel={sentinel}
              actionDef={actionDef}
              objectTypeName={objectTypeName}
              active={Boolean(target)}
            />
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            关闭
          </Button>
          {target && (
            <>
              <Button
                type="button"
                variant="danger"
                onClick={() => onReject(target)}
                disabled={busy || !canDecide}
                title={canDecide ? undefined : '仅管理员可执行审批'}
              >
                拒绝
              </Button>
              <Button
                type="button"
                variant="success"
                onClick={() => onApprove(target)}
                loading={busy}
                disabled={!canDecide}
                title={canDecide ? undefined : '仅管理员可执行审批'}
                className="shadow-sm shadow-emerald-900/10"
              >
                批准并执行
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
