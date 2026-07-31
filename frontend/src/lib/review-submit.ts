import { api, jsonBody } from "@/lib/api"
import type {
  Evaluation,
  ReviewCorrection,
  ReviewPanelSummary,
} from "@/lib/types"

export type ReviewDecisionInput = {
  evaluation: Evaluation
  reviewer: string
  decision: "approved" | "corrected" | "rejected"
  correctedLevel?: string | null
  note?: string
  corrections?: ReviewCorrection[]
}

export async function submitReviewDecision({
  evaluation,
  reviewer,
  decision,
  correctedLevel = null,
  note = "",
  corrections = [],
}: ReviewDecisionInput) {
  const submitPanelReview = (panel: ReviewPanelSummary) => {
    const adjudicating = panel.status === "lead_adjudication"
    return api(
      `/api/evaluations/${evaluation.id}/review-panel/${
        adjudicating ? "lead-adjudication" : "votes"
      }`,
      {
        method: "POST",
        ...jsonBody({
          ...(adjudicating
            ? { lead_reviewer_name: reviewer }
            : { reviewer_name: reviewer }),
          decision,
          note: note || (adjudicating ? "主审在审核工作台裁决" : ""),
          corrections,
          expected_panel_revision: panel.revision,
        }),
      },
    )
  }

  if (evaluation.review_panel) {
    return submitPanelReview(evaluation.review_panel)
  }
  if (evaluation.review_stage === "initial") {
    const openedPanel = await api<ReviewPanelSummary>(
      `/api/evaluations/${evaluation.id}/review-panel/open`,
      { method: "POST", ...jsonBody({}) },
    )
    return submitPanelReview(openedPanel)
  }
  return api(`/api/evaluations/${evaluation.id}/review`, {
    method: "POST",
    ...jsonBody({
      reviewer_name: reviewer,
      decision,
      corrected_level: correctedLevel,
      note,
      corrections,
      expected_stage: evaluation.review_stage,
      expected_review_revision: evaluation.review_revision,
    }),
  })
}
