import { useCallback, useRef, useState } from 'react'

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

/**
 * 可拖拽左右分栏布局（本体建模页与本体网络页共用）。
 *
 * sizes 为百分比；拖拽时以容器实际宽度换算，最小宽度用百分比表达，
 * 与 ExplorationPage 的既有行为保持一致（48% / 24% 下限）。
 */
export function useSplitLayout(initial: [number, number] = [68, 32]) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [sizes, setSizes] = useState<[number, number]>(initial)

  const startResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return

    const startX = event.clientX
    const start = sizes
    const min: [number, number] = [48, 24]
    const pairTotal = start[0] + start[1]
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (moveEvent: PointerEvent) => {
      const delta = ((moveEvent.clientX - startX) / rect.width) * 100
      const left = clamp(start[0] + delta, min[0], pairTotal - min[1])
      setSizes([left, pairTotal - left])
    }
    const onUp = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [sizes])

  return { containerRef, sizes, startResize }
}

/** 分栏拖拽手柄（与 ExplorationSplitHandle 同款视觉）。 */
export function SplitHandle({ onPointerDown, label }: { onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void; label: string }) {
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      className="group flex cursor-col-resize items-center justify-center"
    >
      <div className="h-16 w-1 rounded-full bg-[var(--color-border)] transition-all group-hover:h-24 group-hover:bg-teal-500/70" />
    </div>
  )
}
