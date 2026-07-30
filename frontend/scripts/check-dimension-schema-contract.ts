import assert from "node:assert/strict"

import {
  calculateDimensionPreview,
  dimensionKeys,
  dimensionLabels,
} from "../src/lib/dimension-schema.ts"
import type { EvaluationDimensionSchema } from "../src/lib/types.ts"

const schema: EvaluationDimensionSchema = {
  status: "resolved",
  schema_id: 91,
  schema_key: "dimension.non-eight",
  version: "test-v1",
  canonical_hash: "a".repeat(64),
  legacy_derived: false,
  dimension_keys: ["clarity", "novelty", "utility"],
  definition: {
    dimensions: [
      {
        key: "clarity",
        label: "表达清晰度",
        weight: 0.5,
        grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
      },
      {
        key: "novelty",
        label: "创意新鲜度",
        weight: 0.3,
        grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
      },
      {
        key: "utility",
        label: "业务可用性",
        weight: 0.2,
        grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
      },
    ],
    aggregation: {
      grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
      level_thresholds: { L2: 40, L3: 60, L4: 75, L5: 90 },
      score_round_digits: 2,
    },
    output_contract: {
      dimension_output_keys: ["clarity", "novelty", "utility"],
      unknown_key_policy: "reject",
    },
  },
  error: null,
}

assert.deepEqual(dimensionKeys(schema), ["clarity", "novelty", "utility"])
assert.deepEqual(dimensionLabels(schema), {
  clarity: "表达清晰度",
  novelty: "创意新鲜度",
  utility: "业务可用性",
})
assert.deepEqual(
  calculateDimensionPreview(
    schema,
    { clarity: 5, novelty: 4, utility: 3 },
  ),
  { score: 85.1, level: "L4" },
)
assert.deepEqual(
  calculateDimensionPreview(
    schema,
    { clarity: 5, novelty: 4, utility: 3 },
    [{ cap: "L2" }],
  ),
  { score: 59, level: "L2" },
)

console.log("dimension schema frontend contract: ok")
