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
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, baselineRegressionApi } from "@/lib/api"
import { submitReviewDecision } from "@/lib/review-submit"
import type {
  Asset,
  BaselineLevel,
  BaselineRegressionItem,
  BaselineRegressionRun,
  MaterialPackage,
  PromptVersion,
  ReviewCorrection,
  User,
} from "@/lib/types"
import { ReviewCorrectionForm } from "@/pages/review-correction-form"

const levels: BaselineLevel[] = ["L1", "L2", "L3", "L4", "L5"]
const levelNames: Record<BaselineLevel, string> = {
  L1: "好",
  L2: "中等",
  L3: "中差",
  L4: "极差",
  L5: "过滤",
}

export function BaselineRegressionPage() {
  const queryClient = useQueryClient()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<number>>(new Set())
  const [expectedByAsset, setExpectedByAsset] = useState<Record<number, BaselineLevel>>({})
  const [defaultLevel, setDefaultLevel] = useState<BaselineLevel>("L1")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [selectedPackageId, setSelectedPackageId] = useState(0)
  const [useWholePackage, setUseWholePackage] = useState(false)
  const [selectedSetId, setSelectedSetId] = useState(0)
  const [selectedRunId, setSelectedRunId] = useState(0)
  const [manualPromptSelection, setManualPromptSelection] = useState(false)
  const [selectedPromptAId, setSelectedPromptAId] = useState(0)
  const [selectedPromptBId, setSelectedPromptBId] = useState(0)

  const assets = useQuery({
    queryKey: ["baseline-assets", selectedPackageId],
    queryFn: () => baselineRegressionApi.listAssets(selectedPackageId || undefined),
  })
  const packages = useQuery({
    queryKey: ["baseline-packages"],
    queryFn: baselineRegressionApi.listPackages,
  })
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: baselineRegressionApi.listPrompts,
  })
  const baselineSets = useQuery({
    queryKey: ["baseline-sets"],
    queryFn: baselineRegressionApi.listSets,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.latest_run?.status === "running")
        ? 3000
        : false,
  })
  const selectedSet = useQuery({
    queryKey: ["baseline-set", selectedSetId],
    queryFn: () => baselineRegressionApi.getSet(selectedSetId),
    enabled: selectedSetId > 0,
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) => run.status === "running") ? 3000 : false,
  })
  const runDetail = useQuery({
    queryKey: ["baseline-regression", selectedRunId],
    queryFn: () => baselineRegressionApi.getRun(selectedRunId),
    enabled: selectedRunId > 0,
    refetchInterval: (query) =>
      query.state.data?.summary.status === "running" ? 3000 : false,
  })

  const promptAOptions = useMemo(
    () => (prompts.data?.items ?? []).filter(
      (prompt: PromptVersion) => prompt.stage === "A",
    ),
    [prompts.data?.items],
  )
  const promptBOptions = useMemo(
    () => (prompts.data?.items ?? []).filter(
      (prompt: PromptVersion) => prompt.stage === "B",
    ),
    [prompts.data?.items],
  )
  const publishedPromptA = promptAOptions.find(
    (prompt) => prompt.status === "published",
  )
  const publishedPromptB = promptBOptions.find(
    (prompt) => prompt.status === "published",
  )

  useEffect(() => {
    if (!selectedSetId && baselineSets.data?.items.length) {
      setSelectedSetId(baselineSets.data.items[0].id)
    }
  }, [baselineSets.data?.items, selectedSetId])

  useEffect(() => {
    const runs = selectedSet.data?.runs ?? []
    if (runs.length && !runs.some((run) => run.id === selectedRunId)) {
      setSelectedRunId(runs[0].id)
    }
    if (!runs.length) setSelectedRunId(0)
  }, [selectedRunId, selectedSet.data?.runs])

  useEffect(() => {
    if (runDetail.data?.summary.status !== "running") {
      queryClient.invalidateQueries({ queryKey: ["baseline-sets"] })
      if (selectedSetId) {
        queryClient.invalidateQueries({ queryKey: ["baseline-set", selectedSetId] })
      }
    }
  }, [queryClient, runDetail.data?.summary.status, selectedSetId])

  useEffect(() => {
    if (!manualPromptSelection) return
    if (
      !promptAOptions.some((prompt) => prompt.id === selectedPromptAId)
      && publishedPromptA
    ) {
      setSelectedPromptAId(publishedPromptA.id)
    }
    if (
      !promptBOptions.some((prompt) => prompt.id === selectedPromptBId)
      && publishedPromptB
    ) {
      setSelectedPromptBId(publishedPromptB.id)
    }
  }, [
    manualPromptSelection,
    promptAOptions,
    promptBOptions,
    publishedPromptA,
    publishedPromptB,
    selectedPromptAId,
    selectedPromptBId,
  ])

  const upload = useMutation({
    mutationFn: (files: File[]) => baselineRegressionApi.uploadAssets(files),
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

  const createRun = useMutation({
    mutationFn: () => baselineRegressionApi.createRun(
      selectedSetId,
      manualPromptSelection
        ? {
            prompt_a_id: selectedPromptAId,
            prompt_b_id: selectedPromptBId,
          }
        : {},
    ),
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
  const selectedRun = selectedSet.data?.runs.find((run) => run.id === selectedRunId)
  const summary = runDetail.data?.summary ?? selectedRun

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
  }

  return (
    <>
      <PageHeader
        index="03.6"
        title="基准回归"
        description="冻结素材与 L1–L5 期望等级，可使用当前发布版或手动指定 A/B 提示词版本重复运行；准确率、混淆矩阵和逐张偏差均来自服务端权威结果。"
        actions={
          <>
            <input
              ref={uploadRef}
              className="hidden"
              type="file"
              accept="image/jpeg,image/png,image/webp"
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
            <div className="grid gap-4 px-5 py-5 xl:grid-cols-[minmax(200px,1fr)_minmax(220px,1fr)_160px_auto] xl:items-end">
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
                  {allSelected ? "取消全选" : "全选当前素材"}
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
                已启用整包模式：系统先按文件名中的 L1–L5 / 好 / 中等 / 中差 / 极差 / 过滤预填；下方可逐张修改。未显示的素材同样由服务端解析，不受页面 1000 条预览上限影响。
              </div>
            )}
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
                              <img
                                src={asset.image_url}
                                alt=""
                                className="size-12 shrink-0 border border-[var(--line)] object-cover"
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
                <div className="mt-5 grid gap-3 border-y border-[var(--line)] bg-[#fafbf8] px-4 py-4 xl:grid-cols-[minmax(0,150px)_minmax(0,1fr)_minmax(0,1fr)] xl:items-end min-[1750px]:grid-cols-[minmax(0,150px)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <label>
                    <span className="mb-2 block text-xs font-semibold">提示词取值方式</span>
                    <select
                      className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                      value={manualPromptSelection ? "manual" : "published"}
                      onChange={(event) => setManualPromptSelection(
                        event.target.value === "manual",
                      )}
                    >
                      <option value="published">当前发布版本</option>
                      <option value="manual">手动选择版本</option>
                    </select>
                  </label>
                  <PromptSelect
                    label="调用 A"
                    value={selectedPromptAId}
                    options={promptAOptions}
                    published={publishedPromptA}
                    disabled={!manualPromptSelection}
                    onChange={setSelectedPromptAId}
                  />
                  <PromptSelect
                    label="调用 B"
                    value={selectedPromptBId}
                    options={promptBOptions}
                    published={publishedPromptB}
                    disabled={!manualPromptSelection}
                    onChange={setSelectedPromptBId}
                  />
                  <label>
                    <span className="mb-2 block text-xs font-semibold">维度版本</span>
                    <select
                      disabled
                      className="h-11 w-full rounded-[4px] border border-[var(--line)] bg-[#f1f3ef] px-3 text-sm text-[var(--muted)]"
                      value="strategy_snapshot"
                    >
                      <option value="strategy_snapshot">
                        跟随策略快照（手动选择已预留）
                      </option>
                    </select>
                  </label>
                  <Button
                    disabled={
                      createRun.isPending
                      || selectedSet.data.runs.some((run) => run.status === "running")
                      || (
                        manualPromptSelection
                        && (!selectedPromptAId || !selectedPromptBId)
                      )
                    }
                    onClick={() => createRun.mutate()}
                  >
                    <Play weight="fill" />
                    {createRun.isPending ? "正在启动" : "运行全量回归"}
                  </Button>
                </div>
                <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
                  {manualPromptSelection
                    ? "本轮会冻结所选 A/B 的完整内容，不会改变线上发布指针。"
                    : "本轮启动时自动冻结当时的已发布 A/B；以后发布新版本也不会改写历史 run。"}
                </p>
              </div>

              <div className="mt-5 flex gap-2 overflow-x-auto pb-2">
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

              {summary && (
                <RegressionResults
                  run={summary}
                  items={runDetail.data?.items ?? []}
                  loading={runDetail.isLoading}
                />
              )}
            </section>
          ) : !selectedSet.isLoading && (
            <section className="mt-10 border-y border-[var(--line)] px-5 py-12 text-center text-sm text-[var(--muted)]">
              选择一个已冻结基准集后可启动回归并查看结果。
            </section>
          )}
        </main>
      </div>
    </>
  )
}

function RegressionResults({
  run,
  items,
  loading,
}: {
  run: BaselineRegressionRun
  items: BaselineRegressionItem[]
  loading: boolean
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
  const [reviewingItemId, setReviewingItemId] = useState<number | null>(null)
  const [reviewNotes, setReviewNotes] = useState<Record<number, string>>({})

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
          ? `已将 ${result.created} 张偏差样本加入找补队列`
          : "所选偏差样本已在找补队列中",
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
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["baseline-regression", run.id],
        }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["optimization-cases"] }),
      ])
      setReviewNotes((current) => ({ ...current, [variables.item.id]: "" }))
      setReviewingItemId(null)
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

  const metrics = run.metrics
  return (
    <>
      <section className="mt-6 grid gap-px border-y border-[var(--line)] bg-[var(--line)] md:grid-cols-3">
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
            : "历史 run 未记录"}
        />
        <SelectionFact
          label="本轮维度版本"
          value={
            run.selection.dimension.schemas.length
              ? run.selection.dimension.schemas
                .map((schema) => schema.version ?? schema.schema_key ?? "未知")
                .join(" / ")
              : "跟随旧版策略快照"
          }
        />
      </section>
      <section className="mt-6 grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="总体准确率" value={percent(metrics.exact_accuracy)} />
        <Metric label="相邻等级准确率" value={percent(metrics.adjacent_accuracy)} />
        <Metric label="运行进度" value={`${metrics.completed}/${metrics.total}`} />
        <Metric label="偏差 / 失败" value={`${metrics.deviations} / ${metrics.failed}`} />
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

      <section className="mt-7 grid gap-7 min-[1750px]:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <div className="min-w-0">
          <div className="mb-3">
            <h3 className="font-editorial text-2xl font-bold">L1–L5 混淆矩阵</h3>
            <p className="mt-1 text-xs text-[var(--muted)]">行 = 期望等级，列 = 模型预测等级。</p>
          </div>
          <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white">
            <table className="w-full min-w-[430px] border-collapse text-center text-sm">
              <thead>
                <tr className="border-b border-[var(--line)] bg-[#fafbf8]">
                  <th className="px-3 py-3 text-left text-xs text-[var(--muted)]">期望 \ 预测</th>
                  {levels.map((level) => <th key={level} className="font-data px-3 py-3">{level}</th>)}
                </tr>
              </thead>
              <tbody>
                {levels.map((expected) => (
                  <tr key={expected} className="border-b border-[var(--line)] last:border-0">
                    <th className="font-data px-3 py-3 text-left">{expected}</th>
                    {levels.map((predicted) => {
                      const count = metrics.confusion_matrix[expected]?.[predicted] ?? 0
                      return (
                        <td
                          key={predicted}
                          className={`font-data px-3 py-3 text-base ${
                            expected === predicted && count ? "bg-[#eff7cb] font-bold" : ""
                          }`}
                        >
                          {count}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3 className="font-editorial text-2xl font-bold">逐张预测对照</h3>
              <p className="mt-1 text-xs text-[var(--muted)]">每张展示冻结评测理由，并可原位确认、纠偏或退回。</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{items.length} 张</Badge>
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
                      ? "取消偏差全选"
                      : "选择全部偏差"}
                  </Button>
                  <Button
                    size="sm"
                    disabled={!selectedDeviationIds.size || enqueueDeviations.isPending}
                    onClick={() => enqueueDeviations.mutate()}
                  >
                    {enqueueDeviations.isPending
                      ? "正在加入找补"
                      : `加入找补队列 (${selectedDeviationIds.size})`}
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
                  const reviewing = reviewingItemId === item.id
                  const evaluation = item.evaluation
                  const humanStatus = reviewStatus(evaluation)
                  return (
                    <div
                      key={item.id}
                      className="bg-white"
                    >
                      <div className="grid gap-3 px-4 py-4 sm:grid-cols-[64px_minmax(0,1fr)_auto] sm:items-center">
                        <img src={item.image_url} alt="" className="size-14 border border-[var(--line)] object-cover" />
                        <div className="min-w-0">
                          <p className="file-name truncate text-sm">{item.asset.name}</p>
                          <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">
                            素材 #{item.asset_id} · 评测 #{item.evaluation_id ?? "—"} · 分数 {item.authoritative_score ?? "—"}
                          </p>
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--muted)]">
                            {levelExplanationSummary(item)}
                          </p>
                          {item.error_message && (
                            <p className="mt-1 text-xs text-[#8d2924]">{item.error_message}</p>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-2 sm:max-w-72 sm:justify-end">
                          {fallback && <Badge tone="warning">fallback 分级</Badge>}
                          {item.optimization_case_id !== null && (
                            <Badge tone="success">已入找补队列</Badge>
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
                                  ? "取消找补"
                                  : "选择找补"
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
                          ) : (
                            <Badge tone={item.deviation ? "danger" : "success"}>
                              预测 {item.predicted_level ?? "—"} / 期望 {item.expected_level}
                            </Badge>
                          )}
                          {evaluation && (
                            <Button
                              size="sm"
                              variant={reviewing ? "secondary" : "ghost"}
                              onClick={() => setReviewingItemId(
                                reviewing ? null : item.id,
                              )}
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
                      {reviewing && evaluation && (
                        <div className="border-t border-[var(--line-strong)] bg-white">
                          <div className="grid gap-4 px-5 py-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
                            <label>
                              <span className="mb-2 block text-xs font-semibold">人工说明（可选）</span>
                              <Input
                                value={reviewNotes[item.id] ?? ""}
                                disabled={evaluation.review_stage === "completed"}
                                placeholder="补充确认或退回依据"
                                onChange={(event) => setReviewNotes((current) => ({
                                  ...current,
                                  [item.id]: event.target.value,
                                }))}
                              />
                            </label>
                            {evaluation.review_stage !== "completed" && (
                              <div className="flex flex-wrap gap-2">
                                <Button
                                  variant="secondary"
                                  disabled={reviewResult.isPending}
                                  onClick={() => reviewResult.mutate({
                                    item,
                                    decision: "rejected",
                                    note: reviewNotes[item.id]?.trim() ?? "",
                                  })}
                                >
                                  退回复核
                                </Button>
                                <Button
                                  disabled={reviewResult.isPending}
                                  onClick={() => reviewResult.mutate({
                                    item,
                                    decision: "approved",
                                    note: reviewNotes[item.id]?.trim() ?? "",
                                  })}
                                >
                                  <Check weight="bold" />确认结果
                                </Button>
                              </div>
                            )}
                          </div>
                          <ReviewCorrectionForm
                            key={`${evaluation.id}-${evaluation.review_revision}`}
                            dimensions={evaluation.aesthetic?.dimensions ?? {}}
                            dimensionSchema={evaluation.dimension_schema}
                            scoring={evaluation.scoring ?? {}}
                            pending={reviewResult.isPending}
                            editable={evaluation.review_stage !== "completed"}
                            onSubmit={({ note, corrections }) => reviewResult.mutate({
                              item,
                              decision: "corrected",
                              note,
                              corrections,
                            })}
                          />
                        </div>
                      )}
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
    </>
  )
}

function LevelExplanation({ item }: { item: BaselineRegressionItem }) {
  const explanation = item.level_explanation
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
  const dimensionRows = [
    ...explanation.strong_dimensions.map((dimension) => ({
      ...dimension,
      kind: "主要优势",
    })),
    ...explanation.weak_dimensions.map((dimension) => ({
      ...dimension,
      kind: "主要短板",
    })),
  ].filter(
    (dimension, index, all) =>
      all.findIndex((candidate) => candidate.key === dimension.key) === index,
  )
  return (
    <div className="border-t border-[var(--line)] px-4 py-4">
      <p className="text-sm font-semibold">
        服务端结论：{explanation.predicted_level ?? "未形成等级"} ·
        {" "}{explanation.authoritative_score ?? "无有效分数"} 分
      </p>
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

function reviewStatus(evaluation: BaselineRegressionItem["evaluation"]) {
  if (!evaluation) return null
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
            {prompt.version} · {prompt.name} · {promptStatusName(prompt.status)}
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
