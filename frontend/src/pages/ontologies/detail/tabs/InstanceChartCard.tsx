// 图表卡片：概览/画像区块的统一容器（标题 + 副标题 + 右侧扩展位）。
import { Info } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

export default function InstanceChartCard({
  title,
  sub,
  info,
  testId,
  children,
  bodyClassName,
}: {
  title: string
  sub?: string
  /** 有值时在标题旁显示信息图标，hover/点击弹出说明（radix popover 打样应用）。 */
  info?: string
  testId?: string
  children: React.ReactNode
  bodyClassName?: string
}) {
  return (
    <div
      data-testid={testId}
      className="rounded-xl border border-slate-200 bg-white p-4 transition hover:border-teal-200"
    >
      <div className="mb-2 flex items-center gap-1.5">
        <p className="text-[13px] font-semibold text-slate-800">{title}</p>
        {info && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label={`${title}说明`}
                className="inline-flex h-4 w-4 items-center justify-center rounded-full text-slate-300 transition hover:text-teal-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
              >
                <Info size={13} />
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-64 text-xs leading-5">
              {info}
            </PopoverContent>
          </Popover>
        )}
        {sub && <span className="text-[11px] text-slate-400">{sub}</span>}
      </div>
      <div className={bodyClassName}>{children}</div>
    </div>
  )
}
