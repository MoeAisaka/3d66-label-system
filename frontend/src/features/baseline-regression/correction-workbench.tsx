import type { ReactNode } from "react"
import { ArrowLeft, ArrowRight } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { NodeCorrectionEditor } from "@/pages/node-correction-editor"
import type { BaselineRegressionItem, Evaluation } from "@/lib/types"

export function CorrectionWorkbench({
  item,
  onBack,
  corrector,
  onCorrected,
  onPreview,
  onNext,
  hasNext,
  children,
}: {
  item: BaselineRegressionItem
  onBack: () => void
  corrector: string
  onCorrected: () => Promise<void> | void
  onPreview: (preview: { src: string; alt: string }) => void
  onNext?: () => void
  hasNext?: boolean
  children?: ReactNode
}) {
  const evaluation = item.evaluation as Evaluation | null
  return <section className="border-y border-[var(--line-strong)] bg-white" aria-label="逐条确认与纠偏">
    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-4 md:px-7">
      <div><div className="flex items-center gap-2"><Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft />返回轮次列表</Button><h2 className="font-editorial text-2xl font-bold">逐条确认与纠偏</h2></div><p className="mt-2 text-xs leading-5 text-[var(--muted)]">当前素材：{item.asset.name} · 期望 {item.expected_level} · 预测 {item.predicted_level ?? "—"}</p></div>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => onPreview({ src: item.image_url, alt: item.asset.name })}>放大查看</Button>
        {onNext && <Button variant="secondary" onClick={onNext} disabled={!hasNext}><ArrowRight />下一个</Button>}
      </div>
    </div>
    <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-w-0 px-5 py-5 md:px-7">
        <div className="sticky top-4 overflow-hidden border border-[var(--line-strong)] bg-[#fafbf8]">
          <img src={item.image_url} alt={item.asset.name} className="max-h-[72vh] w-full object-contain" />
          <div className="flex items-center justify-between gap-3 border-t border-[var(--line)] px-4 py-3">
            <p className="truncate text-xs text-[var(--muted)]">{item.asset.name}</p>
            <Button variant="ghost" size="sm" onClick={() => onPreview({ src: item.image_url, alt: item.asset.name })}>查看原图</Button>
          </div>
        </div>
      </div>
      <div className="min-w-0 border-t border-[var(--line-strong)] px-0 py-0 xl:border-l xl:border-t-0">
        {children}
      </div>
    </div>
    {evaluation && evaluation.scoring?.v3_context && (
      <NodeCorrectionEditor key={`${evaluation.id}-${evaluation.review_revision}`} evaluation={evaluation} corrector={corrector} onCorrected={onCorrected} />
    )}
  </section>
}
