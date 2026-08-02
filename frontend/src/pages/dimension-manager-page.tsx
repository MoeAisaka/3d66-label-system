import { useEffect, useMemo, useState } from "react"
import {
  Check,
  ArrowDown,
  ArrowUp,
  Copy,
  FloppyDisk,
  LockKey,
  Minus,
  Plus,
  RocketLaunch,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { api, jsonBody } from "@/lib/api"
import type {
  CategoryDimensionConfig,
  DimensionDefinition,
  DimensionSchemaRegistryItem,
  EvaluationCategoryProfile,
} from "@/lib/types"
import { cn } from "@/lib/utils"

const categoryStatusNames: Record<EvaluationCategoryProfile["status"], string> = {
  active: "已启用",
  draft: "草稿",
  retired: "已停用",
}

const schemaStatusNames: Record<DimensionSchemaRegistryItem["status"], string> = {
  draft: "草稿",
  candidate: "候选",
  published: "已发布",
  retired: "已停用",
}

function copyDimensionConfig(config: CategoryDimensionConfig): CategoryDimensionConfig {
  const keys = config.selected_keys ?? config.enabled_keys ?? []
  return {
    enabled: config.enabled,
    mode: config.enabled ? config.mode : "none",
    enabled_keys: [...keys],
    selected_keys: [...keys],
  }
}

function canonicalDimensionConfig(
  config: CategoryDimensionConfig,
  allowedKeys: ReadonlySet<string>,
): CategoryDimensionConfig {
  if (!config.enabled || config.mode === "none") return { enabled: false, mode: "none", enabled_keys: [], selected_keys: [] }
  if (config.mode === "all") return { enabled: true, mode: "all", enabled_keys: [], selected_keys: [] }
  const keys = (config.selected_keys ?? config.enabled_keys).filter((key, index, all) => (
    allowedKeys.has(key) && all.indexOf(key) === index
  ))
  return {
    enabled: true,
    mode: "selected",
    enabled_keys: keys,
    selected_keys: keys,
  }
}

function dimensionStatus(profile: EvaluationCategoryProfile) {
  const config = profile.pipeline_config.dimensions
  if (!config.enabled) return "仅提示词，不测维度"
  if (config.mode === "all") return "全部维度"
  return `已选 ${(config.selected_keys ?? config.enabled_keys).length} 个维度`
}

type SchemaDraft = {
  id: number | null
  schema_key: string
  version: string
  display_name: string
  family_key: DimensionSchemaRegistryItem["family_key"]
  parent_schema_id: number | null
  definition: NonNullable<DimensionSchemaRegistryItem["definition"]>
}

function cloneDefinition(definition: NonNullable<DimensionSchemaRegistryItem["definition"]>) {
  return JSON.parse(JSON.stringify(definition)) as NonNullable<DimensionSchemaRegistryItem["definition"]>
}

function cloneDimension(dimension: DimensionDefinition) {
  return JSON.parse(JSON.stringify(dimension)) as DimensionDefinition
}

function editableSchemaDraft(schema: DimensionSchemaRegistryItem): SchemaDraft | null {
  if (!schema.definition) return null
  return {
    id: schema.status === "draft" || schema.status === "candidate" ? schema.id : null,
    schema_key: schema.schema_key,
    version: schema.version,
    display_name: schema.display_name,
    family_key: schema.family_key,
    parent_schema_id: schema.status === "published" ? schema.id : schema.parent_schema_id,
    definition: cloneDefinition(schema.definition),
  }
}

function synchronizeDefinition(draft: SchemaDraft): SchemaDraft {
  const definition = cloneDefinition(draft.definition)
  definition.dimensions = definition.dimensions.map((item, index) => ({
    ...item,
    display_order: index + 1,
  }))
  const keys = definition.dimensions.map((item) => item.key)
  if (definition.output_contract) definition.output_contract.dimension_output_keys = keys
  if (definition.risk_review) definition.risk_review.dimension_keys = keys
  definition.core_dimension_keys = (definition.core_dimension_keys ?? []).filter((key) => keys.includes(key))
  definition.family_dimension_keys = keys.filter((key) => !definition.core_dimension_keys?.includes(key))
  const aggregation = definition.aggregation
  if (aggregation) {
    const collapse = aggregation.collapse_rule
    const highEvidence = aggregation.high_evidence_rule
    const topLevel = aggregation.top_level_rule
    if (collapse && typeof collapse.same_grade_count_for_review === "number") {
      collapse.same_grade_count_for_review = Math.min(collapse.same_grade_count_for_review, keys.length)
    }
    if (highEvidence && typeof highEvidence.dimensions_for_l3_cap === "number") {
      highEvidence.dimensions_for_l3_cap = Math.min(highEvidence.dimensions_for_l3_cap, keys.length)
    }
    if (topLevel && typeof topLevel.grade_five_minimum_count === "number") {
      topLevel.grade_five_minimum_count = Math.min(topLevel.grade_five_minimum_count, keys.length)
    }
  }
  return { ...draft, definition }
}

function categoryUpdatePayload(
  profile: EvaluationCategoryProfile,
  dimensions: CategoryDimensionConfig,
  schema?: Pick<DimensionSchemaRegistryItem, "schema_key" | "version"> | null,
) {
  const promptOnly = !dimensions.enabled || dimensions.mode === "none"
  return {
    display_name: profile.display_name,
    description: profile.description,
    status: profile.status,
    allowed_mime_types: profile.allowed_mime_types,
    preprocess_config: profile.preprocess_config,
    pipeline_config: {
      ...profile.pipeline_config,
      prompt_mode: promptOnly ? "single" : profile.pipeline_config.prompt_mode,
      dimensions,
    },
    prompt_a_id: profile.prompt_a_id,
    prompt_b_id: promptOnly ? null : profile.prompt_b_id,
    model_config_id: profile.model_config_id,
    rubric_version: profile.rubric_version,
    dimension_schema_key: schema?.schema_key ?? profile.dimension_schema_key,
    dimension_schema_version: schema?.version ?? profile.dimension_schema_version,
    automation_config: profile.automation_config,
  }
}

export function DimensionManagerPage() {
  const queryClient = useQueryClient()
  const categories = useQuery({
    queryKey: ["evaluation-categories"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
  })
  const [selectedCategoryKey, setSelectedCategoryKey] = useState("")
  const [draft, setDraft] = useState<CategoryDimensionConfig | null>(null)
  const [selectedSchemaId, setSelectedSchemaId] = useState<number | null>(null)
  const [schemaDraft, setSchemaDraft] = useState<SchemaDraft | null>(null)

  const schemas = useQuery({
    queryKey: ["dimension-schemas"],
    queryFn: () => api<{ items: DimensionSchemaRegistryItem[] }>("/api/dimension-schemas"),
  })
  const schemaSummaries = schemas.data?.items ?? []
  const selectedSchemaSummary = schemaSummaries.find((item) => item.id === selectedSchemaId)
  const selectedSchema = useQuery({
    queryKey: ["dimension-schema-id", selectedSchemaSummary?.schema_key, selectedSchemaSummary?.version],
    queryFn: () => api<DimensionSchemaRegistryItem>(
      `/api/dimension-schemas/${encodeURIComponent(selectedSchemaSummary?.schema_key ?? "")}/versions/${encodeURIComponent(selectedSchemaSummary?.version ?? "")}`,
    ),
    enabled: Boolean(selectedSchemaSummary),
  })

  const orderedCategories = useMemo(
    () => [...(categories.data?.items ?? [])].sort((left, right) => {
      const statusOrder = { active: 0, draft: 1, retired: 2 }
      return statusOrder[left.status] - statusOrder[right.status]
        || left.display_name.localeCompare(right.display_name, "zh-CN")
    }),
    [categories.data?.items],
  )

  useEffect(() => {
    if (orderedCategories.some((item) => item.category_key === selectedCategoryKey)) return
    setSelectedCategoryKey(orderedCategories[0]?.category_key ?? "")
  }, [orderedCategories, selectedCategoryKey])

  const selectedCategory = orderedCategories.find(
    (item) => item.category_key === selectedCategoryKey,
  )

  useEffect(() => {
    if (selectedSchemaId && schemaSummaries.some((item) => item.id === selectedSchemaId)) return
    const bound = schemaSummaries.find((item) => (
      item.schema_key === selectedCategory?.dimension_schema_key
      && item.version === selectedCategory.dimension_schema_version
    ))
    setSelectedSchemaId(bound?.id ?? schemaSummaries[0]?.id ?? null)
  }, [schemaSummaries, selectedCategory?.dimension_schema_key, selectedCategory?.dimension_schema_version, selectedSchemaId])

  useEffect(() => {
    setDraft(selectedCategory
      ? copyDimensionConfig(selectedCategory.pipeline_config.dimensions)
      : null)
  }, [selectedCategory])

  useEffect(() => {
    if (!selectedSchema.data) return
    setSchemaDraft(
      selectedSchema.data.status === "draft" || selectedSchema.data.status === "candidate"
        ? editableSchemaDraft(selectedSchema.data)
        : null,
    )
  }, [selectedSchema.data])

  const boundSchema = useQuery({
    queryKey: [
      "dimension-schema",
      selectedCategory?.dimension_schema_key,
      selectedCategory?.dimension_schema_version,
    ],
    queryFn: () => api<DimensionSchemaRegistryItem>(
      `/api/dimension-schemas/${encodeURIComponent(selectedCategory?.dimension_schema_key ?? "")}/versions/${encodeURIComponent(selectedCategory?.dimension_schema_version ?? "")}`,
    ),
    enabled: Boolean(
      selectedCategory?.dimension_schema_key && selectedCategory.dimension_schema_version,
    ),
  })

  const dimensions = useMemo(
    () => [...(boundSchema.data?.definition?.dimensions ?? [])].sort(
      (left, right) => (left.display_order ?? 999) - (right.display_order ?? 999),
    ),
    [boundSchema.data?.definition?.dimensions],
  )
  const allowedKeys = useMemo(
    () => new Set(dimensions.map((dimension) => dimension.key)),
    [dimensions],
  )
  const selectedKeys = useMemo(
    () => new Set((draft?.selected_keys ?? draft?.enabled_keys ?? []).filter((key) => allowedKeys.has(key))),
    [allowedKeys, draft?.enabled_keys, draft?.selected_keys],
  )
  const staleKeys = useMemo(
    () => (draft?.selected_keys ?? draft?.enabled_keys ?? []).filter((key) => !allowedKeys.has(key)),
    [allowedKeys, draft?.enabled_keys, draft?.selected_keys],
  )
  const normalizedDraft = draft
    ? canonicalDimensionConfig(draft, allowedKeys)
    : null
  const savedConfig = selectedCategory
    ? copyDimensionConfig(selectedCategory.pipeline_config.dimensions)
    : null
  const canonicalSavedConfig = savedConfig
    ? canonicalDimensionConfig(savedConfig, allowedKeys)
    : null
  const hasChanges = Boolean(
    normalizedDraft
    && canonicalSavedConfig
    && JSON.stringify(normalizedDraft) !== JSON.stringify(canonicalSavedConfig),
  )
  const canSave = Boolean(
    selectedCategory
    && normalizedDraft
    && hasChanges
    && (!normalizedDraft.enabled || (
      dimensions.length > 0
      && (normalizedDraft.mode === "all" || normalizedDraft.enabled_keys.length > 0)
    )),
  )

  const saveCategoryDimensions = useMutation({
    mutationFn: () => {
      if (!selectedCategory || !normalizedDraft) throw new Error("请先选择评测类目")
      return api<EvaluationCategoryProfile>(
        `/api/evaluation-categories/${encodeURIComponent(selectedCategory.category_key)}`,
        {
          method: "PUT",
          ...jsonBody(categoryUpdatePayload(selectedCategory, normalizedDraft)),
        },
      )
    },
    onSuccess: async (profile) => {
      setDraft(copyDimensionConfig(profile.pipeline_config.dimensions))
      await queryClient.invalidateQueries({ queryKey: ["evaluation-categories"] })
      toast.success(`${profile.display_name}的维度方案已保存`)
    },
    onError: (error) => toast.error(error.message),
  })

  const bindSchema = useMutation({
    mutationFn: (schema: DimensionSchemaRegistryItem) => {
      if (!selectedCategory) throw new Error("请先选择评测类目")
      if (schema.status !== "published") throw new Error("类目只能绑定已发布的维度版本")
      const keys = new Set(schema.definition?.dimensions.map((item) => item.key) ?? [])
      const nextDimensions = canonicalDimensionConfig(
        draft ?? selectedCategory.pipeline_config.dimensions,
        keys,
      )
      return api<EvaluationCategoryProfile>(
        `/api/evaluation-categories/${encodeURIComponent(selectedCategory.category_key)}`,
        { method: "PUT", ...jsonBody(categoryUpdatePayload(selectedCategory, nextDimensions, schema)) },
      )
    },
    onSuccess: async (profile) => {
      await queryClient.invalidateQueries({ queryKey: ["evaluation-categories"] })
      toast.success(`${profile.display_name}已绑定新维度版本`)
    },
    onError: (error) => toast.error(error.message),
  })

  const saveSchemaDraft = useMutation({
    mutationFn: () => {
      if (!schemaDraft) throw new Error("请先创建或选择草稿")
      const normalized = synchronizeDefinition(schemaDraft)
      const payload = {
        display_name: normalized.display_name,
        definition: normalized.definition,
        parent_schema_id: normalized.parent_schema_id,
        core_schema_id: null,
      }
      if (normalized.id) {
        return api<DimensionSchemaRegistryItem>(`/api/dimension-schemas/${normalized.id}`, {
          method: "PUT",
          ...jsonBody(payload),
        })
      }
      return api<DimensionSchemaRegistryItem>("/api/dimension-schemas", {
        method: "POST",
        ...jsonBody({
          schema_key: normalized.schema_key,
          version: normalized.version,
          schema_type: "family_pack",
          family_key: normalized.family_key,
          ...payload,
        }),
      })
    },
    onSuccess: async (schema) => {
      setSchemaDraft(editableSchemaDraft(schema))
      setSelectedSchemaId(schema.id)
      await queryClient.invalidateQueries({ queryKey: ["dimension-schemas"] })
      await queryClient.invalidateQueries({ queryKey: ["dimension-schema-id"] })
      toast.success(`${schema.display_name}草稿已保存`)
    },
    onError: (error) => toast.error(error.message),
  })

  const publishSchema = useMutation({
    mutationFn: async () => {
      if (!schemaDraft?.id) throw new Error("请先保存草稿")
      const normalized = synchronizeDefinition(schemaDraft)
      await api<DimensionSchemaRegistryItem>(`/api/dimension-schemas/${schemaDraft.id}`, {
        method: "PUT",
        ...jsonBody({
          display_name: normalized.display_name,
          definition: normalized.definition,
          parent_schema_id: normalized.parent_schema_id,
          core_schema_id: null,
        }),
      })
      return api<DimensionSchemaRegistryItem>(`/api/dimension-schemas/${schemaDraft.id}/publish`, { method: "POST" })
    },
    onSuccess: async (schema) => {
      setSchemaDraft(null)
      await queryClient.invalidateQueries({ queryKey: ["dimension-schemas"] })
      await queryClient.invalidateQueries({ queryKey: ["dimension-schema-id"] })
      toast.success(`${schema.display_name}已发布，可绑定到类目`)
    },
    onError: (error) => toast.error(error.message),
  })

  const deleteSchema = useMutation({
    mutationFn: (id: number) => api<{ ok: boolean }>(`/api/dimension-schemas/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      setSchemaDraft(null)
      setSelectedSchemaId(null)
      await queryClient.invalidateQueries({ queryKey: ["dimension-schemas"] })
      toast.success("维度草稿已删除")
    },
    onError: (error) => toast.error(error.message),
  })

  function updateDraft(patch: Partial<CategoryDimensionConfig>) {
    setDraft((current) => current ? { ...current, ...patch } : current)
  }

  function toggleDimension(key: string) {
    if (!draft || !allowedKeys.has(key)) return
    updateDraft({
      enabled_keys: selectedKeys.has(key)
        ? draft.enabled_keys.filter((item) => item !== key)
        : [...draft.enabled_keys.filter((item) => allowedKeys.has(item)), key],
      selected_keys: selectedKeys.has(key)
        ? draft.enabled_keys.filter((item) => item !== key)
        : [...draft.enabled_keys.filter((item) => allowedKeys.has(item)), key],
    })
  }

  return (
    <>
      <PageHeader
        index="03.5"
        title="维度管理器"
        description="按评测类目开启、关闭或缩减维度范围。可关闭维度测评，对照仅提示词评测的质量表现。"
      />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <div className="flex items-start gap-3 border-y border-[#d4a53d] bg-[#fff9e9] px-4 py-3 text-sm leading-6 text-[#665016]">
          <WarningCircle className="mt-0.5 shrink-0" size={18} weight="fill" />
          <p>
            保存后只影响新建任务。历史结果、正在执行的任务与已经进入队列的任务继续使用各自的冻结快照，不会被当前配置回写。
          </p>
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="border-y border-[var(--line-strong)] bg-white">
            <div className="border-b border-[var(--line)] px-4 py-4">
              <h2 className="font-editorial text-xl font-bold">评测类目</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">每个类目独立保存维度方案</p>
            </div>
            <div className="divide-y divide-[var(--line)]">
              {categories.isLoading ? (
                <div className="h-64 animate-pulse bg-[#fafbf8]" />
              ) : categories.isError ? (
                <p className="px-4 py-8 text-sm text-[#8d2924]">
                  类目加载失败：{categories.error.message}
                </p>
              ) : orderedCategories.length ? orderedCategories.map((category) => (
                <button
                  type="button"
                  key={category.category_key}
                  className={cn(
                    "w-full px-4 py-4 text-left transition-colors hover:bg-[#f6f8f3]",
                    category.category_key === selectedCategoryKey && "bg-[#eef4e8]",
                  )}
                  onClick={() => setSelectedCategoryKey(category.category_key)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold">{category.display_name}</span>
                    <Badge tone={category.status === "active" ? "success" : category.status === "draft" ? "warning" : "neutral"}>
                      {categoryStatusNames[category.status]}
                    </Badge>
                  </div>
                  <p className="mt-2 text-xs font-semibold text-[var(--muted)]">
                    {dimensionStatus(category)}
                  </p>
                  <p className="font-data mt-1 text-[11px] text-[var(--muted)]">
                    {category.category_key} · R{category.pipeline_revision}
                  </p>
                </button>
              )) : (
                <p className="px-4 py-8 text-sm text-[var(--muted)]">还没有可管理的评测类目。</p>
              )}
            </div>
          </aside>

          <main className="min-w-0 space-y-6">
            <section className="border-y border-[var(--line-strong)] bg-white">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-5">
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-[var(--muted)]">当前类目方案</p>
                  <h2 className="font-editorial mt-1 text-2xl font-bold">
                    {selectedCategory?.display_name ?? "请选择评测类目"}
                  </h2>
                  {selectedCategory && (
                    <p className="font-data mt-2 break-all text-xs text-[var(--muted)]">
                      {selectedCategory.category_key} · 流水线修订 R{selectedCategory.pipeline_revision} · {new Date(selectedCategory.updated_at).toLocaleString("zh-CN")}
                    </p>
                  )}
                </div>
                {selectedCategory && draft && (
                  <Badge tone={!draft.enabled ? "warning" : "active"}>
                    {!draft.enabled
                      ? "维度已关闭"
                      : draft.mode === "all"
                        ? `全部 ${dimensions.length} 维`
                        : `已选 ${selectedKeys.size} / ${dimensions.length} 维`}
                  </Badge>
                )}
              </div>

              {!selectedCategory || !draft ? (
                <div className="h-72 animate-pulse bg-[#fafbf8]" />
              ) : (
                <div>
                  <div className="grid gap-5 border-b border-[var(--line)] px-5 py-5 xl:grid-cols-[220px_minmax(0,1fr)]">
                    <div>
                      <h3 className="text-sm font-bold">是否评测维度</h3>
                      <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
                        关闭后用于仅提示词质量实验；开启后再选择全部或部分维度。
                      </p>
                    </div>
                    <div>
                      <div className="inline-flex max-w-full overflow-auto rounded-[4px] border border-[var(--line-strong)] bg-white p-1">
                        <button
                          type="button"
                          className={cn(
                            "h-10 whitespace-nowrap rounded-[3px] px-4 text-xs font-semibold",
                            draft.enabled ? "bg-[#11130f] text-white" : "text-[var(--muted)] hover:bg-[#f1f3ef]",
                          )}
                          onClick={() => updateDraft({ enabled: true, mode: "all" })}
                        >
                          启用维度测评
                        </button>
                        <button
                          type="button"
                          className={cn(
                            "h-10 whitespace-nowrap rounded-[3px] px-4 text-xs font-semibold",
                            !draft.enabled ? "bg-[#11130f] text-white" : "text-[var(--muted)] hover:bg-[#f1f3ef]",
                          )}
                          onClick={() => updateDraft({ enabled: false, mode: "none", enabled_keys: [], selected_keys: [] })}
                        >
                          关闭，仅提示词
                        </button>
                      </div>
                      {!draft.enabled && (
                        <p className="mt-4 border-l-2 border-primary bg-[#f7fadf] px-4 py-3 text-sm leading-6">
                          新任务将跳过该类目的维度测评，用于观察仅依赖提示词时的质量表现。历史任务的维度和评分仍按冻结快照展示。
                        </p>
                      )}
                    </div>
                  </div>

                  {draft.enabled && (
                    <>
                      <div className="grid gap-5 border-b border-[var(--line)] px-5 py-5 xl:grid-cols-[220px_minmax(0,1fr)]">
                        <div>
                          <h3 className="text-sm font-bold">评测范围</h3>
                          <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
                            可使用绑定规则的全部维度，或只保留本类目需要的维度。
                          </p>
                        </div>
                        <div className="inline-flex w-fit max-w-full overflow-auto rounded-[4px] border border-[var(--line-strong)] bg-white p-1">
                          <button
                            type="button"
                            className={cn(
                              "h-10 whitespace-nowrap rounded-[3px] px-4 text-xs font-semibold",
                              draft.mode === "all" ? "bg-[#11130f] text-white" : "text-[var(--muted)] hover:bg-[#f1f3ef]",
                            )}
                            onClick={() => updateDraft({ mode: "all" })}
                          >
                            全部维度
                          </button>
                          <button
                            type="button"
                            className={cn(
                              "h-10 whitespace-nowrap rounded-[3px] px-4 text-xs font-semibold",
                              draft.mode === "selected" ? "bg-[#11130f] text-white" : "text-[var(--muted)] hover:bg-[#f1f3ef]",
                            )}
                            onClick={() => updateDraft({ mode: "selected" })}
                          >
                            选择部分维度
                          </button>
                        </div>
                      </div>

                      <div className="px-5 py-5">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <h3 className="text-sm font-bold">绑定规则允许的维度</h3>
                            <p className="font-data mt-1 text-xs text-[var(--muted)]">
                              {selectedCategory.dimension_schema_key && selectedCategory.dimension_schema_version
                                ? `${selectedCategory.dimension_schema_key} · ${selectedCategory.dimension_schema_version}`
                                : "当前类目未绑定维度规则"}
                            </p>
                          </div>
                          {draft.mode === "selected" && dimensions.length > 0 && (
                            <div className="flex flex-wrap gap-2">
                              <Button
                                type="button"
                                size="sm"
                                variant="secondary"
                                onClick={() => updateDraft({ enabled_keys: dimensions.map((item) => item.key), selected_keys: dimensions.map((item) => item.key) })}
                              >
                                全部加入
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="ghost"
                                onClick={() => updateDraft({ enabled_keys: [], selected_keys: [] })}
                              >
                                清空已选
                              </Button>
                            </div>
                          )}
                        </div>

                        {boundSchema.isLoading ? (
                          <div className="mt-4 h-48 animate-pulse bg-[#fafbf8]" />
                        ) : boundSchema.isError ? (
                          <p className="mt-4 border-y border-[#e8c1bd] bg-[#fff0ee] px-4 py-3 text-sm text-[#8d2924]">
                            绑定规则加载失败：{boundSchema.error.message}
                          </p>
                        ) : !selectedCategory.dimension_schema_key || !dimensions.length ? (
                          <p className="mt-4 border-y border-[#e8c876] bg-[#fff9e9] px-4 py-3 text-sm leading-6 text-[#7d4308]">
                            当前类目没有可解析的绑定规则。请先在类目设置中绑定维度 Schema；在此之前不能开启维度测评。
                          </p>
                        ) : (
                          <div className="mt-4 divide-y divide-[var(--line)] border-y border-[var(--line)]">
                            {dimensions.map((dimension, index) => {
                              const selected = draft.mode === "all" || selectedKeys.has(dimension.key)
                              return (
                                <div
                                  key={dimension.key}
                                  className="grid gap-3 py-3 sm:grid-cols-[36px_minmax(0,1fr)_auto] sm:items-center"
                                >
                                  <span className="font-data text-xs text-[var(--muted)]">{String(index + 1).padStart(2, "0")}</span>
                                  <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <p className="text-sm font-semibold">{dimension.label}</p>
                                      <Badge tone={selected ? "active" : "neutral"}>{selected ? "已纳入" : "未纳入"}</Badge>
                                    </div>
                                    <p className="font-data mt-1 break-all text-xs text-[var(--muted)]">{dimension.key}</p>
                                  </div>
                                  {draft.mode === "selected" ? (
                                    <Button
                                      type="button"
                                      size="sm"
                                      variant="secondary"
                                      onClick={() => toggleDimension(dimension.key)}
                                      aria-pressed={selectedKeys.has(dimension.key)}
                                    >
                                      {selectedKeys.has(dimension.key) ? <Minus /> : <Plus />}
                                      {selectedKeys.has(dimension.key) ? "移除" : "加入"}
                                    </Button>
                                  ) : (
                                    <span className="flex items-center gap-1 text-xs font-semibold text-[#4f5e13]"><Check />自动纳入</span>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}

                        {draft.mode === "selected" && selectedKeys.size === 0 && (
                          <p className="mt-3 text-xs font-semibold text-[#8d2924]">选择部分维度时，至少加入一个维度后才能保存。</p>
                        )}
                        {staleKeys.length > 0 && (
                          <p className="mt-3 text-xs leading-5 text-[#7d4308]">
                            旧配置中有 {staleKeys.length} 个维度不属于当前绑定规则（{staleKeys.join("、")}），保存时会自动清理。
                          </p>
                        )}
                      </div>
                    </>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-4">
                    <p className="text-xs leading-5 text-[var(--muted)]">
                      {hasChanges ? "有未保存修改；保存后流水线修订号会更新。" : "当前页面与已保存方案一致。"}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={!hasChanges || saveCategoryDimensions.isPending}
                        onClick={() => selectedCategory && setDraft(copyDimensionConfig(selectedCategory.pipeline_config.dimensions))}
                      >
                        撤销修改
                      </Button>
                      <Button
                        type="button"
                        disabled={!canSave || saveCategoryDimensions.isPending}
                        onClick={() => saveCategoryDimensions.mutate()}
                      >
                        <FloppyDisk />
                        {saveCategoryDimensions.isPending ? "正在保存" : "保存维度方案"}
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </section>

            <SchemaVersionManager
              schemas={schemaSummaries}
              selectedSchemaId={selectedSchemaId}
              onSelectSchema={setSelectedSchemaId}
              selectedSchema={selectedSchema.data}
              schemaDraft={schemaDraft}
              setSchemaDraft={setSchemaDraft}
              selectedCategory={selectedCategory}
              onBind={(schema) => bindSchema.mutate(schema)}
              onSave={() => saveSchemaDraft.mutate()}
              onPublish={() => publishSchema.mutate()}
              onDelete={(id) => deleteSchema.mutate(id)}
              busy={bindSchema.isPending || saveSchemaDraft.isPending || publishSchema.isPending || deleteSchema.isPending}
            />
          </main>
        </div>
      </div>
    </>
  )
}

function SchemaVersionManager({
  schemas,
  selectedSchemaId,
  onSelectSchema,
  selectedSchema,
  schemaDraft,
  setSchemaDraft,
  selectedCategory,
  onBind,
  onSave,
  onPublish,
  onDelete,
  busy,
}: {
  schemas: DimensionSchemaRegistryItem[]
  selectedSchemaId: number | null
  onSelectSchema: (id: number) => void
  selectedSchema?: DimensionSchemaRegistryItem
  schemaDraft: SchemaDraft | null
  setSchemaDraft: (value: SchemaDraft | null) => void
  selectedCategory?: EvaluationCategoryProfile
  onBind: (schema: DimensionSchemaRegistryItem) => void
  onSave: () => void
  onPublish: () => void
  onDelete: (id: number) => void
  busy: boolean
}) {
  const dimensions = schemaDraft?.definition.dimensions ?? selectedSchema?.definition?.dimensions ?? []
  const weightSum = dimensions.reduce((sum, item) => sum + (Number(item.weight) || 0), 0)
  const isBound = Boolean(
    selectedCategory && selectedSchema
    && selectedCategory.dimension_schema_key === selectedSchema.schema_key
    && selectedCategory.dimension_schema_version === selectedSchema.version,
  )

  function beginCopy() {
    if (!selectedSchema) return
    const next = editableSchemaDraft(selectedSchema)
    if (!next) return
    next.id = null
    next.parent_schema_id = selectedSchema.id
    next.version = `${selectedSchema.version}-draft-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}`
    next.display_name = `${selectedSchema.display_name} 新版本`
    next.definition.package_version = next.version
    setSchemaDraft(next)
  }

  function updateSchema(patch: Partial<SchemaDraft>) {
    setSchemaDraft(schemaDraft ? { ...schemaDraft, ...patch } : schemaDraft)
  }

  function updateDimension(index: number, patch: Partial<DimensionDefinition>) {
    if (!schemaDraft) return
    const definition = cloneDefinition(schemaDraft.definition)
    definition.dimensions[index] = { ...definition.dimensions[index], ...patch }
    updateSchema({ definition })
  }

  function moveDimension(index: number, direction: -1 | 1) {
    if (!schemaDraft) return
    const target = index + direction
    if (target < 0 || target >= schemaDraft.definition.dimensions.length) return
    const definition = cloneDefinition(schemaDraft.definition)
    const [moved] = definition.dimensions.splice(index, 1)
    definition.dimensions.splice(target, 0, moved)
    updateSchema({ definition })
  }

  function removeDimension(index: number) {
    if (!schemaDraft || schemaDraft.definition.dimensions.length <= 1) return
    const definition = cloneDefinition(schemaDraft.definition)
    definition.dimensions.splice(index, 1)
    updateSchema({ definition })
  }

  function addDimension() {
    if (!schemaDraft) return
    const definition = cloneDefinition(schemaDraft.definition)
    const template = definition.dimensions[0]
    const suffix = definition.dimensions.length + 1
    definition.dimensions.push({
      ...cloneDimension(template),
      key: `new_dimension_${suffix}`,
      label: `新维度 ${suffix}`,
      weight: 0,
      display_order: suffix,
      anchors: { "1": "明显不合格", "3": "普通可用", "5": "代表性优秀" },
    })
    updateSchema({ definition })
  }

  return (
    <section className="border-y border-[var(--line-strong)] bg-white">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-5">
        <div>
          <p className="text-xs font-semibold text-[var(--muted)]">维度方案版本</p>
          <h2 className="font-editorial mt-1 text-xl font-bold">定义、发布并绑定评测维度</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          {selectedSchema?.status === "published" && (
            <Button type="button" variant="secondary" onClick={beginCopy} disabled={busy}>
              <Copy />复制为新草稿
            </Button>
          )}
          {selectedSchema?.status === "published" && selectedCategory && (
            <Button type="button" onClick={() => onBind(selectedSchema)} disabled={busy || isBound}>
              <Check />{isBound ? "当前已绑定" : `绑定到${selectedCategory.display_name}`}
            </Button>
          )}
        </div>
      </div>

      <div className="grid border-b border-[var(--line)] lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className="border-b border-[var(--line)] lg:border-b-0 lg:border-r">
          <div className="px-4 py-3 text-xs font-semibold text-[var(--muted)]">全部版本</div>
          <div className="max-h-[360px] divide-y divide-[var(--line)] overflow-auto">
            {schemas.map((schema) => (
              <button
                type="button"
                key={schema.id}
                className={cn("w-full px-4 py-3 text-left hover:bg-[#f6f8f3]", selectedSchemaId === schema.id && "bg-[#eef4e8]")}
                onClick={() => onSelectSchema(schema.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold">{schema.display_name}</span>
                  <Badge tone={schema.status === "published" ? "active" : "warning"}>{schemaStatusNames[schema.status]}</Badge>
                </div>
                <p className="font-data mt-1 break-all text-[11px] text-[var(--muted)]">{schema.schema_key} · {schema.version}</p>
              </button>
            ))}
          </div>
        </div>
        <div className="min-w-0 px-5 py-5">
          {schemaDraft ? (
            <div className="grid gap-4 md:grid-cols-2">
              <label className="text-xs font-semibold">方案名称<Input className="mt-2" value={schemaDraft.display_name} onChange={(event) => updateSchema({ display_name: event.target.value })} /></label>
              <label className="text-xs font-semibold">版本号<Input className="font-data mt-2" value={schemaDraft.version} disabled={schemaDraft.id !== null} onChange={(event) => updateSchema({ version: event.target.value })} /></label>
              <label className="text-xs font-semibold">方案标识<Input className="font-data mt-2" value={schemaDraft.schema_key} disabled={schemaDraft.id !== null} onChange={(event) => updateSchema({ schema_key: event.target.value })} /></label>
              <div className="flex items-end justify-between gap-3 border-b border-[var(--line)] pb-2 text-xs">
                <span className="font-semibold">当前权重合计</span>
                <span className={cn("font-data font-bold", Math.abs(weightSum - 1) < 0.0001 ? "text-[#4f5e13]" : "text-[#8d2924]")}>{(weightSum * 100).toFixed(1)}%</span>
              </div>
            </div>
          ) : selectedSchema ? (
            <div>
              <div className="flex min-w-0 flex-wrap items-center gap-2"><Badge tone="active">已发布，只读</Badge><span className="font-data min-w-0 break-all text-xs text-[var(--muted)]">{selectedSchema.canonical_hash}</span></div>
              <p className="mt-3 text-sm leading-6 text-[var(--muted)]">已发布版本不能原地修改。需要增删维度时，请复制为新草稿，编辑完成后发布，再绑定到目标类目。</p>
            </div>
          ) : <p className="text-sm text-[var(--muted)]">请选择一个维度方案版本。</p>}
        </div>
      </div>

      {dimensions.length > 0 && (
        <div className="divide-y divide-[var(--line)]">
          {dimensions.map((dimension, index) => (
            <div key={`${dimension.key}-${index}`} className="grid gap-4 px-5 py-4 xl:grid-cols-[70px_180px_190px_100px_minmax(260px,1fr)_auto] xl:items-start">
              <div className="flex items-center gap-1">
                <span className="font-data w-6 text-xs text-[var(--muted)]">{String(index + 1).padStart(2, "0")}</span>
                {schemaDraft && <><button type="button" title="上移" onClick={() => moveDimension(index, -1)} disabled={index === 0}><ArrowUp /></button><button type="button" title="下移" onClick={() => moveDimension(index, 1)} disabled={index === dimensions.length - 1}><ArrowDown /></button></>}
              </div>
              {schemaDraft ? <Input value={dimension.label} onChange={(event) => updateDimension(index, { label: event.target.value })} /> : <p className="text-sm font-semibold">{dimension.label}</p>}
              {schemaDraft ? <Input className="font-data" value={dimension.key} onChange={(event) => updateDimension(index, { key: event.target.value })} /> : <p className="font-data break-all text-xs text-[var(--muted)]">{dimension.key}</p>}
              {schemaDraft ? <Input type="number" min="0.001" max="1" step="0.01" value={dimension.weight ?? 0} onChange={(event) => updateDimension(index, { weight: Number(event.target.value) })} /> : <p className="font-data text-sm">{typeof dimension.weight === "number" ? `${(dimension.weight * 100).toFixed(0)}%` : "-"}</p>}
              {schemaDraft ? <Textarea className="min-h-[72px]" value={dimension.anchors?.["3"] ?? ""} onChange={(event) => updateDimension(index, { anchors: { ...dimension.anchors, "3": event.target.value } })} /> : <p className="text-xs leading-5 text-[var(--muted)]">{dimension.anchors?.["3"] ?? "未声明 3 级锚点"}</p>}
              {schemaDraft && <Button type="button" size="sm" variant="ghost" title="删除维度" onClick={() => removeDimension(index)} disabled={dimensions.length <= 1}><Trash /></Button>}
            </div>
          ))}
        </div>
      )}

      {schemaDraft && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-4">
          <Button type="button" variant="secondary" onClick={addDimension}><Plus />新增维度</Button>
          <div className="flex flex-wrap gap-2">
            {schemaDraft.id && <Button type="button" variant="ghost" onClick={() => onDelete(schemaDraft.id!)} disabled={busy}><Trash />删除草稿</Button>}
            <Button type="button" variant="secondary" onClick={onSave} disabled={busy || !schemaDraft.display_name.trim() || !schemaDraft.version.trim() || dimensions.length === 0 || Math.abs(weightSum - 1) > 0.0001}><FloppyDisk />保存草稿</Button>
            <Button type="button" onClick={onPublish} disabled={busy || !schemaDraft.id || Math.abs(weightSum - 1) > 0.0001}><RocketLaunch />发布版本</Button>
          </div>
        </div>
      )}

      {selectedSchema?.definition?.release_gate && !schemaDraft && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-4 text-xs">
          <span className="flex items-center gap-2 font-semibold"><LockKey />发布门禁</span>
          <span className="text-[var(--muted)]">人工校准 {selectedSchema.definition.release_gate.completed_calibration_samples ?? 0} / {selectedSchema.definition.release_gate.minimum_calibration_samples ?? 0}</span>
        </div>
      )}
    </section>
  )
}
