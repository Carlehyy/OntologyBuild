/**
 * 本体助手卡片轮播的排序规则（纯函数，便于单元测试）：
 *   1. 全局选用次数（assistant_card_clicks）降序；
 *   2. 并列时按最近更新时间降序（无更新时间退回创建时间）；
 *   3. 再并列按名称排序，保证不同会话间顺序稳定可预期。
 */
export interface RankableOntologyCard {
  name: string
  assistant_card_clicks?: number | null
  updated_at?: string
  created_at?: string
}

function cardTimestamp(item: RankableOntologyCard): number {
  const time = Date.parse(item.updated_at || item.created_at || '')
  return Number.isNaN(time) ? 0 : time
}

export function rankOntologyCards<T extends RankableOntologyCard>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => {
    const clicksDiff = (b.assistant_card_clicks ?? 0) - (a.assistant_card_clicks ?? 0)
    if (clicksDiff !== 0) return clicksDiff
    const timeDiff = cardTimestamp(b) - cardTimestamp(a)
    if (timeDiff !== 0) return timeDiff
    return a.name.localeCompare(b.name, 'zh-CN')
  })
}
