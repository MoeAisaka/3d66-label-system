import { ArrowClockwise, ArrowRight, ArrowsClockwise, Clock, WarningCircle } from "@phosphor-icons/react"
import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { Job, JobControl } from "@/lib/types"

type QueueStatus = Record<string, unknown> & { queues?: Record<string, unknown> }

export function OperationsCenterPage() {
  const [detailOpen, setDetailOpen] = useState(false)
  const control = useQuery({ queryKey: ["job-control", "operations"], queryFn: () => api<JobControl>("/api/jobs/control"), refetchInterval: 2500 })
  const jobs = useQuery({ queryKey: ["jobs", "operations"], queryFn: () => api<{ items: Job[] }>("/api/jobs?limit=200"), refetchInterval: 2500 })
  const queues = useQuery({ queryKey: ["queues", "operations"], queryFn: () => api<QueueStatus>("/api/queues/status"), refetchInterval: 2500 })
  const failures = (jobs.data?.items ?? []).filter((job) => job.status === "failed")
  return (
    <>
      <PageHeader index="03" title="运行中心" description="集中观察队列、并发、失败、重试和恢复状态。一级页面只保留影响当前操作的事实，完整任务详情放入抽屉。" actions={<Button variant="secondary" onClick={() => Promise.all([control.refetch(), jobs.refetch(), queues.refetch()])}><ArrowClockwise />刷新状态</Button>} />
      <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="排队" value={String(control.data?.queued_count ?? "—")} />
          <Metric label="运行" value={String(control.data?.processing_count ?? "—")} />
          <Metric label="暂停" value={String(control.data?.paused_count ?? "—")} />
          <Metric label="失败待处理" value={String(failures.length)} tone={failures.length ? "warning" : "success"} />
          <Metric label="调度器" value={queues.isError ? "需检查" : "在线"} tone={queues.isError ? "warning" : "success"} />
        </section>
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="bg-white px-5 py-6 md:px-6"><div className="flex items-center gap-2"><ArrowsClockwise size={19} /><h2 className="text-lg font-bold">当前运行主线</h2></div><p className="mt-2 text-sm leading-6 text-[var(--muted)]">增量与存量任务共用调度内核，但由工作流上下文隔离；暂停、重试、熔断和恢复不改变 Canonical 事实。</p><div className="mt-5 flex flex-wrap gap-2"><Button asChild variant="secondary"><Link to="/workflow/incremental">增量运行<ArrowRight /></Link></Button><Button asChild variant="secondary"><Link to="/workflow/stock">存量运行<ArrowRight /></Link></Button><Button variant="secondary" onClick={() => setDetailOpen(true)}>查看失败与恢复</Button></div></div>
          <aside className="bg-[#f7fadf] px-5 py-6 md:px-6"><div className="flex items-center gap-2"><Clock size={18} /><p className="text-xs font-bold">调度事实</p></div><p className="mt-3 text-sm leading-6 text-[var(--muted)]">当前队列状态由 `/api/queues/status` 和任务控制面联合计算；不在首页平铺原始调度 JSON。</p></aside>
        </section>
        <section className="border-y border-[var(--line)] bg-white px-5 py-5"><div className="flex items-center gap-2"><WarningCircle size={18} /><h2 className="text-sm font-bold">失败处理</h2></div><p className="mt-2 text-xs leading-6 text-[var(--muted)]">失败任务只在达到可重试条件时进入重试；超过上限或属于业务不确定时转入人工纠偏/恢复队列。</p></section>
      </div>
      <SecondaryDrawer open={detailOpen} onOpenChange={setDetailOpen} title="失败与恢复" description="仅展示需要操作的任务摘要；完整执行证据仍在对应运行详情中。"><div className="space-y-3">{failures.length ? failures.map((job) => <div key={job.id} className="border-y border-[var(--line)] px-3 py-3"><div className="flex items-center justify-between gap-3"><span className="font-data text-xs">#{job.id}</span><Badge tone="danger">失败</Badge></div><p className="mt-2 text-xs leading-5 text-[#8d2924]">{job.error_message || "执行失败，请查看对应运行详情。"}</p></div>) : <p className="text-sm text-[var(--muted)]">暂无失败任务。</p>}</div></SecondaryDrawer>
    </>
  )
}

function Metric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "warning" | "success" }) { return <div className="bg-white px-5 py-4"><p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-2 font-data text-2xl font-bold">{value}</p><Badge className="mt-2" tone={tone}>{tone === "success" ? "正常" : tone === "warning" ? "需要处理" : "实时"}</Badge></div> }
