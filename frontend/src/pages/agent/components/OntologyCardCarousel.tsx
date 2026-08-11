/**
 * 本体卡片轮播：未选择本体时占据本体助手右侧工作区大区域。
 *
 * Coverflow 交互：
 *   - 鼠标按住左右拖拽浏览，松手吸附到最近卡片；
 *   - 单击侧边卡片仅聚焦居中，单击聚焦卡片（或「开始使用」）才确认选中；
 *   - 左右箭头、指示点与键盘 ←/→/Enter 提供等价操作；
 *   - 卡片顺序由父级按全局选用次数排好（rankOntologyCards），这里只管呈现。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft, ChevronRight, Flame, Network, Sparkles } from 'lucide-react'
import type { OntologyListItem } from '@/types/ontology'
import { OntologyAvatar } from '@/components/OntologyAvatar'
import { rankOntologyCards } from './ontologyCardRanking'

const CARD_WIDTH = 300
const FOCUS_STEP_X = CARD_WIDTH * 0.7
const CLICK_SLOP_PX = 6

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

function cardMetrics(item: OntologyListItem) {
  return [
    { label: '对象实体', value: item.entity_count ?? 0 },
    { label: '实体关系', value: item.relation_count ?? 0 },
    { label: '执行动作', value: item.action_count ?? 0 },
  ]
}

export function OntologyCardCarousel({
  items,
  onSelect,
}: {
  items: OntologyListItem[]
  onSelect: (item: OntologyListItem) => void
}) {
  const navigate = useNavigate()
  const cards = useMemo(() => rankOntologyCards(items), [items])
  const count = cards.length
  const [focus, setFocus] = useState(0)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ startX: 0, startFocus: 0, moved: false })

  const activeIndex = clamp(Math.round(focus), 0, Math.max(count - 1, 0))

  useEffect(() => {
    if (focus > count - 1) setFocus(Math.max(count - 1, 0))
  }, [count, focus])

  const snapTo = useCallback((target: number) => {
    setFocus(clamp(target, 0, Math.max(count - 1, 0)))
  }, [count])

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || count === 0) return
    if ((e.target as Element).closest('button')) return
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { startX: e.clientX, startFocus: focus, moved: false }
    setDragging(true)
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    const deltaX = e.clientX - dragRef.current.startX
    if (Math.abs(deltaX) > CLICK_SLOP_PX) dragRef.current.moved = true
    const raw = dragRef.current.startFocus - deltaX / FOCUS_STEP_X
    // 边缘橡皮筋：越界拖拽施加阻尼，松手后回弹吸附。
    const resisted = raw < 0 ? raw * 0.35 : raw > count - 1 ? count - 1 + (raw - (count - 1)) * 0.35 : raw
    setFocus(resisted)
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
    if (dragRef.current.moved) {
      snapTo(Math.round(focus))
      return
    }
    // 未拖动的按下-抬起视为点击：命中侧边卡片聚焦，命中聚焦卡片确认选中。
    const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-card-index]')
    const hitIndex = hit ? Number((hit as HTMLElement).dataset.cardIndex) : NaN
    if (Number.isNaN(hitIndex)) return
    if (hitIndex === activeIndex) onSelect(cards[hitIndex])
    else snapTo(hitIndex)
  }

  const handlePointerCancel = () => {
    if (!dragging) return
    setDragging(false)
    snapTo(Math.round(focus))
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (count === 0) return
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      snapTo(activeIndex - 1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      snapTo(activeIndex + 1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      onSelect(cards[activeIndex])
    }
  }

  if (count === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-gradient-to-br from-slate-50 via-sky-50/60 to-emerald-50/50 px-6 text-center dark:from-[#121820] dark:via-[#121820] dark:to-[#121820]">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-sky-100 bg-white text-sky-500 shadow-sm">
          <Network size={24} />
        </div>
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">暂无已发布本体</h3>
        <p className="mt-1 max-w-xs text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          先在本体管理中创建并发布一个本体，再回到这里开始智能对话。
        </p>
        <button
          type="button"
          onClick={() => navigate('/ontologies')}
          className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-4 py-2 text-sm font-medium text-teal-600 transition-colors hover:bg-teal-100"
        >
          前往本体管理
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-gradient-to-b from-slate-50/80 via-[#f8fbff] to-sky-50/50 dark:from-[#121820] dark:via-[#121820] dark:to-[#121820]">
      <div className="flex shrink-0 items-end justify-between gap-3 px-6 pb-1 pt-5">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">选择一个本体，开始探索</h3>
          <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
            按住拖拽或点击两侧卡片浏览，点击居中卡片进入本体工作区
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-sky-100 bg-white/80 px-2.5 py-1 text-[11px] font-medium text-sky-600 shadow-sm dark:border-slate-700 dark:bg-slate-800/80 dark:text-sky-300">
          {count} 个已发布本体
        </span>
      </div>

      <div
        data-testid="ontology-card-carousel"
        role="listbox"
        aria-label="已发布本体卡片轮播"
        aria-activedescendant={`ontology-card-${cards[activeIndex]?.id}`}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        className="scrollbar-none relative min-h-0 flex-1 cursor-grab touch-none select-none outline-none focus-visible:ring-2 focus-visible:ring-teal-300 active:cursor-grabbing"
      >
        {cards.map((item, index) => {
          const pos = index - focus
          const absPos = Math.abs(pos)
          const isFocused = index === activeIndex && !dragging
          const hidden = absPos > 2.6
          const clicks = item.assistant_card_clicks ?? 0
          const style: React.CSSProperties = {
            width: CARD_WIDTH,
            left: '50%',
            top: '50%',
            zIndex: 100 - Math.round(absPos * 10),
            opacity: hidden ? 0 : 1 - Math.min(absPos, 2) * 0.26,
            transform: [
              'translate(-50%, -50%)',
              `translateX(${pos * FOCUS_STEP_X}px)`,
              `translateY(${Math.min(absPos, 2) * 10}px)`,
              `perspective(1200px) rotateY(${clamp(-pos * 9, -24, 24)}deg)`,
              `scale(${1 - Math.min(absPos, 2.5) * 0.09})`,
            ].join(' '),
            transition: dragging
              ? 'none'
              : 'transform 0.45s cubic-bezier(0.22, 0.61, 0.36, 1), opacity 0.35s ease',
            pointerEvents: hidden ? 'none' : 'auto',
            visibility: hidden ? 'hidden' : 'visible',
          }
          return (
            <div
              key={item.id}
              id={`ontology-card-${item.id}`}
              data-testid="ontology-card"
              data-card-index={index}
              data-focused={isFocused || undefined}
              role="option"
              aria-selected={index === activeIndex}
              aria-label={`本体卡片 ${item.name}`}
              style={style}
              className={`absolute flex flex-col overflow-hidden rounded-2xl border bg-white shadow-xl motion-reduce:transition-none dark:bg-slate-800 ${
                index === activeIndex
                  ? 'border-teal-300 shadow-2xl ring-2 ring-teal-400/50 dark:border-teal-500'
                  : 'border-slate-200 dark:border-slate-700'
              }`}
            >
              {clicks > 0 && (
                <span
                  title={`已被选用 ${clicks} 次`}
                  className="absolute right-3 top-3 z-10 inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50/95 px-2 py-0.5 text-[10px] font-semibold text-amber-600 shadow-sm"
                >
                  <Flame size={11} />×{clicks}
                </span>
              )}

              <div className="flex items-start gap-3 px-4 pb-3 pt-4">
                <OntologyAvatar icon={item.icon || undefined} size="lg" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[15px] font-semibold text-slate-800 dark:text-slate-100" title={item.name}>
                    {item.name}
                  </div>
                  <div className="mt-1.5 flex min-w-0 items-center gap-1.5">
                    <span className="inline-flex min-w-0 max-w-full truncate rounded-md border border-teal-100 bg-teal-50 px-2 py-0.5 text-[11px] font-medium leading-4 text-teal-700">
                      {item.domain || '未设置领域'}
                    </span>
                    <span className="inline-flex shrink-0 rounded-md border border-violet-100 bg-violet-50 px-2 py-0.5 font-mono text-[11px] font-medium leading-4 text-violet-600">
                      {item.current_release_version || item.version}
                    </span>
                  </div>
                </div>
              </div>

              <p
                className="min-h-[40px] px-4 text-xs leading-5 text-slate-500 dark:text-slate-400"
                style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
                title={item.description || '暂无描述'}
              >
                {item.description || '暂无描述'}
              </p>

              <div className="mt-3 grid grid-cols-3 gap-1.5 px-4">
                {cardMetrics(item).map(metric => (
                  <div key={metric.label} className="min-w-0 rounded-xl bg-slate-50 px-1 py-2 text-center dark:bg-slate-700/60">
                    <p className="whitespace-nowrap text-[10px] font-medium text-slate-400">{metric.label}</p>
                    <p className="mt-0.5 text-base font-semibold tabular-nums text-slate-800 dark:text-slate-100">{metric.value}</p>
                  </div>
                ))}
              </div>

              <div className="mt-auto px-4 pb-4 pt-3">
                <div
                  data-testid={index === activeIndex ? 'ontology-card-confirm' : undefined}
                  className={`flex h-9 items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-all duration-300 ${
                    index === activeIndex
                      ? 'bg-teal-600 text-white shadow-sm opacity-100'
                      : 'bg-slate-100 text-slate-400 opacity-70 dark:bg-slate-700'
                  }`}
                >
                  {index === activeIndex ? (
                    <><Sparkles size={14} />开始使用</>
                  ) : (
                    '点击卡片聚焦'
                  )}
                </div>
              </div>
            </div>
          )
        })}

        {count > 1 && (
          <>
            <button
              type="button"
              aria-label="上一张本体卡片"
              disabled={activeIndex === 0}
              onClick={() => snapTo(activeIndex - 1)}
              className="absolute left-3 top-1/2 z-[110] flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white/90 text-slate-500 shadow-md backdrop-blur transition-all hover:border-teal-300 hover:text-teal-600 disabled:cursor-not-allowed disabled:opacity-30 dark:border-slate-700 dark:bg-slate-800/90"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              type="button"
              aria-label="下一张本体卡片"
              disabled={activeIndex >= count - 1}
              onClick={() => snapTo(activeIndex + 1)}
              className="absolute right-3 top-1/2 z-[110] flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white/90 text-slate-500 shadow-md backdrop-blur transition-all hover:border-teal-300 hover:text-teal-600 disabled:cursor-not-allowed disabled:opacity-30 dark:border-slate-700 dark:bg-slate-800/90"
            >
              <ChevronRight size={16} />
            </button>
          </>
        )}
      </div>

      {count > 1 && (
        <div className="flex shrink-0 items-center justify-center gap-1.5 pb-4 pt-2">
          {cards.map((item, index) => (
            <button
              key={item.id}
              type="button"
              aria-label={`聚焦第 ${index + 1} 张本体卡片`}
              onClick={() => snapTo(index)}
              className={`h-1.5 rounded-full transition-all duration-300 ${
                index === activeIndex
                  ? 'w-5 bg-teal-600'
                  : 'w-1.5 bg-slate-300 hover:bg-slate-400 dark:bg-slate-600'
              }`}
            />
          ))}
        </div>
      )}
    </div>
  )
}
