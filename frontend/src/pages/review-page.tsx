import { useEffect, useMemo, useState } from "react"
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CornersOut,
  ImageSquare,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  PencilSimple,
  WarningCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { Asset, ReviewCorrection } from "@/lib/types"
import { dimensionLabels, ReviewCorrectionForm } from "@/pages/review-correction-form"
import { filterReviewAssets, ReviewList } from "@/pages/review-list"

const requiredDimensionKeys = Object.keys(dimensionLabels)

export function ReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedId = Number(searchParams.get("asset") || 0)
  const [zoom, setZoom] = useState(100)
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("3d66-reviewer") || "")
  const [note, setNote] = useState("")
  const [correctionOpen, setCorrectionOpen] = useState(false)
  const queryClient = useQueryClient()
  const assets = useQuery({
    queryKey: ["assets", "review-list"],
    queryFn: () => api<{ items: Asset[] }>("/api/assets?limit=1000"),
    refetchInterval: 4000,
  })
  const currentId = requestedId
  const filteredAssets = useMemo(
    () => filterReviewAssets(assets.data?.items ?? [], searchParams),
    [assets.data?.items, searchParams],
  )
  const detail = useQuery({
    queryKey: ["asset", currentId],
    queryFn: () => api<Asset>(`/api/assets/${currentId}`),
    enabled: Boolean(currentId),
    refetchInterval: (query) => (query.state.data?.status === "queued" ? 2500 : false),
  })
  const currentIndex = filteredAssets.findIndex((item) => item.id === currentId)
  const asset = detail.data
  const evaluation = asset?.evaluation
  const dimensions = evaluation?.aesthetic?.dimensions ?? {}
  const scoring = evaluation?.scoring
  const scopeStatus = evaluation?.precheck?.classification?.scope_status
  const completeDimensionCount = requiredDimensionKeys.filter((key) => Number(dimensions[key]?.grade)).length

  useEffect(() => {
    setZoom(100)
    setNote("")
    setCorrectionOpen(false)
  }, [currentId])

  const mediaLabels = useMemo(() => {
    const media = evaluation?.precheck?.media_form ?? {}
    return Object.entries(media)
      .filter(([, value]: any) => value?.status === "yes")
      .map(([key]) => ({ real_photo: "实景图", rendering: "效果图", ai_generated: "AI 图", professional_photography: "专业摄影", documentary_record: "现场记录", casual_snapshot: "随拍", collage_or_multiview: "多视角", unfinished_scene: "未完工", white_background_product: "白底产品" })[key] || key)
  }, [evaluation])

  const enqueue = useMutation({
    mutationFn: () => api("/api/jobs/enqueue", { method: "POST", ...jsonBody({ asset_ids: [currentId] }) }),
    onSuccess: async () => {
      toast.success("已创建评测任务")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["asset", currentId] }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      ])
    },
    onError: (error) => toast.error(error.message),
  })
  const review = useMutation({
    mutationFn: ({ decision, corrected_level, reviewNote, corrections = [] }: { decision: "approved" | "corrected" | "rejected"; corrected_level: string | null; reviewNote: string; corrections?: ReviewCorrection[] }) =>
      api(`/api/evaluations/${evaluation?.id}/review`, {
        method: "POST",
        ...jsonBody({ reviewer_name: reviewer, decision, corrected_level, note: reviewNote, corrections }),
      }),
    onSuccess: async (_data, variables) => {
      localStorage.setItem("3d66-reviewer", reviewer)
      setCorrectionOpen(false)
      setNote("")
      toast.success(variables.decision === "corrected" ? "人工维度纠错和最终结果已保存" : variables.decision === "approved" ? "已确认模型结果" : "已退回复核")
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["asset", currentId] }),
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
    },
    onError: (error) => toast.error(error.message),
  })

  function go(offset: number) {
    if (!filteredAssets.length || currentIndex < 0) return
    const next = filteredAssets[currentIndex + offset]
    if (next) {
      const params = new URLSearchParams(searchParams)
      params.set("asset", String(next.id))
      setSearchParams(params)
    }
  }

  if (!requestedId) {
    return <ReviewList items={assets.data?.items ?? []} loading={assets.isLoading} searchParams={searchParams} setSearchParams={setSearchParams} />
  }

  const listParams = new URLSearchParams(searchParams)
  listParams.delete("asset")
  const listUrl = `/review${listParams.toString() ? `?${listParams.toString()}` : ""}`

  return (
    <>
      <PageHeader
        index="04"
        title="美感评测"
        description="在原图旁核对模型证据、等级限制和版本快照。"
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
                <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">{asset?.width} × {asset?.height} · #{currentId.toString().padStart(5, "0")}</p>
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
              <span className="ml-auto font-data text-[0.68rem] text-[var(--muted)]">{evaluation ? `${evaluation.versions.model} · ${evaluation.versions.engine}` : asset?.status}</span>
            </div>
          </section>

          <aside className="min-w-0 bg-white">
            <div className="flex min-h-20 items-center justify-between border-b border-[var(--line)] px-5 py-4">
              <div><h2 className="font-editorial text-2xl font-bold">证据</h2><p className="mt-1 text-xs text-[var(--muted)]">八个审美维度</p></div>
              {scopeStatus !== "out_of_scope" && completeDimensionCount === requiredDimensionKeys.length && evaluation?.level && (
                <div className="text-right">
                  {evaluation.human_review?.decision === "corrected" && <Badge tone="success" className="mb-2">人工最终</Badge>}
                  <p className="font-data text-3xl font-semibold">{evaluation.final_level || evaluation.level}</p>
                  <p className="font-data mt-1 text-xs text-[var(--muted)]">
                    {evaluation.human_review?.decision === "corrected" ? `模型 ${evaluation.level} · ${evaluation.score?.toFixed(1)}` : `${evaluation.score?.toFixed(1)} / 100`}
                  </p>
                </div>
              )}
            </div>

            {!evaluation ? (
              <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                {asset?.status === "queued" ? <WarningCircle size={30} /> : <ImageSquare size={30} weight="light" />}
                <h3 className="font-editorial mt-4 text-xl font-bold">{asset?.status === "queued" ? "等待模型处理" : "还没有评测结果"}</h3>
                <p className="mt-2 text-sm leading-6 text-[var(--muted)]">任务完成后，这里会显示维度等级、证据、缺陷和最终限制。</p>
                {asset?.status !== "queued" && <Button className="mt-6" onClick={() => enqueue.mutate()} disabled={enqueue.isPending}>开始评测<ArrowRight /></Button>}
              </div>
            ) : scopeStatus === "out_of_scope" ? (
              <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                <Badge>不参与空间美感评分</Badge>
                <ImageSquare className="mt-5" size={32} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">A 阶段判定为范围外</h3>
                <p className="mt-2 max-w-[46ch] text-sm leading-6 text-[var(--muted)]">该素材属于“{evaluation.precheck?.classification?.primary_category || "其他类型"}”，因此没有调用 B，也不会生成八个空间美感维度。这不是数据丢失。</p>
                <Button className="mt-6" variant="secondary" onClick={() => enqueue.mutate()} disabled={enqueue.isPending}>使用当前版本重新评测<ArrowRight /></Button>
              </div>
            ) : completeDimensionCount < requiredDimensionKeys.length ? (
              <div className="flex min-h-[520px] flex-col items-center justify-center px-8 text-center">
                <Badge tone="danger">评测不完整</Badge>
                <WarningCircle className="mt-5 text-[#8d2924]" size={32} weight="light" />
                <h3 className="font-editorial mt-4 text-xl font-bold">缺少八维评分数据</h3>
                <p className="mt-2 max-w-[46ch] text-sm leading-6 text-[var(--muted)]">当前结果只有 {completeDimensionCount} / {requiredDimensionKeys.length} 个有效维度，系统不会把它当作正式评分。请使用当前提示词版本重新评测。</p>
                <Button className="mt-6" onClick={() => enqueue.mutate()} disabled={enqueue.isPending}>重新评测<ArrowRight /></Button>
              </div>
            ) : (
              <>
                <div className="max-h-[calc(100dvh-330px)] overflow-y-auto scrollbar-thin">
                  {Object.entries(dimensionLabels).map(([key, label], index) => {
                    const item = dimensions[key] ?? {}
                    const grade = Number(item.grade || 0)
                    return (
                      <details key={key} className="group border-b border-[var(--line)]" open={index === 0}>
                        <summary className="grid cursor-pointer list-none grid-cols-[28px_1fr_42px] items-center gap-3 px-5 py-4 hover:bg-[#fafbf8]">
                          <span className="font-data text-xs text-[var(--muted)]">{String(index + 1).padStart(2, "0")}</span>
                          <div><p className="text-sm font-semibold">{label}</p><div className="mt-2 flex h-1.5 gap-px">{[1,2,3,4,5].map((step) => <span key={step} className={`flex-1 ${step <= grade ? "bg-primary" : "bg-[#eef1eb]"}`} />)}</div></div>
                          <span className="font-data text-right text-xl font-semibold">{grade || "—"}</span>
                        </summary>
                        <div className="bg-[#fbfcfa] px-5 pb-5 pt-1">
                          <p className="text-xs font-semibold text-[var(--muted)]">视觉证据</p>
                          <ul className="mt-2 space-y-2 text-sm leading-6">{(item.evidence ?? []).map((text: string, evidenceIndex: number) => <li key={evidenceIndex} className="grid grid-cols-[12px_1fr] gap-2"><span className="mt-[0.65rem] size-1.5 bg-primary" /><span>{text}</span></li>)}</ul>
                          {(item.defects ?? []).length > 0 && <><p className="mt-4 text-xs font-semibold text-[#8d2924]">明显缺陷</p><ul className="mt-2 space-y-1 text-sm leading-6 text-[#74302b]">{item.defects.map((text: string, defectIndex: number) => <li key={defectIndex}>{text}</li>)}</ul></>}
                        </div>
                      </details>
                    )
                  })}
                  {(scoring?.caps?.length ?? 0) > 0 && (
                    <div className="border-b border-[var(--line)] bg-[#fff9ef] px-5 py-4">
                      <p className="flex items-center gap-2 text-sm font-semibold text-[#7d4308]"><WarningCircle />等级限制</p>
                      {(scoring?.caps ?? []).map((cap: any, index: number) => <p key={index} className="mt-2 text-xs leading-5 text-[#7d4308]">最高 {cap.cap}：{cap.reason}</p>)}
                    </div>
                  )}
                </div>

                <div className="border-t border-[var(--line-strong)] p-5">
                  {evaluation.human_review && (
                    <div className="mb-4 border-y border-[var(--line)] bg-[#fafbf8] px-3 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Badge tone={evaluation.human_review.decision === "rejected" ? "warning" : "success"}>
                          {evaluation.human_review.decision === "corrected" ? `已人工修正为 ${evaluation.human_review.corrected_level}` : evaluation.human_review.decision === "approved" ? "已确认模型结果" : "已退回复核"}
                        </Badge>
                        <span className="font-data text-[0.68rem] text-[var(--muted)]">{evaluation.human_review.reviewer_name} · {new Date(evaluation.human_review.created_at).toLocaleString("zh-CN")}</span>
                      </div>
                      {evaluation.human_review.note && <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{evaluation.human_review.note}</p>}
                      {(evaluation.human_review.corrections?.length ?? 0) > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{evaluation.human_review.corrections.map((correction, index) => <Badge key={`${correction.field_key}-${index}`} tone="active">{correction.target_type === "dimension" ? dimensionLabels[correction.field_key] || correction.field_key : "评分规则"}{correction.target_type === "dimension" ? ` ${correction.model_value}→${correction.human_value}` : ""}</Badge>)}</div>}
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <label><span className="mb-2 block text-xs font-semibold">审核姓名</span><Input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="例如：小陈" /></label>
                    <label><span className="mb-2 block text-xs font-semibold">模型置信度</span><div className="font-data flex h-11 items-center border border-[var(--line)] bg-[#fafbf8] px-3">{evaluation.confidence != null ? `${Math.round(evaluation.confidence * 100)}%` : "—"}</div></label>
                  </div>
                  <label className="mt-3 block"><span className="mb-2 block text-xs font-semibold">审核说明（确认或退回时可选）</span><Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="补充判断依据" /></label>
                  <div className="mt-3 grid grid-cols-3 gap-2">
                    <Button variant="secondary" onClick={() => review.mutate({ decision: "rejected", corrected_level: null, reviewNote: note.trim() })} disabled={!reviewer || review.isPending}>退回复核</Button>
                    <Button variant="secondary" onClick={() => setCorrectionOpen((value) => !value)} disabled={review.isPending}><PencilSimple />纠正结果</Button>
                    <Button onClick={() => review.mutate({ decision: "approved", corrected_level: null, reviewNote: note.trim() })} disabled={!reviewer || review.isPending}><Check weight="bold" />确认结果</Button>
                  </div>

                  {correctionOpen && <ReviewCorrectionForm dimensions={dimensions} modelLevel={evaluation.level} pending={review.isPending} onCancel={() => setCorrectionOpen(false)} onSubmit={({ correctedLevel, note: correctionNote, corrections }) => {
                    if (!reviewer.trim()) { toast.error("请先填写审核姓名"); return }
                    review.mutate({ decision: "corrected", corrected_level: correctedLevel, reviewNote: correctionNote, corrections })
                  }} />}
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </>
  )
}
