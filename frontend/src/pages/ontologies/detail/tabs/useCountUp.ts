import { useEffect, useRef, useState } from 'react'

/** 数字滚动动画：从上一个值 easeOut 过渡到目标值（600ms 默认）。 */
export function useCountUp(target: number, duration = 600): number {
  const [display, setDisplay] = useState(0)
  const previousRef = useRef(0)

  useEffect(() => {
    const from = previousRef.current
    const to = target
    if (from === to) {
      setDisplay(to)
      return undefined
    }
    let frame = 0
    const startedAt = performance.now()
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / duration)
      const eased = 1 - (1 - progress) ** 3
      setDisplay(Math.round(from + (to - from) * eased))
      if (progress < 1) {
        frame = requestAnimationFrame(tick)
      } else {
        previousRef.current = to
      }
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [target, duration])

  return display
}
