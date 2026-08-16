import { api } from "@/lib/api"
import type {
  ProductionRunSummary,
  RuntimeAction,
  RuntimeSnapshot,
  RuntimeTimelineItem,
} from "@/lib/types"

export const runtimeApi = {
  listRuns: (filters: {
    status?: string
    queueClass?: string
    categoryKey?: string
    limit?: number
  } = {}) => {
    const params = new URLSearchParams()
    if (filters.status) params.set("status", filters.status)
    if (filters.queueClass) params.set("queue_class", filters.queueClass)
    if (filters.categoryKey) params.set("category_key", filters.categoryKey)
    params.set("limit", String(filters.limit ?? 100))
    return api<{ items: ProductionRunSummary[] }>(
      "/api/runtime/runs?" + params.toString(),
    )
  },
  getRun: (runKey: string) => api<ProductionRunSummary>(
    "/api/runtime/runs/" + encodeURIComponent(runKey),
  ),
  getTimeline: (runKey: string) => api<{
    run_key: string
    items: RuntimeTimelineItem[]
  }>("/api/runtime/runs/" + encodeURIComponent(runKey) + "/timeline"),
  getSnapshot: (runKey: string) => api<RuntimeSnapshot>(
    "/api/runtime/runs/" + encodeURIComponent(runKey) + "/snapshot",
  ),
  action: (runKey: string, action: RuntimeAction) => api<ProductionRunSummary>(
    "/api/runtime/runs/" + encodeURIComponent(runKey) + "/" + action,
    { method: "POST" },
  ),
}

