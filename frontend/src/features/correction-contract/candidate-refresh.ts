import type { CorrectionView } from "./types"

export type MechanismRefresh = {
  category_key: string
  prompt_version_ids: number[]
  v3_revision_id: number
  contract_hash: string
}

export type CandidateRefreshPlan = {
  invalidate: ReadonlyArray<readonly unknown[]>
  preserve: ReadonlyArray<readonly unknown[]>
}

export function candidateRefreshPlan(
  refresh: MechanismRefresh,
  existingRunId: number,
): CandidateRefreshPlan {
  return {
    invalidate: [
      ["evaluation-categories"],
      ["prompts", refresh.category_key],
      ["baseline-v3-revisions", refresh.category_key],
    ],
    preserve: [
      ["baseline-regression", existingRunId],
      ["baseline-correction-view", existingRunId],
    ],
  }
}

export function preservesFrozenCorrectionView(
  view: CorrectionView,
  existingRunId: number,
): boolean {
  return view.run_id === existingRunId && view.snapshot_status !== "legacy_read_only"
}
