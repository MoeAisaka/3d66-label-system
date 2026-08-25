/** 上传结果反馈面板 —— 从 assets-page.tsx 抽出。 */

import { Badge } from "@/components/ui/badge"
import type { UploadFeedback } from "./asset-format"

export function UploadFeedbackPanel({ feedback }: { feedback: UploadFeedback }) {
  const groups: Array<{
    key: "successful" | "skipped" | "failed"
    label: string
    tone: "success" | "warning" | "danger"
    items: string[]
  }> = [
    { key: "successful", label: "成功", tone: "success", items: feedback.successful },
    {
      key: "skipped",
      label: "跳过",
      tone: "warning",
      items: feedback.skipped.map((item) => `${item.filename} · ${item.reason}`),
    },
    {
      key: "failed",
      label: "失败",
      tone: "danger",
      items: feedback.failed.map((item) => `${item.filename} · ${item.reason}`),
    },
  ]
  return (
    <div className="border-t border-[var(--line)] bg-[#fafbf8] px-5 py-4" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-bold">{feedback.source}处理结果</p>
        <div className="flex flex-wrap gap-2">
          {groups.map((group) => <Badge key={group.key} tone={group.tone}>{group.label} {group.items.length}</Badge>)}
        </div>
      </div>
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        {groups.map((group) => (
          <details key={group.key} className="border-t border-[var(--line)] pt-2" open={group.key !== "successful" && group.items.length > 0}>
            <summary className="cursor-pointer text-xs font-semibold">{group.label}文件（{group.items.length}）</summary>
            <div className="font-data mt-2 max-h-32 space-y-1 overflow-auto text-[11px] leading-5 text-[var(--muted)]">
              {group.items.length
                ? group.items.map((item, index) => <p className="break-all" key={`${item}-${index}`}>{item}</p>)
                : <p>无</p>}
            </div>
          </details>
        ))}
      </div>
    </div>
  )
}
