import {
  Archive,
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle,
  CircleNotch,
  Clock,
  FileText,
  Images,
  Lightbulb,
  LockKey,
  Package,
  Play,
  ShieldCheck,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react"
import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import {
  buildPipelineReadiness,
  evaluationPackageApi,
  evaluationProductionApi,
  operatorSafeText,
  packageStatusMeta,
  productionStatusMeta,
  toOperatorError,
  type CreateEvaluationProductionRunInput,
  type OperatorError,
} from "@/lib/evaluation-packages"
import type {
  EvaluationCategoryProfile,
  EvaluationPackageDetail,
  EvaluationPackageSummary,
  EvaluationProductionRun,
  EvaluationProductionTimelineStep,
  MaterialPackage,
  User,
} from "@/lib/types"

const activeRunStatuses = new Set([
  "preparing", "queued", "evaluating", "first_review", "optimizing", "regressing",
  "awaiting_review", "approved", "blocked", "failed",
])

export function EvaluationPackagePipelinePage({ user }: { user: User }) {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [categoryKey, setCategoryKey] = useState("")
  const [materialPackageId, setMaterialPackageId] = useState(0)
  const [actionError, setActionError] = useState<OperatorError | null>(null)

  const categories = useQuery({
    queryKey: ["evaluation-categories"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
    retry: false,
  })
  const materialPackages = useQuery({
    queryKey: ["material-packages", "production-line"],
    queryFn: () => api<{ items: MaterialPackage[] }>("/api/material-packages?limit=500"),
    retry: false,
  })
  const runs = useQuery({
    queryKey: ["evaluation-production-runs"],
    queryFn: evaluationProductionApi.list,
    retry: false,
    refetchInterval: (query) => query.state.data?.items.some((item) => activeRunStatuses.has(item.status)) ? 4000 : false,
  })

  const activeCategories = useMemo(
    () => (categories.data?.items ?? []).filter((item) => item.status === "active"),
    [categories.data?.items],
  )
  const availablePackages = useMemo(
    () => (materialPackages.data?.items ?? []).filter((item) => !categoryKey || item.category_key === categoryKey),
    [categoryKey, materialPackages.data?.items],
  )
  useEffect(() => {
    if (!categoryKey && activeCategories.length) setCategoryKey(activeCategories[0].category_key)
  }, [activeCategories, categoryKey])
  useEffect(() => {
    if (!availablePackages.some((item) => item.id === materialPackageId)) setMaterialPackageId(availablePackages[0]?.id ?? 0)
  }, [availablePackages, materialPackageId])

  const selectedCategory = activeCategories.find((item) => item.category_key === categoryKey)
  const selectedPackage = availablePackages.find((item) => item.id === materialPackageId)
  const readiness = buildPipelineReadiness(selectedPackage, selectedCategory)
  const allReady = readiness.every((item) => item.ready)
  const requestedRunId = Number(searchParams.get("run"))
  const currentForPackage = (runs.data?.items ?? []).find(
    (item) => item.material_package_id === materialPackageId && activeRunStatuses.has(item.status),
  )
  const focusedRun = runs.data?.items.find((item) => item.id === requestedRunId)
    ?? currentForPackage
    ?? runs.data?.items[0]

  const createRun = useMutation({
    mutationFn: (payload: CreateEvaluationProductionRunInput) => evaluationProductionApi.create(payload),
    onSuccess: async (created) => {
      setActionError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["evaluation-production-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      ])
      setSearchParams({ run: String(created.id) }, { replace: true })
      toast.success("生产运行已开始；最终评测包只会在真实回归证据齐备后创建")
    },
    onError: (error) => setActionError(toOperatorError(error)),
  })

  const startRun = () => {
    if (!selectedPackage || !selectedCategory) return
    createRun.mutate({
      material_package_id: selectedPackage.id,
      category_key: selectedCategory.category_key,
      idempotency_key: `production:${selectedPackage.id}:${selectedCategory.category_key}:${crypto.randomUUID()}`,
    })
  }
  const essentialError = categories.error ?? materialPackages.error

  return (
    <>
      <PageHeader
        index="01.1"
        title="评测包生产线"
        description="选择素材包与类目队列后一键开始。生产过程持续追踪真实任务，回归完成后才冻结最终评测包进入二审。"
        actions={<Button asChild variant="secondary"><Link to="/workflow/materials/packages"><Images />导入或整理素材</Link></Button>}
      />
      <div className="mx-auto max-w-[1540px] space-y-8 px-5 py-8 md:px-8 lg:px-10">
        {actionError && (
          <OperatorErrorPanel
            error={actionError}
            onRetry={() => createRun.variables ? createRun.mutate(createRun.variables) : startRun()}
            onClose={() => setActionError(null)}
          />
        )}
        {runs.error && <OperatorErrorPanel error={toOperatorError(runs.error)} onRetry={() => runs.refetch()} />}

        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.6fr)]">
          <div className="bg-white px-5 py-6 md:px-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div><p className="text-xs font-semibold text-[var(--muted)]">开始一次可追溯生产</p><h2 className="font-editorial mt-2 text-2xl font-bold">选择来源和类目队列</h2></div>
              <Badge tone={allReady ? "success" : "warning"}>{allReady ? "可以开始" : "还需检查"}</Badge>
            </div>
            {essentialError ? (
              <div className="mt-6"><OperatorErrorPanel error={toOperatorError(essentialError)} compact onRetry={() => Promise.all([categories.refetch(), materialPackages.refetch()])} /></div>
            ) : (
              <div className="mt-6 grid gap-4 md:grid-cols-2">
                <label><span className="mb-2 block text-xs font-semibold">类目队列</span><select className="h-12 w-full border border-[var(--line-strong)] bg-white px-3 text-sm" value={categoryKey} onChange={(event) => { setCategoryKey(event.target.value); setMaterialPackageId(0) }}>{!activeCategories.length && <option value="">暂无已开启类目</option>}{activeCategories.map((item) => <option key={item.category_key} value={item.category_key}>{item.display_name}</option>)}</select></label>
                <label><span className="mb-2 block text-xs font-semibold">素材包</span><select className="h-12 w-full border border-[var(--line-strong)] bg-white px-3 text-sm" value={materialPackageId || ""} onChange={(event) => setMaterialPackageId(Number(event.target.value))}>{!availablePackages.length && <option value="">当前类目暂无素材包</option>}{availablePackages.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.active_asset_count} 份</option>)}</select></label>
              </div>
            )}
            <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-5">
              {currentForPackage ? (
                <Button onClick={() => setSearchParams({ run: String(currentForPackage.id) }, { replace: true })}>查看当前运行<ArrowRight /></Button>
              ) : (
                <Button disabled={!allReady || createRun.isPending || Boolean(essentialError)} onClick={startRun}>{createRun.isPending ? <CircleNotch className="animate-spin" /> : <Play weight="fill" />}{createRun.isPending ? "正在开始" : "开始评测"}</Button>
              )}
              <p className="text-xs text-[var(--muted)]">离开页面不会中断任务；重复提交也不会重复建任务。</p>
            </div>
          </div>
          <aside className="bg-[#f7fadf] px-5 py-6 md:px-6">
            <div className="flex items-center gap-2"><Lightbulb weight="fill" /><p className="text-xs font-bold">AI 下一步</p></div>
            <p className="mt-4 text-lg font-semibold leading-8">{operatorSafeText(focusedRun?.ai_next_step, allReady ? "条件已满足，可以开始评测。" : "请先完成左侧就绪检查。")}</p>
            <p className="mt-4 border-t border-[#dfe7b8] pt-4 text-xs leading-5 text-[#596047]">系统建议不会替代一审、二审或人工发布决定。</p>
          </aside>
        </section>

        <section>
          <SectionHeading title="开始前检查" description="普通审核员只需确认素材、类目和冻结方案三项。" />
          <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">
            {readiness.map((item, index) => <div key={item.key} className="grid gap-3 px-5 py-4 sm:grid-cols-[36px_minmax(0,1fr)_auto] sm:items-center"><span className={`font-data flex size-8 items-center justify-center border text-sm font-bold ${item.ready ? "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]" : "border-[#e5c9a7] bg-[#fff6e9] text-[#7d4308]"}`}>{item.ready ? <Check weight="bold" /> : index + 1}</span><div><p className="text-sm font-semibold">{item.label}</p><p className="mt-1 text-xs text-[var(--muted)]">{item.description}</p></div>{!item.ready && item.action_href && <Button asChild size="sm" variant="secondary"><Link to={item.action_href}>{item.action_label}<ArrowRight /></Link></Button>}</div>)}
          </div>
        </section>

        {focusedRun && (
          <section className="space-y-5">
            <div className="flex flex-wrap items-end justify-between gap-4"><SectionHeading title="当前运行" description={`${focusedRun.material_package.name} · ${focusedRun.category.name}`} /><Button size="sm" variant="ghost" onClick={() => runs.refetch()}><ArrowClockwise />刷新事实</Button></div>
            <ProductionTimeline steps={focusedRun.timeline} />
            <div className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-5">
              <Metric label="状态" value={productionStatusMeta[focusedRun.status].label} />
              <Metric label="总体进度" value={`${focusedRun.progress.percent}%`} />
              <Metric label="评测完成" value={`${focusedRun.job_counts.completed}/${focusedRun.job_counts.total}`} />
              <Metric label="待一审" value={String(focusedRun.pending_first_review_count)} />
              <Metric label="审计修订" value={String(focusedRun.audit.revision)} />
            </div>
            <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">
              {focusedRun.blockers.length ? focusedRun.blockers.map((blocker) => <div key={blocker.code} className="grid gap-3 px-5 py-4 sm:grid-cols-[24px_minmax(0,1fr)_auto] sm:items-center"><WarningCircle className="text-[#a85a0a]" /><div><p className="text-sm font-semibold">{operatorSafeText(blocker.title, "需要处理")}</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">{operatorSafeText(blocker.message, "请打开对应页面处理后刷新。")}</p></div><Button asChild size="sm" variant="secondary"><Link to={blocker.fix.href}>{operatorSafeText(blocker.fix.label, "直接处理")}<ArrowRight /></Link></Button></div>) : <div className="flex items-start gap-3 px-5 py-5"><CheckCircle className="text-[#2f6f48]" weight="fill" /><div><p className="text-sm font-semibold">当前没有人工阻塞</p><p className="mt-1 text-xs text-[var(--muted)]">系统会按现有事实继续推进。</p></div></div>}
            </div>
          </section>
        )}

        <section>
          <div className="flex flex-wrap items-end justify-between gap-4"><SectionHeading title="最近生产运行" description="这里展示生产过程；二审最终包在独立列表中查看。" /><Badge>{user.is_admin ? "管理员" : "审核员"}</Badge></div>
          <ProductionRunRows items={runs.data?.items ?? []} loading={runs.isLoading} onFocus={(id) => setSearchParams({ run: String(id) }, { replace: true })} />
        </section>
      </div>
    </>
  )
}

export function EvaluationPackageReviewListPage() {
  const [showHistory, setShowHistory] = useState(false)
  const packages = useQuery({ queryKey: ["evaluation-packages"], queryFn: evaluationPackageApi.list, retry: false, refetchInterval: 5000 })
  const items = packages.data?.items ?? []
  const pending = items.filter((item) => item.status === "awaiting_review")
  const history = items.filter((item) => ["approved", "rejected", "published", "archived"].includes(item.status))
  return (
    <>
      <PageHeader index="03.1" title="二审评测包" description="这里只展示已经冻结的最终评测包；生产中的任务不会伪装成二审对象。" actions={<Button variant="secondary" onClick={() => packages.refetch()}><ArrowClockwise />刷新队列</Button>} />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <div className="mb-6 grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-3"><QueueMetric label="等待二审" value={pending.length} active={!showHistory} onClick={() => setShowHistory(false)} /><QueueMetric label="已有结论" value={history.length} active={showHistory} onClick={() => setShowHistory(true)} /><div className="bg-[#f7fadf] px-5 py-4"><p className="text-xs font-semibold text-[var(--muted)]">发布门禁</p><p className="mt-2 text-sm font-semibold">批准与发布始终是两个动作</p></div></div>
        {packages.error ? <OperatorErrorPanel error={toOperatorError(packages.error)} onRetry={() => packages.refetch()} /> : <EvaluationPackageRows items={showHistory ? history : pending} loading={packages.isLoading} />}
      </div>
    </>
  )
}

type ReviewAction = "approve" | "reject" | "publish" | "archive"

export function EvaluationPackageDetailPage() {
  const { packageId } = useParams()
  const id = Number(packageId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [note, setNote] = useState("")
  const [actionError, setActionError] = useState<OperatorError | null>(null)
  const detail = useQuery({ queryKey: ["evaluation-package", id], queryFn: () => evaluationPackageApi.get(id), enabled: Number.isInteger(id) && id > 0, retry: false, refetchInterval: (query) => query.state.data?.status === "validating" ? 4000 : false })
  const action = useMutation({
    mutationFn: ({ type, value }: { type: ReviewAction; value: string }) => type === "approve" ? evaluationPackageApi.approve(id, value) : type === "reject" ? evaluationPackageApi.reject(id, value) : type === "publish" ? evaluationPackageApi.publish(id, value) : evaluationPackageApi.archive(id, value),
    onSuccess: async (updated, variables) => {
      setNote("")
      setActionError(null)
      queryClient.setQueryData(["evaluation-package", id], updated)
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["evaluation-packages"] }), queryClient.invalidateQueries({ queryKey: ["evaluation-production-runs"] })])
      toast.success({ approve: "评测包已批准，仍需明确发布", reject: "评测包已拒绝", publish: "评测包已发布", archive: "评测包已归档" }[variables.type])
    },
    onError: (error) => setActionError(toOperatorError(error)),
  })
  if (!Number.isInteger(id) || id <= 0) return <InvalidPackage onBack={() => navigate("/workflow/releases/packages", { replace: true })} />
  if (detail.isLoading) return <DetailLoading />
  if (detail.error || !detail.data) return <><PageHeader index="03.2" title="二审评测包详情" description="读取冻结证据。" /><div className="mx-auto max-w-[1180px] px-5 py-10"><OperatorErrorPanel error={toOperatorError(detail.error)} onRetry={() => detail.refetch()} /></div></>
  const item = detail.data
  const status = packageStatusMeta(item.status)
  const canReview = item.status === "awaiting_review"
  const canPublish = item.status === "approved"
  const canArchive = ["approved", "rejected", "published"].includes(item.status)
  const noteReady = Boolean(note.trim())
  return (
    <>
      <PageHeader index="03.2" title={`二审评测包 · ${item.category_key}`} description={`${status.label} · 创建于 ${formatTime(item.created_at)}`} actions={<><Button asChild variant="secondary"><Link to="/workflow/releases/packages"><ArrowLeft />返回二审队列</Link></Button><Button variant="secondary" onClick={() => detail.refetch()}><ArrowClockwise />刷新</Button></>} />
      <div className="mx-auto max-w-[1320px] space-y-8 px-5 py-8 md:px-8 lg:px-10">
        {actionError && <OperatorErrorPanel error={actionError} onRetry={() => detail.refetch()} onClose={() => setActionError(null)} />}
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-4"><Metric label="状态" value={status.label} /><Metric label="评测模式" value={item.prompts.mode === "single" ? "单次完整评测" : "A/B 两段评测"} /><Metric label="黄金样本" value={String(item.golden_sample_set.item_count)} /><Metric label="回归结果" value={regressionLabel(item.regression.recommendation)} /></section>

        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-2">
          <div className="bg-[#f7fadf] px-5 py-6"><div className="flex items-center gap-2"><Lightbulb weight="fill" /><h2 className="text-sm font-bold">AI 建议</h2></div><Badge className="mt-4" tone={item.ai.recommendation === "pass" ? "success" : item.ai.recommendation === "fail" ? "danger" : "warning"}>{regressionLabel(item.ai.recommendation)}</Badge><p className="mt-3 text-sm leading-6">{item.ai.change_summary || item.change_summary || "没有补充变更说明"}</p><p className="mt-4 text-xs text-[var(--muted)]">AI 不会自动批准或发布。</p></div>
          <div className="bg-white px-5 py-6"><h2 className="font-editorial text-2xl font-bold">冻结身份</h2><p className="mt-3 text-sm leading-6 text-[var(--muted)]">清单校验通过：{item.manifest_hash_valid ? "是" : "否"}</p><p className="font-data mt-3 break-all text-xs">{item.canonical_manifest_hash}</p></div>
        </section>

        <section><SectionHeading title="完整提示词" description="展示最终包冻结的完整 A/B 或单提示词内容，不展示内部编号。" /><div className="space-y-5"><PromptSnapshot title={item.prompts.mode === "single" ? "单次完整评测" : "A 阶段 · 分类与画质"} prompt={item.prompts.a} />{item.prompts.b && <PromptSnapshot title="B 阶段 · 美感维度" prompt={item.prompts.b} />}</div></section>

        <ManifestSection title="维度方案" description="冻结的维度定义、路由与评测配置。"><EvidenceObject value={item.dimensions} /></ManifestSection>

        <section><SectionHeading title="黄金样本集" description={`${item.golden_sample_set.name} · 可判断 ${item.golden_sample_set.judgable_item_count}/${item.golden_sample_set.item_count}`} /><div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">{item.golden_sample_set.items.map((sample) => <div key={sample.sample_item_id} className="grid gap-4 px-5 py-4 sm:grid-cols-[72px_minmax(0,1fr)_auto] sm:items-center"><img src={sample.image_url} alt="" className="size-16 border border-[var(--line)] object-cover" /><div className="min-w-0"><p className="file-name truncate text-sm">{sample.asset_name}</p><p className="mt-1 text-xs text-[var(--muted)]">人工真值 {sample.expected_level || "未记录"} · {sample.role || "未分组"}</p></div><details><summary className="cursor-pointer text-xs font-bold">查看真值</summary><div className="mt-3 max-w-xl"><EvidenceObject value={sample.truth} compact /></div></details></div>)}</div></section>

        <ManifestSection title="回归证据" description={`${item.regression.completed}/${item.regression.total} 已完成 · ${regressionLabel(item.regression.recommendation)}`}><div className="grid gap-px bg-[var(--line)] sm:grid-cols-3"><Metric label="通过" value={String(item.regression.passed)} /><Metric label="失败" value={String(item.regression.failed)} /><Metric label="状态" value={item.regression.status} /></div><EvidenceObject value={{ metrics: item.regression.metrics, summary: item.regression.summary, metric_rules: item.regression.metric_rules, items: item.regression.items }} /></ManifestSection>

        <ManifestSection title="自动改进记录" description="候选来源、关联回归和不可自动发布声明。">{item.automation ? <EvidenceObject value={hideInternalPromptIds(item.automation)} /> : <MissingEvidence text="本评测包未关联自动改进运行。" />}</ManifestSection>
        <ManifestSection title="指标快照" description="回归指标、摘要与版本指标快照。"><EvidenceObject value={item.metrics} /></ManifestSection>
        <ManifestSection title="身份与版本" description="模型、评分规则、引擎和候选策略身份。" advanced><EvidenceObject value={item.identity} /></ManifestSection>

        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="border-b border-[var(--line)] px-5 py-5"><div className="flex items-center gap-2"><ShieldCheck /><h2 className="font-editorial text-2xl font-bold">二审与发布</h2></div><p className="mt-2 text-sm text-[var(--muted)]">批准不会自动发布；拒绝、发布和归档都保留冻结证据。</p></div>
          <div className="grid gap-4 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end"><label><span className="mb-2 block text-xs font-semibold">{canArchive && !canReview && !canPublish ? "归档原因" : "决定说明"}</span><Textarea rows={4} maxLength={4000} value={note} onChange={(event) => setNote(event.target.value)} /></label><div className="flex flex-wrap gap-2">{canReview && <><Button variant="danger" disabled={!noteReady || action.isPending} onClick={() => action.mutate({ type: "reject", value: note.trim() })}>{action.variables?.type === "reject" && action.isPending ? <CircleNotch className="animate-spin" /> : <XCircle />}拒绝</Button><Button disabled={!noteReady || action.isPending} onClick={() => action.mutate({ type: "approve", value: note.trim() })}>{action.variables?.type === "approve" && action.isPending ? <CircleNotch className="animate-spin" /> : <CheckCircle />}批准</Button></>}{canPublish && <Button disabled={!noteReady || action.isPending} onClick={() => action.mutate({ type: "publish", value: note.trim() })}><Play weight="fill" />明确发布</Button>}{canArchive && <Button variant="secondary" disabled={!noteReady || action.isPending} onClick={() => action.mutate({ type: "archive", value: note.trim() })}><Archive />归档</Button>}{!canReview && !canPublish && !canArchive && <Badge tone={status.tone}>{status.label}</Badge>}</div></div>
          {item.review.decision && <div className="border-t border-[var(--line)] bg-[#fafbf8] px-5 py-4 text-xs leading-5 text-[var(--muted)]">二审结论：{item.review.decision === "approved" ? "批准" : "拒绝"} · {item.review.reviewed_by || "未记录"} · {formatTime(item.review.reviewed_at)}<br />{item.review.note}</div>}
        </section>
      </div>
    </>
  )
}

function PromptSnapshot({ title, prompt }: { title: string; prompt: EvaluationPackageDetail["prompts"]["a"] }) {
  return <article className="border-y border-[var(--line-strong)] bg-white"><div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4"><div><p className="text-xs text-[var(--muted)]">{title}</p><h3 className="mt-1 text-lg font-bold">{prompt.name}</h3></div><Badge tone="active">{prompt.version}</Badge></div><div className="grid gap-px bg-[var(--line)] lg:grid-cols-2"><PromptBlock label="系统指令" value={prompt.system_prompt} /><PromptBlock label="用户指令" value={prompt.user_prompt} /></div></article>
}

function PromptBlock({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 bg-white"><p className="px-5 pt-4 text-xs font-semibold text-[var(--muted)]">{label}</p><pre className="max-h-[520px] overflow-auto whitespace-pre-wrap break-words px-5 py-4 font-data text-xs leading-6">{value || "未提供"}</pre></div>
}

function ManifestSection({ title, description, children, advanced = false }: { title: string; description: string; children: React.ReactNode; advanced?: boolean }) {
  if (advanced) return <details className="border-y border-[var(--line-strong)] bg-white"><summary className="flex cursor-pointer items-center gap-2 px-5 py-4 text-sm font-bold"><LockKey />{title}</summary><div className="border-t border-[var(--line)] px-5 py-5"><p className="mb-4 text-xs text-[var(--muted)]">{description}</p>{children}</div></details>
  return <section><SectionHeading title={title} description={description} /><div className="border-y border-[var(--line-strong)] bg-white p-5">{children}</div></section>
}

function EvidenceObject({ value, compact = false }: { value: unknown; compact?: boolean }) {
  return <pre className={`${compact ? "max-h-56" : "max-h-[680px]"} overflow-auto whitespace-pre-wrap break-words bg-[#fafbf8] p-4 font-data text-xs leading-6`}>{JSON.stringify(value, null, 2)}</pre>
}

function ProductionRunRows({ items, loading, onFocus }: { items: EvaluationProductionRun[]; loading: boolean; onFocus: (id: number) => void }) {
  if (loading) return <div className="h-48 animate-pulse border-y border-[var(--line-strong)] bg-white" />
  if (!items.length) return <EmptyState title="还没有生产运行" />
  return <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">{items.map((item) => { const meta = productionStatusMeta[item.status]; return <button key={item.id} type="button" className="grid w-full gap-4 px-5 py-4 text-left hover:bg-[#f8f9f6] xl:grid-cols-[minmax(220px,1fr)_minmax(180px,0.8fr)_180px_130px_auto] xl:items-center" onClick={() => onFocus(item.id)}><div className="min-w-0"><p className="truncate text-sm font-semibold">{item.material_package.name}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">运行 #{item.id} · {formatTime(item.updated_at)}</p></div><div><p className="text-sm">{item.category.name}</p><p className="mt-1 text-xs text-[var(--muted)]">{item.job_counts.total} 份素材</p></div><div><div className="h-1.5 bg-[#eef1eb]"><div className="h-full bg-primary" style={{ width: `${item.progress.percent}%` }} /></div><p className="mt-2 text-xs text-[var(--muted)]">{item.progress.current_step}</p></div><Badge tone={meta.tone}>{meta.label}</Badge><ArrowRight /></button>})}</div>
}

function EvaluationPackageRows({ items, loading }: { items: EvaluationPackageSummary[]; loading: boolean }) {
  if (loading) return <div className="h-48 animate-pulse border-y border-[var(--line-strong)] bg-white" />
  if (!items.length) return <EmptyState title="当前没有评测包" />
  return <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">{items.map((item) => { const meta = packageStatusMeta(item.status); return <Link key={item.id} to={`/workflow/releases/packages/${item.id}`} className="grid gap-4 px-5 py-4 hover:bg-[#f8f9f6] lg:grid-cols-[minmax(220px,1fr)_minmax(180px,0.8fr)_140px_auto] lg:items-center"><div><p className="text-sm font-semibold">评测包 #{item.id}</p><p className="font-data mt-1 truncate text-xs text-[var(--muted)]">{item.package_key}</p></div><div><p className="text-sm">{item.category_key}</p><p className="mt-1 text-xs text-[var(--muted)]">{item.prompt_mode === "single" ? "单次完整评测" : "A/B 两段评测"}</p></div><Badge tone={meta.tone}>{meta.label}</Badge><ArrowRight /></Link>})}</div>
}

function ProductionTimeline({ steps }: { steps: EvaluationProductionTimelineStep[] }) {
  return <ol className="grid overflow-hidden border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-7">{steps.map((step, index) => <li key={step.key} className={`min-w-0 border-b border-r border-[var(--line)] px-4 py-4 lg:border-b-0 ${step.status === "current" ? "bg-[#f7fadf]" : step.status === "blocked" || step.status === "failed" ? "bg-[#fff6e9]" : "bg-white"}`}><div className="flex items-center gap-2"><span className={`font-data flex size-7 shrink-0 items-center justify-center border text-xs font-bold ${step.status === "completed" ? "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]" : step.status === "current" ? "border-[#8da91e] bg-primary" : "border-[var(--line-strong)] text-[var(--muted)]"}`}>{step.status === "completed" ? <Check weight="bold" /> : index + 1}</span><p className="text-sm font-semibold">{step.label}</p></div>{step.completed_at && <p className="mt-2 text-[11px] text-[var(--muted)]">{formatTime(step.completed_at)}</p>}</li>)}</ol>
}

function OperatorErrorPanel({ error, compact = false, onRetry, onClose }: { error: OperatorError; compact?: boolean; onRetry?: () => void; onClose?: () => void }) {
  return <div className={`border-y border-[#e8c1bd] bg-[#fff5f3] text-[#7d201a] ${compact ? "px-4 py-3" : "px-5 py-5"}`} role="alert"><div className="flex flex-wrap items-start justify-between gap-4"><div className="flex gap-3"><WarningCircle className="mt-0.5 shrink-0" weight="fill" /><div><p className="text-sm font-bold">{error.title}</p><p className="mt-1 text-xs leading-5">{error.message}</p></div></div><div className="flex gap-2">{onRetry && error.retryable && <Button size="sm" variant="secondary" onClick={onRetry}><ArrowClockwise />重试</Button>}{onClose && <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button>}</div></div></div>
}

function SectionHeading({ title, description }: { title: string; description: string }) { return <div className="mb-4"><h2 className="font-editorial text-2xl font-bold">{title}</h2><p className="mt-1 text-sm leading-6 text-[var(--muted)]">{description}</p></div> }
function Metric({ label, value }: { label: string; value: string }) { return <div className="min-w-0 bg-white px-5 py-4"><p className="text-xs font-semibold text-[var(--muted)]">{label}</p><p className="font-data mt-2 truncate text-xl font-bold" title={value}>{value}</p></div> }
function QueueMetric({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) { return <button type="button" className={`px-5 py-4 text-left ${active ? "bg-primary" : "bg-white"}`} onClick={onClick}><p className="text-xs font-semibold text-[var(--muted)]">{label}</p><p className="font-data mt-2 text-2xl font-bold">{value}</p></button> }
function MissingEvidence({ text }: { text: string }) { return <div className="flex items-start gap-3 bg-[#fff9e9] px-4 py-4 text-xs text-[#6f5513]"><WarningCircle /><p>{text}</p></div> }
function EmptyState({ title }: { title: string }) { return <div className="flex min-h-48 flex-col items-center justify-center border-y border-[var(--line-strong)] bg-white"><Package size={30} weight="light" /><h3 className="font-editorial mt-4 text-xl font-bold">{title}</h3></div> }
function DetailLoading() { return <><PageHeader index="03.2" title="正在打开评测包" description="读取冻结证据。" /><div className="mx-auto max-w-[1320px] px-5 py-8"><div className="h-72 animate-pulse bg-white" /></div></> }
function InvalidPackage({ onBack }: { onBack: () => void }) { return <><PageHeader index="03.2" title="无法打开评测包" description="链接中的编号无效。" /><div className="mx-auto flex min-h-[50dvh] flex-col items-center justify-center"><FileText size={32} /><Button className="mt-5" onClick={onBack}><ArrowLeft />返回二审队列</Button></div></> }
function regressionLabel(value: string) { return value === "pass" ? "建议通过" : value === "fail" ? "建议拒绝" : "尚未形成" }
function formatTime(value: string | null | undefined) { if (!value) return "时间未记录"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN") }
function hideInternalPromptIds(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(hideInternalPromptIds)
  if (!value || typeof value !== "object") return value
  return Object.fromEntries(Object.entries(value).filter(([key]) => !["prompt_id", "prompt_ids", "prompt_a_id", "prompt_b_id"].includes(key)).map(([key, item]) => [key, hideInternalPromptIds(item)]))
}
