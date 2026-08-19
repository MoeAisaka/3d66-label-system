import type { BaselineLevel, BaselineRegressionItem } from "@/lib/types"

const BASELINE_LEVELS = new Set<BaselineLevel>(["L1", "L2", "L3", "L4", "L5"])

function isBaselineLevel(value: string | null | undefined): value is BaselineLevel {
  return typeof value === "string" && BASELINE_LEVELS.has(value as BaselineLevel)
}

export type CorrectionLevelDisplay = {
  level: BaselineLevel
  source: "human_correction" | "frozen_expected"
}

/**
 * The frozen expected level remains the regression truth. This helper only
 * chooses which level the correction-analysis UI should show as its human
 * reference after a completed manual correction.
 */
export function correctionLevelDisplay(
  item: Pick<BaselineRegressionItem, "expected_level" | "evaluation">,
): CorrectionLevelDisplay {
  const evaluation = item.evaluation
  const humanReview = evaluation?.human_review
  const humanLevel = evaluation?.final_level ?? humanReview?.corrected_level
  const hasHumanNodeCorrection = (evaluation?.correction_history ?? []).some((event) => {
    const metadata = event as unknown as {
      corrector_confidence?: unknown
      corrector_policy?: unknown
    }
    return metadata.corrector_confidence == null && !metadata.corrector_policy
  })
  if (
    isBaselineLevel(humanLevel)
    && (
      (
        evaluation?.review_truth_status === "completed"
        && humanReview?.decision === "corrected"
      )
      || hasHumanNodeCorrection
    )
  ) {
    return { level: humanLevel, source: "human_correction" }
  }
  return { level: item.expected_level, source: "frozen_expected" }
}
