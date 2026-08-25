import { ArrowRight, CheckCircle, Images, Package, Play, Sparkle } from "@phosphor-icons/react"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { ContentIdentityDrawer } from "@/components/content-identity-drawer"
import { WorkflowContextBadge } from "@/components/workflow-context-badge"
import { WorkflowStepper, type WorkflowStep } from "@/components/workflow-stepper"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, contentIngressApi, sourceIdentityApi } from "@/lib/api"
import { evaluationProductionApi } from "@/lib/evaluation-packages"
import type { AuditEvent, ContentIdentityRecord, EvaluationCategoryProfile, EvaluationProductionRun, MaterialPackage } from "@/lib/types"

export function IncrementalWorkspacePage() {
  const [selectedIdentityId, setSelectedIdentityId] = useState<number | null>(null)
  const categories = useQuery({
    queryKey: ["evaluation-categories", "incremental"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
  })
  const packages = useQuery({
    queryKey: ["material-packages", "incremental"],
    queryFn: () => api<{ items: MaterialPackage[] }>("/api/material-packages?limit=500"),
  })
  const runs = useQuery({
    queryKey: ["evaluation-production-runs", "incremental"],
    queryFn: () => evaluationProductionApi.list("incremental"),
    refetchInterval: 4000,
  })
  const ingressEvents = useQuery({
    queryKey: ["audit-events", "content-ingress", "incremental"],
    queryFn: () => api<{ items: AuditEvent[] }>("/api/audit-events?category=content_ingress&limit=500"),
    refetchInterval: 4000,
  })
  const contentRecords = useQuery({
    queryKey: ["content-ingress-records", "incremental"],
    queryFn: contentIngressApi.list,
    refetchInterval: 4000,
  })
  const identityVerifications = useQuery({
    queryKey: ["source-identity-verifications", "incremental"],
    queryFn: sourceIdentityApi.list,
  })
  const activeCategoryCount = useMemo(
    () => (categories.data?.items ?? []).filter((item) => item.status === "active").length,
    [categories.data?.items],
  )
  const availablePackages = packages.data?.items ?? []
  const latestRun = (runs.data?.items ?? [])[0] as EvaluationProductionRun | undefined
  const ingressCounts = useMemo(() => {
    const counts = { received: 0, awaiting: 0, packaged: 0, blocked: 0, duplicate: 0 }
    for (const event of ingressEvents.data?.items ?? []) {
      if (event.action === "incremental_package_created") {
        counts.packaged += 1
      } else if (event.action === "duplicate_reused") {
        counts.duplicate += 1
      } else if (event.action === "awaiting_material") {
        counts.awaiting += 1
      } else if (event.action === "stale" || event.action === "blocked_profile" || event.action === "blocked_identity") {
        counts.blocked += 1
      } else {
        counts.received += 1
      }
    }
    return counts
  }, [ingressEvents.data?.items])
  const recentContentRecords = (contentRecords.data?.items ?? []).slice(0, 5)
  const selectedIdentity = recentContentRecords.find((item) => item.id === selectedIdentityId) ?? null
  const selectedVerification = (identityVerifications.data?.items ?? []).find((item) => item.id === selectedIdentity?.identity_verification_id) ?? null
  const currentStep = latestRun?.status === "published" ? 8 : latestRun?.status === "awaiting_review" ? 6 : latestRun ? 4 : 2
  const steps: WorkflowStep[] = [
    { key: "category", label: "选择类目", note: `${activeCategoryCount} 个类目可用`, state: currentStep > 1 ? "completed" : "current", required: true },
    { key: "ingress", label: "导入增量素材", note: "手动导入或接入合同模拟事件", state: currentStep > 2 ? "completed" : "current", required: true },
    { key: "package", label: "编辑评测包", note: "复杂内容在二级编辑器完成", state: currentStep > 3 ? "completed" : "current", required: true },
    { key: "evaluate", label: "启动评测", note: "固定机制、模型和素材快照", state: currentStep >= 4 ? "current" : "pending", required: true },
    { key: "review", label: "人工纠偏", note: "只处理需要人工判断的结果", state: currentStep >= 6 ? "current" : "pending" },
    { key: "candidate", label: "AI 迭代与回归", note: "候选自动生成，结果等待人工决策", state: currentStep >= 7 ? "current" : "pending" },
    { key: "release", label: "正式发布", note: "机制轴与标签事实轴分开", state: currentStep >= 8 ? "completed" : "pending" },
  ]

  return (
    <>
      <PageHeader
        index="01"
        title="增量评测"
        description="新进入、未定性的素材从类目路由进入评测、纠偏、候选回归和人工发布链路。平台内核与存量回归共享，但任务上下文完全隔离。"
        actions={<><WorkflowContextBadge kind="incremental" /><Button asChild variant="secondary"><Link to="/workflow/materials/packages"><Images />导入或整理素材</Link></Button></>}
      />
      <div className="mx-auto shell-content space-y-6 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
        <WorkflowStepper workflowLabel="增量素材 → 评测 → 纠偏 → 候选回归 → 发布" steps={steps.slice(0, 5)} />
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
          <div className="bg-white px-5 py-6 md:px-6">
            <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-semibold text-[var(--muted)]">主操作</p><h2 className="mt-2 font-editorial text-2xl font-bold">选择类目并进入增量生产</h2></div><Badge tone={availablePackages.length ? "success" : "warning"}>{availablePackages.length ? `${availablePackages.length} 个素材包可用` : "等待素材包"}</Badge></div>
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <div className="border border-[var(--line)] bg-[#fafbf8] p-4"><div className="flex items-center gap-2"><Sparkle size={18} /><p className="text-sm font-bold">类目路由</p></div><p className="mt-2 text-xs leading-5 text-[var(--muted)]">按活动类目 profile 进入对应评测机制；未知 profile 只读诊断，不启动任务。</p><Button asChild size="sm" variant="secondary" className="mt-4"><Link to="/workflow/optimization/category-evaluation-v3-config?workflow_kind=incremental"><Package />查看类目机制</Link></Button></div>
              <div className="border border-[var(--line)] bg-[#fafbf8] p-4"><div className="flex items-center gap-2"><CheckCircle size={18} /><p className="text-sm font-bold">当前可执行项</p></div><p className="mt-2 text-xs leading-5 text-[var(--muted)]">评测启动、人工纠偏、候选回归和发布都沿既有人工闸门执行，不自动采纳。</p><Button asChild size="sm" className="mt-4"><Link to="/workflow/production-line">进入生产线<ArrowRight /></Link></Button></div>
            </div>
          </div>
          <aside className="bg-[#f7fadf] px-5 py-6 md:px-6"><p className="text-xs font-bold">最近一次增量运行</p>{latestRun ? <><p className="mt-3 text-lg font-semibold">{latestRun.material_package.name}</p><p className="mt-2 text-xs leading-5 text-[var(--muted)]">{latestRun.category.name} · {latestRun.current_stage_label} · {latestRun.progress.percent}%</p><div className="mt-5 flex flex-wrap gap-2"><Button asChild size="sm" variant="secondary"><Link to={`/workflow/production-line?run=${latestRun.id}`}>查看运行详情<ArrowRight /></Link></Button>{latestRun.pending_first_review_ids?.[0] && <Button asChild size="sm"><Link to={`/workflow/review/low-confidence?run=${latestRun.id}&evaluation=${latestRun.pending_first_review_ids[0]}`}>进入合同纠偏<ArrowRight /></Link></Button>}</div></> : <p className="mt-3 text-sm leading-6 text-[var(--muted)]">还没有增量运行。先导入素材包，再进入生产线。</p>}</aside>
        </section>
        <section className="border-y border-[var(--line)] bg-white px-5 py-5">
          <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-bold">本地接入合同状态</p><p className="mt-1 text-xs text-[var(--muted)]">这里只统计模拟接入与本地素材包路由，不代表真实上游已连接。</p></div><Badge tone="neutral">content-ingress-v1 / v2</Badge></div>
          <div className="mt-4 grid gap-px bg-[var(--line)] sm:grid-cols-5">
            <IngressMetric label="已接收" value={ingressCounts.received} />
            <IngressMetric label="等待素材" value={ingressCounts.awaiting} />
            <IngressMetric label="已组包" value={ingressCounts.packaged} />
            <IngressMetric label="已阻塞/忽略" value={ingressCounts.blocked} />
            <IngressMetric label="重复复用" value={ingressCounts.duplicate} />
          </div>
        </section>
        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4"><div><h2 className="text-sm font-bold">最近接入身份</h2><p className="mt-1 text-xs text-[var(--muted)]">一级页只保留生产路由所需状态；候选复合键与证据进入二级抽屉。</p></div><Badge tone={recentContentRecords.some((item) => item.identity_status === "pending_verification") ? "warning" : "neutral"}>{recentContentRecords.filter((item) => item.identity_status === "pending_verification").length} 条身份待签认</Badge></div>
          <div className="grid grid-cols-[minmax(0,1.1fr)_140px_140px_150px_150px_auto] gap-3 border-b border-[var(--line)] px-5 py-3 text-xs font-semibold text-[var(--muted)]"><span>来源 / 内容</span><span>类目</span><span>版本</span><span>身份状态</span><span>路由结果</span><span>操作</span></div>
          {recentContentRecords.map((record) => <IdentityRow key={record.id} record={record} onOpen={() => setSelectedIdentityId(record.id)} />)}
          {!recentContentRecords.length && !contentRecords.isFetching && <div className="px-5 py-10 text-center text-sm text-[var(--muted)]">暂无内容接入记录。</div>}
        </section>
        <section className="border-y border-[var(--line)] bg-white px-5 py-5"><div className="flex items-center gap-2"><Play size={18} weight="fill" /><h2 className="text-sm font-bold">主线说明</h2></div><p className="mt-2 text-xs leading-6 text-[var(--muted)]">自动 AI 迭代只负责生成完整候选机制和样本回归证据；是否启用候选、是否将正式标签事实发布给下游，仍由具备权限的人工分别决定。</p></section>
      </div>
      <ContentIdentityDrawer record={selectedIdentity} verification={selectedVerification} open={selectedIdentity != null} onOpenChange={(open) => !open && setSelectedIdentityId(null)} />
    </>
  )
}

function IdentityRow({ record, onOpen }: { record: ContentIdentityRecord; onOpen: () => void }) {
  const pending = record.identity_status === "pending_verification"
  const conflict = record.identity_status === "conflict"
  const statusLabel = pending ? "身份待签认" : conflict ? "身份冲突" : record.identity_status === "verified" ? "已签认" : "旧链路未签认"
  const routing = pending || conflict ? "阻断生产" : record.status === "ready" ? "可继续组包" : "等待素材"
  return <div className="grid grid-cols-[minmax(0,1.1fr)_140px_140px_150px_150px_auto] items-center gap-3 border-b border-[var(--line)] px-5 py-4 text-xs last:border-0"><div><p className="font-bold">{record.source_system}</p><p className="mt-1 font-data text-[11px] text-[var(--muted)]">{record.content_id}</p></div><span>{record.category_key}</span><span>{record.content_version}</span><Badge tone={record.identity_status === "verified" ? "success" : pending || conflict ? "warning" : "neutral"}>{statusLabel}</Badge><span>{routing}</span><Button size="sm" variant="secondary" onClick={onOpen}>查看身份</Button></div>
}

function IngressMetric({ label, value }: { label: string; value: number }) {
  return <div className="bg-white px-4 py-3"><p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-1 font-data text-lg font-bold">{value}</p></div>
}
