import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
  baselineAcceptanceProgress,
  baselineAcceptanceProgressFromPages,
  baselineRunContextPatch,
  baselineRunIdAfterSetLoad,
} from "../src/features/baseline-regression/baseline-regression-contract.ts"

const appShell = readFileSync(new URL("../src/components/app-shell.tsx", import.meta.url), "utf8")
const systemManagement = readFileSync(
  new URL("../src/pages/system-management-page.tsx", import.meta.url),
  "utf8",
)
const appVersion = await import("../src/lib/app-version.ts")
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
const workbenchSource = readFileSync(
  new URL("../src/features/baseline-regression/correction-workbench.tsx", import.meta.url),
  "utf8",
)
const baselineSetDialogSource = readFileSync(
  new URL("../src/features/baseline-regression/baseline-set-dialog.tsx", import.meta.url),
  "utf8",
)

assert.doesNotMatch(appShell, /高级设置首页/)
assert.doesNotMatch(appShell, /active\.to === advancedWorkflowDomain\.to/)
assert.match(systemManagement, /类目评测底座预览/)
assert.match(systemManagement, /类目评测等级规则配置/)
assert.match(appShell, /<AppVersion/)
assert.equal(appVersion.formatAppVersion("0.2.0", "caa46663608c"), "Label System v0.2.0 · build caa4666")
assert.equal(appVersion.formatAppVersion("0.2.0", ""), "Label System v0.2.0 · build dev")
assert.match(baselinePage, /选择基准集/)
assert.match(baselinePage, /逐条确认与纠偏/)
assert.match(baselinePage, /完成人工验收/)
assert.match(baselinePage, /BaselineSetDialog/)
assert.match(baselinePage, /RunConfigDrawer/)
assert.match(baselinePage, /MetricsDrawer/)
assert.match(baselinePage, /<LevelPerformanceSummary metrics=\{metrics\} \/>/)
assert.match(baselinePage, /levelMetrics=\{summary\.metrics\}/)
assert.match(baselinePage, /RunHistoryDrawer/)
assert.match(baselinePage, /CorrectionWorkbench/)
assert.match(baselinePage, /baselineAcceptanceProgressFromPages/)
// 每个会改动验收状态的 mutation 都必须刷新验收进度：复核、逐条纠偏、合同纠偏。
assert.equal(
  (baselinePage.match(/queryKey: \["baseline-acceptance", run\.id\]/g) ?? []).length,
  4,
)
assert.doesNotMatch(baselinePage, /function BaselineCorrectionPanel/)
assert.doesNotMatch(baselinePage, /awaiting_confirmation/)
assert.doesNotMatch(baselinePage, /另行创建候选版本/)
assert.doesNotMatch(baselinePage, /当前阻塞/)
assert.match(baselinePage, /自动分析纠偏样本/)
assert.match(baselinePage, /生成统一机制候选/)
assert.match(baselinePage, /执行候选回归/)
assert.match(baselinePage, /结果查看位置：存量回归 → 基准回归 → 处理纠偏（当前区域）/)
assert.match(baselinePage, /人工采纳位置：候选回归完成后仍在当前区域进入“等待人工决策”/)
assert.match(baselinePage, /等待人工决策/)
assert.match(baselinePage, /启用候选/)
assert.match(baselinePage, /拒绝候选/)
assert.match(baselinePage, /canDecide={me\.data\?\.is_admin === true}/)
assert.match(baselinePage, /latest\.error\?\.retryable/)
assert.match(baselinePage, /aria-controls="baseline-correction-panel"/)
assert.match(baselinePage, /aria-labelledby="baseline-correction-tab"/)
assert.match(workbenchSource, /<NodeCorrectionEditor/)
assert.match(workbenchSource, /返回轮次列表/)
assert.match(baselineSetDialogSource, /onCloseAutoFocus/)
assert.match(baselineSetDialogSource, /returnFocusRef\.current\?\.focus/)
assert.deepEqual(
  baselineAcceptanceProgress([
    { status: "completed", evaluation: { human_review: { decision: "approved" } } },
    { status: "completed", evaluation: { human_review: { decision: "corrected" } } },
    { status: "completed", evaluation: { human_review: { decision: "rejected" } } },
  ]),
  { reviewed: 3, total: 3, complete: true },
)
assert.deepEqual(
  baselineAcceptanceProgress([
    { status: "completed", evaluation: { human_review: { decision: "approved" } } },
    { status: "completed", evaluation: { human_review: { decision: null } } },
    { status: "failed", evaluation: null },
  ]),
  { reviewed: 1, total: 2, complete: false },
)
assert.deepEqual(
  baselineAcceptanceProgressFromPages([
    [{ status: "completed", evaluation: { human_review: { decision: "approved" } } }],
    [{ status: "completed", evaluation: { human_review: { decision: "corrected" } } }],
  ]),
  { reviewed: 2, total: 2, complete: true },
)
assert.equal(baselineRunIdAfterSetLoad(17, null), 17)
assert.equal(baselineRunIdAfterSetLoad(17, [{ id: 17 }]), 17)
assert.equal(baselineRunIdAfterSetLoad(17, [{ id: 21 }]), 21)
assert.equal(baselineRunIdAfterSetLoad(17, []), 0)
assert.deepEqual(
  baselineRunContextPatch("space_image", 0, {
    categoryKey: "inspiration_image",
    baselineSetId: 8,
  }),
  { categoryKey: "inspiration_image" },
)
assert.deepEqual(
  baselineRunContextPatch("inspiration_image", 0, {
    categoryKey: "inspiration_image",
    baselineSetId: 8,
  }),
  { baselineSetId: 8 },
)

console.log("information architecture contract: ok")
