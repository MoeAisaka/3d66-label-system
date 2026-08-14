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
  Prohibit,
  ShieldCheck,
  ShieldWarning,
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

// `crypto.randomUUID()` is unavailable in some HTTP (non-secure) LAN contexts.
// Idempotency keys do not need to be cryptographically secret, but they must be
// unique enough to distinguish retries from separate production runs.
function createIdempotencyToken(): string {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID()

  if (typeof cryptoApi?.getRandomValues === "function") {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }

  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

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
    queryFn: () => evaluationProductionApi.list(),
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
      idempotency_key: `production:${selectedPackage.id}:${selectedCategory.category_key}:${createIdempotencyToken()}`,
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

        <OperatorJourney current={journeyStep(focusedRun, Boolean((materialPackages.data?.items ?? []).length))} />

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
      <PageHeader index="03.1" title="二审评测包" description="这里只展示已经冻结的最终评测包。按变更、维度、黄金集、回归失败项和风险顺序完成一次整体判断。" actions={<Button variant="secondary" onClick={() => packages.refetch()}><ArrowClockwise />刷新队列</Button>} />
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

        <ReviewDecisionBrief item={item} />

        <section><SectionHeading title="完整提示词" description="展示最终包冻结的完整 A/B 或单提示词内容，不展示内部编号。" /><div className="space-y-5"><PromptSnapshot title={item.prompts.mode === "single" ? "单次完整评测" : "A 阶段 · 分类与画质"} prompt={item.prompts.a} />{item.prompts.b && <PromptSnapshot title="B 阶段 · 美感维度" prompt={item.prompts.b} />}</div></section>

        <DimensionPlan item={item} />

        <GoldenSampleEvidence item={item} />

        <RegressionEvidence item={item} />

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

const operatorJourneySteps = [
  { label: "导入素材", note: "生成可追溯素材包", to: "/workflow/materials/packages" },
  { label: "选择素材包与类目", note: "确认本次评测来源", to: "/workflow/production-line" },
  { label: "开始评测", note: "系统自动运行并留痕", to: "/workflow/production-line" },
  { label: "处理纠偏", note: "只处理需要人工判断的结果", to: "/workflow/review/low-confidence" },
  { label: "二审评测包", note: "整体判断冻结方案与证据", to: "/workflow/releases/packages" },
] as const

function journeyStep(run: EvaluationProductionRun | undefined, hasMaterials: boolean) {
  if (!hasMaterials) return 1
  if (!run) return 2
  if (["awaiting_review", "approved", "rejected", "published", "archived"].includes(run.status)) return 5
  if (run.status === "first_review" || run.current_stage.includes("review")) return 4
  return 3
}

function OperatorJourney({ current }: { current: number }) {
  return (
    <section aria-label="一线审核主流程">
      <SectionHeading title="本次工作到哪一步" description="默认只沿这条主线推进；模型、预算、协议和执行器由管理员在高级设置中维护。" />
      <ol className="grid overflow-hidden border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-5">
        {operatorJourneySteps.map((step, index) => {
          const number = index + 1
          const completed = number < current
          const active = number === current
          return (
            <li key={step.label} className={`min-w-0 border-b border-r border-[var(--line)] lg:border-b-0 ${active ? "bg-[#f7fadf]" : "bg-white"}`}>
              <Link className="block min-h-28 px-4 py-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary" to={step.to}>
                <div className="flex items-center gap-2">
                  <span className={`font-data flex size-7 shrink-0 items-center justify-center border text-xs font-bold ${completed ? "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]" : active ? "border-[#8da91e] bg-primary" : "border-[var(--line-strong)] text-[var(--muted)]"}`}>
                    {completed ? <Check weight="bold" /> : number}
                  </span>
                  <p className="text-sm font-bold">{step.label}</p>
                </div>
                <p className="mt-3 text-xs leading-5 text-[var(--muted)]">{step.note}</p>
              </Link>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function ReviewDecisionBrief({ item }: { item: EvaluationPackageDetail }) {
  const blockers: string[] = []
  const risks: string[] = []
  if (!item.manifest_hash_valid) blockers.push("冻结清单校验未通过，不能据此作出发布决定")
  if (!item.regression.terminal || item.regression.completed < item.regression.total) blockers.push(`黄金集回归尚未完成（${item.regression.completed}/${item.regression.total}）`)
  if (!item.golden_sample_set.item_count) blockers.push("评测包没有冻结黄金样本")
  if (item.regression.recommendation === "fail") risks.push("回归门禁建议拒绝，必须逐项检查失败样本")
  if (item.regression.failed > 0) risks.push(`${item.regression.failed} 个样本未通过回归对照`)
  if (item.golden_sample_set.judgable_item_count < item.golden_sample_set.item_count) risks.push(`${item.golden_sample_set.item_count - item.golden_sample_set.judgable_item_count} 个黄金样本缺少可判断真值`)
  if (!item.prompts.a.change_note?.trim() && !item.prompts.b?.change_note?.trim() && !item.change_summary.trim()) risks.push("提示词版本没有填写明确的变更说明")

  const nextStep = blockers.length
    ? "先处理阻塞项，不要批准。刷新后确认冻结证据完整。"
    : item.regression.recommendation === "fail" || item.regression.failed > 0
      ? "先查看下方失败项和基线—候选差异；无法解释或不可接受时拒绝本包。"
      : item.status === "approved"
        ? "本包已通过二审；如确认要进入正式标签，再单独执行发布。"
        : "依次核对提示词变更、维度开关、黄金集组成和逐样本对照，再填写二审决定。"

  return (
    <section>
      <SectionHeading title="二审判断摘要" description="先看阻塞和风险，再展开完整证据。AI 只给下一步建议，不替你作决定。" />
      <div className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-3">
        <div className="min-w-0 bg-[#f7fadf] px-5 py-5">
          <div className="flex items-center gap-2"><Lightbulb weight="fill" /><h3 className="text-sm font-bold">AI 下一步建议</h3></div>
          <p className="mt-3 text-sm font-semibold leading-6">{nextStep}</p>
          {(item.ai.change_summary || item.change_summary) && <p className="mt-3 border-t border-[#dfe7b8] pt-3 text-xs leading-5 text-[#596047]">变更摘要：{item.ai.change_summary || item.change_summary}</p>}
        </div>
        <div className={`min-w-0 px-5 py-5 ${blockers.length ? "bg-[#fff5f3]" : "bg-white"}`}>
          <div className="flex items-center gap-2"><Prohibit /><h3 className="text-sm font-bold">阻塞</h3><Badge tone={blockers.length ? "danger" : "success"}>{blockers.length ? `${blockers.length} 项` : "无"}</Badge></div>
          {blockers.length ? <ul className="mt-3 space-y-2 text-xs leading-5">{blockers.map((text) => <li key={text}>· {text}</li>)}</ul> : <p className="mt-3 text-xs leading-5 text-[var(--muted)]">冻结清单和回归完成度满足二审前置条件。</p>}
        </div>
        <div className={`min-w-0 px-5 py-5 ${risks.length ? "bg-[#fff9e9]" : "bg-white"}`}>
          <div className="flex items-center gap-2"><ShieldWarning /><h3 className="text-sm font-bold">风险</h3><Badge tone={risks.length ? "warning" : "success"}>{risks.length ? `${risks.length} 项` : "低"}</Badge></div>
          {risks.length ? <ul className="mt-3 space-y-2 text-xs leading-5">{risks.map((text) => <li key={text}>· {text}</li>)}</ul> : <p className="mt-3 text-xs leading-5 text-[var(--muted)]">当前未发现由冻结证据直接暴露的风险项。</p>}
        </div>
      </div>
    </section>
  )
}

type JsonRecord = Record<string, unknown>

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : null
}

function asRecordArray(value: unknown) {
  return Array.isArray(value) ? value.map(asRecord).filter((item): item is JsonRecord => Boolean(item)) : []
}

function findDimensionDefinition(value: unknown, depth = 0): JsonRecord | null {
  if (depth > 5) return null
  const record = asRecord(value)
  if (!record) return null
  if (asRecordArray(record.dimensions).length) return record
  for (const child of Object.values(record)) {
    const found = findDimensionDefinition(child, depth + 1)
    if (found) return found
  }
  return null
}

function DimensionPlan({ item }: { item: EvaluationPackageDetail }) {
  const dimensions = asRecord(item.dimensions) ?? {}
  const explicitSchema = asRecord(dimensions.explicit_schema)
  const definition = findDimensionDefinition(explicitSchema?.definition) ?? findDimensionDefinition(dimensions)
  const rows = asRecordArray(definition?.dimensions)
  const profile = asRecord(item.category.profile)
  const pipeline = asRecord(profile?.pipeline_config)
  const switchConfig = asRecord(pipeline?.dimensions)
  const enabled = switchConfig?.enabled !== false
  const mode = switchConfig?.mode === "selected" ? "仅重点维度" : "全部维度"
  const route = asRecord(dimensions.explicit_route_policy)
  const schemaVersion = String(explicitSchema?.version ?? profile?.dimension_schema_version ?? dimensions.resolved_schema_contract_version ?? "未记录")
  const routeVersion = String(route?.version ?? "跟随冻结方案")
  return (
    <section>
      <SectionHeading title="维度规则与开关" description="二审查看本包真正冻结的规则，不受管理员之后修改影响。" />
      <div className="border-y border-[var(--line-strong)] bg-white">
        <div className="grid gap-px bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="维度开关" value={enabled ? "已启用" : "已关闭"} />
          <Metric label="评测范围" value={mode} />
          <Metric label="维度版本" value={schemaVersion} />
          <Metric label="路由版本" value={routeVersion} />
        </div>
        {rows.length ? (
          <div className="divide-y divide-[var(--line)]">
            {rows.map((dimension, index) => {
              const anchors = asRecord(dimension.anchors)
              const weight = typeof dimension.weight === "number" ? `${Math.round(dimension.weight * 100)}%` : "未设权重"
              return (
                <div key={String(dimension.key ?? index)} className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(180px,0.8fr)_110px_minmax(0,1.4fr)] md:items-start">
                  <div><p className="text-sm font-bold">{String(dimension.label ?? dimension.key ?? `维度 ${index + 1}`)}</p><p className="font-data mt-1 break-all text-[11px] text-[var(--muted)]">{String(dimension.key ?? "")}</p></div>
                  <div className="flex flex-wrap gap-2"><Badge tone={dimension.required === false ? "neutral" : "success"}>{dimension.required === false ? "可选" : "必评"}</Badge><Badge>{weight}</Badge></div>
                  <p className="text-xs leading-5 text-[var(--muted)]">中位标准：{String(anchors?.["3"] ?? "未记录等级锚点")}</p>
                </div>
              )
            })}
          </div>
        ) : <MissingEvidence text="本包保留了维度身份，但未提供可逐项展示的冻结定义。" />}
        <details className="border-t border-[var(--line)]"><summary className="cursor-pointer px-5 py-4 text-xs font-bold">查看维度冻结原始记录</summary><div className="border-t border-[var(--line)] p-5"><EvidenceObject value={item.dimensions} /></div></details>
      </div>
    </section>
  )
}

function GoldenSampleEvidence({ item }: { item: EvaluationPackageDetail }) {
  const roles = item.golden_sample_set.items.reduce<Record<string, number>>((counts, sample) => {
    const key = sample.role || "unassigned"
    counts[key] = (counts[key] ?? 0) + 1
    return counts
  }, {})
  return (
    <section>
      <SectionHeading title="黄金集组成与样本" description={`${item.golden_sample_set.name} · ${item.golden_sample_set.item_count} 个样本 · 可判断 ${item.golden_sample_set.judgable_item_count}/${item.golden_sample_set.item_count}`} />
      <div className="mb-4 flex flex-wrap gap-2">{Object.entries(roles).map(([role, count]) => <Badge key={role}>{roleLabel(role)} {count}</Badge>)}</div>
      <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">
        {item.golden_sample_set.items.map((sample) => <div key={sample.sample_item_id} className="grid min-w-0 gap-4 px-5 py-4 sm:grid-cols-[72px_minmax(0,1fr)] lg:grid-cols-[72px_minmax(0,1fr)_minmax(220px,0.6fr)] lg:items-center"><img src={sample.image_url} alt={sample.asset_name} className="size-16 border border-[var(--line)] bg-white object-cover" /><div className="min-w-0"><p className="file-name break-all text-sm">{sample.asset_name}</p><p className="mt-1 text-xs text-[var(--muted)]">人工真值 {sample.expected_level || "未记录"} · {roleLabel(sample.role || "unassigned")} · 真值修订 {sample.truth_revision}</p></div><details className="min-w-0 sm:col-start-2 lg:col-start-auto"><summary className="cursor-pointer text-xs font-bold">查看样本真值详情</summary><div className="mt-3 max-w-full"><EvidenceObject value={sample.truth} compact /></div></details></div>)}
      </div>
    </section>
  )
}

function RegressionEvidence({ item }: { item: EvaluationPackageDetail }) {
  const summary = asRecord(item.regression.summary)
  const rules = asRecord(item.regression.metric_rules)
  const thresholds = asRecord(rules?.thresholds)
  const gates = asRecordArray(summary?.gate_checks)
  const sortedItems = [...item.regression.items].sort((left, right) => Number(right.passed === false) - Number(left.passed === false))
  return (
    <section>
      <SectionHeading title="回归规则、对照与失败项" description={`${item.regression.completed}/${item.regression.total} 已完成 · ${regressionLabel(item.regression.recommendation)} · 规则版本 ${item.regression.metric_rules_version || "未记录"}`} />
      <div className="border-y border-[var(--line-strong)] bg-white">
        <div className="grid gap-px bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-4"><Metric label="通过" value={String(item.regression.passed)} /><Metric label="失败" value={String(item.regression.failed)} /><Metric label="门槛" value={formatThreshold(item.regression.threshold)} /><Metric label="运行状态" value={regressionStatusLabel(item.regression.status)} /></div>
        <div className="grid gap-px border-t border-[var(--line)] bg-[var(--line)] lg:grid-cols-2">
          <div className="min-w-0 bg-white px-5 py-5"><h3 className="text-sm font-bold">指标门槛</h3>{thresholds && Object.keys(thresholds).length ? <dl className="mt-3 space-y-2">{Object.entries(thresholds).map(([key, value]) => <div key={key} className="grid gap-1 text-xs sm:grid-cols-[minmax(0,1fr)_auto]"><dt className="break-words text-[var(--muted)]">{humanizeEvidenceKey(key)}</dt><dd className="font-data font-bold">{formatEvidenceValue(value)}</dd></div>)}</dl> : <p className="mt-3 text-xs text-[var(--muted)]">没有单独记录指标门槛。</p>}</div>
          <div className="min-w-0 bg-white px-5 py-5"><h3 className="text-sm font-bold">门禁检查</h3>{gates.length ? <div className="mt-3 space-y-2">{gates.map((gate, index) => <div key={`${String(gate.gate)}-${index}`} className="flex min-w-0 items-start justify-between gap-3 text-xs"><span className="min-w-0 break-words text-[var(--muted)]">{humanizeEvidenceKey(String(gate.gate ?? `检查 ${index + 1}`))}</span><Badge tone={gate.passed === true ? "success" : "danger"}>{gate.passed === true ? "通过" : "失败"}</Badge></div>)}</div> : <p className="mt-3 text-xs text-[var(--muted)]">没有逐条门禁结果。</p>}</div>
        </div>
        <div className="border-t border-[var(--line)] px-5 py-4"><div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="text-sm font-bold">逐样本回归对照</h3><p className="text-xs text-[var(--muted)]">失败项优先排列；基线是旧方案，候选是本次新方案。</p></div></div>
        <div className="divide-y divide-[var(--line)]">
          {sortedItems.length ? sortedItems.map((entry, index) => <RegressionItemRow key={String(entry.id ?? index)} entry={entry} />) : <MissingEvidence text="本次回归没有冻结逐样本对照。" />}
        </div>
        <details className="border-t border-[var(--line)]"><summary className="cursor-pointer px-5 py-4 text-xs font-bold">查看完整冻结回归记录</summary><div className="border-t border-[var(--line)] p-5"><EvidenceObject value={{ metrics: item.regression.metrics, summary: item.regression.summary, metric_rules: item.regression.metric_rules, items: item.regression.items }} /></div></details>
      </div>
    </section>
  )
}

function RegressionItemRow({ entry }: { entry: JsonRecord }) {
  const comparison = asRecord(entry.comparison)
  const diffs = Array.isArray(comparison?.diffs) ? comparison.diffs : []
  const failed = entry.passed === false || entry.status === "error"
  return (
    <div className={`grid min-w-0 gap-4 px-5 py-4 sm:grid-cols-[72px_minmax(0,1fr)] lg:grid-cols-[72px_minmax(180px,0.7fr)_minmax(0,1fr)_auto] lg:items-center ${failed ? "bg-[#fff8f2]" : "bg-white"}`}>
      {typeof entry.image_url === "string" ? <img src={entry.image_url} alt={String(entry.asset_name ?? "回归样本")} className="size-16 border border-[var(--line)] bg-white object-cover" /> : <div className="flex size-16 items-center justify-center border border-[var(--line)] bg-[#fafbf8]"><Images /></div>}
      <div className="min-w-0"><p className="file-name break-all text-sm">{String(entry.asset_name ?? `样本 ${entry.sample_item_id ?? ""}`)}</p><p className="mt-1 text-xs text-[var(--muted)]">{roleLabel(String(entry.sample_role ?? "unassigned"))}</p></div>
      <div className="min-w-0 text-xs leading-5"><p><span className="text-[var(--muted)]">基线：</span>{extractResultLevel(entry.baseline_result)}</p><p><span className="text-[var(--muted)]">候选：</span>{extractResultLevel(entry.candidate_result)}</p><p className="mt-1 break-words text-[var(--muted)]">差异：{diffs.length ? diffs.map(formatDiff).join("；") : "没有记录字段差异"}</p></div>
      <Badge tone={failed ? "danger" : entry.passed === true ? "success" : "warning"}>{failed ? "失败项" : entry.passed === true ? "通过" : "待完成"}</Badge>
    </div>
  )
}

function roleLabel(role: string) {
  return ({ target_error: "目标错例", stable_control: "稳定对照", blind_holdout: "盲测样本", unassigned: "未分组" } as Record<string, string>)[role] ?? role
}

function extractResultLevel(value: unknown) {
  const record = asRecord(value)
  const fields = asRecord(record?.fields)
  return String(fields?.level ?? record?.level ?? "未记录等级")
}

function formatDiff(value: unknown) {
  const record = asRecord(value)
  if (!record) return formatEvidenceValue(value)
  const field = humanizeEvidenceKey(String(record.field ?? "字段"))
  return `${field}：${formatEvidenceValue(record.change ?? record.value ?? "有变化")}`
}

function humanizeEvidenceKey(value: string) {
  const known: Record<string, string> = {
    all: "全部门禁",
    level_consistency_max_drop: "等级一致率最大允许下降",
    release_gate_passed: "发布门禁",
    target_error_recovery: "目标错例修复",
    critical_regressions: "关键字段退化",
    new_severe_errors: "新增严重错误",
  }
  return known[value] ?? value.replaceAll("_", " ")
}

function formatEvidenceValue(value: unknown) {
  if (value === true) return "是"
  if (value === false) return "否"
  if (value == null) return "未记录"
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
  if (typeof value === "string") return value
  return JSON.stringify(value)
}

function formatThreshold(value: number) {
  return value >= 0 && value <= 1 ? `${Math.round(value * 100)}%` : String(value)
}

function regressionStatusLabel(value: string) {
  return ({ passed: "已通过", regressed: "已完成未通过", waiting_results: "等待结果", running: "正在运行", failed: "运行失败" } as Record<string, string>)[value] ?? value
}

function PromptSnapshot({ title, prompt }: { title: string; prompt: EvaluationPackageDetail["prompts"]["a"] }) {
  return <article className="border-y border-[var(--line-strong)] bg-white"><div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4"><div><p className="text-xs text-[var(--muted)]">{title}</p><h3 className="mt-1 text-lg font-bold">{prompt.name}</h3><p className="mt-2 text-xs leading-5 text-[var(--muted)]">本版变更：{prompt.change_note?.trim() || "该版本没有单独填写变更说明"}</p></div><div className="flex flex-wrap gap-2"><Badge tone="active">版本 {prompt.version}</Badge><Badge>规则 {prompt.rubric_version}</Badge></div></div><div className="grid gap-px bg-[var(--line)] lg:grid-cols-2"><PromptBlock label="系统指令全文" value={prompt.system_prompt} /><PromptBlock label="用户指令全文" value={prompt.user_prompt} /></div></article>
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
