/** 助手配置面板的共享小工具（独立成模块以避免组件间循环依赖） */

export const errorText = (error: any, fallback = '操作失败') =>
  error?.detail || error?.message || fallback
