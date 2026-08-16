import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import {
  closeTab as closeTabLogic,
  recordVisit as recordVisitLogic,
  EMPTY_NAV_TAB_STATE,
  type CloseTabResult,
  type NavTabListState,
} from '@/stores/tabLogic'

/** localStorage 持久化 key：增量新增，不复用 auth-store/token 等既有 key。 */
export const NAV_TABS_STORAGE_KEY = 'nav-tabs'

interface NavTabStore extends NavTabListState {
  recordVisit: (username: string, tab: { key: string; title: string; path: string }) => void
  close: (key: string) => CloseTabResult
}

/**
 * 顶栏多标签页列表，localStorage 持久化，刷新后恢复标签与激活态。
 * 业务逻辑全部在 tabLogic.ts 纯函数中，本 store 只做持久化与装配。
 */
export const useTabStore = create<NavTabStore>()(
  persist(
    (set, get) => ({
      ...EMPTY_NAV_TAB_STATE,
      recordVisit: (username, tab) => {
        set(state => recordVisitLogic(state, username, tab, Date.now()))
      },
      close: (key) => {
        const result = closeTabLogic(get(), key)
        set(result.state)
        return result
      },
    }),
    { name: NAV_TABS_STORAGE_KEY },
  ),
)
