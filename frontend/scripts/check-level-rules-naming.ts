import { strict as assert } from "node:assert"
import { readFileSync } from "node:fs"

function source(relativePath: string): string {
  return readFileSync(new URL(`../src/${relativePath}`, import.meta.url), "utf8")
}

const configPage = source("pages/category-evaluation-v3-config-page.tsx")
assert.match(configPage, /类目评测等级规则配置/)
assert.match(configPage, /等级规则配置/)
assert.match(configPage, /等级规则版本加载失败/)
assert.match(configPage, /加载等级规则与 revision 历史/)
assert.doesNotMatch(configPage, /类目评测 v3 合同配置/)
assert.doesNotMatch(configPage, /<h2[^>]*>v3 配置<\/h2>/)

const imageRuleEditor = source("features/mechanism-config/image-rule-editor.tsx")
assert.match(imageRuleEditor, /新建等级规则/)
assert.match(imageRuleEditor, /等级规则创建候选版本/)
assert.doesNotMatch(imageRuleEditor, /新建 v3 配置/)

const previewPage = source("pages/category-evaluation-preview-page.tsx")
assert.match(previewPage, /灵感图等级规则（只读）/)
assert.match(previewPage, /等级撮合器全链/)
assert.doesNotMatch(previewPage, /灵感图 v3 合同/)

const proposalEditor = source("features/mechanism-config/proposal-text-editor.tsx")
assert.match(proposalEditor, /等级规则身份与版本/)
assert.doesNotMatch(proposalEditor, /合同身份与版本/)

const correctionEditor = source("pages/node-correction-editor.tsx")
assert.match(correctionEditor, /当前等级规则重跑/)
assert.doesNotMatch(correctionEditor, /当前 v3 配置重跑/)

const systemManagement = source("pages/system-management-page.tsx")
assert.match(systemManagement, /类目评测等级规则配置/)
assert.doesNotMatch(systemManagement, /类目评测 v3 合同配置/)

const appShell = source("components/app-shell.tsx")
assert.match(appShell, /特鹏标签中台/)
assert.doesNotMatch(appShell, />TPENG 标签实验台</)

// 2026-08-24：基准回归页从 3250 行拆成 baseline-regression-page.tsx + features/baseline-regression/* 若干模块。
// 契约要守的是「基准回归这块界面整体」符合合同，而不是内容挤在同一个文件里，
// 所以这里把页面与抽出的模块拼起来一起校验：断言语义不变，拆分也不会让合同失效。
const baselinePage = [
  source("pages/baseline-regression-page.tsx"),
  ...[
  "regression-page-shared.tsx",
  "regression-results.tsx",
  "correction-analysis-panel.tsx",
  "level-explanation.tsx",
  "field-metrics-evidence.tsx",
  "form-selects.tsx",
  "correction-stage-meta.tsx",
  ].map((f) => source(`features/baseline-regression/${f}`)),
].join("\n")
assert.match(baselinePage, /现役等级规则/)
assert.match(baselinePage, /启用该等级规则候选/)
assert.doesNotMatch(baselinePage, /active v3 合同/)
assert.doesNotMatch(baselinePage, /v3 机制版本/)

// Internal compatibility identifiers must remain present in the implementation.
assert.match(baselinePage, /v3_contract/)
assert.match(correctionEditor, /v3_context/)

console.log("level-rules naming contract passed")
