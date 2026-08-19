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

import type { BaselineV3Revision } from "@/lib/types"

export type BaselineV3Mode = "active" | "candidate"

export function v3RevisionGroup(
  revision: Pick<BaselineV3Revision, "status" | "id">,
  projectedRevisionId: number,
): "active" | "candidate" | "history" {
  if (revision.id === projectedRevisionId || revision.status === "active") return "active"
  if (revision.status === "candidate") return "candidate"
  return "history"
}

export function isSelectableV3Candidate(
  revision: Pick<BaselineV3Revision, "id" | "status" | "parent_revision_id" | "category_key">,
  revisions: Array<Pick<BaselineV3Revision, "id" | "status" | "parent_revision_id" | "category_key">>,
  projectedRevisionId: number,
): boolean {
  if (revision.status !== "candidate") return false
  const byId = new Map(revisions.map((item) => [item.id, item]))
  let current: typeof revision | undefined = revision
  const seen = new Set<number>()
  while (current && current.id !== projectedRevisionId) {
    if (seen.has(current.id) || current.parent_revision_id == null) return false
    seen.add(current.id)
    current = byId.get(current.parent_revision_id)
  }
  return current?.id === projectedRevisionId
}

export function resolveV3PromptBinding(
  revision: Pick<BaselineV3Revision, "contract">,
  stage: "A" | "B",
): string | null {
  const bindings = revision.contract.prompt_bindings
  if (!bindings || typeof bindings !== "object") return null
  const key = stage === "A" ? "call_a_version" : "call_b_version"
  const value = (bindings as Record<string, unknown>)[key]
  return typeof value === "string" && value.trim() ? value : null
}

export function buildBaselineRunPayload(selection: {
  mode: BaselineV3Mode
  candidateRevisionId?: number | null
  promptMode: "published" | "manual" | "single"
  promptAId?: number | null
  promptBId?: number | null
  executionMode?: "freeform" | "structured"
}): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  if (selection.promptMode === "single" && selection.promptAId) {
    payload.prompt_id = selection.promptAId
  } else if (selection.promptMode === "manual") {
    if (selection.promptAId) payload.prompt_a_id = selection.promptAId
    if (selection.promptBId) payload.prompt_b_id = selection.promptBId
  }
  if (selection.executionMode) payload.execution_mode = selection.executionMode
  if (selection.mode === "candidate" && selection.candidateRevisionId) {
    payload.candidate_revision_id = selection.candidateRevisionId
  }
  return payload
}

export function baselineRunIdAfterSetLoad(
  currentRunId: number,
  runs: Array<{ id: number }> | null,
  pinnedRunId = 0,
): number {
  if (pinnedRunId > 0) return pinnedRunId
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
