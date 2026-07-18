import { useMemo, useState } from "react"
import { Check, WarningCircle, X } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { ReviewCorrection } from "@/lib/types"

export const dimensionLabels: Record<string, string> = {
  composition_viewpoint: "构图与机位",
  lighting_atmosphere: "光影与氛围",
  color_material: "色彩与材质",
  spatial_design_furnishing: "空间设计与家具软装",
  visual_hierarchy: "视觉层级",
  detail_completion: "细节完成度",
  inspiration_reference: "灵感与参考价值",
  presentation_integrity: "画面呈现完整性",
}

const reasons = [
  ["overrated", "评分偏高"],
  ["underrated", "评分偏低"],
  ["ignored_defect", "忽略明显缺陷"],
  ["false_defect", "把正常现象当缺陷"],
  ["photography_as_design", "把摄影质量当设计质量"],
  ["rendering_as_design", "把渲染精美当设计优秀"],
  ["ignored_furnishing", "忽略家具与软装问题"],
  ["boundary_unclear", "等级边界理解错误"],
  ["invalid_evidence", "使用了不可靠证据"],
] as const

const scoringReasons = [
  ["weight_issue", "维度权重不合理"],
  ["level_band_issue", "L1 至 L5 分段不合理"],
  ["cap_issue", "等级上限规则错误"],
  ["render_gate_issue", "效果图 L4/L5 门槛错误"],
] as const

type Draft = { humanGrade: number; reasons: string[]; note: string }

export function ReviewCorrectionForm({
  dimensions,
  modelLevel,
  pending,
  onCancel,
  onSubmit,
}: {
  dimensions: Record<string, any>
  modelLevel: string | null
  pending: boolean
  onCancel: () => void
  onSubmit: (payload: { correctedLevel: string; note: string; corrections: ReviewCorrection[] }) => void
}) {
  const [correctedLevel, setCorrectedLevel] = useState(modelLevel || "L3")
  const [selected, setSelected] = useState<string[]>([])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [scoringSelected, setScoringSelected] = useState(false)
  const [scoringReason, setScoringReason] = useState("")
  const [overallNote, setOverallNote] = useState("")
  const [error, setError] = useState("")

  const selectedCorrections = useMemo(() => selected.map((key) => ({ key, draft: drafts[key] })), [selected, drafts])

  function toggleDimension(key: string) {
    setError("")
    setSelected((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
    setDrafts((current) => current[key] ? current : {
      ...current,
      [key]: { humanGrade: Number(dimensions[key]?.grade || 3), reasons: [], note: "" },
    })
  }

  function updateDraft(key: string, patch: Partial<Draft>) {
    setError("")
    setDrafts((current) => ({ ...current, [key]: { ...current[key], ...patch } }))
  }

  function toggleReason(key: string, reason: string) {
    const current = drafts[key]?.reasons ?? []
    updateDraft(key, { reasons: current.includes(reason) ? current.filter((item) => item !== reason) : [...current, reason] })
  }

  function submit() {
    if (!selected.length && !scoringSelected) {
      setError("请选择至少一个错误维度，或标记评分规则问题")
      return
    }
    for (const { key, draft } of selectedCorrections) {
      const modelGrade = Number(dimensions[key]?.grade || 0)
      if (draft.humanGrade === modelGrade) {
        setError(`${dimensionLabels[key]}的人工分数仍与模型一致，请修改分数或取消选择`)
        return
      }
      if (!draft.reasons.length) {
        setError(`请为${dimensionLabels[key]}选择至少一个错误原因`)
        return
      }
    }
    if (scoringSelected && !scoringReason) {
      setError("请选择评分规则错误的具体原因")
      return
    }
    const corrections: ReviewCorrection[] = selectedCorrections.map(({ key, draft }) => ({
      target_type: "dimension",
      field_key: key,
      model_value: Number(dimensions[key]?.grade || 0),
      human_value: draft.humanGrade,
      reason_codes: draft.reasons,
      note: draft.note.trim(),
    }))
    if (scoringSelected) {
      corrections.push({ target_type: "scoring", field_key: "scoring_engine", model_value: modelLevel, human_value: correctedLevel, reason_codes: [scoringReason], note: overallNote.trim() })
    }
    const summary = selectedCorrections.map(({ key, draft }) => {
      const reasonText = draft.reasons.map((code) => reasons.find(([value]) => value === code)?.[1] || code).join("、")
      return `${dimensionLabels[key]}：${dimensions[key]?.grade ?? "—"}级改为${draft.humanGrade}级（${reasonText}）${draft.note.trim() ? `，${draft.note.trim()}` : ""}`
    }).join("；")
    onSubmit({ correctedLevel, corrections, note: [summary, overallNote.trim()].filter(Boolean).join("；") })
  }

  return (
    <section className="mt-4 border-y border-[var(--line-strong)] bg-[#f8faf4] px-4 py-4" aria-labelledby="correction-title">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 id="correction-title" className="text-sm font-semibold">纠正模型结果</h3>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">只填写判断错误的维度。模型原始分数和响应会永久保留。</p>
        </div>
        <Button variant="ghost" size="icon" className="-mr-2 -mt-2" onClick={onCancel} aria-label="关闭纠错"><X /></Button>
      </div>

      <fieldset className="mt-5">
        <legend className="text-xs font-semibold">最终等级</legend>
        <div className="mt-2 grid grid-cols-5 gap-2">
          {["L1", "L2", "L3", "L4", "L5"].map((level) => (
            <button key={level} type="button" onClick={() => { setCorrectedLevel(level); setError("") }} className={`min-h-10 rounded-[4px] border text-sm font-semibold ${correctedLevel === level ? "border-[#7f991b] bg-primary" : "border-[var(--line-strong)] bg-white hover:bg-[#f3f5f0]"}`}>
              {level}{level === modelLevel && <span className="ml-1 text-[0.62rem] font-normal">模型</span>}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset className="mt-5">
        <legend className="text-xs font-semibold">哪个维度的分数不对，可多选</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(dimensionLabels).map(([key, label]) => {
            const active = selected.includes(key)
            return <button key={key} type="button" aria-pressed={active} onClick={() => toggleDimension(key)} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold transition-colors ${active ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white hover:bg-[#f3f5f0]"}`}>{active && <Check className="mr-1 inline" />} {label} · {dimensions[key]?.grade ?? "—"}</button>
          })}
          <button type="button" aria-pressed={scoringSelected} onClick={() => { setScoringSelected((value) => !value); setError("") }} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold ${scoringSelected ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white"}`}>评分规则/等级上限</button>
        </div>
      </fieldset>

      <div className="mt-4 space-y-4">
        {selectedCorrections.map(({ key, draft }) => (
          <section key={key} className="border border-[var(--line)] bg-white p-3">
            <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-semibold">{dimensionLabels[key]}</h4><span className="font-data text-xs text-[var(--muted)]">模型 {dimensions[key]?.grade ?? "—"}级</span></div>
            <p className="mt-3 text-xs font-semibold">人工分数</p>
            <div className="mt-2 grid grid-cols-5 gap-1.5">{[1, 2, 3, 4, 5].map((grade) => <button key={grade} type="button" onClick={() => updateDraft(key, { humanGrade: grade })} className={`min-h-9 rounded-[4px] border text-xs font-semibold ${draft.humanGrade === grade ? "border-[#7f991b] bg-primary" : "border-[var(--line)] bg-white"}`}>{grade}</button>)}</div>
            <p className="mt-3 text-xs font-semibold">为什么不对，至少选一项</p>
            <div className="mt-2 flex flex-wrap gap-1.5">{reasons.map(([value, label]) => <button key={value} type="button" aria-pressed={draft.reasons.includes(value)} onClick={() => toggleReason(key, value)} className={`rounded-[4px] border px-2.5 py-1.5 text-[0.72rem] ${draft.reasons.includes(value) ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line)] bg-white"}`}>{label}</button>)}</div>
            <Textarea className="mt-3 min-h-20" value={draft.note} onChange={(event) => updateDraft(key, { note: event.target.value })} placeholder="补充图片中可见、可定位的证据，可选" rows={2} />
          </section>
        ))}
      </div>

      {scoringSelected && <section className="mt-4 border border-[var(--line)] bg-white p-3"><p className="text-sm font-semibold">评分规则问题</p><div className="mt-3 flex flex-wrap gap-2">{scoringReasons.map(([value, label]) => <button key={value} type="button" onClick={() => setScoringReason(value)} className={`rounded-[4px] border px-3 py-2 text-xs ${scoringReason === value ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line)] bg-white"}`}>{label}</button>)}</div></section>}

      <label className="mt-4 block"><span className="mb-2 block text-xs font-semibold">整体补充说明，可选</span><Textarea value={overallNote} onChange={(event) => setOverallNote(event.target.value)} placeholder="例如：摄影氛围很好，但空间设计本身较普通" rows={2} /></label>
      {error && <p role="alert" className="mt-3 flex items-start gap-2 text-xs leading-5 text-[#8d2924]"><WarningCircle className="mt-0.5 shrink-0" />{error}</p>}
      <Button className="mt-4 w-full" onClick={submit} disabled={pending}>{pending ? "正在保存人工纠正" : "保存维度与最终结果"}</Button>
    </section>
  )
}
