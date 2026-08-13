import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (relativePath: string) => readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8")

const app = read("src/App.tsx")
const shell = read("src/components/app-shell.tsx")
const incremental = read("src/pages/incremental-workspace-page.tsx")
const stock = read("src/pages/stock-workspace-page.tsx")
const operations = read("src/pages/operations-center-page.tsx")
const qualityAssets = read("src/pages/quality-assets-page.tsx")
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
assert.match(qualityAssets, /质量资产/)
assert.match(stepper, /WorkflowStepper/)

console.log("dual workspaces contract: ok")
