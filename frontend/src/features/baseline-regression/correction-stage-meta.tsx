import type { BaselineCorrectionRun, BaselineRegressionItem } from "@/lib/types"

export const correctionStages: Array<{
  key: BaselineCorrectionRun["stage"]
  label: string
}> = [
  { key: "analysis", label: "自动分析纠偏样本" },
  { key: "candidate_generation", label: "生成统一机制候选" },
  { key: "candidate_validation", label: "校验并冻结候选" },
  { key: "regression", label: "执行候选回归" },
  { key: "decision", label: "等待人工决策" },
]

export type CorrectionStageState = "pending" | "active" | "completed" | "failed"

export function correctionStageState(
  run: BaselineCorrectionRun,
  stage: BaselineCorrectionRun["stage"],
): CorrectionStageState {
  const currentIndex = correctionStages.findIndex((item) => item.key === run.stage)
  const stageIndex = correctionStages.findIndex((item) => item.key === stage)
  if (run.status === "approved" || run.status === "rejected") return "completed"
  if (stageIndex < currentIndex) return "completed"
  if (stageIndex > currentIndex) return "pending"
  if (run.status === "failed") return "failed"
  return "active"
}

export function correctionStageClassName(state: CorrectionStageState) {
  if (state === "completed") return "border-[#9dbb1c] bg-primary text-[#263000]"
  if (state === "active") return "border-[#9dbb1c] bg-[#f0f8c8] text-[#263000]"
  if (state === "failed") return "border-[#b7362e] bg-[#fff0ee] text-[#8d2924]"
  return "border-[var(--line-strong)] bg-white text-[var(--muted)]"
}

export function correctionStageStateName(state: CorrectionStageState) {
  if (state === "completed") return "已完成"
  if (state === "active") return "进行中"
  if (state === "failed") return "执行失败"
  return "等待自动执行"
}

export function correctionStageLabel(stage: BaselineCorrectionRun["stage"]) {
  return correctionStages.find((item) => item.key === stage)?.label ?? "自动纠偏"
}

export function correctionStageRunningMessage(stage: BaselineCorrectionRun["stage"]) {
  if (stage === "analysis") return "AI 正在分析纠偏样本与偏差方向"
  if (stage === "candidate_generation") return "AI 正在生成统一机制候选"
  if (stage === "candidate_validation") return "系统正在校验并冻结候选版本"
  if (stage === "regression") return "系统正在执行候选回归"
  return "系统正在整理最终决策证据"
}

export function correctionStatusName(run: BaselineCorrectionRun) {
  if (run.status === "processing") return correctionStageLabel(run.stage)
  if (run.status === "awaiting_decision") return "等待人工决策"
  if (run.status === "approved") return "已启用候选"
  if (run.status === "rejected") return "已拒绝候选"
  return "执行失败"
}

export function correctionStatusTone(run: BaselineCorrectionRun): "active" | "success" | "danger" {
  if (run.status === "processing") return "active"
  if (run.status === "awaiting_decision" || run.status === "approved") return "success"
  return "danger"
}

export function reviewStatus(evaluation: BaselineRegressionItem["evaluation"]) {
  if (!evaluation) return null
  if (evaluation.review_panel && evaluation.review_panel.status !== "completed") {
    return {
      label: `审核中 ${evaluation.review_panel.submitted_count}/${evaluation.review_panel.required_reviewers}`,
      tone: "active" as const,
    }
  }
  const decision = evaluation.human_review?.decision
  if (decision === "approved") return { label: "已确认", tone: "success" as const }
  if (decision === "corrected") {
    return {
      label: `已纠偏 · 人工 ${evaluation.final_level ?? "维度"}`,
      tone: "warning" as const,
    }
  }
  if (decision === "rejected") return { label: "已退回", tone: "danger" as const }
  if (evaluation.review_panel) {
    return {
      label: `审核中 ${evaluation.review_panel.submitted_count}/${evaluation.review_panel.required_reviewers}`,
      tone: "active" as const,
    }
  }
  return { label: "待审核", tone: "neutral" as const }
}
