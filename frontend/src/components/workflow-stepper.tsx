import { Check, LockKey, WarningCircle } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export type WorkflowStepState = "completed" | "current" | "pending" | "blocked"

export type WorkflowStep = {
  key: string
  label: string
  note: string
  state: WorkflowStepState
  required?: boolean
}

export function WorkflowStepper({
  steps,
  workflowLabel,
}: {
  steps: readonly WorkflowStep[]
  workflowLabel: string
}) {
  return (
    <section className="border-y border-[var(--line-strong)] bg-white" aria-label={`${workflowLabel}步骤`}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
        <div>
          <p className="text-xs font-semibold text-[var(--muted)]">当前工作流</p>
          <h2 className="mt-1 text-lg font-bold">{workflowLabel}</h2>
        </div>
        <Badge tone="neutral">串行推进 · 人工发布闸门</Badge>
      </div>
      <ol className="grid gap-px bg-[var(--line)] lg:grid-cols-5">
        {steps.map((step, index) => {
          const completed = step.state === "completed"
          const blocked = step.state === "blocked"
          const current = step.state === "current"
          return (
            <li key={step.key} className={cn("min-w-0 bg-white px-4 py-4", current && "bg-[#f7fadf]", blocked && "bg-[#fff9f1]")}>
              <div className="flex items-start gap-3">
                <span className={cn(
                  "font-data flex size-7 shrink-0 items-center justify-center border text-xs font-bold",
                  completed && "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]",
                  current && "border-[#8da91e] bg-primary text-primary-foreground",
                  blocked && "border-[#e5c9a7] bg-[#fff6e9] text-[#7d4308]",
                  !completed && !current && !blocked && "border-[var(--line-strong)] text-[var(--muted)]",
                )}>
                  {completed ? <Check weight="bold" /> : blocked ? <WarningCircle weight="fill" /> : index + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-bold leading-5">{step.label}</p>
                  <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{step.note}</p>
                  {step.required && step.state === "pending" && (
                    <p className="mt-2 flex items-center gap-1 text-[0.68rem] font-semibold text-[#7d4308]"><LockKey size={13} />前置未完成</p>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
