import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"

import { patchProposalContract } from "../src/features/mechanism-config/proposal-text-contract.ts"
import {
  imageRuleViewDefaults,
  prepareImageRulePayload,
} from "../src/features/mechanism-config/image-rule-contract.ts"
import { getMechanismEditorPlugin } from "../src/features/mechanism-config/registry.ts"
import { isNewMechanismDraft } from "../src/features/mechanism-config/types.ts"

const root = path.resolve(import.meta.dirname, "..")
const pageSource = fs.readFileSync(
  path.join(root, "src/pages/category-evaluation-v3-config-page.tsx"),
  "utf8",
)
const unknownSource = fs.readFileSync(
  path.join(root, "src/features/mechanism-config/unknown-mechanism-summary.tsx"),
  "utf8",
)
const levelScaleContractSource = fs.readFileSync(
  path.join(root, "scripts/check-level-scale-thinking-controls.ts"),
  "utf8",
)
const proposalEditorSource = fs.readFileSync(
  path.join(root, "src/features/mechanism-config/proposal-text-editor.tsx"),
  "utf8",
)
const profileCapabilitySource = fs.readFileSync(
  path.join(root, "src/features/mechanism-config/profile-capability-summary.tsx"),
  "utf8",
)
const boundarySource = fs.readFileSync(
  path.join(root, "src/features/mechanism-config/mechanism-editor-boundary.tsx"),
  "utf8",
)

const original = { known: { value: 1 }, extension: { keep: ["x"] } }
const next = patchProposalContract(original, ["known", "value"], 2)
assert.deepEqual(next.extension, { keep: ["x"] })
assert.deepEqual(original.known, { value: 1 })

const legacyDimension = {
  key: "visual",
  weight: 1,
  deduction_rules: [{ rule_id: "r1", description: "存在明显缺陷", deduction: 20 }],
}
const viewDefaults = imageRuleViewDefaults(legacyDimension)
assert.equal(viewDefaults.dimensionScoreCap, 100)
assert.deepEqual(viewDefaults.bonusRules, [])
assert.equal("bonus_rules" in legacyDimension, false)
assert.equal("dimension_score_cap" in legacyDimension, false)

const legacyDraft = {
  category_key: "inspiration_image",
  display_name: "灵感图",
  contract: {},
  classification_map: {},
  subcategory_dimensions: {
    class_one: {
      common_group: {
        schema_definition: { dimensions: [legacyDimension] },
      },
      specific_group: {
        schema_definition: {
          dimensions: [{ key: "legacy_grade", weight: 1, grade_points: { "5": 100 } }],
        },
      },
    },
  },
}
const outgoing = prepareImageRulePayload(legacyDraft)
const outgoingRuleDimension = outgoing.subcategory_dimensions.class_one.common_group.schema_definition.dimensions[0]
const outgoingGradeDimension = outgoing.subcategory_dimensions.class_one.specific_group.schema_definition.dimensions[0]
assert.equal(outgoingRuleDimension.dimension_score_cap, 100)
assert.deepEqual(outgoingRuleDimension.bonus_rules, [])
assert.equal("dimension_score_cap" in legacyDimension, false)
assert.equal("bonus_rules" in legacyDimension, false)
assert.equal("dimension_score_cap" in outgoingGradeDimension, false)
assert.equal("bonus_rules" in outgoingGradeDimension, false)

assert.equal(
  getMechanismEditorPlugin("image-rule-deduction-v1")?.profileType,
  "image-rule-deduction-v1",
)
assert.equal(
  getMechanismEditorPlugin("image-rule-deduction-v1")?.prepareForSave,
  prepareImageRulePayload,
)
assert.equal(
  getMechanismEditorPlugin("text-proposal-additive-v1")?.profileType,
  "text-proposal-additive-v1",
)
assert.equal(getMechanismEditorPlugin("future-3d-v1"), null)
assert.equal(isNewMechanismDraft(null, null), true)
assert.equal(isNewMechanismDraft({ id: 1 } as never, null), false)
assert.doesNotMatch(pageSource, /tracks\.map/)
assert.doesNotMatch(pageSource, /onStatus/)
assert.doesNotMatch(pageSource, /\/level-scale[\s\S]{0,400}method:\s*["']PUT["']/)
const imageEditorSource = fs.readFileSync(
  path.join(root, "src/features/mechanism-config/image-rule-editor.tsx"),
  "utf8",
)
assert.match(imageEditorSource, /onKey=\{\(value\) => onPatch\(\(next\) => \{ next\.category_key = value/)
assert.doesNotMatch(imageEditorSource, /onStatus|onSaveLevelScale|仅保存等级档位/)
assert.match(imageEditorSource, /维度分数上限/)
assert.match(imageEditorSource, /加分规则/)
assert.match(imageEditorSource, /dimension_score_cap/)
assert.match(imageEditorSource, /bonus_rules/)
assert.doesNotMatch(levelScaleContractSource, /expected_revision/)
assert.match(levelScaleContractSource, /创建候选版本/)
assert.match(pageSource, /MechanismEditorBoundary/)
assert.match(pageSource, /ProfileCapabilitySummary/)
assert.match(pageSource, /workflowKind=/)
assert.match(profileCapabilitySource, /3D/)
assert.match(profileCapabilitySource, /SU/)
assert.match(profileCapabilitySource, /只读/)
assert.match(boundarySource, /canExecute/)
assert.match(boundarySource, /readOnlyFallback/)
assert.match(boundarySource, /UnknownMechanismSummary/)
assert.match(unknownSource, /不会执行未知代码/)
assert.match(pageSource, /创建候选版本/)
assert.match(pageSource, /plugin\?\.prepareForSave\?\.\(draft\) \?\? draft/)
assert.match(unknownSource, /当前版本不支持结构化编辑/)
assert.match(unknownSource, /查看完整 JSON/)
assert.match(proposalEditorSource, /PDF 输入与确定性预检/)
assert.match(proposalEditorSource, /红线与人工复核/)
assert.match(proposalEditorSource, /赛道与三分项评分/)
assert.match(proposalEditorSource, /回归与验收/)

console.log("mechanism editor contract: ok")
