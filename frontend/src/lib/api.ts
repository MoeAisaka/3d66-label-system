import type {
  Asset,
  BaselineLevel,
  BaselineRegressionDetail,
  BaselineRegressionRun,
  BaselineSetDetail,
  BaselineSetSummary,
  MaterialPackage,
  PromptVersion,
} from "@/lib/types"

export type ApiErrorDetail = {
  code?: string
  message?: string
  current_state?: string
  attempted_transition?: string
  retryable?: boolean
  [key: string]: unknown
}

export class ApiError extends Error {
  status: number
  detail?: ApiErrorDetail

  constructor(message: string, status: number, detail?: ApiErrorDetail) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    let detail: ApiErrorDetail | undefined
    try {
      const data = await response.json() as {
        detail?: unknown
        message?: unknown
      }
      const rawDetail = data.detail ?? data.message
      if (typeof rawDetail === "string") {
        message = rawDetail
      } else if (rawDetail && typeof rawDetail === "object" && !Array.isArray(rawDetail)) {
        detail = rawDetail as ApiErrorDetail
        if (typeof detail.message === "string") {
          message = detail.message
        } else if (typeof detail.code === "string") {
          message = detail.code
        }
      } else if (Array.isArray(rawDetail)) {
        const validationMessages = rawDetail
          .map((item) => (
            item && typeof item === "object" && "msg" in item && typeof item.msg === "string"
              ? item.msg
              : null
          ))
          .filter((item): item is string => Boolean(item))
        if (validationMessages.length) {
          message = validationMessages.join("；")
        }
      }
    } catch {
      // 保留通用错误信息
    }
    throw new ApiError(message, response.status, detail)
  }
  return response.json() as Promise<T>
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) }
}

export const baselineRegressionApi = {
  listAssets: (packageId?: number) => {
    const params = new URLSearchParams({ limit: "1000" })
    if (packageId) params.set("package_id", String(packageId))
    return api<{ items: Asset[]; total: number }>(`/api/assets?${params.toString()}`)
  },
  listPackages: () => api<{ items: MaterialPackage[] }>("/api/material-packages?limit=500"),
  uploadAssets: (files: readonly File[], packageName?: string) => {
    const form = new FormData()
    files.forEach((file) => form.append("files", file))
    if (packageName?.trim()) form.append("package_name", packageName.trim())
    return api<{
      items: Asset[]
      package: { id: number; name: string; item_count: number }
    }>("/api/assets/upload", { method: "POST", body: form })
  },
  listSets: () => api<{ items: BaselineSetSummary[] }>("/api/baseline-sets"),
  getSet: (setId: number) => api<BaselineSetDetail>(`/api/baseline-sets/${setId}`),
  createSet: (payload: {
    name: string
    description: string
    default_expected_level: BaselineLevel
    source_package_id?: number
    expected_level_overrides?: Record<number, BaselineLevel>
    items: Array<{
      asset_id: number
      expected_level?: BaselineLevel
      source_package_id?: number
    }>
  }) => api<BaselineSetSummary>("/api/baseline-sets", {
    method: "POST",
    ...jsonBody(payload),
  }),
  listPrompts: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  createRun: (setId: number, payload: {
    prompt_a_id?: number
    prompt_b_id?: number
    dimension_schema_id?: number
  } = {}) => api<BaselineRegressionRun & { job_ids: number[] }>(
    `/api/baseline-sets/${setId}/runs`,
    { method: "POST", ...jsonBody(payload) },
  ),
  getRun: (runId: number) => api<BaselineRegressionDetail>(
    `/api/baseline-regressions/${runId}`,
  ),
  enqueueDeviations: (runId: number, itemIds: number[]) => api<{
    run_id: number
    case_ids: number[]
    created: number
    idempotent: boolean
  }>(`/api/baseline-regressions/${runId}/optimization-cases`, {
    method: "POST",
    ...jsonBody({ item_ids: itemIds }),
  }),
}
