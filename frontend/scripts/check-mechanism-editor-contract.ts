import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"

import { patchProposalContract } from "../src/features/mechanism-config/proposal-text-contract.ts"
import {
  applyImageRuleBinding,
  imageRuleBindingView,
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

// 运营手选 A/B 绑定：改 B 必须同时改 aesthetic_foundation.call_b_version，否则后端
// 门禁会以 aesthetic_foundation_prompt_binding_mismatch 拒单。
const bindingContract: Record<string, any> = {
  prompt_bindings: { call_a_version: "a-rev3", call_b_version: "b-rev3" },
  aesthetic_foundation: {
    call_b_version: "b-rev3",
    anchors: [{ asset_id: 1 }],
    dimension_keys: ["composition"],
  },
}
const bindingView = imageRuleBindingView(bindingContract)
assert.equal(bindingView.callAVersion, "a-rev3")
assert.equal(bindingView.callBVersion, "b-rev3")
assert.equal(bindingView.foundationEnabled, true)

applyImageRuleBinding(bindingContract, "B", " b-rev4 ")
assert.equal(bindingContract.prompt_bindings.call_b_version, "b-rev4")
assert.equal(bindingContract.aesthetic_foundation.call_b_version, "b-rev4")
applyImageRuleBinding(bindingContract, "A", "a-rev4")
assert.equal(bindingContract.prompt_bindings.call_a_version, "a-rev4")
assert.equal(bindingContract.aesthetic_foundation.call_b_version, "b-rev4")

// 没有 prompt_bindings 的合同也要能写进去，不能静默丢弃运营的选择。
const bareContract: Record<string, any> = {}
applyImageRuleBinding(bareContract, "A", "a-rev5")
assert.equal(bareContract.prompt_bindings.call_a_version, "a-rev5")

// 「未绑定」写 null 而不是空串：后端 call_b_version 用 None 表示不走调用 B，
// 空串会变成声明了却对不上任何版本的假绑定。基座要一起跟到 null。
applyImageRuleBinding(bindingContract, "B", "")
assert.equal(bindingContract.prompt_bindings.call_b_version, null)
assert.equal(bindingContract.aesthetic_foundation.call_b_version, null)
assert.equal(imageRuleBindingView(bindingContract).callBVersion, "")
applyImageRuleBinding(bindingContract, "B", "b-rev4")

// 旧基座总开关已拆除：基座能力已拆成锚点图机制、质量规则等独立配置项。
// 编辑器不得再暴露「启用美感前置基座」入口，带基座的旧修订只展示遗留提示。
assert.match(imageEditorSource, /A \/ B 调用绑定/)
assert.match(imageEditorSource, /applyImageRuleBinding/)
assert.doesNotMatch(imageEditorSource, /setAestheticFoundationEnabled/)
assert.doesNotMatch(imageEditorSource, /启用美感前置基座/)
assert.match(imageEditorSource, /旧版美感前置基座（遗留形态）/)
// 调用 A 在执行侧是必填，界面必须挡住留空，否则存出来的修订发起时必然被拒单
assert.match(imageEditorSource, /调用 A 必须绑定一个版本/)
assert.match(imageEditorSource, /不走调用 B/)

console.log("mechanism editor contract: ok")
