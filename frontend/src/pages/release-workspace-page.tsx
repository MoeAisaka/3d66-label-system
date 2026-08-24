import { ArrowCounterClockwise, ArrowRight, CheckCircle, DownloadSimple } from "@phosphor-icons/react"
import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, downloadApi, jsonBody } from "@/lib/api"
import type { EvaluationCategoryProfile, IntegrationStatus, LabelRelease, PromptMetricSnapshot, PromptVersion, RegressionSummary, User } from "@/lib/types"
import { DataTable, percent } from "@/pages/workflow-shared"

export function ReleaseWorkspacePage({ view }: { view: "decisions" | "metrics" | "history" }) {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/auth/me"),
  })
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })
  const items = prompts.data?.items ?? []
  const regressions = useQuery({
    queryKey: ["prompt-regressions"],
    queryFn: () => api<{ items: RegressionSummary[] }>("/api/prompt-regressions?limit=200"),
  })
  const labelReleases = useQuery({
    queryKey: ["label-releases"],
    queryFn: () => api<{ items: LabelRelease[] }>("/api/label-releases?limit=200"),
  })
  const currentPublishedVersionByContent = useMemo(() => {
    const versions = new Map<string, number>()
    for (const release of labelReleases.data?.items ?? []) {
      if (release.is_current && release.published_version != null) {
        versions.set(release.content_key, release.published_version)
      }
    }
    return versions
  }, [labelReleases.data?.items])
  const integrations = useQuery({
    queryKey: ["integration-status"],
    queryFn: () => api<IntegrationStatus>("/api/integration-status"),
  })
  const categoryProfiles = useQuery({
    queryKey: ["evaluation-categories"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
  })
  const queryClient = useQueryClient()
  const [metricPromptId, setMetricPromptId] = useState("")
  const [taskSetKey, setTaskSetKey] = useState("")
  const [metricSource, setMetricSource] = useState<"batch" | "evaluations">("batch")
  const [batchKey, setBatchKey] = useState("")
  const [evaluationIds, setEvaluationIds] = useState("")
  const [exportFormat, setExportFormat] = useState<"xlsx" | "csv" | "json">("xlsx")
  const [exportScope, setExportScope] = useState<"current" | "history">("current")
  const [exportCategory, setExportCategory] = useState("")
  const [exportPublishedFrom, setExportPublishedFrom] = useState("")
  const [exportPublishedTo, setExportPublishedTo] = useState("")
  const selectedMetricPromptId = Number(metricPromptId || items[0]?.id || 0)
  useEffect(() => {
    if (!metricPromptId && items[0]) setMetricPromptId(String(items[0].id))
  }, [items, metricPromptId])
  const metricSnapshots = useQuery({
    queryKey: ["prompt-metric-snapshots", selectedMetricPromptId],
    queryFn: () => api<{ items: PromptMetricSnapshot[] }>(
      `/api/prompts/${selectedMetricPromptId}/metric-snapshots`,
    ),
    enabled: view === "metrics" && selectedMetricPromptId > 0,
  })
  const freezeMetrics = useMutation({
    mutationFn: () => {
      const ids = Array.from(new Set(
        evaluationIds
          .split(/[\s,，]+/)
          .map((value) => Number(value))
          .filter((value) => Number.isInteger(value) && value > 0),
      ))
      return api<PromptMetricSnapshot>(
        `/api/prompts/${selectedMetricPromptId}/metric-snapshots`,
        {
          method: "POST",
          ...jsonBody({
            task_set_key: taskSetKey.trim(),
            batch_key: metricSource === "batch" ? batchKey.trim() : null,
            evaluation_ids: metricSource === "evaluations" ? ids : [],
          }),
        },
      )
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["prompt-metric-snapshots", selectedMetricPromptId],
      })
      toast.success("版本指标已按冻结任务集保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const rollback = useMutation({
    mutationFn: (promptId: number) => api(`/api/prompts/${promptId}/rollback`, { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      toast.success("已按回滚指针切回上一版本")
    },
    onError: (error) => toast.error(error.message),
  })
  const publishLabel = useMutation({
    mutationFn: (releaseId: number) => api<{ release: LabelRelease }>(
      `/api/label-releases/${releaseId}/approve-and-publish`, { method: "POST" },
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["label-releases"] })
      toast.success("人工二审通过，标签已生成新的发布版本")
    },
    onError: (error) => toast.error(error.message),
  })
  const rollbackLabel = useMutation({
    mutationFn: ({ publishedLabelId, rollbackKey }: { publishedLabelId: number; rollbackKey: string }) =>
      api<{ release: LabelRelease }>(
        `/api/published-labels/${publishedLabelId}/rollback`,
        { method: "POST", ...jsonBody({ rollback_key: rollbackKey }) },
      ),
    onSuccess: async ({ release }) => {
      await queryClient.invalidateQueries({ queryKey: ["label-releases"] })
      toast.success(`已回滚并生成当前版本 v${release.published_version ?? "—"}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const exportLabels = useMutation({
    mutationFn: () => {
      return downloadApi(
        "/api/published-labels/export",
        `published-labels.${exportFormat}`,
        {
          method: "POST",
          ...jsonBody({
            format: exportFormat,
            scope: exportScope,
            category_key: exportCategory || null,
            published_from: exportPublishedFrom
              ? new Date(`${exportPublishedFrom}T00:00:00`).toISOString()
              : null,
            published_to: exportPublishedTo
              ? new Date(`${exportPublishedTo}T23:59:59.999`).toISOString()
              : null,
          }),
        },
      )
    },
    onSuccess: ({ rowCount }) => {
      toast.success(rowCount == null ? "正式标签已导出" : `已导出 ${rowCount} 条正式标签`)
    },
    onError: (error) => toast.error(error.message),
  })

  if (view === "decisions") {
    const pending = (regressions.data?.items ?? []).filter(
      (run) => run.regression_mode === "paired" && run.approval_status === "pending",
    )
    return (
      <>
        <PageHeader index="04.1" title="待发布决策" description="人工二审面向提示词候选与回归证据，不重新审核图片。只有系统建议通过且人工批准后，候选才具备显式发布资格。" />
        <div className="mx-auto shell-content px-5 py-8 md:px-8 lg:px-10">
          <section className="mb-8 border-y border-[var(--line-strong)] bg-white">
            <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-4">
              <div>
                <h2 className="font-editorial text-xl font-bold">正式标签发布</h2>
                <p className="mt-1 text-xs leading-5 text-[var(--muted)]">只有已完成人工初审的结果可以进入发布；模型候选和人工过程数据不会暴露给下游消费方。</p>
              </div>
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge tone={integrations.data?.upstream_content_ingress.configured ? "success" : "warning"}>上游 {integrations.data?.upstream_content_ingress.configured ? "已留接口" : "待配置令牌"}</Badge>
                <Badge tone={integrations.data?.downstream_label_consumer.configured ? "success" : "warning"}>下游 {integrations.data?.downstream_label_consumer.configured ? "可拉取" : "待配置令牌"}</Badge>
                <Badge>外部写入关闭</Badge>
              </div>
            </div>
            <DataTable
              loading={labelReleases.isLoading}
              empty="还没有人工确认标签进入发布队列"
              headers={["内容", "类目", "人工来源", "状态", "版本", "操作"]}
              rows={(labelReleases.data?.items ?? []).map((release) => [
                <span key="content" className="font-data text-xs">{release.content_key}</span>,
                <span key="category">{release.category_key}</span>,
                <span key="source" className="font-data text-xs">评测 #{release.evaluation_id ?? "—"} · 审核 #{release.final_review_id ?? "—"}</span>,
                <Badge key="status" tone={release.status === "published" ? "success" : release.status === "pending_review" ? "warning" : "neutral"}>{release.status === "published" ? "已发布" : release.status === "pending_review" ? "待二审" : release.status}</Badge>,
                <span key="version" className="font-data">{release.published_version == null ? "—" : `v${release.published_version}`}</span>,
                release.status === "pending_review" && me.data?.is_admin ? (
                  <Button key="publish" size="sm" onClick={() => publishLabel.mutate(release.id)} disabled={publishLabel.isPending}>二审通过并发布<CheckCircle /></Button>
                ) : release.status === "published" && release.is_current ? (
                  <span key="current" className="text-xs font-semibold text-[#3f6b35]">当前生效</span>
                ) : release.status === "published" && release.published_label_id != null && release.published_version != null && me.data?.is_admin ? (
                  <Button
                    key="rollback"
                    size="sm"
                    variant="danger"
                    disabled={rollbackLabel.isPending}
                    onClick={() => {
                      const publishedLabelId = release.published_label_id
                      const currentVersion = currentPublishedVersionByContent.get(release.content_key)
                      if (publishedLabelId == null || currentVersion == null) {
                        toast.error("无法确认当前生效版本，请刷新页面后重试")
                        return
                      }
                      if (!window.confirm(`将“${release.content_key}”从当前 v${currentVersion} 回滚到历史 v${release.published_version}？\n\n系统会生成一个新的正式版本并通知下游，不会删除任何历史记录。`)) return
                      rollbackLabel.mutate({
                        publishedLabelId,
                        rollbackKey: `manual-ui:published-${publishedLabelId}:from-v${currentVersion}`,
                      })
                    }}
                  >
                    回滚到 v{release.published_version}<ArrowCounterClockwise />
                  </Button>
                ) : (
                  <span key="noop" className="text-xs text-[var(--muted)]">{release.status === "published" ? "历史版本" : "等待管理员"}</span>
                ),
              ])}
            />
            <div className="grid gap-4 border-t border-[var(--line-strong)] bg-[#fafbf8] px-5 py-5 md:grid-cols-2 xl:grid-cols-4 xl:items-end">
              <label>
                <span className="mb-2 block text-xs font-semibold">导出范围</span>
                <select
                  className="h-11 w-full border border-[var(--line-strong)] bg-white px-3 text-sm"
                  value={exportScope}
                  onChange={(event) => setExportScope(event.target.value as "current" | "history")}
                >
                  <option value="current">当前生效标签</option>
                  <option value="history">全部历史版本</option>
                </select>
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">文件格式</span>
                <select
                  className="h-11 w-full border border-[var(--line-strong)] bg-white px-3 text-sm"
                  value={exportFormat}
                  onChange={(event) => setExportFormat(event.target.value as "xlsx" | "csv" | "json")}
                >
                  <option value="xlsx">Excel（推荐）</option>
                  <option value="csv">CSV</option>
                  <option value="json">JSON</option>
                </select>
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">类目筛选</span>
                <select
                  className="h-11 w-full border border-[var(--line-strong)] bg-white px-3 text-sm"
                  value={exportCategory}
                  onChange={(event) => setExportCategory(event.target.value)}
                >
                  <option value="">全部类目</option>
                  {(categoryProfiles.data?.items ?? []).map((category) => (
                    <option key={category.category_key} value={category.category_key}>
                      {category.display_name}（{category.category_key}）
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">发布时间起</span>
                <Input
                  type="date"
                  value={exportPublishedFrom}
                  max={exportPublishedTo || undefined}
                  onChange={(event) => setExportPublishedFrom(event.target.value)}
                />
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">发布时间止</span>
                <Input
                  type="date"
                  value={exportPublishedTo}
                  min={exportPublishedFrom || undefined}
                  onChange={(event) => setExportPublishedTo(event.target.value)}
                />
              </label>
              <Button
                type="button"
                onClick={() => exportLabels.mutate()}
                disabled={exportLabels.isPending}
              >
                <DownloadSimple />
                {exportLabels.isPending ? "正在生成" : "下载正式标签"}
              </Button>
              <p className="text-xs leading-5 text-[var(--muted)] md:col-span-2 xl:col-span-3">
                只导出已通过二审的正式标签；当前生效范围不会包含已被新版本替代的记录。单次最多 10,000 条。
              </p>
            </div>
          </section>
          <DataTable
            loading={regressions.isLoading}
            empty="当前没有待人工决策的配对回归"
            headers={["回归任务", "候选提示词", "完成进度", "系统建议", "人工状态", "操作"]}
            rows={pending.map((run) => [
              <span key="name" className="font-semibold">{run.name}</span>,
              <span key="prompt" className="font-data">#{run.trigger_prompt_id ?? "—"}</span>,
              <span key="progress" className="font-data">{run.completed}/{run.total}</span>,
              <Badge key="recommendation" tone={run.recommendation === "pass" ? "success" : run.recommendation === "fail" ? "danger" : "warning"}>{run.recommendation === "pass" ? "建议通过" : run.recommendation === "fail" ? "建议拒绝" : "尚未完成"}</Badge>,
              <Badge key="approval">待人工二审</Badge>,
              <Button key="open" asChild size="sm"><Link to={`/workflow/optimization/paired-regression?run=${run.id}`}>查看回归证据<ArrowRight /></Link></Button>,
            ])}
          />
        </div>
      </>
    )
  }

  if (view === "metrics") {
    const canFreeze = (
      selectedMetricPromptId > 0
      && Boolean(taskSetKey.trim())
      && (
        metricSource === "batch"
          ? Boolean(batchKey.trim())
          : evaluationIds.split(/[\s,，]+/).some((value) => Number(value) > 0)
      )
    )
    return (
      <>
        <PageHeader index="04.2" title="版本指标" description="发布依据必须来自冻结任务集或明确批次；实时全量聚合只保留为运营参考，未完成人工初审的结果不会被当作正确。" />
        <div className="mx-auto shell-content space-y-8 px-5 py-8 md:px-8 lg:px-10">
          <section className="border-y border-[var(--line-strong)] bg-white">
            <div className="border-b border-[var(--line)] px-5 py-4">
              <h2 className="font-editorial text-xl font-bold">冻结指标快照</h2>
              <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                选择提示词版本并以批次键或明确评测结果 ID 冻结分母；同一任务集哈希只保存一次。
              </p>
            </div>
            <div className="grid gap-4 px-5 py-5 lg:grid-cols-2">
              <label>
                <span className="mb-2 block text-xs font-semibold">提示词版本</span>
                <select
                  className="h-11 w-full border border-[var(--line-strong)] bg-white px-3 text-sm"
                  value={metricPromptId}
                  onChange={(event) => setMetricPromptId(event.target.value)}
                >
                  {items.map((prompt) => (
                    <option key={prompt.id} value={prompt.id}>
                      {prompt.stage} · {prompt.version} · {prompt.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="mb-2 block text-xs font-semibold">冻结任务集业务键</span>
                <Input
                  value={taskSetKey}
                  onChange={(event) => setTaskSetKey(event.target.value)}
                  placeholder="例如：release-2026-07-batch-01"
                />
              </label>
              <div>
                <span className="mb-2 block text-xs font-semibold">冻结来源</span>
                <div className="grid grid-cols-2 border border-[var(--line-strong)]">
                  <button
                    type="button"
                    className={`h-11 border-r border-[var(--line)] text-sm font-bold ${metricSource === "batch" ? "bg-primary" : "bg-white"}`}
                    onClick={() => setMetricSource("batch")}
                  >
                    任务批次
                  </button>
                  <button
                    type="button"
                    className={`h-11 text-sm font-bold ${metricSource === "evaluations" ? "bg-primary" : "bg-white"}`}
                    onClick={() => setMetricSource("evaluations")}
                  >
                    评测结果任务集
                  </button>
                </div>
              </div>
              <label>
                <span className="mb-2 block text-xs font-semibold">
                  {metricSource === "batch" ? "任务批次键" : "评测结果 ID（逗号或空格分隔）"}
                </span>
                {metricSource === "batch" ? (
                  <Input
                    value={batchKey}
                    onChange={(event) => setBatchKey(event.target.value)}
                    placeholder="例如：job-batch-20260729"
                  />
                ) : (
                  <Input
                    value={evaluationIds}
                    onChange={(event) => setEvaluationIds(event.target.value)}
                    placeholder="例如：101, 102, 103"
                  />
                )}
              </label>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-4">
              <p className="text-xs text-[var(--muted)]">
                创建后不会随新评测或后续审核自动变化；如需新口径，创建新的冻结任务集。
              </p>
              <Button
                onClick={() => freezeMetrics.mutate()}
                disabled={!canFreeze || freezeMetrics.isPending}
              >
                {freezeMetrics.isPending ? "正在冻结" : "保存冻结快照"}
              </Button>
            </div>
          </section>
          <DataTable
            loading={metricSnapshots.isLoading}
            empty="当前提示词还没有冻结指标快照"
            headers={["任务集", "冻结哈希", "样本准确率", "等级准确率", "审核覆盖率", "N / 已审", "创建时间"]}
            rows={(metricSnapshots.data?.items ?? []).map((snapshot) => [
              <div key="key"><p className="font-semibold">{snapshot.task_set_key}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">评测 {snapshot.evaluation_ids.length} 条</p></div>,
              <span key="hash" className="font-data text-xs">{snapshot.task_set_hash.slice(0, 12)}</span>,
              <strong key="accuracy" className="font-data">{percent(snapshot.metrics.sample_accuracy)}</strong>,
              <span key="grade" className="font-data">{percent(snapshot.metrics.grade_accuracy)}</span>,
              <span key="coverage" className="font-data">{percent(snapshot.metrics.review_coverage)}</span>,
              <span key="n" className="font-data">{snapshot.total_count} / {snapshot.reviewed_count}</span>,
              <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(snapshot.created_at).toLocaleString("zh-CN")}</span>,
            ])}
          />
          <div>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="font-editorial text-xl font-bold">实时全量参考</h2>
                <p className="mt-1 text-xs text-[var(--muted)]">只用于发现趋势，不作为提示词发布依据。</p>
              </div>
              <Badge tone="warning">非冻结口径</Badge>
            </div>
          <DataTable
            loading={prompts.isLoading}
            empty="没有提示词版本"
            headers={["版本", "状态", "样本准确率", "纠偏率", "等级准确率", "审核覆盖率", "N / 总评测"]}
            rows={items.map((prompt) => {
              const metrics = prompt.metrics
              return [
                <div key="version"><p className="font-data font-semibold">{prompt.version}</p><p className="mt-1 text-xs text-[var(--muted)]">{prompt.stage} 阶段 · {prompt.name}</p></div>,
                <Badge key="status" tone={prompt.status === "published" ? "success" : "neutral"}>{promptStatus(prompt.status)}</Badge>,
                <strong key="accuracy" className="font-data">{percent(metrics?.sample_accuracy)}</strong>,
                <span key="correction" className="font-data">{metrics?.sample_size_n ? percent((metrics.corrected_sample_count ?? 0) / metrics.sample_size_n) : "—"}</span>,
                <span key="grade" className="font-data">{percent(metrics?.grade_accuracy)}</span>,
                <span key="coverage" className="font-data">{percent(metrics?.review_coverage)}</span>,
                <span key="n" className="font-data">{metrics?.sample_size_n ?? 0} / {metrics?.total_evaluations ?? 0}</span>,
              ]
            })}
          />
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader index="04.3" title="版本历史与回滚" description="版本只追加；已发布版本保留可验证回滚指针和金丝雀状态。回滚是显式人工动作，不会由失败状态自动触发。" />
      <div className="mx-auto shell-content px-5 py-8 md:px-8 lg:px-10">
        <DataTable
          loading={prompts.isLoading}
          empty="没有提示词版本"
          headers={["版本", "阶段", "状态", "回滚指针", "金丝雀", "更新时间", "动作"]}
          rows={items.map((prompt) => [
            <div key="version"><p className="font-data font-semibold">{prompt.version}</p><p className="mt-1 text-xs text-[var(--muted)]">{prompt.name}</p></div>,
            <Badge key="stage">{prompt.stage}</Badge>,
            <Badge key="status" tone={prompt.status === "published" ? "success" : "neutral"}>{promptStatus(prompt.status)}</Badge>,
            <span key="rollback" className="font-data">{prompt.rollback_prompt_id ? `#${prompt.rollback_prompt_id}` : "未建立"}</span>,
            <Badge key="canary" tone={prompt.canary_status === "passed" ? "success" : prompt.canary_status === "failed" ? "danger" : "neutral"}>{canaryStatus(prompt.canary_status)}</Badge>,
            <span key="time" className="font-data text-xs text-[var(--muted)]">{new Date(prompt.updated_at).toLocaleString("zh-CN")}</span>,
            <Button key="action" size="sm" variant="secondary" disabled={prompt.status !== "published" || !prompt.rollback_prompt_id || rollback.isPending} onClick={() => rollback.mutate(prompt.id)}>按指针回滚</Button>,
          ])}
        />
      </div>
    </>
  )
}

function promptStatus(value: PromptVersion["status"]) {
  return ({ draft: "候选草稿", published: "已发布", archived: "已归档" } as const)[value]
}

function canaryStatus(value: PromptVersion["canary_status"]) {
  return ({ not_started: "未开始", planned: "已计划", running: "运行中", passed: "已通过", failed: "失败" } as const)[value ?? "not_started"]
}
