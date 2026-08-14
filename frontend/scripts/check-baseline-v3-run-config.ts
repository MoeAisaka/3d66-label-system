import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const page = readFileSync(new URL("../src/pages/baseline-regression-page.tsx", import.meta.url), "utf8")
const drawer = readFileSync(new URL("../src/features/baseline-regression/run-config-drawer.tsx", import.meta.url), "utf8")
const workspace = readFileSync(new URL("../src/components/workspace-page.tsx", import.meta.url), "utf8")

assert.match(drawer, /wide/)
assert.match(page, /V3 合同配置/)
assert.match(page, /candidate_revision_id/)
assert.match(page, /候选合同.*A\/B|A\/B.*候选合同/s)
assert.match(drawer, /size="wide"/)
assert.match(workspace, /w-\[min\(820px/)

console.log("baseline v3 run config UI contract: ok")
