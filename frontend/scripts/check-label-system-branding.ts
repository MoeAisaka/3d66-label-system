import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"

const root = path.resolve(import.meta.dirname, "../..")
const files = [
  "README.md",
  "PRODUCT.md",
  "frontend/src/components/app-shell.tsx",
  "frontend/src/lib/app-version.ts",
]
const text = files.map((file) => fs.readFileSync(path.join(root, file), "utf8")).join("\n")

assert.match(text, /特鹏标签中台/)
assert.match(text, /Label System/)
assert.doesNotMatch(text, /LabelLab|LABEL LAB|特鹏标签实验台/)

console.log("label-system branding contract passed")
