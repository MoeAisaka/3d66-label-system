import { useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowClockwise,
  ArrowsClockwise,
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
import { ApiError, api, baselineRegressionApi } from "@/lib/api"
import { submitReviewDecision } from "@/lib/review-submit"
import type {
  Asset,
  BalancedRebuildStrategy,
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
  v3CandidateLineage,
  v3RevisionGroup,
} from "@/features/baseline-regression/baseline-regression-contract"
import type { CandidatePromptBindingResolution } from "@/features/baseline-regression/baseline-regression-contract"
import {
  BalancedRebuildDrawer,
  BalancedRebuildForm,
} from "@/features/baseline-regression/balanced-rebuild-drawer"
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
import {
  CandidateGateRejection,
  CandidateRebasePanel,
} from "@/features/baseline-regression/candidate-release-panels"
import {
  RuleDiagnosticsDrawer,
  RuleDiagnosticsEvidence,
} from "@/features/baseline-regression/rule-diagnostics-evidence"
import { RunConfigDrawer } from "@/features/baseline-regression/run-config-drawer"
import { RunHistoryDrawer } from "@/features/baseline-regression/run-history-drawer"
import { FieldMetricsEvidence } from "@/features/baseline-regression/field-metrics-evidence"
import { LevelSelect, PromptSelect } from "@/features/baseline-regression/form-selects"
import { RegressionResults } from "@/features/baseline-regression/regression-results"
import { levels } from "@/features/baseline-regression/regression-page-shared"

const ASSET_PAGE_SIZE = 200
const RUN_PAGE_SIZE = 200
const ACCEPTANCE_PAGE_SIZE = 1000

export function BaselineRegressionPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const uploadRef = useRef<HTMLInputElement>(null)
  const appliedBindingRevisionRef = useRef<number>(0)
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
  const [balancedRebuildDrawerOpen, setBalancedRebuildDrawerOpen] = useState(false)
  const [rebuildPerLevel, setRebuildPerLevel] = useState(20)
  const [rebuildStrategy, setRebuildStrategy] = useState<BalancedRebuildStrategy>("stable_hash")
  const [rebuildSeed, setRebuildSeed] = useState(1)
  const [runConfigDrawerOpen, setRunConfigDrawerOpen] = useState(false)
  const [metricsDrawerOpen, setMetricsDrawerOpen] = useState(false)
  const [ruleDiagnosticsDrawerOpen, setRuleDiagnosticsDrawerOpen] = useState(false)
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
  const ruleDiagnostics = useQuery({
    queryKey: ["baseline-rule-diagnostics", selectedRunId],
    queryFn: () => baselineRegressionApi.getRuleDiagnostics(selectedRunId),
    enabled: selectedRunId > 0 && ruleDiagnosticsDrawerOpen,
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
  const explicitV3Revision = v3Revisions.data?.items.find(
    (revision) => revision.id === selectedV3RevisionId,
  )
  const selectedV3Revision = explicitV3Revision ?? activeV3Revision
  const selectableV3Candidates = (v3Revisions.data?.items ?? []).filter((revision) => (
    isSelectableV3Candidate(
      revision,
      v3Revisions.data?.items ?? [],
      v3Revisions.data?.projected_revision_id ?? 0,
    )
  ))
  // Bindings must resolve from the explicitly chosen candidate. Falling back to
  // the active revision here compared the operator's A/B against V8's bindings
  // and reported a phantom mismatch.
  const candidateBindingRevision = v3SelectionMode === "candidate"
    && explicitV3Revision
    && explicitV3Revision.id !== (v3Revisions.data?.projected_revision_id ?? 0)
    ? explicitV3Revision
    : null
  const candidatePromptBindingA = candidateBindingRevision
    ? resolveV3PromptBinding(candidateBindingRevision, "A")
    : null
  const candidatePromptBindingB = candidateBindingRevision
    ? resolveV3PromptBinding(candidateBindingRevision, "B")
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
      : publishedPromptA?.id ?? promptAOptions[0]?.id ?? 0
  const effectivePromptBId = promptSelectionMode === "single"
    ? 0
    : promptSelectionMode === "manual"
      ? promptBOptions.some((prompt) => prompt.id === selectedPromptBId)
        ? selectedPromptBId
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
  const selectedV3Lineage = candidateBindingRevision
    ? v3CandidateLineage(
      candidateBindingRevision,
      v3Revisions.data?.items ?? [],
      v3Revisions.data?.projected_revision_id ?? 0,
    )
    : null
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

  // Bindings are a starting suggestion, not a lock: apply them once per candidate
  // selection so a later manual A/B change is never overwritten.
  useEffect(() => {
    if (!candidateBindingRevision) {
      appliedBindingRevisionRef.current = 0
      return
    }
    if (appliedBindingRevisionRef.current === candidateBindingRevision.id) return
    appliedBindingRevisionRef.current = candidateBindingRevision.id
    if (
      candidatePromptBindingA
      && candidatePromptResolutionA.status === "available"
      && candidatePromptResolutionA.promptId
    ) {
      setSelectedPromptAId(candidatePromptResolutionA.promptId)
    }
    if (
      candidatePromptBindingB
      && candidatePromptResolutionB.status === "available"
      && candidatePromptResolutionB.promptId
    ) {
      setSelectedPromptBId(candidatePromptResolutionB.promptId)
    }
    setPromptSelectionMode("manual")
  }, [
    candidateBindingRevision,
    candidatePromptBindingA,
    candidatePromptBindingB,
    candidatePromptResolutionA,
    candidatePromptResolutionB,
  ])

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
    onSuccess: async ({ summary, idempotent, item_count }) => {
      setSelectedSetId(summary.id)
      setSelectedRunId(0)
      await queryClient.invalidateQueries({ queryKey: ["baseline-sets"] })
      // 这个接口是幂等的：集合建过一次之后，再点只是切换选中。说清楚"已切换、
      // 什么都没新建"，否则界面看不出变化会被当成没生效。
      if (idempotent) {
        const frozenAt = new Date(summary.created_at).toLocaleDateString("zh-CN")
        toast.success(
          `已切换到均衡基准集（${item_count} 张，${frozenAt} 冻结）`,
          { description: "这份样本已存在，本次没有新建。要纳入新标注素材请用「重建均衡样本」。" },
        )
      } else {
        toast.success(`均衡基准集已冻结（${item_count} 张）`)
      }
    },
    onError: (error) => toast.error(error.message),
  })

  const rebuildSurvey = useQuery({
    queryKey: ["baseline-sets", "balanced-rebuild-survey"],
    queryFn: () => baselineRegressionApi.surveyBalancedRebuild(),
    enabled: balancedRebuildDrawerOpen && selectedCategoryKey === "inspiration_image",
  })

  const rebuildBalanced = useMutation({
    mutationFn: () => baselineRegressionApi.rebuildBalancedSample({
      per_level: rebuildPerLevel,
      strategy: rebuildStrategy,
      seed: rebuildSeed,
    }),
    onSuccess: async (result) => {
      setSelectedSetId(result.summary.id)
      setSelectedRunId(0)
      setBalancedRebuildDrawerOpen(false)
      await queryClient.invalidateQueries({ queryKey: ["baseline-sets"] })
      if (result.idempotent) {
        toast.success(
          `这套参数的样本已存在，已切换（${result.item_count} 张）`,
          { description: "同一批素材配同一组参数抽出的是同一份清单。换一个随机种子才会得到新的抽样。" },
        )
      } else {
        toast.success(
          `新均衡样本已冻结（${result.item_count} 张）`,
          { description: `其中 ${result.coverage.new_asset_count} 张是原样本没有的素材；原样本与它的历史回归未被改动。` },
        )
      }
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
  // 只取消当前这一轮。评测进度页的「取消全部」没有 run 作用域，
  // 会把当时所有回归一并判失败，运营需要的是这个按 run 的入口。
  const cancelRun = useMutation({
    mutationFn: () => baselineRegressionApi.cancelRun(selectedRunId),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-sets"] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-set", selectedSetId] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-regression", selectedRunId] }),
      ])
      toast.success(`已取消本轮，共 ${result.affected} 个未完成任务`)
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
            {/* 这个接口幂等：首次点会冻结，之后只切换。文案说"切换"而不是"生成"，
                因为叫"生成"会让人以为每次都重抽一份，看不到变化就以为坏了。 */}
            {selectedCategoryKey === "inspiration_image" && <Button
              variant="secondary"
              title="L1-L5 各 20 张的既有均衡样本；已冻结，点击只切换到它，不会重新抽样"
              onClick={() => createBalanced100.mutate()}
              disabled={createBalanced100.isPending}
            >
              <CheckSquare />{createBalanced100.isPending ? "正在校验" : "切换到均衡基准集"}
            </Button>}
            {selectedCategoryKey === "inspiration_image" && <Button
              variant="secondary"
              title="用当前全部人工评级素材重新抽样，冻结为新的基准集；原样本与其历史回归不受影响"
              onClick={() => setBalancedRebuildDrawerOpen(true)}
            >
              <ArrowsClockwise />重建均衡样本
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
              onClick={() => setRuleDiagnosticsDrawerOpen(true)}
              disabled={!selectedRunId}
            >
              规则命中诊断
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
                      {selectedV3Revision && <div className="border border-[var(--line)] bg-[#fafbf8] px-3 py-3 text-xs leading-5"><p className="font-semibold">{selectedV3Revision.display_name} · Revision {selectedV3Revision.revision}</p><p className="text-[var(--muted)]">状态：{selectedV3Revision.status} · Hash {selectedV3Revision.contract_hash.slice(0, 12)}</p>{selectedV3Lineage === "diverged" && <p className="text-[var(--muted)]">该候选与当前现役已分叉：可正常回归做实验，但<strong>启用会被拒绝</strong>，因为直接启用会丢弃现役版本引入的改动。</p>}</div>}
                      {selectedV3Lineage === "diverged" && selectedV3Revision && activeV3Revision && (
                        <CandidateRebasePanel
                          categoryKey={selectedCategoryKey}
                          candidate={selectedV3Revision}
                          activeRevision={activeV3Revision}
                          onRebased={(created) => setSelectedV3RevisionId(created.id)}
                        />
                      )}
                      {candidatePromptUnavailable && <div className="border border-[var(--line)] bg-[#fffaf0] px-3 py-3 text-xs leading-5"><p className="font-semibold">候选原绑定 Prompt 已不可用</p><p className="text-[var(--muted)]">{candidatePromptBindingA && candidatePromptResolutionA.status !== "available" ? formatCandidatePromptBindingIssue("A", candidatePromptResolutionA) : ""}{candidatePromptBindingB && candidatePromptResolutionB.status !== "available" ? `，${formatCandidatePromptBindingIssue("B", candidatePromptResolutionB)}` : ""}。已改用当前选中的 A/B，本轮实际版本会如实写入冻结快照。</p></div>}
                      {promptBindingMismatch && !candidatePromptUnavailable && <div className="border border-[var(--line)] bg-[#fffaf0] px-3 py-3 text-xs leading-5"><p className="font-semibold">A/B 与候选原绑定版本不同</p><p className="text-[var(--muted)]">原绑定 {candidatePromptBindingA ? `A ${candidatePromptBindingA}` : ""}{candidatePromptBindingB ? `，B ${candidatePromptBindingB}` : ""}。可以按当前选择直接启动，本轮以实际选中的 A/B 冻结。</p></div>}
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
                {summary.status === "running" && (
                  <div className="mt-5 border-l-2 border-[var(--muted)] bg-[#f7f7f5] px-4 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-bold">本轮回归进行中</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                          {summary.completed}/{summary.total} 已完成。只想停这一轮就用右侧按钮；
                          评测进度页的「取消全部」会把当时所有回归一并取消。
                        </p>
                      </div>
                      <Button
                        variant="secondary"
                        disabled={cancelRun.isPending}
                        onClick={() => {
                          if (
                            window.confirm(
                              `确定取消本轮回归（#${summary.id}）吗？\n\n` +
                                `已完成的 ${summary.completed} 条结果会保留，未完成的任务会判失败，取消后不可继续。\n` +
                                `其他正在进行的回归不受影响。`,
                            )
                          ) {
                            cancelRun.mutate()
                          }
                        }}
                        data-testid="baseline-cancel-run"
                      >
                        {cancelRun.isPending ? "取消中…" : "取消本轮"}
                      </Button>
                    </div>
                  </div>
                )}
                {candidateRevisionFromRun && summary.status === "completed" && me.data?.is_admin && (
                  <div className="mt-5 border-l-2 border-primary bg-[#f8faed] px-4 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
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
                    {/* 门禁拒绝时把每条阻塞原因摊开：只弹一句 toast 会把可行动的信息全丢掉。 */}
                    <CandidateGateRejection
                      error={activateCandidate.error instanceof ApiError ? activateCandidate.error : null}
                    />
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
      <RuleDiagnosticsDrawer
        open={ruleDiagnosticsDrawerOpen}
        onOpenChange={setRuleDiagnosticsDrawerOpen}
      >
        <RuleDiagnosticsEvidence
          data={ruleDiagnostics.data}
          loading={ruleDiagnostics.isLoading}
          error={ruleDiagnostics.error}
        />
      </RuleDiagnosticsDrawer>
      <SemanticQualityDrawer
        open={semanticQualityDrawerOpen}
        onOpenChange={setSemanticQualityDrawerOpen}
        data={semanticMetrics.data}
        loading={semanticMetrics.isLoading}
        error={semanticMetrics.error}
      />
      <BalancedRebuildDrawer
        open={balancedRebuildDrawerOpen}
        onOpenChange={setBalancedRebuildDrawerOpen}
        footer={
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-[var(--muted)]">
              冻结后会自动切换到新样本；原样本与其历史回归保持不变。
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                onClick={() => setBalancedRebuildDrawerOpen(false)}
                disabled={rebuildBalanced.isPending}
              >
                取消
              </Button>
              <Button
                onClick={() => rebuildBalanced.mutate()}
                disabled={
                  rebuildBalanced.isPending
                  || !rebuildSurvey.data
                  || rebuildPerLevel < 1
                  || rebuildPerLevel > (rebuildSurvey.data?.max_per_level ?? 0)
                }
              >
                <ArrowsClockwise />
                {rebuildBalanced.isPending
                  ? "正在冻结"
                  : `冻结 ${rebuildPerLevel * 5} 张新样本`}
              </Button>
            </div>
          </div>
        }
      >
        <BalancedRebuildForm
          survey={rebuildSurvey.data}
          loading={rebuildSurvey.isLoading}
          error={rebuildSurvey.error}
          perLevel={rebuildPerLevel}
          strategy={rebuildStrategy}
          seed={rebuildSeed}
          onPerLevel={setRebuildPerLevel}
          onStrategy={setRebuildStrategy}
          onSeed={setRebuildSeed}
        />
      </BalancedRebuildDrawer>
    </>
  )
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

