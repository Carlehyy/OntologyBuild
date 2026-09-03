/* ReUI/shadcn 同源 Command（cmdk 封装），来源：
   https://ui.shadcn.com/r/styles/new-york-v4/command.json 拷贝日期：2026-09-03。
   平台适配：语义令牌映射（popover/accent/muted → --color-* 令牌，选中项用
   --color-bg-hover + teal 焦点环）；Tailwind 3.4 适配（outline-hidden →
   outline-none，去除 TW4 的 **: 通用变体写法）；CommandDialog 弹壳按平台弹窗
   语言（rounded-2xl、白底、slate 边框、animate-dialog-in）实现，位置改为中心
   偏上，符合命令面板惯例；默认中文无障碍标签。 */
import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { Command as CommandPrimitive } from 'cmdk'
import { Search as SearchIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

function Command({ className, ...props }: React.ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn(
        'flex h-full w-full flex-col overflow-hidden rounded-2xl bg-white text-[var(--color-text-primary)]',
        className,
      )}
      {...props}
    />
  )
}

function CommandDialog({
  title = '全局搜索',
  description = '输入关键词检索',
  children,
  className,
  shouldFilter,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Root> & {
  title?: string
  description?: string
  className?: string
  /** 服务端检索场景传 false，关闭 cmdk 的本地过滤 */
  shouldFilter?: boolean
}) {
  return (
    <DialogPrimitive.Root {...props}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[var(--z-modal,80)] bg-slate-900/30 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-[16vh] z-[var(--z-modal,80)] w-[min(92vw,36rem)] -translate-x-1/2',
            'animate-dialog-in overflow-hidden rounded-2xl border border-slate-200 bg-white p-0 shadow-[0_24px_64px_rgba(15,23,42,0.18)]',
            'focus:outline-none',
            className,
          )}
        >
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">{description}</DialogPrimitive.Description>
          <Command shouldFilter={shouldFilter} className="[&_[data-slot=command-input-wrapper]]:h-12 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-[var(--color-text-tertiary)] [&_[cmdk-group]]:px-1.5 [&_[cmdk-group]:not([hidden])_~[cmdk-group]]:pt-0 [&_[cmdk-input-wrapper]_svg]:h-4 [&_[cmdk-input-wrapper]_svg]:w-4 [&_[cmdk-input]]:h-12 [&_[cmdk-item]]:px-2 [&_[cmdk-item]]:py-2.5">
            {children}
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

function CommandInput({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div data-slot="command-input-wrapper" className="flex h-11 items-center gap-2 border-b border-[var(--color-border)] px-3.5">
      <SearchIcon className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          'flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-[var(--color-text-tertiary)] disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        {...props}
      />
    </div>
  )
}

function CommandList({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn('scrollbar-none max-h-[320px] scroll-py-1 overflow-y-auto overflow-x-hidden', className)}
      {...props}
    />
  )
}

function CommandEmpty({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className={cn('py-8 text-center text-sm text-[var(--color-text-tertiary)]', className)}
      {...props}
    />
  )
}

function CommandGroup({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        'overflow-hidden p-1 text-[var(--color-text-primary)] [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-[var(--color-text-tertiary)]',
        className,
      )}
      {...props}
    />
  )
}

function CommandSeparator({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Separator>) {
  return (
    <CommandPrimitive.Separator
      data-slot="command-separator"
      className={cn('-mx-1 h-px bg-[var(--color-border)]', className)}
      {...props}
    />
  )
}

function CommandItem({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        'relative flex cursor-pointer select-none items-center gap-2 rounded-lg px-2 py-2 text-sm outline-none',
        'data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50',
        'data-[selected=true]:bg-[var(--color-bg-hover)]',
        '[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*="h-"])]:h-4 [&_svg:not([class*="w-"])]:w-4 [&_svg:not([class*="text-"])]:text-[var(--color-text-tertiary)]',
        className,
      )}
      {...props}
    />
  )
}

function CommandShortcut({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span
      data-slot="command-shortcut"
      className={cn('ml-auto text-xs tracking-widest text-[var(--color-text-tertiary)]', className)}
      {...props}
    />
  )
}

export {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
}
