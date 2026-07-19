import { useMemo, useRef, useState } from "react"
import { ArrowRight, CheckSquare, CloudArrowUp, ImageSquare, Square } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Button } from "@/components/ui/button"
import { api, jsonBody } from "@/lib/api"
import type { Asset, PromptVersion } from "@/lib/types"

function fileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fileType(mimeType: string) {
  return mimeType.split("/")[1]?.toUpperCase().replace("JPEG", "JPG") || "图片"
}

export function AssetsPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [promptAId, setPromptAId] = useState<number | null>(null)
  const [promptBId, setPromptBId] = useState<number | null>(null)
  const queryClient = useQueryClient()
  const assets = useQuery({
    queryKey: ["assets"],
    queryFn: () => api<{ items: Asset[]; total: number }>("/api/assets?limit=200"),
  })
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })
  const promptAOptions = prompts.data?.items.filter((item) => item.stage === "A") ?? []
  const promptBOptions = prompts.data?.items.filter((item) => item.stage === "B") ?? []
  const effectivePromptAId = promptAId ?? promptAOptions.find((item) => item.status === "published")?.id ?? null
  const effectivePromptBId = promptBId ?? promptBOptions.find((item) => item.status === "published")?.id ?? null
  const upload = useMutation({
    mutationFn: async (files: FileList | File[]) => {
      const form = new FormData()
      Array.from(files).forEach((file) => form.append("files", file))
      return api<{ items: Asset[] }>("/api/assets/upload", { method: "POST", body: form })
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["assets"] })
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
          prompt_a_id: effectivePromptAId,
          prompt_b_id: effectivePromptBId,
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
        index="02"
        title="素材"
        description="管理原始图片并创建评测任务。同一素材可使用不同模型与提示词版本反复评测，每次结果独立保存。"
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

        <section className="mt-10">
          <div className="mb-4 flex items-end justify-between gap-4">
            <div>
              <h2 className="font-editorial text-2xl font-bold">素材列表</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">{assets.data?.total ?? 0} 张图片，已选择 {selected.size} 张</p>
            </div>
            <Button variant="ghost" size="sm" onClick={toggleAll} disabled={!assets.data?.items.length}>
              {allSelected ? <CheckSquare weight="fill" /> : <Square />}{allSelected ? "取消全选" : "全选"}
            </Button>
          </div>
          <div className="mb-5 grid gap-4 border border-[var(--line)] bg-[#fafbf8] p-4 lg:grid-cols-[minmax(220px,1fr)_minmax(220px,1fr)_auto] lg:items-end">
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
            <Button
              className="lg:min-w-40"
              disabled={!selected.size || !effectivePromptAId || !effectivePromptBId || enqueue.isPending}
              onClick={() => enqueue.mutate()}
            >
              开始评测 {selected.size ? `(${selected.size})` : ""}<ArrowRight weight="bold" />
            </Button>
            <p className="text-xs leading-5 text-[var(--muted)] lg:col-span-3">创建任务不会覆盖旧结果。评测结果、模型版本和提示词版本请到“评测结果”查看。</p>
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
                    <th className="px-4 py-3 text-right font-semibold">上传时间</th>
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
