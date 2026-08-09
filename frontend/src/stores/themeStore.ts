import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import {
  applyThemeClass,
  normalizeTheme,
  THEME_STORAGE_KEY,
  type Theme,
} from '@/lib/theme'

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

/**
 * 全局主题偏好（浅色/深色），localStorage 持久化，与 lang 等 UI 偏好同惯例。
 * 每次变更立即同步 <html> 上的 .dark 类，驱动 tokens.css 的 .dark 变量覆盖。
 */
export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'light',
      setTheme: (theme) => {
        const next = normalizeTheme(theme)
        applyThemeClass(next, document.documentElement)
        set({ theme: next })
      },
      toggleTheme: () => {
        get().setTheme(get().theme === 'dark' ? 'light' : 'dark')
      },
    }),
    {
      name: THEME_STORAGE_KEY,
      onRehydrateStorage: () => (state) => {
        // localStorage 恢复后立即应用，保证刷新/深链进入时主题不跳变
        applyThemeClass(normalizeTheme(state?.theme), document.documentElement)
      },
    },
  ),
)
