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
