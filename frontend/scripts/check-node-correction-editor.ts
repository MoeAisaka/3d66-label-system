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
  score: 70,
  level: "L2",
  precheck: {
    classification: {
      scope_status: "in_scope",
      primary_category: "建筑设计",
      primary_confidence: 0.96,
    },
    production_fields: {
      title: "现代住宅",
      seotitle: "现代住宅空间设计参考",
      category: "居住空间,大平层",
      style: "现代简约",
      tags: ["住宅", "客厅", "木饰面", "自然光"],
      cons: "局部层次略显单薄",
      design: "以自然光和材质层次组织空间",
      score: 70,
      reason: [],
      image_defects: "",
      trait: "实景照片",
    },
  },
  aesthetic: {
    bridge_version: "dimension-deduction-bridge-v2",
    dimensions: {
      visual_structure: {
        hit_rules: [{ rule_id: "r1", confidence: "high", evidence: "主体明显偏移" }],
        hit_bonus_rules: [{ rule_id: "b1", confidence: "medium", evidence: "构图层级清晰" }],
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
                bonus_rules: [
                  { rule_id: "b1", description: "构图层级清晰完整", bonus: 8 },
                  { rule_id: "b2", description: "留白关系舒适", bonus: 4 },
                ],
                dimension_score_cap: 100,
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
const callAFields = [
  "score", "grade", "title", "seotitle", "style", "cons", "design",
  "category", "tags", "trait", "reason", "image_defects",
]
const callANodes = nodes.filter((node) => node.nodeType === "call_a_field")
assert.deepEqual(callANodes.map((node) => node.nodePath), callAFields.map((field) => `call_a.${field}`))
assert.equal(callANodes.length, 12)
assert.deepEqual([...new Set(callANodes.map((node) => node.group))], ["rating", "copy", "classification", "defect"])
assert.equal(nodes.find((node) => node.id === "call-a:score")?.valueKind, "score")
assert.equal(nodes.find((node) => node.id === "call-a:grade")?.editor, "level")
assert.equal(nodes.find((node) => node.id === "call-a:title")?.maxLength, 10)
assert.equal(nodes.find((node) => node.id === "call-a:seotitle")?.maxLength, 28)
assert.equal(nodes.find((node) => node.id === "call-a:category")?.editor, "category")
assert.equal(nodes.find((node) => node.id === "call-a:tags")?.editor, "tags")
assert.equal(nodes.find((node) => node.id === "call-a:cons")?.valueKind, "multiline")
assert.deepEqual(
  nodes.find((node) => node.id === "call-a:reason")?.options?.map((option) => option.value),
  ["是截图"],
)
assert.equal(nodes.find((node) => node.id === "call-a:image_defects")?.options?.length, 2)
assert.equal(nodes.find((node) => node.id === "call-a:trait")?.label, "媒介类型")
assert.equal(nodes.find((node) => node.id === "redline:screenshot")?.summary, "未命中")
assert.equal(nodes.find((node) => node.id === "track:track_key")?.summary, "一类（建筑/室内/景观/规划）")

const contractOwnedReasonEvaluation = structuredClone(evaluation)
contractOwnedReasonEvaluation.scoring.v3_context.contract.redline_policy.rules = [
  {
    key: "transparent_checkerboard",
    label: "透明棋盘格",
    match_any: ["透明棋盘格", "手绘草稿"],
    exemptions: [],
  },
]
const contractOwnedReasonNode = buildCorrectionNodes(contractOwnedReasonEvaluation)
  .find((node) => node.id === "call-a:reason")
assert.deepEqual(
  contractOwnedReasonNode?.options?.map((option) => option.value),
  ["透明棋盘格", "手绘草稿"],
)

const dimension = nodes.find((node) => node.id === "dimension:visual_structure")
assert(dimension)
assert.equal(dimension.summary, "命中 1 / 2 条")
assert.equal(dimension.ruleDefinitions?.[0].description, "主体结构明显失衡")
assert.equal(dimension.ruleDefinitions?.[0].kind, "deduction")
assert.equal(dimension.ruleDefinitions?.[0].value, 20)
assert.equal(dimension.evidenceLines[0], "r1 · 置信度高 · 主体明显偏移")

const bonusDimension = nodes.find((node) => node.id === "dimension:visual_structure:bonus")
assert(bonusDimension)
assert.equal(bonusDimension.nodePath, "dimension.visual_structure.hit_bonus_rules")
assert.equal(bonusDimension.summary, "命中 1 / 2 条")
assert.equal(bonusDimension.ruleDefinitions?.[0].kind, "bonus")
assert.equal(bonusDimension.ruleDefinitions?.[0].value, 8)
assert.equal(bonusDimension.evidenceLines[0], "加分 · b1 · 置信度中 · 构图层级清晰")

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

const legacyCallAEvaluation = structuredClone(evaluation)
delete legacyCallAEvaluation.precheck.production_fields.title
delete legacyCallAEvaluation.precheck.production_fields.tags
const legacyCallANodes = buildCorrectionNodes(legacyCallAEvaluation)
assert.equal(legacyCallANodes.filter((node) => node.nodeType === "call_a_field").length, 12)
assert.equal(legacyCallANodes.find((node) => node.id === "call-a:title")?.readOnly, true)
assert.match(legacyCallANodes.find((node) => node.id === "call-a:title")?.compatibilityMessage ?? "", /旧评测未存储/)
assert.equal(legacyCallANodes.find((node) => node.id === "call-a:tags")?.summary, "未存储")

const baselineSource = readFileSync(
  new URL("../src/pages/baseline-regression-page.tsx", import.meta.url),
  "utf8",
)
const correctionWorkbenchSource = readFileSync(
  new URL("../src/features/baseline-regression/correction-workbench.tsx", import.meta.url),
  "utf8",
)
assert.match(
  correctionWorkbenchSource,
  /evaluation\.scoring\?\.v3_context/,
)
assert.match(correctionWorkbenchSource, /<NodeCorrectionEditor/)

const editorSource = readFileSync(
  new URL("../src/pages/node-correction-editor.tsx", import.meta.url),
  "utf8",
)
assert.match(editorSource, /一级分类/)
assert.match(editorSource, /二级分类/)
assert.match(editorSource, /添加标签/)
assert.match(editorSource, /最多 \{node\.maxLength\} 个字/)
assert.match(editorSource, /rule\.kind === "bonus"/)
assert.match(editorSource, /加分/)
assert.match(editorSource, /data-testid="node-correction-node-list"/)
assert.match(editorSource, /data-testid="node-correction-detail"/)
assert.match(editorSource, /max-h-\[calc\(100dvh-12rem\)\]/)
assert.match(editorSource, /overflow-y-auto/)

console.log("node correction editor frontend contract: ok")
