import * as Dialog from "@radix-ui/react-dialog"
import { X } from "@phosphor-icons/react"
import { useRef, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function WorkspacePageHeader({
  index,
  title,
  description,
  actions,
}: {
  index?: string
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="border-b border-[var(--line)] bg-white px-5 py-6 md:px-8 lg:px-10">
      <div className="mx-auto flex max-w-[1540px] flex-wrap items-end justify-between gap-5">
        <div className="grid grid-cols-[auto_1fr] items-start gap-4">
          {index && <span className="font-data mt-1 text-xs text-[var(--muted)]">{index}</span>}
          <div>
            <h1 className="font-editorial text-[2rem] font-bold leading-[1.15] md:text-[2.35rem]">{title}</h1>
            {description && <p className="mt-2 max-w-[70ch] text-sm leading-6 text-[var(--muted)]">{description}</p>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}

export function StatusSummaryStrip({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cn("border-y border-[var(--line)] bg-white px-5 py-4", className)}>{children}</section>
}

export function InlineDisclosure({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="border-y border-[var(--line)] py-3">
      <summary className="cursor-pointer text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">{summary}</summary>
      <div className="pt-3 text-sm leading-6 text-[var(--muted)]">{children}</div>
    </details>
  )
}

export interface SecondaryDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  size?: "default" | "wide"
  className?: string
}

function useDialogReturnFocus(open: boolean) {
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const wasOpenRef = useRef(false)

  if (open && !wasOpenRef.current && typeof document !== "undefined") {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
  }
  wasOpenRef.current = open

  return returnFocusRef
}

export function SecondaryDrawer({ open, onOpenChange, title, description, children, footer, size = "default", className }: SecondaryDrawerProps) {
  const returnFocusRef = useDialogReturnFocus(open)

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/20" />
        <Dialog.Content
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex flex-col border-l border-[var(--line-strong)] bg-white shadow-2xl focus:outline-none",
            size === "wide" ? "w-[min(820px,calc(100vw-1rem))]" : "w-[min(680px,calc(100vw-1rem))]",
            className,
          )}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            returnFocusRef.current?.focus()
          }}
        >
          <div className="flex items-start justify-between gap-4 border-b border-[var(--line)] px-6 py-5">
            <div>
              <Dialog.Title className="font-editorial text-2xl font-bold">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-2 text-sm leading-6 text-[var(--muted)]">{description}</Dialog.Description>}
            </div>
            <Dialog.Close asChild>
              <Button variant="secondary" size="icon" aria-label="关闭抽屉"><X size={20} /></Button>
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
          {footer && <div className="border-t border-[var(--line)] px-6 py-4">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "确认",
  cancelLabel = "取消",
  onConfirm,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  children?: ReactNode
}) {
  const returnFocusRef = useDialogReturnFocus(open)

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/20" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(460px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 border border-[var(--line-strong)] bg-white p-6 shadow-2xl focus:outline-none"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            returnFocusRef.current?.focus()
          }}
        >
          <Dialog.Title className="font-editorial text-2xl font-bold">{title}</Dialog.Title>
          {description && <Dialog.Description className="mt-2 text-sm leading-6 text-[var(--muted)]">{description}</Dialog.Description>}
          {children && <div className="mt-4">{children}</div>}
          <div className="mt-6 flex justify-end gap-2">
            <Dialog.Close asChild><Button variant="secondary">{cancelLabel}</Button></Dialog.Close>
            <Button onClick={onConfirm}>{confirmLabel}</Button>
          </div>
          <Dialog.Close asChild><button type="button" aria-label="关闭对话框" className="absolute right-4 top-4 rounded p-1 hover:bg-[#eef1eb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><X size={18} /></button></Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
