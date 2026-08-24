// 从 workflow-pages.tsx 拆出的跨页共享件。
// 判定依据是依赖闭包：被两个以上页面组件用到才放这里，只被一个页面用到的
// 辅助随该页迁走，避免共享模块变成什么都往里塞的杂物间。
import { CheckCircle, Clock } from "@phosphor-icons/react"
import { type ReactNode } from "react"

export const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`

export function OptimizationFlow({ activeStep }: { activeStep: 1 | 2 | 3 }) {
  const steps = [
    { index: 1, title: "收集案例", description: "人工纠偏与基准偏差进入案例池" },
    { index: 2, title: "组批与生成", description: "先安全试跑，再按需调用优化模型" },
    { index: 3, title: "验证候选", description: "配对回归与人工发布决策" },
  ] as const
  return (
    <ol className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] md:grid-cols-3">
      {steps.map((step) => (
        <li
          key={step.index}
          aria-current={step.index === activeStep ? "step" : undefined}
          className={`grid grid-cols-[36px_minmax(0,1fr)] gap-3 px-4 py-4 ${
            step.index === activeStep ? "bg-[#f7fadf]" : "bg-white"
          }`}
        >
          <span className={`font-data flex size-8 items-center justify-center border text-sm font-bold ${
            step.index < activeStep
              ? "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]"
              : step.index === activeStep
                ? "border-[#8da91e] bg-primary"
                : "border-[var(--line-strong)] text-[var(--muted)]"
          }`}>
            {step.index < activeStep ? <CheckCircle weight="fill" /> : step.index}
          </span>
          <div>
            <p className="text-sm font-semibold">{step.title}</p>
            <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{step.description}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}

export function DataTable({
  loading,
  empty,
  headers,
  rows,
  className = "",
}: {
  loading: boolean
  empty: string
  headers: string[]
  rows: ReactNode[][]
  className?: string
}) {
  return (
    <div className={`overflow-x-auto border-y border-[var(--line-strong)] bg-white ${className}`}>
      {loading ? <div className="h-64 animate-pulse bg-white" /> : rows.length ? (
        <table className="w-full min-w-[920px] border-collapse text-left text-sm">
          <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8]">{headers.map((header) => <th key={header} className="px-4 py-3 text-xs font-semibold text-[var(--muted)]">{header}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex} className="border-b border-[var(--line)] last:border-0">{row.map((cell, cellIndex) => <td key={cellIndex} className="px-4 py-4">{cell}</td>)}</tr>)}</tbody>
        </table>
      ) : <EmptyLine text={empty} />}
    </div>
  )
}

export function EmptyLine({ text }: { text: string }) {
  return <div className="flex min-h-56 flex-col items-center justify-center px-6 text-center"><Clock size={28} weight="light" /><p className="mt-3 text-sm text-[var(--muted)]">{text}</p></div>
}

export function Metric({ label, value }: { label: string; value: string }) {
  return <div><p className="text-[var(--muted)]">{label}</p><p className="font-data mt-1 font-semibold">{value}</p></div>
}

export function executorError(value: string) {
  return ({
    optimizer_usage_missing: "模型未返回可计费 usage",
    optimizer_usage_exceeds_reserved_cost: "实际 usage 超过预算预留",
    automation_lease_lost: "执行租约已失效",
    automation_budget_settlement_conflict: "预算结算冲突",
    model_timeout: "模型调用超时",
    model_network: "模型网络异常",
    model_429: "模型服务限流",
    model_provider5xx: "模型服务暂时不可用",
    invalid_executor_output: "执行器输出不符合安全合同",
    automation_executor_failed: "自动优化执行失败",
    benchmark_usage_missing: "横评模型未返回可计费 usage",
    benchmark_actual_cost_exceeds_round_limit: "横评实际成本达到单轮上限",
    invalid_benchmark_output: "横评输出不符合安全合同",
    benchmark_executor_failed: "横评执行失败",
  } as Record<string, string>)[value] ?? "执行失败，请查看审计记录"
}
