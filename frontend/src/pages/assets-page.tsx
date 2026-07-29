import { useMemo, useRef, useState } from "react"
import { ArrowRight, CheckSquare, CloudArrowUp, ImageSquare, Square } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, jsonBody } from "@/lib/api"
import type { Asset, MaterialPackage, PromptVersion } from "@/lib/types"

function fileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fileType(mimeType: string) {
  return mimeType.split("/")[1]?.toUpperCase().replace("JPEG", "JPG") || "图片"
}

export function AssetsPage({ view = "assets" }: { view?: "packages" | "assets" }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [promptMode, setPromptMode] = useState<"single" | "split" | null>(null)
  const [promptId, setPromptId] = useState<number | null>(null)
  const [promptAId, setPromptAId] = useState<number | null>(null)
  const [promptBId, setPromptBId] = useState<number | null>(null)
  const [packageId, setPackageId] = useState<number | null>(null)
  const [excludeCurrent, setExcludeCurrent] = useState(true)
  const [uploadedFrom, setUploadedFrom] = useState("")
  const [uploadedTo, setUploadedTo] = useState("")
  const queryClient = useQueryClient()
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })
  const promptAOptions = prompts.data?.items.filter((item) => item.stage === "A") ?? []
  const promptBOptions = prompts.data?.items.filter((item) => item.stage === "B") ?? []
  const allPromptOptions = prompts.data?.items ?? []
  const effectivePromptMode = promptMode ?? (allPromptOptions.length === 1 ? "single" : "split")
  const effectivePromptId = promptId ?? allPromptOptions.find((item) => item.status === "published")?.id ?? allPromptOptions[0]?.id ?? null
  const effectivePromptAId = promptAId ?? promptAOptions.find((item) => item.status === "published")?.id ?? null
  const effectivePromptBId = promptBId ?? promptBOptions.find((item) => item.status === "published")?.id ?? null
  const strategyParams = new URLSearchParams()
  if (effectivePromptMode === "single" && effectivePromptId) strategyParams.set("prompt_id", String(effectivePromptId))
  if (effectivePromptMode === "split" && effectivePromptAId) strategyParams.set("prompt_a_id", String(effectivePromptAId))
  if (effectivePromptMode === "split" && effectivePromptBId) strategyParams.set("prompt_b_id", String(effectivePromptBId))
  const packageParams = new URLSearchParams(strategyParams)
  packageParams.set("limit", "200")
  if (uploadedFrom) packageParams.set("created_from", `${uploadedFrom}T00:00:00Z`)
  if (uploadedTo) packageParams.set("created_to", `${uploadedTo}T23:59:59Z`)
  const packages = useQuery({
    queryKey: ["material-packages", packageParams.toString()],
    queryFn: () => api<{ items: MaterialPackage[] }>(`/api/material-packages?${packageParams.toString()}`),
  })
  const assetsParams = new URLSearchParams(strategyParams)
  assetsParams.set("limit", "500")
  if (packageId) assetsParams.set("package_id", String(packageId))
  if (excludeCurrent) assetsParams.set("exclude_evaluated_current", "true")
  const assets = useQuery({
    queryKey: ["assets", assetsParams.toString()],
    queryFn: () => api<{ items: Asset[]; total: number }>(`/api/assets?${assetsParams.toString()}`),
  })
  const upload = useMutation({
    mutationFn: async (files: FileList | File[]) => {
      const form = new FormData()
      Array.from(files).forEach((file) => form.append("files", file))
      return api<{ items: Asset[] }>("/api/assets/upload", { method: "POST", body: form })
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["assets"] })
      await queryClient.invalidateQueries({ queryKey: ["material-packages"] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
      const duplicates = data.items.filter((item) => item.duplicate).length
      toast.success(`已接收 ${data.items.length} 张图片${duplicates ? `，其中 ${duplicates} 张为重复图片` : ""}`)
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
    () => Boolean(assets.data?.items.length) && selected.size === assets.data?.items.length,
    [assets.data?.items.length, selected.size],
  )

  function toggleAll() {
    if (allSelected) setSelected(new Set())
    else setSelected(new Set(assets.data?.items.map((item) => item.id) ?? []))
  }

  return (
    <>
      <PageHeader
        index={view === "packages" ? "01.1" : "01.2"}
        title={view === "packages" ? "素材包" : "素材选择"}
        description={view === "packages" ? "每次上传形成不可变素材包；重复图片仍保留来源记录，并按当前策略汇总任务状态。" : "按素材包和当前策略筛选图片，默认排除已完成当前策略评测的素材；显式关闭后可安全复测。"}
        actions={
          <Button variant="secondary" onClick={() => inputRef.current?.click()}><CloudArrowUp />选择图片</Button>
        }
      />
      <div className="mx-auto max-w-[1540px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp"
          className="sr-only"
          onChange={(event) => event.target.files && upload.mutate(event.target.files)}
        />
        <button
          className={`flex min-h-44 w-full flex-col items-center justify-center border border-dashed px-6 text-center transition-colors ${dragging ? "border-foreground bg-[#f4facf]" : "border-[var(--line-strong)] bg-white hover:bg-[#fafbf8]"}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            if (event.dataTransfer.files.length) upload.mutate(event.dataTransfer.files)
          }}
          disabled={upload.isPending}
        >
          <CloudArrowUp size={28} weight="light" />
          <p className="mt-4 font-editorial text-xl font-bold">{upload.isPending ? "正在保存图片" : "拖入图片，或点击选择"}</p>
          <p className="mt-2 text-sm text-[var(--muted)]">支持 JPG、PNG、WebP，单张不超过 25MB，单次最多 100 张</p>
        </button>

        {view === "packages" && (
          <section className="mt-10">
            <div className="mb-4 flex flex-wrap items-end justify-between gap-4"><div><h2 className="font-editorial text-2xl font-bold">上传批次</h2><p className="mt-1 text-sm text-[var(--muted)]">状态相对于下方当前选择的提示词策略计算，不写回素材成为唯一结论。</p></div><div className="grid grid-cols-2 gap-3"><label><span className="mb-1 block text-xs font-semibold">上传日期起</span><input type="date" className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" value={uploadedFrom} onChange={(event) => setUploadedFrom(event.target.value)} /></label><label><span className="mb-1 block text-xs font-semibold">上传日期止</span><input type="date" className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" value={uploadedTo} onChange={(event) => setUploadedTo(event.target.value)} /></label></div></div>
            <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white">
              {packages.isLoading ? <div className="h-48 animate-pulse" /> : packages.data?.items.length ? (
                <table className="w-full min-w-[1040px] border-collapse text-left text-sm">
                  <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]"><th className="px-4 py-3">素材包</th><th className="px-3 py-3">图片</th><th className="px-3 py-3">当前策略状态</th><th className="px-3 py-3">上传人</th><th className="px-4 py-3 text-right">上传时间</th></tr></thead>
                  <tbody>{packages.data.items.map((item) => <tr key={item.id} className={`cursor-pointer border-b border-[var(--line)] last:border-0 ${packageId === item.id ? "bg-[#f7fadf]" : "hover:bg-[#fbfcfa]"}`} onClick={() => { setPackageId(item.id); setSelected(new Set()) }}>
                    <td className="px-4 py-4"><p className="font-semibold">{item.name}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">{item.package_key}</p></td>
                    <td className="font-data px-3 py-4">{item.unique_asset_count} 唯一 / {item.item_count} 条来源{item.duplicate_count ? ` · ${item.duplicate_count} 重复` : ""}</td>
                    <td className="px-3 py-4"><div className="flex flex-wrap gap-1.5"><Badge tone="success">当前完成 {item.status_summary.evaluated_current}</Badge><Badge>旧版 {item.status_summary.evaluated_old}</Badge><Badge tone="active">待评 {item.status_summary.not_evaluated}</Badge><Badge tone="warning">队列/运行 {item.status_summary.queued + item.status_summary.running}</Badge>{item.status_summary.failed > 0 && <Badge tone="danger">失败 {item.status_summary.failed}</Badge>}</div></td>
                    <td className="px-3 py-4 text-xs text-[var(--muted)]">{item.created_by}</td>
                    <td className="font-data px-4 py-4 text-right text-xs text-[var(--muted)]">{new Date(item.created_at).toLocaleString("zh-CN")}</td>
                  </tr>)}</tbody>
                </table>
              ) : <div className="flex min-h-48 items-center justify-center text-sm text-[var(--muted)]">还没有素材包</div>}
            </div>
          </section>
        )}

        <section className="mt-10">
          <div className="mb-4 flex items-end justify-between gap-4">
            <div>
              <h2 className="font-editorial text-2xl font-bold">{packageId ? "包内素材选择" : "素材列表"}</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">{assets.data?.total ?? 0} 张图片，已选择 {selected.size} 张</p>
            </div>
            <Button variant="ghost" size="sm" onClick={toggleAll} disabled={!assets.data?.items.length}>
              {allSelected ? <CheckSquare weight="fill" /> : <Square />}{allSelected ? "取消全选" : "全选"}
            </Button>
          </div>
          <div className="mb-5 border border-[var(--line)] bg-[#fafbf8] p-4">
            <div className="mb-4 grid gap-4 border-b border-[var(--line)] pb-4 md:grid-cols-[minmax(240px,1fr)_auto] md:items-end">
              <label><span className="mb-2 block text-xs font-semibold">素材包筛选</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={packageId ?? ""} onChange={(event) => { setPackageId(event.target.value ? Number(event.target.value) : null); setSelected(new Set()) }}><option value="">全部素材</option>{(packages.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.item_count} 条</option>)}</select></label>
              <label className="flex h-11 cursor-pointer items-center gap-2 border border-[var(--line-strong)] bg-white px-3 text-sm font-semibold"><input type="checkbox" className="size-4 accent-[#9dbb1c]" checked={excludeCurrent} onChange={(event) => { setExcludeCurrent(event.target.checked); setSelected(new Set()) }} />排除当前策略已完成</label>
            </div>
            <div className="mb-4 flex flex-wrap items-center gap-2"><span className="mr-2 text-xs font-semibold">评测方式</span><button type="button" onClick={() => setPromptMode("single")} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold ${effectivePromptMode === "single" ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white"}`}>单提示词</button><button type="button" onClick={() => setPromptMode("split")} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold ${effectivePromptMode === "split" ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white"}`}>A/B 两阶段</button></div>
            <div className={`grid gap-4 lg:items-end ${effectivePromptMode === "single" ? "lg:grid-cols-[minmax(300px,1fr)_auto]" : "lg:grid-cols-[minmax(220px,1fr)_minmax(220px,1fr)_auto]"}`}>
            {effectivePromptMode === "single" ? <label className="block min-w-0">
              <span className="mb-2 block text-xs font-semibold">完整评测提示词（一次调用）</span>
              <select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={effectivePromptId ?? ""} onChange={(event) => setPromptId(Number(event.target.value))}>
                {!allPromptOptions.length && <option value="">暂无可用版本</option>}
                {allPromptOptions.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.version} · {prompt.name} · {prompt.status === "published" ? "已发布" : prompt.status === "draft" ? "草稿" : "已归档"}</option>)}
              </select>
            </label> : <>
            <label className="block min-w-0">
              <span className="mb-2 block text-xs font-semibold">分类与画质提示词（A）</span>
              <select
                className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                value={effectivePromptAId ?? ""}
                onChange={(event) => setPromptAId(Number(event.target.value))}
              >
                {!promptAOptions.length && <option value="">暂无可用版本</option>}
                {promptAOptions.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.version} · {prompt.name} · {prompt.status === "published" ? "已发布" : prompt.status === "draft" ? "草稿" : "已归档"}</option>)}
              </select>
            </label>
            <label className="block min-w-0">
              <span className="mb-2 block text-xs font-semibold">美感评测提示词（B）</span>
              <select
                className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                value={effectivePromptBId ?? ""}
                onChange={(event) => setPromptBId(Number(event.target.value))}
              >
                {!promptBOptions.length && <option value="">暂无可用版本</option>}
                {promptBOptions.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.version} · {prompt.name} · {prompt.status === "published" ? "已发布" : prompt.status === "draft" ? "草稿" : "已归档"}</option>)}
              </select>
            </label>
            </>}
            <Button
              className="lg:min-w-40"
              disabled={!selected.size || (effectivePromptMode === "single" ? !effectivePromptId : !effectivePromptAId || !effectivePromptBId) || enqueue.isPending}
              onClick={() => enqueue.mutate()}
            >
              开始评测 {selected.size ? `(${selected.size})` : ""}<ArrowRight weight="bold" />
            </Button>
            </div>
            <p className="mt-3 text-xs leading-5 text-[var(--muted)]">{effectivePromptMode === "single" ? "单提示词必须一次返回分类、画质和八个美感维度的完整结果。" : "A/B 两阶段先完成分类与画质预检，再对范围内图片执行美感评测。"} 创建任务不会覆盖旧结果。</p>
          </div>
          <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white scrollbar-thin">
            {assets.isLoading ? (
              <div className="h-64 animate-pulse bg-white" />
            ) : assets.data?.items.length ? (
              <table className="w-full min-w-[900px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]">
                    <th className="w-12 px-4 py-3"><span className="sr-only">选择</span></th>
                    <th className="px-3 py-3 font-semibold">图片</th>
                    <th className="px-3 py-3 font-semibold">尺寸</th>
                    <th className="px-3 py-3 font-semibold">文件格式</th>
                    <th className="px-3 py-3 font-semibold">文件大小</th>
                    <th className="px-3 py-3 font-semibold">当前策略状态</th>
                    <th className="px-4 py-3 text-right font-semibold">创建时间</th>
                  </tr>
                </thead>
                <tbody>
                  {assets.data.items.map((asset) => {
                    const checked = selected.has(asset.id)
                    return (
                      <tr key={asset.id} className={`border-b border-[var(--line)] last:border-0 ${checked ? "bg-[#f7fadf]" : "hover:bg-[#fbfcfa]"}`}>
                        <td className="px-4 py-3">
                          <button
                            className="flex size-8 items-center justify-center rounded-[4px]"
                            aria-label={`${checked ? "取消选择" : "选择"}${asset.name}`}
                            onClick={() => {
                              const next = new Set(selected)
                              checked ? next.delete(asset.id) : next.add(asset.id)
                              setSelected(next)
                            }}
                          >
                            {checked ? <CheckSquare size={20} weight="fill" /> : <Square size={20} />}
                          </button>
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex min-w-0 items-center gap-3">
                            <img src={asset.image_url} alt="" className="size-14 rounded-[4px] border border-[var(--line)] object-cover" loading="lazy" />
                            <div className="min-w-0"><p className="file-name max-w-[320px] truncate">{asset.name}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">#{asset.id.toString().padStart(5, "0")}</p></div>
                          </div>
                        </td>
                        <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">{asset.width} × {asset.height}</td>
                        <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">{fileType(asset.mime_type)}</td>
                        <td className="font-data px-3 py-3 text-xs text-[var(--muted)]">{fileSize(asset.size_bytes)}</td>
                        <td className="px-3 py-3"><Badge tone={asset.evaluation_status === "evaluated_current" ? "success" : asset.evaluation_status === "failed" ? "danger" : asset.evaluation_status === "queued" || asset.evaluation_status === "running" ? "warning" : "active"}>{evaluationStatus(asset.evaluation_status)}</Badge></td>
                        <td className="font-data px-4 py-3 text-right text-xs text-[var(--muted)]">{new Date(asset.created_at).toLocaleString("zh-CN")}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            ) : (
              <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
                <ImageSquare size={30} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">等待第一批图片</h3>
                <p className="mt-2 text-sm text-[var(--muted)]">上传后可在这里批量创建评测任务。</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </>
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
