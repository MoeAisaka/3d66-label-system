import { CaretDown, CheckCircle, Circle, CircleNotch, WarningCircle, XCircle } from "@phosphor-icons/react"
import { useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { CanaryRun, CanaryRunState } from "@/lib/types"
import { ErrorNotice, Field, TerminalState, asRecord, formatDateTime, stateLabels, stateTone } from "@/features/canary/canary-form-primitives"
import { ApprovalForm, CandidateHandoffForm, FreezeCapabilityForm, ManifestForm, PreflightForm } from "@/features/canary/canary-gate-forms"

export const gateStates = [
  "draft",
  "preflight_ready",
  "approvals_ready",
  "freeze_ready",
  "candidate_ready",
  "human_review_ready",
] as const

export type GateState = (typeof gateStates)[number]

export type TransitionState = Exclude<GateState, "draft">

export const terminalStates = new Set<CanaryRunState>([
  "human_review_ready",
  "failed",
  "cancelled",
])

export const evidenceKeyByState: Partial<Record<GateState, string>> = {
  preflight_ready: "xlsx_preflight",
  approvals_ready: "approval",
  freeze_ready: "fetch_config",
  candidate_ready: "manifest",
  human_review_ready: "human_review_handoff",
}

export function RunDetail({
  run,
  actionError,
  advancing,
  terminating,
  onRefresh,
  onAdvance,
  onTerminate,
}: {
  run: CanaryRun
  actionError: unknown
  advancing: boolean
  terminating: boolean
  onRefresh: () => void
  onAdvance: (transition: TransitionState, evidence: Record<string, unknown>) => void
  onTerminate: (target: TerminalState, reason: string) => void
}) {
  const isTerminal = terminalStates.has(run.state)

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-data text-xs text-[var(--muted)]">CANARY RUN</p>
          <h2 className="font-editorial mt-2 break-words text-3xl font-bold">{run.display_name || "未命名运行"}</h2>
          <p className="font-data mt-2 break-all text-xs text-[var(--muted)]">{run.run_id}</p>
        </div>
        <Badge tone={stateTone(run.state)}>{stateLabels[run.state]}</Badge>
      </div>

      <dl className="mt-5 grid border-y border-[var(--line-strong)] bg-white sm:grid-cols-2 xl:grid-cols-4">
        <MetaItem label="创建人" value={run.created_by} />
        <MetaItem label="创建时间" value={formatDateTime(run.created_at)} />
        <MetaItem label="最近更新" value={formatDateTime(run.updated_at)} />
        <MetaItem label="运行计划" value={`${run.plan.domain} · ${run.plan.target_size} 项 · seed ${run.plan.seed}`} />
      </dl>

      <section className="mt-7">
        <div className="mb-3">
          <h3 className="font-editorial text-xl font-bold">状态时间线</h3>
          <p className="mt-1 text-xs text-[var(--muted)]">门禁只允许单调推进；失败、取消和人工审核就绪均为不可恢复终止态。</p>
        </div>
        <StateTimeline run={run} />
      </section>

      <InvariantPanel run={run} />

      {Boolean(actionError) && (
        <div className="mt-6">
          <ErrorNotice error={actionError} onRefresh={onRefresh} />
        </div>
      )}

      <section className="mt-7">
        <div className="mb-3">
          <h3 className="font-editorial text-xl font-bold">{isTerminal ? "运行已只读" : "唯一下一门禁"}</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
            {isTerminal
              ? "终止态不能恢复或继续推进；已登记证据仍可在下方核对。"
              : "这里只展示当前状态允许提交的一个前向门禁；原始状态、指纹和运行级安全不变量不可编辑。"}
          </p>
        </div>
        <div className="border-y border-[var(--line-strong)] bg-white">
          {isTerminal ? (
            <div className="flex min-h-32 items-center gap-3 px-5 py-8">
              {run.state === "human_review_ready"
                ? <CheckCircle size={25} className="text-[#2f6f48]" weight="fill" />
                : <XCircle size={25} className="text-[#b7362e]" weight="fill" />}
              <div>
                <p className="font-semibold">{stateLabels[run.state]}</p>
                <p className="mt-1 text-sm text-[var(--muted)]">本页面不会从终止态创建恢复入口。</p>
              </div>
            </div>
          ) : (
            <NextGateForm
              key={`${run.run_id}:${run.state}:${run.snapshot_fingerprint}`}
              run={run}
              pending={advancing}
              onAdvance={onAdvance}
            />
          )}
        </div>
      </section>

      {!isTerminal && (
        <TerminalControls
          key={run.snapshot_fingerprint}
          pending={terminating}
          onTerminate={onTerminate}
        />
      )}

      <EvidenceSummary run={run} />

      <details className="mt-6 border-y border-[var(--line)] bg-[#fafbf8] px-4 py-3">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-semibold">
          <CaretDown />
          快照指纹（并发写入依据）
        </summary>
        <code className="font-data mt-3 block break-all text-[0.68rem] leading-5 text-[var(--muted)]">
          {run.snapshot_fingerprint}
        </code>
      </details>
    </div>
  )
}

export function StateTimeline({ run }: { run: CanaryRun }) {
  const currentGateIndex = gateStates.indexOf(run.state as GateState)

  return (
    <ol className="grid border-y border-[var(--line-strong)] bg-white md:grid-cols-3 xl:grid-cols-6">
      {gateStates.map((state, index) => {
        const reached = run.state === "failed" || run.state === "cancelled"
          ? state === "draft" || Boolean(evidenceKeyByState[state] && run.evidence[evidenceKeyByState[state] as string])
          : currentGateIndex >= index
        const current = run.state === state
        return (
          <li
            key={state}
            className={`min-h-28 border-b border-[var(--line)] px-4 py-4 md:border-r ${
              index >= 3 ? "md:border-b-0" : ""
            } xl:border-b-0 xl:last:border-r-0`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-data text-[0.65rem] text-[var(--muted)]">{String(index + 1).padStart(2, "0")}</span>
              {current
                ? <CircleNotch size={18} weight="bold" />
                : reached
                  ? <CheckCircle size={18} className="text-[#2f6f48]" weight="fill" />
                  : <Circle size={18} className="text-[#9aa097]" />}
            </div>
            <p className={`mt-4 text-xs font-semibold leading-5 ${current ? "text-foreground" : "text-[#5f655d]"}`}>
              {stateLabels[state]}
            </p>
            <p className="mt-1 text-[0.68rem] text-[var(--muted)]">
              {current ? "当前状态" : reached ? "证据已登记" : "尚未到达"}
            </p>
          </li>
        )
      })}
      {(run.state === "failed" || run.state === "cancelled") && (
        <li className="flex items-center gap-3 border-t border-[#e8c1bd] bg-[#fff0ee] px-4 py-3 text-sm text-[#8d2924] md:col-span-3 xl:col-span-6">
          <XCircle size={20} weight="fill" />
          运行终止：{stateLabels[run.state]}，后续门禁保持关闭。
        </li>
      )}
    </ol>
  )
}

export function InvariantPanel({ run }: { run: CanaryRun }) {
  const invariants = [
    ["writes_business_database", "写业务素材/评测库", run.writes_business_database],
    ["downloads_performed", "执行下载", run.downloads_performed],
    ["model_runs_performed", "调用模型", run.model_runs_performed],
    ["forms_gold", "形成 Gold", run.forms_gold],
    ["publishes_release", "发布版本", run.publishes_release],
  ] as const
  const violated = invariants.filter(([, , value]) => value !== false)

  return (
    <section className="mt-7">
      <div className="mb-3">
        <h3 className="font-editorial text-xl font-bold">五项安全不变量</h3>
        <p className="mt-1 text-xs text-[var(--muted)]">API 响应中的每一项都必须明确等于 false，不接受缺失、推断或真值。</p>
      </div>
      {violated.length > 0 && (
        <div role="alert" className="mb-3 flex items-start gap-3 border-y border-[#c55b52] bg-[#fff0ee] px-4 py-4 text-[#7d201a]">
          <WarningCircle size={22} weight="fill" className="mt-0.5 shrink-0" />
          <div>
            <p className="font-bold">危险：服务端返回了非 false 的安全不变量</p>
            <p className="mt-1 text-sm">停止继续登记并核查服务端持久化：{violated.map(([key]) => key).join("、")}</p>
          </div>
        </div>
      )}
      <div className="grid border-y border-[var(--line-strong)] bg-white sm:grid-cols-2 xl:grid-cols-5">
        {invariants.map(([key, label, value]) => (
          <div key={key} className="border-b border-[var(--line)] px-4 py-4 sm:border-r xl:border-b-0 xl:last:border-r-0">
            <p className="text-xs text-[var(--muted)]">{label}</p>
            <div className="mt-3 flex items-center gap-2">
              <Badge tone={value === false ? "success" : "danger"}>{String(value)}</Badge>
              <code className="font-data break-all text-[0.62rem] text-[var(--muted)]">{key}</code>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

export function NextGateForm({
  run,
  pending,
  onAdvance,
}: {
  run: CanaryRun
  pending: boolean
  onAdvance: (transition: TransitionState, evidence: Record<string, unknown>) => void
}) {
  if (run.state === "draft") {
    return <PreflightForm pending={pending} onSubmit={(evidence) => onAdvance("preflight_ready", evidence)} />
  }
  if (run.state === "preflight_ready") {
    return <ApprovalForm run={run} pending={pending} onSubmit={(evidence) => onAdvance("approvals_ready", evidence)} />
  }
  if (run.state === "approvals_ready") {
    return <FreezeCapabilityForm pending={pending} onSubmit={(evidence) => onAdvance("freeze_ready", evidence)} />
  }
  if (run.state === "freeze_ready") {
    return <ManifestForm pending={pending} onSubmit={(evidence) => onAdvance("candidate_ready", evidence)} />
  }
  if (run.state === "candidate_ready") {
    return <CandidateHandoffForm run={run} pending={pending} onSubmit={(evidence) => onAdvance("human_review_ready", evidence)} />
  }
  return null
}

export function TerminalControls({
  pending,
  onTerminate,
}: {
  pending: boolean
  onTerminate: (target: TerminalState, reason: string) => void
}) {
  const [target, setTarget] = useState<TerminalState | null>(null)
  const [reason, setReason] = useState("")
  const [error, setError] = useState("")

  function confirm() {
    const normalized = reason.trim()
    if (!target) return
    if (!normalized) {
      setError("终止操作必须填写原因。")
      return
    }
    setError("")
    onTerminate(target, normalized)
  }

  return (
    <section className="mt-7 border-y border-[var(--line)] bg-[#fafbf8]">
      <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
        <div>
          <h3 className="font-semibold">不可恢复的终止操作</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">选择操作后会展开二次确认区域；提交时仍携带当前快照指纹。</p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={() => { setTarget("failed"); setReason(""); setError("") }}>
            标记失败
          </Button>
          <Button type="button" variant="danger" size="sm" onClick={() => { setTarget("cancelled"); setReason(""); setError("") }}>
            取消运行
          </Button>
        </div>
      </div>
      {target && (
        <div className="border-t border-[#e8c1bd] bg-[#fff0ee] px-5 py-5">
          <p className="font-bold text-[#7d201a]">
            二次确认：将运行{target === "cancelled" ? "取消" : "标记为失败"}后不可恢复。
          </p>
          <Field label="原因">
            <Textarea
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明为什么终止；该原因会进入累积证据。"
              className="mt-3 bg-white"
            />
          </Field>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
            <p role={error ? "alert" : undefined} className="text-sm text-[#8d2924]">{error}</p>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" onClick={() => { setTarget(null); setReason(""); setError("") }} disabled={pending}>
                返回
              </Button>
              <Button type="button" variant="danger" onClick={confirm} disabled={pending}>
                {pending ? <CircleNotch className="animate-spin" /> : <XCircle />}
                确认终止
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

export function EvidenceSummary({ run }: { run: CanaryRun }) {
  const items = useMemo(() => summarizeEvidence(run.evidence), [run.evidence])

  return (
    <section className="mt-7">
      <div className="mb-3">
        <h3 className="font-editorial text-xl font-bold">累积证据摘要</h3>
        <p className="mt-1 text-xs text-[var(--muted)]">只以纯文本展示已登记摘要；任何证据 URL 都不会渲染为可点击链接。</p>
      </div>
      <div className="border-y border-[var(--line-strong)] bg-white">
        {items.length ? items.map((item) => (
          <div key={item.key} className="grid gap-2 border-b border-[var(--line)] px-4 py-4 last:border-0 md:grid-cols-[190px_1fr]">
            <div>
              <p className="text-sm font-semibold">{item.label}</p>
              <code className="font-data text-[0.65rem] text-[var(--muted)]">{item.key}</code>
            </div>
            <p className="break-words text-sm leading-6 text-[#535951]">{item.summary}</p>
          </div>
        )) : (
          <div className="px-5 py-10 text-center text-sm text-[var(--muted)]">草稿阶段尚未登记任何门禁证据。</div>
        )}
      </div>
    </section>
  )
}

export function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-[var(--line)] px-4 py-4 sm:border-r xl:border-b-0 xl:last:border-r-0">
      <dt className="text-xs text-[var(--muted)]">{label}</dt>
      <dd className="mt-2 break-words text-sm font-semibold leading-6">{value}</dd>
    </div>
  )
}

export function summarizeEvidence(evidence: Record<string, unknown>) {
  const summaries: Array<{ key: string; label: string; summary: string }> = []
  const preflight = asRecord(evidence.xlsx_preflight)
  if (preflight) {
    summaries.push({
      key: "xlsx_preflight",
      label: "XLSX 预检登记",
      summary: `schema ${textValue(preflight.schema_version)}；batch_key ${textValue(preflight.batch_key)}。`,
    })
  }
  const approval = asRecord(evidence.approval)
  if (approval) {
    const mappings = Array.isArray(approval.applied_mappings) ? approval.applied_mappings.length : 0
    summaries.push({
      key: "approval",
      label: "人工审批",
      summary: `审批人 ${textValue(approval.approved_by)}；批次 ${textValue(approval.batch_key)}；人工确认映射 ${mappings} 条。`,
    })
  }
  const fetchConfig = asRecord(evidence.fetch_config)
  if (fetchConfig) {
    const hosts = Array.isArray(fetchConfig.allowed_hosts) ? fetchConfig.allowed_hosts.map(textValue).join("、") : "—"
    summaries.push({
      key: "fetch_config",
      label: "冻结能力登记",
      summary: `精确主机 ${hosts || "—"}；固定 IP HTTPS 证明 ${textValue(fetchConfig.pinned_https_attested)}。未执行下载。`,
    })
  }
  const manifest = asRecord(evidence.manifest)
  if (manifest) {
    summaries.push({
      key: "manifest",
      label: "冻结清单",
      summary: `版本 ${textValue(manifest.manifest_version)}；来源 ${textValue(manifest.expected_source_count)}；冻结 ${textValue(manifest.frozen_source_count)}；完整 ${textValue(manifest.complete)}。`,
    })
  }
  const preview = asRecord(evidence.candidate_preview)
  if (preview) {
    summaries.push({
      key: "candidate_preview",
      label: "候选预览",
      summary: `版本 ${textValue(preview.schema_version)}；候选 ${textValue(preview.selected_count)}；完整 ${textValue(preview.complete_for_requested_preview)}；Gold/下载/模型运行均为 false。`,
    })
  }
  const handoff = asRecord(evidence.human_review_handoff)
  if (handoff) {
    summaries.push({
      key: "human_review_handoff",
      label: "人工审核交接",
      summary: `逐项必审 ${textValue(handoff.all_items_require_review)}；不授予真值或 Gold ${textValue(handoff.no_truth_or_gold_granted)}；共 ${textValue(handoff.item_count)} 项。`,
    })
  }
  const failure = asRecord(evidence.failure)
  if (failure) {
    summaries.push({ key: "failure", label: "失败原因", summary: textValue(failure.reason) })
  }
  const cancellation = asRecord(evidence.cancellation)
  if (cancellation) {
    summaries.push({ key: "cancellation", label: "取消原因", summary: textValue(cancellation.reason) })
  }
  return summaries
}

export function textValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return "—"
}
