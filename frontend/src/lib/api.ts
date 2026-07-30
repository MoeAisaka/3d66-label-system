import type {
  Asset,
  BaselineLevel,
  BaselineRegressionDetail,
  BaselineRegressionRun,
  BaselineSetDetail,
  BaselineSetSummary,
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
  listAssets: () => api<{ items: Asset[]; total: number }>("/api/assets?limit=1000"),
  uploadAssets: (files: FileList | File[]) => {
    const form = new FormData()
    Array.from(files).forEach((file) => form.append("files", file))
    return api<{ items: Asset[] }>("/api/assets/upload", { method: "POST", body: form })
  },
  listSets: () => api<{ items: BaselineSetSummary[] }>("/api/baseline-sets"),
  getSet: (setId: number) => api<BaselineSetDetail>(`/api/baseline-sets/${setId}`),
  createSet: (payload: {
    name: string
    description: string
    default_expected_level: BaselineLevel
    items: Array<{
      asset_id: number
      expected_level?: BaselineLevel
      source_package_id?: number
    }>
  }) => api<BaselineSetSummary>("/api/baseline-sets", {
    method: "POST",
    ...jsonBody(payload),
  }),
  createRun: (setId: number) => api<BaselineRegressionRun & { job_ids: number[] }>(
    `/api/baseline-sets/${setId}/runs`,
    { method: "POST" },
  ),
  getRun: (runId: number) => api<BaselineRegressionDetail>(
    `/api/baseline-regressions/${runId}`,
  ),
}
