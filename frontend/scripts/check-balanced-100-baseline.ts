import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8")
const pageSource = readFileSync(new URL("../src/pages/baseline-regression-page.tsx", import.meta.url), "utf8")

assert.match(apiSource, /inspiration-balanced-100/)
assert.match(pageSource, /生成 100 张均衡基准集/)
assert.match(pageSource, /L1-L5 各 20 张/)
assert.match(pageSource, /selectedCategoryKey === "inspiration_image"/)

console.log("balanced 100 baseline contract ok")
