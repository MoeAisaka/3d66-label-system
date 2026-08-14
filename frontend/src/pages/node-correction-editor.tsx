import { useEffect, useMemo, useState } from "react"
import {
  ArrowRight,
  CheckCircle,
  ClockCounterClockwise,
  FloppyDisk,
  Path,
  PencilSimple,
  WarningCircle,
} from "@phosphor-icons/react"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { api, jsonBody } from "@/lib/api"
import {
  buildCorrectionNodes,
  CALL_A_GROUP_META,
  cloneJson,
  confidenceLabel,
  correctionValueLabel,
  EMPTY_CORRECTION_HISTORY_TEXT,
  NODE_STAGE_META,
  normalizeRuleHits,
  redlineReasonsAfterToggle,
  ruleEvidenceDelta,
  valuesEqual,
  type CorrectionNode,
  type NodeCorrectionConfidence,
  type NodeCorrectionEvidence,
  type NodeCorrectionHistoryItem,
  type RuleHit,
} from "@/lib/node-correction"
import type { Evaluation } from "@/lib/types"

type CorrectNodeResponse = {
  evaluation_result_id: number
  score: number | null
  level: string | null
  correction: NodeCorrectionHistoryItem
  correction_history: NodeCorrectionHistoryItem[]
}

const NODE_TYPE_LABEL: Record<NodeCorrectionHistoryItem["node_type"], string> = {
  call_a_field: "调用A字段",
  precheck_field: "调用A字段",
  redline: "红线判断",
  track: "赛道归属",
  dimension_rule: "维度规则",
  final_level: "最终等级",
}

export function NodeCorrectionEditor({
  evaluation,
  corrector,
  onCorrected,
}: {
  evaluation: Evaluation
  corrector: string
  onCorrected: () => Promise<void> | void
}) {
  const nodes = useMemo(() => buildCorrectionNodes(evaluation), [evaluation])
  const [selectedId, setSelectedId] = useState("")
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0]
  const [draftValue, setDraftValue] = useState<unknown>(null)
  const [reason, setReason] = useState("")

  useEffect(() => {
    if (!nodes.length) {
      setSelectedId("")
      return
    }
    if (!nodes.some((node) => node.id === selectedId)) setSelectedId(nodes[0].id)
  }, [nodes, selectedId])

  useEffect(() => {
    setDraftValue(selected ? cloneJson(selected.currentValue) : null)
    setReason("")
  }, [evaluation.id, selected?.id, selected?.currentValue])

  const correction = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("请先选择要纠偏的节点")
      const evidence: NodeCorrectionEvidence[] = selected.nodeType === "dimension_rule"
        ? ruleEvidenceDelta(selected.currentValue, draftValue)
        : []
      const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`
      return api<CorrectNodeResponse>(
        `/api/evaluation-results/${evaluation.id}/correct-node`,
        {
          method: "POST",
          ...jsonBody({
            correction_key: `ui-${evaluation.id}-${selected.id}-${suffix}`.slice(0, 120),
            node_type: selected.nodeType,
            node_path: selected.nodePath,
            old_value: selected.currentValue,
            new_value: normalizedDraftValue(selected, draftValue),
            evidence,
            reason: reason.trim(),
          }),
        },
      )
    },
    onSuccess: async (response) => {
      const score = response.score == null ? "无权威分" : `${response.score} 分`
      const outcome = selected?.nodePath === "call_a.score"
        ? `等级已按分数重算为 ${response.level || "无等级"}`
        : selected?.nodePath === "call_a.grade"
          ? `人工等级已更新为 ${response.level || "无等级"}，分数保持 ${score}`
          : selected?.nodeType === "call_a_field"
            ? `字段已更新，分数保持 ${score}`
            : `下游已刷新为 ${score} / ${response.level || "无等级"}`
      toast.success(`节点纠偏已提交：${outcome}`)
      setReason("")
      await onCorrected()
    },
    onError: async (error) => {
      toast.error(error.message)
      await onCorrected()
    },
  })

  const normalizedDraft = selected ? normalizedDraftValue(selected, draftValue) : null
  const changed = selected ? !valuesEqual(selected.currentValue, normalizedDraft) : false
  const invalidRuleEvidence = selected?.editor === "dimension_rules"
    && normalizeRuleHits(normalizedDraft).some((hit) => !hit.evidence.trim())
  const invalidTags = selected?.nodePath === "call_a.tags"
    && (!Array.isArray(normalizedDraft) || normalizedDraft.length < 4)
  const invalidScore = selected?.nodePath === "call_a.score"
    && (!Number.isInteger(normalizedDraft) || Number(normalizedDraft) < 0 || Number(normalizedDraft) > 100)
  const history = Array.isArray(evaluation.correction_history) ? evaluation.correction_history : []
  const hasReplayContext = Boolean(evaluation.scoring?.v3_context)
  const selectedReadOnly = Boolean(selected?.readOnly || (!hasReplayContext && selected?.nodeType !== "call_a_field"))
  const canSubmit = Boolean(selected && !selectedReadOnly && changed && reason.trim() && !invalidRuleEvidence && !invalidTags && !invalidScore && !correction.isPending)
  const readOnlyDimensionCount = nodes.filter((node) => node.stage === 4 && node.readOnly).length

  return (
    <section className="mx-auto max-w-[1820px] border-t border-[var(--line-strong)] bg-white" aria-label="节点纠偏工作台">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line-strong)] px-5 py-5 md:px-7">
        <div>
          <div className="flex items-center gap-2">
            <Path size={22} weight="bold" />
            <h2 className="font-editorial text-2xl font-bold">节点纠偏工作台</h2>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
            调用A的12个字段按评分类、文案类、分类类、缺陷类逐项纠偏；红线、赛道、维度规则和最终等级沿用原有节点链路。所有变更追加留痕。
          </p>
        </div>
        <div className="text-right">
          <Badge tone={hasReplayContext ? "success" : "danger"}>{hasReplayContext ? "可确定性重放" : "缺少冻结上下文"}</Badge>
          <p className="font-data mt-2 text-xs text-[var(--muted)]">结果 #{String(evaluation.id).padStart(5, "0")} · 当前 {evaluation.score ?? "—"} 分 / {evaluation.level || "—"}</p>
        </div>
      </header>

      <div className="grid grid-cols-5 border-b border-[var(--line-strong)] bg-[#fafbf8]">
        {NODE_STAGE_META.map((item, index) => (
          <div key={item.stage} className="relative min-w-0 border-r border-[var(--line)] px-2 py-3 last:border-r-0 md:px-4">
            <div className="flex items-center gap-2">
              <span className="font-data flex size-6 shrink-0 items-center justify-center border border-[var(--line-strong)] bg-white text-[0.68rem] font-bold">{String(item.stage).padStart(2, "0")}</span>
              <div className="min-w-0"><p className="truncate text-xs font-bold md:text-sm">{item.label}</p><p className="mt-0.5 hidden truncate text-[0.68rem] text-[var(--muted)] lg:block">{item.description}</p></div>
            </div>
            {index < NODE_STAGE_META.length - 1 && <ArrowRight className="absolute -right-2.5 top-4 z-10 hidden bg-[#fafbf8] text-[var(--muted)] md:block" size={18} />}
          </div>
        ))}
      </div>

      {!hasReplayContext && (
        <div className="flex items-start gap-3 border-b border-[var(--line)] bg-[#fff8f7] px-5 py-5 md:px-7">
          <WarningCircle className="mt-0.5 shrink-0 text-[#8d2924]" size={22} />
          <div><p className="text-sm font-bold">这条旧评测无法重放评分链路</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">已存储的调用A字段仍可独立纠偏；红线、赛道和维度节点保持只读，建议使用当前 v3 配置重跑。</p></div>
        </div>
      )}
      <>
        {readOnlyDimensionCount > 0 && (
          <div className="flex items-start gap-3 border-b border-[#ead7a5] bg-[#fff9ea] px-5 py-4 md:px-7">
            <WarningCircle className="mt-0.5 shrink-0 text-[#8a5a00]" size={20} />
            <div><p className="text-sm font-bold">部分旧维度仅可查看</p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">该结果由旧引擎产出，维度规则版本不一致；已对齐的维度仍可正常纠偏，未对齐维度建议用新引擎重跑后再处理。</p></div>
          </div>
        )}
        <div className="grid min-h-[560px] xl:grid-cols-[420px_minmax(0,1fr)]">
          <nav className="border-b border-[var(--line-strong)] xl:border-b-0 xl:border-r" aria-label="判断路径节点">
            {NODE_STAGE_META.map((stage) => {
              const stageNodes = nodes.filter((node) => node.stage === stage.stage)
              return <div key={stage.stage} className="border-b border-[var(--line)] last:border-b-0">
                <div className="flex items-center justify-between bg-[#fafbf8] px-4 py-2.5">
                  <p className="text-xs font-bold">{stage.label}</p>
                  <span className="font-data text-[0.68rem] text-[var(--muted)]">{stageNodes.length} 个节点</span>
                </div>
                {stageNodes.length ? stageNodes.map((node, nodeIndex) => (
                  <div key={node.id}>
                  {(nodeIndex === 0 || stageNodes[nodeIndex - 1]?.group !== node.group) && (
                    <div className="flex items-center justify-between border-t border-[var(--line)] bg-[#f4f5f1] px-4 py-2 text-[0.68rem]">
                      <span className="font-bold">{node.group ? CALL_A_GROUP_META.find((group) => group.key === node.group)?.label : "其它流程信号"}</span>
                      <span className="text-[var(--muted)]">{node.group ? CALL_A_GROUP_META.find((group) => group.key === node.group)?.description : "分类置信度与兼容信号"}</span>
                    </div>
                  )}
                  <button
                    type="button"
                    className={`block w-full border-t border-[var(--line)] px-4 py-3 text-left transition-colors first:border-t-0 ${selected?.id === node.id ? "bg-[#f1f7d9]" : "bg-white hover:bg-[#fafbf8]"}`}
                    onClick={() => setSelectedId(node.id)}
                  >
                    <span className="flex items-start justify-between gap-3"><span className="text-sm font-semibold">{node.label}</span><Badge tone={node.stage === 2 && node.summary === "已命中" ? "danger" : node.stage === 4 && node.summary.startsWith("命中") ? "warning" : "neutral"}>{node.summary}</Badge></span>
                    <span className="mt-2 block line-clamp-2 text-xs leading-5 text-[var(--muted)]">{node.evidenceLines[0]}</span>
                  </button>
                  </div>
                )) : <p className="border-t border-[var(--line)] px-4 py-3 text-xs text-[var(--muted)]">当前流程没有该类可编辑节点</p>}
              </div>
            })}
          </nav>

          <div className="min-w-0">
            {selected ? (
              <div className="px-5 py-5 md:px-7 md:py-6">
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] pb-4">
                  <div>
                    <p className="font-data text-[0.68rem] text-[var(--muted)]">{NODE_STAGE_META[selected.stage - 1].label} · {NODE_TYPE_LABEL[selected.nodeType]}</p>
                    <h3 className="font-editorial mt-1 text-xl font-bold">{selected.label}</h3>
                  </div>
                  <Badge tone={selectedReadOnly ? "neutral" : "active"}>{selectedReadOnly ? "只读" : <><PencilSimple />可编辑</>}</Badge>
                </div>

                <div className="grid gap-5 py-5 lg:grid-cols-[minmax(0,1fr)_280px]">
                  <div className="min-w-0">
                    <p className="mb-2 text-xs font-bold">节点当前值与新值</p>
                    {selectedReadOnly ? (
                      <div className="border border-[#ead7a5] bg-[#fff9ea] px-4 py-4 text-sm leading-6 text-[#6b4b0b]">{selected.compatibilityMessage || "该旧评测缺少冻结上下文，此评分链路节点仅可查看。"}</div>
                    ) : (
                      <NodeValueEditor node={selected} value={draftValue} onChange={setDraftValue} />
                    )}
                    {!selectedReadOnly && invalidRuleEvidence && <p className="mt-2 text-xs font-semibold text-[#8d2924]">每条已勾选规则都必须填写可定位的中文证据。</p>}
                    {!selectedReadOnly && invalidTags && <p className="mt-2 text-xs font-semibold text-[#8d2924]">主要标签至少保留 4 个。</p>}
                    {!selectedReadOnly && invalidScore && <p className="mt-2 text-xs font-semibold text-[#8d2924]">综合评分必须是 0-100 的整数。</p>}
                  </div>
                  <aside className="border border-[var(--line)] bg-[#fafbf8] px-4 py-3">
                    <p className="text-xs font-bold">当前证据</p>
                    <div className="mt-2 space-y-2">
                      {selected.evidenceLines.map((line, index) => <p key={`${line}-${index}`} className="text-xs leading-5 text-[var(--muted)]">{line}</p>)}
                    </div>
                  </aside>
                </div>

                {!selectedReadOnly && (
                  <>
                <div className="grid gap-3 border-t border-[var(--line)] pt-5 md:grid-cols-2">
                  <label><span className="mb-2 block text-xs font-bold">纠偏人（当前登录，服务端记录）</span><Input value={corrector} readOnly /></label>
                  <label><span className="mb-2 block text-xs font-bold">纠偏原因（必填）</span><Input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明为什么要改这个节点" maxLength={1000} /></label>
                </div>
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border border-[var(--line)] bg-[#fafbf8] px-4 py-3">
                  <p className="text-xs leading-5 text-[var(--muted)]">服务端会校验旧值防并发覆盖。修改综合评分会自动重算等级；直接改等级以人工等级为准；其他调用A字段不影响分数。</p>
                  <Button onClick={() => correction.mutate()} disabled={!canSubmit}><FloppyDisk weight="bold" />{correction.isPending ? "正在提交" : submitLabel(selected)}</Button>
                </div>
                  </>
                )}
              </div>
            ) : <div className="flex min-h-[420px] items-center justify-center text-sm text-[var(--muted)]">当前没有可编辑节点</div>}
          </div>
        </div>
        </>

      <CorrectionHistory history={history} nodes={nodes} />
    </section>
  )
}

function NodeValueEditor({ node, value, onChange }: { node: CorrectionNode; value: unknown; onChange: (value: unknown) => void }) {
  if (node.editor === "dimension_rules") {
    const hits = normalizeRuleHits(value)
    return <div className="divide-y divide-[var(--line)] border border-[var(--line-strong)]">
      {(node.ruleDefinitions ?? []).map((rule) => {
        const hit = hits.find((item) => item.rule_id === rule.rule_id)
        const bonus = rule.kind === "bonus"
        const polarityLabel = bonus ? "加分" : "扣分"
        const scoreLabel = `${polarityLabel} ${rule.value} 分`
        return <div key={rule.rule_id} className={`px-4 py-4 ${hit ? "bg-[#fffaf0]" : "bg-white"}`}>
          <div className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-1 size-4 accent-[#11130f]"
              checked={Boolean(hit)}
              onChange={(event) => onChange(toggleRuleHit(hits, rule.rule_id, event.target.checked))}
              aria-label={`${eventLabel(hit)}规则 ${rule.rule_id}`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div><p className="text-sm font-bold">{rule.description}</p><p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">规则 {rule.rule_id}{rule.tags?.length ? ` · ${rule.tags.join(" / ")}` : ""}</p></div>
                <Badge tone={hit ? (bonus ? "success" : "warning") : "neutral"}>{hit ? `命中 · ${scoreLabel}` : `未命中 · ${scoreLabel}`}</Badge>
              </div>
              {hit && <div className="mt-3 grid gap-3 md:grid-cols-[160px_minmax(0,1fr)]">
                <label><span className="mb-1.5 block text-xs font-semibold">置信度</span><select className={selectClassName} value={hit.confidence} onChange={(event) => onChange(updateRuleHit(hits, rule.rule_id, { confidence: event.target.value as NodeCorrectionConfidence }))}><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label>
                <label><span className="mb-1.5 block text-xs font-semibold">规则证据</span><Textarea className="min-h-20" value={hit.evidence} onChange={(event) => onChange(updateRuleHit(hits, rule.rule_id, { evidence: event.target.value }))} placeholder="填写画面中可定位、可复核的证据" /></label>
              </div>}
            </div>
          </div>
        </div>
      })}
    </div>
  }

  if (node.editor === "redline" && node.redlineRule) {
    const signals = Array.isArray(value) ? value.map((item) => String(item)) : []
    const hit = node.redlineRule.matchAny.some((item) => signals.includes(item))
      && !node.redlineRule.exemptions.some((item) => signals.includes(item))
    return <div className="grid grid-cols-2 gap-2">
      <Button variant={!hit ? "primary" : "secondary"} onClick={() => onChange(redlineReasonsAfterToggle(value, node.redlineRule!, false))}>不命中</Button>
      <Button variant={hit ? "danger" : "secondary"} onClick={() => onChange(redlineReasonsAfterToggle(value, node.redlineRule!, true))}>命中红线</Button>
    </div>
  }

  if (node.editor === "category") {
    const raw = typeof value === "string" ? value : ""
    const parts = raw.split(/[,，]/)
    const primary = parts.shift()?.trim() ?? ""
    const secondary = parts.join(",").trim()
    const primaryOptions = [...(node.options ?? [])]
    if (primary && !primaryOptions.some((option) => option.value === primary)) {
      primaryOptions.unshift({ value: primary, label: `${primary}（当前值）` })
    }
    const updateCategory = (nextPrimary: string, nextSecondary: string) => {
      onChange(nextSecondary.trim() ? `${nextPrimary},${nextSecondary.trim()}` : nextPrimary)
    }
    return <div className="grid gap-3 md:grid-cols-2">
      <label><span className="mb-1.5 block text-xs font-semibold">一级分类</span><select className={selectClassName} value={primary} onChange={(event) => updateCategory(event.target.value, secondary)}><option value="" disabled>请选择一级分类</option>{primaryOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <label><span className="mb-1.5 block text-xs font-semibold">二级分类</span><Input value={secondary} maxLength={80} onChange={(event) => updateCategory(primary, event.target.value)} placeholder="填写二级分类，例如：大平层" /></label>
    </div>
  }

  if (node.editor === "tags") {
    return <TagEditor value={value} onChange={onChange} />
  }

  if (node.editor === "track" || node.editor === "level" || node.valueKind === "enum") {
    const hasEmptyOption = (node.options ?? []).some((option) => option.value === "")
    return <select className={selectClassName} value={typeof value === "string" ? value : ""} onChange={(event) => onChange(event.target.value)}>
      {!hasEmptyOption && <option value="" disabled>请选择</option>}
      {(node.options ?? []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  }

  if (node.valueKind === "string_list") {
    if (node.options?.length) {
      const selected = Array.isArray(value) ? value.map((item) => String(item)) : []
      return <div className="grid gap-2 sm:grid-cols-2">
        {node.options.map((option) => <label key={option.value} className={`flex min-h-11 items-center gap-2 border px-3 text-sm ${selected.includes(option.value) ? "border-[var(--ink)] bg-[#f1f7d9]" : "border-[var(--line-strong)] bg-white"}`}><input type="checkbox" className="size-4 accent-[#11130f]" checked={selected.includes(option.value)} onChange={(event) => onChange(event.target.checked ? [...selected, option.value] : selected.filter((item) => item !== option.value))} />{option.label}</label>)}
      </div>
    }
    const lines = Array.isArray(value) ? value.join("\n") : ""
    return <Textarea value={lines} onChange={(event) => onChange(event.target.value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean))} placeholder="每行一个信号" />
  }

  if (node.valueKind === "number") {
    return <Input type="number" min={0} max={1} step={0.01} value={typeof value === "number" ? value : ""} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />
  }
  if (node.valueKind === "score") {
    return <Input type="number" min={0} max={100} step={1} value={typeof value === "number" ? value : ""} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />
  }
  if (node.valueKind === "multiline") {
    const text = typeof value === "string" ? value : ""
    return <div><Textarea className="min-h-28" value={text} maxLength={node.maxLength} onChange={(event) => onChange(event.target.value)} /><p className="mt-1.5 text-right text-xs text-[var(--muted)]">{text.length} / {node.maxLength ?? 1000} 字</p></div>
  }
  const text = typeof value === "string" ? value : correctionValueLabel(value)
  return <div><Input value={text} maxLength={node.maxLength} onChange={(event) => onChange(event.target.value)} />{node.maxLength && <p className="mt-1.5 text-xs text-[var(--muted)]">最多 {node.maxLength} 个字，当前 {text.length} 个字</p>}</div>
}

function TagEditor({ value, onChange }: { value: unknown; onChange: (value: unknown) => void }) {
  const [draftTag, setDraftTag] = useState("")
  const tags = Array.isArray(value) ? value.map((item) => String(item).trim()).filter(Boolean) : []
  const addTag = () => {
    const next = draftTag.trim()
    if (!next || tags.includes(next)) return
    onChange([...tags, next])
    setDraftTag("")
  }
  return <div className="space-y-3">
    <div className="flex min-h-12 flex-wrap gap-2 border border-[var(--line-strong)] bg-white p-3">
      {tags.map((tag) => <button key={tag} type="button" className="inline-flex items-center gap-1 border border-[var(--line-strong)] bg-[#f1f7d9] px-2.5 py-1 text-xs font-semibold" onClick={() => onChange(tags.filter((item) => item !== tag))} aria-label={`删除标签 ${tag}`}>{tag}<span aria-hidden="true">×</span></button>)}
      {!tags.length && <span className="text-xs text-[var(--muted)]">暂无标签</span>}
    </div>
    <div className="flex gap-2"><Input value={draftTag} maxLength={40} onChange={(event) => setDraftTag(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTag() } }} placeholder="输入标签后回车" /><Button type="button" variant="secondary" onClick={addTag}>添加标签</Button></div>
    <p className="text-xs text-[var(--muted)]">至少保留 4 个主要标签；点击已有标签可删除。</p>
  </div>
}

function toggleRuleHit(hits: RuleHit[], ruleId: string, checked: boolean): RuleHit[] {
  if (!checked) return hits.filter((hit) => hit.rule_id !== ruleId)
  if (hits.some((hit) => hit.rule_id === ruleId)) return hits
  return [...hits, { rule_id: ruleId, confidence: "medium", evidence: "" }]
}

function updateRuleHit(hits: RuleHit[], ruleId: string, patch: Partial<RuleHit>): RuleHit[] {
  return hits.map((hit) => hit.rule_id === ruleId ? { ...hit, ...patch } : hit)
}

function normalizedDraftValue(node: CorrectionNode, value: unknown): unknown {
  if (node.editor === "dimension_rules") {
    const configuredOrder = (node.ruleDefinitions ?? []).map((rule) => rule.rule_id)
    return normalizeRuleHits(value).sort((left, right) => configuredOrder.indexOf(left.rule_id) - configuredOrder.indexOf(right.rule_id))
  }
  if (node.editor === "tags") {
    return Array.isArray(value)
      ? [...new Set(value.map((item) => String(item).trim()).filter(Boolean))]
      : []
  }
  if (node.editor === "category" && typeof value === "string") {
    const parts = value.split(/[,，]/).map((part) => part.trim()).filter(Boolean)
    return parts.join(",")
  }
  return value
}

function submitLabel(node: CorrectionNode) {
  if (node.nodePath === "call_a.score") return "提交纠偏并重算等级"
  if (node.nodePath === "call_a.grade") return "提交人工等级"
  if (node.nodeType === "call_a_field") return "提交字段纠偏"
  if (node.nodeType === "final_level") return "提交最终等级"
  return "提交纠偏并重算"
}

function eventLabel(hit: RuleHit | undefined) {
  return hit ? "取消命中" : "勾选命中"
}

function CorrectionHistory({ history, nodes }: { history: NodeCorrectionHistoryItem[]; nodes: CorrectionNode[] }) {
  return <section className="border-t border-[var(--line-strong)]" aria-label="纠偏历史">
    <div className="flex items-center justify-between gap-3 bg-[#fafbf8] px-5 py-4 md:px-7">
      <div className="flex items-center gap-2"><ClockCounterClockwise size={19} /><h3 className="text-sm font-bold">纠偏历史</h3></div>
      <Badge>{history.length} 条</Badge>
    </div>
    {history.length ? (
      <div className="divide-y divide-[var(--line)] border-t border-[var(--line)]">
        {[...history].reverse().map((item, reverseIndex) => (
          <article key={item.correction_key || `${item.corrected_at}-${reverseIndex}`} className="grid gap-3 px-5 py-4 md:grid-cols-[180px_minmax(0,1fr)_240px] md:px-7">
            <div><Badge tone={item.downstream_recomputed ? "success" : "active"}>{NODE_TYPE_LABEL[item.node_type]}</Badge><p className="mt-2 text-xs text-[var(--muted)]">{nodes.find((node) => node.nodeType === item.node_type && node.nodePath === item.node_path)?.label || NODE_TYPE_LABEL[item.node_type]}</p></div>
            <div><p className="text-sm font-semibold">{historyValueLabel(item, nodes, item.old_value)} <ArrowRight className="mx-1 inline" size={14} /> {historyValueLabel(item, nodes, item.new_value)}</p><p className="mt-2 text-xs leading-5 text-[var(--muted)]">原因：{item.reason}</p>{item.evidence?.length ? <p className="mt-1 text-xs leading-5 text-[var(--muted)]">逐规则证据：{item.evidence.map((evidence) => `${evidence.rule_id} ${confidenceLabel(evidence.old_confidence) || "无"}→${confidenceLabel(evidence.new_confidence) || "无"}`).join("；")}</p> : null}</div>
            <div className="md:text-right"><p className="text-xs font-semibold">{item.corrector || "未知纠偏人"}</p><p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">{formatDate(item.corrected_at)}</p><p className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--muted)]"><CheckCircle />{historyOutcomeLabel(item)}</p></div>
          </article>
        ))}
      </div>
    ) : <p className="border-t border-[var(--line)] px-5 py-5 text-sm text-[var(--muted)] md:px-7">{EMPTY_CORRECTION_HISTORY_TEXT}</p>}
  </section>
}

function historyOutcomeLabel(item: NodeCorrectionHistoryItem) {
  if (item.node_path === "call_a.score") return "等级已按分数重算"
  if (item.node_path === "call_a.grade") return "人工等级已覆盖"
  if (item.node_type === "call_a_field") return "字段已更新，分数未变"
  return item.downstream_recomputed ? "下游已重算" : "仅改最终等级"
}

function historyValueLabel(item: NodeCorrectionHistoryItem, nodes: CorrectionNode[], value: unknown) {
  const node = nodes.find((candidate) => candidate.nodeType === item.node_type && candidate.nodePath === item.node_path)
  if (typeof value === "string") {
    const option = node?.options?.find((candidate) => candidate.value === value)
    if (option) return option.label
  }
  return correctionValueLabel(value)
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false })
}

const selectClassName = "flex h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm text-foreground hover:border-[#bfc6ba] focus-visible:border-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1"
