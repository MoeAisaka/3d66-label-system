export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
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
    try {
      const data = await response.json()
      message = data.detail || data.message || message
    } catch {
      // 保留通用错误信息
    }
    throw new ApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) }
}
