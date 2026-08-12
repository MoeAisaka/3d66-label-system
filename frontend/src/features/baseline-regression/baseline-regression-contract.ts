export type BaselineAcceptanceRow = {
  status: "queued" | "completed" | "failed"
  evaluation?: {
    human_review?: {
      decision?: "approved" | "corrected" | "rejected" | null
    } | null
  } | null
}

export type BaselineAcceptanceProgress = {
  reviewed: number
  total: number
  complete: boolean
}

export function baselineAcceptanceProgress(
  rows: BaselineAcceptanceRow[],
  runTerminal = true,
): BaselineAcceptanceProgress {
  const evaluable = rows.filter((row) => row.status === "completed" && row.evaluation)
  const reviewed = evaluable.filter((row) => (
    row.evaluation?.human_review?.decision === "approved"
    || row.evaluation?.human_review?.decision === "corrected"
    || row.evaluation?.human_review?.decision === "rejected"
  )).length
  const failedOrUnscored = rows.some((row) => row.status !== "completed" || !row.evaluation)
  return {
    reviewed,
    total: evaluable.length,
    complete: runTerminal && !failedOrUnscored && evaluable.length > 0 && reviewed === evaluable.length,
  }
}
