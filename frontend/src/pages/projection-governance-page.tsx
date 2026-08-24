import { ArrowsClockwise, Database, Plus } from "@phosphor-icons/react"
import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { ProjectionReconciliationDrawer } from "@/components/projection-reconciliation-drawer"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, jsonBody } from "@/lib/api"
import type { ProjectionContract } from "@/lib/types"

const builtins = [
  { key: "unified-dimension", role: "unified_dimension", table: "unified_dimension_table", label: "统一大维表", note: "承载大多数下游当前正式字段读取。" },
  { key: "search-labels", role: "search_labels", table: "search_labels_small_table", label: "搜索标签小表", note: "只投影搜索消费所需的正式语义字段。" },
  { key: "quality-governance", role: "quality_governance", table: "quality_governance_small_table", label: "质量治理小表", note: "承载质量、版本和来源治理字段。" },
] as const

const mappings: Record<(typeof builtins)[number]["role"], Record<string, string>> = {
  unified_dimension: { content_key: "content_key", category_key: "category_key", label_version: "$label.version", level: "level", score: "score", classification: "classification", dimensions: "dimensions", production_fields: "production_fields", image_quality: "image_quality", media_form: "media_form", asset_version: "provenance.asset_sha256", mechanism_version: "provenance.strategy_bundle_id", model_version: "provenance.model_id" },
  search_labels: { content_key: "content_key", category_key: "category_key", label_version: "$label.version", level: "level", classification: "classification", tags: "production_fields.tags", title: "production_fields.title" },
  quality_governance: { content_key: "content_key", category_key: "category_key", label_version: "$label.version", image_quality: "image_quality", asset_version: "provenance.asset_sha256", mechanism_version: "provenance.strategy_bundle_id", model_version: "provenance.model_id", rubric_version: "provenance.rubric_version" },
}

export function ProjectionGovernancePage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const contracts = useQuery({ queryKey: ["projection-contracts"], queryFn: () => api<{ items: ProjectionContract[] }>("/api/projection-contracts") })
  const latestByRole = useMemo(() => {
    const map = new Map<ProjectionContract["target_role"], ProjectionContract>()
    for (const item of contracts.data?.items ?? []) if (!map.has(item.target_role)) map.set(item.target_role, item)
    return map
  }, [contracts.data?.items])
  const selected = (contracts.data?.items ?? []).find((item) => item.id === selectedId) ?? null
  const register = useMutation({
    mutationFn: (builtin: (typeof builtins)[number]) => api<ProjectionContract>("/api/projection-contracts", { method: "POST", ...jsonBody({ contract_key: builtin.key, target_role: builtin.role, table_name: builtin.table, environment: "local", primary_key: ["content_key"], field_mappings: mappings[builtin.role], input_versions: { label_schema_version: "published-label-v1" }, mode: "snapshot", idempotency_key_template: "{table_name}:{content_key}:{label_version}", checkpoint: { kind: "published_label_id" }, reconciliation: { checks: ["row_count", "missing", "payload_hash", "version"] }, rollback: { strategy: "rebuild_previous_published_version" }, owner: "tpeng-label-platform", status: "draft" }) }),
    onSuccess: async (created) => { await queryClient.invalidateQueries({ queryKey: ["projection-contracts"] }); setSelectedId(created.id); toast.success("本地投影合同版本已登记") },
    onError: (error) => toast.error(error.message),
  })
  return <>
    <PageHeader index="A.6" title="下游表投影" description="统一维护一个大维表和数个职责明确的小表的版本化合同。本页只连接 Label System 本地模拟适配器，不写公司业务数据库。" actions={<Button variant="secondary" onClick={() => contracts.refetch()}><ArrowsClockwise />刷新</Button>} />
    <div className="mx-auto shell-content space-y-6 px-5 py-8 md:px-8 lg:px-10">
      <section className="grid gap-px border-y border-[var(--line-strong)] bg-[var(--line)] sm:grid-cols-3"><Metric label="内置目标" value="3" /><Metric label="已登记合同" value={String(contracts.data?.items.length ?? 0)} /><Metric label="最近一致" value={String(Array.from(latestByRole.values()).filter((item) => item.latest_reconciliation?.status === "matched").length)} /></section>
      <section className="border-y border-[var(--line-strong)] bg-white"><div className="grid grid-cols-[minmax(0,1fr)_160px_120px_160px_auto] gap-4 border-b border-[var(--line)] px-5 py-3 text-xs font-semibold text-[var(--muted)]"><span>投影目标</span><span>本地表</span><span>版本</span><span>最近对账</span><span>操作</span></div>{builtins.map((builtin) => { const contract = latestByRole.get(builtin.role); return <div key={builtin.role} className="grid grid-cols-[minmax(0,1fr)_160px_120px_160px_auto] items-center gap-4 border-b border-[var(--line)] px-5 py-4 last:border-0"><div><div className="flex items-center gap-2"><Database /><p className="text-sm font-bold">{builtin.label}</p></div><p className="mt-1 text-xs text-[var(--muted)]">{builtin.note}</p></div><span className="font-data text-xs">{builtin.table}</span><span className="font-data text-xs">{contract ? `v${contract.version}` : "未登记"}</span><Badge tone={contract?.latest_reconciliation?.status === "matched" ? "success" : contract?.latest_reconciliation ? "warning" : "neutral"}>{contract?.latest_reconciliation?.status === "matched" ? "一致" : contract?.latest_reconciliation ? "需处理漂移" : "尚未对账"}</Badge><div className="flex gap-2">{contract ? <Button size="sm" variant="secondary" onClick={() => setSelectedId(contract.id)}>映射与对账</Button> : <Button size="sm" onClick={() => register.mutate(builtin)} disabled={register.isPending}><Plus />登记 v1</Button>}</div></div>})}</section>
      <section className="border border-[var(--line)] bg-[#f7f9ef] p-4 text-xs leading-6"><strong>边界：</strong>下游表、索引、向量和图谱均是可重建消费投影，不是 Canonical 事实主库；真实表名、权限、单写者和上线窗口仍需下一阶段冻结。</section>
    </div>
    <ProjectionReconciliationDrawer contract={selected} open={selected != null} onOpenChange={(open) => !open && setSelectedId(null)} />
  </>
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-white p-5"><p className="text-xs text-[var(--muted)]">{label}</p><p className="font-data mt-2 text-2xl font-bold">{value}</p></div> }
