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
import { ErrorNotice, Field, TerminalState, formatDateTime, readableError, stateLabels, stateTone } from "@/features/canary/canary-form-primitives"
import { RunDetail, TransitionState } from "@/features/canary/canary-run-detail"

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

      <div className="mx-auto shell-content px-5 py-7 md:px-8 lg:px-10 lg:py-10">
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

