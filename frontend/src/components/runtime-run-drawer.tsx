import { ArrowClockwise, Pause, Play, Stop, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { runtimeApi } from "@/lib/runtime-api"
import type { ProductionRunSummary, RuntimeAction } from "@/lib/types"

const actionLabels: Record<RuntimeAction, string> = {
  pause: "暂停",
  resume: "恢复",
  retry: "重试",
  cancel: "取消",
}

const actionIcons: Record<RuntimeAction, typeof Pause> = {
  pause: Pause,
  resume: Play,
  retry: ArrowClockwise,
  cancel: Stop,
}

function statusTone(status: ProductionRunSummary["status"]) {
  if (status === "succeeded") return "success" as const
  if (status === "failed" || status === "blocked") return "danger" as const
  if (status === "retryable" || status === "paused") return "warning" as const
  return "neutral" as const
}

export function RuntimeRunDrawer({
  open,
  onOpenChange,
  run,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  run: ProductionRunSummary | null
}) {
  const queryClient = useQueryClient()
  const runKey = run?.run_key ?? ""
  const detail = useQuery({
    queryKey: ["runtime-run", runKey],
    queryFn: () => runtimeApi.getRun(runKey),
    enabled: open && Boolean(runKey),
    refetchInterval: open ? 2500 : false,
  })
  const timeline = useQuery({
    queryKey: ["runtime-timeline", runKey],
    queryFn: () => runtimeApi.getTimeline(runKey),
    enabled: open && Boolean(runKey),
    refetchInterval: open ? 2500 : false,
  })
  const snapshot = useQuery({
    queryKey: ["runtime-snapshot", runKey],
    queryFn: () => runtimeApi.getSnapshot(runKey),
    enabled: open && Boolean(runKey),
  })
  const action = useMutation({
    mutationFn: (name: RuntimeAction) => runtimeApi.action(runKey, name),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runtime-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["runtime-run", runKey] }),
        queryClient.invalidateQueries({ queryKey: ["runtime-timeline", runKey] }),
      ])
    },
  })
  const current = detail.data ?? run

  return (
    <SecondaryDrawer
      open={open}
      onOpenChange={onOpenChange}
      title="运行详情"
      description="查看冻结版本、步骤尝试、检查点和可执行恢复动作。"
      size="wide"
      footer={current && current.allowed_actions.length ? (
        <div className="flex flex-wrap justify-end gap-2">
          {current.allowed_actions.map((name) => {
            const Icon = actionIcons[name]
            return (
              <Button
                key={name}
                variant={name === "cancel" ? "secondary" : "primary"}
                disabled={action.isPending}
                onClick={() => action.mutate(name)}
              >
                <Icon size={17} />{actionLabels[name]}
              </Button>
            )
          })}
        </div>
      ) : undefined}
    >
      {!current ? (
        <p className="text-sm text-[var(--muted)]">未选择运行。</p>
      ) : (
        <div className="space-y-6">
          <section className="grid gap-px bg-[var(--line)] sm:grid-cols-2 lg:grid-cols-4">
            <Evidence label="状态"><Badge tone={statusTone(current.status)}>{current.status}</Badge></Evidence>
            <Evidence label="工作流版本">{current.workflow_version}</Evidence>
            <Evidence label="当前步骤">{current.current_step_key ?? "已结束"}</Evidence>
            <Evidence label="最后检查点">{current.last_checkpoint_id ? "#" + current.last_checkpoint_id : "暂无"}</Evidence>
          </section>

          {(current.error_message || current.blockers.length > 0) && (
            <section className="border-y border-[#d86f68] bg-[#fff8f7] px-4 py-4">
              <div className="flex items-center gap-2 text-sm font-bold"><WarningCircle />阻塞与错误</div>
              {current.error_message && <p className="mt-2 text-sm text-[#8d2924]">{current.error_code}: {current.error_message}</p>}
              {current.blockers.map((blocker, index) => (
                <p key={index} className="mt-2 text-xs text-[#8d2924]">
                  {typeof blocker === "string" ? blocker : blocker.message ?? blocker.code ?? "未知阻塞"}
                </p>
              ))}
            </section>
          )}

          <section>
            <h3 className="text-sm font-bold">步骤时间线</h3>
            <div className="mt-3 space-y-3">
              {(timeline.data?.items ?? []).map((item) => (
                <article key={item.id} className="border-y border-[var(--line)] px-4 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="font-data text-xs text-[var(--muted)]">#{item.sequence + 1} · attempt {item.attempt_no}</p>
                      <p className="mt-1 text-sm font-bold">{item.step_key} · {item.step_type}</p>
                    </div>
                    <Badge tone={item.status === "succeeded" ? "success" : item.status === "failed" ? "danger" : "neutral"}>{item.status}</Badge>
                  </div>
                  <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                    <Evidence label="脚本版本">{item.script_version}</Evidence>
                    <Evidence label="队列">{item.queue_class ?? "未入队"}</Evidence>
                    <Evidence label="输入哈希">{item.input_hash}</Evidence>
                    <Evidence label="输出哈希">{item.output_hash ?? "暂无"}</Evidence>
                    <Evidence label="checkpoint_hash">{item.checkpoint_hash ?? "暂无"}</Evidence>
                    <Evidence label="租约责任">{item.lease_owner ?? "无"}</Evidence>
                  </dl>
                  {item.last_error_message && <p className="mt-3 text-xs text-[#8d2924]">{item.last_error_code}: {item.last_error_message}</p>}
                </article>
              ))}
              {timeline.isLoading && <p className="text-sm text-[var(--muted)]">正在读取步骤证据…</p>}
              {!timeline.isLoading && !timeline.data?.items.length && <p className="text-sm text-[var(--muted)]">暂无步骤尝试。</p>}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-bold">冻结快照</h3>
            <p className="mt-2 font-data text-xs text-[var(--muted)]">SHA-256 {current.snapshot_hash}</p>
            <pre className="mt-3 max-h-80 overflow-auto border-y border-[var(--line)] bg-[#f7f8f3] p-4 font-data text-[11px] leading-5">
              {snapshot.data ? JSON.stringify(snapshot.data.snapshot, null, 2) : "正在读取冻结快照…"}
            </pre>
          </section>

          {action.isError && <p className="text-sm text-[#8d2924]">动作执行失败，请刷新后重试。</p>}
        </div>
      )}
    </SecondaryDrawer>
  )
}

function Evidence({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 bg-white px-3 py-3">
      <dt className="text-[11px] text-[var(--muted)]">{label}</dt>
      <dd className="mt-1 break-all font-data text-xs">{children}</dd>
    </div>
  )
}
