import * as Dialog from "@radix-ui/react-dialog"
import { X } from "@phosphor-icons/react"
import { useEffect, useMemo, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type { RowCorrectionTarget } from "./detail-model"

/**
 * 单条信息的纠偏弹窗。
 *
 * 运营在这里选/填正确结果并写明理由。理由分两部分：结构化归因码用于纠偏分析聚合，
 * 自由文本补充具体判断依据——只有自由文本的话，一万条纠偏也统计不出「哪一层最常
 * 犯哪类错」。
 */

/** 归因码与 review-correction-form 保持同一套，保证纠偏分析口径一致 */
const REASON_CODES: ReadonlyArray<readonly [string, string]> = [
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
]

export type RowCorrectionSubmit = {
  nodeType: RowCorrectionTarget["nodeType"]
  nodePath: string
  oldValue: unknown
  newValue: unknown
  reason: string
  reasonCodes: string[]
}

/** 把服务端存的原始值转成输入框里的初始文本 */
function initialText(target: RowCorrectionTarget): string {
  const raw = target.currentValue
  if (raw === null || raw === undefined) return ""
  if (Array.isArray(raw)) {
    return raw.map((item) => String(item ?? "")).filter(Boolean).join("、")
  }
  if (typeof raw === "object") return ""
  return String(raw)
}

function initialSelection(target: RowCorrectionTarget): string[] {
  const raw = target.currentValue
  if (Array.isArray(raw)) return raw.map((item) => String(item ?? "")).filter(Boolean)
  if (typeof raw === "string" && raw.trim()) return [raw.trim()]
  return []
}

/** 把界面输入折算回服务端期望的值形态；返回 null 表示输入不合法 */
function parseValue(
  target: RowCorrectionTarget,
  text: string,
  selection: string[],
): { ok: true; value: unknown } | { ok: false; message: string } {
  switch (target.valueKind) {
    case "integer": {
      const trimmed = text.trim()
      if (!trimmed) return { ok: false, message: "请填写一个整数" }
      if (!/^-?\d+$/.test(trimmed)) {
        return { ok: false, message: "必须是整数，不能带小数点或其它字符" }
      }
      const parsed = Number(trimmed)
      const min = target.minimum ?? Number.NEGATIVE_INFINITY
      const max = target.maximum ?? Number.POSITIVE_INFINITY
      if (parsed < min || parsed > max) {
        return {
          ok: false,
          message: `必须在 ${target.minimum ?? "-∞"} 到 ${target.maximum ?? "∞"} 之间`,
        }
      }
      return { ok: true, value: parsed }
    }
    case "enum": {
      if (!selection.length) return { ok: false, message: "请选择一项" }
      return { ok: true, value: selection[0] }
    }
    case "multi_enum":
      // 允许空数组：清空「过滤原因」正是把误判的红线撤掉。
      return { ok: true, value: selection }
    case "string_list": {
      const items = text
        .split(/[，,、\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
      return { ok: true, value: items }
    }
    default: {
      const trimmed = text.trim()
      if (!trimmed) return { ok: false, message: "请填写正确结果" }
      return { ok: true, value: trimmed }
    }
  }
}

export function RowCorrectionDialog({
  open,
  onOpenChange,
  rowLabel,
  modelValue,
  target,
  submitting,
  errorMessage,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  rowLabel: string
  /** 模型的原判断，展示用；提交时用 target.currentValue 做并发校验 */
  modelValue: string
  target: RowCorrectionTarget | null
  submitting?: boolean
  errorMessage?: string | null
  onSubmit: (payload: RowCorrectionSubmit) => void
}) {
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const wasOpenRef = useRef(false)
  const [text, setText] = useState("")
  const [selection, setSelection] = useState<string[]>([])
  const [reasonCodes, setReasonCodes] = useState<string[]>([])
  const [note, setNote] = useState("")
  const [localError, setLocalError] = useState<string | null>(null)

  if (open && !wasOpenRef.current && typeof document !== "undefined") {
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
  }
  wasOpenRef.current = open

  // 每次打开都从模型原值重置，避免把上一条素材的输入带到下一条。
  useEffect(() => {
    if (!open || !target) return
    setText(initialText(target))
    setSelection(initialSelection(target))
    setReasonCodes([])
    setNote("")
    setLocalError(null)
  }, [open, target])

  const options = useMemo(() => target?.options ?? [], [target])

  function toggle(list: string[], value: string): string[] {
    return list.includes(value)
      ? list.filter((item) => item !== value)
      : [...list, value]
  }

  function handleSubmit() {
    if (!target) return
    const parsed = parseValue(target, text, selection)
    if (!parsed.ok) {
      setLocalError(parsed.message)
      return
    }
    const trimmedNote = note.trim()
    if (!reasonCodes.length && !trimmedNote) {
      setLocalError("请至少选一个纠偏理由，或写明具体判断依据")
      return
    }
    setLocalError(null)
    onSubmit({
      nodeType: target.nodeType,
      nodePath: target.nodePath,
      oldValue: target.currentValue,
      newValue: parsed.value,
      // 理由文本是纠偏依据，也会作为新值的证据存入结果。
      reason: trimmedNote
        || reasonCodes
          .map((code) => REASON_CODES.find(([key]) => key === code)?.[1] ?? code)
          .join("；"),
      reasonCodes,
    })
  }

  const message = errorMessage ?? localError

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/20" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100vh-2rem)] w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col border border-[var(--line-strong)] bg-white shadow-2xl focus:outline-none"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            returnFocusRef.current?.focus()
          }}
          data-testid="row-correction-dialog"
        >
          <div className="flex items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-4">
            <div>
              <Dialog.Title className="font-editorial text-xl font-bold">
                纠偏：{rowLabel}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-[0.68rem] leading-5 text-[var(--muted)]">
                {target?.recomputes
                  ? "保存后服务端会按冻结规则重算分数与等级。"
                  : "保存后只更新该字段，不触发分数重算。"}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button variant="secondary" size="icon" aria-label="关闭纠偏弹窗">
                <X size={18} />
              </Button>
            </Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
            <div className="border border-[var(--line)] bg-[#fafbf8] px-3 py-2">
              <p className="font-data text-[0.68rem] text-[var(--muted)]">模型判断</p>
              <p className="mt-0.5 text-sm">{modelValue}</p>
            </div>

            <div>
              <label className="text-xs font-bold" htmlFor="row-correction-value">
                正确结果
              </label>
              {target?.hint && (
                <p className="mt-0.5 text-[0.68rem] text-[var(--muted)]">{target.hint}</p>
              )}
              <div className="mt-2">
                {target?.valueKind === "enum" || target?.valueKind === "multi_enum" ? (
                  <div className="flex flex-wrap gap-1.5">
                    {options.length === 0 && (
                      <p className="text-[0.68rem] text-[var(--muted)]">
                        合同未下发候选项，请联系机制侧补充。
                      </p>
                    )}
                    {options.map((option) => {
                      const active = selection.includes(option.value)
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() =>
                            setSelection(
                              target.valueKind === "enum"
                                ? active ? [] : [option.value]
                                : toggle(selection, option.value),
                            )
                          }
                          aria-pressed={active}
                          className={`min-h-9 rounded-[4px] border px-3 text-xs font-semibold transition-colors ${
                            active
                              ? "border-[#7f991b] bg-[#f0f8c8] text-[#263000]"
                              : "border-[var(--line-strong)] bg-white hover:bg-[#fafbf8]"
                          }`}
                        >
                          {option.label}
                        </button>
                      )
                    })}
                  </div>
                ) : target?.valueKind === "multiline" ? (
                  <Textarea
                    id="row-correction-value"
                    rows={3}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                  />
                ) : (
                  <Input
                    id="row-correction-value"
                    value={text}
                    inputMode={target?.valueKind === "integer" ? "numeric" : undefined}
                    onChange={(event) => setText(event.target.value)}
                  />
                )}
              </div>
              {target?.valueKind === "string_list" && (
                <p className="mt-1 text-[0.68rem] text-[var(--muted)]">
                  多个值用中文逗号、英文逗号或换行分隔。
                </p>
              )}
            </div>

            <div>
              <p className="text-xs font-bold">纠偏理由</p>
              <p className="mt-0.5 text-[0.68rem] text-[var(--muted)]">
                归因码用于纠偏分析统计，可多选。
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {REASON_CODES.map(([code, label]) => {
                  const active = reasonCodes.includes(code)
                  return (
                    <button
                      key={code}
                      type="button"
                      onClick={() => setReasonCodes(toggle(reasonCodes, code))}
                      aria-pressed={active}
                      className={`min-h-8 rounded-[4px] border px-2.5 text-[0.68rem] font-semibold transition-colors ${
                        active
                          ? "border-[#7f991b] bg-[#f0f8c8] text-[#263000]"
                          : "border-[var(--line-strong)] bg-white hover:bg-[#fafbf8]"
                      }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            </div>

            <div>
              <label className="text-xs font-bold" htmlFor="row-correction-note">
                判断依据
              </label>
              <p className="mt-0.5 text-[0.68rem] text-[var(--muted)]">
                写明画面上的可见证据；这段会作为人工结论的证据留存。
              </p>
              <Textarea
                id="row-correction-note"
                rows={3}
                className="mt-2"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </div>

            {message && (
              <p
                role="alert"
                className="border border-[#d9534f] bg-[#fdf2f2] px-3 py-2 text-xs text-[#8a2b28]"
              >
                {message}
              </p>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-[var(--line)] px-5 py-3">
            <Dialog.Close asChild>
              <Button variant="secondary">取消</Button>
            </Dialog.Close>
            <Button onClick={handleSubmit} disabled={submitting || !target}>
              {submitting ? "保存中…" : "保存纠偏"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export { REASON_CODES, parseValue }
