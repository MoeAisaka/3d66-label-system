import { ArrowClockwise, CheckCircle, CircleNotch, WarningCircle } from "@phosphor-icons/react"
import { type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/api"
import type { CanaryRunState } from "@/lib/types"

export const stateLabels: Record<CanaryRunState, string> = {
  draft: "草稿",
  preflight_ready: "预检证据就绪",
  approvals_ready: "人工审批就绪",
  freeze_ready: "冻结能力登记就绪",
  candidate_ready: "候选证据就绪",
  human_review_ready: "待逐项人工审核",
  failed: "已标记失败",
  cancelled: "已取消",
}

export type TerminalState = "failed" | "cancelled"

export function stateTone(state: CanaryRunState) {
  if (state === "human_review_ready") return "success" as const
  if (state === "failed" || state === "cancelled") return "danger" as const
  if (state === "draft") return "neutral" as const
  return "active" as const
}

export function GateFormShell({
  step,
  title,
  warning,
  children,
}: {
  step: string
  title: string
  warning: string
  children: ReactNode
}) {
  return (
    <div>
      <div className="border-b border-[var(--line)] bg-[#fafbf8] px-5 py-4">
        <p className="font-data text-[0.68rem] text-[var(--muted)]">{step}</p>
        <h4 className="font-editorial mt-2 text-xl font-bold">{title}</h4>
        <p className="mt-2 flex items-start gap-2 text-sm leading-6 text-[#6f4a24]">
          <WarningCircle className="mt-1 shrink-0" />
          {warning}
        </p>
      </div>
      <div className="p-5">{children}</div>
    </div>
  )
}

export function SubmitRow({
  pending,
  error,
  label,
}: {
  pending: boolean
  error: string
  label: string
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 md:col-span-2">
      <p role={error ? "alert" : undefined} className="text-sm text-[#8d2924]">{error}</p>
      <Button type="submit" disabled={pending}>
        {pending ? <CircleNotch className="animate-spin" /> : <CheckCircle />}
        {label}
      </Button>
    </div>
  )
}

export function ExplicitCheckbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 border border-[var(--line)] bg-[#fafbf8] px-4 py-3 text-sm leading-6">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 size-4 shrink-0 accent-[#95b314] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-black"
      />
      <span>{label}</span>
    </label>
  )
}

export function ErrorNotice({
  error,
  onRefresh,
  compact = false,
}: {
  error: unknown
  onRefresh: () => void
  compact?: boolean
}) {
  const apiError = error instanceof ApiError ? error : null
  const detail = apiError?.detail
  const stale = apiError?.status === 409
  const unauthorized = apiError?.status === 401

  return (
    <div role="alert" className={`border-y border-[#e8c1bd] bg-[#fff0ee] text-[#7d201a] ${compact ? "px-4 py-4" : "px-5 py-5"}`}>
      <div className="flex items-start gap-3">
        <WarningCircle size={21} weight="fill" className="mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="font-bold">
            {stale
              ? "快照已变化，请刷新后重试"
              : unauthorized
                ? "登录状态已失效"
                : "请求未完成"}
          </p>
          <p className="mt-1 break-words text-sm leading-6">
            {unauthorized ? "请刷新页面并重新登录后继续。" : detail?.message || readableError(error)}
          </p>
          {detail && (
            <dl className="font-data mt-3 grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2">
              {detail.code && <ErrorField label="code" value={detail.code} />}
              {detail.current_state && <ErrorField label="current_state" value={detail.current_state} />}
              {detail.attempted_transition && <ErrorField label="attempted_transition" value={detail.attempted_transition} />}
              {typeof detail.retryable === "boolean" && <ErrorField label="retryable" value={String(detail.retryable)} />}
            </dl>
          )}
          <Button type="button" variant="secondary" size="sm" className="mt-4" onClick={onRefresh}>
            <ArrowClockwise />
            {stale ? "刷新最新快照" : "重试刷新"}
          </Button>
        </div>
      </div>
    </div>
  )
}

export function ErrorField({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[auto_1fr] gap-2">
      <dt className="text-[#8d5e58]">{label}</dt>
      <dd className="break-all font-semibold">{value}</dd>
    </div>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      {children}
      {hint && <span className="mt-2 block text-xs leading-5 text-[var(--muted)]">{hint}</span>}
    </label>
  )
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

export function readableError(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误，请刷新后重试。"
}

export function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN")
}
