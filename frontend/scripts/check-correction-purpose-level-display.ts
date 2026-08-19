import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import { correctionLevelDisplay } from "../src/features/baseline-regression/correction-level-display.ts"

const baseItem = {
  expected_level: "L2",
  evaluation: null,
} as any

assert.deepEqual(correctionLevelDisplay(baseItem), {
  level: "L2",
  source: "frozen_expected",
})

assert.deepEqual(
  correctionLevelDisplay({
    ...baseItem,
    evaluation: {
      review_stage: "completed",
      review_truth_status: "completed",
      human_review: { decision: "corrected", corrected_level: "L1" },
      final_level: "L1",
    },
  } as any),
  {
    level: "L1",
    source: "human_correction",
  },
)

assert.deepEqual(
  correctionLevelDisplay({
    ...baseItem,
    evaluation: {
      review_stage: "initial",
      review_truth_status: "provisional",
      final_level: "L1",
      correction_history: [{
        node_type: "final_level",
        node_path: "final_level",
        new_value: "L1",
        reason: "人工确认等级边界",
      }],
    },
  } as any),
  {
    level: "L1",
    source: "human_correction",
  },
)

assert.deepEqual(
  correctionLevelDisplay({
    ...baseItem,
    evaluation: {
      review_stage: "initial",
      review_truth_status: "provisional",
      final_level: "L1",
      correction_history: [{
        node_type: "final_level",
        node_path: "final_level",
        new_value: "L1",
        reason: "自动候选回放",
        corrector_policy: "auto_candidate",
      }],
    },
  } as any),
  {
    level: "L2",
    source: "frozen_expected",
  },
)

assert.deepEqual(
  correctionLevelDisplay({
    ...baseItem,
    evaluation: {
      review_stage: "initial",
      review_truth_status: "provisional",
      human_review: { decision: "corrected", corrected_level: "L1" },
      final_level: "L1",
    },
  } as any),
  {
    level: "L2",
    source: "frozen_expected",
  },
)

const pageSource = readFileSync("src/pages/baseline-regression-page.tsx", "utf8")
const apiSource = readFileSync("src/lib/api.ts", "utf8")
assert.match(pageSource, /全局优化案例池用途（可选）：把偏差样本沉淀到后续自动组批和长期机制优化流程/)
assert.match(pageSource, /不影响当前纠偏分析/)
assert.match(pageSource, /correctionLevelDisplay\(item\)/)
assert.match(apiSource, /purpose: string/)

console.log("correction purpose and human level display contract: passed")
