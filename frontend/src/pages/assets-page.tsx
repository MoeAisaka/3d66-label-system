import { useMemo, useRef, useState } from "react"
import {
  ArrowRight,
  CheckSquare,
  CloudArrowUp,
  FileZip,
  FolderOpen,
  GearSix,
  ImageSquare,
  Package,
  Square,
  Trash,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { Asset, EvaluationCategoryProfile, MaterialPackage, PromptVersion } from "@/lib/types"

type CategoryKey = EvaluationCategoryProfile["category_key"]

type UploadResult = {
  items: Asset[]
  package: {
    id: number
    name: string
    item_count: number
    duplicate_count: number
    restored_count: number
    ignored_count?: number
  }
}

function fileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fileType(mimeType: string) {
  return mimeType.split("/")[1]?.toUpperCase().replace("JPEG", "JPG") || "图片"
}

function snapshotFiles(files: FileList | null) {
  return files ? Array.from(files) : []
}

export function AssetsPage() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const archiveInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [uploadPackageName, setUploadPackageName] = useState("")
  const [manualPackageName, setManualPackageName] = useState("")
  const [promptMode, setPromptMode] = useState<"single" | "split" | null>(null)
  const [promptId, setPromptId] = useState<number | null>(null)
  const [promptAId, setPromptAId] = useState<number | null>(null)
  const [promptBId, setPromptBId] = useState<number | null>(null)
  const [packageId, setPackageId] = useState<number | null>(null)
  const [excludeCurrent, setExcludeCurrent] = useState(false)
  const [uploadedFrom, setUploadedFrom] = useState("")
  const [uploadedTo, setUploadedTo] = useState("")
  const [categoryKey, setCategoryKey] = useState<CategoryKey>("space_image")
  const queryClient = useQueryClient()
  const categories = useQuery({
    queryKey: ["evaluation-categories"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
  })
  const selectedCategory = categories.data?.items.find((item) => item.category_key === categoryKey)
  const categoryAccept = selectedCategory
    ? [...selectedCategory.allowed_mime_types, ...selectedCategory.pipeline_config.allowed_suffixes].join(",")
    : "image/jpeg,image/png,image/webp,image/gif"
  const isDocumentCategory = selectedCategory?.pipeline_config.input_kind === "pdf"

  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })
  const promptAOptions = prompts.data?.items.filter((item) => item.stage === "A") ?? []
  const promptBOptions = prompts.data?.items.filter((item) => item.stage === "B") ?? []
  const allPromptOptions = prompts.data?.items ?? []
  const effectivePromptMode = promptMode ?? (allPromptOptions.length === 1 ? "single" : "split")
  const effectivePromptId = promptId
    ?? allPromptOptions.find((item) => item.status === "published")?.id
    ?? allPromptOptions[0]?.id
    ?? null
  const effectivePromptAId = promptAId
    ?? promptAOptions.find((item) => item.status === "published")?.id
    ?? null
  const effectivePromptBId = promptBId
    ?? promptBOptions.find((item) => item.status === "published")?.id
    ?? null

  const strategyParams = new URLSearchParams()
  if (effectivePromptMode === "single" && effectivePromptId) strategyParams.set("prompt_id", String(effectivePromptId))
  if (effectivePromptMode === "split" && effectivePromptAId) strategyParams.set("prompt_a_id", String(effectivePromptAId))
  if (effectivePromptMode === "split" && effectivePromptBId) strategyParams.set("prompt_b_id", String(effectivePromptBId))
  const packageParams = new URLSearchParams(strategyParams)
  packageParams.set("category_key", categoryKey)
  packageParams.set("limit", "500")
  if (uploadedFrom) packageParams.set("created_from", `${uploadedFrom}T00:00:00Z`)
  if (uploadedTo) packageParams.set("created_to", `${uploadedTo}T23:59:59Z`)
  const packages = useQuery({
    queryKey: ["material-packages", packageParams.toString()],
    queryFn: () => api<{ items: MaterialPackage[] }>(`/api/material-packages?${packageParams.toString()}`),
  })

  const assetsParams = new URLSearchParams(strategyParams)
  assetsParams.set("limit", "1000")
  assetsParams.set("category_key", categoryKey)
  if (packageId) assetsParams.set("package_id", String(packageId))
  if (excludeCurrent) assetsParams.set("exclude_evaluated_current", "true")
  const assets = useQuery({
    queryKey: ["assets", assetsParams.toString()],
    queryFn: () => api<{ items: Asset[]; total: number }>(`/api/assets?${assetsParams.toString()}`),
  })

  async function refreshMaterials() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["assets"] }),
      queryClient.invalidateQueries({ queryKey: ["material-packages"] }),
      queryClient.invalidateQueries({ queryKey: ["baseline-assets"] }),
      queryClient.invalidateQueries({ queryKey: ["baseline-packages"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
    ])
  }

  const upload = useMutation({
    mutationFn: async ({ files, packageName }: { files: File[]; packageName?: string }) => {
      const form = new FormData()
      files.forEach((file) => form.append("files", file))
      if (packageName?.trim()) form.append("package_name", packageName.trim())
      form.append("category_key", categoryKey)
      return api<UploadResult>("/api/assets/upload", { method: "POST", body: form })
    },
    onSuccess: async (data) => {
      setUploadPackageName("")
      await refreshMaterials()
      const notes = [
        data.package.duplicate_count ? `${data.package.duplicate_count} 张重复` : "",
        data.package.restored_count ? `${data.package.restored_count} 张恢复` : "",
      ].filter(Boolean)
      toast.success(`“${data.package.name}”已汇总 ${data.items.length} 张${notes.length ? `（${notes.join("，")}）` : ""}`)
    },
    onError: (error) => toast.error(error.message),
  })

  const archiveUpload = useMutation({
    mutationFn: async ({ archive, packageName }: { archive: File; packageName?: string }) => {
      const form = new FormData()
      form.append("archive", archive)
      if (packageName?.trim()) form.append("package_name", packageName.trim())
      form.append("category_key", categoryKey)
      return api<UploadResult>("/api/material-packages/import-archive", {
        method: "POST",
        body: form,
      })
    },
    onSuccess: async (data) => {
      setUploadPackageName("")
      await refreshMaterials()
      toast.success(
        `“${data.package.name}”已从 ZIP 汇总 ${data.items.length} 张`
        + (data.package.ignored_count ? `，忽略 ${data.package.ignored_count} 个非图片文件` : ""),
      )
    },
    onError: (error) => toast.error(error.message),
  })

  const createPackage = useMutation({
    mutationFn: () => api<MaterialPackage>("/api/material-packages", {
      method: "POST",
      ...jsonBody({
        name: manualPackageName.trim(),
        asset_ids: Array.from(selected),
        category_key: categoryKey,
      }),
    }),
    onSuccess: async (created) => {
      setManualPackageName("")
      setSelected(new Set())
      setPackageId(created.id)
      await refreshMaterials()
      toast.success(`已整理为素材包“${created.name}”`)
    },
    onError: (error) => toast.error(error.message),
  })

  const deleteAsset = useMutation({
    mutationFn: (assetId: number) => api<{ id: number; history_retained: boolean }>(
      `/api/assets/${assetId}`,
      { method: "DELETE" },
    ),
    onSuccess: async ({ id }) => {
      setSelected((current) => {
        const next = new Set(current)
        next.delete(id)
        return next
      })
      await refreshMaterials()
      toast.success("素材已从可选列表移除，历史评测与素材包记录仍保留")
    },
    onError: (error) => toast.error(error.message),
  })

  const bulkDelete = useMutation({
    mutationFn: (assetIds: number[]) => api<{ deleted: number }>("/api/assets/bulk-delete", {
      method: "POST",
      ...jsonBody({ asset_ids: assetIds }),
    }),
    onSuccess: async (data) => {
      setSelected(new Set())
      await refreshMaterials()
      toast.success(`已删除 ${data.deleted} 张素材，历史记录仍保留`)
    },
    onError: (error) => toast.error(error.message),
  })

  const deletePackage = useMutation({
    mutationFn: (id: number) => api<{ deleted: number }>(`/api/material-packages/${id}`, { method: "DELETE" }),
    onSuccess: async (data) => {
      setPackageId(null)
      setSelected(new Set())
      await refreshMaterials()
      toast.success(`素材包已删除，共移除 ${data.deleted} 张素材；历史记录仍保留`)
    },
    onError: (error) => toast.error(error.message),
  })

  const updateAssetCategory = useMutation({
    mutationFn: ({ id, category }: { id: number; category: CategoryKey }) => api<Asset>(`/api/assets/${id}/category`, {
      method: "PATCH",
      ...jsonBody({ category_key: category }),
    }),
    onSuccess: async () => {
      await refreshMaterials()
      toast.success("素材所属通道已更新")
    },
    onError: (error) => toast.error(error.message),
  })

  const enqueue = useMutation({
    mutationFn: () =>
      api<{ job_ids: number[] }>("/api/jobs/enqueue", {
        method: "POST",
        ...jsonBody({
          asset_ids: Array.from(selected),
          prompt_id: effectivePromptMode === "single" ? effectivePromptId : null,
          prompt_a_id: effectivePromptMode === "split" ? effectivePromptAId : null,
          prompt_b_id: effectivePromptMode === "split" ? effectivePromptBId : null,
          category_key: categoryKey,
        }),
      }),
    onSuccess: async (data) => {
      setSelected(new Set())
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      toast.success(`已创建 ${data.job_ids.length} 个评测任务`)
    },
    onError: (error) => toast.error(error.message),
  })

  const allSelected = useMemo(
    () => Boolean(assets.data?.items.length)
      && Boolean(assets.data?.items.every((item) => selected.has(item.id))),
    [assets.data?.items, selected],
  )
  const uploadBusy = upload.isPending || archiveUpload.isPending

  function choosePackage(nextPackageId: number | null) {
    setPackageId(nextPackageId)
    setSelected(new Set())
  }

  function submitFiles(files: File[], packageName?: string) {
    if (!files.length) return
    upload.mutate({ files, packageName: packageName || uploadPackageName })
  }

  function toggleAll() {
    if (allSelected) setSelected(new Set())
    else setSelected(new Set(assets.data?.items.map((item) => item.id) ?? []))
  }

  return (
    <>
      <PageHeader
        index="04.1"
        title="资产库"
        description="上传、整理和查找素材都在这里完成。一次批量、一个文件夹或一个 ZIP 自动汇总为一个可追溯素材包。"
      />
      <div className="mx-auto max-w-[1540px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={categoryAccept}
          className="sr-only"
          onChange={(event) => {
            const files = snapshotFiles(event.target.files)
            event.target.value = ""
            submitFiles(files)
          }}
        />
        <input
          ref={(node) => {
            folderInputRef.current = node
            node?.setAttribute("webkitdirectory", "")
            node?.setAttribute("directory", "")
          }}
          type="file"
          multiple
          accept={categoryAccept}
          className="sr-only"
          onChange={(event) => {
            const files = snapshotFiles(event.target.files)
            const first = files[0] as (File & { webkitRelativePath?: string }) | undefined
            const folderName = first?.webkitRelativePath?.split("/")[0]
            event.target.value = ""
            submitFiles(files, uploadPackageName || folderName)
          }}
        />
        <input
          ref={archiveInputRef}
          type="file"
          accept=".zip,application/zip"
          className="sr-only"
          onChange={(event) => {
            const archive = event.target.files?.[0]
            event.target.value = ""
            if (archive) archiveUpload.mutate({ archive, packageName: uploadPackageName })
          }}
        />

        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-4 p-5 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-end">
            <label>
              <span className="mb-2 block text-xs font-semibold">本次素材包名称（可选）</span>
              <Input
                value={uploadPackageName}
                maxLength={200}
                placeholder="留空时自动使用文件夹、ZIP 或上传时间命名"
                onChange={(event) => setUploadPackageName(event.target.value)}
              />
            </label>
            <label>
              <span className="mb-2 block text-xs font-semibold">评测类目</span>
              <select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={categoryKey} onChange={(event) => { setCategoryKey(event.target.value as CategoryKey); setPackageId(null); setSelected(new Set()) }}>
                {(categories.data?.items ?? []).filter((item) => item.status !== "retired").map((item) => <option key={item.category_key} value={item.category_key}>{item.display_name}</option>)}
              </select>
            </label>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => fileInputRef.current?.click()} disabled={uploadBusy}>
                <CloudArrowUp />{isDocumentCategory ? "批量 PDF" : "批量图片"}
              </Button>
              <Button variant="secondary" onClick={() => folderInputRef.current?.click()} disabled={uploadBusy}>
                <FolderOpen />整个文件夹
              </Button>
              <Button variant="secondary" onClick={() => archiveInputRef.current?.click()} disabled={uploadBusy}>
                <FileZip />ZIP 压缩包
              </Button>
            </div>
          </div>
          <button
            type="button"
            className={`flex min-h-32 w-full flex-col items-center justify-center border-t border-dashed px-6 text-center transition-colors ${
              dragging ? "border-foreground bg-[#f4facf]" : "border-[var(--line-strong)] bg-[#fafbf8] hover:bg-[#f6f8f3]"
            }`}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              submitFiles(snapshotFiles(event.dataTransfer.files))
            }}
            disabled={uploadBusy}
          >
            <CloudArrowUp size={28} weight="light" />
            <p className="mt-3 font-editorial text-xl font-bold">
              {uploadBusy ? "正在生成素材包" : isDocumentCategory ? `拖入 ${selectedCategory?.display_name ?? "PDF"} 素材` : `拖入一批${selectedCategory?.display_name ?? "图片"}素材`}
            </p>
            <p className="mt-2 text-sm text-[var(--muted)]">
              {isDocumentCategory ? "PDF 单文件不超过 25MB；上传后按类目处理链执行" : "图片/文件夹单次最多 1000 张；更多素材用 ZIP，最多 10000 张；单张不超过 25MB"}
            </p>
          </button>
        </section>

        <section className="mt-10">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="font-editorial text-2xl font-bold">素材包列表</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">点击一行即可查看包内素材和批量创建评测任务。</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label>
                <span className="mb-1 block text-xs font-semibold">上传日期起</span>
                <input type="date" className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={uploadedFrom} onChange={(event) => setUploadedFrom(event.target.value)} />
              </label>
              <label>
                <span className="mb-1 block text-xs font-semibold">上传日期止</span>
                <input type="date" className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={uploadedTo} onChange={(event) => setUploadedTo(event.target.value)} />
              </label>
            </div>
          </div>
          <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white">
            {packages.isLoading ? <div className="h-48 animate-pulse" /> : packages.data?.items.length ? (
              <table className="w-full min-w-[1080px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]">
                    <th className="px-4 py-3">素材包</th>
                    <th className="px-3 py-3">素材</th>
                    <th className="px-3 py-3">当前策略状态</th>
                    <th className="px-3 py-3">创建人</th>
                    <th className="px-3 py-3">创建时间</th>
                    <th className="px-4 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {packages.data.items.map((item) => (
                    <tr
                      key={item.id}
                      className={`cursor-pointer border-b border-[var(--line)] last:border-0 ${
                        packageId === item.id ? "bg-[#f7fadf]" : "hover:bg-[#fbfcfa]"
                      }`}
                      onClick={() => choosePackage(item.id)}
                    >
                      <td className="px-4 py-4">
                        <p className="font-semibold">{item.name}</p>
                        <p className="font-data mt-1 text-xs text-[var(--muted)]">{item.package_key} · {item.category_key}</p>
                      </td>
                      <td className="font-data px-3 py-4">
                        {item.active_asset_count} 可用 / {item.unique_asset_count} 唯一 / {item.item_count} 条来源
                        {item.duplicate_count ? ` · ${item.duplicate_count} 重复` : ""}
                        {item.removed_asset_count ? ` · ${item.removed_asset_count} 已删除` : ""}
                      </td>
                      <td className="px-3 py-4">
                        <div className="flex flex-wrap gap-1.5">
                          <Badge tone="success">当前完成 {item.status_summary.evaluated_current}</Badge>
                          <Badge>旧版 {item.status_summary.evaluated_old}</Badge>
                          <Badge tone="active">待评 {item.status_summary.not_evaluated}</Badge>
                          <Badge tone="warning">队列/运行 {item.status_summary.queued + item.status_summary.running}</Badge>
                          {item.status_summary.failed > 0 && <Badge tone="danger">失败 {item.status_summary.failed}</Badge>}
                        </div>
                      </td>
                      <td className="px-3 py-4 text-xs text-[var(--muted)]">{item.created_by}</td>
                      <td className="font-data px-3 py-4 text-xs text-[var(--muted)]">
                        {new Date(item.created_at).toLocaleString("zh-CN")}
                      </td>
                      <td className="px-4 py-4 text-right"><Button variant="ghost" size="sm" onClick={(event) => { event.stopPropagation(); if (window.confirm(`删除素材包“${item.name}”及包内全部素材？历史评测和原文件仍保留。`)) deletePackage.mutate(item.id) }}><Trash />删除包</Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="flex min-h-48 items-center justify-center text-sm text-[var(--muted)]">还没有素材包</div>}
          </div>
        </section>

        <section className="mt-10">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="font-editorial text-2xl font-bold">{packageId ? "包内素材与任务" : "全部素材与任务"}</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">{assets.data?.total ?? 0} 张可用素材，已选择 {selected.size} 张</p>
            </div>
            <div className="flex gap-2"><Button variant="ghost" size="sm" onClick={toggleAll} disabled={!assets.data?.items.length}>{allSelected ? <CheckSquare weight="fill" /> : <Square />}{allSelected ? "取消全选" : "全选当前列表"}</Button><Button variant="secondary" size="sm" disabled={!selected.size || bulkDelete.isPending} onClick={() => { if (window.confirm(`删除已选 ${selected.size} 张素材？历史评测和原文件仍保留。`)) bulkDelete.mutate(Array.from(selected)) }}><Trash />批量删除 ({selected.size})</Button></div>
          </div>

          <div className="mb-5 border border-[var(--line)] bg-[#fafbf8] p-4">
            <div className="grid gap-4 border-b border-[var(--line)] pb-4 lg:grid-cols-[minmax(240px,1fr)_minmax(260px,1fr)_auto] lg:items-end">
              <label>
                <span className="mb-2 block text-xs font-semibold">素材包筛选</span>
                <select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={packageId ?? ""} onChange={(event) => choosePackage(event.target.value ? Number(event.target.value) : null)}>
                  <option value="">全部可用素材</option>
                  {(packages.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.active_asset_count} 张可用</option>)}
                </select>
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">把已选素材整理成新包</span>
                <Input value={manualPackageName} maxLength={200} placeholder="输入新素材包名称" onChange={(event) => setManualPackageName(event.target.value)} />
              </label>
              <Button
                variant="secondary"
                disabled={!selected.size || !manualPackageName.trim() || createPackage.isPending}
                onClick={() => createPackage.mutate()}
              >
                <Package />整理成包 ({selected.size})
              </Button>
            </div>

            <details className="mt-4 border-y border-[var(--line)] bg-white">
              <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm font-bold">
                <GearSix />管理员高级评测入口
              </summary>
              <div className="border-t border-[var(--line)] bg-[#fafbf8] px-4 pb-4 pt-1">
                <p className="mt-3 text-xs leading-5 text-[var(--muted)]">一线审核请前往“评测包生产线”，系统会自动使用类目冻结方案。这里保留手动版本实验能力。</p>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <span className="mr-2 text-xs font-semibold">评测方式</span>
                  <button type="button" onClick={() => setPromptMode("single")} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold ${effectivePromptMode === "single" ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white"}`}>单提示词</button>
                  <button type="button" onClick={() => setPromptMode("split")} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold ${effectivePromptMode === "split" ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white"}`}>A/B 两阶段</button>
                  <label className="ml-auto flex h-9 cursor-pointer items-center gap-2 border border-[var(--line-strong)] bg-white px-3 text-xs font-semibold">
                    <input type="checkbox" className="size-4 accent-[#9dbb1c]" checked={excludeCurrent} onChange={(event) => { setExcludeCurrent(event.target.checked); setSelected(new Set()) }} />
                    排除当前策略已完成
                  </label>
                </div>

                <div className={`mt-4 grid gap-4 lg:items-end ${effectivePromptMode === "single" ? "lg:grid-cols-[minmax(300px,1fr)_auto]" : "lg:grid-cols-[minmax(220px,1fr)_minmax(220px,1fr)_auto]"}`}>
                  {effectivePromptMode === "single" ? (
                    <PromptSelect label="完整评测提示词（一次调用）" value={effectivePromptId} options={allPromptOptions} onChange={setPromptId} />
                  ) : (
                    <>
                      <PromptSelect label="分类与画质提示词（A）" value={effectivePromptAId} options={promptAOptions} onChange={setPromptAId} />
                      <PromptSelect label="美感评测提示词（B）" value={effectivePromptBId} options={promptBOptions} onChange={setPromptBId} />
                    </>
                  )}
                  <Button
                    className="lg:min-w-40"
                    disabled={!selected.size || (effectivePromptMode === "single" ? !effectivePromptId : !effectivePromptAId || !effectivePromptBId) || enqueue.isPending}
                    onClick={() => enqueue.mutate()}
                  >
                    开始评测 {selected.size ? `(${selected.size})` : ""}<ArrowRight weight="bold" />
                  </Button>
                </div>
              </div>
            </details>
          </div>

          <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white">
            {assets.isLoading ? <div className="h-64 animate-pulse bg-white" /> : assets.data?.items.length ? (
              <table className="w-full min-w-[1040px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]">
                    <th className="w-12 px-4 py-3"><span className="sr-only">选择</span></th>
                    <th className="px-3 py-3">图片</th>
                    <th className="px-3 py-3">尺寸</th>
                    <th className="px-3 py-3">格式</th>
                    <th className="px-3 py-3">大小</th>
                    <th className="px-3 py-3">所属通道</th>
                    <th className="px-3 py-3">当前策略状态</th>
                    <th className="px-4 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {assets.data.items.map((asset) => {
                    const checked = selected.has(asset.id)
                    return (
                      <tr key={asset.id} className={`border-b border-[var(--line)] last:border-0 ${checked ? "bg-[#f7fadf]" : "hover:bg-[#fbfcfa]"}`}>
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            className="flex size-8 items-center justify-center rounded-[4px]"
                            aria-label={`${checked ? "取消选择" : "选择"}${asset.name}`}
                            onClick={() => setSelected((current) => {
                              const next = new Set(current)
                              checked ? next.delete(asset.id) : next.add(asset.id)
                              return next
                            })}
                          >
                            {checked ? <CheckSquare size={20} weight="fill" /> : <Square size={20} />}
                          </button>
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex min-w-0 items-center gap-3">
                            <img src={asset.image_url} alt="" className="size-14 border border-[var(--line)] object-cover" loading="lazy" />
                            <div className="min-w-0">
                              <p className="file-name max-w-[360px] truncate">{asset.name}</p>
                              <p className="font-data mt-1 text-xs text-[var(--muted)]">#{asset.id.toString().padStart(5, "0")}</p>
                            </div>
                          </div>
                        </td>
                        <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "—"}</td>
                        <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">{fileType(asset.mime_type)}</td>
                        <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">{fileSize(asset.size_bytes)}</td>
                        <td className="px-3 py-3"><select className="h-9 rounded-[4px] border border-[var(--line-strong)] bg-white px-2 text-xs" value={asset.category_key} onChange={(event) => updateAssetCategory.mutate({ id: asset.id, category: event.target.value as CategoryKey })}>{(categories.data?.items ?? []).filter((item) => item.status !== "retired").map((item) => <option key={item.category_key} value={item.category_key}>{item.display_name}</option>)}</select></td>
                        <td className="px-3 py-3"><Badge tone={statusTone(asset.evaluation_status)}>{evaluationStatus(asset.evaluation_status)}</Badge></td>
                        <td className="px-4 py-3 text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={deleteAsset.isPending}
                            onClick={() => {
                              if (window.confirm(`删除“${asset.name}”？\n\n素材会从可选列表移除，但历史评测、旧素材包记录和原文件仍保留用于审计。`)) {
                                deleteAsset.mutate(asset.id)
                              }
                            }}
                          >
                            <Trash />删除
                          </Button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            ) : (
              <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
                <ImageSquare size={30} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">当前范围没有可用素材</h3>
                <p className="mt-2 text-sm text-[var(--muted)]">上传素材包，或切换上方筛选条件。</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </>
  )
}

function PromptSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: number | null
  options: PromptVersion[]
  onChange: (value: number) => void
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      <select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={value ?? ""} onChange={(event) => onChange(Number(event.target.value))}>
        {!options.length && <option value="">暂无可用版本</option>}
        {options.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.version} · {prompt.name} · {prompt.status === "published" ? "已发布" : prompt.status === "draft" ? "草稿" : "已归档"}</option>)}
      </select>
    </label>
  )
}

function evaluationStatus(value: Asset["evaluation_status"]) {
  return ({
    not_evaluated: "未评测",
    evaluated_old: "仅旧版本",
    evaluated_current: "当前版本已评测",
    queued: "已排队",
    running: "运行中",
    failed: "失败",
  } as const)[value ?? "not_evaluated"]
}

function statusTone(value: Asset["evaluation_status"]) {
  if (value === "evaluated_current") return "success"
  if (value === "failed") return "danger"
  if (value === "queued" || value === "running") return "warning"
  return "active"
}
