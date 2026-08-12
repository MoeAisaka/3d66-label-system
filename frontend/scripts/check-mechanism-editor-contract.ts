import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"

import { patchProposalContract } from "../src/features/mechanism-config/proposal-text-contract.ts"
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

const original = { known: { value: 1 }, extension: { keep: ["x"] } }
const next = patchProposalContract(original, ["known", "value"], 2)
assert.deepEqual(next.extension, { keep: ["x"] })
assert.deepEqual(original.known, { value: 1 })

assert.equal(
  getMechanismEditorPlugin("image-rule-deduction-v1")?.profileType,
  "image-rule-deduction-v1",
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
assert.doesNotMatch(levelScaleContractSource, /expected_revision/)
assert.match(levelScaleContractSource, /创建候选版本/)
assert.match(pageSource, /MechanismEditorBoundary/)
assert.match(pageSource, /创建候选版本/)
assert.match(unknownSource, /当前版本不支持结构化编辑/)
assert.match(unknownSource, /查看完整 JSON/)
assert.match(proposalEditorSource, /PDF 输入与确定性预检/)
assert.match(proposalEditorSource, /红线与人工复核/)
assert.match(proposalEditorSource, /赛道与三分项评分/)
assert.match(proposalEditorSource, /回归与验收/)

console.log("mechanism editor contract: ok")
