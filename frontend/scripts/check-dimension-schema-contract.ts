import assert from "node:assert/strict"

import {
  authoringFamilyForCategory,
  calculateDimensionPreview,
  dimensionAuthoringTemplateCandidates,
  dimensionKeys,
  dimensionLabels,
  isExecutableAuthoringTemplate,
} from "../src/lib/dimension-schema.ts"
import type { DimensionSchemaRegistryItem, EvaluationDimensionSchema } from "../src/lib/types.ts"

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
        description: "评审信息表达是否清晰、准确且易于定位证据",
        weight: 0.5,
        grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
        anchors: { "1": "无法理解", "2": "多处含混", "3": "基本清晰", "4": "明显清晰", "5": "精确且高度易读" },
      },
      {
        key: "novelty",
        label: "创意新鲜度",
        description: "评审创意是否具有新鲜度与差异化证据",
        weight: 0.3,
        grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
        anchors: { "1": "完全陈旧", "2": "较为常见", "3": "普通可用", "4": "具有新意", "5": "高度原创且具有代表性" },
      },
      {
        key: "utility",
        label: "业务可用性",
        description: "评审结果能否直接支撑业务判断与后续动作",
        weight: 0.2,
        grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
        anchors: { "1": "不可用", "2": "需大量修改", "3": "基本可用", "4": "可直接应用", "5": "可稳定驱动决策" },
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

const summary = (patch: Partial<DimensionSchemaRegistryItem>): DimensionSchemaRegistryItem => ({
  id: 1,
  schema_key: "common_core",
  version: "1.0.0",
  schema_type: "core",
  family_key: "common",
  display_name: "通用核心维",
  status: "published",
  parent_schema_id: null,
  core_schema_id: null,
  canonical_hash: "b".repeat(64),
  created_by: "system",
  created_at: "2026-08-03T00:00:00Z",
  published_by: "system",
  published_at: "2026-08-03T00:00:00Z",
  retired_at: null,
  ...patch,
})
const commonCore = summary({ definition: { dimensions: schema.definition!.dimensions } })
const productPack = summary({
  id: 2,
  schema_key: "product_aesthetic",
  schema_type: "family_pack",
  family_key: "product",
  status: "candidate",
})
const spacePack = summary({
  id: 3,
  schema_key: "space_aesthetic",
  schema_type: "family_pack",
  family_key: "space",
})
assert.equal(isExecutableAuthoringTemplate(commonCore.definition), false)
assert.equal(isExecutableAuthoringTemplate(schema.definition), true)
assert.equal(authoringFamilyForCategory("material_image"), "product")
assert.deepEqual(
  dimensionAuthoringTemplateCandidates(
    [commonCore, spacePack, productPack],
    commonCore,
    "material_image",
  ).map((item) => item.schema_key),
  ["product_aesthetic", "space_aesthetic"],
)

console.log("dimension schema frontend contract: ok")
