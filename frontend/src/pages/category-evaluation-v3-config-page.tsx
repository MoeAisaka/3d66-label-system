import { Suspense, useEffect, useMemo, useState } from "react"
import { ArrowClockwise, Plus, WarningCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { EvaluationBoundaryNote } from "@/components/evaluation-boundary-note"
import { RouteErrorState } from "@/components/route-error-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { MechanismEditorBoundary } from "@/features/mechanism-config/mechanism-editor-boundary"
import { getMechanismEditorPlugin } from "@/features/mechanism-config/registry"
import {
  cloneEditable,
  revisionToEditable,
  type ConfigDetail,
  type ConfigRevision,
  type ConfigSummary,
  type Editable,
  type JsonObject,
  type ValidationErrorItem,
} from "@/features/mechanism-config/types"
import { UnknownMechanismSummary } from "@/features/mechanism-config/unknown-mechanism-summary"
import { api, ApiError } from "@/lib/api"

const BASE = "/api/category-evaluation/v3-config"

type RevisionListResponse = {
  projected_revision_id: number
  candidate_count: number
  items: ConfigRevision[]
}

type ValidateResponse = { ok: boolean; errors: ValidationErrorItem[] }

const STATUS_TONE: Record<string, "success" | "active" | "neutral" | "warning"> = {
  active: "success",
  draft: "active",
  candidate: "warning",
  retired: "neutral",
}

const STATUS_LABEL: Record<string, string> = {
  active: "现役",
  draft: "草稿投影",
  candidate: "候选 · 未发布",
  retired: "已退役",
}

const LEVELS = ["L1", "L2", "L3", "L4", "L5"] as const

function blankImageEditable(): Editable {
  const trackKey = "class_default"
  return {
    category_key: "",
    display_name: "",
    contract: {
      schema_version: "evaluation-category-profile-v3",
      category_key: "",
      profile_type: "image-rule-deduction-v1",
      level_semantics_version: "doc-l5-worst-v1",
      level_scale: {
        version: "category-level-scale-v1",
        levels: LEVELS.map((level, index) => ({
          level,
          enabled: true,
          min_score: [80, 60, 40, 20, 0][index],
          display_name: level,
        })),
      },
      redline_policy: {
        format_version: "redline-policy-v1",
        enabled: true,
        hit_level: "L5",
        hit_score_cap: 49,
        rules: [],
      },
      track_classification: {
        format_version: "track-classification-v1",
        default_track: trackKey,
        tracks: [{
          key: trackKey,
          label: "兜底赛道",
          base_score: 40,
          dimension_max: 30,
          track_cap: 70,
          dimension_schema_ref: { schema_key: "space_v13", version: "v13" },
        }],
      },
      common_modifiers: {
        format_version: "common-modifiers-v1",
        media_type_penalty: {
          enabled: true,
          baseline: "real_photo",
          penalties: { real_photo: 0, render_3d: -5, ai_image: -15, other: 0 },
        },
        high_score_veto: { threshold: 80, cap_to: 79 },
      },
    },
    classification_map: {
      format_version: "subcategory-classification-map-v1",
      min_confidence: 0.6,
      category_to_subcategory: { 其它: trackKey },
      out_of_scope_subcategory: trackKey,
    },
    subcategory_dimensions: {
      [trackKey]: {
        format_version: "subcategory-dimensions-v1",
        sub_category_key: trackKey,
        dimension_max: 30,
        common_group: null,
        specific_group: null,
      },
    },
  }
}

function requestBody(source: Editable) {
  return {
    category_key: source.category_key,
    display_name: source.display_name,
    contract: source.contract,
    classification_map: source.classification_map,
    subcategory_dimensions: source.subcategory_dimensions,
  }
}

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.detail
    return detail?.code
      ? `${detail.code}: ${String(detail.message ?? err.message)}`
      : err.message
  }
  return String(err)
}

export function CategoryEvaluationV3ConfigPage() {
  const listQuery = useQuery({
    queryKey: ["category-evaluation-v3-config", "list"],
    queryFn: () => api<{ items: ConfigSummary[] }>(`${BASE}/`),
  })
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [selectedRevisionId, setSelectedRevisionId] = useState<number | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [draft, setDraft] = useState<Editable | null>(null)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<ValidationErrorItem[]>([])
  const [banner, setBanner] = useState<string | null>(null)

  useEffect(() => {
    const first = listQuery.data?.items[0]
    if (!isNew && selectedKey === null && first) setSelectedKey(first.category_key)
  }, [isNew, listQuery.data, selectedKey])

  const detailQuery = useQuery({
    queryKey: ["category-evaluation-v3-config", "detail", selectedKey],
    queryFn: () => api<ConfigDetail>(`${BASE}/${encodeURIComponent(selectedKey ?? "")}`),
    enabled: !isNew && selectedKey !== null,
  })
  const revisionsQuery = useQuery({
    queryKey: ["category-evaluation-v3-config", "revisions", selectedKey],
    queryFn: () => api<RevisionListResponse>(
      `${BASE}/${encodeURIComponent(selectedKey ?? "")}/revisions`,
    ),
    enabled: !isNew && selectedKey !== null,
  })

  const runtimeRevision = useMemo(() => {
    const data = revisionsQuery.data
    return data?.items.find((item) => item.id === data.projected_revision_id) ?? null
  }, [revisionsQuery.data])
  const selectedRevision = useMemo(() => {
    const items = revisionsQuery.data?.items ?? []
    return items.find((item) => item.id === selectedRevisionId) ?? runtimeRevision
  }, [revisionsQuery.data, runtimeRevision, selectedRevisionId])

  useEffect(() => {
    if (isNew) return
    if (runtimeRevision && selectedRevisionId === null) {
      setSelectedRevisionId(runtimeRevision.id)
    }
  }, [isNew, runtimeRevision, selectedRevisionId])

  useEffect(() => {
    if (!isNew && selectedRevision) setDraft(revisionToEditable(selectedRevision))
  }, [isNew, selectedRevision])

  const profileType = isNew
    ? "image-rule-deduction-v1"
    : selectedRevision?.mechanism_profile.profile_type ?? null
  const plugin = getMechanismEditorPlugin(profileType)

  const startNew = () => {
    setIsNew(true)
    setSelectedKey(null)
    setSelectedRevisionId(null)
    setDraft(blankImageEditable())
    setErrors([])
    setBanner(null)
  }

  const selectCategory = (categoryKey: string) => {
    setIsNew(false)
    setSelectedKey(categoryKey)
    setSelectedRevisionId(null)
    setDraft(null)
    setErrors([])
    setBanner(null)
  }

  const patchDraft = (mutator: (next: Editable) => void) => {
    setDraft((current) => {
      if (!current) return current
      const next = cloneEditable(current)
      mutator(next)
      if (isNew) next.contract.category_key = next.category_key
      return next
    })
  }

  const runValidate = async () => {
    if (!draft) return
    const outgoingDraft = plugin?.prepareForSave?.(draft) ?? draft
    setBusy(true)
    setBanner(null)
    try {
      const key = draft.category_key.trim() || "candidate"
      const result = await api<ValidateResponse>(
        `${BASE}/${encodeURIComponent(key)}/validate`,
        { method: "POST", body: JSON.stringify(requestBody(outgoingDraft)) },
      )
      setErrors(result.errors)
      setBanner(result.ok ? "校验通过，可以创建候选版本。" : `校验发现 ${result.errors.length} 处问题。`)
    } catch (err) {
      setBanner(errMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const createCandidate = async () => {
    if (!draft) return
    const outgoingDraft = plugin?.prepareForSave?.(draft) ?? draft
    if (!draft.category_key.trim()) {
      setBanner("请先填写 category_key。")
      return
    }
    setBusy(true)
    setBanner(null)
    setErrors([])
    try {
      if (isNew) {
        const created = await api<ConfigDetail>(`${BASE}/`, {
          method: "POST",
          body: JSON.stringify(requestBody(outgoingDraft)),
        })
        setIsNew(false)
        setSelectedKey(created.category_key)
        setSelectedRevisionId(created.projected_revision_id)
        setBanner(`初始草稿已创建：revision ${created.revision}。`)
      } else {
        if (!runtimeRevision || !selectedRevision || !detailQuery.data) {
          setBanner("运行时投影或所选版本尚未加载，请刷新后重试。")
          return
        }
        const created = await api<ConfigRevision>(
          `${BASE}/${encodeURIComponent(draft.category_key)}/revisions`,
          {
            method: "POST",
            body: JSON.stringify({
              ...requestBody(outgoingDraft),
              parent_revision_id: selectedRevision.id,
              expected_projected_revision: detailQuery.data.revision,
              expected_projected_contract_hash: detailQuery.data.contract_hash,
            }),
          },
        )
        setSelectedRevisionId(created.id)
        setDraft(revisionToEditable(created))
        setBanner(`候选 revision ${created.revision} 已创建，未发布且未改变现役合同。`)
      }
      await Promise.all([
        listQuery.refetch(),
        detailQuery.refetch(),
        revisionsQuery.refetch(),
      ])
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.detail?.errors)) {
        setErrors(err.detail.errors as ValidationErrorItem[])
      }
      setBanner(errMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const refreshSelected = () => {
    setBanner(null)
    void Promise.all([listQuery.refetch(), detailQuery.refetch(), revisionsQuery.refetch()])
  }

  if (listQuery.isError) {
    return (
      <RouteErrorState
        title="类目评测合同列表加载失败"
        message={errMessage(listQuery.error)}
        onRetry={() => void listQuery.refetch()}
        backTo="/workflow/system"
      />
    )
  }

  const selectedLoadError = detailQuery.error ?? revisionsQuery.error
  const selectedLoading = !isNew && selectedKey !== null
    && (detailQuery.isLoading || revisionsQuery.isLoading)
  const items = listQuery.data?.items ?? []

  return (
    <>
      <PageHeader
        index="A.7"
        title="类目评测 v3 合同配置"
        description="运行时投影只读；结构化编辑始终追加候选 revision，候选不会自动发布、重跑或写入正式标签事实。"
        actions={
          <Button variant="secondary" onClick={refreshSelected} disabled={busy}>
            <ArrowClockwise />刷新
          </Button>
        }
      />
      <div className="mx-auto max-w-[1540px] px-5 py-6 md:px-8 lg:px-10">
        <div className="mb-4"><EvaluationBoundaryNote slot="dimension" /></div>
        <div className="mb-4 flex items-start gap-3 border-y border-[var(--line)] bg-[#fff6e9] px-4 py-3 text-xs leading-6 text-[#7d4308]">
          <WarningCircle className="mt-0.5 shrink-0" size={16} weight="fill" />
          <p>机制发布轴与标签事实发布轴保持独立。这里创建的版本仅为候选，人工发布门禁尚未在本批绑定。</p>
        </div>

        <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="border border-[var(--line)] bg-white">
            <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
              <h2 className="text-sm font-bold">v3 配置</h2>
              <Button size="sm" onClick={startNew} disabled={busy}><Plus />新建</Button>
            </div>
            {listQuery.isLoading ? (
              <p className="px-4 py-8 text-center text-xs text-[var(--muted)]">加载中…</p>
            ) : items.length === 0 ? (
              <p className="px-4 py-8 text-center text-xs text-[var(--muted)]">暂无配置。</p>
            ) : (
              <ul className="divide-y divide-[var(--line)]">
                {items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => selectCategory(item.category_key)}
                      className={`flex w-full flex-col gap-1 px-4 py-3 text-left text-xs ${
                        !isNew && item.category_key === selectedKey ? "bg-[#f0f8c8]" : "hover:bg-[#f8f9f6]"
                      }`}
                    >
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-data font-semibold">{item.category_key}</span>
                        <Badge tone={STATUS_TONE[item.status] ?? "neutral"}>{STATUS_LABEL[item.status] ?? item.status}</Badge>
                        {item.candidate_count > 0 && <Badge tone="warning">候选 {item.candidate_count}</Badge>}
                      </span>
                      <span className="text-[var(--muted)]">{item.display_name}</span>
                      <span className="text-[0.68rem] text-[var(--muted)]">rev {item.revision} · {item.contract_hash.slice(0, 12)}…</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>

          <main className="min-w-0">
            {selectedLoadError ? (
              <RouteErrorState
                title="合同版本加载失败"
                message={errMessage(selectedLoadError)}
                onRetry={refreshSelected}
              />
            ) : selectedLoading ? (
              <div className="border-y border-[var(--line)] bg-white px-5 py-12 text-center text-sm text-[var(--muted)]">加载合同与 revision 历史…</div>
            ) : !draft ? (
              <div className="border border-dashed border-[var(--line-strong)] bg-white px-5 py-12 text-center text-sm text-[var(--muted)]">选择一份配置，或创建新的图像机制配置。</div>
            ) : (
              <div className="space-y-4">
                {!isNew && selectedRevision && (
                  <section className="flex flex-wrap items-center justify-between gap-3 border-y border-[var(--line)] bg-white px-4 py-3 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={STATUS_TONE[selectedRevision.status] ?? "neutral"}>{STATUS_LABEL[selectedRevision.status] ?? selectedRevision.status}</Badge>
                      <span className="font-data">revision {selectedRevision.revision}</span>
                      <span className="font-data text-[var(--muted)]">{selectedRevision.contract_hash.slice(0, 12)}…</span>
                      {selectedRevision.id === runtimeRevision?.id && <span className="font-semibold">运行时投影</span>}
                    </div>
                    <label className="flex items-center gap-2 font-semibold">
                      查看版本
                      <select
                        className="h-9 border border-[var(--line-strong)] bg-white px-2 font-data text-xs"
                        value={selectedRevision.id}
                        onChange={(event) => {
                          setSelectedRevisionId(Number(event.target.value))
                          setErrors([])
                          setBanner(null)
                        }}
                      >
                        {(revisionsQuery.data?.items ?? []).map((revision) => (
                          <option key={revision.id} value={revision.id}>
                            rev {revision.revision} · {STATUS_LABEL[revision.status] ?? revision.status}
                          </option>
                        ))}
                      </select>
                    </label>
                  </section>
                )}
                <MechanismEditorBoundary detail={selectedRevision} onRetry={refreshSelected}>
                  <Suspense fallback={<div className="border-y border-[var(--line)] bg-white px-5 py-10 text-sm text-[var(--muted)]">加载机制编辑器…</div>}>
                    {plugin ? (
                      <plugin.Editor
                        draft={draft}
                        runtimeRevision={runtimeRevision}
                        selectedRevision={selectedRevision}
                        busy={busy}
                        banner={banner}
                        errors={errors}
                        onPatch={patchDraft}
                        onValidate={runValidate}
                        onCreateCandidate={createCandidate}
                      />
                    ) : (
                      <UnknownMechanismSummary detail={selectedRevision} />
                    )}
                  </Suspense>
                </MechanismEditorBoundary>
              </div>
            )}
          </main>
        </div>
      </div>
    </>
  )
}

export function safeJson(value: JsonObject): string {
  return JSON.stringify(value, null, 2)
}
