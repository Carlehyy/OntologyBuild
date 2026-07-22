const ICON_ALIASES: Record<string, string> = {
  cube: '📦',
  box: '📦',
  package: '📦',
  person: '👤',
  user: '👤',
  building: '🏢',
  document: '📄',
  money: '💰',
  rocket: '🚀',
  settings: '⚙️',
  chart: '📊',
  link: '🔗',
  phone: '📱',
  computer: '🖥️',
  target: '🎯',
  radar: '📡',
  'alert-triangle': '⚠️',
  'triangle-alert': '⚠️',
};

const EMOJI_PATTERN = /^\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier}|\u200D\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?)*$/u;

/**
 * 历史 ObjectType.icon 既有 emoji，也有 cube 等语义键，少量导入数据甚至
 * 错写成了实体名称。只渲染真正的图标值，任意文本绝不能再成为节点头像。
 */
export function objectTypeIconGlyph(icon?: string | null): string {
  const value = String(icon || '').trim();
  if (!value) return '📦';
  const alias = ICON_ALIASES[value.toLowerCase()];
  if (alias) return alias;
  return EMOJI_PATTERN.test(value) ? value : '📦';
}
