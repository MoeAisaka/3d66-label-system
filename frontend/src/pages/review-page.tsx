import { useEffect, useMemo, useState } from "react"
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CornersOut,
  ImageSquare,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  WarningCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import { submitReviewDecision } from "@/lib/review-submit"
import {
  dimensionKeys as dimensionKeysForSchema,
  dimensionLabels as dimensionLabelsForSchema,
} from "@/lib/dimension-schema"
import type { EvaluationRecord, ReviewCorrection, ReviewStage, User } from "@/lib/types"
import { ReviewCorrectionForm } from "@/pages/review-correction-form"
import {
  filterReviewAssets,
  ReviewList,
  reviewStageMeta,
  reviewWorkspaceMeta,
  type ReviewWorkspaceView,
} from "@/pages/review-list"

const samplingNames: Record<EvaluationRecord["sampling"]["tier"], string> = {
  required: "必须审核",
  sampled: "抽样审核",
  deferred: "暂缓审核",
  reviewed: "已审核",
}

function samplingTone(tier: EvaluationRecord["sampling"]["tier"]) {
  if (tier === "required") return "danger" as const
  if (tier === "sampled") return "warning" as const
  if (tier === "reviewed") return "success" as const
  return "neutral" as const
}

export function ReviewPage({ user }: { user: User }) {
  const { reviewStage: requestedStage, reviewView: requestedView } = useParams()
  const reviewView = normalizeReviewView(requestedView)
  const reviewStage = reviewView === "completed"
    ? "completed"
    : requestedView
      ? undefined
      : normalizeReviewStage(requestedStage)
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedEvaluationId = Number(searchParams.get("evaluation") || 0)
  const legacyAssetId = Number(searchParams.get("asset") || 0)
  const [zoom, setZoom] = useState(100)
  const reviewer = user.username
  const [note, setNote] = useState("")
  const queryClient = useQueryClient()
  const evaluations = useQuery({
    queryKey: ["evaluations", "review-list"],
    queryFn: () => api<{ items: EvaluationRecord[] }>("/api/evaluations?limit=1000"),
    refetchInterval: 4000,
  })
  const legacyEvaluationId = evaluations.data?.items.find((item) => item.id === legacyAssetId)?.evaluation.id ?? 0
  const currentId = requestedEvaluationId || legacyEvaluationId
  const filteredAssets = useMemo(
    () => filterReviewAssets(evaluations.data?.items ?? [], searchParams, reviewStage, reviewView),
    [evaluations.data?.items, reviewStage, reviewView, searchParams],
  )
  const detail = useQuery({
    queryKey: ["evaluation", currentId],
    queryFn: () => api<EvaluationRecord>(`/api/evaluations/${currentId}`),
    enabled: Boolean(currentId),
  })
  const currentIndex = filteredAssets.findIndex((item) => item.evaluation.id === currentId)
  const asset = detail.data
  const evaluation = asset?.evaluation
  const sampling = asset?.sampling
  const dimensions = evaluation?.aesthetic?.dimensions ?? {}
  const scoring = evaluation?.scoring
  const scopeStatus = evaluation?.precheck?.classification?.scope_status
  const requiredDimensionKeys = useMemo(
    () => dimensionKeysForSchema(evaluation?.dimension_schema),
    [evaluation?.dimension_schema],
  )
  const dimensionLabels = useMemo(
    () => dimensionLabelsForSchema(evaluation?.dimension_schema),
    [evaluation?.dimension_schema],
  )
  const completeDimensionCount = requiredDimensionKeys.filter((key) => Number(dimensions[key]?.grade)).length
  const dimensionContractReady = Boolean(
    evaluation?.dimension_schema?.status === "resolved"
    && requiredDimensionKeys.length,
  )

  useEffect(() => {
    setZoom(100)
    setNote("")
  }, [currentId])

  const mediaLabels = useMemo(() => {
    const media = evaluation?.precheck?.media_form ?? {}
    return Object.entries(media)
      .filter(([, value]: any) => value?.status === "yes")
      .map(([key]) => ({ real_photo: "实景图", rendering: "效果图", ai_generated: "AI 图", professional_photography: "专业摄影", documentary_record: "现场记录", casual_snapshot: "随拍", collage_or_multiview: "多视角", unfinished_scene: "未完工", white_background_product: "白底产品" })[key] || key)
  }, [evaluation])

  const enqueue = useMutation({
    mutationFn: () => api("/api/jobs/enqueue", { method: "POST", ...jsonBody({ asset_ids: [asset?.id], prompt_id: evaluation?.prompt_id, prompt_a_id: evaluation?.prompt_a_id, prompt_b_id: evaluation?.prompt_b_id }) }),
    onSuccess: async () => {
      toast.success("已创建评测任务")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["evaluation", currentId] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      ])
    },
    onError: (error) => toast.error(error.message),
  })
  const review = useMutation({
    mutationFn: async ({ decision, corrected_level, reviewNote, corrections = [] }: { decision: "approved" | "corrected" | "rejected"; corrected_level: string | null; reviewNote: string; corrections?: ReviewCorrection[] }) => {
      if (!evaluation) throw new Error("评测结果尚未加载")
      return submitReviewDecision({
        evaluation,
        reviewer,
        decision,
        correctedLevel: corrected_level,
        note: reviewNote,
        corrections,
      })
    },
    onSuccess: async (_data, variables) => {
      const nextEvaluation =
        filteredAssets[currentIndex + 1]?.evaluation ??
        filteredAssets[currentIndex - 1]?.evaluation
      setNote("")
      toast.success(variables.decision === "corrected" ? "人工维度纠错和最终结果已保存" : variables.decision === "approved" ? "已确认模型结果" : "已退回复核")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["evaluation", currentId] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      const params = new URLSearchParams(searchParams)
      params.delete("asset")
      if (nextEvaluation) params.set("evaluation", String(nextEvaluation.id))
      else params.delete("evaluation")
      setSearchParams(params, { replace: true })
    },
    onError: (error) => toast.error(error.message),
  })

  function go(offset: number) {
    if (!filteredAssets.length || currentIndex < 0) return
    const next = filteredAssets[currentIndex + offset]
    if (next) {
      const params = new URLSearchParams(searchParams)
      params.delete("asset")
      params.set("evaluation", String(next.evaluation.id))
      setSearchParams(params)
    }
  }

  if (!requestedEvaluationId && !legacyAssetId) {
    return <ReviewList items={evaluations.data?.items ?? []} loading={evaluations.isLoading} searchParams={searchParams} setSearchParams={setSearchParams} stage={reviewStage} view={reviewView} />
  }

  const listParams = new URLSearchParams(searchParams)
  listParams.delete("asset")
  listParams.delete("evaluation")
  const listPath = reviewView
    ? `/workflow/review/${reviewView}`
    : `/review/${reviewStage ?? "initial"}`
  const listUrl = `${listPath}${listParams.toString() ? `?${listParams.toString()}` : ""}`
  const pageMeta = reviewView
    ? reviewWorkspaceMeta[reviewView]
    : reviewStageMeta[reviewStage ?? "initial"]

  return (
    <>
      <PageHeader
        index="04"
        title={pageMeta.title}
        description={reviewView ? pageMeta.description : `在原图旁核对模型证据并完成${reviewStageMeta[reviewStage ?? "initial"].label}；每轮记录独立留档。`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="secondary"><Link to={listUrl}><ArrowLeft />返回审核列表</Link></Button>
            <div className="flex items-center border border-[var(--line-strong)] bg-white">
              <Button variant="ghost" size="icon" className="rounded-none" onClick={() => go(-1)} disabled={currentIndex <= 0} aria-label="上一张"><ArrowLeft /></Button>
              <span className="font-data min-w-24 border-x border-[var(--line)] px-3 text-center text-xs">{currentIndex >= 0 ? currentIndex + 1 : 0} / {filteredAssets.length}</span>
              <Button variant="ghost" size="icon" className="rounded-none" onClick={() => go(1)} disabled={currentIndex < 0 || currentIndex >= filteredAssets.length - 1} aria-label="下一张"><ArrowRight /></Button>
            </div>
          </div>
        }
      />

      {!currentId ? (
        <div className="flex min-h-[65dvh] flex-col items-center justify-center px-6 text-center">
          <ImageSquare size={36} weight="light" />
          <h2 className="font-editorial mt-5 text-2xl font-bold">没有可审核的图片</h2>
          <p className="mt-2 text-sm text-[var(--muted)]">先上传图片，再创建评测任务。</p>
          <Button asChild className="mt-6"><Link to="/assets">前往素材页<ArrowRight /></Link></Button>
        </div>
      ) : (
        <div className="mx-auto grid max-w-[1820px] gap-0 bg-white xl:grid-cols-[minmax(0,1fr)_560px]">
          <section className="min-w-0 border-r border-[var(--line)]">
            <div className="flex min-h-14 flex-wrap items-center justify-between gap-2 border-b border-[var(--line)] px-4 py-2">
              <div className="min-w-0">
                <p className="file-name max-w-[60vw] truncate text-sm">{asset?.name || "正在读取"}</p>
                <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">{asset?.width} × {asset?.height} · 素材 #{String(asset?.id || 0).padStart(5, "0")} · 结果 #{String(currentId).padStart(5, "0")}</p>
              </div>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="icon" onClick={() => setZoom(Math.max(50, zoom - 10))} aria-label="缩小"><MagnifyingGlassMinus /></Button>
                <span className="font-data w-14 text-center text-xs">{zoom}%</span>
                <Button variant="ghost" size="icon" onClick={() => setZoom(Math.min(200, zoom + 10))} aria-label="放大"><MagnifyingGlassPlus /></Button>
                <Button variant="ghost" size="icon" onClick={() => setZoom(100)} aria-label="适应窗口"><CornersOut /></Button>
              </div>
            </div>

            <div className="hairline-grid flex min-h-[min(72dvh,860px)] items-center justify-center overflow-auto bg-white p-5 md:p-8 scrollbar-thin">
              {asset ? (
                <figure className="relative border border-[var(--line-strong)] bg-white p-1">
                  <span className="absolute -left-2 -top-px h-px w-5 bg-[#9ca398]" /><span className="absolute -left-px -top-2 h-5 w-px bg-[#9ca398]" />
                  <span className="absolute -right-2 -top-px h-px w-5 bg-[#9ca398]" /><span className="absolute -right-px -top-2 h-5 w-px bg-[#9ca398]" />
                  <span className="absolute -bottom-px -left-2 h-px w-5 bg-[#9ca398]" /><span className="absolute -bottom-2 -left-px h-5 w-px bg-[#9ca398]" />
                  <span className="absolute -bottom-px -right-2 h-px w-5 bg-[#9ca398]" /><span className="absolute -bottom-2 -right-px h-5 w-px bg-[#9ca398]" />
                  <img
                    src={asset.image_url}
                    alt={asset.name}
                    className="block max-h-[70dvh] max-w-full object-contain transition-[width] duration-200"
                    style={{ width: `${Math.max(320, ((asset.width ?? 900) * zoom) / 100)}px` }}
                  />
                </figure>
              ) : <div className="h-96 w-full max-w-4xl animate-pulse bg-[#f1f3ef]" />}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-[var(--line)] px-4 py-3">
              {mediaLabels.map((label) => <Badge key={label}>{label}</Badge>)}
              {evaluation?.precheck?.classification?.primary_category && <Badge tone="active">{evaluation.precheck.classification.primary_category}</Badge>}
              <span className="ml-auto font-data text-[0.68rem] text-[var(--muted)]">{evaluation ? `${evaluation.versions.model} · ${evaluation.versions.engine}` : "正在读取评测版本"}</span>
            </div>
          </section>

          <aside className="min-w-0 bg-white">
            <div className="flex min-h-20 items-center justify-between border-b border-[var(--line)] px-5 py-4">
              <div><h2 className="font-editorial text-2xl font-bold">证据</h2><p className="mt-1 text-xs text-[var(--muted)]">{requiredDimensionKeys.length || "—"} 个审美维度</p></div>
              {scopeStatus !== "out_of_scope" && dimensionContractReady && completeDimensionCount === requiredDimensionKeys.length && evaluation?.level && (
                <div className="text-right">
                  {evaluation.human_review?.decision === "corrected" && <Badge tone="success" className="mb-2">人工最终</Badge>}
                  <p className="font-data text-3xl font-semibold">{evaluation.final_level || evaluation.level}</p>
                  <p className="font-data mt-1 text-xs text-[var(--muted)]">
                    {evaluation.human_review?.decision === "corrected" ? `${evaluation.final_score?.toFixed(1)} / 100 · 模型 ${evaluation.level} ${evaluation.score?.toFixed(1)}` : `${evaluation.score?.toFixed(1)} / 100`}
                  </p>
                </div>
              )}
            </div>

            {!evaluation ? (
              <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                <ImageSquare size={30} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">正在读取评测结果</h3>
                <p className="mt-2 text-sm leading-6 text-[var(--muted)]">每条结果都固定对应一次模型和提示词版本运行。</p>
              </div>
            ) : scopeStatus === "out_of_scope" ? (
              <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                <Badge>不参与空间美感评分</Badge>
                <ImageSquare className="mt-5" size={32} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">{evaluation.versions.prompt ? "单提示词判定为范围外" : "A 阶段判定为范围外"}</h3>
                <p className="mt-2 max-w-[46ch] text-sm leading-6 text-[var(--muted)]">该素材属于“{evaluation.precheck?.classification?.primary_category || "其他类型"}”，因此本次不会进入维度评分。这不是数据丢失。</p>
                <Button className="mt-6" variant="secondary" onClick={() => enqueue.mutate()} disabled={enqueue.isPending}>使用当前版本重新评测<ArrowRight /></Button>
              </div>
            ) : !dimensionContractReady || completeDimensionCount < requiredDimensionKeys.length ? (
              <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                <Badge tone="danger">评测不完整</Badge>
                <WarningCircle className="mt-5 text-[#8d2924]" size={32} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">{dimensionContractReady ? "缺少维度评分数据" : "维度规则无法解析"}</h3>
                <p className="mt-2 max-w-[46ch] text-sm leading-6 text-[var(--muted)]">{dimensionContractReady ? `当前结果只有 ${completeDimensionCount} / ${requiredDimensionKeys.length} 个有效维度，系统不会把它当作正式评分。请使用当前提示词版本重新评测。` : "系统无法确认这条结果使用的维度规则，因此不会开放逐维纠偏。可以重新评测，或退回给管理员检查规则快照。"}</p>
                <Button className="mt-6" onClick={() => enqueue.mutate()} disabled={enqueue.isPending}>重新评测<ArrowRight /></Button>
              </div>
            ) : (
              <>
                {sampling && (
                  <div className="border-b border-[var(--line)] bg-[#fafbf8] px-5 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-bold">智能抽样建议</p>
                        <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">{sampling.version} · 常规抽样 {sampling.sample_rate}%</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge tone={samplingTone(sampling.tier)}>{samplingNames[sampling.tier]}</Badge>
                        <span className="font-data text-xs font-bold">优先级 P{sampling.priority}</span>
                      </div>
                    </div>
                    <p className="mt-3 text-xs leading-5 text-[var(--muted)]">入队依据：{sampling.reasons.map((reason) => reason.label).join("；")}</p>
                  </div>
                )}
                {evaluation.risk_review?.triggered && (
                  <div className={`border-b border-[var(--line)] px-5 py-4 ${evaluation.risk_review.verdict === "downgrade" ? "bg-[#fff9ef]" : evaluation.risk_review.verdict === "error" || evaluation.risk_review.verdict === "uncertain" ? "bg-[#fff8f7]" : "bg-[#fafbf8]"}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="flex items-center gap-2 text-sm font-semibold"><WarningCircle />高风险自动复核</p>
                      <Badge tone={evaluation.risk_review.verdict === "downgrade" ? "warning" : evaluation.risk_review.verdict === "keep" ? "success" : "danger"}>{evaluation.risk_review.verdict === "downgrade" ? `已修正 ${evaluation.risk_review.corrections?.length ?? 0} 项` : evaluation.risk_review.verdict === "keep" ? "维持初评" : evaluation.risk_review.verdict === "error" ? "复核失败" : "需要人工确认"}</Badge>
                    </div>
                    {(evaluation.risk_review.trigger_reasons?.length ?? 0) > 0 && <p className="mt-2 text-xs leading-5 text-[var(--muted)]">触发原因：{evaluation.risk_review.trigger_reasons?.join("、")}</p>}
                    {(evaluation.risk_review.corrections?.length ?? 0) > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{evaluation.risk_review.corrections?.map((correction, index) => <Badge key={`${correction.field}-${index}`} tone="active">{riskFieldLabel(correction.field, dimensionLabels)} {String(correction.before)} → {String(correction.after)}</Badge>)}</div>}
                    {(evaluation.risk_review.reasons?.length ?? 0) > 0 && <p className="mt-3 line-clamp-3 text-xs leading-5 text-[var(--muted)]">{evaluation.risk_review.reasons?.join("；")}</p>}
                  </div>
                )}
                <div className="max-h-[calc(100dvh-330px)] overflow-y-auto scrollbar-thin">
                  <div className="border-b border-[var(--line-strong)] bg-[#fafbf8] px-5 py-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div><p className="text-sm font-bold">{reviewStageMeta[evaluation.review_stage].label}</p><p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">审核版本 {evaluation.review_revision}</p></div>
                      <Badge tone={evaluation.review_stage === "completed" ? "success" : evaluation.review_stage === "arbitration" ? "danger" : "active"}>{reviewStageMeta[evaluation.review_stage].label}</Badge>
                    </div>
                  </div>
                  {(evaluation.review_history?.length ?? 0) > 0 && (
                    <details className="border-b border-[var(--line)] bg-white">
                      <summary className="cursor-pointer px-5 py-4 text-sm font-semibold">查看完整人工审核记录（{evaluation.review_history.length}）</summary>
                      <div className="divide-y divide-[var(--line)] border-t border-[var(--line)]">
                        {evaluation.review_history.map((item) => <div key={item.id} className="px-5 py-3"><div className="flex flex-wrap items-center justify-between gap-2"><Badge>{reviewStageMeta[item.stage].label} · {reviewDecisionLabel(item.decision)}</Badge><span className="font-data text-[0.68rem] text-[var(--muted)]">{item.reviewer_name} · {new Date(item.created_at).toLocaleString("zh-CN")}</span></div>{item.note && <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{item.note}</p>}</div>)}
                      </div>
                    </details>
                  )}
                  <div className="grid grid-cols-2 gap-3 px-5 pt-5">
                    <label><span className="mb-2 block text-xs font-semibold">审核账号（当前登录）</span><Input value={reviewer} readOnly /></label>
                    <label><span className="mb-2 block text-xs font-semibold">模型置信度</span><div className="font-data flex h-11 items-center border border-[var(--line)] bg-[#fafbf8] px-3">{evaluation.confidence != null ? `${Math.round(evaluation.confidence * 100)}%` : "—"}</div></label>
                  </div>
                  {evaluation.review_stage !== "completed" && <div className="px-5 pb-5"><label className="mt-3 block"><span className="mb-2 block text-xs font-semibold">审核说明（确认或退回时可选）</span><Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="补充判断依据" /></label>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <Button variant="secondary" onClick={() => review.mutate({ decision: "rejected", corrected_level: null, reviewNote: note.trim() })} disabled={review.isPending}>退回复核</Button>
                    <Button onClick={() => review.mutate({ decision: "approved", corrected_level: null, reviewNote: note.trim() })} disabled={review.isPending}><Check weight="bold" />确认结果</Button>
                  </div></div>}
                  <ReviewCorrectionForm key={`${evaluation.id}-${evaluation.review_revision}`} dimensions={dimensions} dimensionSchema={evaluation.dimension_schema} scoring={scoring ?? {}} pending={review.isPending} editable={evaluation.review_stage !== "completed"} onSubmit={({ note: correctionNote, corrections }) => {
                    review.mutate({ decision: "corrected", corrected_level: null, reviewNote: correctionNote, corrections })
                  }} />
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </>
  )
}

function riskFieldLabel(field: string, dimensionLabels: Record<string, string>) {
  if (field.startsWith("dimensions.")) return dimensionLabels[field.replace("dimensions.", "")] || field
  return ({ professional_photography: "专业摄影", documentary_record: "现场记录", quality_severity: "画质", level_cap: "等级上限" } as Record<string, string>)[field] || field
}

function normalizeReviewStage(value: string | undefined): ReviewStage {
  return value === "secondary" || value === "arbitration" || value === "completed" ? value : "initial"
}

function normalizeReviewView(value: string | undefined): ReviewWorkspaceView | undefined {
  return value === "model-evaluation"
    || value === "low-confidence"
    || value === "consensus"
    || value === "adjudication"
    || value === "completed"
    ? value
    : undefined
}

function reviewDecisionLabel(value: "approved" | "corrected" | "rejected") {
  if (value === "approved") return "确认"
  if (value === "corrected") return "纠偏"
  return "退回"
}
