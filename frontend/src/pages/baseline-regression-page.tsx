import { useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowClockwise,
  CheckSquare,
  CloudArrowUp,
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
import { baselineRegressionApi } from "@/lib/api"
import type {
  Asset,
  BaselineLevel,
  BaselineRegressionItem,
  BaselineRegressionRun,
  MaterialPackage,
} from "@/lib/types"

const levels: BaselineLevel[] = ["L1", "L2", "L3", "L4", "L5"]

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

  const assets = useQuery({
    queryKey: ["baseline-assets", selectedPackageId],
    queryFn: () => baselineRegressionApi.listAssets(selectedPackageId || undefined),
  })
  const packages = useQuery({
    queryKey: ["baseline-packages"],
    queryFn: baselineRegressionApi.listPackages,
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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-assets"] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-packages"] }),
        queryClient.invalidateQueries({ queryKey: ["material-packages"] }),
      ])
      toast.success(`已上传并选中素材包“${uploadedPackage.name}”（${items.length} 张）`)
    },
    onError: (error) => toast.error(error.message),
  })

  const createSet = useMutation({
    mutationFn: () => baselineRegressionApi.createSet({
      name: name.trim(),
      description: description.trim(),
      default_expected_level: defaultLevel,
      ...(useWholePackage && selectedPackageId
        ? { source_package_id: selectedPackageId, items: [] }
        : {
            items: Array.from(selectedAssetIds).map((assetId) => ({
              asset_id: assetId,
              expected_level: expectedByAsset[assetId] ?? defaultLevel,
              source_package_id: selectedPackageId || undefined,
            })),
          }),
    }),
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
    mutationFn: () => baselineRegressionApi.createRun(selectedSetId),
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
        description="冻结素材与 L1–L5 期望等级，按当前启用模型及已发布 A/B 提示词重复运行；准确率、混淆矩阵和逐张偏差均来自服务端权威结果。"
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
                  disabled={useWholePackage || !selectedAssetIds.size}
                  onClick={() => {
                    const next: Record<number, BaselineLevel> = {}
                    selectedAssetIds.forEach((assetId) => { next[assetId] = defaultLevel })
                    setExpectedByAsset((current) => ({ ...current, ...next }))
                  }}
                >
                  全部声明为 {defaultLevel}
                </Button>
              </div>
            </div>
            {useWholePackage && (
              <div className="border-t border-[var(--line)] bg-[#f6f9dc] px-5 py-3 text-xs font-semibold">
                已启用整包模式：下方仅供预览，创建时由服务端直接冻结素材包内全部可用素材，不受页面 1000 条预览上限影响。
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
                      const checked = selectedAssetIds.has(asset.id)
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
                                <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">素材 #{asset.id}</p>
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
                              disabled={useWholePackage || !checked}
                              value={expectedByAsset[asset.id] ?? defaultLevel}
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
              <div className="flex flex-wrap items-end justify-between gap-4 border-b border-[var(--line-strong)] pb-5">
                <div>
                  <p className="font-data text-xs text-[var(--muted)]">
                    基准集 #{selectedSet.data.summary.id} · {selectedSet.data.summary.item_count} 张 · 指纹 {selectedSet.data.summary.fingerprint.slice(0, 12)}
                  </p>
                  <h2 className="font-editorial mt-2 text-3xl font-bold">{selectedSet.data.summary.name}</h2>
                  {selectedSet.data.summary.description && (
                    <p className="mt-2 text-sm text-[var(--muted)]">{selectedSet.data.summary.description}</p>
                  )}
                </div>
                <Button
                  disabled={createRun.isPending || selectedSet.data.runs.some((run) => run.status === "running")}
                  onClick={() => createRun.mutate()}
                >
                  <Play weight="fill" />{createRun.isPending ? "正在启动" : "运行全量回归"}
                </Button>
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

  const metrics = run.metrics
  return (
    <>
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

      <section className="mt-7 grid gap-7 xl:grid-cols-[minmax(420px,0.8fr)_minmax(520px,1.2fr)]">
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
              <p className="mt-1 text-xs text-[var(--muted)]">保留失败、偏差与 fallback 分级标记。</p>
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
                  return (
                    <div
                      key={item.id}
                      className="grid gap-3 px-4 py-4 sm:grid-cols-[64px_minmax(0,1fr)_auto] sm:items-center"
                    >
                      <img src={item.image_url} alt="" className="size-14 border border-[var(--line)] object-cover" />
                      <div className="min-w-0">
                        <p className="file-name truncate text-sm">{item.asset.name}</p>
                        <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">
                          素材 #{item.asset_id} · 评测 #{item.evaluation_id ?? "—"} · 分数 {item.authoritative_score ?? "—"}
                        </p>
                        {item.error_message && (
                          <p className="mt-1 text-xs text-[#8d2924]">{item.error_message}</p>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                        {fallback && <Badge tone="warning">graded_by=fallback</Badge>}
                        {item.optimization_case_id !== null && (
                          <Badge tone="success">已入找补队列</Badge>
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
                      </div>
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
        {levels.map((level) => <option key={level} value={level}>{level}</option>)}
      </select>
    </label>
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
