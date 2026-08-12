import fs from "node:fs"
import path from "node:path"

const root = path.resolve(import.meta.dirname, "..")
const imageEditor = fs.readFileSync(
  path.join(root, "src/features/mechanism-config/image-rule-editor.tsx"),
  "utf8",
)
const modelPage = fs.readFileSync(path.join(root, "src/pages/model-page.tsx"), "utf8")
const types = fs.readFileSync(path.join(root, "src/lib/types.ts"), "utf8")

for (const token of [
  "LevelScaleEditor",
  "L1 最优",
  "创建候选版本",
]) {
  if (!imageEditor.includes(token)) throw new Error(`等级档位界面缺少合同标记: ${token}`)
}

for (const token of ["thinking_mode", "自动", "开启", "关闭"]) {
  if (!modelPage.includes(token)) throw new Error(`模型界面缺少 thinking 控件: ${token}`)
}

if (!types.includes('thinking_mode: "auto" | "enabled" | "disabled"')) {
  throw new Error("ModelConfig 类型缺少 thinking_mode 枚举")
}

console.log("level-scale + thinking frontend contract: ok")
