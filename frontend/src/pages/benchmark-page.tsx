import { Play, Prohibit, ShieldWarning } from "@phosphor-icons/react"
import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { ModelBenchmark, ModelConfig, StrategyBundleSummary, User } from "@/lib/types"
import { EmptyLine, Metric, executorError, percent } from "@/pages/workflow-shared"

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
      <div className="mx-auto shell-content space-y-6 px-5 py-8 md:px-8 lg:px-10">
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

function benchmarkStatus(value: ModelBenchmark["status"]) {
  return ({ draft: "已冻结", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消" } as const)[value]
}
