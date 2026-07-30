import { useEffect, useMemo, useState } from "react"
import { ArrowsClockwise, Check, CircleNotch, Flask, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { MigrationContext, MigrationDetail, MigrationItem, MigrationSummary, User } from "@/lib/types"


const statusLabel: Record<MigrationSummary["status"], string> = {
  running: "候选模型运行中",
  review: "等待人工复核",
  accepted: "样本验收通过",
  regressed: "发现回退样本",
}

export function MigrationsPage({ user }: { user: User }) {
  const queryClient = useQueryClient()
  const context = useQuery({
    queryKey: ["migration-context"],
    queryFn: () => api<MigrationContext>("/api/migrations/context"),
  })
  const runs = useQuery({
    queryKey: ["migrations"],
    queryFn: () => api<{ items: MigrationSummary[] }>("/api/migrations"),
    refetchInterval: 4000,
  })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [baseline, setBaseline] = useState("")
  const [sampleSize, setSampleSize] = useState(200)
  const [sampleSetId, setSampleSetId] = useState("")
  const [name, setName] = useState("")
  const reviewer = user.username

  useEffect(() => {
    if (!baseline && context.data?.baselines.length) {
      const available = context.data.baselines.find(
        (item) => item.model_id !== context.data?.candidate.model_id,
      )
      if (available) setBaseline(available.model_id)
    }
  }, [baseline, context.data])
  useEffect(() => {
    if (!selectedId && runs.data?.items.length) setSelectedId(runs.data.items[0].id)
  }, [runs.data?.items, selectedId])

  const detail = useQuery({
    queryKey: ["migration", selectedId],
    queryFn: () => api<MigrationDetail>(`/api/migrations/${selectedId}`),
    enabled: Boolean(selectedId),
    refetchInterval: 4000,
  })

  const createRun = useMutation({
    mutationFn: () => api<{ id: number }>("/api/migrations", {
      method: "POST",
      ...jsonBody({
        name: name || `${baseline} → ${context.data?.candidate.model_id}`,
        baseline_model_id: baseline,
        sample_size: sampleSize,
        sample_set_id: sampleSetId ? Number(sampleSetId) : null,
      }),
    }),
    onSuccess: async (data) => {
      setSelectedId(data.id)
      setName("")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["migrations"] }),
        queryClient.invalidateQueries({ queryKey: ["migration-context"] }),
      ])
      toast.success(sampleSetId ? "已使用固定样本集创建迁移评测" : "已创建分层抽样迁移评测")
    },
    onError: (error) => toast.error(error.message),
  })

  const review = useMutation({
    mutationFn: ({ itemId, verdict }: { itemId: number; verdict: string }) =>
      api(`/api/migrations/${selectedId}/items/${itemId}/review`, {
        method: "POST",
        ...jsonBody({ verdict, reviewer_name: reviewer, note: "" }),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["migration", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["migrations"] }),
      ])
      toast.success("复核结论已保存")
    },
    onError: (error) => toast.error(error.message),
  })

  const reviewItems = useMemo(
    () => detail.data?.items.filter((item) => item.requires_review) ?? [],
    [detail.data?.items],
  )

  return (
    <>
      <PageHeader
        index="08"
        title="模型迁移"
        description="旧模型停止服务也没关系：使用已保存的历史结果作为基线，让新模型只重跑分层样本，并把差异项交给人工。"
        actions={<Button variant="secondary" onClick={() => Promise.all([context.refetch(), runs.refetch(), detail.refetch()])}><ArrowsClockwise />刷新状态</Button>}
      />
      <div className="mx-auto max-w-[1540px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <section className="grid border-y border-[var(--line-strong)] bg-white lg:grid-cols-[280px_1fr]">
          <div className="border-b border-[var(--line)] p-5 lg:border-b-0 lg:border-r">
            <p className="font-data text-xs text-[var(--muted)]">CANDIDATE MODEL</p>
            <h2 className="font-editorial mt-3 text-2xl font-bold">当前候选模型</h2>
            <p className="mt-5 break-all text-sm font-semibold">{context.data?.candidate.model_id || "尚未配置"}</p>
            <Badge className="mt-3" tone={context.data?.candidate.has_api_key ? "success" : "warning"}>
              {context.data?.candidate.has_api_key ? "API Key 已配置" : "先配置 API Key，任务会保持排队"}
            </Badge>
          </div>
          <div className="grid gap-5 p-5 md:grid-cols-2 xl:grid-cols-[1.35fr_1.1fr_120px_1fr_auto] xl:items-end">
            <label className="block"><span className="mb-2 block text-xs font-semibold">旧模型基线</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={baseline} onChange={(event) => setBaseline(event.target.value)}><option value="">选择已有历史结果</option>{context.data?.baselines.filter((item) => item.model_id !== context.data?.candidate.model_id).map((item) => <option key={item.model_id} value={item.model_id}>{item.model_id} · {item.asset_count} 张</option>)}</select></label>
            <label className="block"><span className="mb-2 block text-xs font-semibold">样本来源</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={sampleSetId} onChange={(event) => setSampleSetId(event.target.value)}><option value="">自动分层抽样</option>{context.data?.sample_sets.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.item_count} 张</option>)}</select></label>
            <label className="block"><span className="mb-2 block text-xs font-semibold">{sampleSetId ? "固定数量" : "样本数"}</span><Input type="number" min="1" max="500" value={sampleSetId ? context.data?.sample_sets.find((item) => String(item.id) === sampleSetId)?.item_count ?? 0 : sampleSize} disabled={Boolean(sampleSetId)} onChange={(event) => setSampleSize(Number(event.target.value))} /></label>
            <label className="block"><span className="mb-2 block text-xs font-semibold">评测名称（可选）</span><Input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Seed 2.0 迁移验收" /></label>
            <Button onClick={() => createRun.mutate()} disabled={!baseline || createRun.isPending}><Flask />开始抽样</Button>
          </div>
        </section>

        {!context.isLoading && !context.data?.baselines.some((item) => item.model_id !== context.data?.candidate.model_id) && (
          <div className="mt-6 border-y border-[var(--line)] bg-[#fafbf8] px-5 py-4 text-sm leading-6 text-[var(--muted)]">
            暂时没有可用的旧模型历史结果。先完成一批正常评测；以后更换模型 ID 后，这批结果会自动成为可选基线，不需要旧模型继续在线。
          </div>
        )}

        <div className="mt-9 grid gap-8 xl:grid-cols-[300px_1fr]">
          <aside>
            <div className="mb-3 flex items-center justify-between"><h2 className="font-editorial text-2xl font-bold">迁移批次</h2><span className="font-data text-xs text-[var(--muted)]">{runs.data?.items.length ?? 0}</span></div>
            <div className="border-y border-[var(--line-strong)] bg-white">
              {runs.data?.items.length ? runs.data.items.map((run) => (
                <button key={run.id} onClick={() => setSelectedId(run.id)} className={`w-full border-b border-[var(--line)] px-4 py-4 text-left last:border-0 ${selectedId === run.id ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"}`}>
                  <div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-semibold">{run.name}</span><Badge tone={run.status === "accepted" ? "success" : run.status === "regressed" ? "danger" : "warning"}>{statusLabel[run.status]}</Badge></div>
                  <p className="font-data mt-2 truncate text-xs text-[var(--muted)]">{run.baseline_model_id} → {run.candidate_model_id}</p>
                  <p className="mt-2 text-xs text-[var(--muted)]">{run.completed}/{run.sample_size} 完成 · {run.review_required - run.reviewed} 待复核</p>
                </button>
              )) : <div className="px-5 py-12 text-center"><Flask size={26} className="mx-auto" /><p className="mt-3 text-sm font-semibold">还没有迁移批次</p></div>}
            </div>
          </aside>

          <main className="min-w-0">
            {detail.data ? <MigrationRunDetail detail={detail.data} reviewer={reviewer} onReview={(itemId, verdict) => review.mutate({ itemId, verdict })} reviewing={review.isPending} reviewItems={reviewItems} /> : <div className="flex min-h-80 items-center justify-center border-y border-[var(--line)] bg-white text-sm text-[var(--muted)]">选择或创建一个迁移批次</div>}
          </main>
        </div>
      </div>
    </>
  )
}

function MigrationRunDetail({ detail, reviewer, onReview, reviewing, reviewItems }: { detail: MigrationDetail; reviewer: string; onReview: (itemId: number, verdict: string) => void; reviewing: boolean; reviewItems: MigrationItem[] }) {
  const summary = detail.summary
  return <>
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="font-data text-xs text-[var(--muted)]">RUN #{String(summary.id).padStart(4, "0")}</p><h2 className="font-editorial mt-2 text-3xl font-bold">{summary.name}</h2></div><Badge tone={summary.status === "accepted" ? "success" : summary.status === "regressed" ? "danger" : "warning"}>{statusLabel[summary.status]}</Badge></div>
    <div className="mt-5 grid border-y border-[var(--line-strong)] sm:grid-cols-4">{[
      ["已完成", `${summary.completed}/${summary.sample_size}`],
      ["自动一致", `${Math.round(summary.auto_exact_rate * 100)}%`],
      ["需人工", String(summary.review_required)],
      ["待处理", String(Math.max(0, summary.review_required - summary.reviewed))],
    ].map(([label, value], index) => <div key={label} className={`p-4 ${index < 3 ? "sm:border-r" : ""}`}><p className="text-xs text-[var(--muted)]">{label}</p><p className="font-data mt-3 text-2xl font-semibold">{value}</p></div>)}</div>
    <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 border-y border-[var(--line)] bg-[#fafbf8] px-4 py-3 text-xs">
      <span className="font-semibold">人工对比结论</span>
      <span>旧模型更好 <strong className="font-data ml-1">{summary.verdicts.baseline_better}</strong></span>
      <span>效果相当 <strong className="font-data ml-1">{summary.verdicts.same}</strong></span>
      <span>新模型更好 <strong className="font-data ml-1">{summary.verdicts.candidate_better}</strong></span>
      <span className="text-[var(--muted)]">出现人工确认的旧模型更好样本时，批次会标记为“发现回退样本”。</span>
    </div>
    <div className="mt-6 flex flex-wrap items-center justify-between gap-4"><div><h3 className="font-editorial text-xl font-bold">差异与抽检队列</h3><p className="mt-1 text-sm text-[var(--muted)]">只展示等级/分类变化、低置信度和约 5% 的一致样本抽检。</p></div><label className="w-52"><span className="mb-2 block text-xs font-semibold">审核账号（当前登录）</span><Input value={reviewer} readOnly /></label></div>
    <div className="mt-4 border-y border-[var(--line-strong)] bg-white">
      {summary.pending > 0 && <div className="flex items-center gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-4 py-3 text-sm"><CircleNotch className="status-pulse" />候选模型仍在处理 {summary.pending} 张样本</div>}
      {reviewItems.length ? reviewItems.map((item) => <MigrationReviewRow key={item.id} item={item} reviewing={reviewing} onReview={onReview} />) : <div className="px-6 py-14 text-center"><Check size={28} className="mx-auto" /><p className="mt-3 text-sm font-semibold">暂无需要人工介入的样本</p></div>}
    </div>
    <p className="mt-4 flex items-start gap-2 text-xs leading-5 text-[var(--muted)]"><WarningCircle className="mt-0.5 shrink-0" />“样本验收通过”表示本次分层样本未发现人工确认的回退，不等于对全部未来图片做绝对保证；上线后仍建议保留小比例漂移抽检。</p>
  </>
}

function MigrationReviewRow({ item, reviewing, onReview }: { item: MigrationItem; reviewing: boolean; onReview: (itemId: number, verdict: string) => void }) {
  return <div className="grid gap-4 border-b border-[var(--line)] p-4 last:border-0 md:grid-cols-[72px_1fr_auto] md:items-center">
    <img src={item.image_url} alt="" className="size-[72px] border border-[var(--line)] bg-white object-cover" />
    <div className="min-w-0"><p className="file-name truncate text-sm">{item.asset_name}</p><div className="font-data mt-2 flex flex-wrap items-center gap-2 text-xs"><span>旧 {item.comparison?.baseline_level ?? "—"}</span><span className="text-[var(--muted)]">→</span><span>新 {item.comparison?.candidate_level ?? "—"}</span><span className="text-[var(--muted)]">{item.comparison?.baseline_score ?? "—"} → {item.comparison?.candidate_score ?? "—"}</span></div><p className="mt-2 text-xs leading-5 text-[var(--muted)]">{item.comparison?.reasons.join("；") || "等待比较"}</p>{item.human_verdict && <Badge className="mt-2" tone={item.human_verdict === "baseline_better" ? "danger" : "success"}>已复核：{item.human_verdict}</Badge>}</div>
    {!item.human_verdict && item.candidate && <div className="flex flex-wrap gap-2 md:max-w-52 md:justify-end"><Button size="sm" variant="secondary" disabled={reviewing} onClick={() => onReview(item.id, "baseline_better")}>旧模型更好</Button><Button size="sm" variant="secondary" disabled={reviewing} onClick={() => onReview(item.id, "same")}>效果相当</Button><Button size="sm" disabled={reviewing} onClick={() => onReview(item.id, "candidate_better")}>新模型更好</Button></div>}
  </div>
}
