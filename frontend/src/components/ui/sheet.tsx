/**
 * Sheet — ReUI/shadcn 风格的边缘滑出面板（基于 Radix Dialog 封装）。
 *
 * 自带焦点管理、Esc 关闭、遮罩点击关闭与进出场动画
 * （动画复用 tailwind.config 的 fade/slide 系列 token）。
 * 用法：
 *   <Sheet open={open} onOpenChange={setOpen}>
 *     <SheetContent>
 *       <SheetHeader><SheetTitle>标题</SheetTitle></SheetHeader>
 *       …内容…
 *     </SheetContent>
 *   </Sheet>
 */
import * as React from 'react'
import * as SheetPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import clsx from 'clsx'
import { twMerge } from 'tailwind-merge'

function cn(...inputs: Parameters<typeof clsx>) {
  return twMerge(clsx(inputs))
}

const Sheet = SheetPrimitive.Root
const SheetClose = SheetPrimitive.Close

const SheetOverlay = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Overlay
    ref={ref}
    className={cn(
      'fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]',
      'data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out',
      className,
    )}
    {...props}
  />
))
SheetOverlay.displayName = 'SheetOverlay'

const SheetContent = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <SheetPrimitive.Portal>
    <SheetOverlay />
    <SheetPrimitive.Content
      ref={ref}
      className={cn(
        'fixed inset-y-0 right-0 z-50 flex w-[540px] max-w-full flex-col bg-white shadow-2xl',
        'data-[state=open]:animate-slide-in-right data-[state=closed]:animate-slide-out-right',
        className,
      )}
      {...props}
    >
      {children}
      <SheetPrimitive.Close
        aria-label="关闭"
        className="absolute right-4 top-4 flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-900"
      >
        <X size={18} />
      </SheetPrimitive.Close>
    </SheetPrimitive.Content>
  </SheetPrimitive.Portal>
))
SheetContent.displayName = 'SheetContent'

function SheetHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('shrink-0 border-b border-slate-100 px-5 py-3.5 pr-14', className)}
      {...props}
    />
  )
}

const SheetTitle = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Title>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Title
    ref={ref}
    className={cn('text-base font-semibold text-slate-950', className)}
    {...props}
  />
))
SheetTitle.displayName = 'SheetTitle'

const SheetDescription = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Description>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Description
    ref={ref}
    className={cn('mt-0.5 text-xs text-slate-400', className)}
    {...props}
  />
))
SheetDescription.displayName = 'SheetDescription'

export { Sheet, SheetClose, SheetContent, SheetDescription, SheetHeader, SheetTitle }
