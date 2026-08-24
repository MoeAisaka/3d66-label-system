import { ArrowRight, GearSix, Play } from "@phosphor-icons/react"
import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, jsonBody } from "@/lib/api"
import type { AutomationPolicy, AutomationRun, OptimizationCase, User } from "@/lib/types"
import { DataTable, Metric, OptimizationFlow, executorError } from "@/pages/workflow-shared"

export function AutomationControlPage() {
  const queryClient = useQueryClient()
  const policy = useQuery({
    queryKey: ["automation-policy"],
    queryFn: () => api<AutomationPolicy>("/api/automation-policy"),
  })
  const runs = useQuery({
    queryKey: ["automation-runs"],
    queryFn: () => api<{ items: AutomationRun[] }>("/api/automation-runs?limit=100"),
  })
  const cases = useQuery({
    queryKey: ["optimization-cases"],
    queryFn: () => api<{ items: OptimizationCase[] }>("/api/optimization-cases?limit=500"),
  })
  const [draft, setDraft] = useState<AutomationPolicy | null>(null)
  const [riskConfirmed, setRiskConfirmed] = useState(false)
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  useEffect(() => {
    if (policy.data) setDraft(policy.data)
  }, [policy.data])
  const batchPreview = useMemo(
    () => draft ? nextAutomationBatch(cases.data?.items ?? [], draft) : null,
    [cases.data?.items, draft],
  )
  const save = useMutation({
    mutationFn: () => draft
      ? api<AutomationPolicy>("/api/automation-policy", {
      method: "PUT",
      ...jsonBody(automationPolicyBody(draft)),
    })
      : Promise.reject(new Error("自动优化策略尚未加载")),
    onSuccess: async (saved) => {
      setDraft(saved)
      await queryClient.invalidateQueries({ queryKey: ["automation-policy"] })
      toast.success("自动优化策略已保存")
      setRiskConfirmed(false)
    },
    onError: (error) => toast.error(error.message),
  })
  const consume = useMutation({
    mutationFn: () => api<{ status: string }>("/api/automation-runs/consume", { method: "POST" }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["automation-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["optimization-cases"] }),
        queryClient.invalidateQueries({ queryKey: ["automation-policy"] }),
      ])
      toast.success(`队列检查完成：${automationStatus(result.status)}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const safeTrial = useMutation({
    mutationFn: async () => {
      if (!draft || !batchPreview?.caseCount) {
        throw new Error("当前没有可组批案例")
      }
      const safeDraft: AutomationPolicy = {
        ...draft,
        enabled: true,
        dry_run: true,
        case_threshold: batchPreview.caseCount,
      }
      const saved = await api<AutomationPolicy>("/api/automation-policy", {
        method: "PUT",
        ...jsonBody(automationPolicyBody(safeDraft)),
      })
      const result = await api<{ status: string; run_id?: number }>(
        "/api/automation-runs/consume",
        { method: "POST" },
      )
      return { saved, result }
    },
    onSuccess: async ({ saved, result }) => {
      setDraft(saved)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["automation-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["optimization-cases"] }),
        queryClient.invalidateQueries({ queryKey: ["automation-policy"] }),
      ])
      toast.success(
        result.status === "planned"
          ? "安全试跑计划已生成：未调用模型、未产生费用"
          : `安全试跑检查完成：${automationStatus(result.status)}`,
      )
    },
    onError: (error) => toast.error(error.message),
  })
  return (
    <>
      <PageHeader
        index="03.2"
        title="案例组批与优化"
        description="人工纠偏完成后，系统会按类目自动组批、分析、生成候选并执行回归；全部证据完成后才进入人工二审，任何模式都不会自动发布。"
      />
      <div className="mx-auto shell-content px-5 py-8 md:px-8 lg:px-10">
        <OptimizationFlow activeStep={2} />
        <section className="mt-6 grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
          <div className="bg-[#f7fadf] px-5 py-5">
            <p className="text-xs font-semibold text-[var(--muted)]">当前下一步</p>
            <h2 className="font-editorial mt-2 text-2xl font-bold">
              {automationNextTitle(draft, batchPreview)}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              {automationNextDescription(draft, batchPreview)}
            </p>
            {me.data?.is_admin && batchPreview?.caseCount ? (
              <Button
                className="mt-4"
                onClick={() => safeTrial.mutate()}
                disabled={safeTrial.isPending}
              >
                <Play weight="fill" />
                {safeTrial.isPending
                  ? "正在生成试跑计划"
                  : `生成安全试跑计划（${batchPreview.caseCount} 条）`}
              </Button>
            ) : null}
          </div>
          <div className="bg-white px-5 py-5">
            <p className="text-xs font-semibold text-[var(--muted)]">本次预计处理</p>
            <p className="font-data mt-2 text-3xl font-bold">{batchPreview?.caseCount ?? 0}</p>
            <p className="mt-2 truncate text-xs text-[var(--muted)]" title={batchPreview?.promptVersion ?? ""}>
              提示词版本：{batchPreview?.promptVersion ?? "暂无"}
            </p>
            <p className="mt-3 text-xs leading-5 text-[var(--muted)]">
              安全试跑会自动启用消费者、保持 dry-run，并把门槛设置为本批数量；只冻结计划，不调用模型。
            </p>
          </div>
        </section>
        {draft?.runtime && <section className="mt-6 border-y border-[var(--line-strong)] bg-white">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
            <div>
              <p className="text-xs font-semibold text-[var(--muted)]">自动化运行状态</p>
              <p className="mt-1 text-sm font-semibold">{automationRuntimeTitle(draft.runtime.status)}</p>
            </div>
            <Badge tone={automationRuntimeTone(draft.runtime.status)}>
              {draft.runtime.worker.active_worker_count} 个 Worker 存活
            </Badge>
          </div>
          <div className="grid gap-px bg-[var(--line)] md:grid-cols-3">
            <RuntimeMetric
              label="最近队列检查"
              value={runtimeLastCheck(draft.runtime.worker.workers)}
              detail={draft.runtime.worker.workers[0]?.last_status ?? "未记录"}
            />
            <RuntimeMetric
              label="可组批案例"
              value={`${draft.runtime.queue.available_for_prompt}/${draft.runtime.queue.required_for_prompt}`}
              detail={draft.runtime.queue.next_prompt_version ?? "当前无待处理案例"}
            />
            <RuntimeMetric
              label="优化模型"
              value={draft.runtime.optimizer.configured ? "已就绪" : "未就绪"}
              detail={draft.runtime.optimizer.model_id ?? "未解析到可执行配置"}
            />
          </div>
          {draft.runtime.blockers.length > 0 && (
            <div className="divide-y divide-[var(--line)] border-t border-[var(--line)]">
              {draft.runtime.blockers.map((blocker) => (
                <div key={blocker.code} className="flex items-start justify-between gap-4 px-5 py-3 text-xs leading-5">
                  <span>{blocker.message}</span>
                  <Badge tone={automationBlockerTone(blocker.severity)}>{automationBlockerLabel(blocker.severity)}</Badge>
                </div>
              ))}
            </div>
          )}
        </section>}
      </div>
      <div className="mx-auto grid shell-content gap-6 px-5 pb-8 md:px-8 lg:grid-cols-[420px_minmax(0,1fr)] lg:px-10">
        <section className="border-y border-[var(--line-strong)] bg-white p-5">
          <div className="flex items-center justify-between"><h2 className="font-editorial text-xl font-bold">运行方式</h2><Badge tone={draft?.enabled ? "active" : "neutral"}>{draft?.enabled ? "已启用" : "已关闭"}</Badge></div>
          {!draft ? <div className="mt-5 h-64 animate-pulse bg-[#fafbf8]" /> : !me.data?.is_admin ? (
            <p className="mt-5 border-y border-[var(--line)] py-4 text-sm text-[var(--muted)]">当前账号可查看运行与预算，但无权修改执行策略。</p>
          ) : (
            <div className="mt-5 space-y-4">
              <ToggleLine
                label="允许队列自动组批"
                description="开启后，系统才会检查案例数量并形成优化批次。"
                checked={draft.enabled}
                onChange={(enabled) => setDraft({ ...draft, enabled })}
              />
              <ToggleLine
                label="只生成试运行计划"
                description="开启时不调用模型、不计费，只验证会选中哪些案例。首次使用建议保持开启。"
                checked={draft.dry_run}
                onChange={(dry_run) => setDraft({ ...draft, dry_run })}
              />
              <NumberField
                label="同版本组批数量"
                description="同一提示词版本累计到多少条案例时形成一批。"
                value={draft.case_threshold}
                min={1}
                onChange={(case_threshold) => setDraft({ ...draft, case_threshold })}
              />
              <SeverityField
                values={draft.immediate_severities}
                onChange={(immediate_severities) => setDraft({
                  ...draft,
                  immediate_severities,
                })}
              />
              <NumberField
                label="每批最多生成候选"
                description="真实执行时最多创建几个候选提示词草稿，范围为 1–5。"
                value={draft.max_candidates}
                min={1}
                max={5}
                onChange={(max_candidates) => setDraft({ ...draft, max_candidates })}
              />
              <NumberField
                label="每日费用上限"
                description="系统最小计费单位；0 表示只能试运行，真实执行必须大于 0。"
                value={draft.daily_budget_micros}
                min={0}
                onChange={(daily_budget_micros) => setDraft({ ...draft, daily_budget_micros })}
              />
              <details className="border-y border-[var(--line)] bg-[#fafbf8]">
                <summary className="flex cursor-pointer items-center gap-2 px-3 py-3 text-sm font-semibold">
                  <GearSix />高级恢复设置（通常无需修改）
                </summary>
                <div className="space-y-4 border-t border-[var(--line)] px-3 py-4">
                  <NumberField label="两批冷却时间" description="避免短时间连续触发，单位为秒。" value={draft.cooldown_seconds} min={0} onChange={(cooldown_seconds) => setDraft({ ...draft, cooldown_seconds })} />
                  <NumberField label="单次占用保护时间" description="执行异常退出后，超过该时间可由系统回收批次。" value={draft.lease_seconds} min={30} max={3600} onChange={(lease_seconds) => setDraft({ ...draft, lease_seconds })} />
                  <NumberField label="失败最多尝试次数" description="只对可重试的网络或限流故障生效。" value={draft.max_attempts} min={1} max={10} onChange={(max_attempts) => setDraft({ ...draft, max_attempts })} />
                  <NumberField label="首次重试等待" description="后续失败按 2 倍递增等待，单位为秒。" value={draft.base_retry_seconds} min={1} max={86400} onChange={(base_retry_seconds) => setDraft({ ...draft, base_retry_seconds })} />
                </div>
              </details>
              <div className="border-y border-[#e8c876] bg-[#fff9e9] px-3 py-3 text-xs leading-5 text-[#6f5513]">{draft.real_model_calls_enabled ? "优化执行器已连接；关闭 dry-run 后将发生真实计费，但只会创建候选草稿和配对回归。" : "优化执行器尚不可用；请先在模型配置中补齐密钥、输入上限和非零计价。"} 自动发布永久关闭。</div>
              {draft.enabled && !draft.dry_run && <label className="flex items-start gap-3 border-y border-[#c55b52] bg-[#fff0ee] px-3 py-3 text-xs leading-5 text-[#7d201a]"><input className="mt-1 size-4" type="checkbox" checked={riskConfirmed} onChange={(event) => setRiskConfirmed(event.target.checked)} /><span>确认已核对日预算、真实计价和锁定黄金样本；保存后消费者可产生真实模型费用。</span></label>}
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => save.mutate()} disabled={save.isPending || (draft.enabled && !draft.dry_run && !riskConfirmed)}>保存配置</Button>
              <p className="text-xs leading-5 text-[var(--muted)]">纠偏案例由常驻 Worker 自动处理，无需逐批点击。此处仅保留管理员的手动重试入口。</p>
              <Button variant="ghost" onClick={() => consume.mutate()} disabled={consume.isPending || !draft.enabled}>手动重试</Button>
                {!draft.real_model_calls_enabled && !draft.dry_run && (
                  <Button asChild variant="ghost"><Link to="/workflow/governance/model-config">配置优化模型<ArrowRight /></Link></Button>
                )}
              </div>
            </div>
          )}
        </section>
        <section>
          {draft && <div className="mb-5 grid grid-cols-2 border-y border-[var(--line-strong)] bg-white sm:grid-cols-4"><Metric label="当日已用" value={String(draft.budget.spent_micros)} /><Metric label="执行预留" value={String(draft.budget.reserved_micros)} /><Metric label="剩余预算" value={String(draft.budget.remaining_micros)} /><Metric label="日上限" value={String(draft.budget.limit_micros)} /></div>}
          <DataTable
            loading={runs.isLoading}
            empty="还没有自动优化运行"
            headers={["运行", "提示词", "触发", "模式", "状态", "案例 / 候选", "成本 / Token", "下一步"]}
            rows={(runs.data?.items ?? []).map((run) => [
              <span key="id" className="font-data">#{run.id}</span>,
              <span key="prompt" className="font-data text-xs">{run.base_prompt_version}</span>,
              <span key="trigger" className="text-xs">{run.trigger_reason}</span>,
              <Badge key="mode">{run.dry_run ? "dry-run" : "真实执行"}</Badge>,
              <Badge key="status" tone={run.status === "failed" ? "danger" : run.status === "succeeded" || run.status === "awaiting_release_review" ? "success" : run.status === "processing" ? "active" : "neutral"}>{automationStatus(run.status)}</Badge>,
              <span key="cases" className="font-data">{run.case_ids.length} / {run.candidate_count}</span>,
              <span key="cost" className="font-data text-xs">{run.actual_cost_micros} / {run.total_tokens ?? "—"}</span>,
              <RunNextStep key="next" run={run} />,
            ])}
          />
        </section>
      </div>
    </>
  )
}

type AutomationSeverity = AutomationPolicy["immediate_severities"][number]

function automationPolicyBody(policy: AutomationPolicy) {
  return {
    enabled: policy.enabled,
    dry_run: policy.dry_run,
    case_threshold: policy.case_threshold,
    immediate_severities: policy.immediate_severities,
    daily_budget_micros: policy.daily_budget_micros,
    cooldown_seconds: policy.cooldown_seconds,
    max_candidates: policy.max_candidates,
    lease_seconds: policy.lease_seconds,
    max_attempts: policy.max_attempts,
    base_retry_seconds: policy.base_retry_seconds,
  }
}

function nextAutomationBatch(
  items: OptimizationCase[],
  policy: AutomationPolicy,
) {
  const current = Date.now()
  const available = items
    .filter((item) => (
      item.attempt_count < policy.max_attempts
      && (
        item.status === "pending"
        || (
          item.status === "failed"
          && item.next_attempt_at !== null
          && new Date(item.next_attempt_at).getTime() <= current
        )
      )
    ))
    .sort((a, b) => (
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      || a.id - b.id
    ))
  const immediate = available.find((item) =>
    policy.immediate_severities.includes(item.severity),
  )
  const promptVersion = immediate?.prompt_version ?? available[0]?.prompt_version
  const samePrompt = promptVersion
    ? available.filter((item) => item.prompt_version === promptVersion)
    : []
  return {
    caseCount: samePrompt.length,
    promptVersion: promptVersion ?? null,
    immediateSeverity: immediate?.severity ?? null,
    required: policy.case_threshold,
    ready: Boolean(immediate || samePrompt.length >= policy.case_threshold),
  }
}

function automationNextTitle(
  policy: AutomationPolicy | null,
  batch: ReturnType<typeof nextAutomationBatch> | null,
) {
  if (!policy) return "正在读取运行条件"
  if (!batch?.caseCount) return "等待新的纠偏案例"
  if (!policy.enabled) return "先生成一次安全试跑计划"
  if (!batch.ready) return `同版本案例还差 ${Math.max(0, batch.required - batch.caseCount)} 条`
  if (policy.dry_run) return "当前批次可以安全试跑"
  if (!policy.real_model_calls_enabled) return "先补齐优化模型配置"
  if (policy.daily_budget_micros <= 0) return "先设置真实执行费用上限"
  return "当前批次可以生成候选提示词"
}

function automationNextDescription(
  policy: AutomationPolicy | null,
  batch: ReturnType<typeof nextAutomationBatch> | null,
) {
  if (!policy) return "系统正在核对消费者、案例数量、模型配置和费用门槛。"
  if (!batch?.caseCount) {
    return "案例会在人工纠偏完成或基准偏差手动入队后出现在这里。生产回流也会进入同一只追加案例池。"
  }
  if (!policy.enabled) {
    return "点击下方按钮会开启队列消费者、把本批数量设为门槛并保持试运行模式。它只冻结选中的案例范围，不调用模型、不产生费用。"
  }
  if (!batch.ready) {
    return `当前提示词版本 ${batch.promptVersion} 有 ${batch.caseCount} 条可用案例，策略要求 ${batch.required} 条。可以继续积累，也可生成一次以当前数量为门槛的安全试跑。`
  }
  if (policy.dry_run) {
    return "当前设置只会验证组批范围。试跑结果确认无误后，再配置模型、费用上限并关闭试运行，才会真正生成候选。"
  }
  if (!policy.real_model_calls_enabled) {
    return "真实执行需要在“系统治理 → 模型配置”中保存优化模型密钥、输入上限和输入/输出计价。"
  }
  return "执行后只会创建候选提示词草稿和配对回归，不会自动发布或切换线上提示词。"
}

function RunNextStep({ run }: { run: AutomationRun }) {
  if (run.error_message) {
    return (
      <p className="max-w-56 text-xs leading-5 text-[#8d2924]">
        {executorError(run.error_message)}
        {run.retryable ? "；系统会按策略重试" : "；需修复后重新检查"}
      </p>
    )
  }
  if (run.status === "planned") {
    return (
      <p className="max-w-56 text-xs leading-5 text-[var(--muted)]">
        核对本批范围；确认后再配置模型、预算并关闭试运行。
      </p>
    )
  }
  if (run.status === "succeeded" || run.status === "awaiting_release_review") {
    return (
      <Button asChild size="sm" variant="secondary">
        <Link to="/workflow/optimization/candidates">查看候选<ArrowRight /></Link>
      </Button>
    )
  }
  return (
    <p className="max-w-56 text-xs leading-5 text-[var(--muted)]">
      {run.status === "processing" || run.status === "running"
        ? "等待执行完成"
        : "无需操作"}
    </p>
  )
}

function SeverityField({
  values,
  onChange,
}: {
  values: AutomationSeverity[]
  onChange: (values: AutomationSeverity[]) => void
}) {
  const options: Array<{
    value: AutomationSeverity
    label: string
    description: string
  }> = [
    { value: "P0", label: "P0", description: "阻断性严重错误" },
    { value: "P1", label: "P1", description: "高风险明显错误" },
    { value: "P2", label: "P2", description: "常规质量偏差" },
    { value: "P3", label: "P3", description: "低优先级建议" },
  ]
  return (
    <fieldset>
      <legend className="text-sm font-semibold">无需等满即可触发</legend>
      <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
        命中所选优先级时，即使未达到组批数量也可形成一批。至少保留一项。
      </p>
      <div className="mt-3 grid grid-cols-2 gap-px bg-[var(--line)]">
        {options.map((option) => {
          const checked = values.includes(option.value)
          return (
            <label key={option.value} className="flex cursor-pointer gap-2 bg-white px-3 py-3">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-[var(--primary)]"
                checked={checked}
                onChange={(event) => {
                  const next = event.target.checked
                    ? [...values, option.value]
                    : values.filter((value) => value !== option.value)
                  if (next.length) onChange(next)
                }}
              />
              <span>
                <span className="block text-xs font-bold">{option.label}</span>
                <span className="mt-1 block text-[0.68rem] leading-4 text-[var(--muted)]">{option.description}</span>
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

function ToggleLine({ label, description, checked, onChange }: { label: string; description?: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex items-start justify-between gap-4 border-b border-[var(--line)] pb-3"><span><span className="block text-sm font-semibold">{label}</span>{description && <span className="mt-1 block text-xs leading-5 text-[var(--muted)]">{description}</span>}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-1 size-4 shrink-0 accent-[var(--primary)]" /></label>
}

function NumberField({ label, description, value, min, max, onChange }: { label: string; description?: string; value: number; min: number; max?: number; onChange: (value: number) => void }) {
  return <label className="grid grid-cols-[minmax(0,1fr)_132px] items-center gap-4"><span><span className="block text-sm font-semibold">{label}</span>{description && <span className="mt-1 block text-xs leading-5 text-[var(--muted)]">{description}</span>}</span><input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} className="h-9 w-full rounded-[4px] border border-[var(--line-strong)] px-3 font-data" /></label>
}

function automationStatus(value: string) {
  return ({
    disabled: "已关闭",
    idle: "无待处理案例",
    threshold_wait: "等待阈值",
    cooldown: "冷却中",
    budget_blocked: "预算阻断",
    lease_conflict: "租约冲突",
    executor_config_blocked: "执行配置阻断",
    planned: "dry-run 已规划",
    awaiting_executor: "等待执行器",
    processing: "处理中",
    succeeded: "已生成候选与回归",
    running: "处理中",
    awaiting_release_review: "等待发布二审",
    failed: "失败待恢复",
    cancelled: "已取消",
  } as Record<string, string>)[value] ?? value
}

function automationRuntimeTitle(status: AutomationPolicy["runtime"]["status"]) {
  return {
    ready: "条件已满足，Worker 会自动领取下一批",
    waiting: "Worker 正常，等待组批条件满足",
    blocked: "自动化被明确门禁阻止",
  }[status]
}

function automationRuntimeTone(status: AutomationPolicy["runtime"]["status"]) {
  return status === "ready" ? "success" : status === "waiting" ? "warning" : "danger"
}

function automationBlockerTone(
  severity: AutomationPolicy["runtime"]["blockers"][number]["severity"],
) {
  return severity === "blocking" ? "danger" : severity === "info" ? "neutral" : "warning"
}

function automationBlockerLabel(
  severity: AutomationPolicy["runtime"]["blockers"][number]["severity"],
) {
  return severity === "blocking" ? "需要处理" : severity === "waiting" ? "等待中" : severity === "warning" ? "注意" : "信息"
}

function runtimeLastCheck(workers: AutomationPolicy["runtime"]["worker"]["workers"]) {
  const latest = workers[0]?.last_tick_at
  return latest ? new Date(latest).toLocaleString("zh-CN") : "未检测到"
}

function RuntimeMetric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="min-w-0 bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="mt-2 truncate text-sm font-semibold" title={value}>{value}</p>
      <p className="mt-1 truncate font-data text-xs text-[var(--muted)]" title={detail}>{detail}</p>
    </div>
  )
}
