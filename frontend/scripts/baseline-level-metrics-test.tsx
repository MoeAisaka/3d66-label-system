import { createRoot } from "react-dom/client"

import {
  computeBaselineLevelBucketMetrics,
  LevelPerformanceSummary,
} from "../src/features/baseline-regression/level-performance-summary"
import type { BaselineLevelMetrics } from "../src/lib/types"
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

const emptyBuckets = computeBaselineLevelBucketMetrics(zeroMetrics)
if (emptyBuckets.some((bucket) => bucket.precision !== null || bucket.recall !== null)) {
  fail("零分母聚合档必须返回空指标")
}

createRoot(document.getElementById("root")!).render(
  <LevelPerformanceSummary metrics={metrics} />,
)

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
  document.body.dataset.testStatus = "passed"
}, 100)
