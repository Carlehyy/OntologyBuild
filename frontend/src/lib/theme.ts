/**
 * 主题（浅色/深色）纯逻辑。
 *
 * 本模块保持无副作用、可注入依赖，供 node:test 直接做单元测试；
 * DOM/localStorage 的接入在 stores/themeStore.ts 与 index.html 中完成。
 *
 * 持久化使用 zustand persist 的 JSON 信封格式（与 auth-store 一致）：
 * localStorage['theme'] = {"state":{"theme":"dark"},"version":0}
 */

export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'theme'

/** 默认浅色：未设置过偏好的用户保持平台既有浅色外观 */
export const DEFAULT_THEME: Theme = 'light'

/** 深色模式挂接点：tailwind.config.ts 的 darkMode: ['class'] 与 tokens.css 的 .dark 块 */
export const DARK_CLASS = 'dark'

/** 任意非法/缺失值一律回退默认浅色 */
export function normalizeTheme(raw: unknown): Theme {
  return raw === 'dark' ? 'dark' : DEFAULT_THEME
}

/**
 * 解析 localStorage 中的主题值。兼容两种格式：
 * - zustand persist 信封 {"state":{"theme":"dark"},"version":0}
 * - 裸字符串 "dark"（容忍历史/手工写入）
 */
export function parseStoredTheme(raw: string | null | undefined): Theme {
  if (!raw) return DEFAULT_THEME
  if (raw === 'dark' || raw === 'light') return normalizeTheme(raw)
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      return normalizeTheme((parsed as { state?: { theme?: unknown } }).state?.theme)
    }
  } catch {
    /* 非 JSON 内容按默认值处理 */
  }
  return DEFAULT_THEME
}

interface ClassListLike {
  toggle: (name: string, force?: boolean) => void
}

interface RootElementLike {
  classList: ClassListLike
}

/** 应用主题：深色在根元素上加 .dark，浅色移除（保持 DOM 与浅色默认一致） */
export function applyThemeClass(theme: Theme, root: RootElementLike): void {
  root.classList.toggle(DARK_CLASS, theme === 'dark')
}
