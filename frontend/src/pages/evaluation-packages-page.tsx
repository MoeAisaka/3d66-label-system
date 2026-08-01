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
  GearSix,
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
  buildEvaluationPackageTimeline,
  buildPipelineReadiness,
  evaluationPackageApi,
  operatorSafeText,
  packageStatusMeta,
  percentText,
  toOperatorError,
  type OperatorError,
} from "@/lib/evaluation-packages"
import type {
  EvaluationCategoryProfile,
  EvaluationPackageDetail,
  EvaluationPackagePromptChange,
  EvaluationPackageSummary,
  EvaluationPackageTimelineStep,
  MaterialPackage,
  User,
} from "@/lib/types"

const activePackageStatuses = new Set([
  "draft",
  "ready",
  "queued",
  "evaluating",
  "first_review",
  "optimizing",
  "regressing",
  "second_review",
  "approved",
  "publishing",
  "blocked",
  "failed",
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
  const evaluationPackages = useQuery({
    queryKey: ["evaluation-packages"],
    queryFn: evaluationPackageApi.list,
    retry: false,
    refetchInterval: (query) => query.state.data?.items.some(
      (item) => ["queued", "evaluating", "optimizing", "regressing", "publishing"].includes(item.status),
    ) ? 4000 : false,
  })

  const activeCategories = useMemo(
    () => (categories.data?.items ?? []).filter((item) => item.status === "active"),
    [categories.data?.items],
  )
  const availableMaterialPackages = useMemo(
    () => (materialPackages.data?.items ?? []).filter(
      (item) => !categoryKey || item.category_key === categoryKey,
    ),
    [categoryKey, materialPackages.data?.items],
  )

  useEffect(() => {
    if (!categoryKey && activeCategories.length) setCategoryKey(activeCategories[0].category_key)
  }, [activeCategories, categoryKey])

  useEffect(() => {
    if (!availableMaterialPackages.some((item) => item.id === materialPackageId)) {
      setMaterialPackageId(availableMaterialPackages[0]?.id ?? 0)
    }
  }, [availableMaterialPackages, materialPackageId])

  const selectedCategory = activeCategories.find((item) => item.category_key === categoryKey)
  const selectedMaterialPackage = availableMaterialPackages.find((item) => item.id === materialPackageId)
  const readiness = buildPipelineReadiness(selectedMaterialPackage, selectedCategory)
  const allReady = readiness.every((item) => item.ready)
  const requestedPackageId = Number(searchParams.get("package"))
  const currentForMaterials = (evaluationPackages.data?.items ?? [])
    .filter((item) => item.material_package_id === materialPackageId && activePackageStatuses.has(item.status))
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())[0]
  const focusedPackage = evaluationPackages.data?.items.find((item) => item.id === requestedPackageId)
    ?? currentForMaterials
    ?? evaluationPackages.data?.items[0]

  const createPackage = useMutation({
    mutationFn: () => {
      if (!selectedMaterialPackage || !selectedCategory) {
        return Promise.reject(new Error("missing_selection"))
      }
      return evaluationPackageApi.create({
        material_package_id: selectedMaterialPackage.id,
        category_key: selectedCategory.category_key,
        configuration_mode: "category_frozen",
      })
    },
    onSuccess: async (created) => {
      setActionError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["evaluation-packages"] }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      ])
      setSearchParams({ package: String(created.id) }, { replace: true })
      toast.success("评测包已创建，系统会继续完成评测、优化和验证")
    },
    onError: (error) => setActionError(toOperatorError(error)),
  })

  const essentialError = categories.error ?? materialPackages.error
  const partialError = evaluationPackages.error ? toOperatorError(evaluationPackages.error) : null
  const timeline = buildEvaluationPackageTimeline(focusedPackage?.status)
  const visibleBlockers = focusedPackage?.current_blockers?.length
    ? focusedPackage.current_blockers
    : readiness.filter((item) => !item.ready).map((item) => ({
      code: item.key,
      title: item.label,
      message: item.description,
      action_label: item.action_label,
      action_href: item.action_href,
    }))
  const fallbackNextStep = !selectedMaterialPackage
      ? "先导入并选择一个素材包，系统会自动核对它所属的类目队列。"
      : allReady
        ? "素材和类目运行方案已经核对完成，可以直接开始评测。"
        : "先处理下方标出的阻塞项，完成后即可一键开始评测。"
  const aiNextStep = operatorSafeText(focusedPackage?.ai_next_step, fallbackNextStep)

  return (
    <>
      <PageHeader
        index="01.1"
        title="评测包生产线"
        description="选择素材包和类目队列后即可开始。系统会自动完成评测、优化和黄金样本验证，审核员只在一审和二审做决定。"
        actions={
          <Button asChild variant="secondary"><Link to="/workflow/materials/packages"><Images />导入或整理素材</Link></Button>
        }
      />
      <div className="mx-auto max-w-[1540px] space-y-8 px-5 py-8 md:px-8 lg:px-10">
        {partialError && !essentialError && (
          <OperatorErrorPanel
            error={partialError.kind === "permission"
              ? partialError
              : { ...partialError, title: "评测包进度暂时无法读取", message: "素材和类目仍可选择；恢复连接后刷新即可看到生产线进度。" }}
            compact
            onRetry={() => evaluationPackages.refetch()}
          />
        )}
        {actionError && <OperatorErrorPanel error={actionError} onRetry={() => actionError.kind === "conflict" ? evaluationPackages.refetch() : createPackage.mutate()} onClose={() => setActionError(null)} />}

        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-px bg-[var(--line)] lg:grid-cols-[minmax(0,1.45fr)_minmax(300px,0.55fr)]">
            <div className="bg-white px-5 py-6 md:px-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-semibold text-[var(--muted)]">开始一次完整评测</p>
                  <h2 className="font-editorial mt-2 text-2xl font-bold">选择来源和去向</h2>
                </div>
                <Badge tone={allReady ? "success" : "warning"}>{allReady ? "已经就绪" : "还需检查"}</Badge>
              </div>
              {essentialError ? (
                <div className="mt-6">
                  <OperatorErrorPanel error={toOperatorError(essentialError)} compact onRetry={() => Promise.all([categories.refetch(), materialPackages.refetch()])} />
                </div>
              ) : categories.isLoading || materialPackages.isLoading ? (
                <div className="mt-6 grid gap-4 md:grid-cols-2"><LoadingField /><LoadingField /></div>
              ) : (
                <div className="mt-6 grid gap-4 md:grid-cols-2">
                  <label>
                    <span className="mb-2 block text-xs font-semibold">类目队列</span>
                    <select
                      className="h-12 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                      value={categoryKey}
                      onChange={(event) => { setCategoryKey(event.target.value); setMaterialPackageId(0) }}
                    >
                      {!activeCategories.length && <option value="">暂无已开启的类目</option>}
                      {activeCategories.map((item) => <option key={item.category_key} value={item.category_key}>{item.display_name}</option>)}
                    </select>
                    <span className="mt-2 block text-xs leading-5 text-[var(--muted)]">每个队列都有管理员确认过的运行方案。</span>
                  </label>
                  <label>
                    <span className="mb-2 block text-xs font-semibold">素材包</span>
                    <select
                      className="h-12 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
                      value={materialPackageId || ""}
                      onChange={(event) => setMaterialPackageId(Number(event.target.value))}
                    >
                      {!availableMaterialPackages.length && <option value="">当前类目还没有素材包</option>}
                      {availableMaterialPackages.map((item) => (
                        <option key={item.id} value={item.id}>{item.name} · {item.active_asset_count} 份素材</option>
                      ))}
                    </select>
                    <span className="mt-2 block text-xs leading-5 text-[var(--muted)]">素材包在开始时冻结，后续整理不会改写本次记录。</span>
                  </label>
                </div>
              )}
              <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-5">
                {currentForMaterials ? (
                  <Button asChild>
                    <Link to={nextPackageHref(currentForMaterials)}>{nextPackageAction(currentForMaterials)}<ArrowRight /></Link>
                  </Button>
                ) : (
                  <Button disabled={!allReady || createPackage.isPending || Boolean(essentialError)} onClick={() => createPackage.mutate()}>
                    {createPackage.isPending ? <CircleNotch className="animate-spin" /> : <Play weight="fill" />}
                    {createPackage.isPending ? "正在创建评测包" : "开始评测"}
                  </Button>
                )}
                <p className="text-xs leading-5 text-[var(--muted)]">开始后无需守在页面，系统会持续推进并保留每一步证据。</p>
              </div>
            </div>
            <aside className="bg-[#f7fadf] px-5 py-6 md:px-6">
              <div className="flex items-center gap-2"><Lightbulb weight="fill" /><p className="text-xs font-bold">AI 下一步建议</p></div>
              <p className="mt-4 text-lg font-semibold leading-8">{aiNextStep}</p>
              <p className="mt-4 border-t border-[#dfe7b8] pt-4 text-xs leading-5 text-[#596047]">建议仅用于缩短操作路径。评测包是否通过，始终由二审人员明确决定。</p>
            </aside>
          </div>
        </section>

        <section>
          <SectionHeading title="就绪检查" description="开始前只检查三件事；系统参数由管理员预先维护。" />
          <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">
            {readiness.map((item, index) => (
              <div key={item.key} className="grid gap-3 px-5 py-4 sm:grid-cols-[36px_minmax(0,1fr)_auto] sm:items-center">
                <span className={`font-data flex size-8 items-center justify-center border text-sm font-bold ${item.ready ? "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]" : "border-[#e5c9a7] bg-[#fff6e9] text-[#7d4308]"}`}>
                  {item.ready ? <Check weight="bold" /> : index + 1}
                </span>
                <div><p className="text-sm font-semibold">{item.label}</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">{item.description}</p></div>
                {!item.ready && item.action_href && <Button asChild size="sm" variant="secondary"><Link to={item.action_href}>{item.action_label}<ArrowRight /></Link></Button>}
              </div>
            ))}
          </div>
        </section>

        <section>
          <SectionHeading title="从导入到发布" description="每一步都只追加记录；刷新或离开页面不会中断正在进行的工作。" />
          <PipelineTimeline steps={timeline} />
        </section>

        <div className="grid gap-8 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
          <section className="min-w-0">
            <SectionHeading title="当前阻塞" description="这里显示真正需要人工处理的事项。" />
            <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">
              {visibleBlockers.length ? visibleBlockers.map((blocker) => (
                <div key={blocker.code} className="grid gap-3 px-5 py-4 sm:grid-cols-[24px_minmax(0,1fr)_auto] sm:items-center">
                  <WarningCircle className="text-[#a85a0a]" />
                  <div><p className="text-sm font-semibold">{operatorSafeText(blocker.title, "需要处理")}</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">{operatorSafeText(blocker.message, "请打开对应设置完成检查，返回后即可继续。")}</p></div>
                  {blocker.action_href && <Button asChild size="sm" variant="secondary"><Link to={blocker.action_href}>{operatorSafeText(blocker.action_label, "直接处理")}<ArrowRight /></Link></Button>}
                </div>
              )) : (
                <div className="flex items-start gap-3 px-5 py-5"><CheckCircle className="mt-0.5 text-[#2f6f48]" weight="fill" /><div><p className="text-sm font-semibold">当前没有阻塞</p><p className="mt-1 text-xs text-[var(--muted)]">系统会继续自动推进，有需要人工决定的事项会出现在一审或二审队列。</p></div></div>
              )}
            </div>
          </section>
          <section className="min-w-0">
            <SectionHeading title="当前进度" description="优先展示最近更新的评测包。" />
            <CurrentPackageProgress item={focusedPackage} loading={evaluationPackages.isLoading} />
          </section>
        </div>

        <section>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <SectionHeading title="最近评测包" description="从这里回到任意一条完整运行记录。" />
            <Button variant="ghost" size="sm" onClick={() => evaluationPackages.refetch()} disabled={evaluationPackages.isFetching}><ArrowClockwise />刷新进度</Button>
          </div>
          <EvaluationPackageRows items={(evaluationPackages.data?.items ?? []).slice(0, 8)} loading={evaluationPackages.isLoading} />
        </section>

        <details className="border-y border-[var(--line-strong)] bg-white">
          <summary className="flex cursor-pointer items-center justify-between gap-4 px-5 py-4 text-sm font-bold">
            <span className="flex items-center gap-2"><GearSix />管理员高级设置</span>
            <Badge>{user.is_admin ? "可配置" : "只读入口"}</Badge>
          </summary>
          <div className="grid gap-5 border-t border-[var(--line)] bg-[#fafbf8] px-5 py-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
            <div>
              <p className="text-sm font-semibold">当前使用类目冻结方案</p>
              <p className="mt-2 text-xs leading-5 text-[var(--muted)]">单次完整评测或 A/B 两段评测、提示词版本、模型与维度方案均由类目配置决定；普通审核员无需逐次选择。</p>
            </div>
            <Button asChild variant="secondary"><Link to="/workflow/governance">打开高级设置<ArrowRight /></Link></Button>
          </div>
        </details>
      </div>
    </>
  )
}

export function EvaluationPackageReviewListPage() {
  const [showHistory, setShowHistory] = useState(false)
  const packages = useQuery({
    queryKey: ["evaluation-packages"],
    queryFn: evaluationPackageApi.list,
    retry: false,
    refetchInterval: (query) => query.state.data?.items.some(
      (item) => ["evaluating", "optimizing", "regressing", "publishing"].includes(item.status),
    ) ? 4000 : false,
  })
  const allItems = packages.data?.items ?? []
  const pending = allItems.filter((item) => item.status === "second_review")
  const history = allItems.filter((item) => ["approved", "rejected", "publishing", "published", "archived"].includes(item.status))
  const visible = showHistory ? history : pending

  return (
    <>
      <PageHeader
        index="03.1"
        title="二审评测包"
        description="二审查看的是完整新版方案和验证证据，不需要重新逐张审核素材。打开评测包后可明确批准或拒绝。"
        actions={<Button variant="secondary" onClick={() => packages.refetch()} disabled={packages.isFetching}><ArrowClockwise />刷新队列</Button>}
      />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <div className="mb-6 grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-3">
          <QueueMetric label="等待二审" value={pending.length} active={!showHistory} onClick={() => setShowHistory(false)} />
          <QueueMetric label="已有结论" value={history.length} active={showHistory} onClick={() => setShowHistory(true)} />
          <div className="bg-[#f7fadf] px-5 py-4"><p className="text-xs font-semibold text-[var(--muted)]">二审原则</p><p className="mt-2 text-sm font-semibold">先看变更，再看黄金样本与自动验证</p></div>
        </div>
        {packages.error ? (
          <OperatorErrorPanel error={toOperatorError(packages.error)} onRetry={() => packages.refetch()} />
        ) : (
          <EvaluationPackageRows
            items={visible}
            loading={packages.isLoading}
            empty={showHistory ? "还没有已处理的二审评测包" : "当前没有等待二审的评测包"}
          />
        )}
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
  const detail = useQuery({
    queryKey: ["evaluation-package", id],
    queryFn: () => evaluationPackageApi.get(id),
    enabled: Number.isInteger(id) && id > 0,
    retry: false,
    refetchInterval: (query) => ["evaluating", "optimizing", "regressing", "publishing"].includes(query.state.data?.status ?? "") ? 4000 : false,
  })
  const action = useMutation({
    mutationFn: ({ type, actionNote }: { type: ReviewAction; actionNote: string }) => {
      if (type === "approve") return evaluationPackageApi.approve(id, actionNote)
      if (type === "reject") return evaluationPackageApi.reject(id, actionNote)
      if (type === "publish") return evaluationPackageApi.publish(id, actionNote)
      return evaluationPackageApi.archive(id, actionNote)
    },
    onSuccess: async (updated, variables) => {
      setActionError(null)
      setNote("")
      queryClient.setQueryData(["evaluation-package", id], updated)
      await queryClient.invalidateQueries({ queryKey: ["evaluation-packages"] })
      toast.success({ approve: "评测包已批准，仍需明确发布", reject: "评测包已拒绝并退回改进", publish: "评测包已发布", archive: "评测包已归档" }[variables.type])
    },
    onError: (error) => setActionError(toOperatorError(error)),
  })

  if (!Number.isInteger(id) || id <= 0) {
    return <InvalidPackage onBack={() => navigate("/workflow/releases/packages", { replace: true })} />
  }
  if (detail.isLoading) return <DetailLoading />
  if (detail.error) {
    return (
      <>
        <PageHeader index="03.2" title="二审评测包详情" description="正在读取这次运行的完整证据。" />
        <div className="mx-auto max-w-[1180px] px-5 py-10 md:px-8"><OperatorErrorPanel error={toOperatorError(detail.error)} onRetry={() => detail.refetch()} /></div>
      </>
    )
  }
  if (!detail.data) return <InvalidPackage onBack={() => navigate("/workflow/releases/packages", { replace: true })} />

  return (
    <EvaluationPackageDetailContent
      item={detail.data}
      note={note}
      setNote={setNote}
      actionError={actionError}
      clearActionError={() => setActionError(null)}
      pendingAction={action.isPending ? action.variables?.type ?? null : null}
      onAction={(type) => action.mutate({ type, actionNote: note.trim() })}
      onRefresh={() => detail.refetch()}
    />
  )
}

function EvaluationPackageDetailContent({
  item,
  note,
  setNote,
  actionError,
  clearActionError,
  pendingAction,
  onAction,
  onRefresh,
}: {
  item: EvaluationPackageDetail
  note: string
  setNote: (value: string) => void
  actionError: OperatorError | null
  clearActionError: () => void
  pendingAction: ReviewAction | null
  onAction: (type: ReviewAction) => void
  onRefresh: () => void
}) {
  const status = packageStatusMeta(item.status)
  const timeline = buildEvaluationPackageTimeline(item.status)
  const permissions = item.permissions
  const canDecide = item.status === "second_review" && permissions?.can_approve !== false
  const canReject = item.status === "second_review" && permissions?.can_reject !== false
  const canPublish = item.status === "approved" && permissions?.can_publish !== false
  const canArchive = !["published", "archived"].includes(item.status) && permissions?.can_archive !== false
  const noteReady = Boolean(note.trim())

  return (
    <>
      <PageHeader
        index="03.2"
        title={item.name || "二审评测包详情"}
        description={`${item.material_package_name || "素材包未记录"} · ${item.category_name || item.category_key || "类目未记录"} · ${item.asset_count ?? 0} 份素材`}
        actions={
          <>
            <Button asChild variant="secondary"><Link to="/workflow/releases/packages"><ArrowLeft />返回二审队列</Link></Button>
            <Button variant="secondary" onClick={onRefresh}><ArrowClockwise />刷新</Button>
          </>
        }
      />
      <div className="mx-auto max-w-[1320px] space-y-8 px-5 py-8 md:px-8 lg:px-10">
        {actionError && <OperatorErrorPanel error={actionError} onRetry={onRefresh} onClose={clearActionError} />}
        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="flex flex-wrap items-start justify-between gap-5 border-b border-[var(--line)] px-5 py-5">
            <div>
              <p className="font-data text-xs text-[var(--muted)]">评测包 {item.package_key || `#${item.id}`} · 更新于 {formatTime(item.updated_at)}</p>
              <h2 className="font-editorial mt-2 text-2xl font-bold">{status.label}</h2>
              <p className="mt-2 text-sm text-[var(--muted)]">{status.description}</p>
            </div>
            <Badge tone={status.tone}>{status.label}</Badge>
          </div>
          <div className="grid gap-px bg-[var(--line)] sm:grid-cols-3">
            <Metric label="素材数量" value={String(item.asset_count ?? 0)} />
            <Metric label="总体进度" value={`${boundedPercent(item.progress?.percent)}%`} />
            <Metric label="当前环节" value={operatorSafeText(item.progress?.current_step, status.label)} />
          </div>
        </section>

        <PipelineTimeline steps={timeline} />

        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
          <div className="bg-[#f7fadf] px-5 py-6">
            <div className="flex items-center gap-2"><Lightbulb weight="fill" /><h2 className="text-sm font-bold">AI 建议</h2></div>
            {item.recommendation ? (
              <>
                <div className="mt-4 flex flex-wrap items-center gap-2"><Badge tone={recommendationTone(item.recommendation.verdict)}>{recommendationLabel(item.recommendation.verdict)}</Badge><strong>{item.recommendation.title}</strong></div>
                <p className="mt-3 text-sm leading-6">{item.recommendation.summary}</p>
                <EvidenceList title="主要依据" items={item.recommendation.reasons} />
                {item.recommendation.risks.length > 0 && <EvidenceList title="仍需留意" items={item.recommendation.risks} warning />}
              </>
            ) : <MissingEvidence text="本次接口尚未返回 AI 建议。请根据下方完整证据人工判断。" />}
          </div>
          <div className="bg-white px-5 py-6">
            <h2 className="font-editorial text-2xl font-bold">变更摘要</h2>
            {item.change_summary ? (
              <><h3 className="mt-4 text-sm font-semibold">{item.change_summary.title}</h3><p className="mt-2 text-sm leading-6 text-[var(--muted)]">{item.change_summary.overview}</p><EvidenceList title="本次变化" items={item.change_summary.items} /></>
            ) : <MissingEvidence text="本次接口尚未返回结构化变更摘要。可继续核对提示词和维度明细。" />}
          </div>
        </section>

        <section>
          <SectionHeading title="完整新版提示词与差异" description="先看逐行变化，再阅读完整新版内容；任何候选都不会覆盖历史版本。" />
          {item.prompt_changes?.length ? (
            <div className="space-y-6">{item.prompt_changes.map((change, index) => <PromptChangeSection key={`${change.stage}-${change.version_after}-${index}`} change={change} />)}</div>
          ) : <MissingSection text="提示词变更明细暂未返回。当前评测包属于部分证据状态，建议刷新或联系管理员补齐后再做二审。" />}
        </section>

        <section>
          <SectionHeading title="维度定义与变更" description="检查评分维度的含义、锚点或适用范围是否改变。" />
          {item.dimension_changes ? (
            <div className="border-y border-[var(--line-strong)] bg-white">
              <div className="grid gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-5 py-4 sm:grid-cols-[180px_minmax(0,1fr)]"><p className="text-xs font-semibold text-[var(--muted)]">版本变化</p><p className="font-data text-sm font-semibold">{item.dimension_changes.schema_before || "未记录"} → {item.dimension_changes.schema_after}</p><p className="text-xs font-semibold text-[var(--muted)]">变更说明</p><p className="text-sm leading-6">{item.dimension_changes.summary}</p></div>
              <div className="divide-y divide-[var(--line)]">
                {item.dimension_changes.items.length ? item.dimension_changes.items.map((change) => (
                  <div key={change.key} className="grid gap-3 px-5 py-4 md:grid-cols-[180px_110px_minmax(0,1fr)_minmax(0,1fr)]">
                    <div><p className="text-sm font-semibold">{change.label}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">{change.key}</p></div>
                    <Badge tone={dimensionChangeTone(change.change_type)}>{dimensionChangeLabel(change.change_type)}</Badge>
                    <div><p className="text-xs font-semibold text-[var(--muted)]">变化后</p><p className="mt-1 text-sm leading-6">{change.after || "已移除"}</p></div>
                    <div><p className="text-xs font-semibold text-[var(--muted)]">原因</p><p className="mt-1 text-sm leading-6 text-[var(--muted)]">{change.rationale || "未说明"}</p></div>
                  </div>
                )) : <p className="px-5 py-6 text-sm text-[var(--muted)]">维度版本已核对，本次没有定义变化。</p>}
              </div>
            </div>
          ) : <MissingSection text="维度定义与变更暂未返回。二审前应确认评测包已经冻结这部分证据。" />}
        </section>

        <section>
          <SectionHeading title="黄金集摘要与代表样本" description="黄金真值来自人工确认；代表样本用于快速判断新版是否修对了问题。" />
          {item.golden_set ? (
            <div className="border-y border-[var(--line-strong)] bg-white">
              <div className="grid gap-px border-b border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-4">
                <Metric label="黄金集" value={item.golden_set.name} />
                <Metric label="样本数" value={String(item.golden_set.sample_count)} />
                <Metric label="新版准确率" value={percentText(item.golden_set.metrics.accuracy_after)} />
                <Metric label="验证通过率" value={percentText(item.golden_set.metrics.regression_pass_rate)} />
              </div>
              <div className="divide-y divide-[var(--line)]">
                {item.golden_set.representative_samples.length ? item.golden_set.representative_samples.map((sample) => (
                  <div key={sample.id} className="grid gap-4 px-5 py-4 sm:grid-cols-[72px_minmax(0,1fr)] lg:grid-cols-[72px_minmax(180px,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] lg:items-center">
                    <img src={sample.image_url} alt="" className="size-16 border border-[var(--line)] object-cover" />
                    <div className="min-w-0"><p className="file-name truncate text-sm">{sample.name}</p><p className="mt-1 text-xs text-[var(--muted)]">人工真值 {sample.expected_level || "未记录"}</p></div>
                    <div><p className="text-xs font-semibold text-[var(--muted)]">版本前后</p><p className="mt-1 text-sm">{sample.result_before || "未记录"} → <strong>{sample.result_after || "未记录"}</strong></p></div>
                    <p className="text-xs leading-5 text-[var(--muted)]">{sample.reason}</p>
                  </div>
                )) : <p className="px-5 py-7 text-sm text-[var(--muted)]">本次没有返回代表样本，但黄金集汇总已经冻结。</p>}
              </div>
            </div>
          ) : <MissingSection text="黄金集摘要暂未返回。没有人工真值与验证证据时，不建议批准新版。" />}
        </section>

        <section>
          <SectionHeading title="自动优化与回归证据" description="查看系统做了什么、验证了什么，以及还有哪些失败项。" />
          {item.automation ? (
            <div className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <div className="bg-white px-5 py-5"><h3 className="text-sm font-semibold">自动改进记录</h3><p className="mt-2 text-sm leading-6 text-[var(--muted)]">{item.automation.summary}</p><div className="mt-5 divide-y divide-[var(--line)] border-y border-[var(--line)]">{item.automation.rounds.map((round) => <div key={round.sequence} className="grid gap-2 py-3 sm:grid-cols-[36px_minmax(0,1fr)_auto]"><span className="font-data flex size-8 items-center justify-center border border-[var(--line-strong)]">{round.sequence}</span><div><p className="text-sm font-semibold">{round.title}</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">{round.summary}</p></div><Badge tone={round.status === "completed" ? "success" : round.status === "failed" ? "danger" : "active"}>{round.status === "completed" ? "已完成" : round.status === "failed" ? "未完成" : "进行中"}</Badge></div>)}</div></div>
              <div className="bg-[#fafbf8] px-5 py-5"><h3 className="text-sm font-semibold">回归结论</h3>{item.automation.regression ? <><div className="mt-4 flex flex-wrap items-center gap-2"><Badge tone={item.automation.regression.recommendation === "pass" ? "success" : item.automation.regression.recommendation === "fail" ? "danger" : "warning"}>{item.automation.regression.recommendation === "pass" ? "建议通过" : item.automation.regression.recommendation === "fail" ? "建议拒绝" : "仍在验证"}</Badge><span className="font-data text-xs">{item.automation.regression.completed}/{item.automation.regression.total} 已完成</span></div><p className="mt-3 text-sm leading-6">{item.automation.regression.summary}</p><p className="font-data mt-3 text-2xl font-bold">{percentText(item.automation.regression.pass_rate)}</p></> : <MissingEvidence text="没有可展示的回归结论。" />}<EvidenceList title="证据清单" items={item.automation.evidence} /></div>
            </div>
          ) : <MissingSection text="自动优化与回归证据暂未返回。请等系统完成或刷新后再做决定。" />}
        </section>

        <details className="border-y border-[var(--line-strong)] bg-white">
          <summary className="flex cursor-pointer items-center gap-2 px-5 py-4 text-sm font-bold"><LockKey />高级技术详情</summary>
          <div className="grid gap-px border-t border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-3">
            <TechnicalFact label="模型" value={[item.technical?.model_name, item.technical?.model_version].filter(Boolean).join(" · ")} />
            <TechnicalFact label="评分引擎" value={item.technical?.engine_version} />
            <TechnicalFact label="评分规则" value={item.technical?.rubric_version} />
            <TechnicalFact label="维度方案" value={item.technical?.dimension_schema_version} />
            <TechnicalFact label="策略校验值" value={item.technical?.strategy_hash} mono />
            <TechnicalFact label="评测包校验值" value={item.technical?.package_hash} mono />
          </div>
        </details>

        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="border-b border-[var(--line)] px-5 py-5"><div className="flex items-center gap-2"><ShieldCheck /><h2 className="font-editorial text-2xl font-bold">二审决定</h2></div><p className="mt-2 text-sm leading-6 text-[var(--muted)]">批准只确认这份新版评测包可以发布；发布仍是单独的明确动作。拒绝会保留全部证据并退回继续改进。</p></div>
          {permissions?.reason && !canDecide && !canPublish && <div className="border-b border-[var(--line)] bg-[#fff9e9] px-5 py-4 text-sm text-[#6f5513]">{permissions.reason}</div>}
          <div className="grid gap-4 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
            <label><span className="mb-2 block text-xs font-semibold">二审备注或发布说明（必填）</span><Textarea value={note} maxLength={2000} rows={4} onChange={(event) => setNote(event.target.value)} placeholder="说明批准、拒绝或发布的判断依据" /></label>
            <div className="flex flex-wrap gap-2 lg:max-w-[380px] lg:justify-end">
              {canReject && <Button variant="danger" disabled={!noteReady || Boolean(pendingAction)} onClick={() => onAction("reject")}>{pendingAction === "reject" ? <CircleNotch className="animate-spin" /> : <XCircle />}拒绝并退回</Button>}
              {canDecide && <Button disabled={!noteReady || Boolean(pendingAction)} onClick={() => onAction("approve")}>{pendingAction === "approve" ? <CircleNotch className="animate-spin" /> : <CheckCircle />}批准新版</Button>}
              {canPublish && <Button disabled={!noteReady || Boolean(pendingAction)} onClick={() => onAction("publish")}>{pendingAction === "publish" ? <CircleNotch className="animate-spin" /> : <Play weight="fill" />}发布评测包</Button>}
              {canArchive && <Button variant="secondary" disabled={!noteReady || Boolean(pendingAction)} onClick={() => onAction("archive")}>{pendingAction === "archive" ? <CircleNotch className="animate-spin" /> : <Archive />}归档</Button>}
              {!canDecide && !canReject && !canPublish && !canArchive && <Badge tone={status.tone}>{status.label}</Badge>}
            </div>
          </div>
          {item.review && item.review.status !== "pending" && <div className="border-t border-[var(--line)] bg-[#fafbf8] px-5 py-4 text-xs leading-5 text-[var(--muted)]">最近人工结论：{reviewStatusLabel(item.review.status)} · {item.review.reviewer || "操作员未记录"} · {item.review.decided_at ? formatTime(item.review.decided_at) : "时间未记录"}<br />{item.review.note || "未填写备注"}</div>}
        </section>
      </div>
    </>
  )
}

function PromptChangeSection({ change }: { change: EvaluationPackagePromptChange }) {
  return (
    <article className="border-y border-[var(--line-strong)] bg-white">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-4"><div><p className="text-xs font-semibold text-[var(--muted)]">{promptStageLabel(change.stage)}</p><h3 className="mt-1 text-lg font-bold">{change.label || "新版提示词"}</h3></div><Badge tone="active" className="max-w-full break-all leading-5">{change.version_before || "无旧版本"} → {change.version_after}</Badge></div>
      {change.change_summary && <p className="border-b border-[var(--line)] bg-[#f7fadf] px-5 py-3 text-sm leading-6">{change.change_summary}</p>}
      <div className="grid gap-px bg-[var(--line)] xl:grid-cols-2">
        <div className="min-w-0 bg-[#fafbf8]"><p className="border-b border-[var(--line)] px-5 py-3 text-xs font-bold">逐行差异</p>{change.unified_diff ? <DiffView diff={change.unified_diff} /> : <MissingEvidence text="本次接口没有返回逐行差异。" />}</div>
        <div className="min-w-0 bg-white"><p className="border-b border-[var(--line)] px-5 py-3 text-xs font-bold">完整新版内容</p><PromptBlock label="系统指令" value={change.system_prompt_after} /><PromptBlock label="用户指令" value={change.user_prompt_after} /></div>
      </div>
    </article>
  )
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="max-h-[620px] overflow-auto whitespace-pre-wrap break-words p-4 font-data text-xs leading-6">
      {diff.split("\n").map((line, index) => <span key={index} className={`block min-h-6 px-2 ${line.startsWith("+") && !line.startsWith("+++") ? "bg-[#edf7f0] text-[#245b3b]" : line.startsWith("-") && !line.startsWith("---") ? "bg-[#fff0ee] text-[#8d2924]" : line.startsWith("@@") ? "bg-[#eef1eb] font-semibold" : ""}`}>{line || " "}</span>)}
    </pre>
  )
}

function PromptBlock({ label, value }: { label: string; value: string }) {
  return <div className="border-b border-[var(--line)] last:border-0"><p className="px-5 pt-4 text-xs font-semibold text-[var(--muted)]">{label}</p><pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words px-5 py-4 font-data text-xs leading-6">{value || "未提供"}</pre></div>
}

function EvaluationPackageRows({ items, loading, empty = "还没有评测包记录" }: { items: EvaluationPackageSummary[]; loading: boolean; empty?: string }) {
  if (loading) return <div className="h-56 animate-pulse border-y border-[var(--line-strong)] bg-white" />
  if (!items.length) return <div className="flex min-h-56 flex-col items-center justify-center border-y border-[var(--line-strong)] bg-white px-6 text-center"><Package size={30} weight="light" /><h3 className="font-editorial mt-4 text-xl font-bold">{empty}</h3><p className="mt-2 text-sm text-[var(--muted)]">生产线创建后会在这里显示完整进度和证据。</p></div>
  return (
    <div className="divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">
      {items.map((item) => {
        const status = packageStatusMeta(item.status)
        return (
          <Link key={item.id} to={`/workflow/releases/packages/${item.id}`} className="grid gap-4 px-5 py-4 transition-colors hover:bg-[#f8f9f6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary xl:grid-cols-[minmax(220px,1fr)_minmax(180px,0.7fr)_180px_130px_auto] xl:items-center">
            <div className="min-w-0"><p className="truncate text-sm font-semibold">{item.name}</p><p className="font-data mt-1 truncate text-xs text-[var(--muted)]">{item.package_key || `#${item.id}`} · {formatTime(item.updated_at)}</p></div>
            <div className="min-w-0"><p className="truncate text-sm">{item.material_package_name}</p><p className="mt-1 truncate text-xs text-[var(--muted)]">{item.category_name || item.category_key} · {item.asset_count} 份</p></div>
            <div><div className="h-1.5 overflow-hidden bg-[#eef1eb]"><div className="h-full bg-primary" style={{ width: `${boundedPercent(item.progress?.percent)}%` }} /></div><p className="font-data mt-2 text-xs text-[var(--muted)]">{boundedPercent(item.progress?.percent)}% · {operatorSafeText(item.progress?.current_step, status.label)}</p></div>
            <Badge tone={status.tone}>{status.label}</Badge>
            <ArrowRight aria-hidden="true" />
          </Link>
        )
      })}
    </div>
  )
}

function CurrentPackageProgress({ item, loading }: { item: EvaluationPackageSummary | undefined; loading: boolean }) {
  if (loading) return <div className="h-44 animate-pulse border-y border-[var(--line-strong)] bg-white" />
  if (!item) return <div className="flex min-h-44 items-center gap-3 border-y border-[var(--line-strong)] bg-white px-5"><Clock className="text-[var(--muted)]" /><p className="text-sm text-[var(--muted)]">开始评测后，这里会显示当前环节和整体进度。</p></div>
  const status = packageStatusMeta(item.status)
  return <Link to={nextPackageHref(item)} className="block border-y border-[var(--line-strong)] bg-white px-5 py-5 transition-colors hover:bg-[#f8f9f6]"><div className="flex items-start justify-between gap-4"><div className="min-w-0"><p className="truncate text-sm font-semibold">{item.name}</p><p className="mt-1 text-xs text-[var(--muted)]">{operatorSafeText(item.progress?.current_step, status.label)}</p></div><Badge tone={status.tone}>{status.label}</Badge></div><div className="mt-5 h-2 overflow-hidden bg-[#eef1eb]"><div className="h-full bg-primary transition-[width]" style={{ width: `${boundedPercent(item.progress?.percent)}%` }} /></div><div className="mt-3 flex items-center justify-between text-xs"><span className="text-[var(--muted)]">已完成 {item.progress?.completed_assets ?? 0} / {item.progress?.total_assets ?? item.asset_count}</span><strong className="font-data">{boundedPercent(item.progress?.percent)}%</strong></div><p className="mt-3 text-xs font-semibold">{nextPackageAction(item)} →</p></Link>
}

function PipelineTimeline({ steps }: { steps: EvaluationPackageTimelineStep[] }) {
  return (
    <ol className="grid overflow-hidden border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-6">
      {steps.map((step, index) => (
        <li key={step.key} className={`relative min-w-0 border-b border-r border-[var(--line)] px-4 py-4 sm:[&:nth-last-child(-n+2)]:border-b-0 lg:border-b-0 ${step.status === "current" ? "bg-[#f7fadf]" : step.status === "blocked" || step.status === "failed" ? "bg-[#fff6e9]" : "bg-white"}`} aria-current={step.status === "current" ? "step" : undefined}>
          <div className="flex items-center gap-3"><span className={`font-data flex size-7 shrink-0 items-center justify-center border text-xs font-bold ${step.status === "completed" ? "border-[#7ca08a] bg-[#edf7f0] text-[#245b3b]" : step.status === "current" ? "border-[#8da91e] bg-primary" : step.status === "blocked" || step.status === "failed" ? "border-[#d9ae79] bg-[#fff6e9] text-[#7d4308]" : "border-[var(--line-strong)] text-[var(--muted)]"}`}>{step.status === "completed" ? <Check weight="bold" /> : index + 1}</span><p className="text-sm font-semibold">{step.label}</p></div>
          <p className="mt-2 text-xs leading-5 text-[var(--muted)]">{step.description}</p>
        </li>
      ))}
    </ol>
  )
}

function OperatorErrorPanel({ error, compact = false, onRetry, onClose }: { error: OperatorError; compact?: boolean; onRetry?: () => void; onClose?: () => void }) {
  return <div className={`border-y ${error.kind === "permission" ? "border-[#e5c9a7] bg-[#fff9e9] text-[#6f5513]" : "border-[#e8c1bd] bg-[#fff5f3] text-[#7d201a]"} ${compact ? "px-4 py-3" : "px-5 py-5"}`} role="alert"><div className="flex flex-wrap items-start justify-between gap-4"><div className="flex min-w-0 items-start gap-3"><WarningCircle className="mt-0.5 shrink-0" weight="fill" /><div><p className="text-sm font-bold">{error.title}</p><p className="mt-1 text-xs leading-5">{error.message}</p></div></div><div className="flex gap-2">{onRetry && error.retryable && <Button size="sm" variant="secondary" onClick={onRetry}><ArrowClockwise />重试</Button>}{onClose && <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button>}</div></div></div>
}

function SectionHeading({ title, description }: { title: string; description: string }) {
  return <div className="mb-4"><h2 className="font-editorial text-2xl font-bold">{title}</h2><p className="mt-1 text-sm leading-6 text-[var(--muted)]">{description}</p></div>
}

function EvidenceList({ title, items, warning = false }: { title: string; items: string[]; warning?: boolean }) {
  if (!items.length) return null
  return <div className={`mt-5 border-t pt-4 ${warning ? "border-[#d9ae79]" : "border-[var(--line)]"}`}><p className="text-xs font-bold">{title}</p><ul className="mt-2 space-y-2">{items.map((item, index) => <li key={index} className="grid grid-cols-[16px_minmax(0,1fr)] gap-2 text-xs leading-5"><span aria-hidden="true">—</span><span>{item}</span></li>)}</ul></div>
}

function MissingEvidence({ text }: { text: string }) {
  return <div className="m-5 flex items-start gap-3 border-y border-[var(--line)] px-4 py-4 text-xs leading-5 text-[var(--muted)]"><WarningCircle className="mt-0.5 shrink-0" /><p>{text}</p></div>
}

function MissingSection({ text }: { text: string }) {
  return <div className="flex min-h-40 items-center gap-3 border-y border-[#e5c9a7] bg-[#fff9e9] px-5 py-6 text-sm leading-6 text-[#6f5513]"><WarningCircle className="shrink-0" /><p>{text}</p></div>
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 bg-white px-5 py-4"><p className="text-xs font-semibold text-[var(--muted)]">{label}</p><p className="font-data mt-2 truncate text-xl font-bold" title={value}>{value}</p></div>
}

function QueueMetric({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) {
  return <button type="button" className={`px-5 py-4 text-left ${active ? "bg-primary" : "bg-white hover:bg-[#f8f9f6]"}`} onClick={onClick} aria-pressed={active}><p className="text-xs font-semibold text-[var(--muted)]">{label}</p><p className="font-data mt-2 text-2xl font-bold">{value}</p></button>
}

function TechnicalFact({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return <div className="min-w-0 bg-[#fafbf8] px-5 py-4"><p className="text-xs font-semibold text-[var(--muted)]">{label}</p><p className={`${mono ? "font-data break-all text-xs" : "text-sm"} mt-2 font-semibold`}>{value || "未记录"}</p></div>
}

function LoadingField() {
  return <div className="h-20 animate-pulse bg-[#f3f5f0]" />
}

function DetailLoading() {
  return <><PageHeader index="03.2" title="正在打开评测包" description="系统正在读取新版内容与验证证据。" /><div className="mx-auto max-w-[1320px] space-y-6 px-5 py-8 md:px-8"><div className="h-40 animate-pulse bg-white" /><div className="h-72 animate-pulse bg-white" /><div className="h-72 animate-pulse bg-white" /></div></>
}

function InvalidPackage({ onBack }: { onBack: () => void }) {
  return <><PageHeader index="03.2" title="无法打开这条评测包" description="链接中的评测包编号不完整。" /><div className="mx-auto flex min-h-[50dvh] max-w-xl flex-col items-center justify-center px-5 text-center"><FileText size={32} weight="light" /><h2 className="font-editorial mt-4 text-2xl font-bold">评测包链接无效</h2><p className="mt-2 text-sm text-[var(--muted)]">返回二审队列后重新选择即可。</p><Button className="mt-5" onClick={onBack}><ArrowLeft />返回二审队列</Button></div></>
}

function recommendationLabel(value: NonNullable<EvaluationPackageDetail["recommendation"]>["verdict"]) {
  return { approve: "建议通过", reject: "建议拒绝", needs_attention: "建议重点核对", pending: "建议尚未形成" }[value]
}

function recommendationTone(value: NonNullable<EvaluationPackageDetail["recommendation"]>["verdict"]) {
  return value === "approve" ? "success" as const : value === "reject" ? "danger" as const : "warning" as const
}

function dimensionChangeLabel(value: "added" | "changed" | "removed" | "unchanged") {
  return { added: "新增", changed: "已调整", removed: "已移除", unchanged: "未变化" }[value]
}

function dimensionChangeTone(value: "added" | "changed" | "removed" | "unchanged") {
  return value === "added" ? "success" as const : value === "removed" ? "danger" as const : value === "changed" ? "warning" as const : "neutral" as const
}

function promptStageLabel(value: EvaluationPackagePromptChange["stage"]) {
  return value === "single" ? "单次完整评测" : value === "A" ? "分类与画质" : "美感维度"
}

function reviewStatusLabel(value: NonNullable<EvaluationPackageDetail["review"]>["status"]) {
  return { pending: "等待二审", approved: "已批准", rejected: "已拒绝", published: "已发布", archived: "已归档" }[value]
}

function formatTime(value: string | null | undefined) {
  if (!value) return "时间未记录"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN")
}

function boundedPercent(value: number | null | undefined) {
  return Math.max(0, Math.min(100, Number.isFinite(value) ? Math.round(value as number) : 0))
}

function nextPackageHref(item: EvaluationPackageSummary) {
  return item.status === "first_review"
    ? "/workflow/review/low-confidence"
    : `/workflow/releases/packages/${item.id}`
}

function nextPackageAction(item: EvaluationPackageSummary) {
  if (item.status === "first_review") return "前往一审"
  if (item.status === "second_review") return "打开二审评测包"
  if (item.status === "approved") return "查看并发布"
  return "查看当前评测包"
}
