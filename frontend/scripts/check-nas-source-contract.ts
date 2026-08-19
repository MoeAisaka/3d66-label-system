import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { fileURLToPath } from "node:url"

const scriptDir = resolve(fileURLToPath(new URL(".", import.meta.url)))
const page = readFileSync(resolve(scriptDir, "../src/pages/assets-page.tsx"), "utf8")
const types = readFileSync(resolve(scriptDir, "../src/lib/types.ts"), "utf8")

const required = [
  "NAS 只读引用路径",
  "/api/assets/import-nas",
  "只保存规范化来源，不复制原图",
  "storage_backend",
  "source_uri",
]
for (const marker of required) {
  if (!page.includes(marker) && !types.includes(marker)) {
    throw new Error(`NAS 来源合同缺少：${marker}`)
  }
}
console.log("NAS source contract OK")
