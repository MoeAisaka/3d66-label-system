import {
  ArrowClockwise,
  CaretDown,
  CheckCircle,
  Circle,
  CircleNotch,
  Plus,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ApiError, api, jsonBody } from "@/lib/api"
import type { CanaryRun, CanaryRunState } from "@/lib/types"

const PREFLIGHT_SCHEMA = "p0e-xlsx-preflight-v1"
const MANIFEST_VERSION = "p0e-frozen-manifest-v1"
const CANDIDATE_SCHEMA = "p0e-candidate-preview-v1"

const stateLabels: Record<CanaryRunState, string> = {
  draft: "草稿",
  preflight_ready: "预检证据就绪",
  approvals_ready: "人工审批就绪",
  freeze_ready: "冻结能力登记就绪",
  candidate_ready: "候选证据就绪",
  human_review_ready: "待逐项人工审核",
  failed: "已标记失败",
  cancelled: "已取消",
}

const gateStates = [
  "draft",
  "preflight_ready",
  "approvals_ready",
  "freeze_ready",
  "candidate_ready",
  "human_review_ready",
] as const

type GateState = (typeof gateStates)[number]
type TransitionState = Exclude<GateState, "draft">
type TerminalState = "failed" | "cancelled"

const terminalStates = new Set<CanaryRunState>([
  "human_review_ready",
  "failed",
  "cancelled",
])

const evidenceKeyByState: Partial<Record<GateState, string>> = {
  preflight_ready: "xlsx_preflight",
  approvals_ready: "approval",
  freeze_ready: "fetch_config",
  candidate_ready: "manifest",
  human_review_ready: "human_review_handoff",
}

function stateTone(state: CanaryRunState) {
  if (state === "human_review_ready") return "success" as const
  if (state === "failed" || state === "cancelled") return "danger" as const
  if (state === "draft") return "neutral" as const
  return "active" as const
}

export function CanaryRunsPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const pendingSelection = useRef<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)

  const runs = useQuery({
    queryKey: ["canary-runs"],
    queryFn: () => api<{ items: CanaryRun[] }>("/api/canary-runs?limit=200"),
  })

  useEffect(() => {
    const items = runs.data?.items
    if (!items?.length) {
      if (items && selectedId) setSelectedId(null)
      return
    }
    if (pendingSelection.current) {
      if (items.some((item) => item.run_id === pendingSelection.current)) {
        pendingSelection.current = null
      } else {
        return
      }
    }
    if (!selectedId || !items.some((item) => item.run_id === selectedId)) {
      setSelectedId(items[0].run_id)
    }
  }, [runs.data?.items, selectedId])

  const detail = useQuery({
    queryKey: ["canary-run", selectedId],
    queryFn: () => api<CanaryRun>(`/api/canary-runs/${encodeURIComponent(selectedId ?? "")}`),
    enabled: Boolean(selectedId),
  })

  const createRun = useMutation({
    mutationFn: (payload: {
      domain: "3D"
      target_size: number
      seed: string
      display_name: string | null
    }) => api<CanaryRun>("/api/canary-runs", {
      method: "POST",
      ...jsonBody(payload),
    }),
    onSuccess: async (run) => {
      pendingSelection.current = run.run_id
      queryClient.setQueryData<{ items: CanaryRun[] }>(["canary-runs"], (current) => ({
        items: [run, ...(current?.items ?? []).filter((item) => item.run_id !== run.run_id)],
      }))
      queryClient.setQueryData(["canary-run", run.run_id], run)
      setSelectedId(run.run_id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["canary-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["canary-run", run.run_id] }),
      ])
      toast.success("金丝雀运行已创建或幂等复用")
    },
    onError: (error) => toast.error(error.message),
  })

  const advance = useMutation({
    mutationFn: ({
      runId,
      fingerprint,
      transition,
      evidence,
    }: {
      runId: string
      fingerprint: string
      transition: TransitionState
      evidence: Record<string, unknown>
    }) => api<CanaryRun>(
      `/api/canary-runs/${encodeURIComponent(runId)}/transitions/${transition}`,
      {
        method: "POST",
        ...jsonBody({
          expected_snapshot_fingerprint: fingerprint,
          evidence,
        }),
      },
    ),
    onMutate: () => setActionError(null),
    onSuccess: async (run) => {
      queryClient.setQueryData(["canary-run", run.run_id], run)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["canary-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["canary-run", run.run_id] }),
      ])
      toast.success(`门禁已推进：${stateLabels[run.state]}`)
    },
    onError: (error) => {
      setActionError(error)
      if (!(error instanceof ApiError && error.status === 409)) {
        toast.error(error.message)
      }
    },
  })

  const terminate = useMutation({
    mutationFn: ({
      runId,
      fingerprint,
      target,
      reason,
    }: {
      runId: string
      fingerprint: string
      target: TerminalState
      reason: string
    }) => api<CanaryRun>(`/api/canary-runs/${encodeURIComponent(runId)}/${target === "cancelled" ? "cancel" : "fail"}`, {
      method: "POST",
      ...jsonBody({
        expected_snapshot_fingerprint: fingerprint,
        reason,
      }),
    }),
    onMutate: () => setActionError(null),
    onSuccess: async (run) => {
      queryClient.setQueryData(["canary-run", run.run_id], run)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["canary-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["canary-run", run.run_id] }),
      ])
      toast.success(run.state === "cancelled" ? "运行已取消" : "运行已标记失败")
    },
    onError: (error) => {
      setActionError(error)
      if (!(error instanceof ApiError && error.status === 409)) {
        toast.error(error.message)
      }
    },
  })

  async function refreshSelected() {
    setActionError(null)
    await Promise.all([runs.refetch(), selectedId ? detail.refetch() : Promise.resolve()])
  }

  return (
    <>
      <PageHeader
        index="09"
        title="金丝雀运行"
        description="把 P0-E 的门禁证据按顺序登记到认证 API；页面只编排状态，不执行数据导入或评测。"
        actions={
          <Button
            variant="secondary"
            onClick={refreshSelected}
            disabled={runs.isFetching || detail.isFetching}
          >
            <ArrowClockwise className={runs.isFetching || detail.isFetching ? "animate-spin" : ""} />
            手动刷新
          </Button>
        }
      />

      <div className="mx-auto max-w-[1540px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <SafetyBoundary />

        <CreateRunForm
          pending={createRun.isPending}
          error={createRun.error}
          onCreate={(payload) => createRun.mutate(payload)}
        />

        <div className="mt-9 grid gap-8 xl:grid-cols-[320px_minmax(0,1fr)]">
          <aside>
            <div className="mb-3 flex items-end justify-between gap-4">
              <div>
                <h2 className="font-editorial text-2xl font-bold">运行列表</h2>
                <p className="mt-1 text-xs text-[var(--muted)]">按最近更新排序</p>
              </div>
              <span className="font-data text-xs text-[var(--muted)]">{runs.data?.items.length ?? 0}</span>
            </div>
            <RunList
              items={runs.data?.items ?? []}
              selectedId={selectedId}
              loading={runs.isLoading}
              error={runs.error}
              onSelect={(runId) => {
                setActionError(null)
                setSelectedId(runId)
              }}
              onRetry={() => runs.refetch()}
            />
          </aside>

          <main className="min-w-0">
            {detail.isLoading ? (
              <div className="h-[520px] animate-pulse border-y border-[var(--line)] bg-white" />
            ) : detail.error ? (
              <ErrorNotice error={detail.error} onRefresh={refreshSelected} />
            ) : detail.data ? (
              <RunDetail
                key={detail.data.run_id}
                run={detail.data}
                actionError={actionError}
                advancing={advance.isPending}
                terminating={terminate.isPending}
                onRefresh={refreshSelected}
                onAdvance={(transition, evidence) => advance.mutate({
                  runId: detail.data.run_id,
                  fingerprint: detail.data.snapshot_fingerprint,
                  transition,
                  evidence,
                })}
                onTerminate={(target, reason) => terminate.mutate({
                  runId: detail.data.run_id,
                  fingerprint: detail.data.snapshot_fingerprint,
                  target,
                  reason,
                })}
              />
            ) : (
              <div className="flex min-h-80 items-center justify-center border-y border-[var(--line)] bg-white px-6 text-center text-sm text-[var(--muted)]">
                {runs.data?.items.length ? "选择一条运行查看门禁详情" : "创建第一条运行后，可在这里逐门禁登记证据。"}
              </div>
            )}
          </main>
        </div>
      </div>
    </>
  )
}

function SafetyBoundary() {
  return (
    <section className="border-y border-[#e5c9a7] bg-[#fff9ef] px-5 py-5">
      <div className="flex items-start gap-4">
        <WarningCircle className="mt-0.5 shrink-0 text-[#8a4d0f]" size={24} weight="fill" />
        <div>
          <h2 className="font-editorial text-xl font-bold text-[#5f350b]">安全编排与证据登记层</h2>
          <p className="mt-2 max-w-[105ch] text-sm leading-6 text-[#6f4a24]">
            当前页面不会上传 XLSX、不会下载图片、不会调用模型、不会写入业务素材或评测库、不会形成 Gold，也不会发布。
            表单只把已由外部流程验证的证据登记到状态机；真实执行器将在后续阶段接入。
          </p>
        </div>
      </div>
    </section>
  )
}

function CreateRunForm({
  pending,
  error,
  onCreate,
}: {
  pending: boolean
  error: unknown
  onCreate: (payload: {
    domain: "3D"
    target_size: number
    seed: string
    display_name: string | null
  }) => void
}) {
  const [targetSize, setTargetSize] = useState(40)
  const [seed, setSeed] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [localError, setLocalError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    const normalizedSeed = seed.trim()
    if (!normalizedSeed) {
      setLocalError("请填写非空 seed。")
      return
    }
    if (!Number.isInteger(targetSize) || targetSize < 30 || targetSize > 50) {
      setLocalError("计划数量必须是 30～50 的整数。")
      return
    }
    setLocalError("")
    onCreate({
      domain: "3D",
      target_size: targetSize,
      seed: normalizedSeed,
      display_name: displayName.trim() || null,
    })
  }

  return (
    <section className="mt-7 border-y border-[var(--line-strong)] bg-white">
      <div className="border-b border-[var(--line)] px-5 py-4">
        <h2 className="font-editorial text-xl font-bold">创建运行计划</h2>
        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
          相同计划会产生相同 run_id；显示名称也必须保持一致，否则后端将拒绝幂等漂移。
        </p>
      </div>
      <form onSubmit={submit} className="grid gap-5 p-5 md:grid-cols-2 xl:grid-cols-[110px_150px_1fr_1fr_auto] xl:items-end">
        <Field label="领域">
          <Input value="3D" readOnly aria-readonly="true" className="font-data" />
        </Field>
        <Field label="计划数量">
          <Input
            type="number"
            min={30}
            max={50}
            step={1}
            value={targetSize}
            onChange={(event) => setTargetSize(Number(event.target.value))}
          />
        </Field>
        <Field label="Seed">
          <Input
            value={seed}
            maxLength={200}
            onChange={(event) => setSeed(event.target.value)}
            placeholder="必填，用于确定性复现"
          />
        </Field>
        <Field label="显示名称（可选）">
          <Input
            value={displayName}
            maxLength={160}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="例如：P0-E 首轮证据登记"
          />
        </Field>
        <Button type="submit" disabled={pending}>
          {pending ? <CircleNotch className="animate-spin" /> : <Plus />}
          创建运行
        </Button>
      </form>
      {Boolean(localError || error) && (
        <div className="border-t border-[#e8c1bd] bg-[#fff0ee] px-5 py-3 text-sm text-[#8d2924]">
          {localError || readableError(error)}
        </div>
      )}
    </section>
  )
}

function RunList({
  items,
  selectedId,
  loading,
  error,
  onSelect,
  onRetry,
}: {
  items: CanaryRun[]
  selectedId: string | null
  loading: boolean
  error: unknown
  onSelect: (runId: string) => void
  onRetry: () => void
}) {
  if (loading) {
    return (
      <div className="space-y-px border-y border-[var(--line-strong)] bg-[var(--line)]">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="h-28 animate-pulse bg-white" />
        ))}
      </div>
    )
  }

  if (error) {
    return <ErrorNotice error={error} onRefresh={onRetry} compact />
  }

  if (!items.length) {
    return (
      <div className="border-y border-[var(--line-strong)] bg-white px-5 py-14 text-center">
        <Circle size={27} className="mx-auto" />
        <p className="mt-3 text-sm font-semibold">还没有金丝雀运行</p>
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">先创建计划，不会触发任何真实执行。</p>
      </div>
    )
  }

  return (
    <div className="border-y border-[var(--line-strong)] bg-white">
      {items.map((run) => (
        <button
          key={run.run_id}
          type="button"
          onClick={() => onSelect(run.run_id)}
          className={`w-full border-b border-[var(--line)] px-4 py-4 text-left last:border-0 ${
            selectedId === run.run_id ? "bg-[#f5f8ed]" : "hover:bg-[#fafbf8]"
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <span className="min-w-0 truncate text-sm font-semibold">{run.display_name || "未命名运行"}</span>
            <Badge tone={stateTone(run.state)}>{stateLabels[run.state]}</Badge>
          </div>
          <p className="font-data mt-2 truncate text-[0.68rem] text-[var(--muted)]">{run.run_id}</p>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--muted)]">
            <span>{run.plan.domain} · {run.plan.target_size} 项</span>
            <time dateTime={run.updated_at}>{formatDateTime(run.updated_at)}</time>
          </div>
        </button>
      ))}
    </div>
  )
}

function RunDetail({
  run,
  actionError,
  advancing,
  terminating,
  onRefresh,
  onAdvance,
  onTerminate,
}: {
  run: CanaryRun
  actionError: unknown
  advancing: boolean
  terminating: boolean
  onRefresh: () => void
  onAdvance: (transition: TransitionState, evidence: Record<string, unknown>) => void
  onTerminate: (target: TerminalState, reason: string) => void
}) {
  const isTerminal = terminalStates.has(run.state)

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-data text-xs text-[var(--muted)]">CANARY RUN</p>
          <h2 className="font-editorial mt-2 break-words text-3xl font-bold">{run.display_name || "未命名运行"}</h2>
          <p className="font-data mt-2 break-all text-xs text-[var(--muted)]">{run.run_id}</p>
        </div>
        <Badge tone={stateTone(run.state)}>{stateLabels[run.state]}</Badge>
      </div>

      <dl className="mt-5 grid border-y border-[var(--line-strong)] bg-white sm:grid-cols-2 xl:grid-cols-4">
        <MetaItem label="创建人" value={run.created_by} />
        <MetaItem label="创建时间" value={formatDateTime(run.created_at)} />
        <MetaItem label="最近更新" value={formatDateTime(run.updated_at)} />
        <MetaItem label="运行计划" value={`${run.plan.domain} · ${run.plan.target_size} 项 · seed ${run.plan.seed}`} />
      </dl>

      <section className="mt-7">
        <div className="mb-3">
          <h3 className="font-editorial text-xl font-bold">状态时间线</h3>
          <p className="mt-1 text-xs text-[var(--muted)]">门禁只允许单调推进；失败、取消和人工审核就绪均为不可恢复终止态。</p>
        </div>
        <StateTimeline run={run} />
      </section>

      <InvariantPanel run={run} />

      {Boolean(actionError) && (
        <div className="mt-6">
          <ErrorNotice error={actionError} onRefresh={onRefresh} />
        </div>
      )}

      <section className="mt-7">
        <div className="mb-3">
          <h3 className="font-editorial text-xl font-bold">{isTerminal ? "运行已只读" : "唯一下一门禁"}</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
            {isTerminal
              ? "终止态不能恢复或继续推进；已登记证据仍可在下方核对。"
              : "这里只展示当前状态允许提交的一个前向门禁；原始状态、指纹和运行级安全不变量不可编辑。"}
          </p>
        </div>
        <div className="border-y border-[var(--line-strong)] bg-white">
          {isTerminal ? (
            <div className="flex min-h-32 items-center gap-3 px-5 py-8">
              {run.state === "human_review_ready"
                ? <CheckCircle size={25} className="text-[#2f6f48]" weight="fill" />
                : <XCircle size={25} className="text-[#b7362e]" weight="fill" />}
              <div>
                <p className="font-semibold">{stateLabels[run.state]}</p>
                <p className="mt-1 text-sm text-[var(--muted)]">本页面不会从终止态创建恢复入口。</p>
              </div>
            </div>
          ) : (
            <NextGateForm
              key={`${run.run_id}:${run.state}:${run.snapshot_fingerprint}`}
              run={run}
              pending={advancing}
              onAdvance={onAdvance}
            />
          )}
        </div>
      </section>

      {!isTerminal && (
        <TerminalControls
          key={run.snapshot_fingerprint}
          pending={terminating}
          onTerminate={onTerminate}
        />
      )}

      <EvidenceSummary run={run} />

      <details className="mt-6 border-y border-[var(--line)] bg-[#fafbf8] px-4 py-3">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-semibold">
          <CaretDown />
          快照指纹（并发写入依据）
        </summary>
        <code className="font-data mt-3 block break-all text-[0.68rem] leading-5 text-[var(--muted)]">
          {run.snapshot_fingerprint}
        </code>
      </details>
    </div>
  )
}

function StateTimeline({ run }: { run: CanaryRun }) {
  const currentGateIndex = gateStates.indexOf(run.state as GateState)

  return (
    <ol className="grid border-y border-[var(--line-strong)] bg-white md:grid-cols-3 xl:grid-cols-6">
      {gateStates.map((state, index) => {
        const reached = run.state === "failed" || run.state === "cancelled"
          ? state === "draft" || Boolean(evidenceKeyByState[state] && run.evidence[evidenceKeyByState[state] as string])
          : currentGateIndex >= index
        const current = run.state === state
        return (
          <li
            key={state}
            className={`min-h-28 border-b border-[var(--line)] px-4 py-4 md:border-r ${
              index >= 3 ? "md:border-b-0" : ""
            } xl:border-b-0 xl:last:border-r-0`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-data text-[0.65rem] text-[var(--muted)]">{String(index + 1).padStart(2, "0")}</span>
              {current
                ? <CircleNotch size={18} weight="bold" />
                : reached
                  ? <CheckCircle size={18} className="text-[#2f6f48]" weight="fill" />
                  : <Circle size={18} className="text-[#9aa097]" />}
            </div>
            <p className={`mt-4 text-xs font-semibold leading-5 ${current ? "text-foreground" : "text-[#5f655d]"}`}>
              {stateLabels[state]}
            </p>
            <p className="mt-1 text-[0.68rem] text-[var(--muted)]">
              {current ? "当前状态" : reached ? "证据已登记" : "尚未到达"}
            </p>
          </li>
        )
      })}
      {(run.state === "failed" || run.state === "cancelled") && (
        <li className="flex items-center gap-3 border-t border-[#e8c1bd] bg-[#fff0ee] px-4 py-3 text-sm text-[#8d2924] md:col-span-3 xl:col-span-6">
          <XCircle size={20} weight="fill" />
          运行终止：{stateLabels[run.state]}，后续门禁保持关闭。
        </li>
      )}
    </ol>
  )
}

function InvariantPanel({ run }: { run: CanaryRun }) {
  const invariants = [
    ["writes_business_database", "写业务素材/评测库", run.writes_business_database],
    ["downloads_performed", "执行下载", run.downloads_performed],
    ["model_runs_performed", "调用模型", run.model_runs_performed],
    ["forms_gold", "形成 Gold", run.forms_gold],
    ["publishes_release", "发布版本", run.publishes_release],
  ] as const
  const violated = invariants.filter(([, , value]) => value !== false)

  return (
    <section className="mt-7">
      <div className="mb-3">
        <h3 className="font-editorial text-xl font-bold">五项安全不变量</h3>
        <p className="mt-1 text-xs text-[var(--muted)]">API 响应中的每一项都必须明确等于 false，不接受缺失、推断或真值。</p>
      </div>
      {violated.length > 0 && (
        <div role="alert" className="mb-3 flex items-start gap-3 border-y border-[#c55b52] bg-[#fff0ee] px-4 py-4 text-[#7d201a]">
          <WarningCircle size={22} weight="fill" className="mt-0.5 shrink-0" />
          <div>
            <p className="font-bold">危险：服务端返回了非 false 的安全不变量</p>
            <p className="mt-1 text-sm">停止继续登记并核查服务端持久化：{violated.map(([key]) => key).join("、")}</p>
          </div>
        </div>
      )}
      <div className="grid border-y border-[var(--line-strong)] bg-white sm:grid-cols-2 xl:grid-cols-5">
        {invariants.map(([key, label, value]) => (
          <div key={key} className="border-b border-[var(--line)] px-4 py-4 sm:border-r xl:border-b-0 xl:last:border-r-0">
            <p className="text-xs text-[var(--muted)]">{label}</p>
            <div className="mt-3 flex items-center gap-2">
              <Badge tone={value === false ? "success" : "danger"}>{String(value)}</Badge>
              <code className="font-data break-all text-[0.62rem] text-[var(--muted)]">{key}</code>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function NextGateForm({
  run,
  pending,
  onAdvance,
}: {
  run: CanaryRun
  pending: boolean
  onAdvance: (transition: TransitionState, evidence: Record<string, unknown>) => void
}) {
  if (run.state === "draft") {
    return <PreflightForm pending={pending} onSubmit={(evidence) => onAdvance("preflight_ready", evidence)} />
  }
  if (run.state === "preflight_ready") {
    return <ApprovalForm run={run} pending={pending} onSubmit={(evidence) => onAdvance("approvals_ready", evidence)} />
  }
  if (run.state === "approvals_ready") {
    return <FreezeCapabilityForm pending={pending} onSubmit={(evidence) => onAdvance("freeze_ready", evidence)} />
  }
  if (run.state === "freeze_ready") {
    return <ManifestForm pending={pending} onSubmit={(evidence) => onAdvance("candidate_ready", evidence)} />
  }
  if (run.state === "candidate_ready") {
    return <CandidateHandoffForm run={run} pending={pending} onSubmit={(evidence) => onAdvance("human_review_ready", evidence)} />
  }
  return null
}

function PreflightForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [batchKey, setBatchKey] = useState("")
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    const normalized = batchKey.trim()
    if (!normalized.startsWith("p0e:")) {
      setError("batch_key 必须以 p0e: 开头。")
      return
    }
    setError("")
    onSubmit({ schema_version: PREFLIGHT_SCHEMA, batch_key: normalized })
  }

  return (
    <GateFormShell
      step="DRAFT → PREFLIGHT_READY"
      title="登记 XLSX 预检产物"
      warning="这是导入器产物的人工接线占位，不代表页面已经上传、读取或验证 XLSX。"
    >
      <form onSubmit={submit} className="grid gap-5 md:grid-cols-2 md:items-end">
        <Field label="schema_version（固定）">
          <Input value={PREFLIGHT_SCHEMA} readOnly className="font-data" />
        </Field>
        <Field label="batch_key">
          <Input value={batchKey} onChange={(event) => setBatchKey(event.target.value)} placeholder="p0e:…" />
        </Field>
        <SubmitRow pending={pending} error={error} label="登记预检证据" />
      </form>
    </GateFormShell>
  )
}

function ApprovalForm({
  run,
  pending,
  onSubmit,
}: {
  run: CanaryRun
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const preflight = asRecord(run.evidence.xlsx_preflight)
  const batchKey = typeof preflight?.batch_key === "string" ? preflight.batch_key : ""
  const [humanApproved, setHumanApproved] = useState(false)
  const [approvedBy, setApprovedBy] = useState("")
  const [mappings, setMappings] = useState("")
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!humanApproved) {
      setError("必须由人工显式勾选确认，不能静默默认通过。")
      return
    }
    const approver = approvedBy.trim()
    if (!approver) {
      setError("请填写 approved_by。")
      return
    }
    const parsed = parseMappings(mappings)
    if (parsed.error) {
      setError(parsed.error)
      return
    }
    setError("")
    onSubmit({
      human_approved: true,
      approved_by: approver,
      batch_key: batchKey,
      applied_mappings: parsed.items,
    })
  }

  return (
    <GateFormShell
      step="PREFLIGHT_READY → APPROVALS_READY"
      title="人工确认字段映射"
      warning="映射必须逐行由人工确认；human_approved 默认不勾选，页面不会推断或静默应用 farmat → format。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="batch_key（继承且只读）">
            <Input value={batchKey} readOnly className="font-data" />
          </Field>
          <Field label="approved_by">
            <Input value={approvedBy} onChange={(event) => setApprovedBy(event.target.value)} placeholder="填写人工审批人标识" />
          </Field>
        </div>
        <Field label="applied_mappings（每行一个）" hint="格式：源字段 => 目标字段；没有映射时保持为空。">
          <Textarea
            value={mappings}
            onChange={(event) => setMappings(event.target.value)}
            placeholder={"farmat => format\n原字段 => 目标字段"}
          />
        </Field>
        <ExplicitCheckbox
          checked={humanApproved}
          onChange={setHumanApproved}
          label="我已人工核对上述批次和每一条字段映射，并明确批准登记。"
        />
        <SubmitRow pending={pending} error={error} label="提交人工审批证据" />
      </form>
    </GateFormShell>
  )
}

function FreezeCapabilityForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [hosts, setHosts] = useState("")
  const [attested, setAttested] = useState(false)
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    const parsedHosts = Array.from(new Set(hosts.split(/\r?\n/).map((host) => host.trim()).filter(Boolean)))
    if (!parsedHosts.length) {
      setError("至少登记一个精确主机名。")
      return
    }
    const invalidHost = parsedHosts.find((host) => (
      host.includes("://") || /[/?#@:\s*\[\]]/.test(host)
    ))
    if (invalidHost) {
      setError(`主机名必须是无协议、路径、参数或凭据的精确主机：${invalidHost}`)
      return
    }
    if (!attested) {
      setError("必须显式确认已验证固定 IP HTTPS 能力。")
      return
    }
    setError("")
    onSubmit({ allowed_hosts: parsedHosts, pinned_https_attested: true })
  }

  return (
    <GateFormShell
      step="APPROVALS_READY → FREEZE_READY"
      title="登记受控冻结能力"
      warning="这里只登记已经由执行环境验证的能力，不会连接主机、执行 DNS 解析或下载任何图片。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <Field label="allowed_hosts" hint="每行一个精确主机名，不填写协议、路径、query、fragment、userinfo 或通配符。">
          <Textarea
            value={hosts}
            onChange={(event) => setHosts(event.target.value)}
            placeholder={"images.example.internal\nstatic.example.internal"}
          />
        </Field>
        <ExplicitCheckbox
          checked={attested}
          onChange={setAttested}
          label="我确认执行环境已经验证固定 IP HTTPS 传输能力（pinned_https_attested=true）。"
        />
        <SubmitRow pending={pending} error={error} label="登记冻结能力" />
      </form>
    </GateFormShell>
  )
}

function ManifestForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [expectedCount, setExpectedCount] = useState<number | "">("")
  const [frozenCount, setFrozenCount] = useState<number | "">("")
  const [complete, setComplete] = useState(false)
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!Number.isInteger(expectedCount) || !Number.isInteger(frozenCount)) {
      setError("请登记整数形式的来源数量与冻结数量。")
      return
    }
    if (Number(expectedCount) < 1 || Number(frozenCount) < 1) {
      setError("冻结清单至少需要一项来源资产。")
      return
    }
    if (expectedCount !== frozenCount) {
      setError("expected_source_count 必须与 frozen_source_count 完全一致。")
      return
    }
    if (!complete) {
      setError("必须根据执行器证据显式确认清单完整。")
      return
    }
    setError("")
    onSubmit({
      manifest_version: MANIFEST_VERSION,
      expected_source_count: expectedCount,
      frozen_source_count: frozenCount,
      complete: true,
      errors: [],
    })
  }

  return (
    <GateFormShell
      step="FREEZE_READY → CANDIDATE_READY"
      title="登记冻结清单证据"
      warning="当前必须根据执行器产物粘贴或登记证据；页面不会实际冻结文件，也不会替你推断清单完整。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          <Field label="manifest_version（固定）">
            <Input value={MANIFEST_VERSION} readOnly className="font-data" />
          </Field>
          <Field label="expected_source_count">
            <Input
              type="number"
              min={1}
              step={1}
              value={expectedCount}
              onChange={(event) => setExpectedCount(event.target.value === "" ? "" : Number(event.target.value))}
            />
          </Field>
          <Field label="frozen_source_count">
            <Input
              type="number"
              min={1}
              step={1}
              value={frozenCount}
              onChange={(event) => setFrozenCount(event.target.value === "" ? "" : Number(event.target.value))}
            />
          </Field>
          <Field label="errors（固定）">
            <Input value="[]" readOnly className="font-data" />
          </Field>
        </div>
        <ExplicitCheckbox
          checked={complete}
          onChange={setComplete}
          label="我已核对执行器证据，清单完整、数量一致且 errors 为空（complete=true）。"
        />
        <SubmitRow pending={pending} error={error} label="登记冻结清单" />
      </form>
    </GateFormShell>
  )
}

function CandidateHandoffForm({
  run,
  pending,
  onSubmit,
}: {
  run: CanaryRun
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [selectedCount, setSelectedCount] = useState(run.plan.target_size)
  const [complete, setComplete] = useState(false)
  const [allReview, setAllReview] = useState(false)
  const [noTruthOrGold, setNoTruthOrGold] = useState(false)
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!Number.isInteger(selectedCount) || selectedCount !== run.plan.target_size) {
      setError(`selected_count 必须与计划 target_size ${run.plan.target_size} 完全一致。`)
      return
    }
    if (!complete || !allReview || !noTruthOrGold) {
      setError("三个确认项都必须由人工显式勾选，不能默认通过。")
      return
    }
    setError("")
    onSubmit({
      candidate_preview: {
        schema_version: CANDIDATE_SCHEMA,
        selected_count: selectedCount,
        complete_for_requested_preview: true,
        forms_gold: false,
        downloads_performed: false,
        model_runs_performed: false,
      },
      human_review_handoff: {
        all_items_require_review: true,
        no_truth_or_gold_granted: true,
        item_count: selectedCount,
      },
    })
  }

  return (
    <GateFormShell
      step="CANDIDATE_READY → HUMAN_REVIEW_READY"
      title="登记候选预览与人工审核交接"
      warning="这一步只证明候选预览数量完整并转交逐项人工审核；不会赋予真值、Gold、下载或模型运行事实。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          <Field label="schema_version（固定）">
            <Input value={CANDIDATE_SCHEMA} readOnly className="font-data" />
          </Field>
          <Field label="selected_count">
            <Input
              type="number"
              min={30}
              max={50}
              step={1}
              value={selectedCount}
              onChange={(event) => setSelectedCount(Number(event.target.value))}
            />
          </Field>
          <Field label="item_count（继承）">
            <Input value={selectedCount} readOnly className="font-data" />
          </Field>
          <Field label="固定安全事实">
            <div className="min-h-11 border border-[var(--line-strong)] bg-[#f1f3ef] px-3 py-2 text-xs leading-5 text-[var(--muted)]">
              forms_gold=false · downloads_performed=false · model_runs_performed=false
            </div>
          </Field>
        </div>
        <div className="grid gap-3">
          <ExplicitCheckbox
            checked={complete}
            onChange={setComplete}
            label="候选预览已满足计划数量（complete_for_requested_preview=true）。"
          />
          <ExplicitCheckbox
            checked={allReview}
            onChange={setAllReview}
            label="全部候选项都必须进入人工审核（all_items_require_review=true）。"
          />
          <ExplicitCheckbox
            checked={noTruthOrGold}
            onChange={setNoTruthOrGold}
            label="当前不授予任何人工真值或 Gold 身份（no_truth_or_gold_granted=true）。"
          />
        </div>
        <SubmitRow pending={pending} error={error} label="完成人工审核交接" />
      </form>
    </GateFormShell>
  )
}

function GateFormShell({
  step,
  title,
  warning,
  children,
}: {
  step: string
  title: string
  warning: string
  children: ReactNode
}) {
  return (
    <div>
      <div className="border-b border-[var(--line)] bg-[#fafbf8] px-5 py-4">
        <p className="font-data text-[0.68rem] text-[var(--muted)]">{step}</p>
        <h4 className="font-editorial mt-2 text-xl font-bold">{title}</h4>
        <p className="mt-2 flex items-start gap-2 text-sm leading-6 text-[#6f4a24]">
          <WarningCircle className="mt-1 shrink-0" />
          {warning}
        </p>
      </div>
      <div className="p-5">{children}</div>
    </div>
  )
}

function SubmitRow({
  pending,
  error,
  label,
}: {
  pending: boolean
  error: string
  label: string
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 md:col-span-2">
      <p role={error ? "alert" : undefined} className="text-sm text-[#8d2924]">{error}</p>
      <Button type="submit" disabled={pending}>
        {pending ? <CircleNotch className="animate-spin" /> : <CheckCircle />}
        {label}
      </Button>
    </div>
  )
}

function ExplicitCheckbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 border border-[var(--line)] bg-[#fafbf8] px-4 py-3 text-sm leading-6">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 size-4 shrink-0 accent-[#95b314] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-black"
      />
      <span>{label}</span>
    </label>
  )
}

function TerminalControls({
  pending,
  onTerminate,
}: {
  pending: boolean
  onTerminate: (target: TerminalState, reason: string) => void
}) {
  const [target, setTarget] = useState<TerminalState | null>(null)
  const [reason, setReason] = useState("")
  const [error, setError] = useState("")

  function confirm() {
    const normalized = reason.trim()
    if (!target) return
    if (!normalized) {
      setError("终止操作必须填写原因。")
      return
    }
    setError("")
    onTerminate(target, normalized)
  }

  return (
    <section className="mt-7 border-y border-[var(--line)] bg-[#fafbf8]">
      <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
        <div>
          <h3 className="font-semibold">不可恢复的终止操作</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">选择操作后会展开二次确认区域；提交时仍携带当前快照指纹。</p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={() => { setTarget("failed"); setReason(""); setError("") }}>
            标记失败
          </Button>
          <Button type="button" variant="danger" size="sm" onClick={() => { setTarget("cancelled"); setReason(""); setError("") }}>
            取消运行
          </Button>
        </div>
      </div>
      {target && (
        <div className="border-t border-[#e8c1bd] bg-[#fff0ee] px-5 py-5">
          <p className="font-bold text-[#7d201a]">
            二次确认：将运行{target === "cancelled" ? "取消" : "标记为失败"}后不可恢复。
          </p>
          <Field label="原因">
            <Textarea
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明为什么终止；该原因会进入累积证据。"
              className="mt-3 bg-white"
            />
          </Field>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
            <p role={error ? "alert" : undefined} className="text-sm text-[#8d2924]">{error}</p>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" onClick={() => { setTarget(null); setReason(""); setError("") }} disabled={pending}>
                返回
              </Button>
              <Button type="button" variant="danger" onClick={confirm} disabled={pending}>
                {pending ? <CircleNotch className="animate-spin" /> : <XCircle />}
                确认终止
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function EvidenceSummary({ run }: { run: CanaryRun }) {
  const items = useMemo(() => summarizeEvidence(run.evidence), [run.evidence])

  return (
    <section className="mt-7">
      <div className="mb-3">
        <h3 className="font-editorial text-xl font-bold">累积证据摘要</h3>
        <p className="mt-1 text-xs text-[var(--muted)]">只以纯文本展示已登记摘要；任何证据 URL 都不会渲染为可点击链接。</p>
      </div>
      <div className="border-y border-[var(--line-strong)] bg-white">
        {items.length ? items.map((item) => (
          <div key={item.key} className="grid gap-2 border-b border-[var(--line)] px-4 py-4 last:border-0 md:grid-cols-[190px_1fr]">
            <div>
              <p className="text-sm font-semibold">{item.label}</p>
              <code className="font-data text-[0.65rem] text-[var(--muted)]">{item.key}</code>
            </div>
            <p className="break-words text-sm leading-6 text-[#535951]">{item.summary}</p>
          </div>
        )) : (
          <div className="px-5 py-10 text-center text-sm text-[var(--muted)]">草稿阶段尚未登记任何门禁证据。</div>
        )}
      </div>
    </section>
  )
}

function ErrorNotice({
  error,
  onRefresh,
  compact = false,
}: {
  error: unknown
  onRefresh: () => void
  compact?: boolean
}) {
  const apiError = error instanceof ApiError ? error : null
  const detail = apiError?.detail
  const stale = apiError?.status === 409
  const unauthorized = apiError?.status === 401

  return (
    <div role="alert" className={`border-y border-[#e8c1bd] bg-[#fff0ee] text-[#7d201a] ${compact ? "px-4 py-4" : "px-5 py-5"}`}>
      <div className="flex items-start gap-3">
        <WarningCircle size={21} weight="fill" className="mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="font-bold">
            {stale
              ? "快照已变化，请刷新后重试"
              : unauthorized
                ? "登录状态已失效"
                : "请求未完成"}
          </p>
          <p className="mt-1 break-words text-sm leading-6">
            {unauthorized ? "请刷新页面并重新登录后继续。" : detail?.message || readableError(error)}
          </p>
          {detail && (
            <dl className="font-data mt-3 grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2">
              {detail.code && <ErrorField label="code" value={detail.code} />}
              {detail.current_state && <ErrorField label="current_state" value={detail.current_state} />}
              {detail.attempted_transition && <ErrorField label="attempted_transition" value={detail.attempted_transition} />}
              {typeof detail.retryable === "boolean" && <ErrorField label="retryable" value={String(detail.retryable)} />}
            </dl>
          )}
          <Button type="button" variant="secondary" size="sm" className="mt-4" onClick={onRefresh}>
            <ArrowClockwise />
            {stale ? "刷新最新快照" : "重试刷新"}
          </Button>
        </div>
      </div>
    </div>
  )
}

function ErrorField({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[auto_1fr] gap-2">
      <dt className="text-[#8d5e58]">{label}</dt>
      <dd className="break-all font-semibold">{value}</dd>
    </div>
  )
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-[var(--line)] px-4 py-4 sm:border-r xl:border-b-0 xl:last:border-r-0">
      <dt className="text-xs text-[var(--muted)]">{label}</dt>
      <dd className="mt-2 break-words text-sm font-semibold leading-6">{value}</dd>
    </div>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      {children}
      {hint && <span className="mt-2 block text-xs leading-5 text-[var(--muted)]">{hint}</span>}
    </label>
  )
}

function parseMappings(value: string): {
  items: Array<{ source: string; target: string }>
  error?: string
} {
  const items: Array<{ source: string; target: string }> = []
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  for (const [index, line] of lines.entries()) {
    const parts = line.split("=>")
    if (parts.length !== 2 || !parts[0].trim() || !parts[1].trim()) {
      return { items: [], error: `第 ${index + 1} 行格式无效，请使用“源字段 => 目标字段”。` }
    }
    items.push({ source: parts[0].trim(), target: parts[1].trim() })
  }
  return { items }
}

function summarizeEvidence(evidence: Record<string, unknown>) {
  const summaries: Array<{ key: string; label: string; summary: string }> = []
  const preflight = asRecord(evidence.xlsx_preflight)
  if (preflight) {
    summaries.push({
      key: "xlsx_preflight",
      label: "XLSX 预检登记",
      summary: `schema ${textValue(preflight.schema_version)}；batch_key ${textValue(preflight.batch_key)}。`,
    })
  }
  const approval = asRecord(evidence.approval)
  if (approval) {
    const mappings = Array.isArray(approval.applied_mappings) ? approval.applied_mappings.length : 0
    summaries.push({
      key: "approval",
      label: "人工审批",
      summary: `审批人 ${textValue(approval.approved_by)}；批次 ${textValue(approval.batch_key)}；人工确认映射 ${mappings} 条。`,
    })
  }
  const fetchConfig = asRecord(evidence.fetch_config)
  if (fetchConfig) {
    const hosts = Array.isArray(fetchConfig.allowed_hosts) ? fetchConfig.allowed_hosts.map(textValue).join("、") : "—"
    summaries.push({
      key: "fetch_config",
      label: "冻结能力登记",
      summary: `精确主机 ${hosts || "—"}；固定 IP HTTPS 证明 ${textValue(fetchConfig.pinned_https_attested)}。未执行下载。`,
    })
  }
  const manifest = asRecord(evidence.manifest)
  if (manifest) {
    summaries.push({
      key: "manifest",
      label: "冻结清单",
      summary: `版本 ${textValue(manifest.manifest_version)}；来源 ${textValue(manifest.expected_source_count)}；冻结 ${textValue(manifest.frozen_source_count)}；完整 ${textValue(manifest.complete)}。`,
    })
  }
  const preview = asRecord(evidence.candidate_preview)
  if (preview) {
    summaries.push({
      key: "candidate_preview",
      label: "候选预览",
      summary: `版本 ${textValue(preview.schema_version)}；候选 ${textValue(preview.selected_count)}；完整 ${textValue(preview.complete_for_requested_preview)}；Gold/下载/模型运行均为 false。`,
    })
  }
  const handoff = asRecord(evidence.human_review_handoff)
  if (handoff) {
    summaries.push({
      key: "human_review_handoff",
      label: "人工审核交接",
      summary: `逐项必审 ${textValue(handoff.all_items_require_review)}；不授予真值或 Gold ${textValue(handoff.no_truth_or_gold_granted)}；共 ${textValue(handoff.item_count)} 项。`,
    })
  }
  const failure = asRecord(evidence.failure)
  if (failure) {
    summaries.push({ key: "failure", label: "失败原因", summary: textValue(failure.reason) })
  }
  const cancellation = asRecord(evidence.cancellation)
  if (cancellation) {
    summaries.push({ key: "cancellation", label: "取消原因", summary: textValue(cancellation.reason) })
  }
  return summaries
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function textValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return "—"
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误，请刷新后重试。"
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN")
}
