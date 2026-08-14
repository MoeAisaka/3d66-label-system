import { Badge } from "@/components/ui/badge"

import type {
  MechanismProfileCatalogItem,
  MechanismProfileDescription,
} from "./types"

const capabilityLabels: Record<string, string> = {
  structured_editor: "结构化编辑",
  candidate_validation: "候选校验",
  candidate_execution: "受控执行",
  workflow_incremental: "增量链路",
  workflow_stock: "存量链路",
  dedicated_editor_slot: "专用编辑器预留",
}

export function ProfileCapabilitySummary({
  profile,
  catalog,
  workflowKind,
}: {
  profile: MechanismProfileDescription | null
  catalog: MechanismProfileCatalogItem[]
  workflowKind: "incremental" | "stock"
}) {
  const reserved = catalog.filter((item) => (
    item.profile_type === "future-3d-controlled-v1"
    || item.profile_type === "future-su-controlled-v1"
  ))
  return (
    <section className="border-y border-[var(--line)] bg-white px-4 py-4 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-semibold">类目机制扩展边界</p>
          <p className="mt-1 text-[var(--muted)]">当前入口：{workflowKind === "incremental" ? "增量链路" : "存量链路"}；共享同一 profile 注册表和候选 API。</p>
        </div>
        <Badge tone={profile?.can_execute ? "success" : "warning"}>
          {profile?.can_execute ? "可受控执行" : "只读安全降级"}
        </Badge>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {(profile?.capabilities ?? []).map((capability) => (
          <Badge key={capability}>{capabilityLabels[capability] ?? capability}</Badge>
        ))}
        {!profile?.capabilities.length && <span className="text-[var(--muted)]">当前 profile 未开放能力。</span>}
      </div>
      <div className="mt-3 border-l-2 border-primary pl-3 text-[var(--muted)]">
        {reserved.map((item) => (
          <span key={item.profile_type} className="mr-4 inline-block">
            {item.profile_type.includes("3d") ? "3D" : "SU"}：专用可编辑视图槽已预留，当前仍为只读且禁止执行
          </span>
        ))}
      </div>
    </section>
  )
}
