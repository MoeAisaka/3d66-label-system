import { ArrowClockwise, Check, X } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { automationApi } from "@/lib/api"

export function AutomationOverviewPage() {
  const queryClient = useQueryClient()
  const overview = useQuery({ queryKey: ["automation-overview"], queryFn: automationApi.overview, refetchInterval: 5000 })
  const candidates = useQuery({ queryKey: ["automation-candidates"], queryFn: automationApi.candidates, refetchInterval: 5000 })
  const decision = useMutation({
    mutationFn: ({ id, value }: { id: number; value: "approved" | "rejected" }) => automationApi.decideCandidate(id, value, value === "approved" ? "人工采用" : "人工拒绝"),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["automation-overview"] }); void queryClient.invalidateQueries({ queryKey: ["automation-candidates"] }) },
  })
  const data = overview.data
  return (
    <>
      <PageHeader index="07" title="自动组批总览" description="统一查看类目泳道、历史审计、候选二审与费用门禁。自动发布已关闭。" actions={<Button variant="secondary" onClick={() => overview.refetch()}><ArrowClockwise />刷新</Button>} />
      <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-7 md:px-8 lg:px-10">
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="自动化总开关" value={data?.policy.enabled ? "开启" : "关闭"} />
          <Metric label="执行模式" value={data?.policy.dry_run ? "演练" : "真实"} />
          <Metric label="共享日预算" value={data ? `${(data.policy.daily_budget_micros / 1_000_000).toFixed(2)} 元` : "—"} />
          <Metric label="运行批次" value={String(data?.active_batches ?? "—")} />
          <Metric label="历史审计" value={String(data?.historical_audit ?? "—")} />
        </section>
        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="flex items-center justify-between px-5 py-5"><div><p className="text-xs text-[var(--muted)]">类目与链路隔离</p><h2 className="mt-1 text-lg font-bold">活动泳道</h2></div><Badge tone="success">跨泳道不混批</Badge></div>
          <div className="overflow-x-auto border-t border-[var(--line)]"><table className="w-full min-w-[900px] text-left text-xs"><thead className="bg-[#f7f8f3] text-[var(--muted)]"><tr><th className="px-4 py-3">类目</th><th className="px-4 py-3">链路</th><th className="px-4 py-3">代次</th><th className="px-4 py-3">机制指纹</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">三角色集</th></tr></thead><tbody>{(data?.lanes ?? []).map((lane) => <tr key={lane.id} className="border-t border-[var(--line)]"><td className="px-4 py-4 font-bold">{lane.category_key}</td><td className="px-4 py-4">{lane.pipeline_kind === "incremental" ? "增量" : "存量"}</td><td className="px-4 py-4 font-data">g{lane.generation}</td><td className="px-4 py-4 font-data">{lane.mechanism_fingerprint_prefix}</td><td className="px-4 py-4"><Badge>{lane.status}</Badge></td><td className="px-4 py-4">{lane.golden_sets.target_error ? "目标错例 · " : ""}{lane.golden_sets.stable_control ? "稳定对照 · " : ""}{lane.golden_sets.blind_holdout ? "盲测保留" : "未就绪"}</td></tr>)}</tbody></table></div>
        </section>
        <section className="border-y border-[var(--line-strong)] bg-white"><div className="px-5 py-5"><p className="text-xs text-[var(--muted)]">候选二审</p><h2 className="mt-1 text-lg font-bold">人工采用或拒绝</h2><p className="mt-2 text-xs text-[var(--muted)]">候选只改变审批状态，不发布标签事实、不触发存量重跑。</p></div><div className="border-t border-[var(--line)]">{(candidates.data?.items ?? []).map((candidate) => <div key={candidate.id} className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--line)] px-5 py-4"><div><p className="font-data font-bold">候选 #{candidate.id} · {candidate.category_key}</p><p className="mt-1 text-xs text-[var(--muted)]">{candidate.candidate_count} 个候选 · 预计 {(candidate.estimated_cost_micros / 1_000_000).toFixed(4)} 元</p></div><div className="flex gap-2"><Button size="sm" onClick={() => decision.mutate({ id: candidate.id, value: "approved" })}><Check />采用</Button><Button size="sm" variant="secondary" onClick={() => decision.mutate({ id: candidate.id, value: "rejected" })}><X />拒绝</Button></div></div>)}{!(candidates.data?.items.length) && <p className="px-5 py-8 text-sm text-[var(--muted)]">暂无待二审候选。</p>}</div></section>
      </div>
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-white px-5 py-4"><p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-2 font-data text-xl font-bold">{value}</p></div> }
