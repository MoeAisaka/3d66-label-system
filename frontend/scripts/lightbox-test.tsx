import { useState } from "react"
import { createRoot } from "react-dom/client"

import {
  ImageLightbox,
  ImagePreviewButton,
  type ImagePreview,
} from "../src/components/image-lightbox"

const imageUrl = "/api/assets/42/file"
const assetName = "竖版客厅样本.jpg"

function Harness() {
  const [preview, setPreview] = useState<ImagePreview | null>(null)
  return (
    <>
      <ImagePreviewButton
        src={imageUrl}
        alt={assetName}
        imageClassName="size-12 object-cover"
        onPreview={setPreview}
      />
      <ImageLightbox
        preview={preview}
        onOpenChange={(open) => {
          if (!open) setPreview(null)
        }}
      />
    </>
  )
}

function fail(message: string): never {
  document.body.dataset.testStatus = "failed"
  document.body.dataset.testMessage = message
  throw new Error(message)
}

createRoot(document.getElementById("root")!).render(<Harness />)

setTimeout(() => {
  const trigger = document.querySelector<HTMLButtonElement>(
    `button[aria-label="查看原图：${assetName}"]`,
  )
  if (!trigger) fail("缺少可聚焦的缩略图按钮")
  trigger.click()

  setTimeout(() => {
    const previewImage = document.querySelector<HTMLImageElement>(
      '[data-testid="image-lightbox-image"]',
    )
    if (!previewImage) fail("点击缩略图后未打开预览")
    if (previewImage.getAttribute("src") !== imageUrl) {
      fail("预览图 src 与素材 image_url 不一致")
    }

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    setTimeout(() => {
      if (document.querySelector('[data-testid="image-lightbox-image"]')) {
        fail("按 Escape 后预览未关闭")
      }
      document.body.dataset.testStatus = "passed"
    }, 80)
  }, 80)
}, 80)
