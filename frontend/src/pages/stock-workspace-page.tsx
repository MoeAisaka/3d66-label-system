import { ArrowRight, ChartLineUp, Database, FileText, Play, ShieldCheck } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { PageHeader } from "@/components/app-shell"
import { WorkflowContextBadge } from "@/components/workflow-context-badge"
import { WorkflowStepper, type WorkflowStep } from "@/components/workflow-stepper"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { BaselineRegressionRun, BaselineSetSummary } from "@/lib/types"

export function StockWorkspacePage() {
  const sets = useQuery({ queryKey: ["baseline-sets", "stock-workspace"], queryFn: () => api<{ items: BaselineSetSummary[] }>("/api/baseline-sets") })
  const runs = useQuery({ queryKey: ["baseline-regressions", "stock-workspace"], queryFn: () => api<{ items: BaselineRegressionRun[] }>("/api/baseline-regressions") })
  const latest = runs.data?.items[0]
  const step = latest ? (latest.status === "completed" ? 6 : 4) : 2
  const steps: WorkflowStep[] = [
    { key: "category", label: "选择类目", note: "存量类目与基准集隔离", state: step > 1 ? "completed" : "current", required: true },
    { key: "source", label: "选择存量素材/黄金集", note: `${sets.data?.items.length ?? 0} 个基准集`, state: step > 2 ? "completed" : "current", required: true },
    { key: "package", label: "编辑素材包", note: "锁定版本后不可静默改写", state: step > 3 ? "completed" : "current", required: true },
    { key: "regress", label: "启动回归", note: "按冻结机制和真值运行", state: step >= 4 ? "current" : "pending", required: true },
    { key: "review", label: "人工纠偏与 AI 迭代", note: "候选回归结果等待人工决策", state: step >= 5 ? "current" : "pending" },
    { key: "rerun", label: "存量重跑与发布", note: "申请后再进行正式事实发布", state: step >= 6 ? "completed" : "pending" },
  ]
  return (
    <>
      <PageHeader index="02" title="存量回归" description="已定性素材、黄金集和存量重跑沿独立页面主线推进；与增量链路复用纠偏、候选回归、发布和对账内核。" actions={<><WorkflowContextBadge kind="stock" /><Button asChild variant="secondary"><Link to="/workflow/optimization/baseline-regression"><ChartLineUp />打开基准回归</Link></Button></>} />
      <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
        <WorkflowStepper workflowLabel="存量素材/黄金集 → 回归 → 纠偏 → 候选回归 → 重跑发布" steps={steps.slice(0, 5)} />
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
          <div className="bg-white px-5 py-6 md:px-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-semibold text-[var(--muted)]">主操作</p><h2 className="mt-2 font-editorial text-2xl font-bold">选择基准集并启动存量回归</h2></div><Badge tone={sets.data?.items.length ? "success" : "warning"}>{sets.data?.items.length ? `${sets.data.items.length} 个基准集` : "等待基准集"}</Badge></div><div className="mt-6 grid gap-4 sm:grid-cols-3"><Button asChild variant="secondary"><Link to="/legacy/sample-sets"><ShieldCheck />管理黄金数据集</Link></Button><Button asChild variant="secondary"><Link to="/workflow/optimization/category-evaluation-v3-config?workflow_kind=stock"><FileText />选择评测机制</Link></Button><Button asChild><Link to="/workflow/optimization/baseline-regression"><Play weight="fill" />进入回归主线</Link></Button></div></div>
          <aside className="bg-[#f7fadf] px-5 py-6 md:px-6"><p className="text-xs font-bold">最近一次存量回归</p>{latest ? <><p className="mt-3 text-lg font-semibold">基准集 #{latest.baseline_set_id}</p><p className="mt-2 text-xs leading-5 text-[var(--muted)]">{latest.status} · {latest.completed}/{latest.total} 完成 · workflow_kind=stock</p><Button asChild size="sm" variant="secondary" className="mt-5"><Link to={`/workflow/optimization/baseline-regression?run=${latest.id}`}>查看回归详情<ArrowRight /></Link></Button></> : <p className="mt-3 text-sm leading-6 text-[var(--muted)]">还没有存量回归运行。先选择或创建黄金集。</p>}</aside>
        </section>
        <section className="border-y border-[var(--line)] bg-white px-5 py-5"><div className="flex items-center gap-2"><Database size={18} /><h2 className="text-sm font-bold">存量边界</h2></div><p className="mt-2 text-xs leading-6 text-[var(--muted)]">大规模 Worker、批量差异工作台和自动批量发布仍需下一阶段冻结；当前页面只承载可追溯的控制面和人工确认入口。</p></section>
      </div>
    </>
  )
}
