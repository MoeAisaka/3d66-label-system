import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const page = readFileSync(
  new URL("../src/pages/operations-center-page.tsx", import.meta.url),
  "utf8",
)
const drawer = readFileSync(
  new URL("../src/components/runtime-run-drawer.tsx", import.meta.url),
  "utf8",
)
const api = readFileSync(
  new URL("../src/lib/runtime-api.ts", import.meta.url),
  "utf8",
)
const types = readFileSync(
  new URL("../src/lib/types.ts", import.meta.url),
  "utf8",
)

for (const queue of ["validation", "interactive", "production_batch", "canary", "recovery"]) {
  assert.match(page, new RegExp(queue))
}
assert.match(page, /runtimeApi\.listRuns/)
assert.match(page, /当前步骤/)
assert.match(page, /最后检查点/)
assert.match(page, /责任人/)
assert.match(page, /阻塞原因/)
assert.match(page, /RuntimeRunDrawer/)
assert.doesNotMatch(page, /snapshot_json|input_manifest_json|output_manifest_json/)

assert.match(drawer, /步骤时间线/)
assert.match(drawer, /冻结快照/)
assert.match(drawer, /checkpoint_hash/)
assert.match(drawer, /allowed_actions/)
assert.match(drawer, /暂停|恢复|重试|取消/)

assert.match(api, /\/api\/runtime\/runs/)
assert.match(api, /listRuns/)
assert.match(api, /getTimeline/)
assert.match(api, /getSnapshot/)
assert.match(api, /action/)

assert.match(types, /export type ProductionRunSummary/)
assert.match(types, /export type RuntimeTimelineItem/)
assert.match(types, /export type RuntimeSnapshot/)
assert.match(types, /allowed_actions/)

console.log("runtime center contract: ok")

