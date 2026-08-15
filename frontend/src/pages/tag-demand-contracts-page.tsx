import { ArrowsClockwise, Copy, Eye, Fingerprint, ShieldCheck } from "@phosphor-icons/react"
import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { TagDemandContractDrawer } from "@/components/tag-demand-contract-drawer"
import { ConfirmDialog, SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, sourceIdentityApi, tagDemandContractApi } from "@/lib/api"
import type { SourceIdentityVerification, TagDemandContract, User } from "@/lib/types"

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
  const [identityContractId, setIdentityContractId] = useState<number | null>(null)
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  const contracts = useQuery({ queryKey: ["tag-demand-contracts"], queryFn: tagDemandContractApi.list })
  const verifications = useQuery({ queryKey: ["source-identity-verifications"], queryFn: sourceIdentityApi.list })
  const selected = useMemo(() => contracts.data?.items.find((item) => item.id === selectedId) ?? null, [contracts.data?.items, selectedId])
  const activateTarget = contracts.data?.items.find((item) => item.id === activateId) ?? null
  const identityContract = contracts.data?.items.find((item) => item.id === identityContractId) ?? null
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
  const approveIdentity = useMutation({
    mutationFn: (id: number) => sourceIdentityApi.approve(id),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["source-identity-verifications"] }); toast.success("身份签认证据已批准") },
    onError: (error: Error) => toast.error(error.message),
  })
  const bindIdentity = useMutation({
    mutationFn: ({ contractId, verificationId }: { contractId: number; verificationId: number }) => sourceIdentityApi.bindContract(contractId, verificationId),
    onSuccess: async (contract) => { await queryClient.invalidateQueries({ queryKey: ["tag-demand-contracts"] }); setIdentityContractId(null); setSelectedId(contract.id); toast.success(`已追加候选合同 v${contract.version}`) },
    onError: (error: Error) => toast.error(error.message),
  })
  const items = contracts.data?.items ?? []
  const activeCount = items.filter((item) => item.status === "active").length
  const candidateCount = items.filter((item) => item.status === "candidate").length
  const categoryCount = new Set(items.flatMap((item) => Object.keys(item.definition.category_applicability))).size
  const canManage = Boolean(me.data?.is_admin || me.data?.role === "admin")

  return <>
    <PageHeader index="A.7" title="字段需求合同" description="平台通用语义字段的版本、适用类目和质量门槛。一级页只做版本管理，详细字段矩阵进入二级抽屉。" actions={<Button variant="secondary" onClick={() => contracts.refetch()} disabled={contracts.isFetching}><ArrowsClockwise />刷新</Button>} />
    <div className="mx-auto max-w-[1540px] space-y-6 px-5 py-8 md:px-8 lg:px-10">
      <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-4"><Metric label="合同版本" value={String(items.length)} /><Metric label="现役版本" value={String(activeCount)} /><Metric label="候选版本" value={String(candidateCount)} /><Metric label="覆盖类目" value={String(categoryCount)} /></section>
      {contracts.isError && <div className="border-y border-[#d7a64d] bg-[#fff9e9] px-5 py-4 text-sm text-[#6f5513]">字段需求合同暂时无法读取，请稍后刷新。</div>}
      <section className="border-y border-[var(--line-strong)] bg-white">
        <div className="grid grid-cols-[minmax(0,1.3fr)_90px_100px_150px_170px_190px_auto] gap-4 border-b border-[var(--line)] px-5 py-3 text-xs font-semibold text-[var(--muted)]"><span>合同版本</span><span>版本</span><span>状态</span><span>适用类目</span><span>执行变体</span><span>质量门槛</span><span>操作</span></div>
        {items.map((item) => <ContractRow key={item.id} contract={item} onView={() => setSelectedId(item.id)} onIdentity={() => setIdentityContractId(item.id)} onClone={() => clone.mutate(item)} onActivate={() => setActivateId(item.id)} cloning={clone.isPending} canManage={canManage} />)}
        {!items.length && !contracts.isFetching && <div className="px-5 py-12 text-center text-sm text-[var(--muted)]">暂无字段需求合同版本。</div>}
      </section>
      <section className="flex items-start gap-3 border border-[var(--line)] bg-[#f7f9ef] p-4 text-xs leading-6"><ShieldCheck className="mt-0.5 shrink-0" /><p>合同激活只切换字段需求事实，不会启动评测、发布标签、存量回归或下游投影。知识图谱、搜索和数据库表继续只消费正式发布事实。</p></section>
    </div>
    <TagDemandContractDrawer contract={selected} open={selected != null} onOpenChange={(open) => !open && setSelectedId(null)} />
    <IdentityVerificationDrawer contract={identityContract} verifications={verifications.data?.items ?? []} canManage={canManage} open={identityContract != null} onOpenChange={(open) => !open && setIdentityContractId(null)} onApprove={(id) => approveIdentity.mutate(id)} onBind={(verificationId) => identityContract && bindIdentity.mutate({ contractId: identityContract.id, verificationId })} busy={approveIdentity.isPending || bindIdentity.isPending} />
    <ConfirmDialog open={canManage && activateTarget != null} onOpenChange={(open) => !open && setActivateId(null)} title="激活字段需求合同" description={activateTarget ? `确认将 ${activateTarget.contract_key} v${activateTarget.version} 设为现役？上一现役版本会退役，且不会自动发布标签事实。` : undefined} confirmLabel="确认激活" onConfirm={() => activateTarget && activate.mutate(activateTarget.id)} />
  </>
}

function ContractRow({ contract, onView, onIdentity, onClone, onActivate, cloning, canManage }: { contract: TagDemandContract; onView: () => void; onIdentity: () => void; onClone: () => void; onActivate: () => void; cloning: boolean; canManage: boolean }) {
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
    <div className="flex flex-wrap justify-end gap-2"><Button size="sm" variant="secondary" onClick={onView}><Eye />查看</Button>{contract.definition.source_identity && <Button size="sm" variant="secondary" onClick={onIdentity}><Fingerprint />身份签认</Button>}{canManage && contract.status !== "active" && <Button size="sm" variant="secondary" onClick={onClone} disabled={cloning}><Copy />复制候选</Button>}{canManage && contract.status === "candidate" && <Button size="sm" onClick={onActivate}>激活</Button>}{!canManage && <span className="self-center text-[11px] text-[var(--muted)]">管理员可配置</span>}</div>
  </div>
}

function IdentityVerificationDrawer({ contract, verifications, canManage, open, onOpenChange, onApprove, onBind, busy }: { contract: TagDemandContract | null; verifications: SourceIdentityVerification[]; canManage: boolean; open: boolean; onOpenChange: (open: boolean) => void; onApprove: (id: number) => void; onBind: (id: number) => void; busy: boolean }) {
  const sourceSystem = contract?.definition.source_identity?.source_system
  const rows = verifications.filter((item) => item.contract_key === contract?.contract_key && item.source_system === sourceSystem)
  return <SecondaryDrawer open={open} onOpenChange={onOpenChange} size="wide" title={contract ? `身份签认 · ${contract.contract_key} v${contract.version}` : "身份签认"} description="这里只审核已由只读探查产生的摘要证据；不会执行 SQL、激活合同、启动模型或发布标签事实。">
    {!contract ? <p className="text-sm text-[var(--muted)]">请选择合同。</p> : <div className="space-y-5">
      <section className="grid gap-3 border-y border-[var(--line)] py-4 text-xs sm:grid-cols-3"><Metric label="来源" value={sourceSystem ?? "未声明"} /><Metric label="候选键" value={contract.definition.source_identity?.identity_fields.join(" + ") ?? "未声明"} /><Metric label="合同唯一性" value={contract.definition.source_identity?.uniqueness_status ?? "v1"} /></section>
      {!canManage && <div className="border border-[var(--line)] bg-[#f7f9ef] p-4 text-xs leading-6">当前账号只读；管理员可签认并绑定候选合同。</div>}
      <div className="space-y-3">{rows.map((item) => {
        const approvable = item.status === "draft" && item.result === "verified" && item.duplicate_key_count === 0 && item.res_id_conflict_count === 0
        return <article key={item.id} className="border-y border-[var(--line)] px-3 py-4 text-xs">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><strong>证据 #{item.id}</strong><Badge tone={item.status === "approved" ? "success" : item.result === "conflict" ? "danger" : "neutral"}>{item.status}</Badge></div><p className="mt-2 font-data break-all text-[11px] text-[var(--muted)]">{item.probe_hash}</p></div><div className="flex flex-wrap gap-2">{canManage && approvable && <Button size="sm" variant="secondary" onClick={() => onApprove(item.id)} disabled={busy}>批准签认</Button>}{canManage && item.status === "approved" && contract.status !== "active" && <Button size="sm" onClick={() => onBind(item.id)} disabled={busy}>绑定到候选合同</Button>}</div></div>
          <div className="mt-4 grid gap-3 sm:grid-cols-3"><span>数据窗口：{item.data_window}</span><span>范围行数：{item.scoped_row_count}</span><span>结果：{item.result}</span><span>重复键：{item.duplicate_key_count}</span><span>res_id 冲突：{item.res_id_conflict_count}</span><span>审核人：{item.approved_by ?? item.created_by}</span></div>
        </article>
      })}{!rows.length && <div className="border-y border-[var(--line)] px-3 py-8 text-center text-sm text-[var(--muted)]">暂无与该合同来源匹配的身份探查证据。</div>}</div>
    </div>}
  </SecondaryDrawer>
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-white p-5"><p className="text-xs text-[var(--muted)]">{label}</p><p className="font-data mt-2 text-2xl font-bold">{value}</p></div> }
