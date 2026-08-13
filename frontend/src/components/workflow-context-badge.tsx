import { ArrowsClockwise, ChartLineUp } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import type { WorkflowKind } from "@/lib/types"

export function WorkflowContextBadge({ kind }: { kind: WorkflowKind }) {
  return (
    <Badge tone={kind === "incremental" ? "active" : "neutral"}>
      {kind === "incremental" ? <ArrowsClockwise /> : <ChartLineUp />}
      {kind === "incremental" ? "增量评测" : "存量回归"}
    </Badge>
  )
}
