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

const gradePoints: Record<number, number> = { 1: 20, 2: 45, 3: 65, 4: 82, 5: 95 }
const defaultWeights: Record<string, number> = {
  composition_viewpoint: 0.15,
  lighting_atmosphere: 0.12,
  color_material: 0.12,
  spatial_design_furnishing: 0.18,
  visual_hierarchy: 0.10,
  detail_completion: 0.10,
  inspiration_reference: 0.08,
  presentation_integrity: 0.15,
}

type Draft = { humanGrade: number; reasons: string[]; note: string }

function levelForScore(score: number) {
  if (score < 40) return "L1"
  if (score < 60) return "L2"
  if (score < 75) return "L3"
  if (score < 90) return "L4"
  return "L5"
}

export function ReviewCorrectionForm({
  dimensions,
  scoring,
  pending,
  onCancel,
  onSubmit,
}: {
  dimensions: Record<string, any>
  scoring: Record<string, any>
  pending: boolean
  onCancel: () => void
  onSubmit: (payload: { note: string; corrections: ReviewCorrection[] }) => void
}) {
  const dimensionKeys = Object.keys(dimensionLabels)
  const [activeKey, setActiveKey] = useState(dimensionKeys[0])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [overallNote, setOverallNote] = useState("")
  const [error, setError] = useState("")

  const activeDraft = drafts[activeKey] ?? {
    humanGrade: Number(dimensions[activeKey]?.grade || 3),
    reasons: [],
    note: "",
  }
  const changedKeys = useMemo(() => dimensionKeys.filter((key) => {
    const draft = drafts[key]
    return draft && draft.humanGrade !== Number(dimensions[key]?.grade || 0)
  }), [drafts, dimensions])
  const preview = useMemo(() => {
    let score = 0
    dimensionKeys.forEach((key) => {
      const grade = drafts[key]?.humanGrade ?? Number(dimensions[key]?.grade || 0)
      const weight = Number(scoring?.dimension_points?.[key]?.weight ?? defaultWeights[key])
      score += (gradePoints[grade] ?? 0) * weight
    })
    score = Math.round(score * 100) / 100
    let level = levelForScore(score)
    const caps = (scoring?.caps ?? []).map((item: any) => Number(String(item.cap || "").replace("L", ""))).filter(Boolean)
    if (caps.length) {
      const cap = Math.min(...caps)
      level = `L${Math.min(Number(level.replace("L", "")), cap)}`
      score = Math.min(score, { 1: 39, 2: 59, 3: 74, 4: 89 }[cap as 1 | 2 | 3 | 4] ?? score)
    }
    return { score, level }
  }, [dimensions, drafts, scoring])

  function updateDraft(key: string, patch: Partial<Draft>) {
    setError("")
    setDrafts((current) => ({
      ...current,
      [key]: {
        ...(current[key] ?? {
          humanGrade: Number(dimensions[key]?.grade || 3),
          reasons: [],
          note: "",
        }),
        ...patch,
      },
    }))
  }

  function toggleReason(reason: string) {
    const current = activeDraft.reasons
    updateDraft(activeKey, { reasons: current.includes(reason) ? current.filter((item) => item !== reason) : [...current, reason] })
  }

  function submit() {
    if (!changedKeys.length) {
      setError("请至少修改一个维度分数")
      return
    }
    for (const key of changedKeys) {
      if (!(drafts[key]?.reasons.length)) {
        setActiveKey(key)
        setError(`请为${dimensionLabels[key]}选择至少一个错误原因`)
        return
      }
    }
    const corrections: ReviewCorrection[] = changedKeys.map((key) => ({
      target_type: "dimension",
      field_key: key,
      model_value: Number(dimensions[key]?.grade || 0),
      human_value: drafts[key].humanGrade,
      reason_codes: drafts[key].reasons,
      note: drafts[key].note.trim(),
    }))
    const summary = changedKeys.map((key) => {
      const draft = drafts[key]
      const reasonText = draft.reasons.map((code) => reasons.find(([value]) => value === code)?.[1] || code).join("、")
      return `${dimensionLabels[key]}：${dimensions[key]?.grade ?? "—"}级改为${draft.humanGrade}级（${reasonText}）${draft.note.trim() ? `，${draft.note.trim()}` : ""}`
    }).join("；")
    onSubmit({ corrections, note: [summary, overallNote.trim()].filter(Boolean).join("；") })
  }

  return (
    <section className="mt-4 border-y border-[var(--line-strong)] bg-[#f8faf4] px-4 py-4" aria-labelledby="correction-title">
      <div className="flex items-start justify-between gap-4">
        <div><h3 id="correction-title" className="text-sm font-semibold">纠正模型结果</h3><p className="mt-1 text-xs leading-5 text-[var(--muted)]">每次只编辑一个维度；切换维度不会丢失已经填写的分数、原因和说明。</p></div>
        <Button variant="ghost" size="icon" className="-mr-2 -mt-2" onClick={onCancel} aria-label="关闭纠错"><X /></Button>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-px border border-[var(--line)] bg-[var(--line)]">
        <div className="bg-white px-4 py-3"><p className="text-xs text-[var(--muted)]">自动计算总分</p><p className="font-data mt-1 text-2xl font-semibold">{preview.score.toFixed(1)}</p></div>
        <div className="bg-white px-4 py-3"><p className="text-xs text-[var(--muted)]">自动计算等级</p><p className="font-data mt-1 text-2xl font-semibold">{preview.level}</p></div>
      </div>
      <p className="mt-2 text-[0.68rem] leading-5 text-[var(--muted)]">最终分数和等级不能手工修改；保存时由服务端评分引擎按维度权重和等级限制重新计算。</p>

      <fieldset className="mt-5">
        <legend className="text-xs font-semibold">选择当前要编辑的维度（单选）</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {dimensionKeys.map((key) => {
            const active = activeKey === key
            const changed = changedKeys.includes(key)
            return <button key={key} type="button" aria-pressed={active} onClick={() => { setActiveKey(key); setError("") }} className={`rounded-[4px] border px-3 py-2 text-xs font-semibold transition-colors ${active ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line-strong)] bg-white hover:bg-[#f3f5f0]"}`}>{changed && <Check className="mr-1 inline" />}{dimensionLabels[key]} · {drafts[key]?.humanGrade ?? dimensions[key]?.grade ?? "—"}</button>
          })}
        </div>
      </fieldset>

      <section className="mt-4 border border-[var(--line)] bg-white p-3">
        <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-semibold">{dimensionLabels[activeKey]}</h4><span className="font-data text-xs text-[var(--muted)]">模型 {dimensions[activeKey]?.grade ?? "—"}级</span></div>
        <p className="mt-3 text-xs font-semibold">人工分数</p>
        <div className="mt-2 grid grid-cols-5 gap-1.5">{[1, 2, 3, 4, 5].map((grade) => <button key={grade} type="button" onClick={() => updateDraft(activeKey, { humanGrade: grade })} className={`min-h-9 rounded-[4px] border text-xs font-semibold ${activeDraft.humanGrade === grade ? "border-[#7f991b] bg-primary" : "border-[var(--line)] bg-white"}`}>{grade}</button>)}</div>
        <p className="mt-3 text-xs font-semibold">为什么不对，至少选一项</p>
        <div className="mt-2 flex flex-wrap gap-1.5">{reasons.map(([value, label]) => <button key={value} type="button" aria-pressed={activeDraft.reasons.includes(value)} onClick={() => toggleReason(value)} className={`rounded-[4px] border px-2.5 py-1.5 text-[0.72rem] ${activeDraft.reasons.includes(value) ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line)] bg-white"}`}>{label}</button>)}</div>
        <Textarea className="mt-3 min-h-20" value={activeDraft.note} onChange={(event) => updateDraft(activeKey, { note: event.target.value })} placeholder="补充图片中可见、可定位的证据，可选" rows={2} />
      </section>

      <label className="mt-4 block"><span className="mb-2 block text-xs font-semibold">整体补充说明，可选</span><Textarea value={overallNote} onChange={(event) => setOverallNote(event.target.value)} placeholder="例如：摄影氛围很好，但空间设计本身较普通" rows={2} /></label>
      {error && <p role="alert" className="mt-3 flex items-start gap-2 text-xs leading-5 text-[#8d2924]"><WarningCircle className="mt-0.5 shrink-0" />{error}</p>}
      <Button className="mt-4 w-full" onClick={submit} disabled={pending}>{pending ? "正在保存人工纠正" : "保存并自动计算最终结果"}</Button>
    </section>
  )
}
