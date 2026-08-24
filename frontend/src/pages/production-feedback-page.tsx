import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { api } from "@/lib/api"
import type { ProductionFeedbackEvent, User } from "@/lib/types"
import { DataTable } from "@/pages/workflow-shared"

export function ProductionFeedbackPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  const events = useQuery({
    queryKey: ["production-feedback-events"],
    queryFn: () => api<{ items: ProductionFeedbackEvent[] }>("/api/production-feedback-events?limit=500"),
  })
  const authStatus = useQuery({
    queryKey: ["production-feedback-config-status"],
    queryFn: () => api<{ configured: boolean; authentication: string; browser_session_accepted: false }>("/api/production-feedback-config-status"),
    enabled: me.data?.is_admin === true,
  })
  return (
    <>
      <PageHeader index="03.3" title="生产案例回流" description="这里只接收生产系统已落地的最终人工纠偏事件，并幂等映射到实验台优化队列；事件不可变，不写生产数据库，也不自动实装提示词。" />
      <div className="mx-auto shell-content px-5 py-8 md:px-8 lg:px-10">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-y border-[var(--line-strong)] bg-white px-5 py-4 text-sm"><span className="font-semibold">机器接收鉴权</span><div className="flex gap-2"><Badge tone={authStatus.data?.configured ? "success" : "danger"}>{authStatus.data?.configured ? "专用 Token 已配置" : "未配置，写入关闭"}</Badge><Badge>浏览器会话不可写入</Badge></div></div>
        <DataTable
          loading={events.isLoading}
          empty="还没有生产反馈事件"
          headers={["事件", "来源系统", "生产案例", "严重度", "提示词", "队列映射", "接收时间", "边界"]}
          rows={(events.data?.items ?? []).map((event) => {
            const payload = event.payload as { production_case_id?: string; severity?: string; prompt_version?: string }
            return [
              <span key="event" className="font-data text-xs">{event.event_id}</span>,
              <span key="source">{event.source_system}</span>,
              <span key="case" className="font-data text-xs">{payload.production_case_id ?? "—"}</span>,
              <Badge key="severity" tone={payload.severity === "P0" || payload.severity === "P1" ? "danger" : "warning"}>{payload.severity ?? "—"}</Badge>,
              <span key="prompt" className="font-data text-xs">{payload.prompt_version ?? "—"}</span>,
              <span key="mapping" className="font-data">{event.optimization_case_id ? `#${event.optimization_case_id}` : "—"}</span>,
              <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(event.received_at).toLocaleString("zh-CN")}</span>,
              <Badge key="boundary">只读回流</Badge>,
            ]
          })}
        />
      </div>
    </>
  )
}
