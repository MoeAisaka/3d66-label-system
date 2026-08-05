import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
  buildCorrectionNodes,
  confidenceLabel,
  EMPTY_CORRECTION_HISTORY_TEXT,
  formatRuleConfidence,
  normalizeRuleHits,
  redlineReasonsAfterToggle,
  ruleEvidenceDelta,
} from "../src/lib/node-correction.ts"

const evaluation = {
  level: "L2",
  precheck: {
    classification: {
      scope_status: "in_scope",
      primary_category: "建筑设计",
      primary_confidence: 0.96,
    },
    production_fields: { trait: "实景照片", reason: [] },
  },
  aesthetic: {
    bridge_version: "dimension-deduction-bridge-v2",
    dimensions: {
      visual_structure: {
        hit_rules: [{ rule_id: "r1", confidence: "high", evidence: "主体明显偏移" }],
      },
    },
  },
  scoring: {
    score: 70,
    level: "L2",
    track_key: "class_one",
    hit_rules: [],
    v3_context: {
      config_revision: 3,
      contract: {
        redline_policy: {
          rules: [{
            key: "screenshot",
            label: "截图",
            match_any: ["是截图"],
            exemptions: [],
          }],
        },
        track_classification: {
          tracks: [{ key: "class_one", label: "一类（建筑/室内/景观/规划）" }],
        },
      },
      subcategory_dimensions: {
        class_one: {
          common_group: {
            schema_definition: {
              dimensions: [{
                key: "visual_structure",
                label: "视觉结构",
                deduction_rules: [
                  { rule_id: "r1", description: "主体结构明显失衡", deduction: 20 },
                  { rule_id: "r2", description: "视觉层级不清晰", deduction: 10 },
                ],
              }],
            },
          },
          specific_group: null,
        },
      },
    },
  },
}

const nodes = buildCorrectionNodes(evaluation)
assert.deepEqual([...new Set(nodes.map((node) => node.stage))], [1, 2, 3, 4, 5])
assert.equal(nodes.find((node) => node.id === "precheck:production_fields.trait")?.label, "媒介类型")
assert.equal(nodes.find((node) => node.id === "redline:screenshot")?.summary, "未命中")
assert.equal(nodes.find((node) => node.id === "track:track_key")?.summary, "一类（建筑/室内/景观/规划）")

const dimension = nodes.find((node) => node.id === "dimension:visual_structure")
assert(dimension)
assert.equal(dimension.summary, "命中 1 / 2 条")
assert.equal(dimension.ruleDefinitions?.[0].description, "主体结构明显失衡")
assert.equal(dimension.evidenceLines[0], "r1 · 置信度高 · 主体明显偏移")

// UI uses Chinese labels while requests and persisted values stay canonical.
assert.equal(confidenceLabel("medium"), "中")
assert.equal(confidenceLabel("中"), "中")
assert.equal(formatRuleConfidence("medium"), "中")
assert.equal(formatRuleConfidence(0.82), "82%")
assert.equal(formatRuleConfidence("not-a-confidence"), "未知")
assert(!formatRuleConfidence("medium").includes("NaN"))
assert.deepEqual(
  normalizeRuleHits([{ rule_id: "legacy", confidence: "中", evidence: "历史中文值" }]),
  [{ rule_id: "legacy", confidence: "medium", evidence: "历史中文值" }],
)
assert.equal(EMPTY_CORRECTION_HISTORY_TEXT, "暂无纠偏历史。旧评测没有纠偏记录时会安全显示为空。")
assert(!EMPTY_CORRECTION_HISTORY_TEXT.includes("correction_history"))

const redline = nodes.find((node) => node.id === "redline:screenshot")
assert(redline?.redlineRule)
assert.deepEqual(redlineReasonsAfterToggle(redline.currentValue, redline.redlineRule, true), ["是截图"])
assert.deepEqual(redlineReasonsAfterToggle(["是截图", "是多拼图"], redline.redlineRule, false), ["是多拼图"])

assert.deepEqual(
  ruleEvidenceDelta(
    dimension.currentValue,
    [
      { rule_id: "r1", confidence: "low", evidence: "证据强度下调" },
      { rule_id: "r2", confidence: "medium", evidence: "层级关系模糊" },
    ],
  ),
  [
    {
      rule_id: "r1",
      old_confidence: "high",
      new_confidence: "low",
      old_evidence: "主体明显偏移",
      new_evidence: "证据强度下调",
    },
    {
      rule_id: "r2",
      old_confidence: null,
      new_confidence: "medium",
      old_evidence: "",
      new_evidence: "层级关系模糊",
    },
  ],
)

assert.deepEqual(
  [...new Set(buildCorrectionNodes({ ...evaluation, scoring: { score: 70 } }).map((node) => node.stage))],
  [1, 3, 5],
)

const legacyPathEvaluation = structuredClone(evaluation)
legacyPathEvaluation.scoring.v3_context.subcategory_dimensions.class_one.common_group = {
  dimensions: [{
    key: "visual_structure",
    label: "视觉结构（旧路径）",
    deduction_rules: [
      { rule_id: "r1", description: "旧路径主体结构明显失衡", deduction: 20 },
    ],
  }],
}
const legacyPathNodes = buildCorrectionNodes(legacyPathEvaluation)
assert.equal(
  legacyPathNodes.find((node) => node.id === "dimension:visual_structure")?.label,
  "视觉结构（旧路径）",
)

const mismatchedEvaluation = structuredClone(evaluation)
mismatchedEvaluation.aesthetic.dimensions = {
  legacy_only: {
    hit_rules: [{ rule_id: "legacy-rule", confidence: "medium", evidence: "旧规则证据" }],
  },
}
const mismatchedNodes = buildCorrectionNodes(mismatchedEvaluation)
const configuredButMissing = mismatchedNodes.find((node) => node.id === "dimension:visual_structure")
assert(configuredButMissing)
assert.equal(configuredButMissing.readOnly, true)
assert.match(configuredButMissing.compatibilityMessage ?? "", /旧引擎产出/)
const resultOnlyDimension = mismatchedNodes.find((node) => node.id === "dimension:legacy_only")
assert(resultOnlyDimension)
assert.equal(resultOnlyDimension.readOnly, true)
assert.match(resultOnlyDimension.compatibilityMessage ?? "", /版本不一致/)

const unknownRuleEvaluation = structuredClone(evaluation)
unknownRuleEvaluation.aesthetic.dimensions.visual_structure.hit_rules = [{
  rule_id: "unknown-rule",
  confidence: "medium",
  evidence: "历史未知规则",
}]
const unknownRuleNode = buildCorrectionNodes(unknownRuleEvaluation)
  .find((node) => node.id === "dimension:visual_structure")
assert(unknownRuleNode)
assert.equal(unknownRuleNode.readOnly, true)

const alignedDimension = nodes.find((node) => node.id === "dimension:visual_structure")
assert(alignedDimension)
assert.notEqual(alignedDimension.readOnly, true)

const baselineSource = readFileSync(
  new URL("../src/pages/baseline-regression-page.tsx", import.meta.url),
  "utf8",
)
assert.match(
  baselineSource,
  /import \{ NodeCorrectionEditor \} from "@\/pages\/node-correction-editor"/,
)
assert.match(
  baselineSource,
  /evaluation\.scoring\?\.dimension_scoring_mode === "rule_deduction"/,
)
assert.match(baselineSource, /<NodeCorrectionEditor/)

console.log("node correction editor frontend contract: ok")
