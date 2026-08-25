import { ArrowRight } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { OptimizationCase } from "@/lib/types"
import { DataTable, OptimizationFlow } from "@/pages/workflow-shared"

export function OptimizationCasesPage() {
  const cases = useQuery({
    queryKey: ["optimization-cases"],
    queryFn: () => api<{ items: OptimizationCase[] }>("/api/optimization-cases?limit=500"),
  })
  const counts = caseCounts(cases.data?.items ?? [])
  return (
    <>
      <PageHeader
        index="03.1"
        title="纠偏案例池"
        description="人工纠偏和基准偏差先沉淀为可追溯案例，再按同一提示词版本组批。这里负责准备证据，不会自动调用模型或发布提示词。"
      />
      <div className="mx-auto shell-content px-5 py-8 md:px-8 lg:px-10">
        <OptimizationFlow activeStep={1} />
        <section className="mt-6 grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] md:grid-cols-[1fr_1fr_1fr_minmax(260px,1.4fr)]">
          <StatusCount label="待组批" value={counts.pending} />
          <StatusCount label="已形成批次" value={counts.batched + counts.processing} />
          <StatusCount label="已完成优化" value={counts.completed} />
          <div className="bg-[#f7fadf] px-5 py-4">
            <p className="text-xs font-semibold text-[var(--muted)]">当前下一步</p>
            <p className="mt-2 text-sm font-semibold">
              {counts.pending
                ? `有 ${counts.pending} 条案例等待按提示词版本组批`
                : "暂无待组批案例，继续完成纠偏或基准回归"}
            </p>
            {counts.pending > 0 && (
              <Button asChild size="sm" className="mt-3">
                <Link to="/workflow/optimization/automation">
                  去生成安全试跑计划<ArrowRight />
                </Link>
              </Button>
            )}
          </div>
        </section>
        <DataTable
          className="mt-6"
          loading={cases.isLoading}
          empty="还没有完成的纠偏案例"
          headers={["优先级", "来源", "证据", "提示词版本", "当前状态", "进入时间", "下一步"]}
          rows={(cases.data?.items ?? []).map((item) => [
            <Badge key="severity" tone={item.severity === "P0" || item.severity === "P1" ? "danger" : "warning"}>{item.severity}</Badge>,
            <Badge key="source">{item.source_type === "production_feedback" ? "生产回流" : "实验台初审"}</Badge>,
            <span key="evaluation" className="font-data">{item.evaluation_id ? `评测 #${item.evaluation_id}` : `事件 #${item.source_event_id}`}</span>,
            <span key="prompt" className="font-data text-xs">{item.prompt_version}</span>,
            <Badge key="status">{caseStatus(item.status)}</Badge>,
            <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(item.created_at).toLocaleString("zh-CN")}</span>,
            <Button key="next" asChild size="sm" variant="secondary">
              <Link to={
                item.status === "completed"
                  ? "/workflow/optimization/candidates"
                  : "/workflow/optimization/automation"
              }>
                {item.status === "pending"
                  ? "配置本批"
                  : item.status === "completed"
                    ? "查看候选"
                    : "查看运行"}
                <ArrowRight />
              </Link>
            </Button>,
          ])}
        />
      </div>
    </>
  )
}

function StatusCount({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 text-2xl font-bold">{value}</p>
    </div>
  )
}

function caseCounts(items: OptimizationCase[]) {
  return {
    pending: items.filter((item) => item.status === "pending" || item.status === "failed").length,
    batched: items.filter((item) => item.status === "batched").length,
    processing: items.filter((item) => item.status === "processing").length,
    completed: items.filter((item) => item.status === "completed").length,
  }
}

function caseStatus(value: OptimizationCase["status"]) {
  return ({ pending: "待组批", batched: "已组批", processing: "处理中", completed: "已完成", failed: "失败" } as const)[value]
}
