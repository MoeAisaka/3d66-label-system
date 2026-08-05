import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8")
const systemManagement = readFileSync(
  new URL("../src/pages/system-management-page.tsx", import.meta.url),
  "utf8",
)
const evaluationPackages = readFileSync(
  new URL("../src/lib/evaluation-packages.ts", import.meta.url),
  "utf8",
)
const baseline = readFileSync(
  new URL("../src/pages/baseline-regression-page.tsx", import.meta.url),
  "utf8",
)
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8")

assert.doesNotMatch(app, /DimensionManagerPage/)
assert.doesNotMatch(app, /workflow\/optimization\/dimensions/)
assert.doesNotMatch(systemManagement, /类目与维度/)
assert.match(systemManagement, /类目评测 v3 合同配置/)
assert.doesNotMatch(evaluationPackages, /workflow\/optimization\/dimensions/)
assert.match(evaluationPackages, /workflow\/optimization\/category-evaluation-v3-config/)
assert.doesNotMatch(baseline, /DimensionSchemaRegistryItem/)
assert.doesNotMatch(baseline, /dimensionSchemas/)
assert.doesNotMatch(baseline, /dimensionChoice/)
assert.doesNotMatch(baseline, /dimension_schema_id/)
assert.doesNotMatch(baseline, /dimension_mode/)
assert.match(baseline, /active v3 合同/)
assert.doesNotMatch(api, /dimension_schema_id\?: number/)
assert.doesNotMatch(api, /dimension_mode\?:/)

console.log("v3-only frontend contract passed")
