import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import i18n from '@/i18n'

interface UIState {
  lang: 'zh' | 'en'
  setLang: (lang: 'zh' | 'en') => void
}

export const useUIStore = create<UIState>()(
  persist(
    set => ({
      lang: 'zh',
      setLang: (lang) => {
        localStorage.setItem('lang', lang)
        i18n.changeLanguage(lang)
        set({ lang })
      },
    }),
    { name: 'ui-store' }
  )
)
