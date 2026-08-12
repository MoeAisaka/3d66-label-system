import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const appShell = readFileSync(new URL("../src/components/app-shell.tsx", import.meta.url), "utf8")
const systemManagement = readFileSync(
  new URL("../src/pages/system-management-page.tsx", import.meta.url),
  "utf8",
)
const appVersion = await import("../src/lib/app-version.ts")

assert.doesNotMatch(appShell, /高级设置首页/)
assert.doesNotMatch(appShell, /active\.to === advancedWorkflowDomain\.to/)
assert.match(systemManagement, /类目评测底座预览/)
assert.match(systemManagement, /类目评测 v3 合同配置/)
assert.match(appShell, /<AppVersion/)
assert.equal(appVersion.formatAppVersion("0.2.0", "caa46663608c"), "LabelLab v0.2.0 · build caa4666")
assert.equal(appVersion.formatAppVersion("0.2.0", ""), "LabelLab v0.2.0 · build dev")

console.log("information architecture contract: ok")
