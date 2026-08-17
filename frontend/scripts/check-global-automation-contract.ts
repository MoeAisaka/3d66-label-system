import { readFileSync } from "node:fs"
import assert from "node:assert/strict"

const page = readFileSync("src/pages/automation-overview-page.tsx", "utf8")
assert.match(page, /历史审计/)
assert.match(page, /跨泳道不混批/)
assert.match(page, /自动发布已关闭/)
assert.match(page, /目标错例/)
console.log("global automation contract: ok")
