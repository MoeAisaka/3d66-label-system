import {
  ArrowClockwise,
  CircleNotch,
  Pause,
  Play,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { Job, JobControl } from "@/lib/types"

const stageLabels: Record<string, string> = {
  single: "单提示词完整评测",
  waiting: "等待领取",
  precheck: "分类与画质预检",
  aesthetic: "美感维度评测",
  aesthetic_repair: "维度评分校准",
  risk_review: "高风险结果复核",
  scoring: "外部评分计算",
  paused: "已暂停",
  canceled: "已取消",
  done: "已完成",
  error: "执行失败",
}

const statusLabels: Record<string, string> = {
  queued: "排队中",
  processing: "评测中",
  paused: "已暂停",
  canceled: "已取消",
  completed: "已完成",
  failed: "失败",
}

function statusTone(status: string) {
  if (status === "completed") return "success" as const
  if (status === "failed" || status === "canceled") return "danger" as const
  if (status === "processing") return "active" as const
  return "neutral" as const
}

export function JobsPage() {
  const queryClient = useQueryClient()
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<{ items: Job[] }>("/api/jobs?limit=200"),
    refetchInterval: 2000,
  })
  const control = useQuery({
    queryKey: ["job-control"],
    queryFn: () => api<JobControl>("/api/jobs/control"),
    refetchInterval: 2000,
  })
  const action = useMutation({
    mutationFn: (name: "pause" | "resume" | "cancel") =>
      api<{ ok: boolean; affected: number; scope?: string }>(
        `/api/jobs/control/${name}`,
        { method: "POST" },
      ),
    onSuccess: async (data, name) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["job-control"] }),
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      const message = name === "pause" ? "已暂停" : name === "resume" ? "已恢复" : "已取消"
      toast.success(`${message} ${data.affected} 个未完成任务`)
    },
    onError: (error) => toast.error(error.message),
  })

  const activeCount = control.data?.active_count ?? 0
  const isPaused = control.data?.paused ?? false

  function cancelAll() {
    // 这个按钮没有 run 作用域：它会把当时所有排队中与进行中的任务判失败，
    // 跨 run、跨基准集一并击穿。要只停某一轮回归，请用回归详情页的「取消本轮」。
    if (
      window.confirm(
        `确定取消全部未完成任务吗？\n\n` +
          `这会取消当前所有正在进行的回归（共 ${activeCount} 个未完成任务），` +
          `不限于某一轮。已完成的评测结果会保留，取消后不可继续。\n\n` +
          `若只想停止某一轮回归，请到该轮回归的详情页使用「取消本轮」。`,
      )
    ) {
      action.mutate("cancel")
    }
  }

  return (
    <>
      <PageHeader
        index="01.3"
        title="评测进度"
        description="查看素材排队、评测和完成进度。每次任务都会固定使用开始时的类目方案；暂停可继续，取消只结束未完成任务。"
        actions={
          <>
            {isPaused ? (
              <Button onClick={() => action.mutate("resume")} disabled={!activeCount || action.isPending}>
                <Play weight="fill" />恢复全部
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => action.mutate("pause")} disabled={!activeCount || action.isPending}>
                <Pause weight="fill" />暂停全部
              </Button>
            )}
            <Button variant="danger" onClick={cancelAll} disabled={!activeCount || action.isPending}>
              <XCircle weight="bold" />取消全部
            </Button>
            <Button variant="secondary" onClick={() => Promise.all([jobs.refetch(), control.refetch()])}>
              <ArrowClockwise />刷新
            </Button>
          </>
        }
      />
      <div className="mx-auto shell-content px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <div className="mb-5 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
          <span className="border border-[var(--line)] bg-white px-3 py-2">排队 {control.data?.queued_count ?? 0}</span>
          <span className="border border-[var(--line)] bg-white px-3 py-2">运行 {control.data?.processing_count ?? 0}</span>
          <span className="border border-[var(--line)] bg-white px-3 py-2">暂停 {control.data?.paused_count ?? 0}</span>
        </div>
        <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white scrollbar-thin">
          {jobs.data?.items.length ? (
            <table className="w-full min-w-[1120px] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]">
                  <th className="px-4 py-3">任务</th>
                  <th className="px-3 py-3">图片</th>
                  <th className="px-3 py-3">运行依据</th>
                  <th className="px-3 py-3">阶段</th>
                  <th className="px-3 py-3">进度</th>
                  <th className="px-3 py-3">最新更新时间</th>
                  <th className="px-4 py-3">状态</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.items.map((job) => (
                  <tr key={job.id} className="border-b border-[var(--line)] last:border-0">
                    <td className="font-data px-4 py-4 text-xs">#{job.id.toString().padStart(5, "0")}</td>
                    <td className="file-name max-w-[280px] truncate px-3 py-4">{job.asset_name}</td>
                    <td className="px-3 py-4">
                      <details>
                        <summary className="cursor-pointer text-xs font-semibold">类目方案已冻结</summary>
                        <div className="mt-2 border-l border-[var(--line-strong)] pl-3">
                          {job.prompt_version ? <p className="font-data text-xs">单次完整评测 · {job.prompt_version}</p> : <><p className="font-data text-xs">阶段 A · {job.prompt_a_version ?? "历史任务未记录"}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">阶段 B · {job.prompt_b_version ?? "历史任务未记录"}</p></>}
                          <p className="font-data mt-1 text-xs text-[var(--muted)]">运行次数 {job.attempts}</p>
                        </div>
                      </details>
                    </td>
                    <td className="px-3 py-4">{stageLabels[job.stage] ?? job.stage}</td>
                    <td className="px-3 py-4">
                      <div className="flex items-center gap-3">
                        <div className="h-1.5 w-36 bg-[#eef1eb]"><div className="h-full bg-primary transition-[width] duration-200" style={{ width: `${job.progress}%` }} /></div>
                        <span className="font-data text-xs text-[var(--muted)]">{job.progress}%</span>
                      </div>
                    </td>
                    <td className="font-data whitespace-nowrap px-3 py-4 text-xs text-[var(--muted)]">{new Date(job.updated_at).toLocaleString("zh-CN")}</td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-2">
                        {job.status === "processing" && <CircleNotch className="animate-spin" />}
                        {job.status === "failed" && <WarningCircle className="text-[#b7362e]" />}
                        <Badge tone={statusTone(job.status)}>{statusLabels[job.status] ?? job.status}</Badge>
                      </div>
                      {job.error_message && <p className="mt-2 max-w-md text-xs leading-5 text-[#8d2924]">{jobFailureText(job.error_message)}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="flex min-h-72 flex-col items-center justify-center text-center">
              <CircleNotch size={30} weight="light" />
              <h2 className="font-editorial mt-4 text-xl font-bold">暂无评测任务</h2>
              <p className="mt-2 text-sm text-[var(--muted)]">前往评测包生产线，选择素材包和类目后开始评测。</p>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function jobFailureText(value: string) {
  const known = ({
    model_timeout: "模型响应超时，系统已停止本次处理",
    model_network: "模型服务网络异常",
    model_429: "模型服务当前繁忙",
    model_provider5xx: "模型服务暂时不可用",
    invalid_executor_output: "模型返回内容不完整，无法形成可信结果",
  } as Record<string, string>)[value]
  if (known) return known
  return /[\u3400-\u9fff]/u.test(value)
    ? value
    : "评测没有完成，请联系管理员查看运行记录"
}
