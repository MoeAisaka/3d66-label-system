import { ArrowClockwise, CircleNotch, WarningCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { Job } from "@/lib/types"

const stageLabels: Record<string, string> = {
  waiting: "等待领取",
  precheck: "分类与画质预检",
  aesthetic: "美感维度评测",
  scoring: "外部评分计算",
  done: "已完成",
  error: "执行失败",
}

export function JobsPage() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<{ items: Job[] }>("/api/jobs?limit=200"),
    refetchInterval: 2000,
  })

  return (
    <>
      <PageHeader
        index="03"
        title="评测任务"
        description="任务保存在数据库中，关闭网页不会中断；服务重启后等待中的任务仍可继续处理。"
        actions={<Button variant="secondary" onClick={() => jobs.refetch()}><ArrowClockwise />刷新</Button>}
      />
      <div className="mx-auto max-w-[1540px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <div className="overflow-x-auto border-y border-[var(--line-strong)] bg-white scrollbar-thin">
          {jobs.data?.items.length ? (
            <table className="w-full min-w-[980px] border-collapse text-left text-sm">
              <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]"><th className="px-4 py-3">任务</th><th className="px-3 py-3">图片</th><th className="px-3 py-3">阶段</th><th className="px-3 py-3">进度</th><th className="px-3 py-3">尝试</th><th className="px-4 py-3">状态</th></tr></thead>
              <tbody>
                {jobs.data.items.map((job) => (
                  <tr key={job.id} className="border-b border-[var(--line)] last:border-0">
                    <td className="font-data px-4 py-4 text-xs">#{job.id.toString().padStart(5, "0")}</td>
                    <td className="max-w-[320px] truncate px-3 py-4 font-semibold">{job.asset_name}</td>
                    <td className="px-3 py-4">{stageLabels[job.stage] ?? job.stage}</td>
                    <td className="px-3 py-4"><div className="flex items-center gap-3"><div className="h-1.5 w-36 bg-[#eef1eb]"><div className="h-full bg-primary transition-[width] duration-200" style={{ width: `${job.progress}%` }} /></div><span className="font-data text-xs text-[var(--muted)]">{job.progress}%</span></div></td>
                    <td className="font-data px-3 py-4 text-xs text-[var(--muted)]">{job.attempts}</td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-2">
                        {job.status === "processing" && <CircleNotch className="animate-spin" />}
                        {job.status === "failed" && <WarningCircle className="text-[#b7362e]" />}
                        <Badge tone={job.status === "completed" ? "success" : job.status === "failed" ? "danger" : job.status === "processing" ? "active" : "neutral"}>{job.status}</Badge>
                      </div>
                      {job.error_message && <p className="mt-2 max-w-md text-xs leading-5 text-[#8d2924]">{job.error_message}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="flex min-h-72 flex-col items-center justify-center text-center"><CircleNotch size={30} weight="light" /><h2 className="font-editorial mt-4 text-xl font-bold">暂无评测任务</h2><p className="mt-2 text-sm text-[var(--muted)]">在素材页选择图片并开始评测。</p></div>
          )}
        </div>
      </div>
    </>
  )
}
