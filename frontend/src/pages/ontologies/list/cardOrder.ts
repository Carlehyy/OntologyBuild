/* 本体卡片手动排序纯逻辑：localStorage 持久化 + 应用到列表项。
   与 OntologyListPage.tsx 解耦（无 React/DOM 依赖），便于 node:test 单测。 */

export const ONTOLOGY_CARD_ORDER_KEY = 'ontology-card-order:v1'

export interface CardOrderStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

/** 读取手动排序快照：损坏/缺失一律回退空数组（等价默认创建时间倒序）。 */
export function readSavedCardOrder(storage: Pick<CardOrderStorage, 'getItem'>): string[] {
  try {
    const raw = storage.getItem(ONTOLOGY_CARD_ORDER_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const ids = parsed.filter((id): id is string => typeof id === 'string')
    return [...new Set(ids)]
  } catch {
    return []
  }
}

export function writeSavedCardOrder(storage: CardOrderStorage, ids: string[]): void {
  try {
    storage.setItem(ONTOLOGY_CARD_ORDER_KEY, JSON.stringify(ids))
  } catch {
    /* 隐私模式等写入失败时静默降级：本次会话内仍按内存态排序 */
  }
}

/**
 * 手动序优先：已入序的本体按快照相对位置排列；快照之后新建（未入序）的本体
 * 按创建时间倒序插到最前；快照中已被删除的 id 自动剔除。
 * 空快照保持既有默认行为：创建时间倒序。
 */
export function applyCardOrder<T extends { id: string; created_at: string }>(
  items: T[],
  savedOrder: string[],
): T[] {
  if (savedOrder.length === 0) {
    return [...items].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )
  }
  const rank = new Map(savedOrder.map((id, index) => [id, index]))
  const known = items
    .filter(item => rank.has(item.id))
    .sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0))
  const fresh = items
    .filter(item => !rank.has(item.id))
    .sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )
  return [...fresh, ...known]
}

/**
 * 拖拽落位：把 draggedId 从当前位置移动到 targetId 的前/后。
 * 返回新 id 序；目标不在序列中或拖到自身时原样返回。
 */
export function reorderCardIds(
  ids: string[],
  draggedId: string,
  targetId: string,
  place: 'before' | 'after',
): string[] {
  if (draggedId === targetId || !ids.includes(draggedId) || !ids.includes(targetId)) {
    return ids
  }
  const rest = ids.filter(id => id !== draggedId)
  const targetIndex = rest.indexOf(targetId)
  const insertIndex = place === 'before' ? targetIndex : targetIndex + 1
  return [...rest.slice(0, insertIndex), draggedId, ...rest.slice(insertIndex)]
}
