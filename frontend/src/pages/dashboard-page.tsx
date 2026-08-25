import { ArrowRight, CheckCircle, CircleNotch, Images, WarningCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { Asset, Dashboard } from "@/lib/types"

const levelOrder = ["L5", "L4", "L3", "L2", "L1"]

export function DashboardPage() {
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/api/dashboard"),
    refetchInterval: 4000,
  })
  const assets = useQuery({
    queryKey: ["assets", "recent"],
    queryFn: () => api<{ items: Asset[] }>("/api/assets?limit=6"),
  })
  const data = dashboard.data
  const totalEvaluated = Object.values(data?.levels ?? {}).reduce((sum, value) => sum + value, 0)

  return (
    <>
      <PageHeader
        index="01"
        title="评测总览"
        description="查看素材处理进度、模型状态和需要人工介入的图片。"
        actions={
          <Button asChild>
            <Link to="/assets">上传图片<ArrowRight weight="bold" /></Link>
          </Button>
        }
      />
      <div className="mx-auto shell-content px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <section className="grid border-y border-[var(--line-strong)] sm:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "素材总数", value: data?.asset_count ?? 0, icon: Images, note: "当前电脑" },
            { label: "等待评测", value: data?.queued ?? 0, icon: CircleNotch, note: "任务队列" },
            { label: "正在处理", value: data?.processing ?? 0, icon: CircleNotch, note: "模型调用" },
            { label: "需要复核", value: data?.needs_review ?? 0, icon: WarningCircle, note: "人工介入" },
          ].map((metric, index) => {
            const Icon = metric.icon
            return (
              <div key={metric.label} className={`min-h-36 p-5 ${index < 3 ? "xl:border-r" : ""} ${index % 2 === 0 ? "sm:border-r xl:border-r" : ""} border-[var(--line)]`}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{metric.label}</span>
                  <Icon size={20} className={metric.label === "正在处理" && metric.value > 0 ? "status-pulse" : ""} />
                </div>
                <div className="font-data mt-6 text-4xl font-semibold leading-none">{metric.value}</div>
                <p className="mt-3 text-xs text-[var(--muted)]">{metric.note}</p>
              </div>
            )
          })}
        </section>

        <div className="mt-10 grid gap-10 xl:grid-cols-[1.3fr_.7fr]">
          <section>
            <div className="mb-4 flex items-end justify-between gap-4">
              <div>
                <h2 className="font-editorial text-2xl font-bold">最近素材</h2>
                <p className="mt-1 text-sm text-[var(--muted)]">最新上传的原始图片</p>
              </div>
              <Button asChild variant="ghost" size="sm"><Link to="/assets">查看全部<ArrowRight /></Link></Button>
            </div>
            <div className="overflow-hidden border-y border-[var(--line-strong)] bg-white">
              {assets.isLoading ? (
                <div className="space-y-px bg-[var(--line)]">
                  {Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-[74px] animate-pulse bg-white" />)}
                </div>
              ) : assets.data?.items.length ? (
                <div className="divide-y divide-[var(--line)]">
                  {assets.data.items.map((asset) => (
                    <div key={asset.id} className="grid grid-cols-[56px_1fr_auto] items-center gap-4 px-3 py-3">
                      <img src={asset.image_url} alt="" className="size-14 rounded-[4px] border border-[var(--line)] object-cover" />
                      <div className="min-w-0">
                        <p className="file-name truncate text-sm">{asset.name}</p>
                        <p className="font-data mt-1 text-xs text-[var(--muted)]">{asset.width} × {asset.height}</p>
                      </div>
                      <time className="font-data text-right text-xs text-[var(--muted)]" dateTime={asset.created_at}>{new Date(asset.created_at).toLocaleDateString("zh-CN")}</time>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex min-h-56 flex-col items-center justify-center px-6 text-center">
                  <Images size={28} />
                  <h3 className="mt-4 font-semibold">还没有素材</h3>
                  <p className="mt-2 max-w-sm text-sm leading-6 text-[var(--muted)]">上传第一批空间或建筑图片，系统会建立可追溯的评测任务。</p>
                </div>
              )}
            </div>
          </section>

          <section>
            <div className="mb-4">
              <h2 className="font-editorial text-2xl font-bold">等级分布</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">只用于监控，不强制固定比例</p>
            </div>
            <div className="border-y border-[var(--line-strong)] bg-white px-5 py-2">
              {levelOrder.map((level) => {
                const count = data?.levels[level] ?? 0
                const width = totalEvaluated ? Math.max(3, (count / totalEvaluated) * 100) : 0
                return (
                  <div key={level} className="grid grid-cols-[36px_1fr_32px] items-center gap-3 border-b border-[var(--line)] py-4 last:border-0">
                    <span className="font-data text-sm font-semibold">{level}</span>
                    <div className="h-2 bg-[#eef1eb]"><div className="h-full bg-primary" style={{ width: `${width}%` }} /></div>
                    <span className="font-data text-right text-xs text-[var(--muted)]">{count}</span>
                  </div>
                )
              })}
            </div>
            <div className="mt-7 border-y border-[var(--line-strong)] bg-white py-5">
              <div className="flex items-start gap-4 px-5">
                {data?.model.has_api_key ? <CheckCircle size={22} className="text-[#2f6f48]" weight="fill" /> : <WarningCircle size={22} className="text-[#a85a0a]" />}
                <div className="min-w-0">
                  <p className="font-semibold">{data?.model.name ?? "模型状态"}</p>
                  <p className="mt-1 break-all text-xs leading-5 text-[var(--muted)]">{data?.model.model_id || "尚未配置模型端点"}</p>
                  <Badge className="mt-3" tone={data?.model.has_api_key ? "success" : "warning"}>{data?.model.has_api_key ? "已配置密钥" : "需要配置"}</Badge>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </>
  )
}
