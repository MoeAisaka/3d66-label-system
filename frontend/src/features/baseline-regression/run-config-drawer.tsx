import type { ReactNode } from "react"
import { SecondaryDrawer } from "@/components/workspace-page"

export function RunConfigDrawer({ open, onOpenChange, children, footer }: { open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode; footer?: ReactNode }) {
  return <SecondaryDrawer open={open} onOpenChange={onOpenChange} size="wide" title="运行配置" description="选择提示词、等级规则与执行方式；启动后会冻结本轮规则与版本。" footer={footer}>{children}</SecondaryDrawer>
}
