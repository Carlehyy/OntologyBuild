/**
 * 规范化「标识符」类字段的输入（对象实体的实体标识、属性的属性标识等机器名）。
 * 规则：只允许英文小写字母和下划线，且首字符必须是英文字母。
 * - 空格转为下划线（便于输入，如 "order id" → "order_id"）
 * - 过滤掉除 a-z 与 _ 之外的所有字符（数字、符号、大写字母的原字符等）
 * - 去掉开头的下划线，保证首字符是字母
 */
export function sanitizeIdentifier(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/[^a-z_]/g, '')
    .replace(/^_+/, '');
}
