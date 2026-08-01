import { useEffect, useMemo, useState } from "react"
import { ArrowClockwise, ArrowLeft, CheckCircle, ShieldCheck, XCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { RegressionDetail, RegressionSummary, User } from "@/lib/types"

type PairedRegressionItem = RegressionDetail["items"][number] & {
  sample_role?: "target_error" | "stable_control" | "blind_holdout"
}

type PairedRegressionDetail = Omit<RegressionDetail, "items"> & {
  items: PairedRegressionItem[]
}

const roleNames: Record<NonNullable<PairedRegressionItem["sample_role"]>, string> = {
  target_error: "目标错例",
  stable_control: "稳定对照",
  blind_holdout: "锁定盲测",
}

export function PairedRegressionPage({ user }: { user: User }) {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const reviewer = user.username
  const [note, setNote] = useState("")
  const regressions = useQuery({
    queryKey: ["prompt-regressions"],
    queryFn: () => api<{ items: RegressionSummary[] }>("/api/prompt-regressions?limit=200"),
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (item) => item.regression_mode === "paired" && ["queued", "running"].includes(item.status),
      )
        ? 3000
        : false,
  })
  const pairedRuns = useMemo(
    () => (regressions.data?.items ?? []).filter((item) => item.regression_mode === "paired"),
    [regressions.data?.items],
  )
  const requestedRunId = Number(searchParams.get("run"))
  const selectedRun =
    pairedRuns.find((item) => item.id === requestedRunId)
    ?? pairedRuns[0]
  const selectedRunId = selectedRun?.id ?? 0
  const detail = useQuery({
    queryKey: ["prompt-regression", selectedRunId],
    queryFn: () => api<PairedRegressionDetail>(`/api/prompt-regressions/${selectedRunId}`),
    enabled: selectedRunId > 0,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.summary.status ?? "")
        ? 3000
        : false,
  })

  useEffect(() => {
    if (selectedRunId > 0 && requestedRunId !== selectedRunId) {
      setSearchParams({ run: String(selectedRunId) }, { replace: true })
    }
  }, [requestedRunId, selectedRunId, setSearchParams])

  useEffect(() => {
    setNote("")
  }, [selectedRunId])

  const approval = useMutation({
    mutationFn: (status: "approved" | "rejected") =>
      api(`/api/paired-regressions/${selectedRunId}/approval`, {
        method: "POST",
        ...jsonBody({
          status,
          reviewer_name: reviewer.trim(),
          note: note.trim(),
        }),
      }),
    onSuccess: async (_data, status) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["prompt-regressions"] }),
        queryClient.invalidateQueries({ queryKey: ["prompt-regression", selectedRunId] }),
      ])
      toast.success(
        status === "approved"
          ? "配对回归已人工批准；候选仍需显式发布"
          : "配对回归已人工驳回",
      )
    },
    onError: (error) => toast.error(error.message),
  })

  const summary = detail.data?.summary ?? selectedRun
  const canDecide = Boolean(
    summary
    && summary.recommendation !== "pending"
    && summary.approval_status === "pending"
    && reviewer.trim()
    && note.trim(),
  )

  return (
    <>
      <PageHeader
        index="03.6"
        title="小样本配对回归"
        description="独立查看候选与基线的冻结配对证据；盲测完成前不展示答案，人工结论只冻结发布资格，不会自动发布。"
        actions={
          <>
            <Button variant="secondary" asChild>
              <Link to="/workflow/optimization/candidates"><ArrowLeft />返回候选提示词</Link>
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                regressions.refetch()
                if (selectedRunId) detail.refetch()
              }}
            >
              <ArrowClockwise />刷新
            </Button>
          </>
        }
      />

      <div className="mx-auto grid max-w-[1720px] lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="border-r border-[var(--line)] bg-white p-4 lg:min-h-[calc(100dvh-125px)]">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">配对回归任务</h2>
            <span className="font-data text-xs text-[var(--muted)]">{pairedRuns.length}</span>
          </div>
          <div className="space-y-1">
            {pairedRuns.map((run) => (
              <button
                key={run.id}
                type="button"
                aria-pressed={run.id === selectedRunId}
                className={`w-full border px-3 py-3 text-left transition-colors ${
                  run.id === selectedRunId
                    ? "border-[var(--line-strong)] bg-[#f6f9dc]"
                    : "border-transparent hover:bg-[#f8f9f6]"
                }`}
                onClick={() => setSearchParams({ run: String(run.id) })}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-data text-xs font-semibold">#{run.id}</span>
                  <Badge tone={statusTone(run)}>{statusName(run)}</Badge>
                </div>
                <p className="mt-2 line-clamp-2 text-sm font-semibold">{run.name}</p>
                <p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">
                  {run.completed}/{run.total} 已完成 · 提示词 #{run.trigger_prompt_id ?? "—"}
                </p>
              </button>
            ))}
            {!regressions.isLoading && !pairedRuns.length && (
              <div className="border-y border-[var(--line)] px-3 py-8 text-center text-xs leading-5 text-[var(--muted)]">
                还没有配对回归。请先在“候选提示词”完成候选物化与回归交接。
              </div>
            )}
          </div>
        </aside>

        <main className="min-w-0 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
          {summary ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-5 border-b border-[var(--line-strong)] pb-6">
                <div>
                  <p className="font-data text-xs text-[var(--muted)]">
                    回归 #{summary.id} · 候选提示词 #{summary.trigger_prompt_id ?? "—"} · {summary.metric_rules_version ?? "指标规则未记录"}
                  </p>
                  <h2 className="font-editorial mt-2 text-3xl font-bold">{summary.name}</h2>
                  <p className="mt-2 text-sm text-[var(--muted)]">
                    {summary.sample_set_name} · 基线策略 #{summary.baseline_strategy_bundle_id ?? "—"} → 候选策略 #{summary.candidate_strategy_bundle_id ?? "—"}
                  </p>
                </div>
                <Badge tone={statusTone(summary)}>{statusName(summary)}</Badge>
              </div>

              <section className="mt-7 grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="完成进度" value={`${summary.completed}/${summary.total}`} />
                <Metric label="通过率" value={`${Math.round(summary.pass_rate * 100)}%`} />
                <Metric label="系统建议" value={recommendationName(summary.recommendation)} />
                <Metric label="人工结论" value={approvalName(summary.approval_status)} />
              </section>

              <section className="mt-7 border-y border-[var(--line-strong)] bg-white">
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
                  <div>
                    <div className="flex items-center gap-2"><ShieldCheck /><h3 className="font-semibold">冻结样本证据</h3></div>
                    <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                      每项都绑定同一冻结真值、基线与候选策略；进行中的锁定盲测由服务端保持答案隔离。
                    </p>
                  </div>
                  <Badge>{detail.data?.items.length ?? 0} 项</Badge>
                </div>
                <div className="divide-y divide-[var(--line)]">
                  {detail.data?.items.map((item) => (
                    <div key={item.id} className="grid gap-3 px-5 py-4 sm:grid-cols-[64px_minmax(0,1fr)_130px_120px] sm:items-center">
                      <img src={item.image_url} alt="" className="size-14 border border-[var(--line)] object-cover" />
                      <div className="min-w-0">
                        <p className="file-name truncate text-sm">{item.asset_name}</p>
                        <p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">
                          样本 #{item.sample_item_id} · 证据项 #{item.id}
                        </p>
                      </div>
                      <Badge>{item.sample_role ? roleNames[item.sample_role] : "角色已冻结"}</Badge>
                      <Badge tone={item.passed === true ? "success" : item.passed === false ? "danger" : "neutral"}>
                        {item.passed === true ? "通过" : item.passed === false ? "未通过" : item.status}
                      </Badge>
                    </div>
                  ))}
                  {!detail.isLoading && !detail.data?.items.length && (
                    <p className="px-5 py-10 text-center text-sm text-[var(--muted)]">当前回归还没有可展示的冻结样本证据。</p>
                  )}
                </div>
              </section>

              <section className="mt-7 border-y border-[var(--line-strong)] bg-white p-5">
                <div className="flex items-start gap-3">
                  {summary.approval_status === "approved" ? <CheckCircle className="mt-0.5 text-[#386309]" /> : summary.approval_status === "rejected" ? <XCircle className="mt-0.5 text-[#9b2c25]" /> : <ShieldCheck className="mt-0.5" />}
                  <div>
                    <h3 className="font-semibold">人工发布前结论</h3>
                    <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
                      {summary.approval_status === "pending"
                        ? "系统建议完成后，可在此批准或驳回。批准只解锁候选的显式发布资格。"
                        : `结论已冻结：${approvalName(summary.approval_status)} · ${summary.approved_by || "—"}。${summary.approval_note || ""}`}
                    </p>
                  </div>
                </div>
                {summary.approval_status === "pending" && (
                  <div className="mt-5 grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)_auto_auto] lg:items-end">
                    <label>
                      <span className="mb-2 block text-xs font-semibold">审核账号（当前登录）</span>
                      <Input value={reviewer} readOnly />
                    </label>
                    <label>
                      <span className="mb-2 block text-xs font-semibold">人工结论说明（必填）</span>
                      <Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="说明是否接受系统回归结论" />
                    </label>
                    <Button
                      variant="secondary"
                      disabled={!canDecide || approval.isPending}
                      onClick={() => approval.mutate("rejected")}
                    >
                      驳回候选
                    </Button>
                    <Button
                      disabled={!canDecide || summary.recommendation !== "pass" || approval.isPending}
                      onClick={() => approval.mutate("approved")}
                    >
                      批准候选
                    </Button>
                  </div>
                )}
              </section>
            </>
          ) : (
            <div className="min-h-[60dvh] animate-pulse bg-white" />
          )}
        </main>
      </div>
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 text-xl font-semibold">{value}</p>
    </div>
  )
}

function recommendationName(value: RegressionSummary["recommendation"]) {
  if (value === "pass") return "建议通过"
  if (value === "fail") return "建议拒绝"
  return "尚未完成"
}

function approvalName(value: RegressionSummary["approval_status"]) {
  if (value === "approved") return "已批准"
  if (value === "rejected") return "已驳回"
  return "待人工二审"
}

function statusName(run: RegressionSummary) {
  if (run.approval_status === "approved") return "人工已批准"
  if (run.approval_status === "rejected") return "人工已驳回"
  if (run.recommendation === "pass") return "建议通过"
  if (run.recommendation === "fail") return "建议拒绝"
  if (run.status === "running") return "回归进行中"
  if (run.status === "queued") return "等待运行"
  return run.status
}

function statusTone(run: RegressionSummary): "success" | "danger" | "warning" | "active" | "neutral" {
  if (run.approval_status === "approved") return "success"
  if (run.approval_status === "rejected" || run.recommendation === "fail") return "danger"
  if (run.recommendation === "pass") return "success"
  if (["queued", "running"].includes(run.status)) return "active"
  return "neutral"
}
