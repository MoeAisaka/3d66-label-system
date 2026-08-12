import type { ReactNode } from "react"
import { SecondaryDrawer } from "@/components/workspace-page"

export function MetricsDrawer({ open, onOpenChange, children }: { open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  return <SecondaryDrawer open={open} onOpenChange={onOpenChange} title="回归指标" description="查看混淆矩阵、准确率与失败/待人工数据。">{children}</SecondaryDrawer>
}
