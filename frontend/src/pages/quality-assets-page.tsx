import { ArrowRight, DownloadSimple, Lock, ShieldCheck } from "@phosphor-icons/react"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, downloadApi, jsonBody } from "@/lib/api"
import type { SampleSetSummary } from "@/lib/types"

export function QualityAssetsPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const sets = useQuery({ queryKey: ["sample-sets", "quality-assets"], queryFn: () => api<{ items: SampleSetSummary[] }>("/api/sample-sets") })
  const golden = useMemo(() => (sets.data?.items ?? []).filter((item) => item.kind === "golden"), [sets.data?.items])
  const selected = golden.find((item) => item.id === selectedId) ?? golden[0]
  return (
    <>
      <PageHeader index="04" title="质量资产" description="黄金数据集、人工真值、锁定版本和导出集中管理。详细样本证据与历史修订下沉到二级抽屉。" actions={<Button asChild variant="secondary"><Link to="/legacy/sample-sets"><ShieldCheck />打开完整样本库</Link></Button>} />
      <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
        <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-3"><Metric label="黄金集" value={String(golden.length)} /><Metric label="已锁定" value={String(golden.filter((item) => item.status === "locked").length)} /><Metric label="真值完整" value={`${golden.reduce((sum, item) => sum + item.truth_complete_count, 0)}/${golden.reduce((sum, item) => sum + item.item_count, 0)}`} /></section>
        <section className="border-y border-[var(--line-strong)] bg-white"><div className="grid grid-cols-[minmax(0,1fr)_130px_150px_auto] gap-4 border-b border-[var(--line)] px-5 py-3 text-xs font-semibold text-[var(--muted)]"><span>数据集</span><span>状态</span><span>真值完整度</span><span>操作</span></div>{golden.length ? golden.map((item) => <div key={item.id} className="grid grid-cols-[minmax(0,1fr)_130px_150px_auto] items-center gap-4 border-b border-[var(--line)] px-5 py-4 last:border-0"><div><p className="text-sm font-bold">{item.name}</p><p className="mt-1 text-xs text-[var(--muted)]">{item.category_key} · {item.item_count} 条</p></div><Badge tone={item.status === "locked" ? "success" : "active"}>{item.status === "locked" ? <><Lock />已锁定</> : "草稿"}</Badge><span className="font-data text-xs text-[var(--muted)]">{item.truth_complete_count}/{item.item_count}</span><div className="flex gap-2"><Button size="sm" variant="secondary" onClick={() => setSelectedId(item.id)}>详情<ArrowRight /></Button><Button size="sm" variant="secondary" onClick={() => downloadApi(`/api/sample-sets/${item.id}/export`, `${item.name}.json`, { method: "POST", ...jsonBody({ format: "json" }) })}><DownloadSimple />导出</Button></div></div>) : <div className="px-5 py-12 text-center text-sm text-[var(--muted)]">暂无黄金数据集，先在完整样本库创建并锁定。</div>}</section>
      </div>
      <SecondaryDrawer open={selected != null && selectedId != null} onOpenChange={(open) => !open && setSelectedId(null)} title={selected?.name ?? "质量资产详情"} description="锁定版本用于回归；真值修订追加新 revision，不覆盖历史。"><div className="space-y-4 text-sm"><p>类目：{selected?.category_key}</p><p>状态：{selected?.status === "locked" ? "已锁定" : "草稿"}</p><p>真值完整度：{selected?.truth_complete_count}/{selected?.item_count}</p><Button asChild variant="secondary"><Link to={`/legacy/sample-sets?set=${selected?.id}`}>进入样本详情<ArrowRight /></Link></Button></div></SecondaryDrawer>
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-white px-5 py-5"><p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-2 font-data text-2xl font-bold">{value}</p></div> }
