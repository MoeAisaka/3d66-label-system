import {
  candidateRefreshPlan,
  preservesFrozenCorrectionView,
  type MechanismRefresh,
} from "./candidate-refresh.ts"
import type { CorrectionView } from "./types"

const refresh: MechanismRefresh = {
  category_key: "inspiration_image",
  prompt_version_ids: [101, 202],
  v3_revision_id: 303,
  contract_hash: "new-contract-hash",
}

const plan = candidateRefreshPlan(refresh, 77)
function assertEqual(actual: unknown, expected: unknown, label: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`)
  }
}

assertEqual(plan.invalidate, [
  ["evaluation-categories"],
  ["prompts", "inspiration_image"],
  ["baseline-v3-revisions", "inspiration_image"],
], "invalidate")
assertEqual(plan.preserve, [
  ["baseline-regression", 77],
  ["baseline-correction-view", 77],
], "preserve")

const view: CorrectionView = {
  schema_version: "correction-view-v1",
  lane: "baseline",
  run_id: 77,
  item_id: 88,
  evaluation_id: 99,
  category_key: "inspiration_image",
  snapshot_status: "frozen",
  read_only: false,
  contract: {
    contract_version: "v1",
    contract_hash: "old-contract-hash",
    category_key: "inspiration_image",
  },
  review_revision: 0,
  nodes: [],
}
if (!preservesFrozenCorrectionView(view, 77)) throw new Error("frozen view should be preserved")
if (preservesFrozenCorrectionView(view, 78)) throw new Error("different run must not be preserved")

console.log("candidate refresh checks passed")
