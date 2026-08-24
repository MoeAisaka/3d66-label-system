// 从 baseline-regression-page.tsx 抽出的页内共享件。
// 归属按依赖闭包判定：被两个以上抽取模块用到才放这里；只被一个模块用到的
// 辅助随该模块迁走，避免这里变成杂物间。
import type { BaselineLevel } from "@/lib/types"

export const levels: BaselineLevel[] = ["L1", "L2", "L3", "L4", "L5"]

export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 text-2xl font-bold">{value}</p>
    </div>
  )
}

export function percent(value: number) {
  return `${Math.round(value * 1000) / 10}%`
}
