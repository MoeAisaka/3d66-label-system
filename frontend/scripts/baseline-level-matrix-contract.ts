import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import { computeBaselineLevelMatrixMetrics } from "../src/features/baseline-regression/level-metrics.ts"

const levels = ["L1", "L2", "L3", "L4", "L5"] as const
const metrics = {
  levels: [...levels],
  confusion_matrix: Object.fromEntries(levels.map((expected) => [
    expected,
    Object.fromEntries(levels.map((predicted) => [predicted, expected === predicted && expected !== "L2" ? 2 : 0])),
  ])),
} as never
const result = computeBaselineLevelMatrixMetrics(metrics)
assert.deepEqual(result.levels, levels)
assert.equal(result.recallByLevel.L1, 1)
assert.equal(result.precisionByLevel.L5, 1)
assert.equal(result.recallByLevel.L2, null)

const source = readFileSync(new URL("../src/features/baseline-regression/level-performance-summary.tsx", import.meta.url), "utf8")
assert.match(source, /baseline-level-matrix/)
assert.match(source, /data-testid=\{`baseline-level-cell-\$\{expected\}-\$\{predicted\}`\}/)
assert.match(source, /Precision/)
assert.match(source, /召回率/)

console.log("baseline level matrix contract: ok")
