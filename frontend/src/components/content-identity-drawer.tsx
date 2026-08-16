import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import type { ContentIdentityRecord, SourceIdentityVerification } from "@/lib/types"
import type { ReactNode } from "react"

const statusLabels: Record<ContentIdentityRecord["identity_status"], string> = {
  legacy_unverified: "旧链路未签认",
  pending_verification: "身份待签认",
  verified: "已签认",
  conflict: "身份冲突",
}

export function ContentIdentityDrawer({
  record,
  verification,
  open,
  onOpenChange,
}: {
  record: ContentIdentityRecord | null
  verification: SourceIdentityVerification | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const blocked = record?.identity_status === "pending_verification" || record?.identity_status === "conflict"
  const candidateKey = record?.source_res_type && record.source_ll_id
    ? `${record.source_system} + ${record.source_res_type} + ${record.source_ll_id}`
    : `${record?.source_system ?? "source_system"} + res_type + ll_id`

  return (
    <SecondaryDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={record ? `内容身份 · ${record.content_id}` : "内容身份"}
      description="只展示进入生产主线所需的候选键、签认状态和证据引用，不展示完整上游载荷或探查明细。"
    >
      {!record ? <p className="text-sm text-[var(--muted)]">请选择一条内容记录。</p> : <div className="space-y-6">
        <section className="grid gap-4 border-y border-[var(--line)] py-4 sm:grid-cols-2">
          <Detail label="候选复合键"><span className="font-data break-all">{candidateKey}</span></Detail>
          <Detail label="身份状态"><Badge tone={record.identity_status === "verified" ? "success" : blocked ? "warning" : "neutral"}>{statusLabels[record.identity_status]}</Badge></Detail>
          <Detail label="Canonical content_key"><span className="font-data break-all">{record.content_key ?? "身份待签认"}</span></Detail>
          <Detail label="身份哈希"><span className="font-data break-all">{record.identity_hash ?? "未生成"}</span></Detail>
        </section>

        <section>
          <h2 className="text-sm font-bold">来源字段</h2>
          <div className="mt-3 grid gap-3 border-y border-[var(--line)] px-3 py-4 text-xs sm:grid-cols-2">
            <Detail label="source_system"><span>{record.source_system}</span></Detail>
            <Detail label="res_type"><span>{record.source_res_type ?? "未提供"}</span></Detail>
            <Detail label="ll_id"><span>{record.source_ll_id ?? "未提供"}</span></Detail>
            <Detail label="res_id"><span>{record.source_res_id ?? "未提供"}</span></Detail>
          </div>
        </section>

        <section>
          <h2 className="text-sm font-bold">签认证据</h2>
          <div className="mt-3 grid gap-3 border-y border-[var(--line)] px-3 py-4 text-xs sm:grid-cols-2">
            <Detail label="verification id"><span className="font-data">{record.identity_verification_id ?? "未绑定"}</span></Detail>
            <Detail label="签认证据哈希"><span className="font-data break-all">{verification?.probe_hash ?? "未绑定"}</span></Detail>
            <Detail label="数据窗口"><span>{verification?.data_window ?? "未签认"}</span></Detail>
            <Detail label="审核人"><span>{verification?.approved_by ?? "未审核"}</span></Detail>
          </div>
        </section>

        <section className={`border p-4 text-xs leading-6 ${blocked ? "border-[#d7a64d] bg-[#fff9e9] text-[#6f5513]" : "border-[var(--line)] bg-[#f7f9ef]"}`}>
          {blocked
            ? "pending 或 conflict 内容记录不能启动正式标签生产；需完成只读探查、人工签认，并由新事件重新进入链路。"
            : "该身份视图只证明接入身份状态，不代表标签事实已经发布，也不会触发模型或下游投影。"}
        </section>
      </div>}
    </SecondaryDrawer>
  )
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <div className="space-y-1"><p className="text-[var(--muted)]">{label}</p><div>{children}</div></div>
}
