import { useEffect, useMemo, useState } from "react"
import {
  ArrowClockwise,
  ArrowRight,
  CheckCircle,
  CheckSquare,
  ClockCounterClockwise,
  FileXls,
  FolderSimplePlus,
  Lock,
  MagnifyingGlass,
  Play,
  Plus,
  ShieldCheck,
  Square,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import { truthDimensionDefinitions } from "@/lib/dimension-schema"
import type {
  EvaluationRecord,
  PromptVersion,
  RegressionDetail,
  RegressionSummary,
  SampleItemHistory,
  SampleSetDetail,
  SampleSetItem,
  SampleSetSummary,
  SampleTruth,
} from "@/lib/types"

const MEDIA_FIELDS = [
  ["real_photo", "实景图"],
  ["rendering", "效果图"],
  ["ai_generated", "AI 生成"],
  ["professional_photography", "专业摄影"],
  ["casual_snapshot", "随拍"],
  ["documentary_record", "现场记录"],
] as const

export function SampleSetsPage() {
  const queryClient = useQueryClient()
  const [workspace, setWorkspace] = useState<"samples" | "regressions">("samples")
  const [setKind, setSetKind] = useState<"golden" | "test">("golden")
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [historyItemId, setHistoryItemId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [mode, setMode] = useState<"included" | "available">("included")
  const [search, setSearch] = useState("")
  const [selectedAssets, setSelectedAssets] = useState<Set<number>>(new Set())
  const [batchLevel, setBatchLevel] = useState("")

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
    queryKey: ["evaluations", "sample-set-candidates"],
    queryFn: () => api<{ items: EvaluationRecord[]; total: number }>("/api/evaluations?limit=1000"),
    enabled: mode === "available",
  })
  const history = useQuery({
    queryKey: ["sample-history", selectedId, historyItemId],
    queryFn: () => api<SampleItemHistory>(`/api/sample-sets/${selectedId}/items/${historyItemId}/history`),
    enabled: Boolean(selectedId && historyItemId),
  })
  const regressions = useQuery({
    queryKey: ["prompt-regressions"],
    queryFn: () => api<{ items: RegressionSummary[] }>("/api/prompt-regressions"),
    refetchInterval: (query) => query.state.data?.items.some((run) => ["queued", "running"].includes(run.status)) ? 2500 : false,
  })
  const regressionDetail = useQuery({
    queryKey: ["prompt-regression", selectedRunId],
    queryFn: () => api<RegressionDetail>(`/api/prompt-regressions/${selectedRunId}`),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.summary.status || "") ? 2500 : false,
  })
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })

  const visibleSets = useMemo(() => (sets.data?.items ?? []).filter((item) => item.kind === setKind), [sets.data?.items, setKind])
  useEffect(() => {
    if (!visibleSets.some((item) => item.id === selectedId)) setSelectedId(visibleSets[0]?.id ?? null)
  }, [visibleSets, selectedId])
  useEffect(() => {
    if (!selectedRunId && regressions.data?.items.length) setSelectedRunId(regressions.data.items[0].id)
  }, [regressions.data?.items, selectedRunId])
  useEffect(() => {
    setMode("included")
    setHistoryItemId(null)
    setSelectedAssets(new Set())
  }, [selectedId])

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["sample-sets"] }),
      queryClient.invalidateQueries({ queryKey: ["sample-set", selectedId] }),
      queryClient.invalidateQueries({ queryKey: ["sample-history"] }),
      queryClient.invalidateQueries({ queryKey: ["prompt-regressions"] }),
    ])
  }

  const createSet = useMutation({
    mutationFn: () => api<{ id: number }>("/api/sample-sets", {
      method: "POST",
      ...jsonBody({ name: name.trim(), description: description.trim(), kind: setKind }),
    }),
    onSuccess: async ({ id }) => {
      setSelectedId(id)
      setCreating(false)
      setName("")
      setDescription("")
      await refresh()
      toast.success(setKind === "golden" ? "黄金样本集已创建" : "测试样本集已创建")
    },
    onError: (error) => toast.error(error.message),
  })
  const addItems = useMutation({
    mutationFn: () => api(`/api/sample-sets/${selectedId}/items`, {
      method: "POST",
      ...jsonBody({ asset_ids: Array.from(selectedAssets), expected_level: batchLevel || null }),
    }),
    onSuccess: async () => {
      setSelectedAssets(new Set())
      setBatchLevel("")
      setMode("included")
      await refresh()
      toast.success("素材已收录，标准答案已从人工审核结果建立")
    },
    onError: (error) => toast.error(error.message),
  })
  const removeItem = useMutation({
    mutationFn: (itemId: number) => api(`/api/sample-sets/${selectedId}/items/${itemId}`, { method: "DELETE" }),
    onSuccess: async () => { await refresh(); toast.success("已移出样本集") },
    onError: (error) => toast.error(error.message),
  })
  const setStatus = useMutation({
    mutationFn: (status: "draft" | "locked") => api(`/api/sample-sets/${selectedId}/status`, { method: "PATCH", ...jsonBody({ status }) }),
    onSuccess: async (_, status) => { await refresh(); toast.success(status === "locked" ? "黄金标准已锁定，将参与每次发布回归" : "已解锁，可继续调整标准答案") },
    onError: (error) => toast.error(error.message),
  })
  const runRegression = useMutation({
    mutationFn: () => api<{ ids: number[] }>("/api/prompt-regressions", { method: "POST", ...jsonBody({ sample_set_id: selectedId }) }),
    onSuccess: async ({ ids }) => { setWorkspace("regressions"); setSelectedRunId(ids[0]); await refresh(); toast.success("已对全部黄金样本启动回归") },
    onError: (error) => toast.error(error.message),
  })

  const includedIds = useMemo(() => new Set(detail.data?.items.map((item) => item.asset_id) ?? []), [detail.data?.items])
  const availableAssets = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    const latestReviewed = new Map<number, EvaluationRecord>()
    for (const asset of assets.data?.items ?? []) {
      if (["approved", "corrected"].includes(asset.evaluation.human_review?.decision || "") && !latestReviewed.has(asset.id)) latestReviewed.set(asset.id, asset)
    }
    return Array.from(latestReviewed.values()).filter((asset) => !includedIds.has(asset.id) && (!keyword || asset.name.toLowerCase().includes(keyword) || String(asset.id).includes(keyword)))
  }, [assets.data?.items, includedIds, search])

  return <>
    <PageHeader
      index="07"
      title="样本与回归"
      description="黄金样本守住发布质量，测试样本验证泛化；每一次模型、提示词与人工修改都保留历史。"
      actions={<>
        <Button asChild variant="secondary"><Link to="/historical-corrections"><FileXls />历史纠偏预览</Link></Button>
        <Button variant="secondary" onClick={() => refresh()}><ArrowClockwise />刷新状态</Button>
        {workspace === "samples" && <Button onClick={() => setCreating((value) => !value)}><FolderSimplePlus />创建样本集</Button>}
      </>}
    />
    <div className="mx-auto max-w-[1640px] px-5 py-7 md:px-8 lg:px-10 lg:py-9">
      <div className="mb-7 flex flex-wrap items-center justify-between gap-4 border-b border-[var(--line-strong)]">
        <div className="flex gap-7">
          <WorkspaceTab active={workspace === "samples"} onClick={() => setWorkspace("samples")}>样本库</WorkspaceTab>
          <WorkspaceTab active={workspace === "regressions"} onClick={() => setWorkspace("regressions")}>回归记录 <span className="font-data text-xs text-[var(--muted)]">{regressions.data?.items.length ?? 0}</span></WorkspaceTab>
        </div>
        <p className="pb-3 text-xs text-[var(--muted)]">发布新提示词后，系统会自动对所有已锁定黄金样本全量回归</p>
      </div>

      {workspace === "samples" ? <>
        <div className="mb-5 flex gap-2">
          <Button size="sm" variant={setKind === "golden" ? "primary" : "secondary"} onClick={() => setSetKind("golden")}><ShieldCheck />黄金样本</Button>
          <Button size="sm" variant={setKind === "test" ? "primary" : "secondary"} onClick={() => setSetKind("test")}>普通测试集</Button>
        </div>
        {creating && <section className="mb-7 grid gap-4 border-y border-[var(--line-strong)] bg-white p-5 lg:grid-cols-[1fr_1.5fr_auto] lg:items-end">
          <label><FieldLabel>名称</FieldLabel><Input value={name} onChange={(event) => setName(event.target.value)} placeholder={setKind === "golden" ? "例如：空间美感黄金集 V1" : "例如：新来源泛化测试 07月"} /></label>
          <label><FieldLabel>用途说明</FieldLabel><Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明覆盖范围、难例或本批验证目标" /></label>
          <div className="flex gap-2"><Button variant="secondary" onClick={() => setCreating(false)}>取消</Button><Button disabled={!name.trim() || createSet.isPending} onClick={() => createSet.mutate()}>保存</Button></div>
        </section>}

        <div className="grid gap-8 xl:grid-cols-[300px_minmax(0,1fr)]">
          <aside>
            <div className="mb-3 flex items-end justify-between"><div><h2 className="text-xl font-bold">{setKind === "golden" ? "黄金基准" : "测试批次"}</h2><p className="mt-1 text-xs text-[var(--muted)]">{setKind === "golden" ? "锁定后进入发布回归" : "用于探索和泛化验证"}</p></div><span className="font-data text-xs text-[var(--muted)]">{visibleSets.length}</span></div>
            <div className="border-y border-[var(--line-strong)] bg-white">
              {visibleSets.length ? visibleSets.map((sampleSet) => <button key={sampleSet.id} onClick={() => setSelectedId(sampleSet.id)} className={`w-full border-b border-[var(--line)] px-4 py-4 text-left last:border-0 ${selectedId === sampleSet.id ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"}`}>
                <div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-semibold">{sampleSet.name}</span><Badge tone={sampleSet.status === "locked" ? "success" : sampleSet.item_count ? "active" : "neutral"}>{sampleSet.status === "locked" ? "已锁定" : `${sampleSet.item_count} 张`}</Badge></div>
                <p className="mt-2 text-xs text-[var(--muted)]">标准完整 {sampleSet.truth_complete_count}/{sampleSet.item_count}</p>
              </button>) : <EmptySet kind={setKind} onCreate={() => setCreating(true)} />}
            </div>
          </aside>

          <main className="min-w-0">
            {detail.data ? <>
              <div className="flex flex-wrap items-end justify-between gap-4 border-b border-[var(--line-strong)] pb-5">
                <div><div className="flex items-center gap-2"><Badge tone={detail.data.summary.kind === "golden" ? "active" : "neutral"}>{detail.data.summary.kind === "golden" ? "黄金样本" : "普通测试"}</Badge>{detail.data.summary.status === "locked" && <Badge tone="success"><Lock />已锁定</Badge>}</div><h2 className="mt-3 text-3xl font-bold">{detail.data.summary.name}</h2><p className="mt-2 max-w-[72ch] text-sm text-[var(--muted)]">{detail.data.summary.description || "尚未填写用途说明"}</p></div>
                <div className="flex flex-wrap gap-2">
                  {detail.data.summary.kind === "golden" && (detail.data.summary.status === "locked" ? <Button variant="secondary" onClick={() => setStatus.mutate("draft")}>解锁标准</Button> : <Button variant="secondary" onClick={() => setStatus.mutate("locked")}><Lock />锁定黄金标准</Button>)}
                  {detail.data.summary.kind === "golden" && detail.data.summary.status === "locked" && <Button onClick={() => runRegression.mutate()} disabled={runRegression.isPending}><Play />立即全量回归</Button>}
                </div>
              </div>
              <div className="mt-5 flex gap-2"><Button size="sm" variant={mode === "included" ? "primary" : "secondary"} onClick={() => setMode("included")}>已收录 {detail.data.summary.item_count}</Button><Button size="sm" variant={mode === "available" ? "primary" : "secondary"} onClick={() => setMode("available")}><Plus />添加素材</Button></div>
              {mode === "included" ? <section className="mt-4 border-y border-[var(--line-strong)] bg-white">
                {detail.data.items.length ? <><div className="hidden grid-cols-[72px_minmax(200px,1fr)_110px_150px_140px] gap-4 border-b border-[var(--line)] px-4 py-3 text-xs font-semibold text-[var(--muted)] lg:grid"><span>图片</span><span>标准答案</span><span>完整度</span><span>最近来源</span><span className="text-right">操作</span></div>{detail.data.items.map((item) => <SampleRow key={item.id} item={item} selected={historyItemId === item.id} onHistory={() => setHistoryItemId(item.id)} onRemove={() => removeItem.mutate(item.id)} />)}</> : <EmptyItems onAdd={() => setMode("available")} />}
              </section> : <AddAssetsPanel search={search} setSearch={setSearch} batchLevel={batchLevel} setBatchLevel={setBatchLevel} assets={availableAssets} selected={selectedAssets} setSelected={setSelectedAssets} onAdd={() => addItems.mutate()} pending={addItems.isPending} />}
              {historyItemId && <SampleInspector data={history.data} loading={history.isLoading} sampleSetId={selectedId!} onSaved={refresh} />}
            </> : <div className="flex min-h-80 items-center justify-center border-y border-[var(--line)] bg-white text-sm text-[var(--muted)]">创建或选择一个样本集后开始维护</div>}
          </main>
        </div>
      </> : <RegressionWorkspace runs={regressions.data?.items ?? []} selectedId={selectedRunId} setSelectedId={setSelectedRunId} detail={regressionDetail.data} prompts={(prompts.data?.items ?? []).filter((prompt) => prompt.category_key === detail.data?.summary.category_key)} />}
    </div>
  </>
}

function WorkspaceTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className={`flex items-center gap-2 border-b-2 px-1 pb-3 text-sm font-bold ${active ? "border-black text-black" : "border-transparent text-[var(--muted)] hover:text-black"}`}>{children}</button>
}

function FieldLabel({ children }: { children: React.ReactNode }) { return <span className="mb-2 block text-xs font-semibold">{children}</span> }

function EmptySet({ kind, onCreate }: { kind: "golden" | "test"; onCreate: () => void }) { return <div className="px-5 py-12 text-center"><FolderSimplePlus size={28} className="mx-auto" /><p className="mt-3 text-sm font-semibold">还没有{kind === "golden" ? "黄金样本" : "测试集"}</p><Button size="sm" className="mt-4" onClick={onCreate}>创建第一个</Button></div> }

function EmptyItems({ onAdd }: { onAdd: () => void }) { return <div className="px-6 py-16 text-center"><Square size={28} className="mx-auto" /><h3 className="mt-4 text-xl font-bold">样本集还是空的</h3><p className="mt-2 text-sm text-[var(--muted)]">从已完成人工审核的素材中建立可靠标准。</p><Button className="mt-5" onClick={onAdd}><Plus />添加第一批素材</Button></div> }

function truthCompleteness(truth: SampleTruth) {
  const definitions = truthDimensionDefinitions(truth)
  const dimensions = definitions.filter(
    ({ key }) => Number(truth.dimensions?.[key]),
  ).length
  const core = Boolean(truth.level && truth.category && truth.quality_severity)
  const bound = Boolean(
    truth.dimension_schema?.canonical_hash && definitions.length,
  )
  return {
    dimensions,
    expected: definitions.length,
    bound,
    complete: core && bound && dimensions === definitions.length,
  }
}

function SampleRow({ item, selected, onHistory, onRemove }: { item: SampleSetItem; selected: boolean; onHistory: () => void; onRemove: () => void }) {
  const completeness = truthCompleteness(item.truth)
  return <article className={`grid gap-4 border-b border-[var(--line)] p-4 last:border-0 lg:grid-cols-[72px_minmax(200px,1fr)_110px_150px_140px] lg:items-center ${selected ? "bg-[#f7f9ef]" : ""}`}>
    <img src={item.image_url} alt="" className="size-[72px] rounded-[4px] border border-[var(--line)] object-cover" loading="lazy" />
    <div className="min-w-0"><p className="file-name truncate text-sm">{item.asset_name}</p><div className="mt-2 flex flex-wrap gap-2"><Badge tone="active">{item.truth.level || "未定级"}</Badge><span className="text-xs text-[var(--muted)]">{item.truth.category || item.expected_category}</span></div></div>
    <div><p className="text-sm font-semibold">{completeness.dimensions}/{completeness.expected || "?"} 维</p><p className="mt-1 text-xs text-[var(--muted)]">{completeness.complete ? "标准完整" : completeness.bound ? "需要补充" : "历史规则未绑定"}</p></div>
    <div className="min-w-0"><p className="font-data truncate text-xs">{item.source_model_id}</p><p className="mt-1 text-xs text-[var(--muted)]">标准 V{item.truth_revision}</p></div>
    <div className="flex flex-wrap justify-end gap-1"><Button size="sm" variant="secondary" onClick={onHistory}>标准与历史<ArrowRight /></Button><Button size="sm" variant="ghost" onClick={onRemove} aria-label="移出样本集"><Trash /></Button></div>
  </article>
}

function AddAssetsPanel({ search, setSearch, batchLevel, setBatchLevel, assets, selected, setSelected, onAdd, pending }: { search: string; setSearch: (v: string) => void; batchLevel: string; setBatchLevel: (v: string) => void; assets: EvaluationRecord[]; selected: Set<number>; setSelected: (v: Set<number>) => void; onAdd: () => void; pending: boolean }) {
  return <section className="mt-4"><div className="grid gap-4 border-y border-[var(--line-strong)] bg-white p-4 lg:grid-cols-[1fr_190px_auto] lg:items-end"><label><FieldLabel>搜索已审核素材</FieldLabel><div className="relative"><MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" /><Input className="pl-10" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="文件名或素材编号" /></div></label><label><FieldLabel>整批最终等级（可选）</FieldLabel><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={batchLevel} onChange={(event) => setBatchLevel(event.target.value)}><option value="">沿用人工结果</option>{["L1", "L2", "L3", "L4", "L5"].map((level) => <option key={level}>{level}</option>)}</select></label><Button disabled={!selected.size || pending} onClick={onAdd}>收录所选 {selected.size ? `(${selected.size})` : ""}</Button></div><div className="mt-4 max-h-[62vh] overflow-y-auto border-y border-[var(--line-strong)] bg-white">{assets.length ? assets.map((asset) => { const checked = selected.has(asset.id); return <button key={asset.id} className={`grid w-full grid-cols-[32px_56px_1fr_auto] items-center gap-3 border-b border-[var(--line)] px-4 py-3 text-left ${checked ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"}`} onClick={() => { const next = new Set(selected); checked ? next.delete(asset.id) : next.add(asset.id); setSelected(next) }}>{checked ? <CheckSquare size={20} weight="fill" /> : <Square size={20} />}<img src={asset.image_url} alt="" className="size-14 rounded-[4px] object-cover" /><div className="min-w-0"><p className="file-name truncate text-sm">{asset.name}</p><p className="mt-1 text-xs text-[var(--muted)]">#{String(asset.id).padStart(5, "0")} · {asset.evaluation?.versions.model}</p></div><Badge tone="active">人工 {asset.evaluation?.final_level}</Badge></button> }) : <div className="px-6 py-14 text-center text-sm text-[var(--muted)]">没有可收录的已审核素材</div>}</div></section>
}

function SampleInspector({ data, loading, sampleSetId, onSaved }: { data?: SampleItemHistory; loading: boolean; sampleSetId: number; onSaved: () => Promise<void> }) {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<"truth" | "runs" | "reviews">("truth")
  const [truth, setTruth] = useState<SampleTruth>({})
  const [note, setNote] = useState("")
  const [reason, setReason] = useState("")
  useEffect(() => { if (data) { setTruth(data.item.truth); setNote(data.item.note); setReason("") } }, [data?.item.id, data?.item.truth_revision])
  const save = useMutation({
    mutationFn: () => api(`/api/sample-sets/${sampleSetId}/items/${data?.item.id}`, { method: "PATCH", ...jsonBody({ expected_level: truth.level || null, note, truth, revision_reason: reason }) }),
    onSuccess: async () => { await onSaved(); await queryClient.invalidateQueries({ queryKey: ["sample-history"] }); toast.success("标准答案已保存为新版本") },
    onError: (error) => toast.error(error.message),
  })
  if (loading || !data) return <div className="mt-6 h-64 animate-pulse border-y border-[var(--line)] bg-white" />
  const truthDefinitions = truthDimensionDefinitions(truth)
  const truthSchemaBound = Boolean(
    truth.dimension_schema?.canonical_hash && truthDefinitions.length,
  )
  const updateDimension = (key: string, value: number) => setTruth((current) => ({ ...current, dimensions: { ...current.dimensions, [key]: value } }))
  const updateMedia = (key: string, value: "yes" | "no" | "uncertain") => setTruth((current) => ({ ...current, media_form: { ...current.media_form, [key]: value } }))
  return <section className="mt-7 border-y border-[var(--line-strong)] bg-white">
    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] p-5"><div><p className="file-name text-sm">{data.item.asset_name}</p><h3 className="mt-2 text-2xl font-bold">标准答案与完整历史</h3><p className="mt-1 text-xs text-[var(--muted)]">当前标准 V{data.item.truth_revision} · 模型运行 {data.evaluations.length} 次 · 人工记录 {data.evaluations.reduce((sum, item) => sum + item.reviews.length, 0)} 条</p></div><Button asChild size="sm" variant="secondary"><Link to={`/review/completed?asset=${data.item.asset_id}`}>查看大图<ArrowRight /></Link></Button></div>
    <div className="flex gap-6 border-b border-[var(--line)] px-5 pt-4"><WorkspaceTab active={tab === "truth"} onClick={() => setTab("truth")}>黄金标准</WorkspaceTab><WorkspaceTab active={tab === "runs"} onClick={() => setTab("runs")}>模型与回归历史</WorkspaceTab><WorkspaceTab active={tab === "reviews"} onClick={() => setTab("reviews")}>人工修改历史</WorkspaceTab></div>
    {tab === "truth" && <div className="p-5"><div className="grid gap-5 xl:grid-cols-[220px_1fr]">
      <div className="space-y-4"><label><FieldLabel>最终等级</FieldLabel><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3" value={truth.level || ""} onChange={(event) => setTruth({ ...truth, level: event.target.value })}><option value="">未定级</option>{["L1", "L2", "L3", "L4", "L5"].map((level) => <option key={level}>{level}</option>)}</select></label><label><FieldLabel>主分类</FieldLabel><Input value={truth.category || ""} onChange={(event) => setTruth({ ...truth, category: event.target.value })} /></label><label><FieldLabel>画质问题</FieldLabel><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3" value={truth.quality_severity || "uncertain"} onChange={(event) => setTruth({ ...truth, quality_severity: event.target.value })}>{[["normal", "画质正常"], ["slight", "轻微"], ["moderate", "中度"], ["severe", "严重"], ["unusable", "不可用"], ["uncertain", "不确定"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
      <div><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-semibold">{truthSchemaBound ? `${truthDefinitions.length} 个美感维度` : "历史维度记录"}</p>{truthSchemaBound ? <Badge tone="active">{truth.dimension_schema?.schema_key} · {truth.dimension_schema?.version}</Badge> : <Badge tone="warning">规则未绑定，仅供查看</Badge>}</div>{truthSchemaBound ? <div className="mt-2 grid gap-px border border-[var(--line)] bg-[var(--line)] sm:grid-cols-2">{truthDefinitions.map(({ key, label }) => <label key={key} className="flex items-center justify-between gap-3 bg-white px-3 py-3"><span className="text-xs">{label}</span><select className="h-9 w-16 rounded-[4px] border border-[var(--line)] bg-white px-2 text-sm font-semibold" value={truth.dimensions?.[key] || ""} onChange={(event) => updateDimension(key, Number(event.target.value))}><option value="">—</option>{[1, 2, 3, 4, 5].map((grade) => <option key={grade}>{grade}</option>)}</select></label>)}</div> : <div className="mt-2 grid gap-px border border-[var(--line)] bg-[var(--line)] sm:grid-cols-2">{Object.entries(truth.dimensions || {}).map(([key, grade]) => <div key={key} className="flex items-center justify-between gap-3 bg-white px-3 py-3"><span className="font-data text-xs">{key}</span><span className="text-sm font-semibold">{grade || "—"}</span></div>)}</div>}<p className="mt-5 text-xs font-semibold">图片形态</p><div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{MEDIA_FIELDS.map(([key, label]) => <label key={key}><span className="mb-1 block text-xs text-[var(--muted)]">{label}</span><select className="h-9 w-full rounded-[4px] border border-[var(--line)] bg-white px-2 text-xs" value={truth.media_form?.[key] || "uncertain"} onChange={(event) => updateMedia(key, event.target.value as "yes" | "no" | "uncertain")}><option value="yes">是</option><option value="no">否</option><option value="uncertain">不确定</option></select></label>)}</div></div>
    </div><div className="mt-6 grid gap-4 border-t border-[var(--line)] pt-5 lg:grid-cols-[1fr_1fr_auto] lg:items-end"><label><FieldLabel>标准备注</FieldLabel><Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录难点和判断依据" /></label><label><FieldLabel>本次修改原因</FieldLabel><Input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="例如：人工复核确认画质应为中度异常" /></label><Button onClick={() => save.mutate()} disabled={save.isPending}>保存为 V{data.item.truth_revision + 1}</Button></div></div>}
    {tab === "runs" && <HistoryRuns data={data} />}
    {tab === "reviews" && <ReviewHistory data={data} />}
  </section>
}

function HistoryRuns({ data }: { data: SampleItemHistory }) {
  return <div className="divide-y divide-[var(--line)]">{data.evaluations.map((evaluation) => <div key={evaluation.id} className="grid gap-4 p-5 lg:grid-cols-[180px_1fr_180px]"><div><p className="font-data text-xs text-[var(--muted)]">{new Date(evaluation.created_at).toLocaleString("zh-CN")}</p><p className="mt-2 text-sm font-semibold">{evaluation.versions.model}</p></div><div><p className="text-sm font-semibold">A {evaluation.versions.prompt_a} · B {evaluation.versions.prompt_b || "未调用"}</p><p className="mt-2 text-xs text-[var(--muted)]">引擎 {evaluation.versions.engine} · 规则 {evaluation.versions.rubric}{evaluation.versions.risk_review ? ` · 复核 ${evaluation.versions.risk_review}` : ""}</p>{evaluation.risk_review?.verdict === "downgrade" && <p className="mt-2 text-xs font-semibold text-[#7d4308]">高风险复核修正 {evaluation.risk_review.corrections?.length ?? 0} 项</p>}</div><div className="flex items-center justify-between lg:justify-end lg:gap-5"><span className="text-2xl font-bold">{evaluation.level || "—"}</span><span className="font-data text-sm">{evaluation.score ?? "—"}</span></div></div>)}{data.regressions.length > 0 && <div className="bg-[#fafbf8] px-5 py-3 text-xs font-semibold">回归记录 {data.regressions.length} 条，均可在“回归记录”中查看逐字段差异。</div>}</div>
}

function ReviewHistory({ data }: { data: SampleItemHistory }) {
  const reviews = data.evaluations.flatMap((evaluation) => evaluation.reviews.map((review) => ({ ...review, modelLevel: evaluation.level, evaluationId: evaluation.id }))).sort((a, b) => b.created_at.localeCompare(a.created_at))
  return <div className="divide-y divide-[var(--line)]">{reviews.length ? reviews.map((review) => <div key={review.id} className="grid gap-3 p-5 lg:grid-cols-[180px_160px_1fr]"><div><p className="text-sm font-semibold">{review.reviewer_name}</p><p className="mt-1 text-xs text-[var(--muted)]">{new Date(review.created_at).toLocaleString("zh-CN")}</p></div><div><Badge tone={review.decision === "corrected" ? "warning" : "success"}>{review.decision === "corrected" ? `修正为 ${review.corrected_level || "维度"}` : review.decision === "approved" ? "确认模型结果" : "退回"}</Badge><p className="mt-2 text-xs text-[var(--muted)]">模型原值 {review.modelLevel || "—"}</p></div><div><p className="text-sm leading-6">{review.note || "未填写说明"}</p>{review.corrections.map((correction, index) => <p key={index} className="mt-2 text-xs text-[var(--muted)]">{correction.field_key}：{String(correction.model_value)} → {String(correction.human_value)} · {correction.note}</p>)}</div></div>) : <div className="px-5 py-14 text-center text-sm text-[var(--muted)]">还没有人工审核记录</div>}</div>
}

function RegressionWorkspace({ runs, selectedId, setSelectedId, detail }: { runs: RegressionSummary[]; selectedId: number | null; setSelectedId: (id: number) => void; detail?: RegressionDetail; prompts: PromptVersion[] }) {
  return <div className="grid gap-8 xl:grid-cols-[340px_minmax(0,1fr)]"><aside><div className="mb-3"><h2 className="text-xl font-bold">发布回归</h2><p className="mt-1 text-xs text-[var(--muted)]">每个版本、每个黄金集独立留档</p></div><div className="border-y border-[var(--line-strong)] bg-white">{runs.length ? runs.map((run) => <button key={run.id} onClick={() => setSelectedId(run.id)} className={`w-full border-b border-[var(--line)] p-4 text-left ${selectedId === run.id ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"}`}><div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-semibold">{run.sample_set_name}</span><RunBadge status={run.status} /></div><p className="mt-2 truncate font-data text-xs text-[var(--muted)]">A {run.prompt_a_version} · B {run.prompt_b_version}</p><div className="mt-3 flex items-center justify-between text-xs"><span>{run.completed}/{run.total} 已完成</span><span className="font-semibold">{Math.round(run.pass_rate * 100)}%</span></div></button>) : <div className="px-5 py-14 text-center"><ClockCounterClockwise size={28} className="mx-auto" /><p className="mt-3 text-sm font-semibold">还没有回归记录</p><p className="mt-2 text-xs leading-5 text-[var(--muted)]">锁定黄金样本后发布提示词，系统将自动创建。</p></div>}</div></aside><main className="min-w-0">{detail ? <><div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line-strong)] pb-5"><div><p className="font-data text-xs text-[var(--muted)]">RUN #{String(detail.summary.id).padStart(4, "0")}</p><h2 className="mt-2 text-3xl font-bold">{detail.summary.sample_set_name}</h2><p className="mt-2 text-sm text-[var(--muted)]">A {detail.summary.prompt_a_version} · B {detail.summary.prompt_b_version}</p></div><RunBadge status={detail.summary.status} /></div><div className="mt-5 grid grid-cols-2 gap-px border-y border-[var(--line-strong)] bg-[var(--line)] md:grid-cols-4"><Metric value={`${detail.summary.completed}/${detail.summary.total}`} label="已完成" /><Metric value={`${Math.round(detail.summary.pass_rate * 100)}%`} label={`通过率 · 门槛 ${Math.round(detail.summary.threshold * 100)}%`} /><Metric value={detail.summary.passed} label="通过样本" /><Metric value={detail.summary.failed} label="退化或异常" /></div>{detail.summary.status === "regressed" && <div className="mt-4 flex items-start gap-2 border border-[#e4c7c3] bg-[#fff8f7] p-4 text-sm text-[#842e27]"><WarningCircle className="mt-0.5 shrink-0" />该组合低于发布门槛。请查看失败字段，修改提示词后另存新版本再回归，不要用新结果覆盖黄金标准。</div>}<div className="mt-5 border-y border-[var(--line-strong)] bg-white"><div className="hidden grid-cols-[64px_1fr_110px_110px_1.4fr] gap-4 border-b border-[var(--line)] px-4 py-3 text-xs font-semibold text-[var(--muted)] lg:grid"><span>图片</span><span>素材</span><span>标准/结果</span><span>状态</span><span>差异</span></div>{detail.items.map((item) => <div key={item.id} className="grid gap-3 border-b border-[var(--line)] p-4 last:border-0 lg:grid-cols-[64px_1fr_110px_110px_1.4fr] lg:items-center"><img src={item.image_url} alt="" className="size-16 rounded-[4px] object-cover" /><p className="file-name truncate text-sm">{item.asset_name}</p><p className="text-sm font-semibold">{item.expected.level || "—"} / {item.evaluation?.level || "—"}</p><RunBadge status={item.status} /><ComparisonSummary comparison={item.comparison} /></div>)}</div></> : <div className="flex min-h-80 items-center justify-center border-y border-[var(--line)] bg-white text-sm text-[var(--muted)]">选择一条回归记录查看逐样本差异</div>}</main></div>
}

function RunBadge({ status }: { status: string }) { const passed = status === "passed"; const failed = ["failed", "error", "regressed"].includes(status); return <Badge tone={passed ? "success" : failed ? "warning" : "active"}>{passed ? <CheckCircle /> : failed ? <WarningCircle /> : <ArrowClockwise />}{passed ? "通过" : status === "regressed" ? "发现退化" : status === "error" ? "异常" : status === "failed" ? "未通过" : "运行中"}</Badge> }
function Metric({ value, label }: { value: string | number; label: string }) { return <div className="bg-white p-5"><p className="font-data text-2xl font-semibold">{value}</p><p className="mt-1 text-xs text-[var(--muted)]">{label}</p></div> }
function ComparisonSummary({ comparison }: { comparison: Record<string, any> }) { const failures = (comparison.checks || []).filter((check: any) => !check.passed); return <div className="text-xs leading-5 text-[var(--muted)]">{comparison.error ? comparison.error : failures.length ? failures.slice(0, 2).map((check: any) => <p key={check.field}>{check.field}：{String(check.expected)} → {String(check.actual)}</p>) : comparison.checked ? `匹配 ${comparison.matched}/${comparison.checked} 项` : "等待结果"}</div> }
