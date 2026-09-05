import * as React from 'react'
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from 'lucide-react'
import { Toaster as SonnerToaster } from 'sonner'

/**
 * 全局消息提示挂载件（vendored，copy-and-own）。
 * 出处：sonner@2（npm 依赖）+ shadcn/ui sonner 封装思路，2026-09-05 拷贝适配。
 *
 * 平台适配：
 * - 不引入 next-themes：深浅色由 tokens.css 的 .dark 令牌自动翻转；
 * - 位置统一 top-center，offset 4rem 让出 h-14 页头（DESIGN.md §4.3）；
 * - 层级沿用 --z-toast: 1100（高于 antd 弹层，低于悬浮 AI 助手 z-10000）；
 * - 图标沿用旧版 Toast 的语义色圆角徽章视觉。
 * 选型与场景边界见 component-catalog.ts「瞬时消息提示」条目。
 */

function ToneIcon({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ${className}`}>
      {children}
    </span>
  )
}

export function Toaster() {
  return (
    <SonnerToaster
      position="top-center"
      visibleToasts={3}
      closeButton
      offset={{ top: '4rem' }}
      toastOptions={{ closeButtonAriaLabel: '关闭提示' }}
      icons={{
        success: (
          <ToneIcon className="bg-[var(--color-success-bg)] text-[var(--color-success)] ring-[var(--color-success-bg)]">
            <CheckCircle2 size={17} />
          </ToneIcon>
        ),
        error: (
          <ToneIcon className="bg-[var(--color-danger-bg)] text-[var(--color-danger)] ring-[var(--color-danger-bg)]">
            <AlertCircle size={17} />
          </ToneIcon>
        ),
        warning: (
          <ToneIcon className="bg-[var(--color-warning-bg)] text-[var(--color-warning)] ring-[var(--color-warning-bg)]">
            <TriangleAlert size={17} />
          </ToneIcon>
        ),
        info: (
          <ToneIcon className="bg-[var(--color-info-bg)] text-[var(--color-info)] ring-[var(--color-info-bg)]">
            <Info size={17} />
          </ToneIcon>
        ),
      }}
      style={
        {
          zIndex: 'var(--z-toast)',
          '--width': 'min(390px, calc(100vw - 32px))',
          '--normal-bg': 'var(--color-popover)',
          '--normal-text': 'var(--color-text-primary)',
          '--normal-border': 'var(--color-border)',
        } as React.CSSProperties
      }
    />
  )
}
