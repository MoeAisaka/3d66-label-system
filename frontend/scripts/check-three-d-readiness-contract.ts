import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8")
}

function requireText(value: string, expected: string): void {
  assert.ok(value.includes(expected), `missing readiness contract: ${expected}`)
}

const readiness = source(
  "../../docs/contracts/3d-su-readiness-freeze-v1.md",
)
const fields = source(
  "../../docs/contracts/3d-su-field-signoff-template-v1.csv",
)
const goldenSet = source(
  "../../docs/contracts/3d-su-golden-set-plan-v1.md",
)
const permissionRaci = source(
  "../../docs/contracts/3d-su-permission-raci-v1.md",
)

requireText(readiness, "aliyun_3d66_dw.dim_res_info_union")
requireText(readiness, "res_type=1")
requireText(readiness, "res_type=6")
requireText(readiness, "pending_external_signoff")
requireText(readiness, "双人工门")
requireText(readiness, "未连接真实源")
requireText(readiness, "未执行 SQL")
requireText(readiness, "未调用模型")
requireText(readiness, "未写外部数据库")
requireText(readiness, "未发布标签")
requireText(readiness, "未部署")

const fieldLines = fields.trimEnd().split("\n")
const headers = fieldLines[0].split(",")
const records = fieldLines.slice(1).map((line) => {
  const values = line.split(",")
  assert.equal(values.length, headers.length, `invalid CSV row: ${line}`)
  return Object.fromEntries(headers.map((header, index) => [header, values[index]]))
})
const platformFields = new Set(
  records
    .filter((record) => record.namespace === "semantic")
    .map((record) => record.field_key),
)
for (const field of [
  "space",
  "object",
  "style",
  "material",
  "structural_features",
  "architectural_element",
  "soft_decoration",
  "hard_decoration",
  "color",
  "title",
]) {
  assert.ok(platformFields.has(field), `missing platform field: ${field}`)
}
for (const record of records.filter((row) => row.namespace !== "semantic")) {
  assert.match(record.field_key, /^category\.model_3d_su\./)
}
for (const record of records) {
  assert.ok(Number(record.precision_gate) >= 0.8)
  assert.ok(Number(record.recall_gate) >= 0.7)
  assert.equal(record.signoff_state, "pending")
}

requireText(goldenSet, "最低锁定样本数为 100")
for (const stratum of ["3D", "SU", "whole", "single", "L1", "L5"]) {
  requireText(goldenSet, stratum)
}
requireText(goldenSet, "Precision ≥ 0.80")
requireText(goldenSet, "Recall ≥ 0.70")
requireText(permissionRaci, "SELECT, DESCRIBE")
for (const denied of ["DOWNLOAD", "UPDATE", "ALTER", "DROP", "INSERT", "DELETE"]) {
  requireText(permissionRaci, denied)
}
for (const role of [
  "Product Owner",
  "Data Owner",
  "Algorithm Owner",
  "Platform Owner",
  "Reviewer Owner",
  "Consumer Owner",
]) {
  requireText(permissionRaci, role)
}

console.log("3D/SU readiness contract: ok")
