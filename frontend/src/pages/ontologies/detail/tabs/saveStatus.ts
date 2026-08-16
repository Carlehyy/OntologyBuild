/**
 * 本体结构画布的布局保存状态与文案（纯函数，便于单测）。
 *
 * 保存时机固定为拖拽停止后 3 秒提交（PUT /ontologies/:id/layout），
 * 这里只负责把状态机翻译成用户可见的文案；pending 状态会传入剩余秒数，
 * 由组件每秒递减实现真实倒计时。
 */
export type StructureSaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error'

export const SAVE_COUNTDOWN_SECONDS = 3

export function saveStatusLabel(state: StructureSaveState, countdown: number): string {
  switch (state) {
    case 'pending': {
      const remaining = Math.max(1, Math.min(SAVE_COUNTDOWN_SECONDS, Math.round(countdown)))
      return `${remaining} 秒后自动保存`
    }
    case 'saving':
      return '正在保存布局'
    case 'saved':
      return '布局已保存'
    case 'error':
      return '保存失败'
    default:
      return '拖动后自动保存布局'
  }
}
