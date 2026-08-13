import type { ReactNode } from "react"
import { SecondaryDrawer } from "@/components/workspace-page"

export function MetricsDrawer({ open, onOpenChange, children }: { open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  return <SecondaryDrawer open={open} onOpenChange={onOpenChange} title="字段质量证据" description="查看准确率、召回率、字段混淆矩阵与失败样本；证据不会自动启用候选机制。">{children}</SecondaryDrawer>
}
