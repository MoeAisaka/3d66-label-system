import { ArrowsClockwise, CheckCircle, Database, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { ProjectionContract, ProjectionReconciliation } from "@/lib/types"

export function ProjectionReconciliationDrawer({
  contract,
  open,
  onOpenChange,
}: {
  contract: ProjectionContract | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const reconcile = useMutation({
    mutationFn: () => api<ProjectionReconciliation>(`/api/projection-contracts/${contract?.id}/reconcile`, { method: "POST" }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["projection-contracts"] })
      toast.success(result.status === "matched" ? "本地投影已重建并完成对账" : "本地投影存在漂移，请查看补偿信息")
    },
    onError: (error) => toast.error(error.message),
  })
  const latest = contract?.latest_reconciliation
  return (
    <SecondaryDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={contract ? `${targetLabel(contract.target_role)} · v${contract.version}` : "投影对账"}
      description="仅操作 LabelLab 本地模拟表；不会写公司真实业务数据库，也不会修改 Canonical 正式标签事实。"
      footer={contract && <Button onClick={() => reconcile.mutate()} disabled={reconcile.isPending}><ArrowsClockwise />{reconcile.isPending ? "正在重建并对账" : "重建本地投影并对账"}</Button>}
    >
      {contract && <div className="space-y-6 text-sm">
        <section className="grid gap-3 sm:grid-cols-2"><Info label="目标表" value={contract.table_name} /><Info label="环境" value={contract.environment} /><Info label="模式" value={contract.mode} /><Info label="Owner" value={contract.owner} /></section>
        <section className="border-y border-[var(--line)] py-4"><div className="flex items-center gap-2"><Database /><h3 className="font-bold">字段映射</h3></div><div className="mt-3 divide-y divide-[var(--line)]">{Object.entries(contract.field_mappings).map(([target, source]) => <div key={target} className="grid gap-1 py-2 font-data text-xs sm:grid-cols-[1fr_1fr]"><span>{target}</span><span className="text-[var(--muted)]">← {source}</span></div>)}</div></section>
        <section><div className="flex items-center justify-between gap-3"><h3 className="font-bold">最近对账</h3>{latest ? <Badge tone={latest.status === "matched" ? "success" : "warning"}>{latest.status === "matched" ? <><CheckCircle />一致</> : <><WarningCircle />漂移</>}</Badge> : <Badge>尚未运行</Badge>}</div>{latest ? <div className="mt-3 grid gap-3 sm:grid-cols-2"><Info label="行数" value={String(latest.row_count)} /><Info label="缺失 / 多余" value={`${latest.missing_count} / ${latest.unexpected_count}`} /><Info label="版本一致" value={latest.version_match ? "是" : "否"} /><Info label="原因" value={latest.reason || "无"} /><Info label="实际哈希" value={latest.payload_hash.slice(0, 16)} /><Info label="预期哈希" value={latest.expected_payload_hash.slice(0, 16)} /></div> : <p className="mt-3 text-xs leading-5 text-[var(--muted)]">首次点击下方按钮会从正式 PublishedLabel 重建本地投影并生成行数、缺失、哈希和版本对账证据。</p>}</section>
        <section className="border border-[var(--line)] bg-[#f7f9ef] p-4 text-xs leading-5"><p className="font-semibold">事实主权不变</p><p className="mt-1 text-[var(--muted)]">投影失败只追加对账与补偿记录，不回写候选机制、人工过程或模型原始响应，也不改变任何正式标签历史。</p></section>
      </div>}
    </SecondaryDrawer>
  )
}

function targetLabel(role: ProjectionContract["target_role"]) {
  return { unified_dimension: "统一大维表", search_labels: "搜索标签小表", quality_governance: "质量治理小表" }[role]
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="border border-[var(--line)] bg-[#fafbf8] p-3"><p className="text-xs text-[var(--muted)]">{label}</p><p className="font-data mt-1 break-all text-xs font-semibold">{value}</p></div>
}
