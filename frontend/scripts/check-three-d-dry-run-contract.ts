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

assert.match(page, /data-testid="three-d-dry-run-summary"/)
assert.match(page, /model_3d_su/)
assert.match(page, /双人工门/)
assert.match(page, /当前关口/)
assert.match(page, /最后检查点/)
assert.match(page, /查看完整证据/)
assert.match(drawer, /步骤时间线/)
assert.match(drawer, /checkpoint_hash/)
assert.match(drawer, /冻结快照/)

console.log("3D/SU dry-run desktop contract: ok")
