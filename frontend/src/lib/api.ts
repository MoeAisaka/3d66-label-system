import type {
  Asset,
  BaselineCorrectionRun,
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

function downloadFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  const encoded = utf8 ?? plain
  if (!encoded) return fallback
  try {
    return decodeURIComponent(encoded)
  } catch {
    return fallback
  }
}

export async function downloadApi(
  path: string,
  fallbackFilename: string,
  init: RequestInit = {},
): Promise<{ filename: string; rowCount: number | null }> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const response = await fetch(path, { ...init, headers, credentials: "include" })
  if (!response.ok) {
    let message = `导出失败 (${response.status})`
    try {
      const data = await response.json() as { detail?: unknown }
      if (typeof data.detail === "string") message = data.detail
    } catch {
      // 保留通用错误信息
    }
    throw new ApiError(message, response.status)
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  const filename = downloadFilename(response, fallbackFilename)
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
  const rawCount = response.headers.get("X-Export-Row-Count")
  const parsedCount = rawCount == null ? null : Number(rawCount)
  return { filename, rowCount: Number.isFinite(parsedCount) ? parsedCount : null }
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) }
}

export const baselineRegressionApi = {
  listAssets: (packageId?: number, categoryKey?: string) => {
    const params = new URLSearchParams({ limit: "1000" })
    if (packageId) params.set("package_id", String(packageId))
    if (categoryKey) params.set("category_key", categoryKey)
    return api<{ items: Asset[]; total: number }>(`/api/assets?${params.toString()}`)
  },
  listPackages: (categoryKey?: string) => {
    const params = new URLSearchParams({ limit: "500" })
    if (categoryKey) params.set("category_key", categoryKey)
    return api<{ items: MaterialPackage[] }>(`/api/material-packages?${params.toString()}`)
  },
  uploadAssets: (files: readonly File[], packageName?: string, categoryKey?: string) => {
    const form = new FormData()
    files.forEach((file) => form.append("files", file))
    if (packageName?.trim()) form.append("package_name", packageName.trim())
    if (categoryKey) form.append("category_key", categoryKey)
    return api<{
      items: Asset[]
      package: { id: number; name: string; item_count: number }
    }>("/api/assets/upload", { method: "POST", body: form })
  },
  listSets: (categoryKey?: string) => {
    const params = new URLSearchParams()
    if (categoryKey) params.set("category_key", categoryKey)
    const query = params.size ? `?${params.toString()}` : ""
    return api<{ items: BaselineSetSummary[] }>(`/api/baseline-sets${query}`)
  },
  getSet: (setId: number) => api<BaselineSetDetail>(`/api/baseline-sets/${setId}`),
  createSet: (payload: {
    name: string
    description: string
    default_expected_level: BaselineLevel
    category_key: string
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
  listPrompts: (categoryKey?: string) => {
    const params = new URLSearchParams({ pipeline_scope: "baseline_regression" })
    if (categoryKey) params.set("category_key", categoryKey)
    const query = `?${params.toString()}`
    return api<{ items: PromptVersion[] }>(`/api/prompts${query}`)
  },
  createRun: (setId: number, payload: {
    prompt_id?: number
    prompt_a_id?: number
    prompt_b_id?: number
    dimension_schema_id?: number
    dimension_mode?: "category_default" | "all" | "none"
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
  listCorrectionRuns: (runId: number) => api<{ items: BaselineCorrectionRun[] }>(
    `/api/baseline-regressions/${runId}/corrections`,
  ),
  createCorrectionRun: (
    runId: number,
    itemIds: number[],
    idempotencyKey: string,
  ) => api<BaselineCorrectionRun>(
    `/api/baseline-regressions/${runId}/corrections`,
    {
      method: "POST",
      ...jsonBody({ item_ids: itemIds, idempotency_key: idempotencyKey }),
    },
  ),
  retryCorrectionRun: (correctionRunId: number) => api<BaselineCorrectionRun>(
    `/api/baseline-corrections/${correctionRunId}/retry`,
    { method: "POST" },
  ),
}

export type PromptPipelineScope = "full_pipeline" | "baseline_regression" | "shared"

export const promptApi = {
  update: (promptId: number, payload: {
    category_key: string
    pipeline_scope: PromptPipelineScope
    stage: "A" | "B"
    name: string
    version: string
    system_prompt: string
    user_prompt: string
    rubric_version: string
    change_note?: string
  }) => api<PromptVersion>(`/api/prompts/${promptId}`, {
    method: "PUT",
    ...jsonBody(payload),
  }),
  clone: (promptId: number, payload: {
    category_key: string
    pipeline_scope: PromptPipelineScope
    stage: "A" | "B"
    name: string
    version: string
    system_prompt: string
    user_prompt: string
    rubric_version: string
    change_note?: string
  }) => api<{ id: number }>(`/api/prompts/${promptId}/clone`, {
    method: "POST",
    ...jsonBody(payload),
  }),
  publish: (promptId: number, pipelineScope?: PromptPipelineScope) => api<{ ok: boolean; regression_run_ids?: number[] }>(
    `/api/prompts/${promptId}/publish`,
    { method: "POST", ...jsonBody(pipelineScope ? { pipeline_scope: pipelineScope } : {}) },
  ),
  archive: (promptId: number) => api<{ ok: boolean }>(`/api/prompts/${promptId}`, { method: "DELETE" }),
}
