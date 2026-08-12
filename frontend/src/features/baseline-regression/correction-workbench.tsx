import type { ReactNode } from "react"
import { ArrowLeft } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { NodeCorrectionEditor } from "@/pages/node-correction-editor"
import type { BaselineRegressionItem, Evaluation } from "@/lib/types"

export function CorrectionWorkbench({
  item,
  onBack,
  corrector,
  onCorrected,
  onPreview,
  children,
}: {
  item: BaselineRegressionItem
  onBack: () => void
  corrector: string
  onCorrected: () => Promise<void> | void
  onPreview: (preview: { src: string; alt: string }) => void
  children?: ReactNode
}) {
  const evaluation = item.evaluation as Evaluation | null
  return <section className="border-y border-[var(--line-strong)] bg-white" aria-label="逐条确认与纠偏">
    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-4 md:px-7">
      <div><div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft />返回轮次列表</Button><h2 className="font-editorial text-2xl font-bold">逐条确认与纠偏</h2></div><p className="mt-2 text-xs leading-5 text-[var(--muted)]">当前素材：{item.asset.name} · 期望 {item.expected_level} · 预测 {item.predicted_level ?? "—"}</p></div>
      <Button variant="secondary" onClick={() => onPreview({ src: item.image_url, alt: item.asset.name })}>查看素材</Button>
    </div>
    <div className="px-5 py-5 md:px-7">{children}</div>
    {evaluation && evaluation.scoring?.dimension_scoring_mode === "rule_deduction" && (
      <NodeCorrectionEditor key={`${evaluation.id}-${evaluation.review_revision}`} evaluation={evaluation} corrector={corrector} onCorrected={onCorrected} />
    )}
  </section>
}
