import { useMemo, useState } from "react"
import { ArrowCounterClockwise, Check, WarningCircle } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  calculateDimensionPreview,
  dimensionKeys as dimensionKeysForSchema,
  dimensionLabels as dimensionLabelsForSchema,
} from "@/lib/dimension-schema"
import type {
  EvaluationDimensionSchema,
  ReviewCorrection,
} from "@/lib/types"

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

type Draft = { humanGrade: number; reasons: string[]; note: string }

export function ReviewCorrectionForm({
  dimensions,
  dimensionSchema,
  scoring,
  pending,
  editable = true,
  onSubmit,
}: {
  dimensions: Record<string, any>
  dimensionSchema: EvaluationDimensionSchema
  scoring: Record<string, any>
  pending: boolean
  editable?: boolean
  onSubmit: (payload: { note: string; corrections: ReviewCorrection[] }) => void
}) {
  const dimensionKeys = useMemo(
    () => dimensionKeysForSchema(dimensionSchema),
    [dimensionSchema],
  )
  const dimensionLabels = useMemo(
    () => dimensionLabelsForSchema(dimensionSchema),
    [dimensionSchema],
  )
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [overallNote, setOverallNote] = useState("")
  const [error, setError] = useState("")

  const changedKeys = useMemo(
    () =>
      dimensionKeys.filter((key) => {
        const draft = drafts[key]
        return draft && draft.humanGrade !== Number(dimensions[key]?.grade || 0)
      }),
    [dimensionKeys, drafts, dimensions],
  )

  const preview = useMemo(() => {
    const grades = Object.fromEntries(
      dimensionKeys.map((key) => [
        key,
        drafts[key]?.humanGrade
          ?? Number(dimensions[key]?.grade || 0),
      ]),
    )
    return calculateDimensionPreview(
      dimensionSchema,
      grades,
      scoring?.caps ?? [],
    )
  }, [dimensionKeys, dimensionSchema, dimensions, drafts, scoring])

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

  function resetDraft(key: string) {
    setError("")
    setDrafts((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
  }

  function toggleReason(key: string, reason: string) {
    const current = drafts[key]?.reasons ?? []
    updateDraft(key, {
      reasons: current.includes(reason) ? current.filter((item) => item !== reason) : [...current, reason],
    })
  }

  function submit() {
    if (!changedKeys.length) {
      setError("请至少修改一个维度分数")
      return
    }
    for (const key of changedKeys) {
      if (!drafts[key]?.reasons.length) {
        setError(`请为${dimensionLabels[key]}选择至少一个错误原因`)
        document.getElementById(`dimension-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" })
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
    const summary = changedKeys
      .map((key) => {
        const draft = drafts[key]
        const reasonText = draft.reasons
          .map((code) => reasons.find(([value]) => value === code)?.[1] || code)
          .join("、")
        return `${dimensionLabels[key]}：${dimensions[key]?.grade ?? "—"}级改为${draft.humanGrade}级（${reasonText}）${
          draft.note.trim() ? `，${draft.note.trim()}` : ""
        }`
      })
      .join("；")
    onSubmit({ corrections, note: [summary, overallNote.trim()].filter(Boolean).join("；") })
  }

  if (!dimensionKeys.length || !preview) {
    return (
      <section
        aria-label="维度合同异常"
        className="border-y border-[#e4c7c3] bg-[#fff8f7] px-5 py-5"
      >
        <p className="flex items-center gap-2 text-sm font-semibold text-[#8d2924]">
          <WarningCircle />维度规则无法解析，已禁止逐维纠偏
        </p>
        <p className="mt-2 text-xs leading-5 text-[#74302b]">
          可以确认或退回整条结果，但不能在规则身份不明时修改维度分数。
          {dimensionSchema.error ? ` 原因：${dimensionSchema.error}` : ""}
        </p>
      </section>
    )
  }

  return (
    <section aria-label="维度证据与人工纠偏">
      <div className="divide-y divide-[var(--line)] border-b border-[var(--line)]">
        {dimensionKeys.map((key, index) => {
          const item = dimensions[key] ?? {}
          const modelGrade = Number(item.grade || 0)
          const draft = drafts[key]
          const humanGrade = draft?.humanGrade ?? modelGrade
          const changed = changedKeys.includes(key)
          return (
            <details
              id={`dimension-${key}`}
              key={key}
              className={`group scroll-mt-24 ${changed ? "bg-[#fbfdeb]" : "bg-white"}`}
            >
              <summary className="grid cursor-pointer list-none grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3 px-5 py-4 hover:bg-[#fafbf8]">
                <span className="font-data text-xs text-[var(--muted)]">{String(index + 1).padStart(2, "0")}</span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold">{dimensionLabels[key]}</p>
                    {changed && <Badge tone="active">模型 {modelGrade} → 人工 {humanGrade}</Badge>}
                  </div>
                  <div className="mt-2 flex h-1.5 gap-px">
                    {[1, 2, 3, 4, 5].map((step) => (
                      <span key={step} className={`flex-1 ${step <= humanGrade ? "bg-primary" : "bg-[#eef1eb]"}`} />
                    ))}
                  </div>
                </div>
                <span className="font-data min-w-14 text-right text-xl font-semibold">
                  {changed ? `${modelGrade}→${humanGrade}` : modelGrade || "—"}
                </span>
              </summary>
              <div className="bg-[#fbfcfa] px-5 pb-5 pt-1">
                <p className="text-xs font-semibold text-[var(--muted)]">视觉证据</p>
                <ul className="mt-2 space-y-2 text-sm leading-6">
                  {(item.evidence ?? []).map((text: string, evidenceIndex: number) => (
                    <li key={evidenceIndex} className="grid grid-cols-[12px_1fr] gap-2">
                      <span className="mt-[0.65rem] size-1.5 bg-primary" />
                      <span>{text}</span>
                    </li>
                  ))}
                </ul>
                {(item.defects ?? []).length > 0 && (
                  <>
                    <p className="mt-4 text-xs font-semibold text-[#8d2924]">明显缺陷</p>
                    <ul className="mt-2 space-y-1 text-sm leading-6 text-[#74302b]">
                      {item.defects.map((text: string, defectIndex: number) => (
                        <li key={defectIndex}>{text}</li>
                      ))}
                    </ul>
                  </>
                )}

                {editable && (
                  <div className="mt-4 border-t border-[var(--line)] pt-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-xs font-semibold">人工分数</p>
                        <p className="mt-1 text-[0.68rem] text-[var(--muted)]">直接在模型评分处修正；未修改则保持模型值。</p>
                      </div>
                      {changed && (
                        <Button type="button" variant="ghost" size="sm" onClick={() => resetDraft(key)}>
                          <ArrowCounterClockwise />撤销本维修改
                        </Button>
                      )}
                    </div>
                    <div className="mt-3 grid grid-cols-5 gap-1.5">
                      {[1, 2, 3, 4, 5].map((grade) => (
                        <button
                          key={grade}
                          type="button"
                          aria-label={`${dimensionLabels[key]}人工评分${grade}`}
                          aria-pressed={humanGrade === grade}
                          onClick={() => updateDraft(key, { humanGrade: grade })}
                          className={`min-h-10 rounded-[4px] border text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#6f8614] ${
                            humanGrade === grade
                              ? "border-[#7f991b] bg-primary"
                              : "border-[var(--line-strong)] bg-white hover:bg-[#f3f5f0]"
                          }`}
                        >
                          {grade}
                        </button>
                      ))}
                    </div>

                    {changed && (
                      <div className="mt-4 border-l-2 border-[#a2bd2a] pl-3">
                        <p className="text-xs font-semibold">纠偏原因（至少一项）</p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {reasons.map(([value, label]) => (
                            <button
                              key={value}
                              type="button"
                              aria-pressed={draft?.reasons.includes(value)}
                              onClick={() => toggleReason(key, value)}
                              className={`rounded-[4px] border px-2.5 py-1.5 text-[0.72rem] transition-colors ${
                                draft?.reasons.includes(value)
                                  ? "border-[#7f991b] bg-[#eff8c7]"
                                  : "border-[var(--line)] bg-white hover:bg-[#f3f5f0]"
                              }`}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                        <Textarea
                          className="mt-3 min-h-20 bg-white"
                          value={draft?.note ?? ""}
                          onChange={(event) => updateDraft(key, { note: event.target.value })}
                          placeholder="补充图片中可见、可定位的证据，可选"
                          rows={2}
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            </details>
          )
        })}
      </div>

      {(scoring?.caps?.length ?? 0) > 0 && (
        <div className="border-b border-[var(--line)] bg-[#fff9ef] px-5 py-4">
          <p className="flex items-center gap-2 text-sm font-semibold text-[#7d4308]">
            <WarningCircle />等级限制
          </p>
          {(scoring?.caps ?? []).map((cap: any, index: number) => (
            <p key={index} className="mt-2 text-xs leading-5 text-[#7d4308]">
              最高 {cap.cap}：{cap.reason}
            </p>
          ))}
        </div>
      )}

      {editable && changedKeys.length > 0 && (
        <div className="sticky bottom-0 z-10 border-t border-[var(--line-strong)] bg-white/98 px-5 py-4 shadow-[0_-6px_18px_rgba(28,33,24,0.06)] backdrop-blur-sm">
          <div className="grid gap-3 sm:grid-cols-[auto_auto_minmax(0,1fr)] sm:items-center">
            <div>
              <p className="font-data text-2xl font-semibold">{preview.score.toFixed(1)}</p>
              <p className="text-[0.68rem] text-[var(--muted)]">自动重算总分</p>
            </div>
            <div className="border-l border-[var(--line)] pl-4">
              <p className="font-data text-2xl font-semibold">{preview.level}</p>
              <p className="text-[0.68rem] text-[var(--muted)]">自动重算等级</p>
            </div>
            <div className="sm:pl-3">
              <Textarea
                className="min-h-16"
                value={overallNote}
                onChange={(event) => setOverallNote(event.target.value)}
                placeholder="整体补充说明，可选"
                rows={1}
              />
            </div>
          </div>
          <p className="mt-2 text-[0.68rem] leading-5 text-[var(--muted)]">
            已修改 {changedKeys.length} 个维度；最终结果由服务端评分引擎重算，不能手工填写。
          </p>
          {error && (
            <p role="alert" className="mt-2 flex items-start gap-2 text-xs leading-5 text-[#8d2924]">
              <WarningCircle className="mt-0.5 shrink-0" />
              {error}
            </p>
          )}
          <Button className="mt-3 w-full" onClick={submit} disabled={pending}>
            <Check weight="bold" />
            {pending ? "正在保存人工纠偏" : `提交 ${changedKeys.length} 处纠偏`}
          </Button>
        </div>
      )}
    </section>
  )
}
