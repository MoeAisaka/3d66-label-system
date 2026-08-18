import * as Dialog from "@radix-ui/react-dialog"
import { X } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

export type ImagePreview = {
  src: string
  alt: string
}

export function ImagePreviewButton({
  src,
  alt,
  imageClassName,
  onPreview,
}: {
  src: string
  alt: string
  imageClassName?: string
  onPreview: (preview: ImagePreview) => void
}) {
  return (
    <button
      type="button"
      aria-label={`查看原图：${alt}`}
      className="shrink-0 cursor-zoom-in rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      onClick={() => onPreview({ src, alt })}
    >
      <img
        src={src}
        alt=""
        className={cn("block border border-[var(--line)] object-cover", imageClassName)}
      />
    </button>
  )
}

export function ImageReferenceDock({
  preview,
  onOpenChange,
}: {
  preview: ImagePreview | null
  onOpenChange: (open: boolean) => void
}) {
  if (!preview) return null

  return (
    <aside
      aria-label="原图参考浮窗"
      className="sticky top-4 z-20 mt-3 overflow-hidden border border-[var(--line-strong)] bg-white shadow-xl"
    >
      <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-bold">原图参考浮窗</p>
          <p className="mt-1 truncate text-[0.68rem] text-[var(--muted)]">{preview.alt}</p>
        </div>
        <button
          type="button"
          aria-label="关闭原图参考浮窗"
          className="flex size-8 shrink-0 items-center justify-center rounded-[4px] border border-[var(--line-strong)] bg-white text-foreground hover:bg-[#eef1eb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          onClick={() => onOpenChange(false)}
        >
          <X size={18} />
        </button>
      </div>
      <div
        data-testid="image-reference-dock-canvas"
        className="flex max-h-[calc(100dvh-12rem)] min-h-64 items-center justify-center overflow-auto p-3"
        style={{
          backgroundColor: "#eef0eb",
          backgroundImage:
            "linear-gradient(45deg,#cfd4ca 25%,transparent 25%),linear-gradient(-45deg,#cfd4ca 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#cfd4ca 75%),linear-gradient(-45deg,transparent 75%,#cfd4ca 75%)",
          backgroundPosition: "0 0,0 8px,8px -8px,-8px 0",
          backgroundSize: "16px 16px",
        }}
      >
        <img
          data-testid="image-reference-dock-image"
          src={preview.src}
          alt={preview.alt}
          className="block h-auto max-h-[calc(100dvh-14rem)] w-auto max-w-full border border-black/60 object-contain"
        />
      </div>
    </aside>
  )
}

export function ImageLightbox({
  preview,
  onOpenChange,
}: {
  preview: ImagePreview | null
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog.Root open={preview !== null} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100dvh-2rem)] w-[min(96vw,96rem)] -translate-x-1/2 -translate-y-1/2 items-center justify-center overflow-hidden border border-[var(--line-strong)] bg-white p-4 shadow-xl focus:outline-none"
        >
          <Dialog.Title className="sr-only">
            原图预览{preview ? `：${preview.alt}` : ""}
          </Dialog.Title>
          <Dialog.Close
            aria-label="关闭原图预览"
            className="absolute right-3 top-3 z-10 flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)] bg-white text-foreground hover:bg-[#eef1eb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <X size={20} />
          </Dialog.Close>
          {preview && (
            <div
              data-testid="image-lightbox-inspection-canvas"
              className="flex max-h-[calc(100dvh-4rem)] max-w-full items-center justify-center overflow-auto p-3"
              style={{
                backgroundColor: "#eef0eb",
                backgroundImage:
                  "linear-gradient(45deg,#cfd4ca 25%,transparent 25%),linear-gradient(-45deg,#cfd4ca 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#cfd4ca 75%),linear-gradient(-45deg,transparent 75%,#cfd4ca 75%)",
                backgroundPosition: "0 0,0 8px,8px -8px,-8px 0",
                backgroundSize: "16px 16px",
              }}
            >
              <img
                data-testid="image-lightbox-image"
                src={preview.src}
                alt={preview.alt}
                className="block h-auto max-h-[calc(100dvh-6rem)] w-auto max-w-full border border-black/60 object-contain"
              />
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
