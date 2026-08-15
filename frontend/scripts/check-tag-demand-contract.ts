import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const page = readFileSync(new URL("../src/pages/tag-demand-contracts-page.tsx", import.meta.url), "utf8")
const drawer = readFileSync(new URL("../src/components/tag-demand-contract-drawer.tsx", import.meta.url), "utf8")
const types = readFileSync(new URL("../src/lib/types.ts", import.meta.url), "utf8")
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8")
const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8")
const management = readFileSync(new URL("../src/pages/system-management-page.tsx", import.meta.url), "utf8")

assert.match(types, /export type TagDemandContract/)
assert.match(api, /tagDemandContractApi/)
assert.match(page, /tag-demand-contracts/)
assert.match(page, /合同版本/)
assert.match(page, /复制候选/)
assert.match(page, /激活字段需求合同/)
assert.match(page, /TagDemandContractDrawer/)
assert.match(drawer, /平台字段/)
assert.match(drawer, /类目适用性/)
assert.match(drawer, /执行变体/)
assert.match(drawer, /质量门槛与投影/)
assert.match(app, /workflow\/governance\/tag-demand-contracts/)
assert.match(management, /字段需求合同/)
assert.match(management, /\/workflow\/governance\/tag-demand-contracts/)
assert.doesNotMatch(page, /default_value.*map\(/)

console.log("tag demand contract frontend contract: ok")
