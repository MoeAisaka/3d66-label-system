import { useEffect, useMemo, useState } from "react"
import { ArrowClockwise, Check, CheckSquare, Play, Square } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ImagePreviewButton, type ImagePreview } from "@/components/image-lightbox"
import { baselineRegressionApi } from "@/lib/api"
import type { BaselineRegressionItem, BaselineRegressionRun } from "@/lib/types"
import { candidateRefreshPlan } from "@/features/correction-contract/candidate-refresh"
import { correctionLevelDisplay } from "@/features/baseline-regression/correction-level-display"
import { correctionStageClassName, correctionStageLabel, correctionStageRunningMessage, correctionStageState, correctionStageStateName, correctionStages, correctionStatusName, correctionStatusTone } from "@/features/baseline-regression/correction-stage-meta"
import { levelExplanationSummary } from "@/features/baseline-regression/level-explanation"
import { Metric, percent } from "@/features/baseline-regression/regression-page-shared"

export function CorrectionAnalysisPanel({
  run,
  items,
  loading,
  onPreview,
  canDecide,
}: {
  run: BaselineRegressionRun
  items: BaselineRegressionItem[]
  loading: boolean
  onPreview: (preview: ImagePreview) => void
  canDecide: boolean
}) {
  const queryClient = useQueryClient()
  const deviations = useMemo(
    () => items.filter((item) => item.status === "completed" && item.deviation),
    [items],
  )
  const deviationIds = useMemo(() => deviations.map((item) => item.id), [deviations])
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const correctionRuns = useQuery({
    queryKey: ["baseline-correction-runs", run.id],
    queryFn: () => baselineRegressionApi.listCorrectionRuns(run.id),
    refetchInterval: (query) => (
      query.state.data?.items.some((item) => item.status === "processing") ? 2500 : false
    ),
  })
  const latest = correctionRuns.data?.items[0]

  useEffect(() => {
    setSelectedIds(new Set(deviationIds))
  }, [run.id, deviationIds.join(",")])

  const createCorrection = useMutation({
    mutationFn: () => baselineRegressionApi.createCorrectionRun(
      run.id,
      Array.from(selectedIds),
      `baseline-correction-${run.id}-${Date.now()}`,
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["baseline-correction-runs", run.id] })
      toast.success("纠偏分析已启动；AI 将自动生成候选并执行回归")
    },
    onError: (error) => toast.error(error.message),
  })
  const retryCorrection = useMutation({
    mutationFn: (correctionRunId: number) => baselineRegressionApi.retryCorrectionRun(correctionRunId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["baseline-correction-runs", run.id] })
      toast.success("已重新启动纠偏分析")
    },
    onError: (error) => toast.error(error.message),
  })
  const decideCorrection = useMutation({
    mutationFn: ({
      correctionRunId,
      decision,
      note,
    }: {
      correctionRunId: number
      decision: "approved" | "rejected"
      note: string
    }) => baselineRegressionApi.decideCorrectionRun(correctionRunId, decision, note),
    onSuccess: async (result) => {
      const refreshPlan = result.mechanism_refresh
        ? candidateRefreshPlan(result.mechanism_refresh, run.id)
        : null
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-correction-runs", run.id] }),
        ...(refreshPlan?.invalidate ?? [
          ["evaluation-categories"],
          ["prompts", run.category_key],
          ["baseline-v3-revisions", run.category_key],
        ]).map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ])
      toast.success(result.status === "approved" ? "候选已启用" : "候选已拒绝")
    },
    onError: (error) => toast.error(error.message),
  })

  const allSelected = deviations.length > 0 && selectedIds.size === deviations.length
  const report = latest?.report ?? {}
  const promptSuggestions = recordArray(report.prompt_suggestions)
  const dimensionSuggestions = recordArray(report.dimension_suggestions)
  const risks = stringArray(report.risks)
  const candidateRegression = recordValue(report.candidate_regression)
  const baselineMetrics = recordValue(candidateRegression.baseline_metrics)
  const candidateMetrics = recordValue(candidateRegression.candidate_metrics)
  const regressions = recordArray(candidateRegression.regressions)
  const approvalAllowed = candidateRegression.approval_allowed === true
  const recommendation = stringValue(candidateRegression.recommendation)
  const blockers = (latest?.blockers ?? []).map((blocker) => (
    typeof blocker === "string" ? blocker : readableRecord(blocker)
  )).filter(Boolean)
  const latestLocked = latest?.status === "processing" || latest?.status === "awaiting_decision"

  const requestDecision = (decision: "approved" | "rejected") => {
    if (!latest || latest.status !== "awaiting_decision") return
    const approved = decision === "approved"
    const confirmed = window.confirm(
      approved
        ? "确认启用该等级规则候选？启用后会切换当前类目的现役提示词与等级规则版本，但不会发布标签事实。"
        : "确认拒绝该机制候选？本次候选与回归证据会保留，结论提交后不可修改。",
    )
    if (!confirmed) return
    decideCorrection.mutate({
      correctionRunId: latest.id,
      decision,
      note: approved ? "人工确认启用自动纠偏候选" : "人工确认拒绝自动纠偏候选",
    })
  }

  return (
    <section
      id="baseline-correction-panel"
      className="mt-6 border-y border-[var(--line-strong)] bg-white"
      role="tabpanel"
      aria-labelledby="baseline-correction-tab"
    >
      <div className="grid gap-5 border-b border-[var(--line)] px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-editorial text-2xl font-bold">基准回归处理纠偏</h3>
            <Badge tone="active">全自动候选流水线</Badge>
            <Badge tone="neutral">最终人工决策</Badge>
          </div>
          <p className="mt-2 max-w-3xl text-xs leading-5 text-[var(--muted)]">
            启动后，系统自动分析纠偏样本、生成并校验统一机制候选，再执行候选回归。中间无需人工配置；回归完成后只需决定启用或拒绝，系统不会自动启用候选。
          </p>
          <div className="mt-3 grid gap-2 text-xs leading-5 text-[var(--muted)] sm:grid-cols-2">
            <p className="border-l-2 border-[var(--line-strong)] pl-3">
              结果查看位置：存量回归 → 基准回归 → 处理纠偏（当前区域）。分析报告、候选机制、回归指标和风险提示都在这里展示。
            </p>
            <p className="border-l-2 border-primary pl-3">
              人工采纳位置：候选回归完成后仍在当前区域进入“等待人工决策”；只有系统管理员在这里点击“启用候选”或“拒绝候选”。
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            onClick={() => correctionRuns.refetch()}
            disabled={correctionRuns.isFetching}
          >
            <ArrowClockwise />刷新状态
          </Button>
          <Button
            onClick={() => createCorrection.mutate()}
            disabled={
              run.status === "running"
              || !selectedIds.size
              || createCorrection.isPending
              || latestLocked
            }
          >
            <Play weight="fill" />
            {createCorrection.isPending ? "正在启动" : `启动纠偏分析 (${selectedIds.size})`}
          </Button>
        </div>
      </div>

      <div className="grid min-w-0 lg:grid-cols-[minmax(320px,0.88fr)_minmax(0,1.12fr)]">
        <div className="min-w-0 border-b border-[var(--line)] lg:border-r lg:border-b-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-4 py-3">
            <div>
              <p className="text-sm font-bold">选择偏差样本</p>
              <p className="mt-1 text-xs text-[var(--muted)]">已选 {selectedIds.size} / {deviations.length}</p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              disabled={!deviations.length || latestLocked}
              onClick={() => setSelectedIds(allSelected ? new Set() : new Set(deviationIds))}
            >
              {allSelected ? <CheckSquare weight="fill" /> : <Square />}
              {allSelected ? "取消全选" : "全选偏差"}
            </Button>
          </div>
          <div className="max-h-[620px] overflow-auto">
            {loading ? (
              <div className="h-64 animate-pulse bg-white" />
            ) : deviations.length ? (
              <div className="divide-y divide-[var(--line)]">
                {deviations.map((item) => {
                  const levelDisplay = correctionLevelDisplay(item)
                  return (
                  <div key={item.id} className="grid grid-cols-[auto_52px_minmax(0,1fr)] gap-3 px-4 py-3 hover:bg-[#fafbf8]">
                    <input
                      type="checkbox"
                      aria-label={`选择偏差样本：${item.asset.name}`}
                      className="mt-4 size-4 accent-[#9dbb1c]"
                      checked={selectedIds.has(item.id)}
                      disabled={latestLocked}
                      onChange={(event) => setSelectedIds((current) => {
                        const next = new Set(current)
                        if (event.target.checked) next.add(item.id)
                        else next.delete(item.id)
                        return next
                      })}
                    />
                    <ImagePreviewButton
                      src={item.image_url}
                      alt={item.asset.name}
                      imageClassName="size-12"
                      onPreview={onPreview}
                    />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="file-name min-w-0 truncate text-sm">{item.asset.name}</p>
                        <Badge tone="danger">{levelDisplay.level} → {item.predicted_level ?? "—"}</Badge>
                        <Badge tone={levelDisplay.source === "human_correction" ? "warning" : "neutral"}>
                          {levelDisplay.source === "human_correction" ? "人工纠偏等级" : "冻结预期等级"}
                        </Badge>
                      </div>
                      {levelDisplay.source === "human_correction" && (
                        <p className="mt-1 text-[0.68rem] leading-4 text-[#7d4308]">
                          原冻结预期 {item.expected_level} · 当前展示以已完成人工纠偏为准
                        </p>
                      )}
                      {levelDisplay.source === "frozen_expected" && (
                        <p className="mt-1 text-[0.68rem] leading-4 text-[var(--muted)]">
                          尚未保存人工纠偏等级 · 当前仅用于筛选偏差样本
                        </p>
                      )}
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{levelExplanationSummary(item)}</p>
                    </div>
                  </div>
                  )
                })}
              </div>
            ) : (
              <p className="px-5 py-12 text-center text-sm text-[var(--muted)]">
                当前运行没有可分析的已完成偏差样本。
              </p>
            )}
          </div>
        </div>

        <div className="min-w-0 px-5 py-5">
          {!latest ? (
            <div className="border-y border-[var(--line)] px-4 py-12 text-center">
              <p className="text-sm font-bold">尚未启动纠偏分析</p>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">选择左侧偏差样本后启动，AI 将自动接管候选生成与回归，直到需要最终人工决策。</p>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line-strong)] pb-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="text-base font-bold">纠偏分析 #{latest.id}</h4>
                    <Badge tone={correctionStatusTone(latest)}>{correctionStatusName(latest)}</Badge>
                    <Badge>第 {latest.attempt_count} 次尝试</Badge>
                  </div>
                  <p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">
                    {latest.selected_item_ids.length} 个冻结样本 · {latest.updated_at}
                  </p>
                </div>
                {latest.status === "failed" && latest.error?.retryable && (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={retryCorrection.isPending}
                    onClick={() => retryCorrection.mutate(latest.id)}
                  >
                    <ArrowClockwise />{retryCorrection.isPending ? "正在重新执行" : "重新执行"}
                  </Button>
                )}
              </div>

              <div className="mt-4 grid grid-cols-2 border-y border-[var(--line)] bg-[#fafbf8] 2xl:grid-cols-5">
                {correctionStages.map((stage, index) => {
                  const state = correctionStageState(latest, stage.key)
                  return (
                    <div
                      key={stage.key}
                      className="min-w-0 border-r border-[var(--line)] px-3 py-3 last:border-r-0"
                    >
                      <div className="flex items-center gap-2">
                        <span className={`flex size-6 shrink-0 items-center justify-center rounded-full border text-[0.68rem] font-bold ${correctionStageClassName(state)}`}>
                          {state === "completed" ? <Check weight="bold" /> : index + 1}
                        </span>
                        <p className="text-xs font-bold leading-4">{stage.label}</p>
                      </div>
                      <p className="mt-1 pl-8 text-[0.68rem] leading-4 text-[var(--muted)]">
                        {correctionStageStateName(state)}
                      </p>
                    </div>
                  )
                })}
              </div>

              {latest.status === "processing" && (
                <div className="mt-4 border border-[#d6dfb1] bg-[#f7fadf] px-4 py-3">
                  <div className="flex justify-between gap-4 text-xs font-bold">
                    <span>{correctionStageRunningMessage(latest.stage)}</span>
                    <span className="font-data">{latest.progress}%</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden bg-white">
                    <div className="h-full bg-primary transition-[width] duration-300" style={{ width: `${latest.progress}%` }} />
                  </div>
                </div>
              )}

              {latest.status === "failed" && (
                <div className="mt-4 border border-[#e2b4af] bg-[#fff5f3] px-4 py-3 text-xs leading-5 text-[#8d2924]">
                  <p className="font-bold">{correctionStageLabel(latest.stage)}失败{latest.error?.code ? ` · ${latest.error.code}` : ""}</p>
                  <p className="mt-1">{latest.error?.message || "未返回具体失败原因，可重新执行本次冻结样本。"}</p>
                  <p className="mt-1 text-[#6f3935]">重新执行会沿用本次冻结样本，不需要补充任何配置。</p>
                </div>
              )}

              {(blockers.length > 0) && (
                <div className="mt-4 border border-[#e2c188] bg-[#fff9ea] px-4 py-3 text-xs leading-5 text-[#7d4308]">
                  <p className="font-bold">自动处理提示</p>
                  {blockers.map((blocker, index) => <p key={`${blocker}-${index}`} className="mt-1">{blocker}</p>)}
                </div>
              )}

              {(latest.status === "awaiting_decision"
                || latest.status === "approved"
                || latest.status === "rejected") && (
                <>
                  <div className="mt-5 grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
                    <Metric label="基准准确率" value={percent(numberValue(baselineMetrics.exact_accuracy) ?? run.metrics.exact_accuracy)} />
                    <Metric label="候选准确率" value={formatPercent(numberValue(candidateMetrics.exact_accuracy))} />
                    <Metric label="准确率变化" value={formatSignedPercent(numberValue(candidateRegression.exact_accuracy_delta))} />
                    <Metric label="相邻准确率变化" value={formatSignedPercent(numberValue(candidateRegression.adjacent_accuracy_delta))} />
                  </div>
                  <div className="mt-5 grid grid-cols-4 gap-px border-y border-[var(--line)] bg-[var(--line)]">
                    <ReportFact label="机制候选" value={latest.candidate_revision_id ? `Revision #${latest.candidate_revision_id}` : "—"} />
                    <ReportFact label="候选提示词" value={latest.orchestration.candidate_prompt?.version ?? "—"} />
                    <ReportFact label="候选回归" value={latest.regression_run_id ? `Run #${latest.regression_run_id}` : "—"} />
                    <ReportFact label="回归建议" value={recommendation === "approve" ? "建议启用" : "建议拒绝"} />
                  </div>
                  <div className="mt-6">
                    <h5 className="font-editorial text-xl font-bold">AI 分析与候选变更摘要</h5>
                    <p className="mt-1 text-xs text-[var(--muted)]">系统已把以下分析自动落入统一机制候选，并使用同一基准集完成验证。</p>
                    <div className="mt-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
                      {[...promptSuggestions, ...dimensionSuggestions].map((suggestion, index) => (
                        <div key={`${stringValue(suggestion.code) || stringValue(suggestion.dimension_key) || "suggestion"}-${index}`} className="grid gap-2 py-3 text-xs sm:grid-cols-[120px_minmax(0,1fr)]">
                          <span className="font-bold">
                            {stringValue(suggestion.dimension_key) || "提示词建议"}
                            {stringValue(suggestion.priority) ? ` · ${priorityName(stringValue(suggestion.priority))}` : ""}
                          </span>
                          <span className="leading-5 text-[var(--muted)]">{stringValue(suggestion.message) || readableRecord(suggestion)}</span>
                        </div>
                      ))}
                      {!promptSuggestions.length && !dimensionSuggestions.length && (
                        <p className="py-5 text-center text-xs text-[var(--muted)]">本次未形成可展示的改进建议。</p>
                      )}
                    </div>
                  </div>
                  {risks.length > 0 && (
                    <div className="mt-5 border border-[#e2c188] bg-[#fff9ea] px-4 py-3 text-xs leading-5 text-[#7d4308]">
                      <p className="font-bold">报告风险提示</p>
                      {risks.map((risk) => <p key={risk} className="mt-1">{risk}</p>)}
                    </div>
                  )}
                  {regressions.length > 0 && (
                    <div className="mt-5 border border-[#e2b4af] bg-[#fff5f3] px-4 py-3 text-xs leading-5 text-[#8d2924]">
                      <p className="font-bold">候选回归未通过</p>
                      {regressions.map((regression, index) => (
                        <p key={`${stringValue(regression.code)}-${index}`} className="mt-1">
                          {stringValue(regression.message) || readableRecord(regression)}
                        </p>
                      ))}
                    </div>
                  )}
                  {latest.status === "awaiting_decision" && canDecide && (
                    <div className="mt-5 border-l-2 border-primary bg-[#f8faed] px-4 py-4">
                      <div className="flex items-center justify-between gap-5">
                        <div>
                          <p className="text-sm font-bold">等待人工决策</p>
                          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                            中间步骤已全部完成。启用只切换机制发布轴；标签事实仍需通过独立发布流程。
                          </p>
                        </div>
                        <div className="flex shrink-0 gap-2">
                          <Button
                            variant="danger"
                            disabled={decideCorrection.isPending}
                            onClick={() => requestDecision("rejected")}
                          >
                            拒绝候选
                          </Button>
                          <Button
                            disabled={!approvalAllowed || decideCorrection.isPending}
                            title={approvalAllowed ? undefined : "候选回归未通过，不能启用"}
                            onClick={() => requestDecision("approved")}
                          >
                            <Check weight="bold" />启用候选
                          </Button>
                        </div>
                      </div>
                    </div>
                  )}
                  {latest.status === "awaiting_decision" && !canDecide && (
                    <div className="mt-5 border-l-2 border-[var(--line-strong)] bg-[#fafbf8] px-4 py-4">
                      <p className="text-sm font-bold">等待系统管理员决策</p>
                      <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                        候选与回归证据已就绪。只有系统管理员可以启用或拒绝机制候选。
                      </p>
                    </div>
                  )}
                  {(latest.status === "approved" || latest.status === "rejected") && (
                    <div className={`mt-5 border-l-2 px-4 py-4 ${latest.status === "approved" ? "border-primary bg-[#f8faed]" : "border-[#b7362e] bg-[#fff5f3]"}`}>
                      <p className="text-sm font-bold">
                        人工结论：{latest.status === "approved" ? "已启用候选" : "已拒绝候选"}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                        {latest.decided_by || "管理员"} · {latest.decided_at || latest.updated_at}
                        {latest.decision_note ? ` · ${latest.decision_note}` : ""}
                      </p>
                      <p className="mt-1 text-xs text-[var(--muted)]">该人工结论不可修改，候选、回归与决策证据均已保留。</p>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  )
}

export function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export function recordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(recordValue).filter((item) => Object.keys(item).length) : []
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []
}

export function stringValue(value: unknown): string {
  return typeof value === "string" ? value : ""
}

export function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function readableRecord(value: unknown): string {
  const record = recordValue(value)
  return stringValue(record.message) || stringValue(record.title) || stringValue(record.code)
}

export function formatSignedPercent(value: number | null) {
  if (value === null) return "—"
  const sign = value > 0 ? "+" : ""
  return `${sign}${(value * 100).toFixed(1)}%`
}

export function formatPercent(value: number | null) {
  return value === null ? "—" : percent(value)
}

export function priorityName(priority: string) {
  if (priority === "high") return "高优先"
  if (priority === "medium") return "中优先"
  if (priority === "low") return "低优先"
  return priority
}

export function ReportFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-y border-[var(--line)] px-4 py-3">
      <p className="text-xs text-[var(--muted)]">{label}</p>
      <p className="font-data mt-1 text-lg font-bold">{value}</p>
    </div>
  )
}
