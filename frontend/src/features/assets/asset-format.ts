/** 素材页格式化与状态推导 —— 从 assets-page.tsx 抽出的纯函数，不含 JSX。 */

import { ApiError } from "@/lib/api"
import type { Asset, EvaluationCategoryProfile, UploadFileIssue } from "@/lib/types"

export type CategoryKey = EvaluationCategoryProfile["category_key"]

export type UploadFeedback = {
  source: string
  successful: string[]
  skipped: UploadFileIssue[]
  failed: UploadFileIssue[]
}

export function fileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function fileType(mimeType: string) {
  return mimeType.split("/")[1]?.toUpperCase().replace("JPEG", "JPG") || "图片"
}

export function snapshotFiles(files: FileList | null) {
  return files ? Array.from(files) : []
}

export function relativeBrowserFileName(file: File) {
  const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath
  const raw = (relativePath || file.name).replaceAll("\\", "/")
  const parts = raw.split("/").filter((part) => part && part !== "." && part !== "..")
  const absolute = raw.startsWith("/") || raw.startsWith("//") || /^[A-Za-z]:\//.test(raw) || raw.split("/").includes("..")
  return (absolute ? parts.at(-1) : parts.join("/")) || "unnamed-file"
}

export function fileSkipReason(file: File, category?: EvaluationCategoryProfile) {
  const filename = relativeBrowserFileName(file)
  const parts = filename.split("/")
  const basename = parts.at(-1)?.toLowerCase() ?? ""
  if (
    parts.some((part) => part.startsWith("."))
    || parts.some((part) => part.toLowerCase() === "__macosx")
    || basename === "thumbs.db"
    || basename === "desktop.ini"
  ) return "隐藏或系统元数据"
  const suffixIndex = basename.lastIndexOf(".")
  const suffix = suffixIndex >= 0 ? basename.slice(suffixIndex) : ""
  const allowedSuffixes = new Set(category?.pipeline_config.allowed_suffixes.map((item) => item.toLowerCase()) ?? [])
  if (!allowedSuffixes.has(suffix)) return `当前类目不支持 ${suffix || "无扩展名"} 格式`
  return null
}

export function uploadIssuesFromError(error: unknown, key: "skipped_files" | "failed_files") {
  if (!(error instanceof ApiError) || !Array.isArray(error.detail?.[key])) return []
  return error.detail[key].filter((item): item is UploadFileIssue => (
    Boolean(item)
    && typeof item === "object"
    && "filename" in item
    && typeof item.filename === "string"
    && "reason" in item
    && typeof item.reason === "string"
  ))
}

export function evaluationStatus(value: Asset["evaluation_status"]) {
  return ({
    not_evaluated: "未评测",
    evaluated_old: "仅旧版本",
    evaluated_current: "当前版本已评测",
    queued: "已排队",
    running: "运行中",
    failed: "失败",
  } as const)[value ?? "not_evaluated"]
}

export function statusTone(value: Asset["evaluation_status"]) {
  if (value === "evaluated_current") return "success"
  if (value === "failed") return "danger"
  if (value === "queued" || value === "running") return "warning"
  return "active"
}
