import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import type { TagDemandContract } from "@/lib/types"
import type { ReactNode } from "react"

const statusLabels: Record<TagDemandContract["status"], string> = {
  draft: "草稿",
  candidate: "候选",
  active: "现役",
  retired: "已退役",
}

export function TagDemandContractDrawer({
  contract,
  open,
  onOpenChange,
}: {
  contract: TagDemandContract | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <SecondaryDrawer
      open={open}
      onOpenChange={onOpenChange}
      size="wide"
      title={contract ? `${contract.contract_key} · v${contract.version}` : "字段需求合同详情"}
      description="字段矩阵、空值语义、执行变体和投影映射只在二级抽屉查看，避免一级页面平铺。"
    >
      {!contract ? <p className="text-sm text-[var(--muted)]">请选择一个合同版本。</p> : <div className="space-y-7">
        <section className="grid gap-4 border-y border-[var(--line)] py-4 sm:grid-cols-3">
          <Detail label="状态"><Badge tone={contract.status === "active" ? "success" : contract.status === "candidate" ? "warning" : "neutral"}>{statusLabels[contract.status]}</Badge></Detail>
          <Detail label="合同哈希"><span className="font-data break-all text-xs">{contract.contract_hash}</span></Detail>
          <Detail label="创建信息"><span>{contract.created_by} · {new Date(contract.created_at).toLocaleString("zh-CN")}</span></Detail>
        </section>

        <section>
          <SectionTitle>平台字段</SectionTitle>
          <div className="mt-3 overflow-hidden border-y border-[var(--line)]">
            <div className="grid grid-cols-[minmax(0,1fr)_110px_90px_110px] gap-3 border-b border-[var(--line)] px-3 py-2 text-xs font-semibold text-[var(--muted)]"><span>字段</span><span>基数</span><span>本地化</span><span>最大值</span></div>
            {Object.values(contract.definition.semantic_schema.fields).map((field) => <div key={field.field_key} className="grid grid-cols-[minmax(0,1fr)_110px_90px_110px] gap-3 border-b border-[var(--line)] px-3 py-3 text-sm last:border-0"><span className="font-bold">{field.field_key}<small className="ml-2 font-normal text-[var(--muted)]">{field.vocabulary_owner}</small></span><span>{field.cardinality}</span><span>{field.localized ? "是" : "否"}</span><span>{field.max_values}</span></div>)}
          </div>
        </section>

        <section>
          <SectionTitle>类目适用性</SectionTitle>
          <div className="mt-3 space-y-3">{Object.entries(contract.definition.category_applicability).map(([category, fields]) => <div key={category} className="border-y border-[var(--line)] px-3 py-3"><div className="font-bold">{category}</div><div className="mt-2 flex flex-wrap gap-2">{Object.entries(fields).map(([field, status]) => <span key={field} className="border border-[var(--line)] px-2 py-1 text-xs"><strong>{field}</strong> · {status}</span>)}</div></div>)}</div>
        </section>

        <section>
          <SectionTitle>源身份合同</SectionTitle>
          {contract.definition.source_identity ? (
            <div className="mt-3 grid gap-3 border-y border-[var(--line)] px-3 py-4 text-xs sm:grid-cols-2">
              <Detail label="来源 / 对象粒度"><span>{contract.definition.source_identity.source_system} · {contract.definition.source_identity.object_grain}</span></Detail>
              <Detail label="候选复合键"><span className="font-data">{contract.definition.source_identity.identity_fields.join(" + ")}</span></Detail>
              <Detail label="可选消歧字段"><span>{contract.definition.source_identity.optional_disambiguator ?? "无"}</span></Detail>
              <Detail label="版本 / 删除字段"><span>{contract.definition.source_identity.version_field} · {contract.definition.source_identity.deletion_field}</span></Detail>
              <Detail label="唯一性状态"><Badge tone={contract.definition.source_identity.uniqueness_status === "verified" ? "success" : contract.definition.source_identity.uniqueness_status === "conflict" ? "danger" : "warning"}>{contract.definition.source_identity.uniqueness_status}</Badge></Detail>
              <Detail label="签认证据哈希"><span className="font-data break-all">{contract.definition.source_identity.verification_evidence_hash ?? "尚未绑定"}</span></Detail>
            </div>
          ) : <p className="mt-3 border-y border-[var(--line)] px-3 py-4 text-xs text-[var(--muted)]">v1 合同沿用旧身份口径；升级为 v2 后才可绑定源身份签认证据。</p>}
        </section>

        <section>
          <SectionTitle>字段供给路径</SectionTitle>
          {Object.keys(contract.definition.field_supply ?? {}).length ? (
            <div className="mt-3 overflow-x-auto border-y border-[var(--line)]">
              <div className="min-w-[920px]">
                <div className="grid grid-cols-[150px_100px_120px_170px_150px_80px_1fr] gap-3 border-b border-[var(--line)] px-3 py-2 text-xs font-semibold text-[var(--muted)]"><span>字段</span><span>命名空间</span><span>生产方式</span><span>Authority</span><span>Owner</span><span>SLA</span><span>空值 / 回退</span></div>
                {Object.entries(contract.definition.field_supply ?? {}).map(([fieldKey, supply]) => <div key={fieldKey} className="grid grid-cols-[150px_100px_120px_170px_150px_80px_1fr] gap-3 border-b border-[var(--line)] px-3 py-3 text-xs last:border-0"><strong>{fieldKey}</strong><span>{supply.fact_namespace}</span><span>{supply.production_method}</span><span>{supply.source_authority}</span><span>{supply.owner}</span><span>{supply.freshness_sla_hours}h</span><span>{supply.null_semantics.join(" / ")} · {supply.rollback_strategy}</span></div>)}
              </div>
            </div>
          ) : <p className="mt-3 border-y border-[var(--line)] px-3 py-4 text-xs text-[var(--muted)]">当前 v1 合同没有版本化字段供给路径。</p>}
        </section>

        <section>
          <SectionTitle>执行变体</SectionTitle>
          <div className="mt-3 space-y-2">{contract.definition.execution_variants.map((variant) => <div key={`${variant.category_key}-${variant.site_scope}-${variant.asset_scope}-${variant.prompt_version}`} className="grid gap-2 border-y border-[var(--line)] px-3 py-3 text-xs sm:grid-cols-[1fr_1fr_1fr_1fr]"><span>{variant.category_key}</span><span>{variant.site_scope} · {variant.locale}</span><span>{variant.asset_scope} · {variant.prompt_variant}</span><span className="font-data">{variant.prompt_version} / {variant.model_version}</span>{Object.keys(variant.field_applicability_overrides ?? {}).length > 0 && <div className="sm:col-span-4 border-t border-[var(--line)] pt-2"><strong>执行变体覆盖：</strong>{Object.entries(variant.field_applicability_overrides ?? {}).map(([field, status]) => <span key={field} className="ml-2">{variant.asset_scope} → {field}: {status}</span>)}</div>}</div>)}</div>
        </section>

        <section>
          <SectionTitle>质量门槛与投影</SectionTitle>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">{Object.entries(contract.definition.quality_gates).map(([field, gate]) => <div key={field} className="border border-[var(--line)] p-3 text-xs"><div className="font-bold">{field}</div><p className="mt-2 text-[var(--muted)]">Precision ≥ {gate.min_precision} · Recall ≥ {gate.min_recall}</p><p className="mt-1 text-[var(--muted)]">映射覆盖 ≥ {gate.min_mapping_coverage} · 冲突率 ≤ {gate.max_conflict_rate}</p></div>)}{contract.definition.projection_targets.map((target) => <div key={target.target_key} className="border border-[var(--line)] p-3 text-xs"><div className="font-bold">{target.target_key}</div><p className="mt-1 text-[var(--muted)]">{target.locale} · {target.mode}</p></div>)}</div>
        </section>
      </div>}
    </SecondaryDrawer>
  )
}

function SectionTitle({ children }: { children: ReactNode }) { return <h2 className="text-sm font-bold">{children}</h2> }
function Detail({ label, children }: { label: string; children: ReactNode }) { return <div className="space-y-1 text-xs"><p className="text-[var(--muted)]">{label}</p><div>{children}</div></div> }
