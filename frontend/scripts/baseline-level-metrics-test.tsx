import { useState } from "react"
import { createRoot } from "react-dom/client"

import {
  computeBaselineLevelBucketMetrics,
  LevelPerformanceSummary,
} from "../src/features/baseline-regression/level-performance-summary"
import { FieldMetricsEvidence } from "../src/features/baseline-regression/field-metrics-evidence"
import type { BaselineFieldMetrics, BaselineLevelMetrics } from "../src/lib/types"
import "../src/index.css"

const metrics: BaselineLevelMetrics = {
  schema_version: "baseline-level-metrics-v2",
  levels: ["L1", "L2", "L3", "L4", "L5"],
  total: 100,
  completed: 100,
  pending: 0,
  denominator: 100,
  valid_predictions: 100,
  failed: 0,
  exact_hits: 42,
  adjacent_hits: 87,
  deviations: 58,
  exact_accuracy: 0.42,
  adjacent_accuracy: 0.87,
  confusion_matrix: {
    L1: { L1: 8, L2: 8, L3: 0, L4: 2, L5: 2 },
    L2: { L1: 8, L2: 8, L3: 3, L4: 0, L5: 1 },
    L3: { L1: 0, L2: 8, L3: 6, L4: 5, L5: 1 },
    L4: { L1: 3, L2: 0, L3: 6, L4: 6, L5: 5 },
    L5: { L1: 4, L2: 0, L3: 0, L4: 2, L5: 14 },
  },
}

function fail(message: string): never {
  document.body.dataset.testStatus = "failed"
  document.body.dataset.testMessage = message
  throw new Error(message)
}

const zeroMetrics: BaselineLevelMetrics = {
  ...metrics,
  total: 0,
  completed: 0,
  denominator: 0,
  valid_predictions: 0,
  exact_hits: 0,
  adjacent_hits: 0,
  deviations: 0,
  exact_accuracy: 0,
  adjacent_accuracy: 0,
  confusion_matrix: Object.fromEntries(
    metrics.levels.map((expected) => [
      expected,
      Object.fromEntries(metrics.levels.map((predicted) => [predicted, 0])),
    ]),
  ) as BaselineLevelMetrics["confusion_matrix"],
}

const alternateMetrics: BaselineLevelMetrics = {
  ...metrics,
  exact_hits: 100,
  adjacent_hits: 100,
  deviations: 0,
  exact_accuracy: 1,
  adjacent_accuracy: 1,
  confusion_matrix: {
    L1: { L1: 20, L2: 0, L3: 0, L4: 0, L5: 0 },
    L2: { L1: 0, L2: 20, L3: 0, L4: 0, L5: 0 },
    L3: { L1: 0, L2: 0, L3: 20, L4: 0, L5: 0 },
    L4: { L1: 0, L2: 0, L3: 0, L4: 20, L5: 0 },
    L5: { L1: 0, L2: 0, L3: 0, L4: 0, L5: 20 },
  },
}

const fieldMetrics: BaselineFieldMetrics = {
  schema_version: "baseline-field-metrics-v1",
  run_id: 1,
  category_key: "inspiration_image",
  field_metrics: [
    {
      field_key: "level",
      support: 100,
      tp: 42,
      fp: 58,
      fn: 58,
      accuracy: 0.42,
      recall: 0.42,
      confusion_matrix: metrics.confusion_matrix,
      failure_sample_ids: [7, 9],
    },
  ],
  aggregates: {
    macro: { field_count: 1, accuracy: 0.42, recall: 0.42 },
    micro: { support: 100, tp: 42, fp: 58, fn: 58, accuracy: 0.42, recall: 0.42 },
  },
  failure_sample_ids: [7, 9],
  golden_failure_sample_ids: [7, 9],
  versions: {
    model: ["fixture-model"],
    prompt: { a: ["fixture-a"], b: ["fixture-b"] },
    mechanism: {
      spec_version: "fixture-v3",
      rubric: ["fixture-rubric"],
      engine: ["fixture-engine"],
      strategy_bundle_id: 1,
      strategy_canonical_id: "fixture-strategy",
    },
    asset: {
      baseline_set_fingerprint: "fixture-set",
      count: 100,
      payload_hash: "fixture-payload",
    },
    truth: {
      locked_sample_set_ids: [1],
      revision_min: 1,
      revision_max: 1,
      matched_asset_count: 100,
    },
  },
  decision_policy: {
    evidence_only: true,
    auto_activate_candidate: false,
  },
}

const emptyBuckets = computeBaselineLevelBucketMetrics(zeroMetrics)
if (emptyBuckets.some((bucket) => bucket.precision !== null || bucket.recall !== null)) {
  fail("零分母聚合档必须返回空指标")
}

function Harness() {
  const [selectedMetrics, setSelectedMetrics] = useState(metrics)
  return (
    <>
      <button type="button" onClick={() => setSelectedMetrics(alternateMetrics)}>
        切换历史轮次
      </button>
      <LevelPerformanceSummary metrics={selectedMetrics} />
      <FieldMetricsEvidence
        data={fieldMetrics}
        loading={false}
        error={null}
        levelMetrics={selectedMetrics}
      />
    </>
  )
}

createRoot(document.getElementById("root")!).render(<Harness />)

setTimeout(() => {
  const content = document.body.textContent?.replace(/\s+/g, "") ?? ""
  const expectations = [
    "精确等级准确率42%",
    "相邻等级准确率87%",
    "推荐档L1–L2精确率68.09%召回率80%",
    "常规档L3–L4精确率76.67%召回率57.5%",
    "过滤档L5精确率60.87%召回率70%",
  ]
  for (const expected of expectations) {
    if (!content.includes(expected)) fail(`等级指标缺失：${expected}`)
  }
  const levelEvidence = document.querySelector<HTMLDetailsElement>(
    '[data-testid="baseline-field-metric-level"]',
  )
  if (!levelEvidence) fail("字段证据未保留正式等级明细入口")
  levelEvidence.querySelector<HTMLElement>("summary")?.click()
  const matrix = document.querySelector<HTMLElement>(
    '[data-testid="baseline-five-level-confusion-matrix"]',
  )
  if (!matrix) fail("字段证据未保留 L1–L5 五档矩阵")
  const matrixContent = matrix.textContent?.replace(/\s+/g, "") ?? ""
  for (const level of metrics.levels) {
    if (!matrixContent.includes(level)) fail(`五档矩阵缺少 ${level}`)
  }

  document.querySelector<HTMLButtonElement>("button")?.click()
  setTimeout(() => {
    const exactValue = document.querySelector<HTMLElement>(
      '[data-testid="baseline-exact-accuracy"]',
    )?.textContent
    const recommendedValue = document.querySelector<HTMLElement>(
      '[data-testid="baseline-bucket-recommended"]',
    )?.textContent?.replace(/\s+/g, "")
    if (exactValue !== "100%") fail(`切换历史轮次后精确准确率未刷新：${exactValue ?? "缺失"}`)
    if (!recommendedValue?.includes("精确率100%召回率100%")) {
      fail("切换历史轮次后推荐档指标未刷新")
    }
    document.body.dataset.testStatus = "passed"
  }, 80)
}, 100)
