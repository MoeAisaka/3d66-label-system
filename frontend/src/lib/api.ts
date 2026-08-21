import type {
  Asset,
  BaselineCorrectionRun,
  BaselineFieldMetrics,
  BaselineLevel,
  BaselineRegressionDetail,
  BaselineRegressionRun,
  BaselineSemanticQualityMetrics,
  CorrectionSubmissionRequest,
  CorrectionView,
  BaselineSetDetail,
  BaselineSetSummary,
  BaselineV3RevisionList,
  BaselineV3Revision,
  ContentIdentityRecord,
  MaterialPackage,
  PromptVersion,
  SourceIdentityVerification,
  TagDemandContract,
  AutomationOverview,
  AutomationLaneSummary,
  AutomationCandidateReview,
  ReviewPanelSummary,
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
  listAssets: (
    packageId?: number,
    categoryKey?: string,
    offset = 0,
    limit = 200,
  ) => {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    })
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
  getSet: (setId: number, includeItems = true) => {
    const params = new URLSearchParams({ include_items: String(includeItems) })
    return api<BaselineSetDetail>(`/api/baseline-sets/${setId}?${params.toString()}`)
  },
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
  createBalanced100: () => api<{
    summary: BaselineSetSummary
    created: boolean
    idempotent: boolean
    item_count: number
    distribution: Record<string, number>
    fingerprint: string
  }>("/api/baseline-sets/inspiration-balanced-100", { method: "POST" }),
  listPrompts: (categoryKey?: string) => {
    const params = new URLSearchParams({ pipeline_scope: "baseline_regression" })
    if (categoryKey) params.set("category_key", categoryKey)
    const query = `?${params.toString()}`
    return api<{ items: PromptVersion[] }>(`/api/prompts${query}`)
  },
  listV3Revisions: (categoryKey: string) => api<BaselineV3RevisionList>(
    `/api/category-evaluation/v3-config/${encodeURIComponent(categoryKey)}/revisions`,
  ),
  activateV3Revision: (
    categoryKey: string,
    revision: number,
    payload: {
      regression_run_id: number
      expected_projected_revision: number
      expected_projected_contract_hash: string
      note?: string
    },
  ) => api<{
    category_key: string
    activated_revision: BaselineV3Revision
    regression_run_id: number
    regression_evidence: Record<string, unknown>
    mechanism_refresh: Record<string, unknown>
    audit_event_key: string
  }>(
    `/api/category-evaluation/v3-config/${encodeURIComponent(categoryKey)}/revisions/${revision}/activate`,
    { method: "POST", ...jsonBody(payload) },
  ),
  createRun: (setId: number, payload: {
    prompt_id?: number
    prompt_a_id?: number
    prompt_b_id?: number
    execution_mode?: "freeform" | "structured"
    candidate_revision_id?: number
    category_context?: {
      source: "baseline_set"
      category_key: string
    }
  } = {}) => api<BaselineRegressionRun & { job_ids: number[] }>(
    `/api/baseline-sets/${setId}/runs`,
    { method: "POST", ...jsonBody(payload) },
  ),
  getRun: (runId: number, offset = 0, limit = 200) => {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(limit),
    })
    return api<BaselineRegressionDetail>(
      `/api/baseline-regressions/${runId}?${params.toString()}`,
    )
  },
  getCorrectionView: (runId: number, itemId: number) => api<CorrectionView>(
    `/api/baseline-regressions/${runId}/items/${itemId}/correction-view`,
  ),
  submitCorrectionNodes: (runId: number, itemId: number, payload: CorrectionSubmissionRequest) => api<CorrectionView>(
    `/api/baseline-regressions/${runId}/items/${itemId}/corrections`,
    { method: "POST", ...jsonBody(payload) },
  ),
  getMetrics: (runId: number) => api<BaselineFieldMetrics>(
    `/api/baseline-regressions/${runId}/metrics`,
  ),
  getSemanticMetrics: (runId: number) => api<BaselineSemanticQualityMetrics>(
    `/api/baseline-regressions/${runId}/semantic-metrics`,
  ),
  enqueueDeviations: (runId: number, itemIds: number[]) => api<{
    run_id: number
    case_ids: number[]
    created: number
    idempotent: boolean
    purpose: string
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
  decideCorrectionRun: (
    correctionRunId: number,
    decision: "approved" | "rejected",
    note: string,
  ) => api<BaselineCorrectionRun>(
    `/api/baseline-corrections/${correctionRunId}/decision`,
    { method: "POST", ...jsonBody({ decision, note }) },
  ),
  reopenReview: (evaluationId: number, expectedReviewRevision: number) => api<ReviewPanelSummary>(
    `/api/evaluations/${evaluationId}/review-panel/reopen`,
    {
      method: "POST",
      ...jsonBody({ expected_review_revision: expectedReviewRevision }),
    },
  ),
}

export const candidateRegressionApi = {
  getCorrectionView: (runId: number, itemId: number) => api<CorrectionView>(
    `/api/prompt-regressions/${runId}/items/${itemId}/correction-view`,
  ),
  submitCorrectionNodes: (runId: number, itemId: number, payload: CorrectionSubmissionRequest) => api<CorrectionView>(
    `/api/prompt-regressions/${runId}/items/${itemId}/corrections`,
    { method: "POST", ...jsonBody(payload) },
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

export const tagDemandContractApi = {
  list: () => api<{ items: TagDemandContract[]; active_versions: Record<string, number> }>("/api/tag-demand-contracts"),
  get: (id: number) => api<TagDemandContract>(`/api/tag-demand-contracts/${id}`),
  create: (payload: { contract_key: string; definition: TagDemandContract["definition"]; status: "draft" | "candidate" }) => api<TagDemandContract>("/api/tag-demand-contracts", { method: "POST", ...jsonBody(payload) }),
  activate: (id: number) => api<TagDemandContract>(`/api/tag-demand-contracts/${id}/activate`, { method: "POST" }),
}

export const sourceIdentityApi = {
  list: () => api<{ items: SourceIdentityVerification[] }>("/api/source-identity-verifications"),
  approve: (id: number) => api<SourceIdentityVerification>(
    `/api/source-identity-verifications/${id}/approve`,
    { method: "POST" },
  ),
  bindContract: (contractId: number, verificationId: number) => api<TagDemandContract>(
    `/api/tag-demand-contracts/${contractId}/bind-source-identity-verification`,
    { method: "POST", ...jsonBody({ verification_id: verificationId }) },
  ),
}

export const contentIngressApi = {
  list: () => api<{ items: ContentIdentityRecord[] }>("/api/content-ingress/records"),
}

export const automationApi = {
  overview: () => api<AutomationOverview>("/api/automation/overview"),
  lanes: (pipelineKind?: "incremental" | "baseline") => {
    const query = pipelineKind ? `?pipeline_kind=${pipelineKind}` : ""
    return api<{ items: AutomationLaneSummary[] }>(`/api/automation/lanes${query}`)
  },
  candidates: () => api<{ items: AutomationCandidateReview[]; auto_publish_enabled: false }>("/api/automation/candidates"),
  decideCandidate: (id: number, decision: "approved" | "rejected", note: string) => api<{ id: number; decision: string; auto_publish: false; stock_rerun: false }>(`/api/automation/candidates/${id}/decision`, { method: "POST", ...jsonBody({ decision, note }) }),
}
