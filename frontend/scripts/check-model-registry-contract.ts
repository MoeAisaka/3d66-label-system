import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const root = resolve(import.meta.dirname, "..")

function read(path: string) {
  return readFileSync(resolve(root, path), "utf8")
}

function requireText(source: string, text: string, message: string) {
  if (!source.includes(text)) throw new Error(message)
}

const page = read("src/pages/model-registry-page.tsx")
const types = read("src/lib/types.ts")
const app = read("src/App.tsx")
const management = read("src/pages/system-management-page.tsx")

requireText(types, "export type ModelRegistryEntry", "缺少模型注册中心前端类型")
requireText(page, 'api<{ items: ModelRegistryEntry[] }>("/api/model-registry")', "页面必须读取统一注册表 API")
requireText(page, "主模型", "列表必须显式区分主模型")
requireText(page, "调优模型", "列表必须显式区分调优模型")
requireText(page, "新建模型", "缺少新建模型抽屉入口")
requireText(page, "编辑模型", "缺少编辑模型抽屉入口")
requireText(page, "API Key", "抽屉必须提供安全凭据录入")
requireText(page, "has_api_key", "列表必须只读取密钥状态")
if (page.includes("encrypted_api_key")) throw new Error("前端不得读取加密密钥字段")
requireText(app, "ModelRegistryPage", "缺少模型注册中心路由")
requireText(management, "/workflow/governance/model-registry", "高级设置未指向模型注册中心")

console.log("model registry frontend contract: ok")
