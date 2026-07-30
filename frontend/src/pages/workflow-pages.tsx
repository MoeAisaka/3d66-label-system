import { ArrowRight, Clock, Play, Prohibit, ShieldWarning } from "@phosphor-icons/react"
import { useEffect, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type {
  AuditEvent,
  AutomationPolicy,
  AutomationRun,
  ModelBenchmark,
  ModelConfig,
  OptimizationCase,
  ProductionFeedbackEvent,
  PromptMetricSnapshot,
  PromptVersion,
  RegressionSummary,
  StrategyBundleSummary,
  User,
} from "@/lib/types"

const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`

type BenchmarkForm = {
  experimentKey: string
  name: string
  executionMode: "test" | "real"
  cohortAssetIds: string
  strategyBundleId: number
  modelConfigIds: Record<"sol" | "terra" | "luna", number>
  maxRoundCostMicros: number
  qualityGateApproved: boolean
}

export function OptimizationCasesPage() {
  const cases = useQuery({
    queryKey: ["optimization-cases"],
    queryFn: () => api<{ items: OptimizationCase[] }>("/api/optimization-cases?limit=500"),
  })
  return (
    <>
      <PageHeader
        index="03.1"
        title="纠偏案例队列"
        description="初审最终纠偏以幂等事件进入这里；队列只沉淀证据，不会自动调用优化模型或发布提示词。"
      />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <DataTable
          loading={cases.isLoading}
          empty="还没有完成的纠偏案例"
          headers={["优先级", "来源", "证据", "提示词版本", "队列状态", "进入时间", "下一步"]}
          rows={(cases.data?.items ?? []).map((item) => [
            <Badge key="severity" tone={item.severity === "P0" || item.severity === "P1" ? "danger" : "warning"}>{item.severity}</Badge>,
            <Badge key="source">{item.source_type === "production_feedback" ? "生产回流" : "实验台初审"}</Badge>,
            <span key="evaluation" className="font-data">{item.evaluation_id ? `评测 #${item.evaluation_id}` : `事件 #${item.source_event_id}`}</span>,
            <span key="prompt" className="font-data text-xs">{item.prompt_version}</span>,
            <Badge key="status">{caseStatus(item.status)}</Badge>,
            <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(item.created_at).toLocaleString("zh-CN")}</span>,
            <Button key="next" asChild size="sm" variant="secondary"><Link to="/workflow/optimization/automation">查看自动编排<ArrowRight /></Link></Button>,
          ])}
        />
      </div>
    </>
  )
}

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
  const [draft, setDraft] = useState<AutomationPolicy | null>(null)
  const [riskConfirmed, setRiskConfirmed] = useState(false)
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  useEffect(() => {
    if (policy.data) setDraft(policy.data)
  }, [policy.data])
  const save = useMutation({
    mutationFn: () => api<AutomationPolicy>("/api/automation-policy", {
      method: "PUT",
      ...jsonBody({
        enabled: draft?.enabled,
        dry_run: draft?.dry_run,
        case_threshold: draft?.case_threshold,
        immediate_severities: draft?.immediate_severities,
        daily_budget_micros: draft?.daily_budget_micros,
        cooldown_seconds: draft?.cooldown_seconds,
        max_candidates: draft?.max_candidates,
        lease_seconds: draft?.lease_seconds,
        max_attempts: draft?.max_attempts,
        base_retry_seconds: draft?.base_retry_seconds,
      }),
    }),
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
  return (
    <>
      <PageHeader index="03.2" title="自动优化编排" description="消费者默认关闭并保持 dry-run；租约、阈值、P0/P1即时触发、预算、冷却、重试与审计均在本地执行。自动链路最多到发布二审，永不自动发布。" />
      <div className="mx-auto grid max-w-[1540px] gap-6 px-5 py-8 md:px-8 lg:grid-cols-[380px_minmax(0,1fr)] lg:px-10">
        <section className="border-y border-[var(--line-strong)] bg-white p-5">
          <div className="flex items-center justify-between"><h2 className="font-editorial text-xl font-bold">消费者策略</h2><Badge tone={draft?.enabled ? "active" : "neutral"}>{draft?.enabled ? "已启用" : "已关闭"}</Badge></div>
          {!draft ? <div className="mt-5 h-64 animate-pulse bg-[#fafbf8]" /> : !me.data?.is_admin ? (
            <p className="mt-5 border-y border-[var(--line)] py-4 text-sm text-[var(--muted)]">当前账号可查看运行与预算，但无权修改执行策略。</p>
          ) : (
            <div className="mt-5 space-y-4">
              <ToggleLine label="启用自动消费者" checked={draft.enabled} onChange={(enabled) => setDraft({ ...draft, enabled })} />
              <ToggleLine label="安全 dry-run" checked={draft.dry_run} onChange={(dry_run) => setDraft({ ...draft, dry_run })} />
              <NumberField label="组批阈值" value={draft.case_threshold} min={1} onChange={(case_threshold) => setDraft({ ...draft, case_threshold })} />
              <NumberField label="日预算（微单位）" value={draft.daily_budget_micros} min={0} onChange={(daily_budget_micros) => setDraft({ ...draft, daily_budget_micros })} />
              <NumberField label="冷却秒数" value={draft.cooldown_seconds} min={0} onChange={(cooldown_seconds) => setDraft({ ...draft, cooldown_seconds })} />
              <NumberField label="候选上限" value={draft.max_candidates} min={1} max={5} onChange={(max_candidates) => setDraft({ ...draft, max_candidates })} />
              <NumberField label="租约秒数" value={draft.lease_seconds} min={30} max={3600} onChange={(lease_seconds) => setDraft({ ...draft, lease_seconds })} />
              <NumberField label="最大尝试次数" value={draft.max_attempts} min={1} max={10} onChange={(max_attempts) => setDraft({ ...draft, max_attempts })} />
              <NumberField label="基础退避秒数" value={draft.base_retry_seconds} min={1} max={86400} onChange={(base_retry_seconds) => setDraft({ ...draft, base_retry_seconds })} />
              <div className="border-y border-[#e8c876] bg-[#fff9e9] px-3 py-3 text-xs leading-5 text-[#6f5513]">{draft.real_model_calls_enabled ? "优化执行器已连接；关闭 dry-run 后将发生真实计费，但只会创建候选草稿和配对回归。" : "优化执行器尚不可用；请先在模型配置中补齐密钥、输入上限和非零计价。"} 自动发布永久关闭。</div>
              {draft.enabled && !draft.dry_run && <label className="flex items-start gap-3 border-y border-[#c55b52] bg-[#fff0ee] px-3 py-3 text-xs leading-5 text-[#7d201a]"><input className="mt-1 size-4" type="checkbox" checked={riskConfirmed} onChange={(event) => setRiskConfirmed(event.target.checked)} /><span>确认已核对日预算、真实计价和锁定黄金样本；保存后消费者可产生真实模型费用。</span></label>}
              <div className="flex gap-2"><Button onClick={() => save.mutate()} disabled={save.isPending || (draft.enabled && !draft.dry_run && !riskConfirmed)}>保存策略</Button><Button variant="secondary" onClick={() => consume.mutate()} disabled={consume.isPending}>立即检查一次</Button></div>
            </div>
          )}
        </section>
        <section>
          {draft && <div className="mb-5 grid grid-cols-2 border-y border-[var(--line-strong)] bg-white sm:grid-cols-4"><Metric label="当日已用" value={String(draft.budget.spent_micros)} /><Metric label="执行预留" value={String(draft.budget.reserved_micros)} /><Metric label="剩余预算" value={String(draft.budget.remaining_micros)} /><Metric label="日上限" value={String(draft.budget.limit_micros)} /></div>}
          <DataTable
            loading={runs.isLoading}
            empty="还没有自动优化运行"
            headers={["运行", "提示词", "触发", "模式", "状态", "案例 / 候选", "成本 / Token", "失败与重试"]}
            rows={(runs.data?.items ?? []).map((run) => [
              <span key="id" className="font-data">#{run.id}</span>,
              <span key="prompt" className="font-data text-xs">{run.base_prompt_version}</span>,
              <span key="trigger" className="text-xs">{run.trigger_reason}</span>,
              <Badge key="mode">{run.dry_run ? "dry-run" : "真实执行"}</Badge>,
              <Badge key="status" tone={run.status === "failed" ? "danger" : run.status === "succeeded" || run.status === "awaiting_release_review" ? "success" : run.status === "processing" ? "active" : "neutral"}>{automationStatus(run.status)}</Badge>,
              <span key="cases" className="font-data">{run.case_ids.length} / {run.candidate_count}</span>,
              <span key="cost" className="font-data text-xs">{run.actual_cost_micros} / {run.total_tokens ?? "—"}</span>,
              <span key="retry" className="text-xs text-[var(--muted)]">{run.error_message ? `${executorError(run.error_message)}${run.retryable ? "，将按策略退避重试" : "，已终止重试"}` : "—"}</span>,
            ])}
          />
        </section>
      </div>
    </>
  )
}

export function ProductionFeedbackPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  const events = useQuery({
    queryKey: ["production-feedback-events"],
    queryFn: () => api<{ items: ProductionFeedbackEvent[] }>("/api/production-feedback-events?limit=500"),
  })
  const authStatus = useQuery({
    queryKey: ["production-feedback-config-status"],
    queryFn: () => api<{ configured: boolean; authentication: string; browser_session_accepted: false }>("/api/production-feedback-config-status"),
    enabled: me.data?.is_admin === true,
  })
  return (
    <>
      <PageHeader index="03.3" title="生产案例回流" description="这里只接收生产系统已落地的最终人工纠偏事件，并幂等映射到实验台优化队列；事件不可变，不写生产数据库，也不自动实装提示词。" />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-y border-[var(--line-strong)] bg-white px-5 py-4 text-sm"><span className="font-semibold">机器接收鉴权</span><div className="flex gap-2"><Badge tone={authStatus.data?.configured ? "success" : "danger"}>{authStatus.data?.configured ? "专用 Token 已配置" : "未配置，写入关闭"}</Badge><Badge>浏览器会话不可写入</Badge></div></div>
        <DataTable
          loading={events.isLoading}
          empty="还没有生产反馈事件"
          headers={["事件", "来源系统", "生产案例", "严重度", "提示词", "队列映射", "接收时间", "边界"]}
          rows={(events.data?.items ?? []).map((event) => {
            const payload = event.payload as { production_case_id?: string; severity?: string; prompt_version?: string }
            return [
              <span key="event" className="font-data text-xs">{event.event_id}</span>,
              <span key="source">{event.source_system}</span>,
              <span key="case" className="font-data text-xs">{payload.production_case_id ?? "—"}</span>,
              <Badge key="severity" tone={payload.severity === "P0" || payload.severity === "P1" ? "danger" : "warning"}>{payload.severity ?? "—"}</Badge>,
              <span key="prompt" className="font-data text-xs">{payload.prompt_version ?? "—"}</span>,
              <span key="mapping" className="font-data">{event.optimization_case_id ? `#${event.optimization_case_id}` : "—"}</span>,
              <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(event.received_at).toLocaleString("zh-CN")}</span>,
              <Badge key="boundary">只读回流</Badge>,
            ]
          })}
        />
      </div>
    </>
  )
}

export function BenchmarkPage() {
  const queryClient = useQueryClient()
  const benchmarks = useQuery({
    queryKey: ["model-benchmarks"],
    queryFn: () => api<{ items: ModelBenchmark[] }>("/api/model-benchmarks?limit=100"),
  })
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  const modelConfigs = useQuery({
    queryKey: ["model-configs"],
    queryFn: () => api<{ items: ModelConfig[] }>("/api/model-configs"),
    enabled: me.data?.is_admin === true,
  })
  const bundles = useQuery({
    queryKey: ["strategy-bundles"],
    queryFn: () => api<{ items: StrategyBundleSummary[] }>("/api/strategy-bundles"),
    enabled: me.data?.is_admin === true,
  })
  const [form, setForm] = useState<BenchmarkForm>({
    experimentKey: "",
    name: "",
    executionMode: "test",
    cohortAssetIds: "",
    strategyBundleId: 0,
    modelConfigIds: { sol: 0, terra: 0, luna: 0 },
    maxRoundCostMicros: 0,
    qualityGateApproved: false,
  })
  const [createRiskConfirmed, setCreateRiskConfirmed] = useState(false)
  const [runRiskConfirmedId, setRunRiskConfirmedId] = useState<number | null>(null)
  useEffect(() => {
    const firstBundle = bundles.data?.items[0]
    if (firstBundle) {
      setForm((current) => current.strategyBundleId ? current : { ...current, strategyBundleId: firstBundle.id })
    }
  }, [bundles.data?.items])
  const eligibleConfigs = (modelConfigs.data?.items ?? []).filter((item) =>
    item.benchmark_enabled && item.has_api_key && item.max_input_tokens > 0 &&
    item.input_micros_per_million_tokens > 0 && item.output_micros_per_million_tokens > 0,
  )
  const create = useMutation({
    mutationFn: () => {
      const cohortAssetIds = Array.from(new Set(
        form.cohortAssetIds.split(/[\s,，]+/).filter(Boolean).map(Number),
      )).filter((value) => Number.isInteger(value) && value > 0)
      return api<ModelBenchmark>("/api/model-benchmarks", {
        method: "POST",
        ...jsonBody({
          experiment_key: form.experimentKey,
          name: form.name,
          execution_mode: form.executionMode,
          cohort_asset_ids: cohortAssetIds,
          strategy_bundle_id: form.strategyBundleId,
          variants: (["sol", "terra", "luna"] as const).map((modelKey) => form.executionMode === "real" ? {
            model_key: modelKey,
            model_config_id: form.modelConfigIds[modelKey],
            human_review_cost_micros: 0,
          } : {
            model_key: modelKey,
            provider: "test",
            model_id: `test-${modelKey}`,
            input_micros_per_million_tokens: 0,
            output_micros_per_million_tokens: 0,
            human_review_cost_micros: 0,
          }),
          min_quality_accuracy: 0.9,
          max_p0_p1_errors: 0,
          min_retry_stability: 0.95,
          low_confidence_threshold: 0.7,
          max_round_cost_micros: form.executionMode === "real" ? form.maxRoundCostMicros : 0,
          quality_gate_approved: form.executionMode === "real" && form.qualityGateApproved,
        }),
      })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-benchmarks"] })
      setCreateRiskConfirmed(false)
      toast.success("横评实验已冻结")
    },
    onError: (error) => toast.error(error.message),
  })
  const runReal = useMutation({
    mutationFn: (experimentId: number) => api<ModelBenchmark>(`/api/model-benchmarks/${experimentId}/run-real`, { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-benchmarks"] })
      setRunRiskConfirmedId(null)
      toast.success("真实横评执行完成")
    },
    onError: (error) => toast.error(error.message),
  })
  const realConfigComplete = (["sol", "terra", "luna"] as const).every((key) => form.modelConfigIds[key] > 0)
  const cohortComplete = form.cohortAssetIds.split(/[\s,，]+/).some((value) => Number(value) > 0)
  const canCreate = Boolean(
    form.experimentKey.trim() && form.name.trim() && form.strategyBundleId > 0 && cohortComplete &&
    (form.executionMode === "test" || (
      realConfigComplete && form.maxRoundCostMicros > 0 && form.qualityGateApproved && createRiskConfirmed
    )),
  )
  return (
    <>
      <PageHeader index="05.1" title="Sol / Terra / Luna 横评" description="每次实验冻结同一 cohort、Prompt、Rubric、Engine 与 AgentPlan。默认使用测试替身；真实模式必须由管理员配置三项服务端模型、质量门和单轮成本上限。" />
      <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-8 md:px-8 lg:px-10">
        {me.data?.is_admin && <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-5 px-5 py-5 lg:grid-cols-[220px_minmax(0,1fr)]">
            <div><h2 className="font-editorial text-xl font-bold">冻结新实验</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">真实模式不会自动改变生产模型，结果仍需人工决定。</p></div>
            <div className="space-y-4">
              <div className="inline-grid grid-cols-2 rounded-[4px] border border-[var(--line-strong)] p-1" role="group" aria-label="执行模式">
                {(["test", "real"] as const).map((mode) => <button key={mode} type="button" className={`h-9 px-5 text-sm font-bold ${form.executionMode === mode ? "bg-primary" : "bg-white"}`} onClick={() => { setForm({ ...form, executionMode: mode }); setCreateRiskConfirmed(false) }}>{mode === "test" ? "测试替身" : "真实执行"}</button>)}
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-xs font-semibold">实验键<Input className="mt-2" value={form.experimentKey} onChange={(event) => setForm({ ...form, experimentKey: event.target.value })} /></label>
                <label className="text-xs font-semibold">实验名称<Input className="mt-2" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
                <label className="text-xs font-semibold">素材 ID<Input className="mt-2" value={form.cohortAssetIds} onChange={(event) => setForm({ ...form, cohortAssetIds: event.target.value })} placeholder="例如：12, 18, 27" /></label>
                <label className="text-xs font-semibold">StrategyBundle<select className="mt-2 h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={form.strategyBundleId} onChange={(event) => setForm({ ...form, strategyBundleId: Number(event.target.value) })}><option value={0}>请选择冻结策略</option>{(bundles.data?.items ?? []).map((bundle) => <option key={bundle.id} value={bundle.id}>#{bundle.id} · {bundle.model_id} · {bundle.prompt_a_version}/{bundle.prompt_b_version ?? "—"}</option>)}</select></label>
              </div>
              {form.executionMode === "real" && <div className="space-y-4 border-y border-[#e8c876] bg-[#fff9e9] px-4 py-4">
                <div className="grid gap-4 md:grid-cols-3">{(["sol", "terra", "luna"] as const).map((key) => <label key={key} className="text-xs font-semibold uppercase">{key}<select className="mt-2 h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm normal-case" value={form.modelConfigIds[key]} onChange={(event) => setForm({ ...form, modelConfigIds: { ...form.modelConfigIds, [key]: Number(event.target.value) } })}><option value={0}>选择已启用配置</option>{eligibleConfigs.map((config) => <option key={config.id} value={config.id}>{config.name} · {config.model_id}</option>)}</select></label>)}</div>
                <label className="block text-xs font-semibold">单轮成本上限（micros）<Input className="mt-2 max-w-xs" type="number" min="1" value={form.maxRoundCostMicros} onChange={(event) => setForm({ ...form, maxRoundCostMicros: Number(event.target.value) })} /></label>
                <label className="flex items-start gap-3 text-xs leading-5"><input className="mt-1 size-4" type="checkbox" checked={form.qualityGateApproved} onChange={(event) => setForm({ ...form, qualityGateApproved: event.target.checked })} /><span>确认该冻结组合已完成质量门审查；服务端仍会先验证冻结哈希、版本和预测成本。</span></label>
                <label className="flex items-start gap-3 text-xs font-semibold leading-5 text-[#7d201a]"><input className="mt-1 size-4" type="checkbox" checked={createRiskConfirmed} onChange={(event) => setCreateRiskConfirmed(event.target.checked)} /><span>确认创建的是可产生真实模型费用的实验，且不会自动扩预算或切换生产模型。</span></label>
              </div>}
              <Button onClick={() => create.mutate()} disabled={!canCreate || create.isPending}><ShieldWarning />冻结实验</Button>
            </div>
          </div>
        </section>}
        {!me.data?.is_admin && <div className="flex items-center gap-3 border-y border-[var(--line-strong)] bg-white px-5 py-4 text-sm text-[var(--muted)]"><Prohibit />当前账号可查看冻结实验与结果，但无权配置或运行真实横评。</div>}
        {(benchmarks.data?.items ?? []).map((experiment) => (
          <section key={experiment.id} className="border-y border-[var(--line-strong)] bg-white">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
              <div><h2 className="font-editorial text-xl font-bold">{experiment.name}</h2><p className="font-data mt-1 text-xs text-[var(--muted)]">{experiment.experiment_key} · cohort {experiment.frozen_snapshot.cohort_asset_ids.length} · {experiment.snapshot_hash.slice(0, 12)}</p></div>
              <div className="flex gap-2"><Badge tone={experiment.execution_mode === "real" ? "warning" : "neutral"}>{experiment.execution_mode === "real" ? "真实执行" : experiment.execution_mode === "test" ? "测试替身" : "未启用"}</Badge><Badge tone={experiment.status === "completed" ? "success" : experiment.status === "failed" ? "danger" : experiment.status === "running" ? "active" : "neutral"}>{benchmarkStatus(experiment.status)}</Badge></div>
            </div>
            <div className="grid grid-cols-2 border-b border-[var(--line)] bg-[#fafbf8] sm:grid-cols-4"><Metric label="预测成本" value={String(experiment.frozen_snapshot.predicted_cost_micros ?? 0)} /><Metric label="实际成本" value={String(experiment.actual_cost_micros)} /><Metric label="单轮上限" value={String(experiment.max_round_cost_micros)} /><Metric label="质量门" value={experiment.quality_gate.approved_for_real_execution === true ? "已批准" : "测试模式"} /></div>
            <div className="grid divide-y divide-[var(--line)] xl:grid-cols-3 xl:divide-x xl:divide-y-0">
              {experiment.variants.map((variant) => <div key={variant.id} className="p-5"><div className="flex items-center justify-between"><strong className="uppercase">{variant.model_key}</strong><Badge tone={variant.status === "failed" ? "danger" : variant.status === "completed" ? "success" : variant.status === "running" ? "active" : "neutral"}>{variant.status}</Badge></div><p className="font-data mt-2 text-xs text-[var(--muted)]">{variant.model_id}</p><div className="mt-4 grid grid-cols-2 gap-3 text-xs"><Metric label="质量准确率" value={percent(variant.metrics.quality_accuracy)} /><Metric label="P0/P1" value={String(variant.metrics.p0_p1_error_count ?? "—")} /><Metric label="实际成本" value={String(variant.actual_cost_micros)} /><Metric label="Token" value={String(variant.total_tokens ?? "—")} /><Metric label="人工率" value={percent(variant.metrics.human_review_rate)} /><Metric label="P95" value={variant.metrics.latency_p95_ms == null ? "—" : `${variant.metrics.latency_p95_ms.toFixed(0)}ms`} /></div>{variant.error_message && <p className="mt-4 border-t border-[var(--line)] pt-3 text-xs text-[#8d2924]">{executorError(variant.error_message)}</p>}</div>)}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-4 border-t border-[var(--line)] bg-[#fafbf8] px-5 py-4 text-sm"><div>人工候选建议：<strong>{experiment.decision.recommendation?.toUpperCase() ?? "尚无"}</strong><span className="ml-3 text-xs text-[var(--muted)]">{experiment.decision.reason ?? "等待执行证据"}</span></div>{me.data?.is_admin && experiment.execution_mode === "real" && (experiment.status === "draft" || experiment.status === "failed") && <div className="flex flex-wrap items-center gap-3"><label className="flex items-center gap-2 text-xs font-semibold"><input type="checkbox" checked={runRiskConfirmedId === experiment.id} onChange={(event) => setRunRiskConfirmedId(event.target.checked ? experiment.id : null)} />确认本轮费用上限</label><Button size="sm" onClick={() => runReal.mutate(experiment.id)} disabled={runRiskConfirmedId !== experiment.id || runReal.isPending}><Play />运行真实横评</Button></div>}</div>
          </section>
        ))}
        {!benchmarks.isLoading && !benchmarks.data?.items.length && <EmptyLine text="还没有冻结的三模型横评实验" />}
      </div>
    </>
  )
}

export function AuditEventsPage() {
  const events = useQuery({
    queryKey: ["audit-events"],
    queryFn: () => api<{ items: AuditEvent[] }>("/api/audit-events?limit=500"),
  })
  return (
    <>
      <PageHeader index="06.3" title="系统审计" description="自动组批、预算阻断、生产回流和模型横评均写入只追加审计事件；事件禁止原地修改或删除。" />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <DataTable loading={events.isLoading} empty="还没有 Phase B 审计事件" headers={["时间", "类别", "动作", "主体", "执行者", "事件键"]} rows={(events.data?.items ?? []).map((event) => [
          <span key="time" className="font-data text-xs">{new Date(event.created_at).toLocaleString("zh-CN")}</span>,
          <Badge key="category">{event.category}</Badge>,
          <span key="action">{event.action}</span>,
          <span key="subject" className="font-data text-xs">{event.subject_type} · {event.subject_id}</span>,
          <span key="actor">{event.actor}</span>,
          <span key="key" className="font-data text-xs text-[var(--muted)]">{event.event_key.slice(0, 28)}</span>,
        ])} />
      </div>
    </>
  )
}

export function ReleaseWorkspacePage({ view }: { view: "decisions" | "metrics" | "history" }) {
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })
  const items = prompts.data?.items ?? []
  const regressions = useQuery({
    queryKey: ["prompt-regressions"],
    queryFn: () => api<{ items: RegressionSummary[] }>("/api/prompt-regressions?limit=200"),
  })
  const queryClient = useQueryClient()
  const [metricPromptId, setMetricPromptId] = useState("")
  const [taskSetKey, setTaskSetKey] = useState("")
  const [metricSource, setMetricSource] = useState<"batch" | "evaluations">("batch")
  const [batchKey, setBatchKey] = useState("")
  const [evaluationIds, setEvaluationIds] = useState("")
  const selectedMetricPromptId = Number(metricPromptId || items[0]?.id || 0)
  useEffect(() => {
    if (!metricPromptId && items[0]) setMetricPromptId(String(items[0].id))
  }, [items, metricPromptId])
  const metricSnapshots = useQuery({
    queryKey: ["prompt-metric-snapshots", selectedMetricPromptId],
    queryFn: () => api<{ items: PromptMetricSnapshot[] }>(
      `/api/prompts/${selectedMetricPromptId}/metric-snapshots`,
    ),
    enabled: view === "metrics" && selectedMetricPromptId > 0,
  })
  const freezeMetrics = useMutation({
    mutationFn: () => {
      const ids = Array.from(new Set(
        evaluationIds
          .split(/[\s,，]+/)
          .map((value) => Number(value))
          .filter((value) => Number.isInteger(value) && value > 0),
      ))
      return api<PromptMetricSnapshot>(
        `/api/prompts/${selectedMetricPromptId}/metric-snapshots`,
        {
          method: "POST",
          ...jsonBody({
            task_set_key: taskSetKey.trim(),
            batch_key: metricSource === "batch" ? batchKey.trim() : null,
            evaluation_ids: metricSource === "evaluations" ? ids : [],
          }),
        },
      )
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["prompt-metric-snapshots", selectedMetricPromptId],
      })
      toast.success("版本指标已按冻结任务集保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const rollback = useMutation({
    mutationFn: (promptId: number) => api(`/api/prompts/${promptId}/rollback`, { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      toast.success("已按回滚指针切回上一版本")
    },
    onError: (error) => toast.error(error.message),
  })

  if (view === "decisions") {
    const pending = (regressions.data?.items ?? []).filter(
      (run) => run.regression_mode === "paired" && run.approval_status === "pending",
    )
    return (
      <>
        <PageHeader index="04.1" title="待发布决策" description="人工二审面向提示词候选与回归证据，不重新审核图片。只有系统建议通过且人工批准后，候选才具备显式发布资格。" />
        <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
          <DataTable
            loading={regressions.isLoading}
            empty="当前没有待人工决策的配对回归"
            headers={["回归任务", "候选提示词", "完成进度", "系统建议", "人工状态", "操作"]}
            rows={pending.map((run) => [
              <span key="name" className="font-semibold">{run.name}</span>,
              <span key="prompt" className="font-data">#{run.trigger_prompt_id ?? "—"}</span>,
              <span key="progress" className="font-data">{run.completed}/{run.total}</span>,
              <Badge key="recommendation" tone={run.recommendation === "pass" ? "success" : run.recommendation === "fail" ? "danger" : "warning"}>{run.recommendation === "pass" ? "建议通过" : run.recommendation === "fail" ? "建议拒绝" : "尚未完成"}</Badge>,
              <Badge key="approval">待人工二审</Badge>,
              <Button key="open" asChild size="sm"><Link to={`/workflow/optimization/paired-regression?run=${run.id}`}>查看回归证据<ArrowRight /></Link></Button>,
            ])}
          />
        </div>
      </>
    )
  }

  if (view === "metrics") {
    const canFreeze = (
      selectedMetricPromptId > 0
      && Boolean(taskSetKey.trim())
      && (
        metricSource === "batch"
          ? Boolean(batchKey.trim())
          : evaluationIds.split(/[\s,，]+/).some((value) => Number(value) > 0)
      )
    )
    return (
      <>
        <PageHeader index="04.2" title="版本指标" description="发布依据必须来自冻结任务集或明确批次；实时全量聚合只保留为运营参考，未完成人工初审的结果不会被当作正确。" />
        <div className="mx-auto max-w-[1540px] space-y-8 px-5 py-8 md:px-8 lg:px-10">
          <section className="border-y border-[var(--line-strong)] bg-white">
            <div className="border-b border-[var(--line)] px-5 py-4">
              <h2 className="font-editorial text-xl font-bold">冻结指标快照</h2>
              <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                选择提示词版本并以批次键或明确评测结果 ID 冻结分母；同一任务集哈希只保存一次。
              </p>
            </div>
            <div className="grid gap-4 px-5 py-5 lg:grid-cols-2">
              <label>
                <span className="mb-2 block text-xs font-semibold">提示词版本</span>
                <select
                  className="h-11 w-full border border-[var(--line-strong)] bg-white px-3 text-sm"
                  value={metricPromptId}
                  onChange={(event) => setMetricPromptId(event.target.value)}
                >
                  {items.map((prompt) => (
                    <option key={prompt.id} value={prompt.id}>
                      {prompt.stage} · {prompt.version} · {prompt.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">冻结任务集业务键</span>
                <Input
                  value={taskSetKey}
                  onChange={(event) => setTaskSetKey(event.target.value)}
                  placeholder="例如：release-2026-07-batch-01"
                />
              </label>
              <div>
                <span className="mb-2 block text-xs font-semibold">冻结来源</span>
                <div className="grid grid-cols-2 border border-[var(--line-strong)]">
                  <button
                    type="button"
                    className={`h-11 border-r border-[var(--line)] text-sm font-bold ${metricSource === "batch" ? "bg-primary" : "bg-white"}`}
                    onClick={() => setMetricSource("batch")}
                  >
                    任务批次
                  </button>
                  <button
                    type="button"
                    className={`h-11 text-sm font-bold ${metricSource === "evaluations" ? "bg-primary" : "bg-white"}`}
                    onClick={() => setMetricSource("evaluations")}
                  >
                    评测结果任务集
                  </button>
                </div>
              </div>
              <label>
                <span className="mb-2 block text-xs font-semibold">
                  {metricSource === "batch" ? "任务批次键" : "评测结果 ID（逗号或空格分隔）"}
                </span>
                {metricSource === "batch" ? (
                  <Input
                    value={batchKey}
                    onChange={(event) => setBatchKey(event.target.value)}
                    placeholder="例如：job-batch-20260729"
                  />
                ) : (
                  <Input
                    value={evaluationIds}
                    onChange={(event) => setEvaluationIds(event.target.value)}
                    placeholder="例如：101, 102, 103"
                  />
                )}
              </label>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-4">
              <p className="text-xs text-[var(--muted)]">
                创建后不会随新评测或后续审核自动变化；如需新口径，创建新的冻结任务集。
              </p>
              <Button
                onClick={() => freezeMetrics.mutate()}
                disabled={!canFreeze || freezeMetrics.isPending}
              >
                {freezeMetrics.isPending ? "正在冻结" : "保存冻结快照"}
              </Button>
            </div>
          </section>
          <DataTable
            loading={metricSnapshots.isLoading}
            empty="当前提示词还没有冻结指标快照"
            headers={["任务集", "冻结哈希", "样本准确率", "等级准确率", "审核覆盖率", "N / 已审", "创建时间"]}
            rows={(metricSnapshots.data?.items ?? []).map((snapshot) => [
              <div key="key"><p className="font-semibold">{snapshot.task_set_key}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">评测 {snapshot.evaluation_ids.length} 条</p></div>,
              <span key="hash" className="font-data text-xs">{snapshot.task_set_hash.slice(0, 12)}</span>,
              <strong key="accuracy" className="font-data">{percent(snapshot.metrics.sample_accuracy)}</strong>,
              <span key="grade" className="font-data">{percent(snapshot.metrics.grade_accuracy)}</span>,
              <span key="coverage" className="font-data">{percent(snapshot.metrics.review_coverage)}</span>,
              <span key="n" className="font-data">{snapshot.total_count} / {snapshot.reviewed_count}</span>,
              <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(snapshot.created_at).toLocaleString("zh-CN")}</span>,
            ])}
          />
          <div>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="font-editorial text-xl font-bold">实时全量参考</h2>
                <p className="mt-1 text-xs text-[var(--muted)]">只用于发现趋势，不作为提示词发布依据。</p>
              </div>
              <Badge tone="warning">非冻结口径</Badge>
            </div>
          <DataTable
            loading={prompts.isLoading}
            empty="没有提示词版本"
            headers={["版本", "状态", "样本准确率", "纠偏率", "等级准确率", "审核覆盖率", "N / 总评测"]}
            rows={items.map((prompt) => {
              const metrics = prompt.metrics
              return [
                <div key="version"><p className="font-data font-semibold">{prompt.version}</p><p className="mt-1 text-xs text-[var(--muted)]">{prompt.stage} 阶段 · {prompt.name}</p></div>,
                <Badge key="status" tone={prompt.status === "published" ? "success" : "neutral"}>{promptStatus(prompt.status)}</Badge>,
                <strong key="accuracy" className="font-data">{percent(metrics?.sample_accuracy)}</strong>,
                <span key="correction" className="font-data">{metrics?.sample_size_n ? percent((metrics.corrected_sample_count ?? 0) / metrics.sample_size_n) : "—"}</span>,
                <span key="grade" className="font-data">{percent(metrics?.grade_accuracy)}</span>,
                <span key="coverage" className="font-data">{percent(metrics?.review_coverage)}</span>,
                <span key="n" className="font-data">{metrics?.sample_size_n ?? 0} / {metrics?.total_evaluations ?? 0}</span>,
              ]
            })}
          />
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader index="04.3" title="版本历史与回滚" description="版本只追加；已发布版本保留可验证回滚指针和金丝雀状态。回滚是显式人工动作，不会由失败状态自动触发。" />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <DataTable
          loading={prompts.isLoading}
          empty="没有提示词版本"
          headers={["版本", "阶段", "状态", "回滚指针", "金丝雀", "更新时间", "动作"]}
          rows={items.map((prompt) => [
            <div key="version"><p className="font-data font-semibold">{prompt.version}</p><p className="mt-1 text-xs text-[var(--muted)]">{prompt.name}</p></div>,
            <Badge key="stage">{prompt.stage}</Badge>,
            <Badge key="status" tone={prompt.status === "published" ? "success" : "neutral"}>{promptStatus(prompt.status)}</Badge>,
            <span key="rollback" className="font-data">{prompt.rollback_prompt_id ? `#${prompt.rollback_prompt_id}` : "未建立"}</span>,
            <Badge key="canary" tone={prompt.canary_status === "passed" ? "success" : prompt.canary_status === "failed" ? "danger" : "neutral"}>{canaryStatus(prompt.canary_status)}</Badge>,
            <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(prompt.updated_at).toLocaleString("zh-CN")}</span>,
            <Button key="action" size="sm" variant="secondary" disabled={prompt.status !== "published" || !prompt.rollback_prompt_id || rollback.isPending} onClick={() => rollback.mutate(prompt.id)}>按指针回滚</Button>,
          ])}
        />
      </div>
    </>
  )
}

export function CapabilityStatusPage({ kind }: { kind: "benchmark" | "candidates" | "audit" }) {
  const bundles = useQuery({
    queryKey: ["strategy-bundles"],
    queryFn: () => api<{ items: StrategyBundleSummary[] }>("/api/strategy-bundles"),
  })
  const plans = useQuery({
    queryKey: ["agent-plans"],
    queryFn: () => api<{ items: Array<{ id: number; name: string; version: string; status: string; created_at: string }> }>("/api/agent-plans"),
  })
  const copy = {
    benchmark: {
      index: "05.1",
      title: "Sol / Terra / Luna 横评",
      description: "横评执行器尚未接通。本页只展示冻结契约，系统没有调用任何 Sol、Terra 或 Luna。",
    },
    candidates: {
      index: "05.3",
      title: "生产候选",
      description: "实验台尚未接入生产自动回流或自动实装消费者；现阶段只能保存候选证据，不能写生产数据库。",
    },
    audit: {
      index: "06.3",
      title: "系统审计",
      description: "展示受控 AgentPlan 与 StrategyBundle 证据；完整审计事件流尚未接通。",
    },
  }[kind]
  return (
    <>
      <PageHeader index={copy.index} title={copy.title} description={copy.description} />
      <div className="mx-auto grid max-w-[1540px] gap-6 px-5 py-8 md:px-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:px-10">
        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="border-b border-[var(--line)] px-5 py-4"><h2 className="font-editorial text-xl font-bold">当前可验证证据</h2></div>
          <div className="divide-y divide-[var(--line)]">
            {(bundles.data?.items ?? []).slice(0, 12).map((bundle) => (
              <div key={bundle.id} className="grid gap-2 px-5 py-4 md:grid-cols-[80px_1fr_auto] md:items-center">
                <span className="font-data text-xs">策略 #{bundle.id}</span>
                <div><p className="text-sm font-semibold">{bundle.model_id}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">A {bundle.prompt_a_version} · B {bundle.prompt_b_version ?? "—"} · {bundle.rubric_version} · {bundle.engine_version}</p></div>
                <Badge>{bundle.agent_plan_version}</Badge>
              </div>
            ))}
            {!bundles.isLoading && !bundles.data?.items.length && <EmptyLine text="还没有 StrategyBundle 证据" />}
          </div>
        </section>
        <aside className="border-y border-[var(--line-strong)] bg-[#fafbf8] p-5">
          <div className="flex items-center gap-2"><Prohibit className="text-[#8d2924]" /><h2 className="font-bold">执行状态：未接通</h2></div>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">不会伪造模型横评、自动回流、生产候选实装或完整审计。后续执行前仍需冻结同一批样本、Prompt、Rubric、Engine，并单独通过预算与发布门禁。</p>
          <div className="mt-5 border-t border-[var(--line)] pt-4">
            <p className="text-xs font-bold">受控编排版本</p>
            {(plans.data?.items ?? []).map((plan) => <p key={plan.id} className="font-data mt-2 text-xs">{plan.version} · {plan.status}</p>)}
          </div>
        </aside>
      </div>
    </>
  )
}

function DataTable({ loading, empty, headers, rows }: { loading: boolean; empty: string; headers: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white">
      {loading ? <div className="h-64 animate-pulse bg-white" /> : rows.length ? (
        <table className="w-full min-w-[920px] border-collapse text-left text-sm">
          <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8]">{headers.map((header) => <th key={header} className="px-4 py-3 text-xs font-semibold text-[var(--muted)]">{header}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex} className="border-b border-[var(--line)] last:border-0">{row.map((cell, cellIndex) => <td key={cellIndex} className="px-4 py-4">{cell}</td>)}</tr>)}</tbody>
        </table>
      ) : <EmptyLine text={empty} />}
    </div>
  )
}

function EmptyLine({ text }: { text: string }) {
  return <div className="flex min-h-56 flex-col items-center justify-center px-6 text-center"><Clock size={28} weight="light" /><p className="mt-3 text-sm text-[var(--muted)]">{text}</p></div>
}

function ToggleLine({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex items-center justify-between gap-4 border-b border-[var(--line)] pb-3 text-sm font-semibold"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="size-4 accent-[var(--primary)]" /></label>
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max?: number; onChange: (value: number) => void }) {
  return <label className="grid grid-cols-[1fr_132px] items-center gap-4 text-sm"><span>{label}</span><input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} className="h-9 rounded-[4px] border border-[var(--line-strong)] px-3 font-data" /></label>
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><p className="text-[var(--muted)]">{label}</p><p className="font-data mt-1 font-semibold">{value}</p></div>
}

function caseStatus(value: OptimizationCase["status"]) {
  return ({ pending: "待组批", batched: "已组批", processing: "处理中", completed: "已完成", failed: "失败" } as const)[value]
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

function executorError(value: string) {
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

function benchmarkStatus(value: ModelBenchmark["status"]) {
  return ({ draft: "已冻结", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消" } as const)[value]
}

function promptStatus(value: PromptVersion["status"]) {
  return ({ draft: "候选草稿", published: "已发布", archived: "已归档" } as const)[value]
}

function canaryStatus(value: PromptVersion["canary_status"]) {
  return ({ not_started: "未开始", planned: "已计划", running: "运行中", passed: "已通过", failed: "失败" } as const)[value ?? "not_started"]
}
