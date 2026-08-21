import { useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowClockwise,
  Check,
  CheckSquare,
  CloudArrowUp,
  PencilSimple,
  Play,
  Square,
  WarningCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { StatusSummaryStrip } from "@/components/workspace-page"
import { SemanticQualityDrawer } from "@/components/semantic-quality-drawer"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  ImageLightbox,
  ImagePreviewButton,
  type ImagePreview,
} from "@/components/image-lightbox"
import { api, baselineRegressionApi } from "@/lib/api"
import { submitReviewDecision } from "@/lib/review-submit"
import type {
  Asset,
  BaselineLevel,
  BaselineCorrectionRun,
  BaselineFieldMetrics,
  BaselineRegressionItem,
  BaselineRegressionRun,
  BaselineSemanticQualityMetrics,
  BaselineV3Revision,
  EvaluationCategoryProfile,
  MaterialPackage,
  PromptVersion,
  ReviewCorrection,
  User,
} from "@/lib/types"
import { ReviewCorrectionForm } from "@/pages/review-correction-form"
import {
  baselineAcceptanceProgressFromPages,
  buildBaselineRunPayload,
  isSelectableV3Candidate,
  baselineRunContextPatch,
  baselineRunIdAfterSetLoad,
  resolveCandidatePromptBinding,
  resolveV3PromptBinding,
  v3RevisionGroup,
} from "@/features/baseline-regression/baseline-regression-contract"
import type { CandidatePromptBindingResolution } from "@/features/baseline-regression/baseline-regression-contract"
import { BaselineSetDialog } from "@/features/baseline-regression/baseline-set-dialog"
import { CorrectionWorkbench } from "@/features/baseline-regression/correction-workbench"
import {
  correctionDraftFromView,
  correctionSubmissionPayload,
  mergeCorrectionResponse,
  updateCorrectionDraft,
} from "@/features/correction-contract/correction-view-state"
import type { CorrectionDraft, CorrectionView } from "@/features/correction-contract/types"
import {
  nextPendingCorrectionId,
  previousCorrectionId,
} from "@/features/baseline-regression/correction-navigation"
import { candidateRefreshPlan } from "@/features/correction-contract/candidate-refresh"
import { LevelPerformanceSummary } from "@/features/baseline-regression/level-performance-summary"
import { correctionLevelDisplay } from "@/features/baseline-regression/correction-level-display"
import { MetricsDrawer } from "@/features/baseline-regression/metrics-drawer"
import { RunConfigDrawer } from "@/features/baseline-regression/run-config-drawer"
import { RunHistoryDrawer } from "@/features/baseline-regression/run-history-drawer"

const levels: BaselineLevel[] = ["L1", "L2", "L3", "L4", "L5"]
const levelNames: Record<BaselineLevel, string> = {
  L1: "好",
  L2: "中等",
  L3: "中差",
  L4: "极差",
  L5: "过滤",
}

const ASSET_PAGE_SIZE = 200
const RUN_PAGE_SIZE = 200
const ACCEPTANCE_PAGE_SIZE = 1000

function correctionIdempotencyKey(runId: number, itemId: number): string {
  const random = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `baseline-contract:${runId}:${itemId}:${random}`
}

export function BaselineRegressionPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [selectedCategoryKey, setSelectedCategoryKey] = useState("space_image")
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<number>>(new Set())
  const [expectedByAsset, setExpectedByAsset] = useState<Record<number, BaselineLevel>>({})
  const [defaultLevel, setDefaultLevel] = useState<BaselineLevel>("L1")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [selectedPackageId, setSelectedPackageId] = useState(0)
  const [useWholePackage, setUseWholePackage] = useState(false)
  const [selectedSetId, setSelectedSetId] = useState(0)
  const [selectedRunId, setSelectedRunId] = useState(0)
  const [promptSelectionMode, setPromptSelectionMode] = useState<"published" | "manual" | "single">("published")
  const [selectedPromptAId, setSelectedPromptAId] = useState(0)
  const [selectedPromptBId, setSelectedPromptBId] = useState(0)
  const [v3SelectionMode, setV3SelectionMode] = useState<"active" | "candidate">("active")
  const [selectedV3RevisionId, setSelectedV3RevisionId] = useState(0)
  const [executionMode, setExecutionMode] = useState<"freeform" | "structured">("freeform")
  const [assetPage, setAssetPage] = useState(0)
  const [runPage, setRunPage] = useState(0)
  const [imagePreview, setImagePreview] = useState<ImagePreview | null>(null)
  const [baselineSetDialogOpen, setBaselineSetDialogOpen] = useState(false)
  const [runConfigDrawerOpen, setRunConfigDrawerOpen] = useState(false)
  const [metricsDrawerOpen, setMetricsDrawerOpen] = useState(false)
  const [semanticQualityDrawerOpen, setSemanticQualityDrawerOpen] = useState(false)
  const [runHistoryDrawerOpen, setRunHistoryDrawerOpen] = useState(false)

  const categories = useQuery({
    queryKey: ["evaluation-categories"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
  })

  const assets = useQuery({
    queryKey: ["baseline-assets", selectedCategoryKey, selectedPackageId, assetPage],
    queryFn: () => baselineRegressionApi.listAssets(
      selectedPackageId || undefined,
      selectedCategoryKey,
      assetPage * ASSET_PAGE_SIZE,
      ASSET_PAGE_SIZE,
    ),
  })
  const packages = useQuery({
    queryKey: ["baseline-packages", selectedCategoryKey],
    queryFn: () => baselineRegressionApi.listPackages(selectedCategoryKey),
  })
  const prompts = useQuery({
    queryKey: ["prompts", selectedCategoryKey],
    queryFn: () => baselineRegressionApi.listPrompts(selectedCategoryKey),
  })
  const v3Revisions = useQuery({
    queryKey: ["baseline-v3-revisions", selectedCategoryKey],
    queryFn: () => baselineRegressionApi.listV3Revisions(selectedCategoryKey),
    enabled: Boolean(selectedCategoryKey),
  })
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/auth/me"),
  })
  const baselineSets = useQuery({
    queryKey: ["baseline-sets", selectedCategoryKey],
    queryFn: () => baselineRegressionApi.listSets(selectedCategoryKey),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.latest_run?.status === "running")
        ? 3000
        : false,
  })
  const selectedSet = useQuery({
    queryKey: ["baseline-set", selectedSetId],
    queryFn: () => baselineRegressionApi.getSet(selectedSetId, false),
    enabled: selectedSetId > 0,
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) => run.status === "running") ? 3000 : false,
  })
  const runDetail = useQuery({
    queryKey: ["baseline-regression", selectedRunId, runPage],
    queryFn: () => baselineRegressionApi.getRun(
      selectedRunId,
      runPage * RUN_PAGE_SIZE,
      RUN_PAGE_SIZE,
    ),
    enabled: selectedRunId > 0,
    refetchInterval: (query) =>
      query.state.data?.summary.status === "running" ? 3000 : false,
  })
  const fieldMetrics = useQuery({
    queryKey: ["baseline-field-metrics", selectedRunId],
    queryFn: () => baselineRegressionApi.getMetrics(selectedRunId),
    enabled: selectedRunId > 0,
    refetchInterval: runDetail.data?.summary.status === "running" ? 3000 : false,
  })
  const semanticMetrics = useQuery<BaselineSemanticQualityMetrics>({
    queryKey: ["baseline-semantic-metrics", selectedRunId],
    queryFn: () => baselineRegressionApi.getSemanticMetrics(selectedRunId),
    enabled: selectedRunId > 0,
    refetchInterval: runDetail.data?.summary.status === "running" ? 3000 : false,
  })
  const acceptancePages = useQuery({
    queryKey: [
      "baseline-acceptance",
      selectedRunId,
      runDetail.data?.summary.total,
      runDetail.data?.summary.completed,
      runDetail.data?.summary.failed,
    ],
    queryFn: async () => {
      const total = runDetail.data?.summary.total ?? 0
      const pages: BaselineRegressionItem[][] = []
      for (let offset = 0; offset < total; offset += ACCEPTANCE_PAGE_SIZE) {
        const detail = await baselineRegressionApi.getRun(
          selectedRunId,
          offset,
          ACCEPTANCE_PAGE_SIZE,
        )
        pages.push(detail.items)
        if (detail.items.length < ACCEPTANCE_PAGE_SIZE) break
      }
      return pages
    },
    enabled: selectedRunId > 0 && Boolean(runDetail.data?.summary),
    refetchInterval: runDetail.data?.summary.status === "running" ? 3000 : false,
  })

  const promptAOptions = useMemo(
    () => (prompts.data?.items ?? []).filter(
      (prompt: PromptVersion) => prompt.stage === "A"
        && prompt.status !== "archived"
        && (prompt.pipeline_scope === "baseline_regression" || prompt.pipeline_scope === "shared"),
    ),
    [prompts.data?.items],
  )
  const promptBOptions = useMemo(
    () => (prompts.data?.items ?? []).filter(
      (prompt: PromptVersion) => prompt.stage === "B"
        && prompt.status !== "archived"
        && (prompt.pipeline_scope === "baseline_regression" || prompt.pipeline_scope === "shared"),
    ),
    [prompts.data?.items],
  )
  const activeCategories = useMemo(
    () => (categories.data?.items ?? []).filter((category) => category.status === "active"),
    [categories.data?.items],
  )
  const selectedCategory = activeCategories.find(
    (category) => category.category_key === selectedCategoryKey,
  )
  const publishedPromptA = promptAOptions.find(
    (prompt) => prompt.id === selectedCategory?.prompt_a_id,
  )
  const publishedPromptB = promptBOptions.find(
    (prompt) => prompt.id === selectedCategory?.prompt_b_id,
  )
  const activeV3Revision = v3Revisions.data?.items.find(
    (revision) => revision.id === v3Revisions.data?.projected_revision_id
      || revision.status === "active",
  )
  const selectedV3Revision = v3Revisions.data?.items.find(
    (revision) => revision.id === selectedV3RevisionId,
  ) ?? activeV3Revision
  const selectableV3Candidates = (v3Revisions.data?.items ?? []).filter((revision) => (
    isSelectableV3Candidate(
      revision,
      v3Revisions.data?.items ?? [],
      v3Revisions.data?.projected_revision_id ?? 0,
    )
  ))
  const candidatePromptBindingA = v3SelectionMode === "candidate" && selectedV3Revision
    ? resolveV3PromptBinding(selectedV3Revision, "A")
    : null
  const candidatePromptBindingB = v3SelectionMode === "candidate" && selectedV3Revision
    ? resolveV3PromptBinding(selectedV3Revision, "B")
    : null
  const candidatePromptResolutionA = useMemo(
    () => resolveCandidatePromptBinding(
      prompts.data?.items ?? [],
      "A",
      candidatePromptBindingA,
    ),
    [candidatePromptBindingA, prompts.data?.items],
  )
  const candidatePromptResolutionB = useMemo(
    () => resolveCandidatePromptBinding(
      prompts.data?.items ?? [],
      "B",
      candidatePromptBindingB,
    ),
    [candidatePromptBindingB, prompts.data?.items],
  )
  const effectivePromptAId = promptSelectionMode === "published"
    ? publishedPromptA?.id ?? 0
    : promptAOptions.some((prompt) => prompt.id === selectedPromptAId)
      ? selectedPromptAId
      : v3SelectionMode === "candidate"
        ? 0
        : publishedPromptA?.id ?? promptAOptions[0]?.id ?? 0
  const effectivePromptBId = promptSelectionMode === "single"
    ? 0
    : promptSelectionMode === "manual"
      ? promptBOptions.some((prompt) => prompt.id === selectedPromptBId)
        ? selectedPromptBId
        : v3SelectionMode === "candidate"
          ? 0
          : publishedPromptB?.id ?? promptBOptions[0]?.id ?? 0
      : publishedPromptB?.id ?? 0
  const selectedPromptA = promptAOptions.find((prompt) => prompt.id === effectivePromptAId)
  const selectedPromptB = promptBOptions.find((prompt) => prompt.id === effectivePromptBId)
  const candidatePromptUnavailable = v3SelectionMode === "candidate" && Boolean(
    (candidatePromptBindingA && candidatePromptResolutionA.status !== "available")
      || (candidatePromptBindingB && candidatePromptResolutionB.status !== "available"),
  )
  const promptBindingMismatch = v3SelectionMode === "candidate" && Boolean(
    candidatePromptUnavailable
      || (candidatePromptBindingA && selectedPromptA?.version !== candidatePromptBindingA)
      || (candidatePromptBindingB && selectedPromptB?.version !== candidatePromptBindingB),
  )
  const v3SelectionLoading = v3Revisions.isLoading || prompts.isLoading
  const v3SelectionUnavailable = v3Revisions.isError || !selectedV3Revision

  useEffect(() => {
    const activeId = v3Revisions.data?.projected_revision_id ?? 0
    if (!activeId) return
    const requestedCandidateId = Number(searchParams.get("candidate_revision_id") || 0)
    if (requestedCandidateId > 0) {
      if (v3Revisions.data?.items.some((item) => item.id === requestedCandidateId)) {
        setSelectedV3RevisionId(requestedCandidateId)
        setV3SelectionMode("candidate")
      }
      return
    }
    if (!selectedV3RevisionId || !v3Revisions.data?.items.some((item) => item.id === selectedV3RevisionId)) {
      setSelectedV3RevisionId(activeId)
      setV3SelectionMode("active")
    }
  }, [searchParams, selectedCategoryKey, selectedV3RevisionId, v3Revisions.data])

  useEffect(() => {
    if (v3SelectionMode !== "candidate" || !selectedV3Revision) return
    const bindingA = resolveV3PromptBinding(selectedV3Revision, "A")
    const bindingB = resolveV3PromptBinding(selectedV3Revision, "B")
    setSelectedPromptAId(
      bindingA && candidatePromptResolutionA.status === "available"
        ? candidatePromptResolutionA.promptId ?? 0
        : 0,
    )
    if (bindingB) {
      setSelectedPromptBId(
        candidatePromptResolutionB.status === "available"
          ? candidatePromptResolutionB.promptId ?? 0
          : 0,
      )
    }
    setPromptSelectionMode("manual")
  }, [candidatePromptResolutionA, candidatePromptResolutionB, selectedV3Revision, v3SelectionMode])

  useEffect(() => {
    const categoryFromUrl = searchParams.get("category_key")
    if (categoryFromUrl && categoryFromUrl !== selectedCategoryKey) {
      setSelectedCategoryKey(categoryFromUrl)
      setSelectedSetId(0)
      setSelectedRunId(0)
    }
    const candidateFromUrl = Number(searchParams.get("candidate_revision_id") || 0)
    if (candidateFromUrl > 0 && candidateFromUrl !== selectedV3RevisionId) {
      setSelectedV3RevisionId(candidateFromUrl)
      setV3SelectionMode("candidate")
    }
  }, [searchParams, selectedCategoryKey, selectedV3RevisionId])

  useEffect(() => {
    const runFromUrl = Number(searchParams.get("run") || 0)
    if (runFromUrl > 0 && runFromUrl !== selectedRunId) {
      setSelectedRunId(runFromUrl)
    }
  }, [searchParams, selectedRunId])

  useEffect(() => {
    const itemFromUrl = Number(searchParams.get("item") || 0)
    if (searchParams.get("mode") === "correction" && itemFromUrl > 0 && selectedRunId <= 0) {
      const runFromUrl = Number(searchParams.get("run") || 0)
      if (runFromUrl > 0) setSelectedRunId(runFromUrl)
    }
  }, [searchParams, selectedRunId])

  useEffect(() => {
    if (!activeCategories.length) return
    if (!activeCategories.some((category) => category.category_key === selectedCategoryKey)) {
      setSelectedCategoryKey(
        activeCategories.find((category) => category.category_key === "space_image")?.category_key
        ?? activeCategories[0].category_key,
      )
    }
  }, [activeCategories, selectedCategoryKey])

  useEffect(() => {
    setAssetPage(0)
  }, [selectedCategoryKey, selectedPackageId])

  useEffect(() => {
    setRunPage(0)
  }, [selectedRunId])

  useEffect(() => {
    const items = baselineSets.data?.items ?? []
    if (items.length && !items.some((item) => item.id === selectedSetId)) {
      setSelectedSetId(items[0].id)
      setSelectedRunId(0)
    }
    if (!items.length && selectedSetId) {
      setSelectedSetId(0)
      setSelectedRunId(0)
    }
  }, [baselineSets.data?.items, selectedSetId])

  useEffect(() => {
    const runFromUrl = Number(searchParams.get("run") || 0)
    const nextRunId = baselineRunIdAfterSetLoad(
      selectedRunId,
      selectedSet.data?.runs ?? null,
      runFromUrl,
    )
    if (nextRunId !== selectedRunId) setSelectedRunId(nextRunId)
  }, [searchParams, selectedRunId, selectedSet.data?.runs])

  useEffect(() => {
    if (runDetail.data?.summary.status !== "running") {
      queryClient.invalidateQueries({ queryKey: ["baseline-sets"] })
      if (selectedSetId) {
        queryClient.invalidateQueries({ queryKey: ["baseline-set", selectedSetId] })
      }
    }
  }, [queryClient, runDetail.data?.summary.status, selectedSetId])

  const upload = useMutation({
    mutationFn: (files: File[]) => baselineRegressionApi.uploadAssets(
      files,
      undefined,
      selectedCategoryKey,
    ),
    onSuccess: async ({ items, package: uploadedPackage }) => {
      setSelectedPackageId(uploadedPackage.id)
      setUseWholePackage(true)
      setSelectedAssetIds((current) => {
        const next = new Set(current)
        items.forEach((item) => next.add(item.id))
        return next
      })
      setExpectedByAsset((current) => ({
        ...current,
        ...Object.fromEntries(
          items
            .filter((item) => item.suggested_expected_level)
            .map((item) => [item.id, item.suggested_expected_level]),
        ),
      }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-assets"] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-packages"] }),
        queryClient.invalidateQueries({ queryKey: ["material-packages"] }),
      ])
      toast.success(`已上传并选中素材包“${uploadedPackage.name}”（${items.length} 张）`)
    },
    onError: (error) => toast.error(error.message),
  })

  useEffect(() => {
    if (!assets.data?.items.length) return
    setExpectedByAsset((current) => {
      const next = { ...current }
      for (const asset of assets.data.items) {
        if (next[asset.id] === undefined && asset.suggested_expected_level) {
          next[asset.id] = asset.suggested_expected_level
        }
      }
      return next
    })
  }, [assets.data?.items])

  const createSet = useMutation({
    mutationFn: () => {
      const visibleAssets = assets.data?.items ?? []
      const expectedLevelOverrides = Object.fromEntries(
        visibleAssets
          .filter((asset) => {
            const value = expectedByAsset[asset.id]
            return value && value !== (asset.suggested_expected_level ?? defaultLevel)
          })
          .map((asset) => [asset.id, expectedByAsset[asset.id]]),
      )
      return baselineRegressionApi.createSet({
        name: name.trim(),
        description: description.trim(),
        default_expected_level: defaultLevel,
        category_key: selectedCategoryKey,
        ...(useWholePackage && selectedPackageId
          ? {
              source_package_id: selectedPackageId,
              expected_level_overrides: expectedLevelOverrides,
              items: [],
            }
          : {
              items: Array.from(selectedAssetIds).map((assetId) => {
                const asset = visibleAssets.find((item) => item.id === assetId)
                return {
                  asset_id: assetId,
                  expected_level: expectedByAsset[assetId]
                    ?? asset?.suggested_expected_level
                    ?? defaultLevel,
                  source_package_id: selectedPackageId || undefined,
                }
              }),
            }),
      })
    },
    onSuccess: async (created) => {
      setSelectedSetId(created.id)
      setSelectedRunId(0)
      setSelectedAssetIds(new Set())
      setExpectedByAsset({})
      setUseWholePackage(false)
      setName("")
      setDescription("")
      await queryClient.invalidateQueries({ queryKey: ["baseline-sets"] })
      toast.success(`基准集“${created.name}”已冻结`)
    },
    onError: (error) => toast.error(error.message),
  })

  const createBalanced100 = useMutation({
    mutationFn: () => baselineRegressionApi.createBalanced100(),
    onSuccess: async ({ summary, idempotent }) => {
      setSelectedSetId(summary.id)
      setSelectedRunId(0)
      await queryClient.invalidateQueries({ queryKey: ["baseline-sets"] })
      toast.success(idempotent ? "100 张均衡基准集已存在，已切换" : "100 张均衡基准集已冻结")
    },
    onError: (error) => toast.error(error.message),
  })

  const createRun = useMutation({
    mutationFn: () => {
      const promptPayload = buildBaselineRunPayload({
        mode: v3SelectionMode,
        candidateRevisionId: selectedV3Revision?.id,
        promptMode: promptSelectionMode,
        promptAId: effectivePromptAId,
        promptBId: effectivePromptBId,
        executionMode,
        categoryKey: selectedSet.data?.summary.category_key,
      })
      return baselineRegressionApi.createRun(selectedSetId, {
        ...promptPayload,
      })
    },
    onSuccess: async (run) => {
      setSelectedRunId(run.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-sets"] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-set", selectedSetId] }),
      ])
      toast.success(`第 ${run.sequence_no} 轮基准回归已启动`)
    },
    onError: (error) => toast.error(error.message),
  })

  const allSelected = Boolean(assets.data?.items.length)
    && selectedAssetIds.size === assets.data?.items.length
  const selectedPackage = packages.data?.items.find(
    (item: MaterialPackage) => item.id === selectedPackageId,
  )
  const creationCount = useWholePackage
    ? selectedPackage?.active_asset_count ?? 0
    : selectedAssetIds.size
  const assetPageCount = Math.max(
    1,
    Math.ceil((assets.data?.total ?? 0) / ASSET_PAGE_SIZE),
  )
  const selectedRun = selectedSet.data?.runs.find((run) => run.id === selectedRunId)
  const summary = runDetail.data?.summary ?? selectedRun
  const candidateRevisionFromRun = summary?.selection.dimension.v3_contract?.candidate_revision_id
    ? v3Revisions.data?.items.find(
      (revision) => revision.id === summary.selection.dimension.v3_contract?.candidate_revision_id,
    )
    : null
  const activateCandidate = useMutation({
    mutationFn: () => {
      if (!summary || !candidateRevisionFromRun || !activeV3Revision) {
        throw new Error("候选回归上下文尚未加载完整")
      }
      return baselineRegressionApi.activateV3Revision(
        summary.category_key,
        candidateRevisionFromRun.revision,
        {
          regression_run_id: summary.id,
          expected_projected_revision: activeV3Revision.revision,
          expected_projected_contract_hash: activeV3Revision.contract_hash,
          note: "管理员确认候选回归通过后启用",
        },
      )
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["category-evaluation-v3-config"] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-v3-revisions", summary?.category_key] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-sets"] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-set", selectedSetId] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-regression", selectedRunId] }),
      ])
      toast.success("候选机制已启用")
    },
    onError: (error) => toast.error(error.message),
  })
  const correctionItemId = searchParams.get("mode") === "correction"
    ? Number(searchParams.get("item") || 0)
    : 0

  useEffect(() => {
    const categoryKey = runDetail.data?.summary.category_key
    const baselineSetId = runDetail.data?.baseline_set.id
    if (!categoryKey || !baselineSetId) return
    const patch = baselineRunContextPatch(
      selectedCategoryKey,
      selectedSetId,
      { categoryKey, baselineSetId },
    )
    if (patch.categoryKey) {
      setSelectedCategoryKey(patch.categoryKey)
      return
    }
    if (patch.baselineSetId) setSelectedSetId(patch.baselineSetId)
  }, [
    runDetail.data?.baseline_set.id,
    runDetail.data?.summary.category_key,
    selectedCategoryKey,
    selectedSetId,
  ])

  function toggleAsset(assetId: number) {
    setSelectedAssetIds((current) => {
      const next = new Set(current)
      if (next.has(assetId)) next.delete(assetId)
      else next.add(assetId)
      return next
    })
  }

  function selectSet(setId: number) {
    setSelectedSetId(setId)
    setSelectedRunId(0)
    setSearchParams({}, { replace: true })
  }

  function selectCategory(categoryKey: string) {
    if (categoryKey === selectedCategoryKey) return
    setSelectedCategoryKey(categoryKey)
    setSelectedPackageId(0)
    setUseWholePackage(false)
    setSelectedAssetIds(new Set())
    setExpectedByAsset({})
    setSelectedSetId(0)
    setSelectedRunId(0)
    setSearchParams({}, { replace: true })
  }

  function openCorrection(itemId: number) {
    if (!selectedRunId) return
    setSearchParams({ run: String(selectedRunId), item: String(itemId), mode: "correction" })
  }

  function closeCorrection() {
    setSearchParams({}, { replace: true })
  }

  return (
    <>
      <PageHeader
        index="03.7"
        title="基准回归"
        description="按类目冻结素材与 L1–L5 期望等级，可独立选择提示词重复运行；每轮只使用启动时冻结的现役等级规则，回归结果与后续纠偏分析互相隔离。"
        actions={
          <>
            <input
              ref={uploadRef}
              className="hidden"
              type="file"
              accept={(selectedCategory?.allowed_mime_types ?? [
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/gif",
              ]).join(",")}
              multiple
              onChange={(event) => {
                const files = event.target.files
                  ? Array.from(event.target.files)
                  : []
                event.target.value = ""
                if (files.length) upload.mutate(files)
              }}
            />
            <Button
              variant="secondary"
              onClick={() => uploadRef.current?.click()}
              disabled={upload.isPending}
            >
              <CloudArrowUp />{upload.isPending ? "正在上传" : "上传基准素材"}
            </Button>
            {selectedCategoryKey === "inspiration_image" && <Button
              variant="secondary"
              title="L1-L5 各 20 张；按人工真值与 SHA-256 去重后冻结"
              onClick={() => createBalanced100.mutate()}
              disabled={createBalanced100.isPending}
            >
              <CheckSquare />{createBalanced100.isPending ? "正在校验" : "生成 100 张均衡基准集"}
            </Button>}
            <Button
              variant="secondary"
              onClick={() => setBaselineSetDialogOpen(true)}
            >
              选择基准集
            </Button>
            <Button
              variant="secondary"
              onClick={() => setRunConfigDrawerOpen(true)}
              disabled={!selectedSetId}
            >
              运行配置
            </Button>
            <Button
              variant="secondary"
              onClick={() => setRunHistoryDrawerOpen(true)}
              disabled={!selectedSetId}
            >
              运行历史
            </Button>
            <Button
              variant="secondary"
              onClick={() => setMetricsDrawerOpen(true)}
              disabled={!selectedRunId}
            >
              查看字段证据
            </Button>
            <Button
              variant="secondary"
              onClick={() => setSemanticQualityDrawerOpen(true)}
              disabled={!selectedRunId}
            >
              语义字段质量
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                assets.refetch()
                packages.refetch()
                baselineSets.refetch()
                if (selectedSetId) selectedSet.refetch()
                if (selectedRunId) runDetail.refetch()
              }}
            >
              <ArrowClockwise />刷新
            </Button>
          </>
        }
      />

      <section className="mx-auto max-w-[1720px] border-b border-[var(--line-strong)] bg-[#f8faed] px-5 py-5 md:px-8 lg:px-10">
        <div className="grid gap-4 lg:grid-cols-[minmax(260px,420px)_minmax(0,1fr)] lg:items-end">
          <label>
            <span className="mb-2 block text-xs font-bold">评测类目</span>
            <select
              className="h-12 w-full rounded-[4px] border-2 border-[var(--line-strong)] bg-white px-3 text-base font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              value={selectedCategoryKey}
              onChange={(event) => selectCategory(event.target.value)}
            >
              {activeCategories.map((category) => (
                <option key={category.category_key} value={category.category_key}>
                  {category.display_name} · {category.category_key}
                </option>
              ))}
            </select>
          </label>
          <div className="border-l-2 border-primary pl-4">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="text-sm">当前流水线：{selectedCategory?.display_name ?? selectedCategoryKey}</strong>
              <Badge tone="active">类目隔离</Badge>
              <Badge>
                {selectedCategory?.pipeline_config.dimensions.enabled === false
                  ? "当前仅提示词"
                  : `当前维度 ${selectedCategory?.dimension_schema_version ?? "未绑定"}`}
              </Badge>
            </div>
            <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
              {selectedCategory?.description || "素材包、冻结基准集、回归运行和纠偏分析均只在当前类目内流转。"}
            </p>
          </div>
        </div>
      </section>

      <div className="mx-auto grid max-w-[1720px] lg:grid-cols-[310px_minmax(0,1fr)]">
        <aside className="border-r border-[var(--line)] bg-white p-4 lg:min-h-[calc(100dvh-125px)]">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">已冻结基准集</h2>
            <span className="font-data text-xs text-[var(--muted)]">
              {baselineSets.data?.items.length ?? 0}
            </span>
          </div>
          <div className="space-y-1">
            {baselineSets.data?.items.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.id === selectedSetId}
                className={`w-full border px-3 py-3 text-left transition-colors ${
                  item.id === selectedSetId
                    ? "border-[var(--line-strong)] bg-[#f6f9dc]"
                    : "border-transparent hover:bg-[#f8f9f6]"
                }`}
                onClick={() => selectSet(item.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-data text-xs font-semibold">#{item.id}</span>
                  <Badge tone={item.latest_run ? statusTone(item.latest_run) : "neutral"}>
                    {item.latest_run ? statusName(item.latest_run) : "尚未运行"}
                  </Badge>
                </div>
                <p className="mt-2 truncate text-sm font-semibold">{item.name}</p>
                <p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">
                  {item.item_count} 张 · 默认 {item.default_expected_level} · {item.run_count} 轮
                </p>
              </button>
            ))}
            {!baselineSets.isLoading && !baselineSets.data?.items.length && (
              <p className="border-y border-[var(--line)] px-3 py-8 text-center text-xs leading-5 text-[var(--muted)]">
                尚无基准集。请从右侧上传或选择已有素材后创建。
              </p>
            )}
          </div>
        </aside>

        <main className="min-w-0 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
          <BaselineSetDialog open={baselineSetDialogOpen} onOpenChange={setBaselineSetDialogOpen}>
          <section className="border-y border-[var(--line-strong)] bg-white">
            <div className="border-b border-[var(--line)] px-5 py-4">
              <h2 className="font-editorial text-2xl font-bold">创建冻结基准集</h2>
              <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                上传会复用现有素材去重规则。整批等级作为默认真值，也可在入集前逐张覆盖；创建后不可修改。
              </p>
            </div>
            <div className="grid gap-4 border-b border-[var(--line)] bg-[#fafbf8] px-5 py-4 lg:grid-cols-[minmax(280px,1fr)_auto] lg:items-end">
              <label>
                <span className="mb-2 block text-xs font-semibold">从素材包选择（推荐）</span>
                <select
                  className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                  value={selectedPackageId || ""}
                  onChange={(event) => {
                    const packageId = event.target.value ? Number(event.target.value) : 0
                    setSelectedPackageId(packageId)
                    setUseWholePackage(Boolean(packageId))
                    setSelectedAssetIds(new Set())
                    setExpectedByAsset({})
                  }}
                >
                  <option value="">不限定素材包，逐张选择</option>
                  {(packages.data?.items ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.active_asset_count} 张可用
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex h-11 cursor-pointer items-center gap-2 border border-[var(--line-strong)] bg-white px-3 text-sm font-semibold">
                <input
                  type="checkbox"
                  className="size-4 accent-[#9dbb1c]"
                  checked={useWholePackage}
                  disabled={!selectedPackageId}
                  onChange={(event) => {
                    setUseWholePackage(event.target.checked)
                    setSelectedAssetIds(new Set())
                    setExpectedByAsset({})
                  }}
                />
                整包加入，无需逐张勾选
              </label>
            </div>
            <div className="grid gap-4 px-5 py-5 min-[1280px]:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] min-[1280px]:items-end min-[1750px]:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,160px)_auto]">
              <label>
                <span className="mb-2 block text-xs font-semibold">基准集名称</span>
                <Input
                  value={name}
                  maxLength={160}
                  placeholder="例如：2026-07 全 L1 基准"
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">说明（可选）</span>
                <Input
                  value={description}
                  maxLength={2000}
                  placeholder="记录来源与确认口径"
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <LevelSelect
                label="整批期望等级"
                value={defaultLevel}
                onChange={(value) => setDefaultLevel(value)}
              />
              <Button
                disabled={!name.trim() || !creationCount || createSet.isPending}
                onClick={() => createSet.mutate()}
              >
                {createSet.isPending ? "正在冻结" : `创建基准集 (${creationCount})`}
              </Button>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] bg-[#fafbf8] px-5 py-3">
              <p className="text-xs text-[var(--muted)]">
                {useWholePackage ? (
                  <>将整包加入：<strong className="text-foreground">{selectedPackage?.name}</strong> · {creationCount} 张可用</>
                ) : (
                  <>已选择 <strong className="text-foreground">{selectedAssetIds.size}</strong> / {assets.data?.total ?? 0} 张</>
                )}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={useWholePackage || !assets.data?.items.length}
                  onClick={() => {
                    setSelectedAssetIds(
                      allSelected
                        ? new Set()
                        : new Set(assets.data?.items.map((asset) => asset.id) ?? []),
                    )
                  }}
                >
                  {allSelected ? <CheckSquare weight="fill" /> : <Square />}
                  {allSelected ? "取消本页全选" : "全选本页素材"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={
                    useWholePackage
                      ? !assets.data?.items.length
                      : !selectedAssetIds.size
                  }
                  onClick={() => {
                    const next: Record<number, BaselineLevel> = {}
                    const assetIds = useWholePackage
                      ? assets.data?.items.map((asset) => asset.id) ?? []
                      : Array.from(selectedAssetIds)
                    assetIds.forEach((assetId) => { next[assetId] = defaultLevel })
                    setExpectedByAsset((current) => ({ ...current, ...next }))
                  }}
                >
                  当前{useWholePackage ? "预览" : "所选"}全部改为 {defaultLevel}
                </Button>
              </div>
            </div>
            {useWholePackage && (
              <div className="border-t border-[var(--line)] bg-[#f6f9dc] px-5 py-3 text-xs font-semibold">
                已启用整包模式：单个基准集最多 10000 张。系统先按文件名中的 L1–L5 / 好 / 中等 / 中差 / 极差 / 过滤预填；下方按页预览并可逐张修改，未访问页面的素材同样由服务端解析。
              </div>
            )}
            <div className="flex items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-3 text-xs text-[var(--muted)]">
              <span>第 {assetPage + 1} / {assetPageCount} 页 · 每页最多 {ASSET_PAGE_SIZE} 张</span>
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={assetPage <= 0 || assets.isFetching}
                  onClick={() => setAssetPage((current) => Math.max(0, current - 1))}
                >
                  上一页
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={assetPage + 1 >= assetPageCount || assets.isFetching}
                  onClick={() => setAssetPage((current) => current + 1)}
                >
                  下一页
                </Button>
              </div>
            </div>
            <div className="max-h-[440px] overflow-auto">
              {assets.isLoading ? (
                <div className="h-48 animate-pulse bg-white" />
              ) : assets.data?.items.length ? (
                <table className="w-full min-w-[720px] border-collapse text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-white">
                    <tr className="border-b border-[var(--line)] text-xs text-[var(--muted)]">
                      <th className="w-14 px-5 py-3"><span className="sr-only">选择</span></th>
                      <th className="px-3 py-3">素材</th>
                      <th className="px-3 py-3">尺寸</th>
                      <th className="w-44 px-5 py-3">期望等级</th>
                    </tr>
                  </thead>
                  <tbody>
                    {assets.data.items.map((asset) => {
                      const checked = useWholePackage || selectedAssetIds.has(asset.id)
                      const suggestedLevel = asset.suggested_expected_level
                      const effectiveLevel = expectedByAsset[asset.id]
                        ?? suggestedLevel
                        ?? defaultLevel
                      const manuallyChanged = expectedByAsset[asset.id] !== undefined
                        && expectedByAsset[asset.id] !== (suggestedLevel ?? defaultLevel)
                      return (
                        <tr
                          key={asset.id}
                          className={`border-b border-[var(--line)] last:border-0 ${checked ? "bg-[#f7fadf]" : "hover:bg-[#fbfcfa]"}`}
                        >
                          <td className="px-5 py-3">
                            <button
                              type="button"
                              className="flex size-8 items-center justify-center rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                              aria-label={`${checked ? "取消选择" : "选择"}${asset.name}`}
                              disabled={useWholePackage}
                              onClick={() => toggleAsset(asset.id)}
                            >
                              {checked ? <CheckSquare size={20} weight="fill" /> : <Square size={20} />}
                            </button>
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex min-w-0 items-center gap-3">
                              <ImagePreviewButton
                                src={asset.image_url}
                                alt={asset.name}
                                imageClassName="size-12"
                                onPreview={setImagePreview}
                              />
                              <div className="min-w-0">
                                <p className="file-name max-w-lg truncate text-sm">{asset.name}</p>
                                <div className="mt-1 flex flex-wrap items-center gap-2">
                                  <p className="font-data text-[0.68rem] text-[var(--muted)]">素材 #{asset.id}</p>
                                  <Badge tone={manuallyChanged ? "active" : suggestedLevel ? "success" : "neutral"}>
                                    {manuallyChanged
                                      ? "已手动调整"
                                      : suggestedLevel
                                        ? `文件名预填 ${suggestedLevel}`
                                        : `整批默认 ${defaultLevel}`}
                                  </Badge>
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">
                            {asset.width && asset.height ? `${asset.width} × ${asset.height}` : "—"}
                          </td>
                          <td className="px-5 py-3">
                            <select
                              aria-label={`${asset.name}期望等级`}
                              className="h-9 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-2 text-sm disabled:bg-[#f1f3ef] disabled:text-[var(--muted)]"
                              disabled={!checked}
                              value={effectiveLevel}
                              onChange={(event) => setExpectedByAsset((current) => ({
                                ...current,
                                [asset.id]: event.target.value as BaselineLevel,
                              }))}
                            >
                              {levels.map((level) => <option key={level} value={level}>{level}</option>)}
                            </select>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <p className="px-5 py-12 text-center text-sm text-[var(--muted)]">
                  还没有可选素材，请先上传基准图片。
                </p>
              )}
            </div>
          </section>
          </BaselineSetDialog>

          {selectedSet.data ? (
            <section className="mt-10">
              <div className="border-b border-[var(--line-strong)] pb-5">
                <div>
                  <p className="font-data text-xs text-[var(--muted)]">
                    基准集 #{selectedSet.data.summary.id} · {selectedSet.data.summary.item_count} 张 · 指纹 {selectedSet.data.summary.fingerprint.slice(0, 12)}
                  </p>
                  <h2 className="font-editorial mt-2 text-3xl font-bold">{selectedSet.data.summary.name}</h2>
              {selectedSet.data.summary.description && (
                    <p className="mt-2 text-sm text-[var(--muted)]">{selectedSet.data.summary.description}</p>
                  )}
                </div>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-y border-[var(--line)] bg-[#fafbf8] px-4 py-3">
                  <div className="min-w-0">
                    <p className="text-xs font-semibold">运行配置</p>
                    <p className="mt-1 truncate text-xs text-[var(--muted)]">
                      {promptSelectionMode === "published" ? "当前发布版本" : promptSelectionMode === "manual" ? "手动选择版本" : "单提示词"}
                      {" · "}{executionMode === "structured" ? "标准评分合同" : "自由实验"}
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => setRunConfigDrawerOpen(true)}>
                    调整运行配置
                  </Button>
                </div>
                <RunConfigDrawer
                  open={runConfigDrawerOpen}
                  onOpenChange={setRunConfigDrawerOpen}
                  footer={(
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="min-w-0 text-xs leading-5 text-[var(--muted)]">
                        <p className="font-semibold text-[var(--ink)]">启动摘要</p>
                        <p className="truncate">A {selectedPromptA?.version ?? "—"} · B {selectedPromptB?.version ?? "—"} · 等级规则 {selectedV3Revision ? `Revision ${selectedV3Revision.revision}` : "未加载"}</p>
                      </div>
                      <Button
                        className="shrink-0"
                        disabled={
                          createRun.isPending
                          || v3SelectionLoading
                          || v3SelectionUnavailable
                          || promptBindingMismatch
                          || selectedSet.data.runs.some((run) => run.status === "running")
                          || (
                            promptSelectionMode !== "published"
                            && (!effectivePromptAId
                              || (promptSelectionMode === "manual" && !effectivePromptBId))
                          )
                        }
                        onClick={() => createRun.mutate()}
                      >
                        <Play weight="fill" />
                        {createRun.isPending ? "正在启动" : "运行全量回归"}
                      </Button>
                    </div>
                  )}
                >
                  <div className="space-y-7">
                    <section className="space-y-4" aria-labelledby="baseline-prompt-config">
                      <div>
                        <p id="baseline-prompt-config" className="text-sm font-bold">提示词配置</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">A/B 版本只影响本轮冻结快照，不改变线上发布指针。</p>
                      </div>
                      <label>
                        <span className="mb-2 block text-xs font-semibold">提示词取值方式</span>
                        <select
                          className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                          value={promptSelectionMode}
                          onChange={(event) => setPromptSelectionMode(event.target.value as "published" | "manual" | "single")}
                        >
                          <option value="published">当前发布版本</option>
                          <option value="manual">手动选择版本</option>
                          <option value="single">单提示词（一次调用）</option>
                        </select>
                      </label>
                      <div className="grid gap-4 md:grid-cols-2">
                        <PromptSelect
                          label={promptSelectionMode === "single" ? "单提示词" : "调用 A"}
                          value={effectivePromptAId}
                          options={promptAOptions}
                          published={publishedPromptA}
                          disabled={promptSelectionMode === "published"}
                          onChange={setSelectedPromptAId}
                        />
                        <PromptSelect
                          label="调用 B"
                          value={effectivePromptBId}
                          options={promptBOptions}
                          published={publishedPromptB}
                          disabled={promptSelectionMode !== "manual"}
                          onChange={setSelectedPromptBId}
                        />
                      </div>
                    </section>

                    <section className="space-y-4 border-t border-[var(--line)] pt-6" aria-labelledby="baseline-v3-config">
                      <div>
                        <p id="baseline-v3-config" className="text-sm font-bold">等级规则配置</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">默认使用当前现役；候选只允许同类目且仍在现役祖先链上的版本。</p>
                      </div>
                      <label>
                        <span className="mb-2 block text-xs font-semibold">等级规则取值方式</span>
                        <select
                          className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                          value={v3SelectionMode}
                          onChange={(event) => {
                            const next = event.target.value as "active" | "candidate"
                            setV3SelectionMode(next)
                            if (next === "active") setSelectedV3RevisionId(v3Revisions.data?.projected_revision_id ?? 0)
                          }}
                          disabled={v3SelectionLoading || v3SelectionUnavailable}
                        >
                          <option value="active">当前现役版本</option>
                          <option value="candidate">手动选择候选版本</option>
                        </select>
                      </label>
                      <label>
                        <span className="mb-2 block text-xs font-semibold">等级规则版本</span>
                        <select
                          className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                          value={selectedV3Revision?.id ?? 0}
                          onChange={(event) => {
                            const id = Number(event.target.value)
                            setSelectedV3RevisionId(id)
                            if (id === v3Revisions.data?.projected_revision_id) setV3SelectionMode("active")
                            else setV3SelectionMode("candidate")
                          }}
                          disabled={v3SelectionLoading || v3SelectionUnavailable}
                        >
                          {(v3Revisions.data?.items ?? []).map((revision: BaselineV3Revision) => {
                            const group = v3RevisionGroup(revision, v3Revisions.data?.projected_revision_id ?? 0)
                            const selectable = group === "active" || isSelectableV3Candidate(revision, v3Revisions.data?.items ?? [], v3Revisions.data?.projected_revision_id ?? 0)
                            return <option key={revision.id} value={revision.id} disabled={!selectable}>{`${group === "active" ? "现役" : group === "candidate" ? "候选" : "历史"} · V${revision.revision} · ${revision.display_name}`}</option>
                          })}
                        </select>
                      </label>
                      {v3Revisions.isError && <div className="border border-[#d7a09d] bg-[#fff5f4] px-3 py-3 text-xs text-[#8d2924]">等级规则版本列表加载失败，无法启动。<button type="button" className="ml-2 underline" onClick={() => v3Revisions.refetch()}>重试</button></div>}
                      {selectedV3Revision && <div className="border border-[var(--line)] bg-[#fafbf8] px-3 py-3 text-xs leading-5"><p className="font-semibold">{selectedV3Revision.display_name} · Revision {selectedV3Revision.revision}</p><p className="text-[var(--muted)]">状态：{selectedV3Revision.status} · Hash {selectedV3Revision.contract_hash.slice(0, 12)}</p></div>}
                      {v3SelectionMode === "candidate" && candidatePromptUnavailable && <div className="border border-[#d7a09d] bg-[#fff5f4] px-3 py-3 text-xs leading-5 text-[#8d2924]">候选等级规则绑定 Prompt 不可用：{candidatePromptBindingA && candidatePromptResolutionA.status !== "available" ? formatCandidatePromptBindingIssue("A", candidatePromptResolutionA) : ""}{candidatePromptBindingB && candidatePromptResolutionB.status !== "available" ? `，${formatCandidatePromptBindingIssue("B", candidatePromptResolutionB)}` : ""}。不能用当前 v4 替代；请恢复原 Prompt 或基于当前 Prompt 重建候选。</div>}
                      {v3SelectionMode === "candidate" && promptBindingMismatch && !candidatePromptUnavailable && <div className="border border-[#d7a09d] bg-[#fff5f4] px-3 py-3 text-xs leading-5 text-[#8d2924]">候选等级规则绑定版本不匹配：{candidatePromptBindingA ? `A 需要 ${candidatePromptBindingA}` : ""}{candidatePromptBindingB ? `，B 需要 ${candidatePromptBindingB}` : ""}。请调整 A/B 后再启动。</div>}
                    </section>

                    <section className="space-y-4 border-t border-[var(--line)] pt-6" aria-labelledby="baseline-execution-config">
                      <div>
                        <p id="baseline-execution-config" className="text-sm font-bold">执行方式</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">运行启动后会冻结提示词、等级规则和执行模式；历史记录不会被后续版本覆盖。</p>
                      </div>
                      <label>
                        <span className="mb-2 block text-xs font-semibold">结果判定方式</span>
                        <select
                          className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                          value={executionMode}
                          onChange={(event) => setExecutionMode(event.target.value as "freeform" | "structured")}
                        >
                          <option value="freeform">自由实验（默认）</option>
                          <option value="structured">标准评分合同</option>
                        </select>
                      </label>
                      <p className="text-xs leading-5 text-[var(--muted)]">
                        {executionMode === "freeform" ? "自由实验不会要求固定 JSON、范围字段或八维输出；系统完整保留原始回答，能安全识别时自动计分，否则进入人工判断。" : "标准评分合同沿用现有结构化协议；缺少范围、等级、分数或所选维度时会按合同失败。"}
                      </p>
                    </section>
                  </div>
                </RunConfigDrawer>
              </div>

              <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-y border-[var(--line)] bg-white px-4 py-3">
                <div>
                  <p className="text-xs font-semibold">当前轮次</p>
                  <p className="font-data mt-1 text-xs text-[var(--muted)]">
                    {selectedRun ? `第 ${selectedRun.sequence_no} 轮 · ${statusName(selectedRun)} · ${selectedRun.completed}/${selectedRun.total}` : "尚未运行"}
                  </p>
                </div>
                <Button variant="secondary" size="sm" onClick={() => setRunHistoryDrawerOpen(true)}>
                  查看运行历史
                </Button>
              </div>
              <div className="flex gap-2 overflow-x-auto pb-2">
                {selectedSet.data.runs.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    aria-pressed={run.id === selectedRunId}
                    className={`min-w-44 border px-3 py-3 text-left ${
                      run.id === selectedRunId
                        ? "border-[#8da91e] bg-[#f1f8cf]"
                        : "border-[var(--line)] bg-white hover:bg-[#fafbf8]"
                    }`}
                    onClick={() => setSelectedRunId(run.id)}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold">第 {run.sequence_no} 轮</span>
                      <Badge tone={statusTone(run)}>{statusName(run)}</Badge>
                    </div>
                    <p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">
                      {run.completed}/{run.total} · #{run.id}
                    </p>
                    <p className="font-data mt-1 truncate text-[0.68rem] text-[var(--muted)]">
                      A {run.selection.prompt_a?.version ?? "—"} · B {run.selection.prompt_b?.version ?? "—"}
                    </p>
                  </button>
                ))}
                {!selectedSet.data.runs.length && (
                  <p className="w-full border-y border-[var(--line)] px-4 py-7 text-center text-sm text-[var(--muted)]">
                    此基准集尚未运行。
                  </p>
                )}
              </div>
              <RunHistoryDrawer open={runHistoryDrawerOpen} onOpenChange={setRunHistoryDrawerOpen}>
                <div className="space-y-2">
                  {selectedSet.data.runs.map((run) => (
                    <button
                      key={`history-${run.id}`}
                      type="button"
                      className="w-full border border-[var(--line)] bg-white px-4 py-3 text-left hover:bg-[#fafbf8]"
                      onClick={() => {
                        setSelectedRunId(run.id)
                        setRunHistoryDrawerOpen(false)
                      }}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-semibold">第 {run.sequence_no} 轮</span>
                        <Badge tone={statusTone(run)}>{statusName(run)}</Badge>
                      </div>
                      <p className="font-data mt-1 text-xs text-[var(--muted)]">{run.completed}/{run.total} · #{run.id}</p>
                    </button>
                  ))}
                  {!selectedSet.data.runs.length && <p className="py-8 text-center text-sm text-[var(--muted)]">此基准集尚未运行。</p>}
                </div>
              </RunHistoryDrawer>

              {summary && (
                <>
                {candidateRevisionFromRun && summary.status === "completed" && me.data?.is_admin && (
                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-l-2 border-primary bg-[#f8faed] px-4 py-4">
                    <div>
                      <p className="text-sm font-bold">候选回归已完成</p>
                      <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                        Revision {candidateRevisionFromRun.revision} 使用当前冻结基准集完成回归；通过后可切换运行时机制，标签事实发布保持独立。
                      </p>
                    </div>
                    <Button
                      disabled={activateCandidate.isPending || !summary.previous_run_id}
                      onClick={() => {
                        if (window.confirm("确认启用该候选机制？此操作会更新当前类目的运行时投影。")) {
                          activateCandidate.mutate()
                        }
                      }}
                    >
                      <Check weight="bold" />{activateCandidate.isPending ? "正在启用" : "启用候选"}
                    </Button>
                  </div>
                )}
                <StatusSummaryStrip className="mt-5">
                  {(() => {
                    const acceptanceRows = acceptancePages.data?.flat() ?? []
                    const progress = baselineAcceptanceProgressFromPages(
                      acceptancePages.data ?? [],
                      summary.status !== "running",
                    )
                    const allRowsLoaded = acceptanceRows.length === summary.total
                    const blockers = acceptanceRows.filter(
                      (item) => item.status !== "completed" || !item.evaluation,
                    ).length
                    return (
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold">逐条确认与纠偏</p>
                          <p className="mt-1 text-xs text-[var(--muted)]">
                            {acceptancePages.isLoading
                              ? "正在汇总全轮人工验收进度"
                              : `已确认 ${progress.reviewed}/${progress.total} · 未评分/失败阻塞 ${blockers}`}
                          </p>
                        </div>
                        <Button
                          size="sm"
                          disabled={!allRowsLoaded || !progress.complete}
                          onClick={() => toast.success("本轮人工验收条件已满足；当前仅展示本地完成摘要，不写入新状态。")}
                        >
                          完成人工验收
                        </Button>
                      </div>
                    )
                  })()}
                </StatusSummaryStrip>
                <RegressionResults
                  run={summary}
                  items={runDetail.data?.items ?? []}
                  pagination={runDetail.data?.pagination ?? {
                    offset: 0,
                    limit: RUN_PAGE_SIZE,
                    total: summary.total,
                  }}
                  page={runPage}
                  onPageChange={setRunPage}
                  loading={runDetail.isLoading}
                  fieldMetrics={fieldMetrics.data}
                  onPreview={setImagePreview}
                  onOpenMetrics={() => setMetricsDrawerOpen(true)}
                  correctionItemId={correctionItemId}
                  onOpenCorrection={openCorrection}
                  onCloseCorrection={closeCorrection}
                />
                </>
              )}
            </section>
          ) : !selectedSet.isLoading && (
            <section className="mt-10 border-y border-[var(--line)] px-5 py-12 text-center text-sm text-[var(--muted)]">
              选择一个已冻结基准集后可启动回归并查看结果。
            </section>
          )}
        </main>
      </div>
      <ImageLightbox
        preview={imagePreview}
        onOpenChange={(open) => {
          if (!open) setImagePreview(null)
        }}
      />
      {summary && (
        <MetricsDrawer open={metricsDrawerOpen} onOpenChange={setMetricsDrawerOpen}>
          <FieldMetricsEvidence
            data={fieldMetrics.data}
            loading={fieldMetrics.isLoading}
            error={fieldMetrics.error}
            levelMetrics={summary.metrics}
          />
        </MetricsDrawer>
      )}
      <SemanticQualityDrawer
        open={semanticQualityDrawerOpen}
        onOpenChange={setSemanticQualityDrawerOpen}
        data={semanticMetrics.data}
        loading={semanticMetrics.isLoading}
        error={semanticMetrics.error}
      />
    </>
  )
}

function RegressionResults({
  run,
  items,
  pagination,
  page,
  onPageChange,
  loading,
  fieldMetrics,
  onPreview,
  onOpenMetrics,
  correctionItemId,
  onOpenCorrection,
  onCloseCorrection,
}: {
  run: BaselineRegressionRun
  items: BaselineRegressionItem[]
  pagination: { offset: number; limit: number; total: number }
  page: number
  onPageChange: (page: number) => void
  loading: boolean
  fieldMetrics?: BaselineFieldMetrics
  onPreview: (preview: ImagePreview) => void
  onOpenMetrics: () => void
  correctionItemId: number
  onOpenCorrection: (itemId: number) => void
  onCloseCorrection: () => void
}) {
  const queryClient = useQueryClient()
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/auth/me"),
  })
  const availableDeviationIds = useMemo(
    () => items
      .filter((item) => (
        item.status === "completed"
        && item.deviation
        && item.optimization_case_id === null
      ))
      .map((item) => item.id),
    [items],
  )
  const [selectedDeviationIds, setSelectedDeviationIds] = useState<Set<number>>(
    new Set(),
  )
  const [activeView, setActiveView] = useState<"results" | "correction">("results")
  const [reviewNotes, setReviewNotes] = useState<Record<number, string>>({})
  const [reopenSeeds, setReopenSeeds] = useState<Record<number, {
    corrections: ReviewCorrection[]
    note: string
  }>>({})

  useEffect(() => {
    setSelectedDeviationIds(new Set(availableDeviationIds))
  }, [run.id, availableDeviationIds.join(",")])

  const enqueueDeviations = useMutation({
    mutationFn: () => baselineRegressionApi.enqueueDeviations(
      run.id,
      Array.from(selectedDeviationIds),
    ),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: ["baseline-regression", run.id],
      })
      queryClient.invalidateQueries({ queryKey: ["optimization-cases"] })
      setSelectedDeviationIds(new Set())
      toast.success(
        result.created
          ? `已将 ${result.created} 张偏差样本加入全局优化池`
          : "所选偏差样本已在全局优化池中",
      )
    },
    onError: (error) => toast.error(error.message),
  })
  const reviewResult = useMutation({
    mutationFn: ({
      item,
      decision,
      note,
      corrections = [],
    }: {
      item: BaselineRegressionItem
      decision: "approved" | "corrected" | "rejected"
      note: string
      corrections?: ReviewCorrection[]
    }) => {
      if (!item.evaluation) throw new Error("该回归结果没有可审核的评测记录")
      if (!me.data) throw new Error("当前登录账号尚未加载")
      return submitReviewDecision({
        evaluation: item.evaluation,
        reviewer: me.data.username,
        decision,
        note,
        corrections,
      })
    },
    onSuccess: async (_result, variables) => {
      await queryClient.invalidateQueries({
        queryKey: ["baseline-regression", run.id],
      })
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-acceptance", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["optimization-cases"] }),
      ])
      setReviewNotes((current) => ({ ...current, [variables.item.id]: "" }))
      toast.success(
        variables.decision === "corrected"
          ? "人工纠偏与最终等级已保存"
          : variables.decision === "approved"
            ? "已确认模型结果"
            : "已退回复核",
      )
    },
    onError: (error) => toast.error(error.message),
  })
  const reopenReview = useMutation({
    mutationFn: ({ item }: { item: BaselineRegressionItem }) => {
      if (!item.evaluation) throw new Error("该回归结果没有可重开的评测记录")
      const corrections = item.evaluation.human_review?.corrections ?? []
      setReopenSeeds((current) => ({
        ...current,
        [item.id]: {
          corrections,
          note: item.evaluation?.human_review?.note ?? "",
        },
      }))
      return baselineRegressionApi.reopenReview(
        item.evaluation.id,
        item.evaluation.review_revision,
      )
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-correction-view", run.id, correctionItemId] }),
      ])
      toast.success("已创建新的人工审核轮次，可继续修改")
    },
    onError: (error) => toast.error(error.message),
  })

  const metrics = run.metrics
  const pageCount = Math.max(1, Math.ceil(pagination.total / pagination.limit))
  const correctionItem = items.find((item) => item.id === correctionItemId)
  const correctionViewQuery = useQuery<CorrectionView>({
    queryKey: ["baseline-correction-view", run.id, correctionItemId],
    queryFn: () => baselineRegressionApi.getCorrectionView(run.id, correctionItemId),
    enabled: correctionItemId > 0 && Boolean(correctionItem),
  })
  const [correctionDraft, setCorrectionDraft] = useState<CorrectionDraft | null>(null)
  const [correctionDraftKey, setCorrectionDraftKey] = useState("")
  const correctionViewKey = correctionViewQuery.data
    ? `${correctionViewQuery.data.item_id}:${correctionViewQuery.data.contract?.contract_hash ?? "legacy"}:${correctionViewQuery.data.review_revision}`
    : ""

  useEffect(() => {
    if (!correctionViewQuery.data || !correctionViewKey) return
    if (correctionDraftKey === correctionViewKey) return
    setCorrectionDraft(correctionDraftFromView(correctionViewQuery.data))
    setCorrectionDraftKey(correctionViewKey)
  }, [correctionDraftKey, correctionViewKey, correctionViewQuery.data])

  useEffect(() => {
    if (correctionItemId > 0) return
    setCorrectionDraft(null)
    setCorrectionDraftKey("")
  }, [correctionItemId])

  const submitContractCorrection = useMutation({
    mutationFn: async () => {
      const view = correctionViewQuery.data
      if (!view || !correctionDraft) throw new Error("合同纠偏面板尚未加载")
      const payload = correctionSubmissionPayload(
        correctionDraft,
        view,
        correctionIdempotencyKey(run.id, correctionItemId),
      )
      if (!payload.nodes.length) throw new Error("请先修改至少一个纠偏节点")
      return baselineRegressionApi.submitCorrectionNodes(run.id, correctionItemId, payload)
    },
    onSuccess: async (response) => {
      setCorrectionDraft(mergeCorrectionResponse(correctionDraft ?? {}, response))
      setCorrectionDraftKey(`${response.item_id}:${response.contract?.contract_hash ?? "legacy"}:${response.review_revision}`)
      await queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] })
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-acceptance", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      toast.success("合同纠偏已保存，当前素材保持不变")
    },
    onError: (error) => toast.error(error.message),
  })

  if (correctionItemId > 0 && correctionItem) {
    return (
      <CorrectionWorkbench
        item={correctionItem}
        onBack={onCloseCorrection}
        corrector={me.data?.username ?? ""}
        onPrevious={() => {
          const previousId = previousCorrectionId(items, correctionItem.id)
          if (previousId) onOpenCorrection(previousId)
        }}
        hasPrevious={Boolean(previousCorrectionId(items, correctionItem.id))}
        onNext={() => {
          const nextId = nextPendingCorrectionId(items, correctionItem.id)
          if (nextId) onOpenCorrection(nextId)
        }}
        hasNext={Boolean(nextPendingCorrectionId(items, correctionItem.id))}
        correctionView={correctionViewQuery.data ?? null}
        correctionDraft={correctionDraft ?? undefined}
        onCorrectionChange={(nodeKey, patch) => {
          setCorrectionDraft((current) => current ? updateCorrectionDraft(current, nodeKey, patch) : current)
        }}
        onCorrectionSubmit={() => submitContractCorrection.mutate()}
        correctionSubmitPending={submitContractCorrection.isPending}
        correctionSubmitDisabled={correctionViewQuery.isLoading || correctionViewQuery.isError}
        correctionDisabled={correctionItem.evaluation?.review_stage === "completed"}
        onCorrected={async () => {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] }),
            queryClient.invalidateQueries({ queryKey: ["baseline-acceptance", run.id] }),
            queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
            queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
          ])
        }}
        onPreview={onPreview}
      >
        <div className="grid grid-cols-1 gap-4">
          <LevelExplanation item={correctionItem} />
          <div className="space-y-3">
            <p className="text-sm font-semibold">人工决策</p>
            <p className="text-xs leading-5 text-[var(--muted)]">提交后会停留在当前素材；可使用页面右上角的“上一条”和“下一条”手动切换。</p>
            {correctionItem.evaluation && (
              <div className="space-y-4 border-t border-[var(--line)] pt-4">
                <label>
                  <span className="mb-2 block text-xs font-semibold">人工说明（可选）</span>
                  <Input
                    value={reviewNotes[correctionItem.id] ?? ""}
                    disabled={correctionItem.evaluation.review_stage === "completed"}
                    placeholder="补充确认或退回依据"
                    onChange={(event) => setReviewNotes((current) => ({
                      ...current,
                      [correctionItem.id]: event.target.value,
                    }))}
                  />
                </label>
                {correctionItem.evaluation.review_stage === "completed" ? (
                  <div className="space-y-3 rounded-[4px] border border-[#ead7a5] bg-[#fff9ea] px-4 py-3">
                    <p className="text-sm font-semibold">人工结果已保存</p>
                    <p className="text-xs leading-5 text-[#6b4b0b]">
                      当前轮次已完成，{correctionItem.evaluation.human_review?.corrections?.length ?? 0} 处纠偏记录可回看。点击“再次修改”会保留本轮历史并创建新审核轮次。
                    </p>
                    {correctionItem.evaluation.human_review?.note && (
                      <p className="text-xs leading-5 text-[var(--muted)]">说明：{correctionItem.evaluation.human_review.note}</p>
                    )}
                    <Button
                      variant="secondary"
                      disabled={reopenReview.isPending}
                      onClick={() => reopenReview.mutate({ item: correctionItem })}
                    >
                      <PencilSimple />{reopenReview.isPending ? "正在创建新轮次" : "再次修改"}
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      disabled={reviewResult.isPending}
                      onClick={() => reviewResult.mutate({
                        item: correctionItem,
                        decision: "rejected",
                        note: reviewNotes[correctionItem.id]?.trim() ?? "",
                      })}
                    >
                      退回复核
                    </Button>
                    <Button
                      disabled={reviewResult.isPending}
                      onClick={() => reviewResult.mutate({
                        item: correctionItem,
                        decision: "approved",
                        note: reviewNotes[correctionItem.id]?.trim() ?? "",
                      })}
                    >
                      <Check weight="bold" />确认结果
                    </Button>
                  </div>
                )}
                {!correctionViewQuery.data && correctionItem.evaluation.scoring?.dimension_scoring_mode !== "rule_deduction" && (
                  <ReviewCorrectionForm
                    key={`${correctionItem.evaluation.id}-${correctionItem.evaluation.review_revision}`}
                    dimensions={correctionItem.evaluation.aesthetic?.dimensions ?? {}}
                    precheck={correctionItem.evaluation.precheck ?? {}}
                    dimensionSchema={correctionItem.evaluation.dimension_schema}
                    scoring={correctionItem.evaluation.scoring ?? {}}
                    pending={reviewResult.isPending}
                    editable={correctionItem.evaluation.review_stage !== "completed"}
                    initialCorrections={reopenSeeds[correctionItem.id]?.corrections ?? correctionItem.evaluation.human_review?.corrections ?? []}
                    initialNote={reopenSeeds[correctionItem.id]?.note ?? correctionItem.evaluation.human_review?.note ?? ""}
                    onSubmit={({ note, corrections }) => reviewResult.mutate({
                      item: correctionItem,
                      decision: "corrected",
                      note,
                      corrections,
                    })}
                  />
                )}
              </div>
            )}
          </div>
        </div>
      </CorrectionWorkbench>
    )
  }
  return (
    <>
      <section className="mt-6 grid gap-px border-y border-[var(--line)] bg-[var(--line)] md:grid-cols-2 xl:grid-cols-4">
        <SelectionFact
          label="本轮调用 A"
          value={run.selection.prompt_a
            ? `${run.selection.prompt_a.version} · ${run.selection.prompt_a.name}`
            : "历史 run 未记录"}
        />
        <SelectionFact
          label="本轮调用 B"
          value={run.selection.prompt_b
            ? `${run.selection.prompt_b.version} · ${run.selection.prompt_b.name}`
            : run.selection.prompt_a
              ? "单提示词模式（B 位不调用）"
              : "历史 run 未记录"}
        />
        <SelectionFact
          label="本轮维度版本"
          value={dimensionSelectionName(run.selection.dimension)}
        />
        <SelectionFact
          label="结果判定方式"
          value={run.selection.execution_mode === "structured" ? "标准评分合同" : "自由实验 · 无结构也可完成"}
        />
      </section>
      <div className="mt-4 flex justify-end">
        <Button variant="secondary" size="sm" onClick={onOpenMetrics}>查看字段证据</Button>
      </div>
      <div
        className="mt-6 flex gap-0 overflow-x-auto border-b border-[var(--line-strong)]"
        role="tablist"
        aria-label="基准回归工作区"
      >
        <button
          id="baseline-results-tab"
          type="button"
          role="tab"
          aria-controls="baseline-results-panel"
          aria-selected={activeView === "results"}
          className={`min-h-11 shrink-0 border-x border-t px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${activeView === "results" ? "border-[var(--line-strong)] bg-white" : "border-transparent bg-[#f3f5f0] text-[var(--muted)]"}`}
          onClick={() => setActiveView("results")}
        >
          回归结果
        </button>
        <button
          id="baseline-correction-tab"
          type="button"
          role="tab"
          aria-controls="baseline-correction-panel"
          aria-selected={activeView === "correction"}
          className={`min-h-11 shrink-0 border-x border-t px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${activeView === "correction" ? "border-[var(--line-strong)] bg-white" : "border-transparent bg-[#f3f5f0] text-[var(--muted)]"}`}
          onClick={() => setActiveView("correction")}
        >
          基准回归处理纠偏 · {availableDeviationIds.length}
        </button>
      </div>

      {activeView === "results" ? (
        <div id="baseline-results-panel" role="tabpanel" aria-labelledby="baseline-results-tab">
      <LevelPerformanceSummary metrics={metrics} />
      <section className="mt-6 grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="字段宏平均准确率" value={fieldMetrics ? percent(fieldMetrics.aggregates.macro.accuracy) : percent(metrics.exact_accuracy)} />
        <Metric label="字段宏平均召回率" value={fieldMetrics ? percent(fieldMetrics.aggregates.macro.recall) : "—"} />
        <Metric label="人工门禁状态" value={run.status === "running" ? "等待运行完成" : metrics.failed ? "先处理失败" : "等待人工确认"} />
        <Metric label="下一步" value={availableDeviationIds.length ? `纠偏 ${availableDeviationIds.length} 条` : "查看证据并决定"} />
      </section>

      {run.status === "running" && (
        <div className="mt-4 border border-[#d6dfb1] bg-[#f7fadf] px-4 py-3">
          <div className="flex items-center justify-between gap-4 text-xs font-semibold">
            <span>回归运行中，页面每 3 秒自动刷新</span>
            <span className="font-data">{run.completed}/{run.total}</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden bg-white">
            <div
              className="h-full bg-primary transition-[width] duration-300"
              style={{ width: `${run.total ? Math.round(run.completed / run.total * 100) : 0}%` }}
            />
          </div>
        </div>
      )}

      <section className="mt-7">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3 className="font-editorial text-2xl font-bold">逐张预测对照</h3>
              <p className="mt-1 text-xs text-[var(--muted)]">每张展示冻结评测理由，并可原位确认、纠偏或退回。</p>
              <p className="mt-2 max-w-3xl text-xs leading-5 text-[var(--muted)]">
                全局优化案例池用途（可选）：把偏差样本沉淀到后续自动组批和长期机制优化流程；不影响当前纠偏分析，不修改本轮真值，也不自动启用候选。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge>第 {page + 1}/{pageCount} 页 · 共 {pagination.total} 张</Badge>
              <Button
                variant="ghost"
                size="sm"
                disabled={page <= 0 || loading}
                onClick={() => onPageChange(Math.max(0, page - 1))}
              >
                上一页
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={page + 1 >= pageCount || loading}
                onClick={() => onPageChange(page + 1)}
              >
                下一页
              </Button>
              {availableDeviationIds.length > 0 && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setSelectedDeviationIds(
                        selectedDeviationIds.size === availableDeviationIds.length
                          ? new Set()
                          : new Set(availableDeviationIds),
                      )
                    }}
                  >
                    {selectedDeviationIds.size === availableDeviationIds.length
                      ? "取消本页偏差全选"
                      : "选择本页全部偏差"}
                  </Button>
                  <Button
                    size="sm"
                    disabled={!selectedDeviationIds.size || enqueueDeviations.isPending}
                    onClick={() => enqueueDeviations.mutate()}
                  >
                    {enqueueDeviations.isPending
                      ? "正在加入全局优化池"
                      : `加入全局优化池（可选） · ${selectedDeviationIds.size}`}
                  </Button>
                </>
              )}
            </div>
          </div>
          <div className="max-h-[620px] overflow-auto border-y border-[var(--line-strong)] bg-white">
            {loading ? (
              <div className="h-64 animate-pulse bg-white" />
            ) : items.length ? (
              <div className="divide-y divide-[var(--line)]">
                {items.map((item) => {
                  const fallback = gradedByFallback(item.stage_a)
                  const evaluation = item.evaluation
                  const humanStatus = reviewStatus(evaluation)
                  return (
                    <div
                      key={item.id}
                      className="bg-white"
                    >
                      <div className="grid gap-3 px-4 py-4 sm:grid-cols-[64px_minmax(0,1fr)_auto] sm:items-center">
                        <ImagePreviewButton
                          src={item.image_url}
                          alt={item.asset.name}
                          imageClassName="size-14"
                          onPreview={onPreview}
                        />
                        <div className="min-w-0">
                          <p className="file-name truncate text-sm">{item.asset.name}</p>
                          <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">
                            素材 #{item.asset_id} · 评测 #{item.evaluation_id ?? "—"} · 分数 {item.authoritative_score ?? "—"}
                          </p>
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--muted)]">
                            {levelExplanationSummary(item)}
                          </p>
                          {item.error_message && (
                            <p className="mt-1 text-xs text-[#8d2924]">失败原因：{baselineErrorMessage(item.error_message)}</p>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-2 sm:max-w-72 sm:justify-end">
                          {fallback && <Badge tone="warning">fallback 分级</Badge>}
                          {item.optimization_case_id !== null && (
                            <Badge tone="success">已入全局优化池</Badge>
                          )}
                          {humanStatus && (
                            <Badge tone={humanStatus.tone}>{humanStatus.label}</Badge>
                          )}
                          {item.status === "completed"
                            && item.deviation
                            && item.optimization_case_id === null && (
                            <button
                              type="button"
                              className="flex size-8 items-center justify-center rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                              aria-label={`${
                                selectedDeviationIds.has(item.id)
                                  ? "取消加入全局优化池"
                                  : "选择加入全局优化池"
                              }${item.asset.name}`}
                              onClick={() => {
                                setSelectedDeviationIds((current) => {
                                  const next = new Set(current)
                                  if (next.has(item.id)) next.delete(item.id)
                                  else next.add(item.id)
                                  return next
                                })
                              }}
                            >
                              {selectedDeviationIds.has(item.id)
                                ? <CheckSquare size={20} weight="fill" />
                                : <Square size={20} />}
                            </button>
                          )}
                          {item.status === "failed" ? (
                            <Badge tone="danger"><WarningCircle />失败</Badge>
                          ) : item.status === "queued" ? (
                            <Badge tone="active">等待预测</Badge>
                          ) : item.interpretation?.status === "manual_required" ? (
                            <Badge tone="warning">已完成 · 待人工判断</Badge>
                          ) : (
                            <Badge tone={item.deviation ? "danger" : "success"}>
                              预测 {item.predicted_level ?? "—"} / 期望 {item.expected_level}
                            </Badge>
                          )}
                          {evaluation && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => onOpenCorrection(item.id)}
                            >
                              <PencilSimple />
                              {evaluation.review_stage === "completed"
                                ? "查看人工标记"
                                : "确认或纠偏"}
                            </Button>
                          )}
                        </div>
                      </div>
                      <details className="border-t border-[var(--line)] bg-[#fafbf8]">
                        <summary className="cursor-pointer px-4 py-3 text-xs font-semibold">
                          展开完整评测理由
                        </summary>
                        <LevelExplanation item={item} />
                      </details>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="px-5 py-12 text-center text-sm text-[var(--muted)]">
                当前运行尚无逐张结果。
              </p>
            )}
          </div>
        </div>
      </section>
        </div>
      ) : (
        <CorrectionAnalysisPanel
          run={run}
          items={items}
          loading={loading}
          onPreview={onPreview}
          canDecide={me.data?.is_admin === true}
        />
      )}
    </>
  )
}

function CorrectionAnalysisPanel({
  run,
  items,
  loading,
  onPreview,
  canDecide,
}: {
  run: BaselineRegressionRun
  items: BaselineRegressionItem[]
  loading: boolean
  onPreview: (preview: ImagePreview) => void
  canDecide: boolean
}) {
  const queryClient = useQueryClient()
  const deviations = useMemo(
    () => items.filter((item) => item.status === "completed" && item.deviation),
    [items],
  )
  const deviationIds = useMemo(() => deviations.map((item) => item.id), [deviations])
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const correctionRuns = useQuery({
    queryKey: ["baseline-correction-runs", run.id],
    queryFn: () => baselineRegressionApi.listCorrectionRuns(run.id),
    refetchInterval: (query) => (
      query.state.data?.items.some((item) => item.status === "processing") ? 2500 : false
    ),
  })
  const latest = correctionRuns.data?.items[0]

  useEffect(() => {
    setSelectedIds(new Set(deviationIds))
  }, [run.id, deviationIds.join(",")])

  const createCorrection = useMutation({
    mutationFn: () => baselineRegressionApi.createCorrectionRun(
      run.id,
      Array.from(selectedIds),
      `baseline-correction-${run.id}-${Date.now()}`,
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["baseline-correction-runs", run.id] })
      toast.success("纠偏分析已启动；AI 将自动生成候选并执行回归")
    },
    onError: (error) => toast.error(error.message),
  })
  const retryCorrection = useMutation({
    mutationFn: (correctionRunId: number) => baselineRegressionApi.retryCorrectionRun(correctionRunId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["baseline-correction-runs", run.id] })
      toast.success("已重新启动纠偏分析")
    },
    onError: (error) => toast.error(error.message),
  })
  const decideCorrection = useMutation({
    mutationFn: ({
      correctionRunId,
      decision,
      note,
    }: {
      correctionRunId: number
      decision: "approved" | "rejected"
      note: string
    }) => baselineRegressionApi.decideCorrectionRun(correctionRunId, decision, note),
    onSuccess: async (result) => {
      const refreshPlan = result.mechanism_refresh
        ? candidateRefreshPlan(result.mechanism_refresh, run.id)
        : null
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-correction-runs", run.id] }),
        ...(refreshPlan?.invalidate ?? [
          ["evaluation-categories"],
          ["prompts", run.category_key],
          ["baseline-v3-revisions", run.category_key],
        ]).map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ])
      toast.success(result.status === "approved" ? "候选已启用" : "候选已拒绝")
    },
    onError: (error) => toast.error(error.message),
  })

  const allSelected = deviations.length > 0 && selectedIds.size === deviations.length
  const report = latest?.report ?? {}
  const promptSuggestions = recordArray(report.prompt_suggestions)
  const dimensionSuggestions = recordArray(report.dimension_suggestions)
  const risks = stringArray(report.risks)
  const candidateRegression = recordValue(report.candidate_regression)
  const baselineMetrics = recordValue(candidateRegression.baseline_metrics)
  const candidateMetrics = recordValue(candidateRegression.candidate_metrics)
  const regressions = recordArray(candidateRegression.regressions)
  const approvalAllowed = candidateRegression.approval_allowed === true
  const recommendation = stringValue(candidateRegression.recommendation)
  const blockers = (latest?.blockers ?? []).map((blocker) => (
    typeof blocker === "string" ? blocker : readableRecord(blocker)
  )).filter(Boolean)
  const latestLocked = latest?.status === "processing" || latest?.status === "awaiting_decision"

  const requestDecision = (decision: "approved" | "rejected") => {
    if (!latest || latest.status !== "awaiting_decision") return
    const approved = decision === "approved"
    const confirmed = window.confirm(
      approved
        ? "确认启用该等级规则候选？启用后会切换当前类目的现役提示词与等级规则版本，但不会发布标签事实。"
        : "确认拒绝该机制候选？本次候选与回归证据会保留，结论提交后不可修改。",
    )
    if (!confirmed) return
    decideCorrection.mutate({
      correctionRunId: latest.id,
      decision,
      note: approved ? "人工确认启用自动纠偏候选" : "人工确认拒绝自动纠偏候选",
    })
  }

  return (
    <section
      id="baseline-correction-panel"
      className="mt-6 border-y border-[var(--line-strong)] bg-white"
      role="tabpanel"
      aria-labelledby="baseline-correction-tab"
    >
      <div className="grid gap-5 border-b border-[var(--line)] px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-editorial text-2xl font-bold">基准回归处理纠偏</h3>
            <Badge tone="active">全自动候选流水线</Badge>
            <Badge tone="neutral">最终人工决策</Badge>
          </div>
          <p className="mt-2 max-w-3xl text-xs leading-5 text-[var(--muted)]">
            启动后，系统自动分析纠偏样本、生成并校验统一机制候选，再执行候选回归。中间无需人工配置；回归完成后只需决定启用或拒绝，系统不会自动启用候选。
          </p>
          <div className="mt-3 grid gap-2 text-xs leading-5 text-[var(--muted)] sm:grid-cols-2">
            <p className="border-l-2 border-[var(--line-strong)] pl-3">
              结果查看位置：存量回归 → 基准回归 → 处理纠偏（当前区域）。分析报告、候选机制、回归指标和风险提示都在这里展示。
            </p>
            <p className="border-l-2 border-primary pl-3">
              人工采纳位置：候选回归完成后仍在当前区域进入“等待人工决策”；只有系统管理员在这里点击“启用候选”或“拒绝候选”。
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            onClick={() => correctionRuns.refetch()}
            disabled={correctionRuns.isFetching}
          >
            <ArrowClockwise />刷新状态
          </Button>
          <Button
            onClick={() => createCorrection.mutate()}
            disabled={
              run.status === "running"
              || !selectedIds.size
              || createCorrection.isPending
              || latestLocked
            }
          >
            <Play weight="fill" />
            {createCorrection.isPending ? "正在启动" : `启动纠偏分析 (${selectedIds.size})`}
          </Button>
        </div>
      </div>

      <div className="grid min-w-0 lg:grid-cols-[minmax(320px,0.88fr)_minmax(0,1.12fr)]">
        <div className="min-w-0 border-b border-[var(--line)] lg:border-r lg:border-b-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-4 py-3">
            <div>
              <p className="text-sm font-bold">选择偏差样本</p>
              <p className="mt-1 text-xs text-[var(--muted)]">已选 {selectedIds.size} / {deviations.length}</p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              disabled={!deviations.length || latestLocked}
              onClick={() => setSelectedIds(allSelected ? new Set() : new Set(deviationIds))}
            >
              {allSelected ? <CheckSquare weight="fill" /> : <Square />}
              {allSelected ? "取消全选" : "全选偏差"}
            </Button>
          </div>
          <div className="max-h-[620px] overflow-auto">
            {loading ? (
              <div className="h-64 animate-pulse bg-white" />
            ) : deviations.length ? (
              <div className="divide-y divide-[var(--line)]">
                {deviations.map((item) => {
                  const levelDisplay = correctionLevelDisplay(item)
                  return (
                  <div key={item.id} className="grid grid-cols-[auto_52px_minmax(0,1fr)] gap-3 px-4 py-3 hover:bg-[#fafbf8]">
                    <input
                      type="checkbox"
                      aria-label={`选择偏差样本：${item.asset.name}`}
                      className="mt-4 size-4 accent-[#9dbb1c]"
                      checked={selectedIds.has(item.id)}
                      disabled={latestLocked}
                      onChange={(event) => setSelectedIds((current) => {
                        const next = new Set(current)
                        if (event.target.checked) next.add(item.id)
                        else next.delete(item.id)
                        return next
                      })}
                    />
                    <ImagePreviewButton
                      src={item.image_url}
                      alt={item.asset.name}
                      imageClassName="size-12"
                      onPreview={onPreview}
                    />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="file-name min-w-0 truncate text-sm">{item.asset.name}</p>
                        <Badge tone="danger">{levelDisplay.level} → {item.predicted_level ?? "—"}</Badge>
                        <Badge tone={levelDisplay.source === "human_correction" ? "warning" : "neutral"}>
                          {levelDisplay.source === "human_correction" ? "人工纠偏等级" : "冻结预期等级"}
                        </Badge>
                      </div>
                      {levelDisplay.source === "human_correction" && (
                        <p className="mt-1 text-[0.68rem] leading-4 text-[#7d4308]">
                          原冻结预期 {item.expected_level} · 当前展示以已完成人工纠偏为准
                        </p>
                      )}
                      {levelDisplay.source === "frozen_expected" && (
                        <p className="mt-1 text-[0.68rem] leading-4 text-[var(--muted)]">
                          尚未保存人工纠偏等级 · 当前仅用于筛选偏差样本
                        </p>
                      )}
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{levelExplanationSummary(item)}</p>
                    </div>
                  </div>
                  )
                })}
              </div>
            ) : (
              <p className="px-5 py-12 text-center text-sm text-[var(--muted)]">
                当前运行没有可分析的已完成偏差样本。
              </p>
            )}
          </div>
        </div>

        <div className="min-w-0 px-5 py-5">
          {!latest ? (
            <div className="border-y border-[var(--line)] px-4 py-12 text-center">
              <p className="text-sm font-bold">尚未启动纠偏分析</p>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">选择左侧偏差样本后启动，AI 将自动接管候选生成与回归，直到需要最终人工决策。</p>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line-strong)] pb-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="text-base font-bold">纠偏分析 #{latest.id}</h4>
                    <Badge tone={correctionStatusTone(latest)}>{correctionStatusName(latest)}</Badge>
                    <Badge>第 {latest.attempt_count} 次尝试</Badge>
                  </div>
                  <p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">
                    {latest.selected_item_ids.length} 个冻结样本 · {latest.updated_at}
                  </p>
                </div>
                {latest.status === "failed" && latest.error?.retryable && (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={retryCorrection.isPending}
                    onClick={() => retryCorrection.mutate(latest.id)}
                  >
                    <ArrowClockwise />{retryCorrection.isPending ? "正在重新执行" : "重新执行"}
                  </Button>
                )}
              </div>

              <div className="mt-4 grid grid-cols-2 border-y border-[var(--line)] bg-[#fafbf8] 2xl:grid-cols-5">
                {correctionStages.map((stage, index) => {
                  const state = correctionStageState(latest, stage.key)
                  return (
                    <div
                      key={stage.key}
                      className="min-w-0 border-r border-[var(--line)] px-3 py-3 last:border-r-0"
                    >
                      <div className="flex items-center gap-2">
                        <span className={`flex size-6 shrink-0 items-center justify-center rounded-full border text-[0.68rem] font-bold ${correctionStageClassName(state)}`}>
                          {state === "completed" ? <Check weight="bold" /> : index + 1}
                        </span>
                        <p className="text-xs font-bold leading-4">{stage.label}</p>
                      </div>
                      <p className="mt-1 pl-8 text-[0.68rem] leading-4 text-[var(--muted)]">
                        {correctionStageStateName(state)}
                      </p>
                    </div>
                  )
                })}
              </div>

              {latest.status === "processing" && (
                <div className="mt-4 border border-[#d6dfb1] bg-[#f7fadf] px-4 py-3">
                  <div className="flex justify-between gap-4 text-xs font-bold">
                    <span>{correctionStageRunningMessage(latest.stage)}</span>
                    <span className="font-data">{latest.progress}%</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden bg-white">
                    <div className="h-full bg-primary transition-[width] duration-300" style={{ width: `${latest.progress}%` }} />
                  </div>
                </div>
              )}

              {latest.status === "failed" && (
                <div className="mt-4 border border-[#e2b4af] bg-[#fff5f3] px-4 py-3 text-xs leading-5 text-[#8d2924]">
                  <p className="font-bold">{correctionStageLabel(latest.stage)}失败{latest.error?.code ? ` · ${latest.error.code}` : ""}</p>
                  <p className="mt-1">{latest.error?.message || "未返回具体失败原因，可重新执行本次冻结样本。"}</p>
                  <p className="mt-1 text-[#6f3935]">重新执行会沿用本次冻结样本，不需要补充任何配置。</p>
                </div>
              )}

              {(blockers.length > 0) && (
                <div className="mt-4 border border-[#e2c188] bg-[#fff9ea] px-4 py-3 text-xs leading-5 text-[#7d4308]">
                  <p className="font-bold">自动处理提示</p>
                  {blockers.map((blocker, index) => <p key={`${blocker}-${index}`} className="mt-1">{blocker}</p>)}
                </div>
              )}

              {(latest.status === "awaiting_decision"
                || latest.status === "approved"
                || latest.status === "rejected") && (
                <>
                  <div className="mt-5 grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
                    <Metric label="基准准确率" value={percent(numberValue(baselineMetrics.exact_accuracy) ?? run.metrics.exact_accuracy)} />
                    <Metric label="候选准确率" value={formatPercent(numberValue(candidateMetrics.exact_accuracy))} />
                    <Metric label="准确率变化" value={formatSignedPercent(numberValue(candidateRegression.exact_accuracy_delta))} />
                    <Metric label="相邻准确率变化" value={formatSignedPercent(numberValue(candidateRegression.adjacent_accuracy_delta))} />
                  </div>
                  <div className="mt-5 grid grid-cols-4 gap-px border-y border-[var(--line)] bg-[var(--line)]">
                    <ReportFact label="机制候选" value={latest.candidate_revision_id ? `Revision #${latest.candidate_revision_id}` : "—"} />
                    <ReportFact label="候选提示词" value={latest.orchestration.candidate_prompt?.version ?? "—"} />
                    <ReportFact label="候选回归" value={latest.regression_run_id ? `Run #${latest.regression_run_id}` : "—"} />
                    <ReportFact label="回归建议" value={recommendation === "approve" ? "建议启用" : "建议拒绝"} />
                  </div>
                  <div className="mt-6">
                    <h5 className="font-editorial text-xl font-bold">AI 分析与候选变更摘要</h5>
                    <p className="mt-1 text-xs text-[var(--muted)]">系统已把以下分析自动落入统一机制候选，并使用同一基准集完成验证。</p>
                    <div className="mt-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
                      {[...promptSuggestions, ...dimensionSuggestions].map((suggestion, index) => (
                        <div key={`${stringValue(suggestion.code) || stringValue(suggestion.dimension_key) || "suggestion"}-${index}`} className="grid gap-2 py-3 text-xs sm:grid-cols-[120px_minmax(0,1fr)]">
                          <span className="font-bold">
                            {stringValue(suggestion.dimension_key) || "提示词建议"}
                            {stringValue(suggestion.priority) ? ` · ${priorityName(stringValue(suggestion.priority))}` : ""}
                          </span>
                          <span className="leading-5 text-[var(--muted)]">{stringValue(suggestion.message) || readableRecord(suggestion)}</span>
                        </div>
                      ))}
                      {!promptSuggestions.length && !dimensionSuggestions.length && (
                        <p className="py-5 text-center text-xs text-[var(--muted)]">本次未形成可展示的改进建议。</p>
                      )}
                    </div>
                  </div>
                  {risks.length > 0 && (
                    <div className="mt-5 border border-[#e2c188] bg-[#fff9ea] px-4 py-3 text-xs leading-5 text-[#7d4308]">
                      <p className="font-bold">报告风险提示</p>
                      {risks.map((risk) => <p key={risk} className="mt-1">{risk}</p>)}
                    </div>
                  )}
                  {regressions.length > 0 && (
                    <div className="mt-5 border border-[#e2b4af] bg-[#fff5f3] px-4 py-3 text-xs leading-5 text-[#8d2924]">
                      <p className="font-bold">候选回归未通过</p>
                      {regressions.map((regression, index) => (
                        <p key={`${stringValue(regression.code)}-${index}`} className="mt-1">
                          {stringValue(regression.message) || readableRecord(regression)}
                        </p>
                      ))}
                    </div>
                  )}
                  {latest.status === "awaiting_decision" && canDecide && (
                    <div className="mt-5 border-l-2 border-primary bg-[#f8faed] px-4 py-4">
                      <div className="flex items-center justify-between gap-5">
                        <div>
                          <p className="text-sm font-bold">等待人工决策</p>
                          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                            中间步骤已全部完成。启用只切换机制发布轴；标签事实仍需通过独立发布流程。
                          </p>
                        </div>
                        <div className="flex shrink-0 gap-2">
                          <Button
                            variant="danger"
                            disabled={decideCorrection.isPending}
                            onClick={() => requestDecision("rejected")}
                          >
                            拒绝候选
                          </Button>
                          <Button
                            disabled={!approvalAllowed || decideCorrection.isPending}
                            title={approvalAllowed ? undefined : "候选回归未通过，不能启用"}
                            onClick={() => requestDecision("approved")}
                          >
                            <Check weight="bold" />启用候选
                          </Button>
                        </div>
                      </div>
                    </div>
                  )}
                  {latest.status === "awaiting_decision" && !canDecide && (
                    <div className="mt-5 border-l-2 border-[var(--line-strong)] bg-[#fafbf8] px-4 py-4">
                      <p className="text-sm font-bold">等待系统管理员决策</p>
                      <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                        候选与回归证据已就绪。只有系统管理员可以启用或拒绝机制候选。
                      </p>
                    </div>
                  )}
                  {(latest.status === "approved" || latest.status === "rejected") && (
                    <div className={`mt-5 border-l-2 px-4 py-4 ${latest.status === "approved" ? "border-primary bg-[#f8faed]" : "border-[#b7362e] bg-[#fff5f3]"}`}>
                      <p className="text-sm font-bold">
                        人工结论：{latest.status === "approved" ? "已启用候选" : "已拒绝候选"}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                        {latest.decided_by || "管理员"} · {latest.decided_at || latest.updated_at}
                        {latest.decision_note ? ` · ${latest.decision_note}` : ""}
                      </p>
                      <p className="mt-1 text-xs text-[var(--muted)]">该人工结论不可修改，候选、回归与决策证据均已保留。</p>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  )
}

const correctionStages: Array<{
  key: BaselineCorrectionRun["stage"]
  label: string
}> = [
  { key: "analysis", label: "自动分析纠偏样本" },
  { key: "candidate_generation", label: "生成统一机制候选" },
  { key: "candidate_validation", label: "校验并冻结候选" },
  { key: "regression", label: "执行候选回归" },
  { key: "decision", label: "等待人工决策" },
]

type CorrectionStageState = "pending" | "active" | "completed" | "failed"

function correctionStageState(
  run: BaselineCorrectionRun,
  stage: BaselineCorrectionRun["stage"],
): CorrectionStageState {
  const currentIndex = correctionStages.findIndex((item) => item.key === run.stage)
  const stageIndex = correctionStages.findIndex((item) => item.key === stage)
  if (run.status === "approved" || run.status === "rejected") return "completed"
  if (stageIndex < currentIndex) return "completed"
  if (stageIndex > currentIndex) return "pending"
  if (run.status === "failed") return "failed"
  return "active"
}

function correctionStageClassName(state: CorrectionStageState) {
  if (state === "completed") return "border-[#9dbb1c] bg-primary text-[#263000]"
  if (state === "active") return "border-[#9dbb1c] bg-[#f0f8c8] text-[#263000]"
  if (state === "failed") return "border-[#b7362e] bg-[#fff0ee] text-[#8d2924]"
  return "border-[var(--line-strong)] bg-white text-[var(--muted)]"
}

function correctionStageStateName(state: CorrectionStageState) {
  if (state === "completed") return "已完成"
  if (state === "active") return "进行中"
  if (state === "failed") return "执行失败"
  return "等待自动执行"
}

function correctionStageLabel(stage: BaselineCorrectionRun["stage"]) {
  return correctionStages.find((item) => item.key === stage)?.label ?? "自动纠偏"
}

function correctionStageRunningMessage(stage: BaselineCorrectionRun["stage"]) {
  if (stage === "analysis") return "AI 正在分析纠偏样本与偏差方向"
  if (stage === "candidate_generation") return "AI 正在生成统一机制候选"
  if (stage === "candidate_validation") return "系统正在校验并冻结候选版本"
  if (stage === "regression") return "系统正在执行候选回归"
  return "系统正在整理最终决策证据"
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function recordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(recordValue).filter((item) => Object.keys(item).length) : []
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function readableRecord(value: unknown): string {
  const record = recordValue(value)
  return stringValue(record.message) || stringValue(record.title) || stringValue(record.code)
}

function formatSignedPercent(value: number | null) {
  if (value === null) return "—"
  const sign = value > 0 ? "+" : ""
  return `${sign}${(value * 100).toFixed(1)}%`
}

function formatPercent(value: number | null) {
  return value === null ? "—" : percent(value)
}

function priorityName(priority: string) {
  if (priority === "high") return "高优先"
  if (priority === "medium") return "中优先"
  if (priority === "low") return "低优先"
  return priority
}

function dimensionSelectionName(selection: BaselineRegressionRun["selection"]["dimension"]) {
  if (selection.mode === "none" || selection.prompt_only) return "已关闭 · 仅提示词评级"
  const contract = selection.v3_contract
  if (!contract?.spec_version || !contract.tracks.length) return "未知版本"
  const trackNames: Record<string, string> = {
    class_one: "一类",
    class_two: "二类",
    class_three: "三类",
  }
  const dimensions = contract.tracks
    .map((track) => {
      const fallbackName = track.label.split("（", 1)[0]?.trim() || track.key
      return `${trackNames[track.key] ?? fallbackName}${track.dimension_count}维`
    })
    .join("/")
  return `${contract.spec_version} · ${dimensions}`
}

function correctionStatusName(run: BaselineCorrectionRun) {
  if (run.status === "processing") return correctionStageLabel(run.stage)
  if (run.status === "awaiting_decision") return "等待人工决策"
  if (run.status === "approved") return "已启用候选"
  if (run.status === "rejected") return "已拒绝候选"
  return "执行失败"
}

function correctionStatusTone(run: BaselineCorrectionRun): "active" | "success" | "danger" {
  if (run.status === "processing") return "active"
  if (run.status === "awaiting_decision" || run.status === "approved") return "success"
  return "danger"
}

function ReportFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-y border-[var(--line)] px-4 py-3">
      <p className="text-xs text-[var(--muted)]">{label}</p>
      <p className="font-data mt-1 text-lg font-bold">{value}</p>
    </div>
  )
}

function LevelExplanation({ item }: { item: BaselineRegressionItem }) {
  const explanation = item.level_explanation
  const interpretation = item.interpretation
  const labels = Object.fromEntries(
    item.evaluation?.dimension_schema.definition?.dimensions.map((dimension) => [
      dimension.key,
      dimension.label,
    ]) ?? [],
  )
  if (explanation.status === "unavailable_historical") {
    return (
      <p className="border-t border-[var(--line)] px-4 py-4 text-xs text-[var(--muted)]">
        {explanation.message ?? "历史结果未冻结评测理由"}
      </p>
    )
  }
  const dimensionRows = (explanation.all_dimensions.length
    ? explanation.all_dimensions
    : [
        ...explanation.strong_dimensions,
        ...explanation.weak_dimensions,
      ].filter(
        (dimension, index, all) =>
          all.findIndex((candidate) => candidate.key === dimension.key) === index,
      )
  ).map((dimension) => ({
    ...dimension,
    kind: dimension.grade >= 4
      ? "主要优势"
      : dimension.grade <= 2
        ? "主要短板"
        : "中性维度",
  }))
  return (
    <div className="border-t border-[var(--line)] px-4 py-4">
      {interpretation?.status === "manual_required" ? (
        <div className="border-l-2 border-[#c98a1f] bg-[#fff9ea] px-3 py-2 text-xs leading-5 text-[#7d4308]">
          <strong>自由输出已正常完成，等待人工判断。</strong>
          {interpretation.message ? ` ${interpretation.message}` : " 本次未形成可安全比较的 L1–L5 等级，因此不进入自动准确率分母。"}
        </div>
      ) : (
        <p className="text-sm font-semibold">
          服务端结论：{explanation.predicted_level ?? "未形成等级"} ·
          {" "}{explanation.authoritative_score ?? "无有效分数"} 分
        </p>
      )}
      {(interpretation?.raw_text_a || interpretation?.raw_text_b) && (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {interpretation.raw_text_a && (
            <RawModelOutput label="调用 A 原始输出" value={interpretation.raw_text_a} />
          )}
          {interpretation.raw_text_b && (
            <RawModelOutput label="调用 B 原始输出" value={interpretation.raw_text_b} />
          )}
        </div>
      )}
      {explanation.status === "out_of_scope" && (
        <p className="mt-2 text-xs text-[#8d2924]">素材超出评测范围，未形成正式美感等级。</p>
      )}
      {dimensionRows.length > 0 && (
        <div className="mt-4 divide-y divide-[var(--line)] border-y border-[var(--line)]">
          {dimensionRows.map((dimension) => (
            <div
              key={dimension.key}
              className="grid gap-2 py-3 text-xs sm:grid-cols-[88px_120px_minmax(0,1fr)]"
            >
              <span className="font-semibold text-[var(--muted)]">{dimension.kind}</span>
              <span className="font-semibold">
                {labels[dimension.key] ?? dimension.key} · {dimension.grade} 级
              </span>
              <span className="leading-5 text-[var(--muted)]">
                {[...dimension.evidence, ...dimension.defects].join("；") || "未返回可展示证据"}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-4 grid gap-3 border-y border-[var(--line)] py-3 text-xs sm:grid-cols-2">
        <p>
          <span className="font-semibold text-[var(--muted)]">模型置信度：</span>
          {item.confidence === null || item.confidence === undefined
            ? "未返回"
            : percent(item.confidence)}
        </p>
        <p>
          <span className="font-semibold text-[var(--muted)]">复核标记：</span>
          {item.needs_review === true
            ? "建议人工复核"
            : item.needs_review === false
              ? "未触发复核"
              : "未记录"}
        </p>
        <p>
          <span className="font-semibold text-[var(--muted)]">画质：</span>
          {explanation.image_quality.status === "available"
            ? `${explanation.image_quality.severity_label || explanation.image_quality.severity || "已返回"}${explanation.image_quality.confidence === null || explanation.image_quality.confidence === undefined ? "" : ` · ${percent(explanation.image_quality.confidence)} 置信度`}`
            : "未返回画质证据"}
        </p>
        <p>
          <span className="font-semibold text-[var(--muted)]">版本：</span>
          {[item.versions.model, item.versions.rubric, item.versions.engine]
            .filter(Boolean)
            .join(" · ") || "历史结果未记录"}
        </p>
      </div>
      {explanation.image_quality.evidence.length > 0 && (
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
          画质证据：{explanation.image_quality.evidence.join("；")}
        </p>
      )}
      {explanation.caps.length > 0 && (
        <p className="mt-3 text-xs leading-5 text-[#7d4308]">
          等级限制：{explanation.caps.map((cap) => {
            const level = typeof cap.cap === "string" ? cap.cap : ""
            const reason = typeof cap.reason === "string" ? cap.reason : "触发等级限制"
            return [level, reason].filter(Boolean).join(" · ")
          }).join("；")}
        </p>
      )}
      {explanation.review_reasons.length > 0 && (
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
          建议人工复核：{explanation.review_reasons.join("；")}
        </p>
      )}
    </div>
  )
}

function levelExplanationSummary(item: BaselineRegressionItem) {
  if (item.interpretation?.status === "manual_required") {
    return "自由输出已完整保存；未强制转换为八维或等级，等待人工判断"
  }
  const explanation = item.level_explanation
  if (explanation.status === "unavailable_historical") {
    return explanation.message ?? "历史结果未冻结评测理由"
  }
  if (explanation.status === "out_of_scope") {
    return "超出评测范围，未形成正式等级"
  }
  const weakest = explanation.weak_dimensions[0]
  const weakEvidence = weakest
    ? [...weakest.defects, ...weakest.evidence][0]
    : null
  const cap = explanation.caps[0]
  const capReason = cap && typeof cap.reason === "string" ? cap.reason : null
  return capReason
    ? `等级受限：${capReason}`
    : weakEvidence
      ? `主要短板：${weakEvidence}`
      : `服务端按 ${explanation.authoritative_score ?? "—"} 分判定为 ${explanation.predicted_level ?? "未定级"}`
}

function RawModelOutput({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-y border-[var(--line)] bg-white px-3 py-3">
      <p className="text-xs font-bold text-[var(--muted)]">{label}</p>
      <pre className="font-data mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-foreground">{value}</pre>
    </div>
  )
}

function baselineErrorMessage(error: string) {
  const labels: Record<string, string> = {
    missing_level: "未形成 L1-L5 有效等级",
    no_authoritative_score: "未形成服务端权威分数",
    missing_quality_evidence: "未返回画质证据",
    missing_confidence: "未返回模型置信度",
    missing_precheck_scope_status: "调用 A 未返回评测范围字段，无法继续调用 B",
    missing_prompt_b_response: "调用 B 未返回结果",
    missing_aesthetic_result: "未形成八个美感维度结果",
  }
  const [, reasons] = error.split(":", 2)
  if (!reasons) return error
  return reasons.split(",").map((reason) => labels[reason] ?? reason).join("；")
}

function reviewStatus(evaluation: BaselineRegressionItem["evaluation"]) {
  if (!evaluation) return null
  if (evaluation.review_panel && evaluation.review_panel.status !== "completed") {
    return {
      label: `审核中 ${evaluation.review_panel.submitted_count}/${evaluation.review_panel.required_reviewers}`,
      tone: "active" as const,
    }
  }
  const decision = evaluation.human_review?.decision
  if (decision === "approved") return { label: "已确认", tone: "success" as const }
  if (decision === "corrected") {
    return {
      label: `已纠偏 · 人工 ${evaluation.final_level ?? "维度"}`,
      tone: "warning" as const,
    }
  }
  if (decision === "rejected") return { label: "已退回", tone: "danger" as const }
  if (evaluation.review_panel) {
    return {
      label: `审核中 ${evaluation.review_panel.submitted_count}/${evaluation.review_panel.required_reviewers}`,
      tone: "active" as const,
    }
  }
  return { label: "待审核", tone: "neutral" as const }
}

function LevelSelect({
  label,
  value,
  onChange,
}: {
  label: string
  value: BaselineLevel
  onChange: (value: BaselineLevel) => void
}) {
  return (
    <label>
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      <select
        className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value as BaselineLevel)}
      >
        {levels.map((level) => (
          <option key={level} value={level}>{level} · {levelNames[level]}</option>
        ))}
      </select>
    </label>
  )
}

export function FieldMetricsEvidence({
  data,
  loading,
  error,
  levelMetrics,
}: {
  data?: BaselineFieldMetrics
  loading: boolean
  error: Error | null
  levelMetrics: BaselineRegressionRun["metrics"]
}) {
  if (loading) {
    return <div className="h-52 animate-pulse bg-[#f7f9ef]" />
  }
  if (error) {
    return (
      <div className="border border-[#d7a09d] bg-[#fff5f4] px-4 py-4 text-sm text-[#8d2924]">
        字段质量证据加载失败：{error.message}
      </div>
    )
  }
  if (!data) {
    return <p className="text-sm text-[var(--muted)]">当前轮次尚未形成字段证据。</p>
  }

  return (
    <div className="space-y-6">
      <section className="grid gap-px border-y border-[var(--line)] bg-[var(--line)] grid-cols-2 xl:grid-cols-4">
        <Metric label="宏平均准确率" value={percent(data.aggregates.macro.accuracy)} />
        <Metric label="宏平均召回率" value={percent(data.aggregates.macro.recall)} />
        <Metric label="微平均准确率" value={percent(data.aggregates.micro.accuracy)} />
        <Metric label="失败样本" value={String(data.failure_sample_ids.length)} />
      </section>

      <section className="border-y border-[var(--line-strong)] bg-white">
        <div className="grid grid-cols-[minmax(210px,1fr)_90px_110px_110px_100px] gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-4 py-3 text-xs font-semibold text-[var(--muted)]">
          <span>字段</span><span>支持数</span><span>准确率</span><span>召回率</span><span>失败数</span>
        </div>
        {data.field_metrics.map((item) => (
          <details
            key={item.field_key}
            className="border-b border-[var(--line)] last:border-0"
            data-testid={`baseline-field-metric-${item.field_key}`}
          >
            <summary className="grid cursor-pointer grid-cols-[minmax(210px,1fr)_90px_110px_110px_100px] gap-3 px-4 py-3 text-sm hover:bg-[#fbfcf5]">
              <span className="font-data break-all font-semibold">{fieldMetricLabel(item.field_key)}</span>
              <span className="font-data">{item.support}</span>
              <span className="font-data">{percent(item.accuracy)}</span>
              <span className="font-data">{percent(item.recall)}</span>
              <span className="font-data">{item.failure_sample_ids.length}</span>
            </summary>
            <div className="space-y-4 border-t border-[var(--line)] bg-[#fcfdf8] px-4 py-4">
              <div className="grid gap-3 grid-cols-3">
                <EvidenceMetric label="TP" value={item.tp} />
                <EvidenceMetric label="FP" value={item.fp} />
                <EvidenceMetric label="FN" value={item.fn} />
              </div>
              <div
                className="overflow-x-auto border border-[var(--line)] bg-white"
                data-testid={item.field_key === "level" ? "baseline-five-level-confusion-matrix" : undefined}
              >
                <table className="w-full min-w-[520px] border-collapse text-left text-xs">
                  <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8]"><th className="px-3 py-2">人工真值</th><th className="px-3 py-2">模型输出</th><th className="px-3 py-2">样本数</th></tr></thead>
                  <tbody>
                    {Object.entries(item.confusion_matrix).flatMap(([expected, predictions]) => (
                      Object.entries(predictions).map(([predicted, count]) => (
                        <tr key={`${expected}:${predicted}`} className="border-b border-[var(--line)] last:border-0">
                          <td className="font-data px-3 py-2">{fieldMetricValue(expected)}</td>
                          <td className="font-data px-3 py-2">{fieldMetricValue(predicted)}</td>
                          <td className="font-data px-3 py-2">{count}</td>
                        </tr>
                      ))
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs leading-5 text-[var(--muted)]">
                失败样本：{item.failure_sample_ids.length ? item.failure_sample_ids.map((id) => `#${id}`).join("、") : "无"}
              </p>
            </div>
          </details>
        ))}
      </section>

      <section className="grid gap-4 border-y border-[var(--line)] bg-[#f7f9ef] px-4 py-4 text-xs leading-5 grid-cols-2">
        <div>
          <p className="font-semibold">本轮版本</p>
          <p className="mt-1 text-[var(--muted)]">模型 {data.versions.model.join(" / ") || "未记录"}</p>
          <p className="text-[var(--muted)]">Prompt A {data.versions.prompt.a.join(" / ") || "未记录"}</p>
          <p className="text-[var(--muted)]">Prompt B {data.versions.prompt.b.join(" / ") || "未使用"}</p>
          <p className="text-[var(--muted)]">机制 {data.versions.mechanism.spec_version || "历史版本未记录"}</p>
        </div>
        <div>
          <p className="font-semibold">证据覆盖</p>
          <p className="mt-1 text-[var(--muted)]">素材 {data.versions.asset.count} 条 · 黄金真值匹配 {data.versions.truth.matched_asset_count} 条</p>
          <p className="text-[var(--muted)]">真值修订 V{data.versions.truth.revision_min}–V{data.versions.truth.revision_max}</p>
          <p className="text-[var(--muted)]">等级准确率 {percent(levelMetrics.exact_accuracy)} · 等级矩阵继续作为字段 level 的详细证据</p>
        </div>
      </section>

      <div className="border-l-2 border-primary bg-[#f7fadf] px-4 py-3 text-xs leading-5">
        指标仅作为人工决策证据，不会自动采纳或启用候选评测机制。人工需结合失败样本、黄金集门禁和回归结果另行决定。
      </div>
    </div>
  )
}

function EvidenceMetric({ label, value }: { label: string; value: number }) {
  return <div className="border border-[var(--line)] bg-white px-3 py-3"><p className="text-[var(--muted)]">{label}</p><p className="font-data mt-1 text-lg font-bold">{value}</p></div>
}

function fieldMetricLabel(fieldKey: string) {
  const labels: Record<string, string> = {
    level: "正式等级",
    scope_status: "范围判定",
    primary_category: "主类目",
    quality_severity: "画质严重度",
  }
  if (fieldKey.startsWith("dimensions.")) return `维度 · ${fieldKey.slice("dimensions.".length)}`
  return labels[fieldKey] ?? fieldKey
}

function fieldMetricValue(value: string) {
  if (value === "__missing__") return "缺失"
  if (value === "__empty__") return "空值"
  return value
}

function PromptSelect({
  label,
  value,
  options,
  published,
  disabled,
  onChange,
}: {
  label: string
  value: number
  options: PromptVersion[]
  published: PromptVersion | undefined
  disabled: boolean
  onChange: (value: number) => void
}) {
  return (
    <label>
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      <select
        className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm disabled:border-[var(--line)] disabled:bg-[#f1f3ef] disabled:text-[var(--muted)]"
        value={disabled ? (published?.id ?? "") : (value || "")}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {!published && disabled && (
          <option value="">当前发布版本未配置</option>
        )}
        {!options.length && !disabled && (
          <option value="">暂无可选版本</option>
        )}
        {options.map((prompt) => (
          <option key={prompt.id} value={prompt.id}>
            {prompt.version} · {prompt.name} · {promptStatusName(prompt.status)} · {promptScopeName(prompt.pipeline_scope)}
          </option>
        ))}
      </select>
    </label>
  )
}

function SelectionFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 truncate text-sm font-semibold" title={value}>
        {value}
      </p>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 text-2xl font-bold">{value}</p>
    </div>
  )
}

function promptStatusName(status: PromptVersion["status"]) {
  if (status === "published") return "已发布"
  if (status === "archived") return "已归档"
  return "草稿"
}

function promptScopeName(scope: PromptVersion["pipeline_scope"]) {
  if (scope === "baseline_regression") return "基准回归专用"
  if (scope === "full_pipeline") return "完整流水线专用"
  return "共用"
}

function formatCandidatePromptBindingIssue(
  stage: "A" | "B",
  resolution: CandidatePromptBindingResolution,
) {
  const reason = {
    missing: "不存在",
    stage: "阶段不匹配",
    archived: "已归档",
    pipeline_scope: "未开放基准回归",
  }[resolution.reason ?? "missing"]
  return `${stage} ${resolution.requestedVersion ?? "未绑定"}（${reason}）`
}

function percent(value: number) {
  return `${Math.round(value * 1000) / 10}%`
}

function statusName(run: BaselineRegressionRun) {
  if (run.status === "completed") return "已完成"
  if (run.status === "partial_failed") return "部分失败"
  if (run.status === "failed") return "失败"
  return `运行中 ${run.completed}/${run.total}`
}

function statusTone(run: BaselineRegressionRun): "neutral" | "active" | "warning" | "danger" | "success" {
  if (run.status === "completed") return "success"
  if (run.status === "partial_failed") return "warning"
  if (run.status === "failed") return "danger"
  return "active"
}

function gradedByFallback(stageA: Record<string, unknown>) {
  const nested = [stageA, stageA.classification, stageA.grading]
  return nested.some((value) => (
    value !== null
    && value !== undefined
    && typeof value === "object"
    && !Array.isArray(value)
    && "graded_by" in value
    && (value as { graded_by?: unknown }).graded_by === "fallback"
  ))
}
