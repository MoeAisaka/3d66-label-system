import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8")
}

function assertContains(value: string, expected: string): void {
  assert.ok(value.includes(expected), `missing frontend contract: ${expected}`)
}

const types = source("../src/lib/types.ts")
const drawer = source("../src/components/tag-demand-contract-drawer.tsx")
const identityDrawer = source("../src/components/content-identity-drawer.tsx")
const incrementalPage = source("../src/pages/incremental-workspace-page.tsx")
const contractPage = source("../src/pages/tag-demand-contracts-page.tsx")

assertContains(types, 'uniqueness_status: "unverified" | "verified" | "conflict"')
assertContains(types, 'identity_status: "legacy_unverified" | "pending_verification" | "verified" | "conflict"')
assertContains(drawer, "源身份合同")
assertContains(drawer, "字段供给路径")
assertContains(identityDrawer, "候选复合键")
assertContains(identityDrawer, "签认证据哈希")
assertContains(incrementalPage, "身份待签认")
assertContains(contractPage, "身份签认")
assertContains(contractPage, "绑定到候选合同")
assertContains(contractPage, "管理员可签认")

console.log("content identity frontend contract: ok")
