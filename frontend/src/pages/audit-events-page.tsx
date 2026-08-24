import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { api } from "@/lib/api"
import type { AuditEvent } from "@/lib/types"
import { DataTable } from "@/pages/workflow-shared"

export function AuditEventsPage() {
  const events = useQuery({
    queryKey: ["audit-events"],
    queryFn: () => api<{ items: AuditEvent[] }>("/api/audit-events?limit=500"),
  })
  return (
    <>
      <PageHeader index="06.3" title="系统审计" description="自动组批、预算阻断、生产回流和模型横评均写入只追加审计事件；事件禁止原地修改或删除。" />
      <div className="mx-auto shell-content px-5 py-8 md:px-8 lg:px-10">
        <DataTable loading={events.isLoading} empty="还没有 Phase B 审计事件" headers={["时间", "类别", "动作", "主体", "执行者", "事件键"]} rows={(events.data?.items ?? []).map((event) => [
          <span key="time" className="font-data text-xs">{new Date(event.created_at).toLocaleString("zh-CN")}</span>,
          <Badge key="category">{event.category}</Badge>,
          <span key="action">{event.action}</span>,
          <span key="subject" className="font-data text-xs">{event.subject_type} · {event.subject_id}</span>,
          <span key="actor">{event.actor}</span>,
          <span key="key" className="font-data text-xs text-[var(--muted)]">{event.event_key.slice(0, 28)}</span>,
        ])} />
      </div>
    </>
  )
}
