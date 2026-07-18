import { useEffect, useMemo, useState } from "react"
import { ArrowRight, CheckSquare, FolderSimplePlus, MagnifyingGlass, Plus, Square, Trash } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { Asset, SampleSetDetail, SampleSetItem, SampleSetSummary } from "@/lib/types"

export function SampleSetsPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [mode, setMode] = useState<"included" | "available">("included")
  const [search, setSearch] = useState("")
  const [selectedAssets, setSelectedAssets] = useState<Set<number>>(new Set())

  const sets = useQuery({
    queryKey: ["sample-sets"],
    queryFn: () => api<{ items: SampleSetSummary[] }>("/api/sample-sets"),
  })
  const detail = useQuery({
    queryKey: ["sample-set", selectedId],
    queryFn: () => api<SampleSetDetail>(`/api/sample-sets/${selectedId}`),
    enabled: Boolean(selectedId),
  })
  const assets = useQuery({
    queryKey: ["assets", "sample-set-candidates"],
    queryFn: () => api<{ items: Asset[]; total: number }>("/api/assets?limit=1000"),
  })

  useEffect(() => {
    if (!selectedId && sets.data?.items.length) setSelectedId(sets.data.items[0].id)
  }, [selectedId, sets.data?.items])
  useEffect(() => {
    setMode("included")
    setSearch("")
    setSelectedAssets(new Set())
  }, [selectedId])

  async function refreshSampleSet() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["sample-sets"] }),
      queryClient.invalidateQueries({ queryKey: ["sample-set", selectedId] }),
      queryClient.invalidateQueries({ queryKey: ["migration-context"] }),
    ])
  }

  const createSet = useMutation({
    mutationFn: () => api<{ id: number }>("/api/sample-sets", {
      method: "POST",
      ...jsonBody({ name: name.trim(), description: description.trim() }),
    }),
    onSuccess: async (data) => {
      setSelectedId(data.id)
      setName("")
      setDescription("")
      setCreating(false)
      await refreshSampleSet()
      toast.success("样本集已创建，可以开始收录图片")
    },
    onError: (error) => toast.error(error.message),
  })
  const addItems = useMutation({
    mutationFn: () => api<{ added: number; skipped: number[] }>(`/api/sample-sets/${selectedId}/items`, {
      method: "POST",
      ...jsonBody({ asset_ids: Array.from(selectedAssets) }),
    }),
    onSuccess: async (data) => {
      setSelectedAssets(new Set())
      setMode("included")
      await refreshSampleSet()
      toast.success(`已收录 ${data.added} 张图片${data.skipped.length ? `，跳过 ${data.skipped.length} 张` : ""}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const removeItem = useMutation({
    mutationFn: (itemId: number) => api(`/api/sample-sets/${selectedId}/items/${itemId}`, { method: "DELETE" }),
    onSuccess: async () => {
      await refreshSampleSet()
      toast.success("已移出样本集，原素材和评测结果仍保留")
    },
    onError: (error) => toast.error(error.message),
  })
  const updateItem = useMutation({
    mutationFn: ({ itemId, expectedLevel, note }: { itemId: number; expectedLevel: string | null; note: string }) =>
      api(`/api/sample-sets/${selectedId}/items/${itemId}`, {
        method: "PATCH",
        ...jsonBody({ expected_level: expectedLevel, note }),
      }),
    onSuccess: async () => {
      await refreshSampleSet()
      toast.success("样本基准已保存")
    },
    onError: (error) => toast.error(error.message),
  })

  const includedIds = useMemo(() => new Set(detail.data?.items.map((item) => item.asset_id) ?? []), [detail.data?.items])
  const availableAssets = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return (assets.data?.items ?? []).filter((asset) =>
      asset.evaluation &&
      ["approved", "corrected"].includes(asset.evaluation.human_review?.decision || "") &&
      !includedIds.has(asset.id) &&
      (!keyword || asset.name.toLowerCase().includes(keyword) || String(asset.id).includes(keyword)),
    )
  }, [assets.data?.items, includedIds, search])

  return (
    <>
      <PageHeader
        index="07"
        title="样本集"
        description="固定一组有人工基准的图片，跨提示词和模型版本重复使用。模型原始结果不会被覆盖。"
        actions={<Button onClick={() => setCreating((value) => !value)}><FolderSimplePlus />创建样本集</Button>}
      />
      <div className="mx-auto max-w-[1540px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        {creating && (
          <section className="mb-8 grid gap-4 border-y border-[var(--line-strong)] bg-white p-5 lg:grid-cols-[1fr_1.5fr_auto] lg:items-end">
            <label><span className="mb-2 block text-xs font-semibold">样本集名称</span><Input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：室内空间黄金样本 V1" /></label>
            <label><span className="mb-2 block text-xs font-semibold">用途说明</span><Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例如：覆盖住宅、商业空间和困难画质案例" /></label>
            <div className="flex gap-2"><Button variant="secondary" onClick={() => setCreating(false)}>取消创建</Button><Button disabled={!name.trim() || createSet.isPending} onClick={() => createSet.mutate()}>保存样本集</Button></div>
          </section>
        )}

        <div className="grid gap-8 xl:grid-cols-[300px_1fr]">
          <aside>
            <div className="mb-3 flex items-end justify-between"><div><h2 className="font-editorial text-2xl font-bold">样本库</h2><p className="mt-1 text-xs text-[var(--muted)]">迁移时可重复调用</p></div><span className="font-data text-xs text-[var(--muted)]">{sets.data?.items.length ?? 0}</span></div>
            <div className="border-y border-[var(--line-strong)] bg-white">
              {sets.isLoading ? <div className="h-40 animate-pulse" /> : sets.data?.items.length ? sets.data.items.map((sampleSet) => (
                <button key={sampleSet.id} onClick={() => setSelectedId(sampleSet.id)} className={`w-full border-b border-[var(--line)] px-4 py-4 text-left last:border-0 ${selectedId === sampleSet.id ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"}`}>
                  <div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-semibold">{sampleSet.name}</span><Badge tone={sampleSet.item_count ? "active" : "neutral"}>{sampleSet.item_count} 张</Badge></div>
                  <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{sampleSet.description || "尚未填写用途说明"}</p>
                </button>
              )) : <div className="px-5 py-12 text-center"><FolderSimplePlus size={28} className="mx-auto" /><p className="mt-3 text-sm font-semibold">还没有样本集</p><p className="mt-2 text-xs leading-5 text-[var(--muted)]">先创建一个，再从已评测素材中收录图片。</p></div>}
            </div>
          </aside>

          <main className="min-w-0">
            {detail.data ? (
              <>
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <div><p className="font-data text-xs text-[var(--muted)]">SET #{String(detail.data.summary.id).padStart(4, "0")}</p><h2 className="font-editorial mt-2 text-3xl font-bold">{detail.data.summary.name}</h2><p className="mt-2 max-w-[70ch] text-sm leading-6 text-[var(--muted)]">{detail.data.summary.description || "尚未填写用途说明"}</p></div>
                  <div className="flex gap-2"><Button variant={mode === "included" ? "primary" : "secondary"} onClick={() => setMode("included")}>已收录 {detail.data.summary.item_count}</Button><Button variant={mode === "available" ? "primary" : "secondary"} onClick={() => setMode("available")}><Plus />添加素材</Button></div>
                </div>

                {mode === "included" ? (
                  <section className="mt-5 border-y border-[var(--line-strong)] bg-white">
                    {detail.data.items.length ? detail.data.items.map((item) => <SampleItemRow key={item.id} item={item} saving={updateItem.isPending} removing={removeItem.isPending} onSave={(expectedLevel, note) => updateItem.mutate({ itemId: item.id, expectedLevel, note })} onRemove={() => removeItem.mutate(item.id)} />) : <div className="px-6 py-16 text-center"><Square size={28} className="mx-auto" /><h3 className="font-editorial mt-4 text-xl font-bold">样本集还是空的</h3><p className="mt-2 text-sm text-[var(--muted)]">添加已经完成评测和人工确认的图片，形成可复用基准。</p><Button className="mt-5" onClick={() => setMode("available")}><Plus />添加第一批素材</Button></div>}
                  </section>
                ) : (
                  <section className="mt-5">
                    <div className="flex flex-wrap items-end justify-between gap-4 border-y border-[var(--line-strong)] bg-white p-4">
                      <label className="min-w-64 flex-1"><span className="mb-2 block text-xs font-semibold">搜索已评测素材</span><div className="relative"><MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" /><Input className="pl-10" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入文件名或素材编号" /></div></label>
                      <Button disabled={!selectedAssets.size || addItems.isPending} onClick={() => addItems.mutate()}>收录所选素材 {selectedAssets.size ? `(${selectedAssets.size})` : ""}</Button>
                    </div>
                    <div className="mt-4 max-h-[62vh] overflow-y-auto border-y border-[var(--line-strong)] bg-white">
                      {availableAssets.length ? availableAssets.map((asset) => {
                        const checked = selectedAssets.has(asset.id)
                        const level = asset.evaluation?.final_level || asset.evaluation?.level
                        return <button key={asset.id} className={`grid w-full grid-cols-[36px_56px_1fr_auto] items-center gap-3 border-b border-[var(--line)] px-4 py-3 text-left last:border-0 ${checked ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"}`} onClick={() => { const next = new Set(selectedAssets); checked ? next.delete(asset.id) : next.add(asset.id); setSelectedAssets(next) }}>
                          {checked ? <CheckSquare size={20} weight="fill" /> : <Square size={20} />}
                          <img src={asset.image_url} alt="" className="size-14 rounded-[4px] border border-[var(--line)] object-cover" loading="lazy" />
                          <div className="min-w-0"><p className="truncate text-sm font-semibold">{asset.name}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">#{String(asset.id).padStart(5, "0")} · {asset.evaluation?.versions.model}</p></div>
                          <Badge tone="active">{level}</Badge>
                        </button>
                      }) : <div className="px-6 py-14 text-center text-sm text-[var(--muted)]">没有符合条件的人工确认素材，请先到“结果审核”确认或修改等级</div>}
                    </div>
                  </section>
                )}
              </>
            ) : <div className="flex min-h-80 items-center justify-center border-y border-[var(--line)] bg-white px-6 text-center text-sm text-[var(--muted)]">创建或选择一个样本集后开始维护基准图片</div>}
          </main>
        </div>
      </div>
    </>
  )
}

function SampleItemRow({ item, saving, removing, onSave, onRemove }: { item: SampleSetItem; saving: boolean; removing: boolean; onSave: (expectedLevel: string | null, note: string) => void; onRemove: () => void }) {
  const [level, setLevel] = useState(item.expected_level || "")
  const [note, setNote] = useState(item.note)
  useEffect(() => { setLevel(item.expected_level || ""); setNote(item.note) }, [item.expected_level, item.note])
  const changed = level !== (item.expected_level || "") || note !== item.note
  return <article className="grid gap-4 border-b border-[var(--line)] p-4 last:border-0 lg:grid-cols-[72px_1fr_120px_minmax(220px,.8fr)_auto] lg:items-center">
    <img src={item.image_url} alt="" className="size-[72px] rounded-[4px] border border-[var(--line)] object-cover" loading="lazy" />
    <div className="min-w-0"><p className="truncate text-sm font-semibold">{item.asset_name}</p><p className="mt-2 text-xs text-[var(--muted)]">{item.expected_category}</p><p className="font-data mt-1 truncate text-[0.68rem] text-[var(--muted)]">来源 {item.source_model_id} · {item.source_level || "无等级"}</p></div>
    <label><span className="mb-2 block text-xs font-semibold">人工基准</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={level} onChange={(event) => setLevel(event.target.value)}><option value="">无等级</option>{["L1", "L2", "L3", "L4", "L5"].map((value) => <option key={value}>{value}</option>)}</select></label>
    <label><span className="mb-2 block text-xs font-semibold">基准备注</span><Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录难点或判断依据" /></label>
    <div className="flex flex-wrap gap-2 lg:justify-end"><Button size="sm" variant="secondary" disabled={!changed || saving} onClick={() => onSave(level || null, note.trim())}>保存基准</Button><Button size="sm" variant="ghost" disabled={removing} onClick={onRemove}><Trash />移出</Button><Button asChild size="sm" variant="ghost"><Link to={`/review?asset=${item.asset_id}`}>查看<ArrowRight /></Link></Button></div>
  </article>
}
