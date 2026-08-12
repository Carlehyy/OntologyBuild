/**
 * 本体卡片轮播：未选择本体时占据本体助手右侧工作区大区域。
 *
 * Coverflow 交互：
 *   - 卡片环形布局：最热卡居中，右侧按选用次数递减，末尾卡环绕到左侧；
 *   - 鼠标按住左右拖拽浏览，松手吸附；滚轮向下等价向右切换、向上等价向左；
 *     >=3 张时可单向无限循环；
 *   - 单击侧边卡片仅聚焦居中，单击聚焦卡片（或「开始使用」）才确认选中；
 *   - 左右箭头、指示点与键盘 ←/→/Enter 提供等价操作；
 *   - 焦点动画由 requestAnimationFrame 逐帧驱动，环绕接缝处卡片从一侧
 *     淡出、另一侧淡入，避免 CSS transition 造成的瞬移或横穿。
 * 卡片顺序由父级按全局选用次数排好（rankOntologyCards），这里只管呈现。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft, ChevronRight, Flame, Network, Sparkles } from 'lucide-react'
import type { OntologyListItem } from '@/types/ontology'
import { OntologyAvatar } from '@/components/OntologyAvatar'
import { rankOntologyCards } from './ontologyCardRanking'
import { circularCardPosition, normalizeCardIndex } from './ontologyCarouselMath'

const CARD_WIDTH = 300
const FOCUS_STEP_X = CARD_WIDTH * 0.7
const CLICK_SLOP_PX = 6
/** |pos| 超过该值的卡片完全移出视窗（不渲染交互），环绕接缝藏在该区域。 */
const HIDE_BEYOND = 2.6
/** |pos| 超过该值的卡片透明度降为 0，但保留在视窗内保证环绕时连续淡入。 */
const FADE_BEYOND = 2
/** 滚轮累积滚动量达到该阈值才切换一张，避免触控板一次手势连切多张。 */
const WHEEL_SWITCH_THRESHOLD = 40
/** 两次滚轮切换的最小间隔（毫秒），吸收触控板惯性滚动。 */
const WHEEL_SWITCH_COOLDOWN_MS = 200

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

const prefersReducedMotion = () =>
  typeof window !== 'undefined'
  && typeof window.matchMedia === 'function'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches

function cardMetrics(item: OntologyListItem) {
  return [
    { label: '对象实体', value: item.entity_count ?? 0 },
    { label: '实体关系', value: item.relation_count ?? 0 },
    { label: '执行动作', value: item.action_count ?? 0 },
    { label: '哨兵引擎', value: item.sentinel_count ?? 0 },
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
  const ranked = useMemo(() => rankOntologyCards(items), [items])
  const count = ranked.length
  const looped = count >= 3

  const [focus, setFocus] = useState(0)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ startX: 0, startFocus: 0, moved: false })
  const focusRef = useRef(0)
  const targetRef = useRef(0)
  const rafRef = useRef<number | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)

  const activeIndex = normalizeCardIndex(focus, count)

  const cancelAnimation = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [])

  const applyFocus = useCallback((next: number) => {
    focusRef.current = next
    targetRef.current = next
    setFocus(next)
  }, [])

  const animateTo = useCallback((target: number) => {
    targetRef.current = target
    if (prefersReducedMotion()) {
      cancelAnimation()
      applyFocus(target)
      return
    }
    if (rafRef.current !== null) return
    const step = () => {
      const delta = targetRef.current - focusRef.current
      if (Math.abs(delta) < 0.002) {
        applyFocus(targetRef.current)
        rafRef.current = null
        return
      }
      focusRef.current += delta * 0.16
      setFocus(focusRef.current)
      rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
  }, [applyFocus, cancelAnimation])

  useEffect(() => cancelAnimation, [cancelAnimation])

  // 列表在轮播展示期间被刷新（如计数回填）时，把焦点收回有效范围。
  useEffect(() => {
    if (count === 0) return
    if (!looped && focusRef.current > count - 1) applyFocus(count - 1)
    if (looped) applyFocus(normalizeCardIndex(focusRef.current, count))
  }, [count, looped, applyFocus])

  /** 吸附到最近的整数焦点；looped 时不规整，保持环绕方向上的连续运动。 */
  const snapTo = useCallback((target: number) => {
    if (count === 0) return
    animateTo(looped ? target : clamp(target, 0, count - 1))
  }, [animateTo, count, looped])

  // 滚轮切换：向下滚与向右拖等价（切到下一张），向上滚与向左拖等价（切到上一张）。
  // 用原生非 passive 监听（与 OntologyNetworkView 的滚轮缩放同一模式），
  // 并按累积滚动量 + 冷却时间节流，避免触控板一次手势连切多张。
  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    let accumulated = 0
    let lastSwitchAt = 0
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault()
      if (count < 2) return
      const now = performance.now()
      if (now - lastSwitchAt < WHEEL_SWITCH_COOLDOWN_MS) {
        accumulated = 0
        return
      }
      accumulated += event.deltaY
      if (Math.abs(accumulated) < WHEEL_SWITCH_THRESHOLD) return
      const direction = accumulated > 0 ? 1 : -1
      accumulated = 0
      lastSwitchAt = now
      snapTo(Math.round(focusRef.current) + direction)
    }
    stage.addEventListener('wheel', handleWheel, { passive: false })
    return () => stage.removeEventListener('wheel', handleWheel)
  }, [count, snapTo])

  /** 侧边卡片点击聚焦：沿环形最短路径滑过去。 */
  const focusCard = useCallback((index: number) => {
    const delta = circularCardPosition(index, focusRef.current, count)
    animateTo(focusRef.current + delta)
  }, [animateTo, count])

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || count === 0) return
    if ((e.target as Element).closest('button')) return
    cancelAnimation()
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { startX: e.clientX, startFocus: focusRef.current, moved: false }
    setDragging(true)
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    const deltaX = e.clientX - dragRef.current.startX
    if (Math.abs(deltaX) > CLICK_SLOP_PX) dragRef.current.moved = true
    const raw = dragRef.current.startFocus - deltaX / FOCUS_STEP_X
    if (looped) {
      applyFocus(raw)
      return
    }
    // 非循环（1~2 张卡）边缘橡皮筋：越界拖拽施加阻尼，松手后回弹吸附。
    const resisted = raw < 0 ? raw * 0.35 : raw > count - 1 ? count - 1 + (raw - (count - 1)) * 0.35 : raw
    applyFocus(resisted)
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
    if (dragRef.current.moved) {
      snapTo(Math.round(focusRef.current))
      return
    }
    // 未拖动的按下-抬起视为点击：命中侧边卡片聚焦，命中聚焦卡片确认选中。
    const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-card-index]')
    const hitIndex = hit ? Number((hit as HTMLElement).dataset.cardIndex) : NaN
    if (Number.isNaN(hitIndex)) return
    if (hitIndex === activeIndex) onSelect(ranked[hitIndex])
    else focusCard(hitIndex)
  }

  const handlePointerCancel = () => {
    if (!dragging) return
    setDragging(false)
    snapTo(Math.round(focusRef.current))
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (count === 0) return
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      snapTo(Math.round(focusRef.current) - 1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      snapTo(Math.round(focusRef.current) + 1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      onSelect(ranked[activeIndex])
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
            滚轮滚动、按住拖拽或点击两侧卡片浏览，点击居中卡片进入本体工作区
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-sky-100 bg-white/80 px-2.5 py-1 text-[11px] font-medium text-sky-600 shadow-sm dark:border-slate-700 dark:bg-slate-800/80 dark:text-sky-300">
          {count} 个已发布本体
        </span>
      </div>

      <div
        ref={stageRef}
        data-testid="ontology-card-carousel"
        role="listbox"
        aria-label="已发布本体卡片轮播"
        aria-activedescendant={`ontology-card-${ranked[activeIndex]?.id}`}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        className="scrollbar-none relative min-h-0 flex-1 cursor-grab touch-none select-none outline-none focus-visible:ring-2 focus-visible:ring-teal-300 active:cursor-grabbing"
      >
        {ranked.map((item, index) => {
          const pos = circularCardPosition(index, focus, count)
          const absPos = Math.abs(pos)
          const isFocused = index === activeIndex && !dragging
          const hidden = absPos > HIDE_BEYOND
          const clicks = item.assistant_card_clicks ?? 0
          const style: React.CSSProperties = {
            width: CARD_WIDTH,
            left: '50%',
            // 垂直方向居中略偏上，给标题与指示点留出视觉余量。
            top: '46%',
            zIndex: 100 - Math.round(absPos * 10),
            opacity: absPos > FADE_BEYOND ? 0 : 1 - Math.min(absPos, 2) * 0.26,
            transform: [
              'translate(-50%, -50%)',
              `translateX(${pos * FOCUS_STEP_X}px)`,
              `translateY(${Math.min(absPos, 2) * 10}px)`,
              // 居中卡（pos 为 0）不施加 3D 透视与缩放：常驻 GPU 光栅化纹理会让
              // 文字、数字发虚；侧边卡保留浅幅 rotateY/scale 维持 coverflow 纵深感。
              ...(pos === 0 ? [] : [
                `perspective(1200px) rotateY(${clamp(-pos * 6, -20, 20)}deg)`,
                `scale(${1 - Math.min(absPos, 2.5) * 0.06})`,
              ]),
            ].join(' '),
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
              className={`absolute flex flex-col overflow-hidden rounded-2xl border bg-white shadow-xl will-change-transform dark:bg-slate-800 ${
                index === activeIndex
                  ? 'border-teal-200 shadow-2xl ring-1 ring-teal-400/30 dark:border-teal-600'
                  : 'border-slate-200 dark:border-slate-700'
              }`}
            >
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
                    {clicks > 0 && (
                      <span
                        title={`已被选用 ${clicks} 次`}
                        className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-600"
                      >
                        <Flame size={11} />×{clicks}
                      </span>
                    )}
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

              <div className="mt-3 grid grid-cols-4 gap-1.5 px-4">
                {cardMetrics(item).map(metric => (
                  <div key={metric.label} className="min-w-0 rounded-xl bg-slate-50 px-0.5 py-2 text-center dark:bg-slate-700/60">
                    <p className="whitespace-nowrap text-[10px] font-medium text-slate-400">{metric.label}</p>
                    <p className="mt-0.5 text-base font-semibold tabular-nums text-slate-800 dark:text-slate-100">{metric.value}</p>
                  </div>
                ))}
              </div>

              <div className="mt-auto px-4 pb-4 pt-3">
                <div
                  data-testid={index === activeIndex ? 'ontology-card-confirm' : undefined}
                  className={`flex h-9 items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-colors duration-300 ${
                    index === activeIndex
                      ? 'bg-teal-600 text-white shadow-sm'
                      : 'bg-slate-100 text-slate-400 dark:bg-slate-700'
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
              disabled={!looped && activeIndex === 0}
              onClick={() => snapTo(Math.round(focusRef.current) - 1)}
              className="absolute left-3 top-1/2 z-[110] flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white/90 text-slate-500 shadow-md backdrop-blur transition-all hover:border-teal-300 hover:text-teal-600 disabled:cursor-not-allowed disabled:opacity-30 dark:border-slate-700 dark:bg-slate-800/90"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              type="button"
              aria-label="下一张本体卡片"
              disabled={!looped && activeIndex >= count - 1}
              onClick={() => snapTo(Math.round(focusRef.current) + 1)}
              className="absolute right-3 top-1/2 z-[110] flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white/90 text-slate-500 shadow-md backdrop-blur transition-all hover:border-teal-300 hover:text-teal-600 disabled:cursor-not-allowed disabled:opacity-30 dark:border-slate-700 dark:bg-slate-800/90"
            >
              <ChevronRight size={16} />
            </button>
          </>
        )}
      </div>

      {count > 1 && (
        <div className="flex shrink-0 items-center justify-center gap-1.5 pb-4 pt-2">
          {ranked.map((item, index) => (
            <button
              key={item.id}
              type="button"
              aria-label={`聚焦第 ${index + 1} 张本体卡片`}
              onClick={() => focusCard(index)}
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
