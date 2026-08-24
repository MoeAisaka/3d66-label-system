import { useEffect, useMemo, useState } from "react"
import { Check, CheckSquare, PencilSimple, Square, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ImagePreviewButton, type ImagePreview } from "@/components/image-lightbox"
import { api, baselineRegressionApi } from "@/lib/api"
import { submitReviewDecision } from "@/lib/review-submit"
import type { BaselineFieldMetrics, BaselineRegressionItem, BaselineRegressionRun, ReviewCorrection, User } from "@/lib/types"
import { ReviewCorrectionForm } from "@/pages/review-correction-form"
import { CorrectionWorkbench } from "@/features/baseline-regression/correction-workbench"
import { correctionDraftFromView, correctionSubmissionPayload, mergeCorrectionResponse, updateCorrectionDraft } from "@/features/correction-contract/correction-view-state"
import type { CorrectionDraft, CorrectionView } from "@/features/correction-contract/types"
import { nextPendingCorrectionId, previousCorrectionId } from "@/features/baseline-regression/correction-navigation"
import { LevelPerformanceSummary } from "@/features/baseline-regression/level-performance-summary"
import { CorrectionAnalysisPanel } from "@/features/baseline-regression/correction-analysis-panel"
import { reviewStatus } from "@/features/baseline-regression/correction-stage-meta"
import { LevelExplanation, levelExplanationSummary } from "@/features/baseline-regression/level-explanation"
import { Metric, percent } from "@/features/baseline-regression/regression-page-shared"

export function correctionIdempotencyKey(runId: number, itemId: number): string {
  const random = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `baseline-contract:${runId}:${itemId}:${random}`
}

export function RegressionResults({
  run,
  items,
  pagination,
  page,
  onPageChange,
  loading,
  fieldMetrics,
  onPreview,
  onOpenMetrics,
  correctionItemId,
  onOpenCorrection,
  onCloseCorrection,
}: {
  run: BaselineRegressionRun
  items: BaselineRegressionItem[]
  pagination: { offset: number; limit: number; total: number }
  page: number
  onPageChange: (page: number) => void
  loading: boolean
  fieldMetrics?: BaselineFieldMetrics
  onPreview: (preview: ImagePreview) => void
  onOpenMetrics: () => void
  correctionItemId: number
  onOpenCorrection: (itemId: number) => void
  onCloseCorrection: () => void
}) {
  const queryClient = useQueryClient()
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/auth/me"),
  })
  const availableDeviationIds = useMemo(
    () => items
      .filter((item) => (
        item.status === "completed"
        && item.deviation
        && item.optimization_case_id === null
      ))
      .map((item) => item.id),
    [items],
  )
  const [selectedDeviationIds, setSelectedDeviationIds] = useState<Set<number>>(
    new Set(),
  )
  const [activeView, setActiveView] = useState<"results" | "correction">("results")
  const [reviewNotes, setReviewNotes] = useState<Record<number, string>>({})
  const [reopenSeeds, setReopenSeeds] = useState<Record<number, {
    corrections: ReviewCorrection[]
    note: string
  }>>({})

  useEffect(() => {
    setSelectedDeviationIds(new Set(availableDeviationIds))
  }, [run.id, availableDeviationIds.join(",")])

  const enqueueDeviations = useMutation({
    mutationFn: () => baselineRegressionApi.enqueueDeviations(
      run.id,
      Array.from(selectedDeviationIds),
    ),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: ["baseline-regression", run.id],
      })
      queryClient.invalidateQueries({ queryKey: ["optimization-cases"] })
      setSelectedDeviationIds(new Set())
      toast.success(
        result.created
          ? `已将 ${result.created} 张偏差样本加入全局优化池`
          : "所选偏差样本已在全局优化池中",
      )
    },
    onError: (error) => toast.error(error.message),
  })
  const reviewResult = useMutation({
    mutationFn: ({
      item,
      decision,
      note,
      corrections = [],
    }: {
      item: BaselineRegressionItem
      decision: "approved" | "corrected" | "rejected"
      note: string
      corrections?: ReviewCorrection[]
    }) => {
      if (!item.evaluation) throw new Error("该回归结果没有可审核的评测记录")
      if (!me.data) throw new Error("当前登录账号尚未加载")
      return submitReviewDecision({
        evaluation: item.evaluation,
        reviewer: me.data.username,
        decision,
        note,
        corrections,
      })
    },
    onSuccess: async (_result, variables) => {
      await queryClient.invalidateQueries({
        queryKey: ["baseline-regression", run.id],
      })
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-acceptance", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["optimization-cases"] }),
      ])
      setReviewNotes((current) => ({ ...current, [variables.item.id]: "" }))
      toast.success(
        variables.decision === "corrected"
          ? "人工纠偏与最终等级已保存"
          : variables.decision === "approved"
            ? "已确认模型结果"
            : "已退回复核",
      )
    },
    onError: (error) => toast.error(error.message),
  })
  const reopenReview = useMutation({
    mutationFn: ({ item }: { item: BaselineRegressionItem }) => {
      if (!item.evaluation) throw new Error("该回归结果没有可重开的评测记录")
      const corrections = item.evaluation.human_review?.corrections ?? []
      setReopenSeeds((current) => ({
        ...current,
        [item.id]: {
          corrections,
          note: item.evaluation?.human_review?.note ?? "",
        },
      }))
      return baselineRegressionApi.reopenReview(
        item.evaluation.id,
        item.evaluation.review_revision,
      )
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["baseline-correction-view", run.id, correctionItemId] }),
      ])
      toast.success("已创建新的人工审核轮次，可继续修改")
    },
    onError: (error) => toast.error(error.message),
  })

  const metrics = run.metrics
  const pageCount = Math.max(1, Math.ceil(pagination.total / pagination.limit))
  const correctionItem = items.find((item) => item.id === correctionItemId)
  const correctionViewQuery = useQuery<CorrectionView>({
    queryKey: ["baseline-correction-view", run.id, correctionItemId],
    queryFn: () => baselineRegressionApi.getCorrectionView(run.id, correctionItemId),
    enabled: correctionItemId > 0 && Boolean(correctionItem),
  })
  const [correctionDraft, setCorrectionDraft] = useState<CorrectionDraft | null>(null)
  const [correctionDraftKey, setCorrectionDraftKey] = useState("")
  const correctionViewKey = correctionViewQuery.data
    ? `${correctionViewQuery.data.item_id}:${correctionViewQuery.data.contract?.contract_hash ?? "legacy"}:${correctionViewQuery.data.review_revision}`
    : ""

  useEffect(() => {
    if (!correctionViewQuery.data || !correctionViewKey) return
    if (correctionDraftKey === correctionViewKey) return
    setCorrectionDraft(correctionDraftFromView(correctionViewQuery.data))
    setCorrectionDraftKey(correctionViewKey)
  }, [correctionDraftKey, correctionViewKey, correctionViewQuery.data])

  useEffect(() => {
    if (correctionItemId > 0) return
    setCorrectionDraft(null)
    setCorrectionDraftKey("")
  }, [correctionItemId])

  const submitContractCorrection = useMutation({
    mutationFn: async () => {
      const view = correctionViewQuery.data
      if (!view || !correctionDraft) throw new Error("合同纠偏面板尚未加载")
      const payload = correctionSubmissionPayload(
        correctionDraft,
        view,
        correctionIdempotencyKey(run.id, correctionItemId),
      )
      if (!payload.nodes.length) throw new Error("请先修改至少一个纠偏节点")
      return baselineRegressionApi.submitCorrectionNodes(run.id, correctionItemId, payload)
    },
    onSuccess: async (response) => {
      setCorrectionDraft(mergeCorrectionResponse(correctionDraft ?? {}, response))
      setCorrectionDraftKey(`${response.item_id}:${response.contract?.contract_hash ?? "legacy"}:${response.review_revision}`)
      await queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] })
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["baseline-acceptance", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      toast.success("合同纠偏已保存，当前素材保持不变")
    },
    onError: (error) => toast.error(error.message),
  })

  if (correctionItemId > 0 && correctionItem) {
    return (
      <CorrectionWorkbench
        item={correctionItem}
        onBack={onCloseCorrection}
        corrector={me.data?.username ?? ""}
        onPrevious={() => {
          const previousId = previousCorrectionId(items, correctionItem.id)
          if (previousId) onOpenCorrection(previousId)
        }}
        hasPrevious={Boolean(previousCorrectionId(items, correctionItem.id))}
        onNext={() => {
          const nextId = nextPendingCorrectionId(items, correctionItem.id)
          if (nextId) onOpenCorrection(nextId)
        }}
        hasNext={Boolean(nextPendingCorrectionId(items, correctionItem.id))}
        correctionView={correctionViewQuery.data ?? null}
        correctionDraft={correctionDraft ?? undefined}
        onCorrectionChange={(nodeKey, patch) => {
          setCorrectionDraft((current) => current ? updateCorrectionDraft(current, nodeKey, patch) : current)
        }}
        onCorrectionSubmit={() => submitContractCorrection.mutate()}
        correctionSubmitPending={submitContractCorrection.isPending}
        correctionSubmitDisabled={correctionViewQuery.isLoading || correctionViewQuery.isError}
        correctionDisabled={correctionItem.evaluation?.review_stage === "completed"}
        onCorrected={async () => {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] }),
            queryClient.invalidateQueries({ queryKey: ["baseline-acceptance", run.id] }),
            queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
            queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
          ])
        }}
        onPreview={onPreview}
      >
        <div className="grid grid-cols-1 gap-4">
          <LevelExplanation item={correctionItem} />
          <div className="space-y-3">
            <p className="text-sm font-semibold">人工决策</p>
            <p className="text-xs leading-5 text-[var(--muted)]">提交后会停留在当前素材；可使用页面右上角的“上一条”和“下一条”手动切换。</p>
            {correctionItem.evaluation && (
              <div className="space-y-4 border-t border-[var(--line)] pt-4">
                <label>
                  <span className="mb-2 block text-xs font-semibold">人工说明（可选）</span>
                  <Input
                    value={reviewNotes[correctionItem.id] ?? ""}
                    disabled={correctionItem.evaluation.review_stage === "completed"}
                    placeholder="补充确认或退回依据"
                    onChange={(event) => setReviewNotes((current) => ({
                      ...current,
                      [correctionItem.id]: event.target.value,
                    }))}
                  />
                </label>
                {correctionItem.evaluation.review_stage === "completed" ? (
                  <div className="space-y-3 rounded-[4px] border border-[#ead7a5] bg-[#fff9ea] px-4 py-3">
                    <p className="text-sm font-semibold">人工结果已保存</p>
                    <p className="text-xs leading-5 text-[#6b4b0b]">
                      当前轮次已完成，{correctionItem.evaluation.human_review?.corrections?.length ?? 0} 处纠偏记录可回看。点击“再次修改”会保留本轮历史并创建新审核轮次。
                    </p>
                    {correctionItem.evaluation.human_review?.note && (
                      <p className="text-xs leading-5 text-[var(--muted)]">说明：{correctionItem.evaluation.human_review.note}</p>
                    )}
                    <Button
                      variant="secondary"
                      disabled={reopenReview.isPending}
                      onClick={() => reopenReview.mutate({ item: correctionItem })}
                    >
                      <PencilSimple />{reopenReview.isPending ? "正在创建新轮次" : "再次修改"}
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      disabled={reviewResult.isPending}
                      onClick={() => reviewResult.mutate({
                        item: correctionItem,
                        decision: "rejected",
                        note: reviewNotes[correctionItem.id]?.trim() ?? "",
                      })}
                    >
                      退回复核
                    </Button>
                    <Button
                      disabled={reviewResult.isPending}
                      onClick={() => reviewResult.mutate({
                        item: correctionItem,
                        decision: "approved",
                        note: reviewNotes[correctionItem.id]?.trim() ?? "",
                      })}
                    >
                      <Check weight="bold" />确认结果
                    </Button>
                  </div>
                )}
                {!correctionViewQuery.data && correctionItem.evaluation.scoring?.dimension_scoring_mode !== "rule_deduction" && (
                  <ReviewCorrectionForm
                    key={`${correctionItem.evaluation.id}-${correctionItem.evaluation.review_revision}`}
                    dimensions={correctionItem.evaluation.aesthetic?.dimensions ?? {}}
                    precheck={correctionItem.evaluation.precheck ?? {}}
                    dimensionSchema={correctionItem.evaluation.dimension_schema}
                    scoring={correctionItem.evaluation.scoring ?? {}}
                    pending={reviewResult.isPending}
                    editable={correctionItem.evaluation.review_stage !== "completed"}
                    initialCorrections={reopenSeeds[correctionItem.id]?.corrections ?? correctionItem.evaluation.human_review?.corrections ?? []}
                    initialNote={reopenSeeds[correctionItem.id]?.note ?? correctionItem.evaluation.human_review?.note ?? ""}
                    onSubmit={({ note, corrections }) => reviewResult.mutate({
                      item: correctionItem,
                      decision: "corrected",
                      note,
                      corrections,
                    })}
                  />
                )}
              </div>
            )}
          </div>
        </div>
      </CorrectionWorkbench>
    )
  }
  return (
    <>
      <section className="mt-6 grid gap-px border-y border-[var(--line)] bg-[var(--line)] md:grid-cols-2 xl:grid-cols-4">
        <SelectionFact
          label="本轮调用 A"
          value={run.selection.prompt_a
            ? `${run.selection.prompt_a.version} · ${run.selection.prompt_a.name}`
            : "历史 run 未记录"}
        />
        <SelectionFact
          label="本轮调用 B"
          value={run.selection.prompt_b
            ? `${run.selection.prompt_b.version} · ${run.selection.prompt_b.name}`
            : run.selection.prompt_a
              ? "单提示词模式（B 位不调用）"
              : "历史 run 未记录"}
        />
        <SelectionFact
          label="本轮维度版本"
          value={dimensionSelectionName(run.selection.dimension)}
        />
        <SelectionFact
          label="结果判定方式"
          value={run.selection.execution_mode === "structured" ? "标准评分合同" : "自由实验 · 无结构也可完成"}
        />
      </section>
      <div className="mt-4 flex justify-end">
        <Button variant="secondary" size="sm" onClick={onOpenMetrics}>查看字段证据</Button>
      </div>
      <div
        className="mt-6 flex gap-0 overflow-x-auto border-b border-[var(--line-strong)]"
        role="tablist"
        aria-label="基准回归工作区"
      >
        <button
          id="baseline-results-tab"
          type="button"
          role="tab"
          aria-controls="baseline-results-panel"
          aria-selected={activeView === "results"}
          className={`min-h-11 shrink-0 border-x border-t px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${activeView === "results" ? "border-[var(--line-strong)] bg-white" : "border-transparent bg-[#f3f5f0] text-[var(--muted)]"}`}
          onClick={() => setActiveView("results")}
        >
          回归结果
        </button>
        <button
          id="baseline-correction-tab"
          type="button"
          role="tab"
          aria-controls="baseline-correction-panel"
          aria-selected={activeView === "correction"}
          className={`min-h-11 shrink-0 border-x border-t px-4 text-sm font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${activeView === "correction" ? "border-[var(--line-strong)] bg-white" : "border-transparent bg-[#f3f5f0] text-[var(--muted)]"}`}
          onClick={() => setActiveView("correction")}
        >
          基准回归处理纠偏 · {availableDeviationIds.length}
        </button>
      </div>

      {activeView === "results" ? (
        <div id="baseline-results-panel" role="tabpanel" aria-labelledby="baseline-results-tab">
      <LevelPerformanceSummary metrics={metrics} />
      <section className="mt-6 grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="字段宏平均准确率" value={fieldMetrics ? percent(fieldMetrics.aggregates.macro.accuracy) : percent(metrics.exact_accuracy)} />
        <Metric label="字段宏平均召回率" value={fieldMetrics ? percent(fieldMetrics.aggregates.macro.recall) : "—"} />
        <Metric label="人工门禁状态" value={run.status === "running" ? "等待运行完成" : metrics.failed ? "先处理失败" : "等待人工确认"} />
        <Metric label="下一步" value={availableDeviationIds.length ? `纠偏 ${availableDeviationIds.length} 条` : "查看证据并决定"} />
      </section>

      {run.status === "running" && (
        <div className="mt-4 border border-[#d6dfb1] bg-[#f7fadf] px-4 py-3">
          <div className="flex items-center justify-between gap-4 text-xs font-semibold">
            <span>回归运行中，页面每 3 秒自动刷新</span>
            <span className="font-data">{run.completed}/{run.total}</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden bg-white">
            <div
              className="h-full bg-primary transition-[width] duration-300"
              style={{ width: `${run.total ? Math.round(run.completed / run.total * 100) : 0}%` }}
            />
          </div>
        </div>
      )}

      <section className="mt-7">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3 className="font-editorial text-2xl font-bold">逐张预测对照</h3>
              <p className="mt-1 text-xs text-[var(--muted)]">每张展示冻结评测理由，并可原位确认、纠偏或退回。</p>
              <p className="mt-2 max-w-3xl text-xs leading-5 text-[var(--muted)]">
                全局优化案例池用途（可选）：把偏差样本沉淀到后续自动组批和长期机制优化流程；不影响当前纠偏分析，不修改本轮真值，也不自动启用候选。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge>第 {page + 1}/{pageCount} 页 · 共 {pagination.total} 张</Badge>
              <Button
                variant="ghost"
                size="sm"
                disabled={page <= 0 || loading}
                onClick={() => onPageChange(Math.max(0, page - 1))}
              >
                上一页
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={page + 1 >= pageCount || loading}
                onClick={() => onPageChange(page + 1)}
              >
                下一页
              </Button>
              {availableDeviationIds.length > 0 && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setSelectedDeviationIds(
                        selectedDeviationIds.size === availableDeviationIds.length
                          ? new Set()
                          : new Set(availableDeviationIds),
                      )
                    }}
                  >
                    {selectedDeviationIds.size === availableDeviationIds.length
                      ? "取消本页偏差全选"
                      : "选择本页全部偏差"}
                  </Button>
                  <Button
                    size="sm"
                    disabled={!selectedDeviationIds.size || enqueueDeviations.isPending}
                    onClick={() => enqueueDeviations.mutate()}
                  >
                    {enqueueDeviations.isPending
                      ? "正在加入全局优化池"
                      : `加入全局优化池（可选） · ${selectedDeviationIds.size}`}
                  </Button>
                </>
              )}
            </div>
          </div>
          <div className="max-h-[620px] overflow-auto border-y border-[var(--line-strong)] bg-white">
            {loading ? (
              <div className="h-64 animate-pulse bg-white" />
            ) : items.length ? (
              <div className="divide-y divide-[var(--line)]">
                {items.map((item) => {
                  const fallback = gradedByFallback(item.stage_a)
                  const evaluation = item.evaluation
                  const humanStatus = reviewStatus(evaluation)
                  return (
                    <div
                      key={item.id}
                      className="bg-white"
                    >
                      <div className="grid gap-3 px-4 py-4 sm:grid-cols-[64px_minmax(0,1fr)_auto] sm:items-center">
                        <ImagePreviewButton
                          src={item.image_url}
                          alt={item.asset.name}
                          imageClassName="size-14"
                          onPreview={onPreview}
                        />
                        <div className="min-w-0">
                          <p className="file-name truncate text-sm">{item.asset.name}</p>
                          <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">
                            素材 #{item.asset_id} · 评测 #{item.evaluation_id ?? "—"} · 分数 {item.authoritative_score ?? "—"}
                          </p>
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-[var(--muted)]">
                            {levelExplanationSummary(item)}
                          </p>
                          {item.error_message && (
                            <p className="mt-1 text-xs text-[#8d2924]">失败原因：{baselineErrorMessage(item.error_message)}</p>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-2 sm:max-w-72 sm:justify-end">
                          {fallback && <Badge tone="warning">fallback 分级</Badge>}
                          {item.optimization_case_id !== null && (
                            <Badge tone="success">已入全局优化池</Badge>
                          )}
                          {humanStatus && (
                            <Badge tone={humanStatus.tone}>{humanStatus.label}</Badge>
                          )}
                          {item.status === "completed"
                            && item.deviation
                            && item.optimization_case_id === null && (
                            <button
                              type="button"
                              className="flex size-8 items-center justify-center rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                              aria-label={`${
                                selectedDeviationIds.has(item.id)
                                  ? "取消加入全局优化池"
                                  : "选择加入全局优化池"
                              }${item.asset.name}`}
                              onClick={() => {
                                setSelectedDeviationIds((current) => {
                                  const next = new Set(current)
                                  if (next.has(item.id)) next.delete(item.id)
                                  else next.add(item.id)
                                  return next
                                })
                              }}
                            >
                              {selectedDeviationIds.has(item.id)
                                ? <CheckSquare size={20} weight="fill" />
                                : <Square size={20} />}
                            </button>
                          )}
                          {item.status === "failed" ? (
                            <Badge tone="danger"><WarningCircle />失败</Badge>
                          ) : item.status === "queued" ? (
                            <Badge tone="active">等待预测</Badge>
                          ) : item.interpretation?.status === "manual_required" ? (
                            <Badge tone="warning">已完成 · 待人工判断</Badge>
                          ) : (
                            <Badge tone={item.deviation ? "danger" : "success"}>
                              预测 {item.predicted_level ?? "—"} / 期望 {item.expected_level}
                            </Badge>
                          )}
                          {evaluation && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => onOpenCorrection(item.id)}
                            >
                              <PencilSimple />
                              {evaluation.review_stage === "completed"
                                ? "查看人工标记"
                                : "确认或纠偏"}
                            </Button>
                          )}
                        </div>
                      </div>
                      <details className="border-t border-[var(--line)] bg-[#fafbf8]">
                        <summary className="cursor-pointer px-4 py-3 text-xs font-semibold">
                          展开完整评测理由
                        </summary>
                        <LevelExplanation item={item} />
                      </details>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="px-5 py-12 text-center text-sm text-[var(--muted)]">
                当前运行尚无逐张结果。
              </p>
            )}
          </div>
        </div>
      </section>
        </div>
      ) : (
        <CorrectionAnalysisPanel
          run={run}
          items={items}
          loading={loading}
          onPreview={onPreview}
          canDecide={me.data?.is_admin === true}
        />
      )}
    </>
  )
}

export function dimensionSelectionName(selection: BaselineRegressionRun["selection"]["dimension"]) {
  if (selection.mode === "none" || selection.prompt_only) return "已关闭 · 仅提示词评级"
  const contract = selection.v3_contract
  if (!contract?.spec_version || !contract.tracks.length) return "未知版本"
  const trackNames: Record<string, string> = {
    class_one: "一类",
    class_two: "二类",
    class_three: "三类",
  }
  const dimensions = contract.tracks
    .map((track) => {
      const fallbackName = track.label.split("（", 1)[0]?.trim() || track.key
      return `${trackNames[track.key] ?? fallbackName}${track.dimension_count}维`
    })
    .join("/")
  return `${contract.spec_version} · ${dimensions}`
}

export function baselineErrorMessage(error: string) {
  const labels: Record<string, string> = {
    missing_level: "未形成 L1-L5 有效等级",
    no_authoritative_score: "未形成服务端权威分数",
    missing_quality_evidence: "未返回画质证据",
    missing_confidence: "未返回模型置信度",
    missing_precheck_scope_status: "调用 A 未返回评测范围字段，无法继续调用 B",
    missing_prompt_b_response: "调用 B 未返回结果",
    missing_aesthetic_result: "未形成八个美感维度结果",
  }
  const [, reasons] = error.split(":", 2)
  if (!reasons) return error
  return reasons.split(",").map((reason) => labels[reason] ?? reason).join("；")
}

export function SelectionFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 truncate text-sm font-semibold" title={value}>
        {value}
      </p>
    </div>
  )
}

export function gradedByFallback(stageA: Record<string, unknown>) {
  const nested = [stageA, stageA.classification, stageA.grading]
  return nested.some((value) => (
    value !== null
    && value !== undefined
    && typeof value === "object"
    && !Array.isArray(value)
    && "graded_by" in value
    && (value as { graded_by?: unknown }).graded_by === "fallback"
  ))
}
