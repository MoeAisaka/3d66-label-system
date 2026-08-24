import * as Dialog from "@radix-ui/react-dialog"

import { Button } from "@/components/ui/button"
import type { ReviewCorrection } from "@/lib/types"

/**
 * 一次待确认的定案动作。
 *
 * 三种决定在服务端都会把 review_stage 置为 completed（见 main.py 的复核提交接口），
 * 也就是说它们都是「定案」而不是「暂存」，所以三者都要过这道确认。
 */
export type PendingReviewDecision = {
  decision: "approved" | "corrected" | "rejected"
  corrected_level: string | null
  reviewNote: string
  corrections?: ReviewCorrection[]
}

type DecisionCopy = {
  title: string
  summary: string
  confirmLabel: string
  /** corrected 会把人工结论写进类目黄金集，是人工真值的入口，必须单独警示 */
  goldenSetWarning?: string
}

const DECISION_COPY: Record<PendingReviewDecision["decision"], DecisionCopy> = {
  approved: {
    title: "确认采纳模型结果",
    summary: "这条样本的模型评测结果将被采纳，复核状态标记为「已完成」。",
    confirmLabel: "确认并进入下一条",
  },
  corrected: {
    title: "保存人工纠偏结果",
    summary: "人工结论将覆盖模型结果，复核状态标记为「已完成」。",
    goldenSetWarning:
      "该结论会写入这个类目的黄金集，作为后续基准回归的人工真值。误判会污染基准，请确认已看完三段评测细节再定案。",
    confirmLabel: "保存并进入下一条",
  },
  rejected: {
    title: "退回这条样本",
    summary: "这条样本将被退回，复核状态标记为「已完成」。",
    confirmLabel: "退回并进入下一条",
  },
}

const LEVEL_HINT = "未改动等级"

export function ReviewDecisionConfirmDialog({
  pending,
  submitting,
  onConfirm,
  onCancel,
}: {
  pending: PendingReviewDecision | null
  submitting: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const copy = pending ? DECISION_COPY[pending.decision] : null

  return (
    <Dialog.Root
      open={Boolean(pending)}
      onOpenChange={(next) => {
        // 提交进行中不允许被 Esc / 点遮罩关掉，否则运营会以为自己取消了但请求已经发出
        if (!next && !submitting) onCancel()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/20" />
        <Dialog.Content
          data-testid="review-decision-confirm-dialog"
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100vh-2rem)] w-[min(520px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col border border-[var(--line-strong)] bg-white shadow-2xl focus:outline-none"
        >
          {copy && pending ? (
            <>
              <div className="border-b border-[var(--line-strong)] px-5 py-4">
                <Dialog.Title className="font-editorial text-xl font-bold">{copy.title}</Dialog.Title>
                <Dialog.Description className="mt-1 text-[0.68rem] leading-5 text-[var(--muted)]">
                  {copy.summary}
                </Dialog.Description>
              </div>

              <div className="flex-1 overflow-y-auto px-5 py-4">
                {copy.goldenSetWarning ? (
                  <p
                    data-testid="review-decision-golden-set-warning"
                    className="mb-3 border border-[#d97706] bg-[#fffbeb] px-3 py-2 text-[0.68rem] leading-5 text-[#92400e]"
                  >
                    {copy.goldenSetWarning}
                  </p>
                ) : null}

                <dl className="space-y-2 text-[0.68rem] leading-5">
                  <div className="flex gap-2">
                    <dt className="w-20 shrink-0 text-[var(--muted)]">最终等级</dt>
                    <dd className="font-medium">{pending.corrected_level ?? LEVEL_HINT}</dd>
                  </div>
                  {pending.decision === "corrected" ? (
                    <div className="flex gap-2">
                      <dt className="w-20 shrink-0 text-[var(--muted)]">维度纠错</dt>
                      <dd className="font-medium">{pending.corrections?.length ?? 0} 项</dd>
                    </div>
                  ) : null}
                  <div className="flex gap-2">
                    <dt className="w-20 shrink-0 text-[var(--muted)]">复核备注</dt>
                    <dd className={pending.reviewNote.trim() ? "font-medium" : "text-[var(--muted)]"}>
                      {pending.reviewNote.trim() || "未填写"}
                    </dd>
                  </div>
                </dl>

                <p className="mt-3 text-[0.68rem] leading-5 text-[var(--muted)]">
                  定案后会自动跳到下一条。逐条纠偏在保存时已分别入库，这一步只决定这条样本的复核结论。
                </p>
              </div>

              <div className="flex justify-end gap-2 border-t border-[var(--line-strong)] px-5 py-3">
                <Button variant="secondary" onClick={onCancel} disabled={submitting}>
                  返回继续看
                </Button>
                <Button onClick={onConfirm} disabled={submitting}>
                  {submitting ? "提交中…" : copy.confirmLabel}
                </Button>
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
