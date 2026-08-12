import { useState } from "react"

import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

import type { ConfigRevision } from "./types"

export function UnknownMechanismSummary({
  detail,
  reason,
}: {
  detail: ConfigRevision | null
  reason?: string
}) {
  const [jsonOpen, setJsonOpen] = useState(false)
  const explanation = reason
    ?? detail?.mechanism_profile.reason
    ?? "当前版本不支持结构化编辑，请先确认机制插件与合同版本。"

  return (
    <section className="border-y border-[var(--line-strong)] bg-white px-5 py-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="warning">只读安全降级</Badge>
        {detail && <span className="font-data text-xs">revision {detail.revision}</span>}
      </div>
      <h2 className="font-editorial mt-4 text-2xl font-bold">当前版本不支持结构化编辑</h2>
      <p className="mt-3 max-w-[72ch] text-sm leading-6 text-[var(--muted)]">{explanation}</p>
      {detail && (
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setJsonOpen(true)}>查看完整 JSON</Button>
        </div>
      )}
      {detail && (
        <SecondaryDrawer
          open={jsonOpen}
          onOpenChange={setJsonOpen}
          title={`${detail.category_key} · revision ${detail.revision}`}
          description="只读合同工件。未知机制不会猜测字段结构，也不会写回运行时投影。"
        >
          <pre className="overflow-x-auto whitespace-pre-wrap break-words bg-[#f6f8f3] p-4 font-data text-xs leading-6">
            {JSON.stringify({
              contract: detail.contract,
              classification_map: detail.classification_map,
              subcategory_dimensions: detail.subcategory_dimensions,
            }, null, 2)}
          </pre>
        </SecondaryDrawer>
      )}
    </section>
  )
}
