import { ArrowsClockwise, Copy, Eye, ShieldCheck } from "@phosphor-icons/react"
import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { TagDemandContractDrawer } from "@/components/tag-demand-contract-drawer"
import { ConfirmDialog } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { tagDemandContractApi } from "@/lib/api"
import type { TagDemandContract } from "@/lib/types"

const statusLabels: Record<TagDemandContract["status"], string> = {
  draft: "草稿",
  candidate: "候选",
  active: "现役",
  retired: "已退役",
}

export function TagDemandContractsPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [activateId, setActivateId] = useState<number | null>(null)
  const contracts = useQuery({ queryKey: ["tag-demand-contracts"], queryFn: tagDemandContractApi.list })
  const selected = useMemo(() => contracts.data?.items.find((item) => item.id === selectedId) ?? null, [contracts.data?.items, selectedId])
  const activateTarget = contracts.data?.items.find((item) => item.id === activateId) ?? null
  const clone = useMutation({
    mutationFn: (contract: TagDemandContract) => tagDemandContractApi.create({ contract_key: contract.contract_key, definition: contract.definition, status: "candidate" }),
    onSuccess: async (contract) => { await queryClient.invalidateQueries({ queryKey: ["tag-demand-contracts"] }); setSelectedId(contract.id); toast.success(`已创建候选版本 v${contract.version}`) },
    onError: (error: Error) => toast.error(error.message),
  })
  const activate = useMutation({
    mutationFn: (id: number) => tagDemandContractApi.activate(id),
    onSuccess: async (contract) => { await queryClient.invalidateQueries({ queryKey: ["tag-demand-contracts"] }); setActivateId(null); toast.success(`已激活 ${contract.contract_key} v${contract.version}`) },
    onError: (error: Error) => toast.error(error.message),
  })
  const items = contracts.data?.items ?? []
  const activeCount = items.filter((item) => item.status === "active").length
  const candidateCount = items.filter((item) => item.status === "candidate").length
  const categoryCount = new Set(items.flatMap((item) => Object.keys(item.definition.category_applicability))).size

  return <>
    <PageHeader index="A.7" title="字段需求合同" description="平台通用语义字段的版本、适用类目和质量门槛。一级页只做版本管理，详细字段矩阵进入二级抽屉。" actions={<Button variant="secondary" onClick={() => contracts.refetch()} disabled={contracts.isFetching}><ArrowsClockwise />刷新</Button>} />
    <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-8 md:px-8 lg:px-10">
      <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-4"><Metric label="合同版本" value={String(items.length)} /><Metric label="现役版本" value={String(activeCount)} /><Metric label="候选版本" value={String(candidateCount)} /><Metric label="覆盖类目" value={String(categoryCount)} /></section>
      {contracts.isError && <div className="border-y border-[#d7a64d] bg-[#fff9e9] px-5 py-4 text-sm text-[#6f5513]">字段需求合同暂时无法读取，请稍后刷新。</div>}
      <section className="border-y border-[var(--line-strong)] bg-white">
        <div className="grid grid-cols-[minmax(0,1.3fr)_90px_100px_150px_170px_190px_auto] gap-4 border-b border-[var(--line)] px-5 py-3 text-xs font-semibold text-[var(--muted)]"><span>合同版本</span><span>版本</span><span>状态</span><span>适用类目</span><span>执行变体</span><span>质量门槛</span><span>操作</span></div>
        {items.map((item) => <ContractRow key={item.id} contract={item} onView={() => setSelectedId(item.id)} onClone={() => clone.mutate(item)} onActivate={() => setActivateId(item.id)} cloning={clone.isPending} />)}
        {!items.length && !contracts.isFetching && <div className="px-5 py-12 text-center text-sm text-[var(--muted)]">暂无字段需求合同版本。</div>}
      </section>
      <section className="flex items-start gap-3 border border-[var(--line)] bg-[#f7f9ef] p-4 text-xs leading-6"><ShieldCheck className="mt-0.5 shrink-0" /><p>合同激活只切换字段需求事实，不会启动评测、发布标签、存量回归或下游投影。知识图谱、搜索和数据库表继续只消费正式发布事实。</p></section>
    </div>
    <TagDemandContractDrawer contract={selected} open={selected != null} onOpenChange={(open) => !open && setSelectedId(null)} />
    <ConfirmDialog open={activateTarget != null} onOpenChange={(open) => !open && setActivateId(null)} title="激活字段需求合同" description={activateTarget ? `确认将 ${activateTarget.contract_key} v${activateTarget.version} 设为现役？上一现役版本会退役，且不会自动发布标签事实。` : undefined} confirmLabel="确认激活" onConfirm={() => activateTarget && activate.mutate(activateTarget.id)} />
  </>
}

function ContractRow({ contract, onView, onClone, onActivate, cloning }: { contract: TagDemandContract; onView: () => void; onClone: () => void; onActivate: () => void; cloning: boolean }) {
  const categories = Object.keys(contract.definition.category_applicability)
  const variants = contract.definition.execution_variants.length
  const gates = Object.keys(contract.definition.quality_gates).length
  return <div className="grid grid-cols-[minmax(0,1.3fr)_90px_100px_150px_170px_190px_auto] items-center gap-4 border-b border-[var(--line)] px-5 py-4 last:border-0">
    <div><div className="flex items-center gap-2"><span className="font-bold">{contract.contract_key}</span>{contract.status === "active" && <Badge tone="success">现役</Badge>}</div><p className="mt-1 font-data text-[11px] text-[var(--muted)]">{contract.contract_hash.slice(0, 12)}… · {contract.created_by}</p></div>
    <span className="font-data text-sm">v{contract.version}</span>
    <Badge tone={contract.status === "active" ? "success" : contract.status === "candidate" ? "warning" : "neutral"}>{statusLabels[contract.status]}</Badge>
    <span className="text-xs text-[var(--muted)]">{categories.length} 个类目</span>
    <span className="text-xs text-[var(--muted)]">{variants} 个变体</span>
    <span className="text-xs text-[var(--muted)]">{gates} 个字段门槛</span>
    <div className="flex flex-wrap justify-end gap-2"><Button size="sm" variant="secondary" onClick={onView}><Eye />查看</Button>{contract.status !== "active" && <Button size="sm" variant="secondary" onClick={onClone} disabled={cloning}><Copy />复制候选</Button>}{contract.status === "candidate" && <Button size="sm" onClick={onActivate}>激活</Button>}</div>
  </div>
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-white p-5"><p className="text-xs text-[var(--muted)]">{label}</p><p className="font-data mt-2 text-2xl font-bold">{value}</p></div> }
