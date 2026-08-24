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

// 2026-08-24：基准回归页从 3250 行拆成 baseline-regression-page.tsx + features/baseline-regression/* 若干模块。
// 契约要守的是「基准回归这块界面整体」符合合同，而不是内容挤在同一个文件里，
// 所以这里把页面与抽出的模块拼起来一起校验：断言语义不变，拆分也不会让合同失效。
const pageSource = [
  readFileSync("src/pages/baseline-regression-page.tsx", "utf8"),
  ...[
  "regression-page-shared.tsx",
  "regression-results.tsx",
  "correction-analysis-panel.tsx",
  "level-explanation.tsx",
  "field-metrics-evidence.tsx",
  "form-selects.tsx",
  "correction-stage-meta.tsx",
  ].map((f) => readFileSync(`src/features/baseline-regression/${f}`, "utf8")),
].join("\n")
const apiSource = readFileSync("src/lib/api.ts", "utf8")
assert.match(pageSource, /全局优化案例池用途（可选）：把偏差样本沉淀到后续自动组批和长期机制优化流程/)
assert.match(pageSource, /不影响当前纠偏分析/)
assert.match(pageSource, /correctionLevelDisplay\(item\)/)
assert.match(pageSource, /冻结预期等级/)
assert.match(pageSource, /尚未保存人工纠偏等级/)
assert.match(apiSource, /purpose: string/)

console.log("correction purpose and human level display contract: passed")
