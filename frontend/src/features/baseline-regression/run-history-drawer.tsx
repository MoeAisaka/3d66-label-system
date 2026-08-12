import type { ReactNode } from "react"
import { SecondaryDrawer } from "@/components/workspace-page"

export function RunHistoryDrawer({ open, onOpenChange, children }: { open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  return <SecondaryDrawer open={open} onOpenChange={onOpenChange} title="运行历史" description="查看本基准集的历史回归轮次与冻结版本。">{children}</SecondaryDrawer>
}
