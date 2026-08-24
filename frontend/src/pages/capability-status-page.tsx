import { Prohibit } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { api } from "@/lib/api"
import type { StrategyBundleSummary } from "@/lib/types"
import { EmptyLine } from "@/pages/workflow-shared"

export function CapabilityStatusPage({ kind }: { kind: "benchmark" | "candidates" | "audit" }) {
  const bundles = useQuery({
    queryKey: ["strategy-bundles"],
    queryFn: () => api<{ items: StrategyBundleSummary[] }>("/api/strategy-bundles"),
  })
  const plans = useQuery({
    queryKey: ["agent-plans"],
    queryFn: () => api<{ items: Array<{ id: number; name: string; version: string; status: string; created_at: string }> }>("/api/agent-plans"),
  })
  const copy = {
    benchmark: {
      index: "05.1",
      title: "Sol / Terra / Luna 横评",
      description: "横评执行器尚未接通。本页只展示冻结契约，系统没有调用任何 Sol、Terra 或 Luna。",
    },
    candidates: {
      index: "05.3",
      title: "生产候选",
      description: "实验台尚未接入生产自动回流或自动实装消费者；现阶段只能保存候选证据，不能写生产数据库。",
    },
    audit: {
      index: "06.3",
      title: "系统审计",
      description: "展示受控 AgentPlan 与 StrategyBundle 证据；完整审计事件流尚未接通。",
    },
  }[kind]
  return (
    <>
      <PageHeader index={copy.index} title={copy.title} description={copy.description} />
      <div className="mx-auto grid shell-content gap-6 px-5 py-8 md:px-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:px-10">
        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="border-b border-[var(--line)] px-5 py-4"><h2 className="font-editorial text-xl font-bold">当前可验证证据</h2></div>
          <div className="divide-y divide-[var(--line)]">
            {(bundles.data?.items ?? []).slice(0, 12).map((bundle) => (
              <div key={bundle.id} className="grid gap-2 px-5 py-4 md:grid-cols-[80px_1fr_auto] md:items-center">
                <span className="font-data text-xs">策略 #{bundle.id}</span>
                <div><p className="text-sm font-semibold">{bundle.model_id}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">A {bundle.prompt_a_version} · B {bundle.prompt_b_version ?? "—"} · {bundle.rubric_version} · {bundle.engine_version}</p></div>
                <Badge>{bundle.agent_plan_version}</Badge>
              </div>
            ))}
            {!bundles.isLoading && !bundles.data?.items.length && <EmptyLine text="还没有 StrategyBundle 证据" />}
          </div>
        </section>
        <aside className="border-y border-[var(--line-strong)] bg-[#fafbf8] p-5">
          <div className="flex items-center gap-2"><Prohibit className="text-[#8d2924]" /><h2 className="font-bold">执行状态：未接通</h2></div>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">不会伪造模型横评、自动回流、生产候选实装或完整审计。后续执行前仍需冻结同一批样本、Prompt、Rubric、Engine，并单独通过预算与发布门禁。</p>
          <div className="mt-5 border-t border-[var(--line)] pt-4">
            <p className="text-xs font-bold">受控编排版本</p>
            {(plans.data?.items ?? []).map((plan) => <p key={plan.id} className="font-data mt-2 text-xs">{plan.version} · {plan.status}</p>)}
          </div>
        </aside>
      </div>
    </>
  )
}
