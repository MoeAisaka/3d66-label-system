import { useEffect, useMemo, useState } from "react"
import { ArrowCounterClockwise, Check, WarningCircle } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  calculateDimensionPreview,
  dimensionKeys as dimensionKeysForSchema,
  dimensionLabels as dimensionLabelsForSchema,
} from "@/lib/dimension-schema"
import {
  dimensionGradeOptions,
  type LevelThresholds,
} from "@/lib/level-thresholds"
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
  ["wrong_visible_attribute", "可见属性判断错误"],
  ["enum_mismatch", "枚举选择错误"],
  ["unsupported_inference", "存在无依据推断"],
  ["missing_evidence", "缺少可见证据"],
] as const

type Draft = { humanGrade: number; reasons: string[]; note: string }
type KeyFieldDraft = { rawValue: string; reasons: string[]; note: string }
type KeyFieldKind = "text" | "number" | "json"
const EMPTY_CORRECTIONS: ReviewCorrection[] = []
const keyFieldConfigs: Array<{
  path: string
  label: string
  kind: KeyFieldKind
  hint: string
}> = [
  { path: "production_fields.title", label: "专业标题", kind: "text", hint: "10字内" },
  { path: "production_fields.seotitle", label: "SEO标题", kind: "text", hint: "28字内" },
  { path: "production_fields.category", label: "一二级分类", kind: "text", hint: "一级分类，二级分类" },
  { path: "production_fields.style", label: "可见风格", kind: "text", hint: "无法判断时写“无法判断”" },
  { path: "production_fields.tags", label: "主要标签", kind: "json", hint: "至少4个字符串的 JSON 数组" },
  { path: "production_fields.cons", label: "缺点点评", kind: "text", hint: "只依据可见证据" },
  { path: "production_fields.design", label: "设计理念", kind: "text", hint: "无法判断时不得编造" },
  { path: "production_fields.score", label: "调用A初步分", kind: "number", hint: "0-100整数，不是最终等级" },
  { path: "production_fields.reason", label: "过滤原因", kind: "json", hint: "允许枚举的 JSON 数组" },
  { path: "production_fields.image_defects", label: "图片缺陷", kind: "text", hint: "仅空字符串或“有水印”" },
  { path: "production_fields.trait", label: "素材特征", kind: "text", hint: "AI图/实景照片/3D数字效果图/其它" },
  { path: "image_quality.quality_severity", label: "画质严重度", kind: "text", hint: "normal/slight/moderate/severe/unusable/uncertain" },
  { path: "media_form", label: "媒介形态明细", kind: "json", hint: "每项包含 status、confidence、evidence" },
]

function valueAtPath(source: Record<string, any>, path: string): unknown {
  return path.split(".").reduce<unknown>((value, key) => (
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)[key]
      : undefined
  ), source)
}

function editableValue(value: unknown, kind: KeyFieldKind): string {
  if (value === undefined || value === null) return ""
  if (kind === "json") return JSON.stringify(value, null, 2)
  return String(value)
}

function parsedValue(rawValue: string, kind: KeyFieldKind): unknown {
  if (kind === "json") return JSON.parse(rawValue)
  if (kind === "number") {
    const value = Number(rawValue)
    if (!Number.isInteger(value)) throw new Error("必须填写整数")
    return value
  }
  return rawValue.trim()
}

export function ReviewCorrectionForm({
  dimensions,
  precheck,
  dimensionSchema,
  scoring,
  pending,
  editable = true,
  initialCorrections = EMPTY_CORRECTIONS,
  initialNote = "",
  onSubmit,
}: {
  dimensions: Record<string, any>
  precheck: Record<string, any>
  dimensionSchema: EvaluationDimensionSchema
  scoring: Record<string, any>
  pending: boolean
  editable?: boolean
  initialCorrections?: ReviewCorrection[]
  initialNote?: string
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
  const [keyFieldDrafts, setKeyFieldDrafts] = useState<Record<string, KeyFieldDraft>>({})
  const [overallNote, setOverallNote] = useState("")
  const [error, setError] = useState("")
  const initialCorrectionsKey = useMemo(
    () => JSON.stringify(initialCorrections),
    [initialCorrections],
  )

  useEffect(() => {
    const nextDimensions: Record<string, Draft> = {}
    const nextKeyFields: Record<string, KeyFieldDraft> = {}
    for (const correction of initialCorrections) {
      const reasonsForCorrection = Array.isArray(correction.reason_codes)
        ? correction.reason_codes.map(String)
        : []
      const noteForCorrection = String(correction.note ?? "")
      if (correction.target_type === "dimension") {
        const grade = Number(correction.human_value)
        if (dimensionKeys.includes(correction.field_key) && Number.isInteger(grade) && grade >= 1 && grade <= 5) {
          nextDimensions[correction.field_key] = {
            humanGrade: grade,
            reasons: reasonsForCorrection,
            note: noteForCorrection,
          }
        }
      } else if (correction.target_type === "key_field") {
        const config = keyFieldConfigs.find((item) => item.path === correction.field_key)
        if (config) {
          nextKeyFields[correction.field_key] = {
            rawValue: editableValue(correction.human_value, config.kind),
            reasons: reasonsForCorrection,
            note: noteForCorrection,
          }
        }
      }
    }
    setDrafts(nextDimensions)
    setKeyFieldDrafts(nextKeyFields)
    setOverallNote(initialNote)
    setError("")
  }, [dimensionKeys, initialCorrectionsKey, initialNote])

  const initialDimensionCorrections = useMemo(
    () => new Map(
      initialCorrections
        .filter((correction) => correction.target_type === "dimension")
        .map((correction) => [correction.field_key, correction]),
    ),
    [initialCorrectionsKey],
  )
  const initialKeyFieldCorrections = useMemo(
    () => new Map(
      initialCorrections
        .filter((correction) => correction.target_type === "key_field")
        .map((correction) => [correction.field_key, correction]),
    ),
    [initialCorrectionsKey],
  )

  const changedKeys = useMemo(
    () =>
      dimensionKeys.filter((key) => {
        const draft = drafts[key]
        const initial = initialDimensionCorrections.get(key)
        const valueChanged = draft && draft.humanGrade !== Number(dimensions[key]?.grade || 0)
        const correctionChanged = draft && initial && (
          draft.humanGrade !== Number(initial.human_value)
          || JSON.stringify(draft.reasons) !== JSON.stringify(initial.reason_codes ?? [])
          || draft.note.trim() !== String(initial.note ?? "").trim()
        )
        return draft && (valueChanged || correctionChanged)
      }),
    [dimensionKeys, drafts, dimensions, initialDimensionCorrections],
  )
  const availableKeyFields = useMemo(
    () => keyFieldConfigs.filter((config) => valueAtPath(precheck, config.path) !== undefined),
    [precheck],
  )
  const changedKeyFields = useMemo(
    () => availableKeyFields.filter((config) => {
      const draft = keyFieldDrafts[config.path]
      const initial = initialKeyFieldCorrections.get(config.path)
      const valueChanged = draft && draft.rawValue !== editableValue(
        valueAtPath(precheck, config.path),
        config.kind,
      )
      const correctionChanged = draft && initial && (
        draft.rawValue !== editableValue(initial.human_value, config.kind)
        || JSON.stringify(draft.reasons) !== JSON.stringify(initial.reason_codes ?? [])
        || draft.note.trim() !== String(initial.note ?? "").trim()
      )
      return draft && (valueChanged || correctionChanged)
    }),
    [availableKeyFields, keyFieldDrafts, precheck, initialKeyFieldCorrections],
  )
  const v3LevelThresholds = useMemo<LevelThresholds | null>(() => {
    const thresholds = scoring?.v3_context?.contract?.aesthetic_foundation?.score_thresholds
    if (!Array.isArray(thresholds)) return null
    const entries = thresholds.flatMap((item: any) => (
      item
      && /^L[1-5]$/.test(String(item.level))
      && Number.isFinite(Number(item.min_score))
        ? [[String(item.level), Number(item.min_score)] as const]
        : []
    ))
    return entries.length ? Object.fromEntries(entries) : null
  }, [scoring])

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
      v3LevelThresholds,
    )
  }, [dimensionKeys, dimensionSchema, dimensions, drafts, scoring, v3LevelThresholds])

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

  function updateKeyFieldDraft(
    path: string,
    kind: KeyFieldKind,
    patch: Partial<KeyFieldDraft>,
  ) {
    setError("")
    setKeyFieldDrafts((current) => ({
      ...current,
      [path]: {
        ...(current[path] ?? {
          rawValue: editableValue(valueAtPath(precheck, path), kind),
          reasons: [],
          note: "",
        }),
        ...patch,
      },
    }))
  }

  function toggleKeyFieldReason(path: string, kind: KeyFieldKind, reason: string) {
    const current = keyFieldDrafts[path]?.reasons ?? []
    updateKeyFieldDraft(path, kind, {
      reasons: current.includes(reason)
        ? current.filter((item) => item !== reason)
        : [...current, reason],
    })
  }

  function submit() {
    if (!changedKeys.length && !changedKeyFields.length) {
      setError("请至少修改一个维度或生产字段")
      return
    }
    for (const key of changedKeys) {
      if (!drafts[key]?.reasons.length) {
        setError(`请为${dimensionLabels[key]}选择至少一个错误原因`)
        document.getElementById(`dimension-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" })
        return
      }
    }
    for (const config of changedKeyFields) {
      if (!keyFieldDrafts[config.path]?.reasons.length) {
        setError(`请为${config.label}选择至少一个错误原因`)
        document.getElementById(`key-field-${config.path}`)?.scrollIntoView({ behavior: "smooth", block: "center" })
        return
      }
    }
    let keyCorrections: ReviewCorrection[]
    try {
      keyCorrections = changedKeyFields.map((config) => ({
        target_type: "key_field",
        field_key: config.path,
        model_value: valueAtPath(precheck, config.path),
        human_value: parsedValue(keyFieldDrafts[config.path].rawValue, config.kind),
        reason_codes: keyFieldDrafts[config.path].reasons,
        note: keyFieldDrafts[config.path].note.trim(),
      }))
    } catch (parseError) {
      setError(parseError instanceof Error ? parseError.message : "生产字段格式无效")
      return
    }
    const dimensionCorrections: ReviewCorrection[] = changedKeys.map((key) => ({
      target_type: "dimension",
      field_key: key,
      model_value: Number(dimensions[key]?.grade || 0),
      human_value: drafts[key].humanGrade,
      reason_codes: drafts[key].reasons,
      note: drafts[key].note.trim(),
    }))
    const dimensionSummary = changedKeys
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
    const keyFieldSummary = changedKeyFields
      .map((config) => `${config.label}已人工修正`)
      .join("；")
    onSubmit({
      corrections: [...dimensionCorrections, ...keyCorrections],
      note: [dimensionSummary, keyFieldSummary, overallNote.trim()].filter(Boolean).join("；"),
    })
  }

  if ((!dimensionKeys.length || !preview) && !availableKeyFields.length) {
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
                        <p className="mt-1 text-[0.68rem] text-[var(--muted)]">维度质量分为 5 级最好、1 级最差；最终等级为 L1 最好、L5 最差。未修改则保持模型值。</p>
                      </div>
                      {changed && (
                        <Button type="button" variant="ghost" size="sm" onClick={() => resetDraft(key)}>
                          <ArrowCounterClockwise />撤销本维修改
                        </Button>
                      )}
                    </div>
                    <div className="mt-3 grid grid-cols-5 gap-1.5">
                    {dimensionGradeOptions.map(({ grade, label }) => (
                      <button
                          key={grade}
                          type="button"
                        aria-label={`${dimensionLabels[key]}人工评分${grade}级（${label}）`}
                          aria-pressed={humanGrade === grade}
                          onClick={() => updateDraft(key, { humanGrade: grade })}
                          className={`min-h-10 rounded-[4px] border text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#6f8614] ${
                            humanGrade === grade
                              ? "border-[#7f991b] bg-primary"
                              : "border-[var(--line-strong)] bg-white hover:bg-[#f3f5f0]"
                          }`}
                        >
                        {grade}级 · {label}
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

      {availableKeyFields.length > 0 && (
        <div className="border-b border-[var(--line)]">
          <div className="bg-[#fafbf8] px-5 py-4">
            <p className="text-sm font-semibold">生产消费字段</p>
            <p className="mt-1 text-xs text-[var(--muted)]">搜索推荐等下游直接消费；修改会进入人工真值与正式标签快照。</p>
          </div>
          {availableKeyFields.map((config) => {
            const modelValue = valueAtPath(precheck, config.path)
            const draft = keyFieldDrafts[config.path]
            const currentValue = draft?.rawValue ?? editableValue(modelValue, config.kind)
            const changed = changedKeyFields.some((item) => item.path === config.path)
            return (
              <details id={`key-field-${config.path}`} key={config.path} className={changed ? "bg-[#fbfdeb]" : "bg-white"}>
                <summary className="flex cursor-pointer items-center justify-between gap-3 border-t border-[var(--line)] px-5 py-4">
                  <div><p className="text-sm font-semibold">{config.label}</p><p className="mt-1 text-[0.68rem] text-[var(--muted)]">{config.hint}</p></div>
                  {changed ? <Badge tone="active">已修改</Badge> : <Badge>模型值</Badge>}
                </summary>
                <div className="px-5 pb-5">
                  <Textarea
                    value={currentValue}
                    disabled={!editable}
                    rows={config.kind === "json" ? 6 : 2}
                    onChange={(event) => updateKeyFieldDraft(config.path, config.kind, { rawValue: event.target.value })}
                  />
                  {editable && changed && (
                    <div className="mt-3 border-l-2 border-[#a2bd2a] pl-3">
                      <p className="text-xs font-semibold">纠偏原因（至少一项）</p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {reasons.slice(-4).map(([value, label]) => (
                          <button key={value} type="button" aria-pressed={draft?.reasons.includes(value)} onClick={() => toggleKeyFieldReason(config.path, config.kind, value)} className={`rounded-[4px] border px-2.5 py-1.5 text-[0.72rem] ${draft?.reasons.includes(value) ? "border-[#7f991b] bg-[#eff8c7]" : "border-[var(--line)] bg-white"}`}>
                            {label}
                          </button>
                        ))}
                      </div>
                      <Textarea className="mt-3 min-h-16 bg-white" value={draft?.note ?? ""} onChange={(event) => updateKeyFieldDraft(config.path, config.kind, { note: event.target.value })} placeholder="补充可见证据，可选" rows={2} />
                    </div>
                  )}
                </div>
              </details>
            )
          })}
        </div>
      )}

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

      {editable && (changedKeys.length > 0 || changedKeyFields.length > 0) && (
        <div className="sticky bottom-0 z-10 border-t border-[var(--line-strong)] bg-white/98 px-5 py-4 shadow-[0_-6px_18px_rgba(28,33,24,0.06)] backdrop-blur-sm">
          <div className="grid gap-3 sm:grid-cols-[auto_auto_minmax(0,1fr)] sm:items-center">
            <div>
              <p className="font-data text-2xl font-semibold">
                {preview?.score.toFixed(1) ?? scoring?.score ?? "—"}
              </p>
              <p className="text-[0.68rem] text-[var(--muted)]">自动重算总分</p>
            </div>
            <div className="border-l border-[var(--line)] pl-4">
              <p className="font-data text-2xl font-semibold">
                {preview?.level ?? scoring?.level ?? "—"}
              </p>
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
            已修改 {changedKeys.length} 个维度、{changedKeyFields.length} 个生产字段；最终等级只由服务端评分引擎计算。
          </p>
          {error && (
            <p role="alert" className="mt-2 flex items-start gap-2 text-xs leading-5 text-[#8d2924]">
              <WarningCircle className="mt-0.5 shrink-0" />
              {error}
            </p>
          )}
          <Button className="mt-3 w-full" onClick={submit} disabled={pending}>
            <Check weight="bold" />
            {pending ? "正在保存人工纠偏" : `提交 ${changedKeys.length + changedKeyFields.length} 处纠偏`}
          </Button>
        </div>
      )}
    </section>
  )
}
