import * as Dialog from "@radix-ui/react-dialog"
import { X } from "@phosphor-icons/react"
import { useRef, type ReactNode } from "react"

import { Button } from "@/components/ui/button"

export function BaselineSetDialog({
  open,
  onOpenChange,
  children,
  footer,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
  footer?: ReactNode
}) {
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const wasOpenRef = useRef(false)

  if (open && !wasOpenRef.current && typeof document !== "undefined") {
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
  }
  wasOpenRef.current = open

  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/20" />
      <Dialog.Content
        className="fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100vh-2rem)] w-[min(1080px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col border border-[var(--line-strong)] bg-white shadow-2xl focus:outline-none"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocusRef.current?.focus()
        }}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[var(--line)] px-6 py-5">
          <div><Dialog.Title className="font-editorial text-2xl font-bold">选择基准集</Dialog.Title><Dialog.Description className="mt-1 text-sm text-[var(--muted)]">冻结素材与人工期望等级，创建后不可修改。</Dialog.Description></div>
          <Dialog.Close asChild><Button variant="secondary" size="icon" aria-label="关闭基准集对话框"><X size={18} /></Button></Dialog.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="border-t border-[var(--line)] px-6 py-4">{footer}</div>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}
