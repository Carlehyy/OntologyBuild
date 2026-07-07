/**
 * OntoPrompt Motion Design Tokens
 * 
 * 时长 + 缓动集中管理，全站复用
 * 配合 prefers-reduced-motion 无障碍
 */

// ── 时长 ──
export const DURATION = {
  micro: 0.15,      // 微交互: hover/press/focus
  standard: 0.25,   // 常规过渡: tab切换/表单
  large: 0.35,      // 较大转场: 页面/模态框
} as const

// ── 缓动 ──
export const EASE = {
  standard: [0.2, 0, 0, 1] as [number, number, number, number],
  decelerate: [0, 0, 0, 1] as [number, number, number, number],
  accelerate: [0.3, 0, 1, 1] as [number, number, number, number],
  spring: { type: "spring" as const, stiffness: 300, damping: 30 },
} as const

// ── 公共 Variants ──
export const fadeInUp = {
  hidden: { opacity: 0, y: 10 },
  visible: (i: number = 0) => ({
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.standard, ease: EASE.decelerate, delay: i * 0.04 },
  }),
}

export const fadeIn = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: DURATION.standard, ease: EASE.standard } },
  exit: { opacity: 0, transition: { duration: DURATION.micro, ease: EASE.accelerate } },
}

export const scaleIn = {
  hidden: { opacity: 0, scale: 0.96 },
  visible: { opacity: 1, scale: 1, transition: { duration: DURATION.standard, ease: EASE.decelerate } },
  exit: { opacity: 0, scale: 0.96, transition: { duration: DURATION.micro, ease: EASE.accelerate } },
}

export const slideInRight = {
  hidden: { opacity: 0, x: 20 },
  visible: { opacity: 1, x: 0, transition: { duration: DURATION.standard, ease: EASE.decelerate } },
  exit: { opacity: 0, x: 20, transition: { duration: DURATION.micro, ease: EASE.accelerate } },
}

export const staggerContainer = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.04, delayChildren: 0.05 } },
}

export const staggerItem = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: DURATION.standard, ease: EASE.decelerate } },
}

export const modalOverlay = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: DURATION.micro } },
  exit: { opacity: 0, transition: { duration: DURATION.micro } },
}

export const modalContent = {
  hidden: { opacity: 0, scale: 0.96, y: 8 },
  visible: { opacity: 1, scale: 1, y: 0, transition: { duration: DURATION.standard, ease: EASE.decelerate } },
  exit: { opacity: 0, scale: 0.96, y: 8, transition: { duration: DURATION.micro, ease: EASE.accelerate } },
}

export const tabSwitch = {
  hidden: { opacity: 0, y: 6 },
  visible: { opacity: 1, y: 0, transition: { duration: DURATION.standard, ease: EASE.decelerate } },
}

export const sidebarToggle = {
  expanded: { width: 224, transition: { duration: DURATION.large, ease: EASE.standard } },
  collapsed: { width: 64, transition: { duration: DURATION.large, ease: EASE.standard } },
}

// ── 按钮微交互 ──
export const buttonTap = { scale: 0.97, transition: { duration: 0.08 } }
export const buttonHover = { scale: 1.02, transition: { duration: DURATION.micro } }

// ── 卡片微交互 ──
export const cardHover = {
  y: -2,
  boxShadow: '0 8px 24px rgba(0, 0, 0, 0.08)',
  transition: { duration: DURATION.micro, ease: EASE.standard },
}

// ── reduced-motion 检测 ──
export function useReducedMotion(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

// ── 包装: 如果用户要求减少动画，返回0时长 ──
export function withReducedMotion(transition: object): object {
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return { duration: 0 }
  }
  return transition
}
