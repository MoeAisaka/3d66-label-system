import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowClockwise, WarningCircle } from "@phosphor-icons/react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { ApiError, baselineRegressionApi } from "@/lib/api"
import type { BaselineCandidateRebasePlan, BaselineV3Revision } from "@/lib/types"

type GateRegression = {
  code?: string
  message?: string
  field_key?: string
  accuracy_delta?: number
  recall_delta?: number
  failure_sample_ids?: number[]
}

function signedPercent(value: unknown): string | null {
  if (typeof value !== "number" || Number.isNaN(value)) return null
  const percent = value * 100
  const sign = percent > 0 ? "+" : ""
  return `${sign}${percent.toFixed(1)}%`
}

/**
 * Render why the release gate refused a candidate.
 *
 * The backend already reports every blocking reason, but a bare toast throws it
 * all away and leaves the operator with "rejected" and nothing to act on.
 */
export function CandidateGateRejection({ error }: { error: ApiError | null }) {
  if (!error) return null
  const detail = error.detail ?? {}
  const code = typeof detail.code === "string" ? detail.code : undefined
  const regressions = Array.isArray(detail.regressions)
    ? (detail.regressions as GateRegression[])
    : []
  const conflicts = Array.isArray(detail.conflicts)
    ? (detail.conflicts as Array<{ path?: string; reason?: string }>)
    : []

  return (
    <div
      className="mt-3 border border-[#d7a09d] bg-[#fff5f4] px-4 py-3 text-sm text-[#8d2924]"
      data-testid="candidate-gate-rejection"
    >
      <div className="flex items-start gap-2">
        <WarningCircle className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="font-semibold">{error.message || "候选启用被拒绝"}</div>
          {code ? <div className="font-data mt-0.5 text-xs">{code}</div> : null}

          {code === "candidate_ancestry_conflict" ? (
            <div className="mt-2 text-xs leading-5">
              该候选不是现役版本的直接子代，直接启用会丢弃现役版本引入的改动。
              可用下方「变基到现役版本」生成一个挂在现役之上的等效候选，再重新回归。
            </div>
          ) : null}

          {regressions.length ? (
            <ul className="mt-2 space-y-1.5 text-xs leading-5">
              {regressions.map((item, index) => {
                const accuracy = signedPercent(item.accuracy_delta)
                const recall = signedPercent(item.recall_delta)
                return (
                  <li key={`${item.code ?? "regression"}-${item.field_key ?? index}`}>
                    <span className="font-semibold">{item.message ?? item.code}</span>
                    {accuracy || recall ? (
                      <span className="font-data ml-2">
                        {accuracy ? `准确率 ${accuracy}` : ""}
                        {accuracy && recall ? " · " : ""}
                        {recall ? `召回率 ${recall}` : ""}
                      </span>
                    ) : null}
                    {item.failure_sample_ids?.length ? (
                      <span className="ml-2">失败样本 {item.failure_sample_ids.length} 个</span>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          ) : null}

          {conflicts.length ? (
            <ul className="mt-2 space-y-1 text-xs leading-5">
              {conflicts.map((item) => (
                <li key={item.path}>
                  <span className="font-data break-all">{item.path}</span>
                  <span className="ml-2">{item.reason}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/**
 * Offer a rebase for a candidate that branched away from the active revision.
 *
 * Preview first: the operator sees which of their changes carry over and any
 * conflicts before anything is written.  Rebasing appends a new candidate and
 * leaves the diverged one untouched.
 */
export function CandidateRebasePanel({
  categoryKey,
  candidate,
  activeRevision,
  onRebased,
}: {
  categoryKey: string
  candidate: BaselineV3Revision
  activeRevision: BaselineV3Revision
  onRebased?: (revision: BaselineV3Revision) => void
}) {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)

  const preview = useQuery<BaselineCandidateRebasePlan>({
    queryKey: ["candidate-rebase-preview", categoryKey, candidate.revision, activeRevision.revision],
    queryFn: () => baselineRegressionApi.previewV3Rebase(categoryKey, candidate.revision),
    enabled: expanded,
  })

  const rebase = useMutation({
    mutationFn: () => baselineRegressionApi.rebaseV3Revision(categoryKey, candidate.revision, {
      expected_projected_revision: activeRevision.revision,
      expected_projected_contract_hash: activeRevision.contract_hash,
    }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["baseline-v3-revisions", categoryKey] })
      toast.success(`已生成 Revision ${created.revision}，挂在现役 Revision ${activeRevision.revision} 之上`)
      onRebased?.(created)
    },
    onError: (error) => toast.error(error.message),
  })

  const plan = preview.data
  const conflicts = plan?.conflicts ?? []
  const rebaseError = rebase.error instanceof ApiError ? rebase.error : null

  return (
    <div className="mt-3 border border-[#d8b070] bg-[#fdf7ea] px-4 py-3 text-sm text-[#7a5312]" data-testid="candidate-rebase-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold">该候选与现役版本已分叉，无法直接启用</div>
          <div className="mt-1 text-xs leading-5">
            Revision {candidate.revision} 挂在 Revision {candidate.parent_revision_id ?? "—"} 之上，
            而现役已是 Revision {activeRevision.revision}。变基会生成一个把本候选改动重放到现役之上的新候选，
            原候选保持不变。
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setExpanded((value) => !value)}>
          <ArrowClockwise />{expanded ? "收起" : "查看变基影响"}
        </Button>
      </div>

      {expanded ? (
        <div className="mt-3 border-t border-[#e5d3ac] pt-3">
          {preview.isLoading ? <div className="h-16 animate-pulse bg-[#f6eed9]" /> : null}
          {preview.error ? (
            <div className="text-xs text-[#8d2924]">变基预览失败：{preview.error.message}</div>
          ) : null}
          {plan && !plan.needed ? (
            <div className="text-xs">{plan.reason ?? "该候选无需变基。"}</div>
          ) : null}
          {plan && plan.needed ? (
            <div className="space-y-3">
              <div className="text-xs leading-5">
                共同祖先 Revision {plan.base_revision ?? plan.base_revision_id}；
                将把本候选的 {plan.adopted_changes.length} 处改动重放到 Revision {plan.onto_revision ?? activeRevision.revision} 之上。
              </div>
              {plan.adopted_changes.length ? (
                <details className="text-xs">
                  <summary className="cursor-pointer font-semibold">带过来的改动</summary>
                  <ul className="font-data mt-1.5 space-y-0.5 break-all">
                    {plan.adopted_changes.map((change) => <li key={change}>{change}</li>)}
                  </ul>
                </details>
              ) : null}
              {conflicts.length ? (
                <div className="border border-[#d7a09d] bg-[#fff5f4] px-3 py-2 text-xs text-[#8d2924]">
                  <div className="font-semibold">存在 {conflicts.length} 处冲突，需人工决定取舍后才能变基</div>
                  <ul className="mt-1 space-y-0.5">
                    {conflicts.map((conflict) => (
                      <li key={conflict.path}>
                        <span className="font-data break-all">{conflict.path}</span>
                        <span className="ml-2">{conflict.reason}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <Button
                  size="sm"
                  disabled={rebase.isPending}
                  onClick={() => {
                    if (window.confirm(`确认生成变基候选？将基于现役 Revision ${activeRevision.revision} 新建一个候选，原候选保持不变。`)) {
                      rebase.mutate()
                    }
                  }}
                >
                  <ArrowClockwise />{rebase.isPending ? "正在变基" : `变基到 Revision ${activeRevision.revision}`}
                </Button>
              )}
              <CandidateGateRejection error={rebaseError} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
