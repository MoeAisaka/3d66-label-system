import { ApiError, api, jsonBody } from "@/lib/api"
import type {
  EvaluationCategoryProfile,
  EvaluationPackageDetail,
  EvaluationPackageStatus,
  EvaluationPackageSummary,
  EvaluationPackageTimelineStep,
  MaterialPackage,
} from "@/lib/types"

export type CreateEvaluationPackageInput = {
  material_package_id: number
  category_key: string
  configuration_mode: "category_frozen"
}

export type EvaluationPackageList = {
  items: EvaluationPackageSummary[]
  total: number
}

export type OperatorError = {
  title: string
  message: string
  retryable: boolean
  kind: "permission" | "conflict" | "network" | "missing" | "validation" | "service"
}

export type PipelineReadinessCheck = {
  key: "materials" | "category" | "configuration"
  label: string
  description: string
  ready: boolean
  action_label?: string
  action_href?: string
}

type PackageDetailWire = EvaluationPackageDetail | { item: EvaluationPackageDetail }
type PackageListWire = EvaluationPackageSummary[] | {
  items: EvaluationPackageSummary[]
  total?: number
}

function unwrapDetail(value: PackageDetailWire): EvaluationPackageDetail {
  return "item" in value ? value.item : value
}

export function normalizeEvaluationPackageList(value: PackageListWire): EvaluationPackageList {
  if (Array.isArray(value)) return { items: value, total: value.length }
  return { items: value.items, total: value.total ?? value.items.length }
}

export const evaluationPackageApi = {
  list: async () => normalizeEvaluationPackageList(
    await api<PackageListWire>("/api/evaluation-packages"),
  ),
  create: async (payload: CreateEvaluationPackageInput) => unwrapDetail(
    await api<PackageDetailWire>("/api/evaluation-packages", {
      method: "POST",
      ...jsonBody(payload),
    }),
  ),
  get: async (packageId: number) => unwrapDetail(
    await api<PackageDetailWire>(`/api/evaluation-packages/${packageId}`),
  ),
  approve: async (packageId: number, note: string) => unwrapDetail(
    await api<PackageDetailWire>(`/api/evaluation-packages/${packageId}/approve`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
  reject: async (packageId: number, note: string) => unwrapDetail(
    await api<PackageDetailWire>(`/api/evaluation-packages/${packageId}/reject`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
  publish: async (packageId: number, note: string) => unwrapDetail(
    await api<PackageDetailWire>(`/api/evaluation-packages/${packageId}/publish`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
  archive: async (packageId: number, note: string) => unwrapDetail(
    await api<PackageDetailWire>(`/api/evaluation-packages/${packageId}/archive`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
}

export const evaluationPackageStatusMeta: Record<EvaluationPackageStatus, {
  label: string
  tone: "neutral" | "active" | "warning" | "danger" | "success"
  description: string
}> = {
  draft: { label: "准备中", tone: "neutral", description: "正在核对素材和类目运行方案" },
  ready: { label: "可以开始", tone: "active", description: "开始条件已经满足" },
  queued: { label: "等待评测", tone: "active", description: "已进入评测队列" },
  evaluating: { label: "正在评测", tone: "active", description: "系统正在处理素材" },
  first_review: { label: "等待一审", tone: "warning", description: "需要审核员确认或纠偏" },
  optimizing: { label: "正在自动优化", tone: "active", description: "系统正在根据纠偏改进方案" },
  regressing: { label: "正在验证改进", tone: "active", description: "系统正在用黄金样本验证新版" },
  second_review: { label: "等待二审", tone: "warning", description: "完整新版评测包已准备好" },
  approved: { label: "二审已通过", tone: "success", description: "已具备人工发布资格" },
  rejected: { label: "二审已拒绝", tone: "danger", description: "需要按二审意见继续改进" },
  publishing: { label: "正在发布", tone: "active", description: "系统正在生成正式版本" },
  published: { label: "已发布", tone: "success", description: "正式版本已经生效" },
  blocked: { label: "需要处理", tone: "danger", description: "存在阻塞，处理后可继续" },
  failed: { label: "处理未完成", tone: "danger", description: "本次运行遇到问题" },
  archived: { label: "已归档", tone: "neutral", description: "评测包已停止推进并保留记录" },
}

export function packageStatusMeta(status: string | undefined) {
  if (status && status in evaluationPackageStatusMeta) {
    return evaluationPackageStatusMeta[status as EvaluationPackageStatus]
  }
  return { label: "状态待确认", tone: "neutral" as const, description: "刷新后查看最新状态" }
}

const timelineDefinition = [
  { key: "materials", label: "导入素材", description: "素材进入不可变素材包" },
  { key: "configuration", label: "冻结类目方案", description: "按类目保存本次运行依据" },
  { key: "evaluation", label: "模型评测", description: "批量生成分类、画质和美感结果" },
  { key: "first_review", label: "一审纠偏", description: "人工只处理需要确认的结果" },
  { key: "optimization", label: "自动优化与验证", description: "系统生成新版并完成黄金样本验证" },
  { key: "release", label: "二审与发布", description: "查看完整证据后通过或拒绝" },
] as const

function activeTimelineIndex(status: EvaluationPackageStatus) {
  if (status === "draft" || status === "ready" || status === "blocked" || status === "archived") return 1
  if (status === "queued" || status === "evaluating" || status === "failed") return 2
  if (status === "first_review") return 3
  if (status === "optimizing" || status === "regressing") return 4
  return 5
}

export function buildEvaluationPackageTimeline(
  status: EvaluationPackageStatus | undefined,
): EvaluationPackageTimelineStep[] {
  if (!status) {
    return timelineDefinition.map((step, index) => ({
      ...step,
      status: index === 0 ? "current" : "pending",
    }))
  }
  if (status === "published") {
    return timelineDefinition.map((step) => ({ ...step, status: "completed" }))
  }
  const activeIndex = activeTimelineIndex(status)
  return timelineDefinition.map((step, index) => ({
    ...step,
    status: index < activeIndex
      ? "completed"
      : index > activeIndex
        ? "pending"
        : status === "failed"
          ? "failed"
          : status === "blocked" || status === "rejected" || status === "archived"
            ? "blocked"
            : "current",
  }))
}

function categoryConfigurationReady(category: EvaluationCategoryProfile) {
  if (!category.model_config_id || !category.prompt_a_id) return false
  return category.pipeline_config.prompt_mode !== "ab" || Boolean(category.prompt_b_id)
}

export function buildPipelineReadiness(
  materialPackage: MaterialPackage | undefined,
  category: EvaluationCategoryProfile | undefined,
): PipelineReadinessCheck[] {
  const materialsReady = Boolean(materialPackage?.active_asset_count)
  const categoryReady = category?.status === "active"
  const configurationReady = Boolean(categoryReady && category && categoryConfigurationReady(category))
  return [
    {
      key: "materials",
      label: "素材包可用",
      description: materialsReady
        ? `已选择“${materialPackage?.name}”，共 ${materialPackage?.active_asset_count} 份可用素材`
        : "请选择一个包含可用素材的素材包",
      ready: materialsReady,
      action_label: "去导入素材",
      action_href: "/workflow/materials/packages",
    },
    {
      key: "category",
      label: "类目队列已开启",
      description: categoryReady
        ? `“${category?.display_name}”队列当前可接收任务`
        : "请选择一个已经启用的类目队列",
      ready: categoryReady,
      action_label: "查看类目设置",
      action_href: "/workflow/optimization/dimensions",
    },
    {
      key: "configuration",
      label: "运行方案已就绪",
      description: configurationReady
        ? "系统会自动使用该类目已经确认的冻结方案"
        : "该类目还缺少可执行的模型或评测方案",
      ready: configurationReady,
      action_label: "补齐运行方案",
      action_href: "/workflow/governance",
    },
  ]
}

export function toOperatorError(error: unknown): OperatorError {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return { title: "登录状态已失效", message: "请重新登录后继续操作。", retryable: false, kind: "permission" }
    }
    if (error.status === 403) {
      return { title: "当前账号无权执行此操作", message: "可请管理员处理，或切换到具备相应权限的账号。", retryable: false, kind: "permission" }
    }
    if (error.status === 404) {
      return { title: "暂时无法找到评测包服务", message: "所需接口可能尚未部署，或这条评测包记录已经不存在。", retryable: true, kind: "missing" }
    }
    if (error.status === 409) {
      return { title: "页面内容已经发生变化", message: "其他操作员或系统刚刚更新了这条记录，请刷新后再决定。", retryable: true, kind: "conflict" }
    }
    if (error.status === 422 || error.status === 400) {
      const hasChineseMessage = /[\u3400-\u9fff]/u.test(error.message)
      const fallback = "请检查素材包、类目队列和备注后再试。"
      return {
        title: "还不能完成这项操作",
        message: hasChineseMessage ? operatorSafeText(error.message, fallback) : fallback,
        retryable: false,
        kind: "validation",
      }
    }
    if (error.status === 429) {
      return { title: "系统当前处理较忙", message: "请稍后刷新，已经提交的任务不会重复创建。", retryable: true, kind: "service" }
    }
    return { title: "系统暂时没有完成请求", message: "请稍后重试；若持续出现，可将发生时间告知管理员。", retryable: true, kind: "service" }
  }
  if (error instanceof TypeError) {
    return { title: "网络连接失败", message: "请检查当前网络和服务地址，连接恢复后可直接重试。", retryable: true, kind: "network" }
  }
  return { title: "操作没有完成", message: "请刷新页面后重试；现有记录不会因此被删除。", retryable: true, kind: "service" }
}

export function percentText(value: number | null | undefined) {
  return value == null ? "暂无" : `${Math.round(value * 1000) / 10}%`
}

const advancedTermPattern = /\bprompt\b|prompt_|提示词|阈值|dry[-_ ]?run|\bbudget\b|\blease\b|\bretry\b|\bcooldown\b/iu

export function operatorSafeText(value: string | null | undefined, fallback: string) {
  const normalized = value?.trim()
  return normalized && !advancedTermPattern.test(normalized) ? normalized : fallback
}
