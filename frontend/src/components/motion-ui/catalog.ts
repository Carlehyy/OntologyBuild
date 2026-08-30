/**
 * beUI 上游组件策展目录（B 端适配视角的唯一事实源）。
 *
 * - 版本钉死在 UPSTREAM.commit：vendor 模式不自动跟随上游，升级 = 显式重新引入；
 * - 本文件只存「判断层」（有什么 / 干什么 / B 端适不适配），不复述 props/API——
 *   已引入组件的 API 以 motion-ui/ 源码为准，未引入的以上游源码为准；
 * - 人类视角的渲染在 /#/design/components 画廊（活示例 + 目录速查，受 e2e 门禁保护）；
 * - 引入新组件时按 components/README.md 流程同步更新本目录状态与画廊示例。
 */

export const UPSTREAM = {
  repo: 'starc007/ui-components',
  /** 本目录与全部已 vendor 文件的共同上游基准 */
  commit: 'afba7fa055dd',
  license: 'MIT © 2026 Saurabh Chauhan',
  /** 上游完整目录的机器可查事实源（脚本可拉取，无需爬官网） */
  treeApi:
    'https://api.github.com/repos/starc007/ui-components/git/trees/main?recursive=1',
} as const

export type CatalogStatus = 'vendored' | 'available' | 'unsuitable'

export interface CatalogEntry {
  /** 上游组件名（components/motion/ 下的文件或目录名） */
  name: string
  /** 一句话用途 */
  desc: string
  status: CatalogStatus
  /** 状态不为 vendored 时：引入提示 / 不适用原因；同族变体也在此说明 */
  note?: string
}

export const MOTION_UI_CATALOG: CatalogEntry[] = [
  // ── 已引入（motion-ui/ 内，API 读源码） ─────────────────────────────
  { name: 'availability-scheduler', desc: '每周可用时段编辑器（block 级完整组件）', status: 'vendored' },
  { name: 'animated-number', desc: '数字滚动，进视口触发', status: 'vendored' },
  { name: 'center-morph-modal', desc: '中心展开 morph 弹窗，焦点圈定', status: 'vendored' },
  { name: 'tilt-card', desc: '指针跟随 3D 倾转卡（glare 建议关）', status: 'vendored' },
  { name: 'select', desc: '动效下拉，gooey 连体面板', status: 'vendored' },
  { name: 'multi-select', desc: '多选筛选：chip 逐项移除、输入即过滤、键盘 ↑↓/Enter/Backspace', status: 'vendored', note: '闭包含上游 combobox 的 use-active-option hook（hooks/use-active-option.ts）' },
  { name: 'tooltip', desc: '动效提示，替代原生 title', status: 'vendored' },
  { name: 'popover-morph', desc: '从触发角展开的气泡面板', status: 'vendored' },
  { name: 'switch', desc: '动效开关', status: 'vendored' },
  { name: 'checkbox', desc: '动效复选框', status: 'vendored' },
  { name: 'popover-position', desc: 'portal 定位 hook（内部基建）', status: 'vendored' },

  // ── 可按需引入（B 端适配，按 README 五步流程 vendor） ───────────────
  { name: 'drawer', desc: '侧边抽屉，详情面板首选', status: 'available', note: '世界模型两个手写抽屉的未来替换' },
  { name: 'bottom-sheet', desc: '底部滑出面板', status: 'available' },
  { name: 'combobox', desc: '可搜索下拉', status: 'available', note: '本体/领域等大列表选择' },
  { name: 'command-palette', desc: 'Ctrl+K 命令面板', status: 'available' },
  { name: 'context-menu', desc: '右键菜单', status: 'available' },
  { name: 'overflow-actions', desc: '溢出操作菜单', status: 'available', note: '表格操作列收纳' },
  { name: 'expandable-action-bar', desc: '可展开操作条', status: 'available' },
  { name: 'expandable-control', desc: '可展开控件', status: 'available' },
  { name: 'expandable-tabs', desc: '可展开 Tab', status: 'available' },
  { name: 'tabs', desc: '页内 Tab', status: 'available' },
  { name: 'morphing-tabs', desc: 'morph 过渡 Tab', status: 'available' },
  { name: 'morphing-search', desc: 'morph 展开搜索框', status: 'available' },
  { name: 'morphing-modal', desc: 'morph 弹窗（另一形态）', status: 'available', note: '与已引入 center-morph-modal 同族，二选一' },
  { name: 'select-morph', desc: 'morph 版下拉', status: 'available', note: '与已引入 select 同族变体' },
  { name: 'number-ticker', desc: '数字翻牌', status: 'available', note: '与 animated-number 同族备选' },
  { name: 'animated-badge', desc: '状态徽标动效', status: 'available', note: '在线/草稿等状态' },
  { name: 'animated-toast-stack', desc: '通知堆叠', status: 'available', note: '收件箱/工单提醒' },
  { name: 'notification-stack', desc: '通知堆叠（另一形态）', status: 'available' },
  { name: 'dynamic-island', desc: '灵动岛式全局提示', status: 'available', note: '全局任务进度类场景' },
  { name: 'feedback-widget', desc: '侧边反馈挂件', status: 'available' },
  { name: 'file-upload', desc: '文件上传', status: 'available' },
  { name: 'attachment-upload', desc: '附件上传（含预览）', status: 'available', note: '工单附件' },
  { name: 'file-tree', desc: '文件树', status: 'available' },
  { name: 'input', desc: '动效输入框', status: 'available' },
  { name: 'radio', desc: '单选', status: 'available' },
  { name: 'loader', desc: '加载动效', status: 'available' },
  { name: 'hold-action-button', desc: '按住确认按钮', status: 'available', note: '危险操作防误触' },
  { name: 'slide-action-button', desc: '滑动确认按钮', status: 'available', note: '危险操作防误触' },
  { name: 'action-swap', desc: '状态切换动效（loading→完成）', status: 'available', note: '含 blur/cascade/roll 三个同族变体' },
  { name: 'swipeable-list', desc: '滑动操作列表', status: 'available' },
  { name: 'bouncy-accordion', desc: '手风琴', status: 'available' },
  { name: 'range-slider', desc: '滑杆', status: 'available', note: '含 bubble/fluid/ruler/wave 四个同族变体' },
  { name: 'wheel-picker', desc: '滚轮选择器', status: 'available' },
  { name: 'pull-to-refresh', desc: '下拉刷新', status: 'available' },
  { name: 'scroll-progress', desc: '滚动进度条', status: 'available' },
  { name: 'scroll-reveal', desc: '滚动渐显', status: 'available' },
  { name: 'scroll-to', desc: '平滑滚动定位', status: 'available' },
  { name: 'smooth-scroll', desc: '平滑滚动基建', status: 'available' },
  { name: 'text-shimmer', desc: '加载占位文字微光', status: 'available' },
  { name: 'shared-layout-bg', desc: '共享布局背景（路由间 morph）', status: 'available', note: '基建类，引入需评估路由结构' },
  { name: 'preview-rail', desc: '预览导轨', status: 'available' },

  // ── 不适用 B 端（显式排除，agent 无需再评估） ───────────────────────
  { name: 'animated-sidebar', desc: '动效侧边栏', status: 'unsuitable', note: '平台已有 Layout 导航' },
  { name: 'bounce-sidebar', desc: '弹跳侧边栏', status: 'unsuitable', note: '平台已有 Layout 导航' },
  { name: 'theme-toggle', desc: '主题切换', status: 'unsuitable', note: '平台已有 lib/theme + 偏好设置' },
  { name: 'dock', desc: '码头放大导航', status: 'unsuitable', note: '装饰性导航' },
  { name: 'marquee', desc: '跑马灯', status: 'unsuitable', note: '营销' },
  { name: 'parallax', desc: '视差', status: 'unsuitable', note: '营销' },
  { name: 'shader-background', desc: '着色器背景', status: 'unsuitable', note: '营销' },
  { name: 'chromatic-text-reveal', desc: '色散文字', status: 'unsuitable', note: '营销标题' },
  { name: 'text-cascade', desc: '级联文字', status: 'unsuitable', note: '营销标题' },
  { name: 'text-reveal', desc: '文字揭示', status: 'unsuitable', note: '营销标题' },
  { name: 'text-scramble', desc: '乱序文字', status: 'unsuitable', note: '营销标题' },
  { name: 'knockout-bracket', desc: '对阵图装饰', status: 'unsuitable', note: '装饰' },
  { name: 'knockout-wheel', desc: '转盘装饰', status: 'unsuitable', note: '装饰' },
  { name: 'cylinder-carousel', desc: '圆柱轮播', status: 'unsuitable', note: '营销' },
  { name: 'magnetic', desc: '磁吸按钮', status: 'unsuitable', note: '装饰' },
  { name: 'prediction-market', desc: '预测市场演示', status: 'unsuitable', note: '业务演示' },
  { name: 'swap', desc: '代币交换演示', status: 'unsuitable', note: '业务演示' },
  { name: 'signup-form', desc: '注册表单', status: 'unsuitable', note: '营销表单' },
  { name: 'wallet-card', desc: '钱包卡片组', status: 'unsuitable', note: '演示组件' },
  { name: 'infinite-masonry', desc: '无限瀑布流', status: 'unsuitable', note: '营销布局' },
  { name: 'project-folder', desc: '项目文件夹动效', status: 'unsuitable', note: '装饰' },
  { name: 'otp-input', desc: 'OTP 验证码输入', status: 'unsuitable', note: '平台无此场景' },
]
