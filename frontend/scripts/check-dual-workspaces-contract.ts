import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (relativePath: string) => readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8")

const app = read("src/App.tsx")
const shell = read("src/components/app-shell.tsx")
const incremental = read("src/pages/incremental-workspace-page.tsx")
const stock = read("src/pages/stock-workspace-page.tsx")
const operations = read("src/pages/operations-center-page.tsx")
const qualityAssets = read("src/pages/quality-assets-page.tsx")
// 2026-08-24：基准回归页从 3250 行拆成 baseline-regression-page.tsx + features/baseline-regression/* 若干模块。
// 契约要守的是「基准回归这块界面整体」符合合同，而不是内容挤在同一个文件里，
// 所以这里把页面与抽出的模块拼起来一起校验：断言语义不变，拆分也不会让合同失效。
const baselineRegression = [
  read("src/pages/baseline-regression-page.tsx"),
  ...[
  "regression-page-shared.tsx",
  "regression-results.tsx",
  "correction-analysis-panel.tsx",
  "level-explanation.tsx",
  "field-metrics-evidence.tsx",
  "form-selects.tsx",
  "correction-stage-meta.tsx",
  ].map((f) => read(`src/features/baseline-regression/${f}`)),
].join("\n")
const api = read("src/lib/api.ts")
const types = read("src/lib/types.ts")
const stepper = read("src/components/workflow-stepper.tsx")

assert.match(app, /workflow\/incremental/)
assert.match(app, /workflow\/stock/)
assert.match(app, /workflow\/operations/)
assert.match(app, /workflow\/quality-assets/)
assert.match(shell, /增量评测/)
assert.match(shell, /存量回归/)
assert.match(shell, /运行中心/)
assert.match(shell, /质量资产/)
assert.match(incremental, /增量评测/)
assert.match(stock, /存量回归/)
assert.match(operations, /运行中心/)
assert.match(operations, /SecondaryDrawer/)
assert.match(operations, /queues\.data\?\.global_limit/)
assert.doesNotMatch(operations, /queues\.data\?\.policy\.global_limit/)
assert.match(operations, /queue_class/)
assert.match(operations, /blocked_by_breaker/)
assert.match(operations, /delayed_by_retry_after/)
assert.match(operations, /retry_after_at/)
assert.match(operations, /circuit-breakers/)
assert.match(operations, /最后检查点/)
assert.match(qualityAssets, /质量资产/)
assert.match(qualityAssets, /查看回归质量证据/)
assert.match(baselineRegression, /查看字段证据/)
assert.match(baselineRegression, /宏平均准确率/)
assert.match(baselineRegression, /失败样本/)
assert.match(api, /getMetrics/)
assert.match(types, /BaselineFieldMetrics/)
assert.match(stepper, /WorkflowStepper/)
assert.match(incremental, /content-ingress-v1/)
assert.match(incremental, /不代表真实上游已连接/)

console.log("dual workspaces contract: ok")
