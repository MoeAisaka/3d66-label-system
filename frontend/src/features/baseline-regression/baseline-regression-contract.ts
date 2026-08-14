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

export function baselineRunIdAfterSetLoad(
  currentRunId: number,
  runs: Array<{ id: number }> | null,
): number {
  if (runs === null) return currentRunId
  if (runs.some((run) => run.id === currentRunId)) return currentRunId
  return runs[0]?.id ?? 0
}

export function baselineRunContextPatch(
  currentCategoryKey: string,
  currentBaselineSetId: number,
  target: { categoryKey: string; baselineSetId: number },
): { categoryKey?: string; baselineSetId?: number } {
  if (target.categoryKey !== currentCategoryKey) {
    return { categoryKey: target.categoryKey }
  }
  if (target.baselineSetId !== currentBaselineSetId) {
    return { baselineSetId: target.baselineSetId }
  }
  return {}
}

export function baselineAcceptanceProgressFromPages(
  pages: BaselineAcceptanceRow[][],
  runTerminal = true,
): BaselineAcceptanceProgress {
  return baselineAcceptanceProgress(pages.flat(), runTerminal)
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
