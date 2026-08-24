import { useState } from "react"
import { ArrowRight, Funnel, ImageSquare, MagnifyingGlass, X } from "@phosphor-icons/react"
import { Link, type SetURLSearchParams } from "react-router-dom"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { dimensionKeys } from "@/lib/dimension-schema"
import type { EvaluationRecord, ReviewStage } from "@/lib/types"

export type ReviewWorkspaceView =
  | "model-evaluation"
  | "low-confidence"
  | "consensus"
  | "adjudication"
  | "completed"

export const reviewWorkspaceMeta: Record<ReviewWorkspaceView, { title: string; description: string }> = {
  "model-evaluation": {
    title: "模型初评",
    description: "查看全部模型评测运行及其策略快照；这里是运行证据，不等于最终人工真值。",
  },
  "low-confidence": {
    title: "低置信度待审",
    description: "低于当前策略阈值的新结果进入初审组；审核员提交前后都看不到其他人的具体答案。",
  },
  consensus: {
    title: "初审组共识",
    description: "查看正在收集的独立盲审票数；收齐后按维度和关键字段分别计算严格多数。",
  },
  adjudication: {
    title: "主审裁决",
    description: "只处理收齐奇数票后仍无严格多数的字段；裁决仍在初审工作台内形成最终真值。",
  },
  completed: {
    title: "已完成",
    description: "查看初审最终真值和兼容历史审核记录；旧二审/仲裁数据不再成为新任务主流程。",
  },
}

export const reviewStageMeta: Record<ReviewStage, { label: string; title: string; description: string }> = {
  initial: {
    label: "初审纠偏",
    title: "初审纠偏工作台",
    description: "在模型评分原位完成首次确认或逐维纠偏，保存后按风险规则进入二审或完成。",
  },
  secondary: {
    label: "二审确认",
    title: "二审确认工作台",
    description: "独立复核高风险、高等级、重大纠偏与抽样任务，不覆盖初审记录。",
  },
  arbitration: {
    label: "冲突仲裁",
    title: "冲突仲裁工作台",
    description: "只处理初审与二审结论不一致的样本，形成最终人工真值。",
  },
  completed: {
    label: "已完成",
    title: "已完成审核",
    description: "查看完整审核事件链和最终真值；已完成记录不再直接编辑。",
  },
}

const mediaNames: Record<string, string> = {
  real_photo: "实景图",
  rendering: "效果图",
  ai_generated: "AI 图",
  professional_photography: "专业摄影",
  documentary_record: "现场记录",
  casual_snapshot: "随拍",
  collage_or_multiview: "多视角",
  unfinished_scene: "未完工",
  white_background_product: "白底产品",
}

const statusNames: Record<string, string> = {
  pending: "待审核",
  deferred: "暂缓审核",
  corrected: "已人工纠正",
  approved: "已确认结果",
  rejected: "已退回复核",
}

const samplingNames = {
  required: "必须审核",
  sampled: "抽样审核",
  deferred: "暂缓审核",
  reviewed: "已审核",
} as const

const qualityNames: Record<string, string> = {
  good: "画质正常",
  minor: "轻微问题",
  moderate: "中度问题",
  severe: "严重问题",
  unusable: "不可用",
  uncertain: "待确认",
}

function reviewStatus(asset: EvaluationRecord) {
  if (asset.evaluation.human_review?.decision) return asset.evaluation.human_review.decision
  return asset.sampling.tier === "deferred" ? "deferred" : "pending"
}

export function ruleDeductionDimensions(evaluation: EvaluationRecord["evaluation"] | undefined) {
  if (evaluation?.scoring?.dimension_scoring_mode !== "rule_deduction") return null
  const raw = evaluation.aesthetic?.dimensions
  if (Array.isArray(raw)) {
    const normalized: Record<string, Record<string, any>> = {}
    for (const item of raw) {
      if (!item || typeof item !== "object" || typeof item.dimension_key !== "string" || normalized[item.dimension_key]) return null
      normalized[item.dimension_key] = item
    }
    return normalized
  }
  if (!raw || typeof raw !== "object") return null
  return raw as Record<string, Record<string, any>>
}

function isProposalTextEvaluation(asset: EvaluationRecord) {
  const channel = asset.evaluation.preprocess?.pdf_input_channel
  return asset.category_key === "proposal_text_pdf"
    || asset.evaluation.preprocess?.category_key === "proposal_text_pdf"
    || channel?.evaluation_object === "source_pdf_document"
}

function proposalReviewReason(evaluation: EvaluationRecord["evaluation"]) {
  const scoringReason = evaluation.scoring?.review_reasons?.[0]
  const precheckReason = evaluation.precheck?.["预检结果"]?.["结论说明"]
  return String(scoringReason || precheckReason || "整份 PDF 尚未形成可发布的自动等级")
}

export function resultStatus(asset: EvaluationRecord) {
  if (isProposalTextEvaluation(asset)) {
    const level = asset.evaluation.final_level || asset.evaluation.level
    const score = asset.evaluation.final_score ?? asset.evaluation.score
    if (["L1", "L2", "L3", "L4", "L5"].includes(level || "") && typeof score === "number") {
      return "scored"
    }
    return asset.evaluation.needs_review || asset.evaluation.scoring?.needs_review
      ? "proposal_review"
      : "proposal_incomplete"
  }
  if (asset.evaluation.precheck?.classification?.scope_status === "out_of_scope") return "out_of_scope"
  const deductionDimensions = ruleDeductionDimensions(asset.evaluation)
  if (asset.evaluation.scoring?.dimension_scoring_mode === "rule_deduction") {
    const entries = deductionDimensions ? Object.values(deductionDimensions) : []
    if (!entries.length) return "invalid_contract"
    return entries.every((item) => Array.isArray(item.hit_rules)) ? "scored" : "incomplete"
  }
  if (asset.evaluation.dimension_schema?.status !== "resolved") return "invalid_contract"
  const requiredDimensionKeys = dimensionKeys(asset.evaluation.dimension_schema)
  if (!requiredDimensionKeys.length) return "invalid_contract"
  const dimensions = asset.evaluation.aesthetic?.dimensions ?? {}
  return requiredDimensionKeys.some((key) => !Number(dimensions[key]?.grade)) ? "incomplete" : "scored"
}

function mediaLabels(asset: EvaluationRecord) {
  const media = asset.evaluation?.precheck?.media_form ?? {}
  return Object.entries(media)
    .filter(([, value]: any) => value?.status === "yes")
    .map(([key]) => mediaNames[key] || key)
}

function normalizedQuality(value: string | undefined) {
  if (value === "normal") return "good"
  if (value === "slight" || value === "mild") return "minor"
  return value || ""
}

export function filterReviewAssets(items: EvaluationRecord[], params: URLSearchParams, stage?: ReviewStage, view?: ReviewWorkspaceView) {
  const query = (params.get("q") || "").trim().toLowerCase()
  const status = params.get("status") || ""
  const level = params.get("level") || ""
  const category = params.get("category") || ""
  const confidence = params.get("confidence") || ""
  const media = params.get("media") || ""
  const quality = params.get("quality") || ""
  const model = params.get("model") || ""
  const prompt = params.get("prompt") || ""
  const reviewer = params.get("reviewer") || ""
  const sampling = params.get("sampling") || ""
  const needsReview = params.get("needs_review") === "1"
  const sort = params.get("sort") || "priority"

  const filtered = items.filter((asset) => {
    const evaluation = asset.evaluation
    if (stage && evaluation.review_stage !== stage) return false
    if (view === "low-confidence" && (
      evaluation.review_panel?.status !== "collecting"
      || evaluation.review_panel.submitted_count !== 0
    )) return false
    if (view === "consensus" && (
      evaluation.review_panel?.status !== "collecting"
      || evaluation.review_panel.submitted_count === 0
    )) return false
    if (view === "adjudication" && evaluation.review_panel?.status !== "lead_adjudication") return false
    if (view === "completed" && evaluation.review_stage !== "completed") return false
    const finalLevel = evaluation?.final_level || evaluation?.level || ""
    const primaryCategory = evaluation?.precheck?.classification?.primary_category || "无法判断"
    const confidenceValue = evaluation?.confidence
    if (query && !asset.name.toLowerCase().includes(query) && !String(asset.id).includes(query) && !String(evaluation.id).includes(query)) return false
    if (status && reviewStatus(asset) !== status) return false
    if (level && finalLevel !== level) return false
    if (category && primaryCategory !== category) return false
    if (media && !mediaLabels(asset).includes(mediaNames[media] || media)) return false
    if (quality && normalizedQuality(evaluation?.precheck?.image_quality?.quality_severity) !== quality) return false
    if (model && evaluation?.versions.model !== model) return false
    if (prompt && evaluation?.versions.prompt !== prompt && evaluation?.versions.prompt_a !== prompt && evaluation?.versions.prompt_b !== prompt) return false
    if (reviewer && evaluation?.human_review?.reviewer_name !== reviewer) return false
    if (sampling && asset.sampling.tier !== sampling) return false
    if (needsReview && !evaluation?.needs_review) return false
    if (confidence === "low" && (confidenceValue == null || confidenceValue >= 0.7)) return false
    if (confidence === "medium" && (confidenceValue == null || confidenceValue < 0.7 || confidenceValue >= 0.9)) return false
    if (confidence === "high" && (confidenceValue == null || confidenceValue < 0.9)) return false
    return true
  })

  return [...filtered].sort((a, b) => {
    if (sort === "newest") return new Date(b.evaluation.created_at).getTime() - new Date(a.evaluation.created_at).getTime()
    if (sort === "confidence_asc") return (a.evaluation?.confidence ?? 2) - (b.evaluation?.confidence ?? 2)
    if (sort === "score_desc") return (b.evaluation?.score ?? -1) - (a.evaluation?.score ?? -1)
    if (sort === "score_asc") return (a.evaluation?.score ?? 101) - (b.evaluation?.score ?? 101)
    return b.sampling.priority - a.sampling.priority || new Date(b.evaluation.created_at).getTime() - new Date(a.evaluation.created_at).getTime()
  })
}

function filterOptions(items: EvaluationRecord[]) {
  const categories = new Set<string>()
  const models = new Set<string>()
  const prompts = new Set<string>()
  const reviewers = new Set<string>()
  items.forEach((asset) => {
    const evaluation = asset.evaluation
    const category = evaluation?.precheck?.classification?.primary_category
    if (category) categories.add(category)
    if (evaluation?.versions.model) models.add(evaluation.versions.model)
    if (evaluation?.versions.prompt) prompts.add(evaluation.versions.prompt)
    if (evaluation?.versions.prompt_a) prompts.add(evaluation.versions.prompt_a)
    if (evaluation?.versions.prompt_b) prompts.add(evaluation.versions.prompt_b)
    if (evaluation?.human_review?.reviewer_name) reviewers.add(evaluation.human_review.reviewer_name)
  })
  return {
    categories: Array.from(categories).sort(),
    models: Array.from(models).sort(),
    prompts: Array.from(prompts).sort(),
    reviewers: Array.from(reviewers).sort(),
  }
}

function statusTone(status: string) {
  if (status === "corrected" || status === "approved") return "success" as const
  if (status === "rejected") return "warning" as const
  if (status === "pending") return "active" as const
  if (status === "deferred") return "neutral" as const
  if (status === "incomplete") return "danger" as const
  return "neutral" as const
}

function samplingTone(tier: EvaluationRecord["sampling"]["tier"]) {
  if (tier === "required") return "danger" as const
  if (tier === "sampled") return "warning" as const
  if (tier === "reviewed") return "success" as const
  return "neutral" as const
}

function detailUrl(evaluationId: number, params: URLSearchParams, stage?: ReviewStage, view?: ReviewWorkspaceView) {
  const next = new URLSearchParams(params)
  next.delete("asset")
  next.set("evaluation", String(evaluationId))
  const path = view ? `/workflow/review/${view}` : `/review/${stage ?? "initial"}`
  return `${path}?${next.toString()}`
}

export function ReviewList({ items, loading, searchParams, setSearchParams, stage, view }: { items: EvaluationRecord[]; loading: boolean; searchParams: URLSearchParams; setSearchParams: SetURLSearchParams; stage?: ReviewStage; view?: ReviewWorkspaceView }) {
  const [filtersOpen, setFiltersOpen] = useState(false)
  const stageItems = filterReviewAssets(items, new URLSearchParams(), stage, view)
  const filtered = filterReviewAssets(items, searchParams, stage, view)
  const options = filterOptions(stageItems)
  const stageCounts = items.reduce<Record<ReviewStage, number>>((counts, item) => {
    counts[item.evaluation.review_stage] += 1
    return counts
  }, { initial: 0, secondary: 0, arbitration: 0, completed: 0 })
  const samplingCounts = stageItems.reduce<Record<EvaluationRecord["sampling"]["tier"], number>>((counts, item) => {
    counts[item.sampling.tier] += 1
    return counts
  }, { required: 0, sampled: 0, deferred: 0, reviewed: 0 })
  const activeFilters = ["q", "status", "level", "category", "media", "quality", "confidence", "model", "prompt", "reviewer", "sampling", "needs_review"].filter((key) => searchParams.get(key)).length

  function update(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    value ? next.set(key, value) : next.delete(key)
    next.delete("asset")
    next.delete("evaluation")
    setSearchParams(next, { replace: true })
  }

  function clearFilters() {
    const next = new URLSearchParams()
    const sort = searchParams.get("sort")
    if (sort && sort !== "priority") next.set("sort", sort)
    setSearchParams(next, { replace: true })
  }

  return <>
    <PageHeader index={view ? "02" : "04"} title={view ? reviewWorkspaceMeta[view].title : reviewStageMeta[stage ?? "initial"].title} description={view ? reviewWorkspaceMeta[view].description : reviewStageMeta[stage ?? "initial"].description} />
    <div className="mx-auto shell-content-wide px-5 py-7 md:px-8 lg:px-10 lg:py-9">
      {!view && <nav className="mb-5 grid border-y border-[var(--line-strong)] bg-white sm:grid-cols-4" aria-label="兼容历史人工审核工作台">
        {(Object.keys(reviewStageMeta) as ReviewStage[]).map((itemStage) => {
          const active = itemStage === stage
          return <Link key={itemStage} to={`/review/${itemStage}`} className={`border-b border-r border-[var(--line)] px-4 py-4 last:border-r-0 sm:border-b-0 ${active ? "bg-primary text-primary-foreground" : "hover:bg-[#fafbf8]"}`}><span className="block text-xs font-semibold">{reviewStageMeta[itemStage].label}</span><span className="font-data mt-1 block text-xl font-bold">{stageCounts[itemStage]}</span></Link>
        })}
      </nav>}
      <section className="mb-5 grid border-y border-[var(--line-strong)] bg-white lg:grid-cols-[minmax(280px,1fr)_auto] lg:items-stretch" aria-label="智能抽样队列">
        <div className="px-4 py-4 lg:border-r lg:border-[var(--line)]">
          <div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-bold">智能抽样审核</h2><Badge>规则 {items[0]?.sampling.version || "smart-sampling-v1.0"}</Badge></div>
          <p className="mt-2 max-w-[70ch] text-xs leading-5 text-[var(--muted)]">黄金样本、高分、低置信度、版本差异和异常同分进入必审；其余常规结果稳定抽取 {items[0]?.sampling.sample_rate ?? 10}%。</p>
        </div>
        <div className="grid grid-cols-2 border-t border-[var(--line)] sm:grid-cols-4 lg:border-t-0">
          {(Object.keys(samplingNames) as Array<keyof typeof samplingNames>).map((tier) => {
            const selected = searchParams.get("sampling") === tier
            return <button key={tier} type="button" className={`min-w-28 border-r border-[var(--line)] px-4 py-3 text-left last:border-r-0 transition-colors ${selected ? "bg-primary" : "hover:bg-[#fafbf8]"}`} onClick={() => update("sampling", selected ? "" : tier)} aria-pressed={selected}><span className="block text-xs font-semibold">{samplingNames[tier]}</span><span className="font-data mt-1 block text-xl font-bold">{samplingCounts[tier]}</span></button>
          })}
        </div>
      </section>
      <section className="border-y border-[var(--line-strong)] bg-white" aria-label="审核列表筛选">
        <button type="button" className="flex min-h-12 w-full items-center justify-between px-4 text-sm font-semibold md:hidden" onClick={() => setFiltersOpen((value) => !value)} aria-expanded={filtersOpen} aria-controls="review-filters"><span className="flex items-center gap-2"><Funnel />筛选条件{activeFilters ? ` · ${activeFilters}` : ""}</span><span>{filtersOpen ? "收起" : "展开"}</span></button>
        <div id="review-filters" className={`${filtersOpen ? "block" : "hidden"} md:block`}>
          <div className="grid gap-4 border-t border-[var(--line)] p-4 md:grid-cols-2 md:border-t-0 xl:grid-cols-4 2xl:grid-cols-[minmax(220px,1.3fr)_repeat(6,minmax(110px,.65fr))]">
          <label className="md:col-span-2 2xl:col-span-1"><span className="mb-2 block text-xs font-semibold">搜索素材</span><div className="relative"><MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" /><Input className="pl-10" value={searchParams.get("q") || ""} onChange={(event) => update("q", event.target.value)} placeholder="文件名或素材编号" /></div></label>
          <FilterSelect label="审核状态" value={searchParams.get("status") || ""} onChange={(value) => update("status", value)} options={Object.entries(statusNames)} />
          <FilterSelect label="最终等级" value={searchParams.get("level") || ""} onChange={(value) => update("level", value)} options={["L5", "L4", "L3", "L2", "L1"].map((value) => [value, value])} />
          <FilterSelect label="主分类" value={searchParams.get("category") || ""} onChange={(value) => update("category", value)} options={options.categories.map((value) => [value, value])} />
          <FilterSelect label="素材形态" value={searchParams.get("media") || ""} onChange={(value) => update("media", value)} options={Object.entries(mediaNames)} />
          <FilterSelect label="画质" value={searchParams.get("quality") || ""} onChange={(value) => update("quality", value)} options={Object.entries(qualityNames)} />
          <FilterSelect label="置信度" value={searchParams.get("confidence") || ""} onChange={(value) => update("confidence", value)} options={[["low", "低于 70%"], ["medium", "70% 至 89%"], ["high", "90% 及以上"]]} />
          </div>
          <div className="grid gap-4 border-t border-[var(--line)] p-4 md:grid-cols-2 xl:grid-cols-3 xl:items-end 2xl:grid-cols-[1fr_1fr_1fr_1fr_auto_auto]">
          <FilterSelect label="模型版本" value={searchParams.get("model") || ""} onChange={(value) => update("model", value)} options={options.models.map((value) => [value, value])} />
          <FilterSelect label="提示词版本" value={searchParams.get("prompt") || ""} onChange={(value) => update("prompt", value)} options={options.prompts.map((value) => [value, value])} />
          <FilterSelect label="审核人" value={searchParams.get("reviewer") || ""} onChange={(value) => update("reviewer", value)} options={options.reviewers.map((value) => [value, value])} />
          <FilterSelect label="排序" emptyLabel="审核优先级" value={searchParams.get("sort") || ""} onChange={(value) => update("sort", value)} options={[["newest", "最新评测"], ["confidence_asc", "置信度从低到高"], ["score_desc", "分数从高到低"], ["score_asc", "分数从低到高"]]} />
          <label className="flex min-h-11 cursor-pointer items-center gap-2 border border-[var(--line-strong)] bg-white px-3 text-sm font-semibold"><input type="checkbox" className="size-4 accent-[#9dbb1c]" checked={searchParams.get("needs_review") === "1"} onChange={(event) => update("needs_review", event.target.checked ? "1" : "")} />仅看需要复核</label>
          <Button variant="ghost" disabled={!activeFilters} onClick={clearFilters}><X />清空筛选</Button>
          </div>
        </div>
      </section>

      <div className="mt-7 flex flex-wrap items-end justify-between gap-4">
        <div><h2 className="font-editorial text-2xl font-bold">{view ? reviewWorkspaceMeta[view].title : reviewStageMeta[stage ?? "initial"].label}队列</h2><p className="mt-1 text-sm text-[var(--muted)]">显示 {filtered.length} 条，本工作台共 {stageItems.length} 条{activeFilters ? `，已启用 ${activeFilters} 项筛选` : ""}</p></div>
        <div className="flex items-center gap-2 text-xs text-[var(--muted)]"><Funnel />筛选条件会保留到大图详情</div>
      </div>

      <div className="mt-4 overflow-x-auto border-y border-[var(--line-strong)] bg-white scrollbar-thin">
        {loading ? <div className="h-72 animate-pulse bg-white" /> : filtered.length ? (
          <table className="w-full min-w-[1640px] border-collapse text-left text-sm">
            <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]"><th className="px-4 py-3 font-semibold">图片</th><th className="px-3 py-3 font-semibold">分类与形态</th><th className="px-3 py-3 font-semibold">画质</th><th className="px-3 py-3 font-semibold">美感结果</th><th className="px-3 py-3 font-semibold">置信度</th><th className="px-3 py-3 font-semibold">审核建议</th><th className="px-3 py-3 font-semibold">审核状态</th><th className="px-3 py-3 font-semibold">版本</th><th className="px-3 py-3 font-semibold">最新更新时间</th><th className="w-28 px-4 py-3 text-right font-semibold">操作</th></tr></thead>
            <tbody>{filtered.map((asset) => {
              const evaluation = asset.evaluation
              const proposalText = isProposalTextEvaluation(asset)
              const status = reviewStatus(asset)
              const result = resultStatus(asset)
              const level = evaluation?.final_level || evaluation?.level
              const category = proposalText
                ? evaluation?.precheck?.["信息提取"]?.["项目分类"]?.["一级分类"] || "PDF方案文本"
                : evaluation?.precheck?.classification?.primary_category || "无法判断"
              const quality = normalizedQuality(evaluation?.precheck?.image_quality?.quality_severity)
              const forms = mediaLabels(asset)
              const nonScored = result !== "scored"
              const deductionDimensions = ruleDeductionDimensions(evaluation)
              const ruleMode = evaluation.scoring?.dimension_scoring_mode === "rule_deduction"
              const expectedDimensions = ruleMode ? Object.keys(deductionDimensions ?? {}) : dimensionKeys(evaluation.dimension_schema)
              const completedDimensions = ruleMode
                ? expectedDimensions.filter((key) => Array.isArray(deductionDimensions?.[key]?.hit_rules)).length
                : expectedDimensions.filter((key) => Number(evaluation.aesthetic?.dimensions?.[key]?.grade)).length
              return <tr key={evaluation.id} className="border-b border-[var(--line)] last:border-0 hover:bg-[#fbfcfa]">
                <td className="px-4 py-3"><div className="flex min-w-0 items-center gap-3"><img src={asset.image_url} alt="" loading="lazy" className="size-16 rounded-[4px] border border-[var(--line)] object-cover" /><div className="min-w-0"><p className="file-name max-w-[260px] truncate">{asset.name}</p><p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">素材 #{String(asset.id).padStart(5, "0")} · 结果 #{String(evaluation.id).padStart(5, "0")} · {asset.width} × {asset.height}</p></div></div></td>
                <td className="px-3 py-3"><Badge tone="active">{category}</Badge>{proposalText ? <p className="mt-2 text-xs text-[var(--muted)]">源 PDF 文档</p> : <div className="mt-2 flex max-w-52 flex-wrap gap-1">{forms.slice(0, 3).map((label) => <Badge key={label}>{label}</Badge>)}{forms.length > 3 && <Badge>+{forms.length - 3}</Badge>}</div>}</td>
                <td className="px-3 py-3"><span className={`text-xs font-semibold ${quality === "severe" || quality === "unusable" ? "text-[#8d2924]" : "text-[var(--muted)]"}`}>{proposalText ? "PDF 全页预处理" : qualityNames[quality] || quality || "—"}</span>{(evaluation?.scoring?.caps?.length ?? 0) > 0 && <p className="mt-2 text-xs text-[#7d4308]">{evaluation?.scoring.caps.length} 项等级限制</p>}</td>
                <td className="px-3 py-3">{result === "proposal_review" ? <><Badge tone="danger">PDF 文档级人工复核</Badge><p className="mt-2 max-w-64 text-xs leading-5 text-[#8d2924]">{proposalReviewReason(evaluation)}</p></> : result === "proposal_incomplete" ? <><Badge tone="danger">PDF 结果不完整</Badge><p className="mt-2 text-xs text-[#8d2924]">整份源 PDF 尚未完成文档级评分</p></> : result === "out_of_scope" ? <><Badge>范围外</Badge><p className="mt-2 text-xs text-[var(--muted)]">范围判定后未生成美感等级</p></> : result === "invalid_contract" ? <><Badge tone="danger">规则异常</Badge><p className="mt-2 text-xs text-[#8d2924]">维度合同无法解析</p></> : result === "incomplete" ? <><Badge tone="danger">结果不完整</Badge><p className="mt-2 text-xs text-[#8d2924]">{ruleMode ? "规则判定" : "维度数据"} {completedDimensions}/{expectedDimensions.length}</p></> : <><div className="flex items-baseline gap-2"><strong className="font-data text-xl">{level || "—"}</strong><span className="font-data text-xs text-[var(--muted)]">{(evaluation?.final_score ?? evaluation?.score)?.toFixed(1) ?? "—"}</span></div>{proposalText && <p className="mt-1 text-xs font-semibold text-[#45620c]">PDF 文档级评分</p>}{ruleMode && <p className="mt-1 text-xs font-semibold text-[#45620c]">规则扣分 · {completedDimensions} 个维度</p>}{evaluation?.risk_review?.verdict === "downgrade" && <p className="mt-1 text-xs font-semibold text-[#7d4308]">高风险复核已降级</p>}{evaluation?.final_level !== evaluation?.level && <p className="mt-1 text-xs text-[var(--muted)]">模型 {evaluation?.level} · {evaluation?.score?.toFixed(1)}</p>}</>}</td>
                <td className="font-data px-3 py-3"><span className={evaluation?.confidence != null && evaluation.confidence < 0.7 ? "font-semibold text-[#8d2924]" : ""}>{nonScored ? "不适用" : evaluation?.confidence != null ? `${Math.round(evaluation.confidence * 100)}%` : "—"}</span>{evaluation?.needs_review && <p className="mt-2 text-xs text-[#7d4308]">需要复核</p>}</td>
                <td className="px-3 py-3"><div className="flex items-center gap-2"><Badge tone={samplingTone(asset.sampling.tier)}>{samplingNames[asset.sampling.tier]}</Badge><span className="font-data text-xs font-semibold">P{asset.sampling.priority}</span></div><p className="mt-2 max-w-48 text-xs leading-5 text-[var(--muted)]">{asset.sampling.reasons.slice(0, 2).map((reason) => reason.label).join("；")}</p></td>
                <td className="px-3 py-3"><Badge tone={statusTone(status)}>{statusNames[status]}</Badge>{evaluation?.human_review && <p className="mt-2 max-w-32 truncate text-xs text-[var(--muted)]">{evaluation.human_review.reviewer_name}</p>}</td>
                <td className="px-3 py-3"><p className="font-data max-w-44 truncate text-xs" title={evaluation?.versions.model || ""}>{evaluation?.versions.model || "—"}</p><p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">{evaluation?.versions.prompt ? `单提示词 ${evaluation.versions.prompt}` : `A ${evaluation?.versions.prompt_a || "—"}${evaluation?.versions.prompt_b ? ` · B ${evaluation.versions.prompt_b}` : ""}`}</p></td>
                <td className="font-data whitespace-nowrap px-3 py-3 text-xs text-[var(--muted)]">{new Date(evaluation.updated_at).toLocaleString("zh-CN")}</td>
                <td className="w-28 px-4 py-3 text-right"><Button asChild size="sm" variant="secondary"><Link to={detailUrl(evaluation.id, searchParams, stage, view)}><span>{evaluation.review_stage === "completed" || view === "model-evaluation" ? "查看" : "审核"}</span><ArrowRight /></Link></Button></td>
              </tr>
            })}</tbody>
          </table>
        ) : <div className="flex min-h-72 flex-col items-center justify-center px-6 text-center"><ImageSquare size={30} weight="light" /><h3 className="font-editorial mt-4 text-xl font-bold">没有符合条件的结果</h3><p className="mt-2 text-sm text-[var(--muted)]">调整筛选条件，或清空筛选查看全部评测记录。</p>{activeFilters > 0 && <Button className="mt-5" variant="secondary" onClick={clearFilters}><X />清空筛选</Button>}</div>}
      </div>
    </div>
  </>
}

function FilterSelect({ label, value, onChange, options, emptyLabel = "全部" }: { label: string; value: string; onChange: (value: string) => void; options: string[][]; emptyLabel?: string }) {
  return <label className="block min-w-0"><span className="mb-2 block text-xs font-semibold">{label}</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm focus-visible:border-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" value={value} onChange={(event) => onChange(event.target.value)}><option value="">{emptyLabel}</option>{options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}</select></label>
}
