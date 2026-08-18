import { useMemo } from "react"
import { FloppyDisk, LockKey, WarningCircle } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  correctionNodeDisplayValue,
  correctionNodeEditable,
  correctionNodeInputValue,
  correctionNodeOptions,
  correctionNodeValueType,
  groupCorrectionNodes,
  parseCorrectionNodeInput,
} from "./contract-renderer"
import type {
  CorrectionContractNode,
  CorrectionDraft,
  CorrectionNodeValue,
  CorrectionView,
} from "./types"

export { groupCorrectionNodes, correctionNodeOptions, correctionNodeValueType } from "./contract-renderer"

export function renderCorrectionNode(
  node: CorrectionContractNode,
  value: CorrectionNodeValue,
  onChange: (value: CorrectionNodeValue) => void,
  disabled = false,
) {
  const readOnly = disabled || !correctionNodeEditable(node)
  const valueType = correctionNodeValueType(node)
  const inputValue = correctionNodeInputValue(node, value)
  if (valueType === "enum") {
    return (
      <select
        className="flex h-11 min-w-0 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm focus-visible:border-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:bg-[#f1f3ef]"
        value={inputValue}
        disabled={readOnly}
        onChange={(event) => onChange(parseCorrectionNodeInput(node, event.target.value))}
        aria-label={node.label}
      >
        <option value="">请选择</option>
        {correctionNodeOptions(node).map((option) => (
          <option key={String(option)} value={String(option)}>{String(option)}</option>
        ))}
      </select>
    )
  }
  if (valueType === "number") {
    return (
      <Input
        type="number"
        value={inputValue}
        min={node.minimum ?? node.min}
        max={node.maximum ?? node.max}
        disabled={readOnly}
        onChange={(event) => onChange(parseCorrectionNodeInput(node, event.target.value))}
        aria-label={node.label}
      />
    )
  }
  if (valueType === "boolean") {
    return (
      <label className="flex min-h-11 items-center gap-3 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--primary)]"
          checked={Boolean(correctionNodeDisplayValue(node, value))}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.checked)}
          aria-label={node.label}
        />
        <span>{eventLabel(Boolean(correctionNodeDisplayValue(node, value)))}</span>
      </label>
    )
  }
  if (valueType === "text") {
    return (
      <Input
        value={inputValue}
        disabled={readOnly}
        onChange={(event) => onChange(parseCorrectionNodeInput(node, event.target.value))}
        aria-label={node.label}
      />
    )
  }
  return (
    <Textarea
      value={inputValue}
      disabled={readOnly}
      onChange={(event) => onChange(parseCorrectionNodeInput(node, event.target.value))}
      aria-label={node.label}
      className="min-h-28 font-data text-xs"
    />
  )
}

function eventLabel(value: boolean): string {
  return value ? "是" : "否"
}

function layerLabel(layer: "A" | "B" | "V3"): string {
  return layer === "A" ? "调用 A" : layer === "B" ? "调用 B" : "等级撮合器"
}

export function CorrectionContractRenderer({
  view,
  draft,
  onChange,
  onSubmit,
  pending = false,
  submitDisabled = false,
  disabled = false,
}: {
  view: CorrectionView
  draft: CorrectionDraft
  onChange: (nodeKey: string, patch: Partial<CorrectionDraft[string]>) => void
  onSubmit?: () => void
  pending?: boolean
  submitDisabled?: boolean
  disabled?: boolean
}) {
  const grouped = useMemo(() => groupCorrectionNodes(view.nodes), [view.nodes])
  if (!view.contract) {
    return (
      <section className="border-y border-[#e4c7c3] bg-[#fff8f7] px-5 py-5" aria-label="纠偏合同不可用">
        <p className="flex items-center gap-2 text-sm font-semibold text-[#8d2924]"><WarningCircle />当前运行没有可编辑的纠偏合同</p>
        <p className="mt-2 text-xs leading-5 text-[#74302b]">{view.unavailable_reason || "历史快照缺少完整合同，只能查看原始结果。"}</p>
      </section>
    )
  }
  return (
    <section className="space-y-4 border-y border-[var(--line)] bg-[#fafbf8] px-5 py-5" aria-label="合同驱动纠偏面板">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-bold">合同驱动纠偏</p>
          <p className="mt-1 text-xs text-[var(--muted)]">本轮版本 {view.contract.contract_version || "—"} · 合同哈希 {view.contract.contract_hash.slice(0, 12)}…</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={view.snapshot_status === "frozen" ? "success" : "warning"}>{view.snapshot_status === "frozen" ? "历史快照" : "只读兼容"}</Badge>
          {view.read_only && <Badge tone="warning"><LockKey />不可编辑</Badge>}
        </div>
      </header>
      {view.unavailable_reason && <p className="border border-[#e5c9a7] bg-[#fff6e9] px-3 py-2 text-xs leading-5 text-[#7d4308]">{view.unavailable_reason}</p>}
      <div className="space-y-4">
        {(["A", "B", "V3"] as const).map((layer) => {
          const nodes = grouped[layer]
          if (!nodes.length) return null
          return (
            <section key={layer} className="space-y-3 border-t border-[var(--line)] pt-4 first:border-t-0 first:pt-0" aria-label={`${layerLabel(layer)}纠偏节点`}>
              <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-bold">{layerLabel(layer)}</h3><span className="font-data text-xs text-[var(--muted)]">{nodes.length} 项</span></div>
              <div className="space-y-3">
                {nodes.map((node) => {
                  const nodeDraft = draft[node.node_key] ?? { value: node.human_value ?? node.current_value ?? node.model_value, reason: node.reason ?? "", evidence: [] }
                  const editable = !disabled && !view.read_only && correctionNodeEditable(node)
                  const evidenceDescription = correctionEvidenceDescription(node)
                  const evidenceText = correctionEvidenceText(nodeDraft.evidence)
                  return (
                    <article key={node.node_key} className="space-y-2 border border-[var(--line-strong)] bg-white px-4 py-4" data-testid={`correction-node-${node.node_key}`}>
                      <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-sm font-semibold">{node.label}</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">{node.description}</p></div>{!editable && <Badge tone="neutral"><LockKey />只读</Badge>}{node.inheritance?.status && node.inheritance.status !== "current" && <Badge tone={node.inheritance.status === "inherited" ? "success" : "warning"}>{node.inheritance.status === "inherited" ? "已继承" : node.inheritance.status === "new" ? "新增待确认" : "合同已变化"}</Badge>}</div>
                      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(220px,0.7fr)]">
                        <div>{renderCorrectionNode(node, nodeDraft.value, (value) => onChange(node.node_key, { value }), !editable)}</div>
                        <div className="space-y-3"><label className="block text-xs font-semibold">人工理由{node.required && "（必填）"}<Textarea className="mt-2 min-h-24" value={nodeDraft.reason} disabled={!editable} placeholder={evidenceDescription} onChange={(event) => onChange(node.node_key, { reason: event.target.value })} /></label><label className="block text-xs font-semibold">人工证据{node.evidence && typeof node.evidence === "object" && !Array.isArray(node.evidence) && node.evidence.required && "（必填）"}<Textarea className="mt-2 min-h-20" value={evidenceText} disabled={!editable} placeholder={evidenceDescription} onChange={(event) => onChange(node.node_key, { evidence: event.target.value.trim() ? [{ text: event.target.value }] : [] })} /></label><p className="text-[0.7rem] leading-4 text-[var(--muted)]">{evidenceDescription}</p></div>
                      </div>
                      {node.steps && node.steps.length > 0 && <ol className="flex flex-wrap gap-x-4 gap-y-1 border-t border-[var(--line)] pt-2 text-[0.7rem] text-[var(--muted)]">{node.steps.map((step, index) => <li key={`${node.node_key}-step-${index}`}>{index + 1}. {step}</li>)}</ol>}
                    </article>
                  )
                })}
              </div>
            </section>
          )
        })}
      </div>
      {onSubmit && (
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] pt-4">
          <p className="text-xs leading-5 text-[var(--muted)]">只提交已修改且填写理由的节点；冻结的等级规则仍由服务端重算。</p>
          <Button disabled={disabled || view.read_only || submitDisabled || pending} onClick={onSubmit}>
            <FloppyDisk />{pending ? "正在保存" : "保存合同纠偏"}
          </Button>
        </footer>
      )}
    </section>
  )
}

function correctionEvidenceDescription(node: CorrectionContractNode): string {
  const evidence = node.evidence
  return evidence && typeof evidence === "object" && !Array.isArray(evidence) && typeof evidence.description === "string"
    ? evidence.description
    : "请填写可复核的图片或规则证据"
}

function correctionEvidenceText(evidence: Array<Record<string, unknown>>): string {
  const first = evidence[0]
  if (!first) return ""
  const value = first.text ?? first.new_evidence ?? first.description ?? first.value
  return typeof value === "string" ? value : value == null ? "" : JSON.stringify(value)
}
