import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
  baselineAcceptanceProgress,
  baselineAcceptanceProgressFromPages,
} from "../src/features/baseline-regression/baseline-regression-contract.ts"

const appShell = readFileSync(new URL("../src/components/app-shell.tsx", import.meta.url), "utf8")
const systemManagement = readFileSync(
  new URL("../src/pages/system-management-page.tsx", import.meta.url),
  "utf8",
)
const appVersion = await import("../src/lib/app-version.ts")
const baselinePage = readFileSync(
  new URL("../src/pages/baseline-regression-page.tsx", import.meta.url),
  "utf8",
)
const workbenchSource = readFileSync(
  new URL("../src/features/baseline-regression/correction-workbench.tsx", import.meta.url),
  "utf8",
)

assert.doesNotMatch(appShell, /高级设置首页/)
assert.doesNotMatch(appShell, /active\.to === advancedWorkflowDomain\.to/)
assert.match(systemManagement, /类目评测底座预览/)
assert.match(systemManagement, /类目评测 v3 合同配置/)
assert.match(appShell, /<AppVersion/)
assert.equal(appVersion.formatAppVersion("0.2.0", "caa46663608c"), "LabelLab v0.2.0 · build caa4666")
assert.equal(appVersion.formatAppVersion("0.2.0", ""), "LabelLab v0.2.0 · build dev")
assert.match(baselinePage, /选择基准集/)
assert.match(baselinePage, /逐条确认与纠偏/)
assert.match(baselinePage, /完成人工验收/)
assert.match(baselinePage, /BaselineSetDialog/)
assert.match(baselinePage, /RunConfigDrawer/)
assert.match(baselinePage, /MetricsDrawer/)
assert.match(baselinePage, /RunHistoryDrawer/)
assert.match(baselinePage, /CorrectionWorkbench/)
assert.match(baselinePage, /baselineAcceptanceProgressFromPages/)
assert.equal(
  (baselinePage.match(/queryKey: \["baseline-acceptance", run\.id\]/g) ?? []).length,
  2,
)
assert.doesNotMatch(baselinePage, /function BaselineCorrectionPanel/)
assert.match(workbenchSource, /<NodeCorrectionEditor/)
assert.match(workbenchSource, /返回轮次列表/)
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

console.log("information architecture contract: ok")
