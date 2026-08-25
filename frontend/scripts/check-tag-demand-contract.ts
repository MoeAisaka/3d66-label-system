import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const page = readFileSync(new URL("../src/pages/tag-demand-contracts-page.tsx", import.meta.url), "utf8")
const drawer = readFileSync(new URL("../src/components/tag-demand-contract-drawer.tsx", import.meta.url), "utf8")
const types = readFileSync(new URL("../src/lib/types.ts", import.meta.url), "utf8")
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8")
const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8")
const management = readFileSync(new URL("../src/pages/system-management-page.tsx", import.meta.url), "utf8")
const categoryPage = readFileSync(new URL("../src/pages/category-evaluation-v3-config-page.tsx", import.meta.url), "utf8")
// 2026-08-24：基准回归页从 3250 行拆成 baseline-regression-page.tsx + features/baseline-regression/* 若干模块。
// 契约要守的是「基准回归这块界面整体」符合合同，而不是内容挤在同一个文件里，
// 所以这里把页面与抽出的模块拼起来一起校验：断言语义不变，拆分也不会让合同失效。
const baselinePage = [
  readFileSync(new URL("../src/pages/baseline-regression-page.tsx", import.meta.url), "utf8"),
  ...[
  "regression-page-shared.tsx",
  "regression-results.tsx",
  "correction-analysis-panel.tsx",
  "level-explanation.tsx",
  "field-metrics-evidence.tsx",
  "form-selects.tsx",
  "correction-stage-meta.tsx",
  ].map((f) => readFileSync(new URL(`../src/features/baseline-regression/${f}`, import.meta.url), "utf8")),
].join("\n")

assert.match(types, /export type TagDemandContract/)
assert.match(api, /tagDemandContractApi/)
assert.match(page, /tag-demand-contracts/)
assert.match(page, /合同版本/)
assert.match(page, /复制候选/)
assert.match(page, /激活字段需求合同/)
assert.match(page, /api<User>\("\/api\/auth\/me"\)/)
assert.match(page, /canManage/)
assert.match(page, /TagDemandContractDrawer/)
assert.match(drawer, /平台字段/)
assert.match(drawer, /类目适用性/)
assert.match(drawer, /执行变体/)
assert.match(drawer, /质量门槛与投影/)
assert.match(app, /workflow\/governance\/tag-demand-contracts/)
assert.match(management, /字段需求合同/)
assert.match(management, /\/workflow\/governance\/tag-demand-contracts/)
assert.doesNotMatch(page, /default_value.*map\(/)
assert.match(categoryPage, /semantic_tag_applicability/)
assert.match(categoryPage, /查看字段合同/)
assert.match(baselinePage, /SemanticQualityDrawer/)
assert.match(baselinePage, /语义字段质量/)
assert.match(baselinePage, /baseline-five-level-confusion-matrix/)
assert.match(baselinePage, /LevelPerformanceSummary/)
assert.match(types, /unavailable_historical/)

console.log("tag demand contract frontend contract: ok")
