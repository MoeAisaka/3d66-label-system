import { ApiError, api, jsonBody } from "@/lib/api"
import type {
  EvaluationCategoryProfile,
  EvaluationPackageDetail,
  EvaluationPackageStatus,
  EvaluationPackageSummary,
  EvaluationProductionRun,
  EvaluationProductionRunStatus,
  MaterialPackage,
} from "@/lib/types"

export type CreateEvaluationProductionRunInput = {
  material_package_id: number
  category_key: string
  idempotency_key: string
}

export type EvaluationProductionRunList = {
  items: EvaluationProductionRun[]
  total: number
}

export type EvaluationPackageList = {
  items: EvaluationPackageSummary[]
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

type RunWire = EvaluationProductionRun | { run: EvaluationProductionRun }
type PackageWire = EvaluationPackageDetail | { item: EvaluationPackageDetail } | { package: EvaluationPackageDetail }
type PackageListWire = EvaluationPackageSummary[] | { items: EvaluationPackageSummary[] }

function unwrapRun(value: RunWire): EvaluationProductionRun {
  return "run" in value ? value.run : value
}

function unwrapPackage(value: PackageWire): EvaluationPackageDetail {
  if ("package" in value) return value.package
  if ("item" in value) return value.item
  return value
}

export const evaluationProductionApi = {
  list: () => api<EvaluationProductionRunList>("/api/evaluation-production-runs"),
  create: async (payload: CreateEvaluationProductionRunInput) => unwrapRun(
    await api<RunWire>("/api/evaluation-production-runs", {
      method: "POST",
      ...jsonBody(payload),
    }),
  ),
  get: (runId: number) => api<EvaluationProductionRun>(`/api/evaluation-production-runs/${runId}`),
  reconcile: (runId: number) => api<EvaluationProductionRun>(`/api/evaluation-production-runs/${runId}/reconcile`, {
    method: "POST",
  }),
}

export const evaluationPackageApi = {
  list: async (): Promise<EvaluationPackageList> => {
    const value = await api<PackageListWire>("/api/evaluation-packages")
    return { items: Array.isArray(value) ? value : value.items }
  },
  get: async (packageId: number) => unwrapPackage(
    await api<PackageWire>(`/api/evaluation-packages/${packageId}`),
  ),
  approve: async (packageId: number, note: string) => unwrapPackage(
    await api<PackageWire>(`/api/evaluation-packages/${packageId}/approve`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
  reject: async (packageId: number, note: string) => unwrapPackage(
    await api<PackageWire>(`/api/evaluation-packages/${packageId}/reject`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
  publish: async (packageId: number, note: string) => unwrapPackage(
    await api<PackageWire>(`/api/evaluation-packages/${packageId}/publish`, {
      method: "POST",
      ...jsonBody({ note }),
    }),
  ),
  archive: async (packageId: number, reason: string) => unwrapPackage(
    await api<PackageWire>(`/api/evaluation-packages/${packageId}/archive`, {
      method: "POST",
      ...jsonBody({ reason }),
    }),
  ),
}

export const productionStatusMeta: Record<EvaluationProductionRunStatus, {
  label: string
  tone: "neutral" | "active" | "warning" | "danger" | "success"
}> = {
  preparing: { label: "准备运行", tone: "neutral" },
  queued: { label: "等待评测", tone: "active" },
  evaluating: { label: "正在评测", tone: "active" },
  first_review: { label: "等待一审", tone: "warning" },
  optimizing: { label: "正在自动改进", tone: "active" },
  regressing: { label: "正在黄金集回归", tone: "active" },
  awaiting_review: { label: "等待二审", tone: "warning" },
  approved: { label: "二审已通过", tone: "success" },
  rejected: { label: "二审已拒绝", tone: "danger" },
  published: { label: "已发布", tone: "success" },
  blocked: { label: "需要处理", tone: "danger" },
  failed: { label: "处理失败", tone: "danger" },
  archived: { label: "已归档", tone: "neutral" },
}

export const evaluationPackageStatusMeta: Record<EvaluationPackageStatus, {
  label: string
  tone: "neutral" | "active" | "warning" | "danger" | "success"
  description: string
}> = {
  validating: { label: "回归验证中", tone: "active", description: "回归尚未完成，暂不能二审" },
  awaiting_review: { label: "等待二审", tone: "warning", description: "冻结证据已齐备，等待人工决定" },
  approved: { label: "二审已通过", tone: "success", description: "仍需单独执行发布" },
  rejected: { label: "二审已拒绝", tone: "danger", description: "证据已保留，不会自动发布" },
  published: { label: "已发布", tone: "success", description: "已由人工明确发布" },
  archived: { label: "已归档", tone: "neutral", description: "记录与冻结证据继续保留" },
}

export function packageStatusMeta(status: EvaluationPackageStatus) {
  return evaluationPackageStatusMeta[status]
}

function categoryConfigurationReady(category: EvaluationCategoryProfile) {
  if (category.pipeline_config.prompt_mode === "single") return Boolean(category.prompt_a_id)
  if (category.pipeline_config.prompt_mode === "ab") return Boolean(category.prompt_a_id && category.prompt_b_id)
  return true
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
      description: categoryReady ? `“${category?.display_name}”队列当前可接收任务` : "请选择一个已经启用的类目队列",
      ready: categoryReady,
      action_label: "查看类目设置",
      action_href: "/workflow/optimization/category-evaluation-v3-config",
    },
    {
      key: "configuration",
      label: "类目方案可冻结",
      description: configurationReady ? "系统会在开始时冻结该类目的完整执行方案" : "该类目的评测方案尚不完整",
      ready: configurationReady,
      action_label: "补齐运行方案",
      action_href: "/workflow/governance",
    },
  ]
}

export function toOperatorError(error: unknown): OperatorError {
  if (error instanceof ApiError) {
    if (error.status === 401) return { title: "登录状态已失效", message: "请重新登录后继续操作。", retryable: false, kind: "permission" }
    if (error.status === 403) return { title: "当前账号无权执行此操作", message: "请联系管理员处理。", retryable: false, kind: "permission" }
    if (error.status === 404) return { title: "记录不存在", message: "请刷新列表后重新选择。", retryable: true, kind: "missing" }
    if (error.status === 409) return { title: "当前条件尚未满足", message: operatorSafeText(error.message, "请处理页面显示的阻塞项后重试。"), retryable: true, kind: "conflict" }
    if (error.status === 422 || error.status === 400) return { title: "还不能完成这项操作", message: operatorSafeText(error.message, "请检查素材包、类目和备注。"), retryable: false, kind: "validation" }
    return { title: "系统暂时没有完成请求", message: "请稍后重试；已经保存的记录不会丢失。", retryable: true, kind: "service" }
  }
  if (error instanceof TypeError) return { title: "网络连接失败", message: "连接恢复后可直接重试。", retryable: true, kind: "network" }
  return { title: "操作没有完成", message: "请刷新后重试。", retryable: true, kind: "service" }
}

const advancedTermPattern = /\bprompt\b|prompt_|提示词编号|dry[-_ ]?run|\bbudget\b|\blease\b|\bretry\b|\bcooldown\b/iu

export function operatorSafeText(value: string | null | undefined, fallback: string) {
  const normalized = value?.trim()
  return normalized && !advancedTermPattern.test(normalized) ? normalized : fallback
}

export function percentText(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 1000) / 10}%`
    : "暂无"
}
