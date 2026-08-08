import { useEffect, useState } from 'react'

/**
 * 输入防抖：值稳定 delayMs 后才向外输出，避免每次击键都触发查询。
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}
