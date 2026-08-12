/**
 * 本体卡片轮播的循环布局数学（纯函数，便于单元测试）。
 *
 * count >= 3 时卡片位置按环形计算：焦点卡居中，右侧按排名递减，
 * 排名末尾的卡环绕到左侧，支持焦点单向无限递增/递减（无限轮播）。
 * count < 3 时退化为线性位置（1~2 张卡直接展示，不环绕）。
 */

/** 卡片 index 相对焦点 focus 的环形偏移，结果落在 (-count/2, count/2]。 */
export function circularCardPosition(index: number, focus: number, count: number): number {
  if (count < 3) return index - focus
  let pos = index - focus
  const half = count / 2
  while (pos > half) pos -= count
  while (pos <= -half) pos += count
  return pos
}

/** 把可能越界的焦点值规整为用于展示/选中的卡索引（0..count-1）。 */
export function normalizeCardIndex(focus: number, count: number): number {
  if (count <= 0) return 0
  return ((Math.round(focus) % count) + count) % count
}

/** 第 p 环侧卡缩放后的视觉半宽（与组件内的 scale 规则一致）。 */
function cardHalfWidthAt(ring: number, cardWidth: number): number {
  const scale = ring === 0 ? 1 : 1 - Math.min(ring, 2.5) * 0.06
  return (cardWidth / 2) * scale
}

/**
 * 依据舞台宽度计算两侧最多完整展示的卡环数（0..2）：
 * 侧卡要么完整落在面板内，要么整体淡出，避免半张卡被面板边缘裁掉。
 */
export function maxVisibleSideRings(
  stageWidth: number,
  cardWidth: number,
  stepX: number,
  maxRings = 2,
): number {
  const half = stageWidth / 2 - 4
  let rings = 0
  for (let ring = 1; ring <= maxRings; ring += 1) {
    if (ring * stepX + cardHalfWidthAt(ring, cardWidth) <= half) rings = ring
  }
  return rings
}
