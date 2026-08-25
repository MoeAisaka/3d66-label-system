/** 质量规则机制编辑器 —— 只管两件事：随手拍限分、硬伤例外名单。
 *
 * 严禁把其它机制搬进这个面板：
 *   分数阈值   → 等级刻度面板
 *   维度与权重 → 调用 B 维度面板
 *   红线策略   → 红线与人工复核面板
 *   锚点图片   → 锚点图机制面板
 *
 * 混在一起会让调试失去可归因性——改了限分却看不出是限分还是阈值在起作用。
 * inspiration_aesthetic_foundation 就是把锚图、阈值、维度、封顶揉一起的反面样本。
 */

import { useEffect, useState } from "react"
import type { ReactNode } from "react"
import { Plus, Warning } from "@phosphor-icons/react"

import {
  DEFECT_SOURCES,
  SNAPSHOT_LIMIT_LEVELS,
  appendDefectException,
  readQualityRules,
  qualityRulesIntruders,
  removeDefectException,
  setDefectExceptions,
  setQualityRulesEnabled,
  setSnapshotLimitDimensionCeilings,
  setSnapshotLimitEnabled,
  setSnapshotLimitKeywords,
  setSnapshotLimitMaxLevel,
  setSnapshotLimitMaxScore,
} from "./image-rule-contract"
import type {
  DefectExceptionView,
  DefectSource,
  SnapshotLimitLevel,
} from "./image-rule-contract"
import { FieldCard, IconButton, inputClass, numberClass } from "./mechanism-form-primitives"
import type { Editable } from "./types"

const DEFECT_SOURCE_LABELS: Record<DefectSource, string> = {
  image_defects: "画面硬伤",
  content_defects: "内容硬伤",
}

/**
 * 关键词列表输入：失焦时才提交。
 *
 * 若在 onChange 就切分提交，用户刚敲下分隔符时末项为空会被过滤掉，
 * 分隔符看起来「打不出来」。改成失焦提交可以避开这个输入体验问题。
 */
function KeywordListInput({
  value,
  onCommit,
  placeholder,
}: {
  value: string[]
  onCommit: (next: string[]) => void
  placeholder?: string
}) {
  const [text, setText] = useState(value.join("、"))
  const [focused, setFocused] = useState(false)

  useEffect(() => {
    if (!focused) setText(value.join("、"))
  }, [value, focused])

  return (
    <input
      className={inputClass}
      value={text}
      placeholder={placeholder}
      onFocus={() => setFocused(true)}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => {
        setFocused(false)
        onCommit(
          text
            .split(/[、,，]/)
            .map((item) => item.trim())
            .filter(Boolean),
        )
      }}
    />
  )
}

function SubSection({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="space-y-2 border border-[var(--line)] px-3 py-3">
      <div>
        <div className="text-[0.78rem] font-semibold">{title}</div>
        <div className="text-[0.68rem] leading-relaxed text-[var(--muted)]">{hint}</div>
      </div>
      {children}
    </div>
  )
}

export function QualityRulesEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const view = readQualityRules(draft.contract)
  const intruders = qualityRulesIntruders(draft.contract)
  const limit = view.snapshotLimit
  const capByLevel = limit?.maxLevel !== null && limit?.maxLevel !== undefined

  const patchExceptions = (next: DefectExceptionView[]) => {
    onPatch((draftNext) => {
      setDefectExceptions(draftNext.contract, next)
    })
  }

  const updateException = (index: number, patch: Partial<DefectExceptionView>) => {
    patchExceptions(
      view.defectExceptions.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    )
  }

  return (
    <FieldCard title="质量规则机制">
      <p className="text-[0.68rem] leading-relaxed text-[var(--muted)]">
        只配两件事：<b>随手拍限分</b>（识别到随手拍就把总分压下来）和
        <b>硬伤例外名单</b>（满足佐证条件时硬伤不降级）。分数阈值在<b>等级刻度</b>、
        维度与权重在<b>调用 B 维度</b>、红线在<b>红线与人工复核</b>、锚点图在
        <b>锚点图机制</b> —— 这里不重复设置，这样改了限分才归因得清是限分在起作用。
      </p>

      {intruders.length > 0 && (
        <div className="flex items-start gap-2 border border-[#e0b4b0] bg-[#fdf3f2] px-3 py-2 text-[0.72rem] text-[#8d2924]">
          <Warning className="mt-[2px] size-4 shrink-0" />
          <span>
            合同里的质量规则块混入了不属于它的机制：{intruders.join("、")}。
            保存会被后端隔离守卫拒绝，请把这些设置移回各自的面板。
          </span>
        </div>
      )}

      <label className="flex items-center gap-2 text-[0.78rem]">
        <input
          type="checkbox"
          checked={view.present && view.enabled}
          onChange={(event) =>
            onPatch((next) => {
              setQualityRulesEnabled(next.contract, event.target.checked)
            })
          }
        />
        <span className="font-semibold">启用质量规则机制</span>
        <span className="text-[0.68rem] text-[var(--muted)]">
          关闭后限分与豁免都不生效；合同不带本块时按历史基座规则跑
        </span>
      </label>

      {view.present && view.enabled && (
        <div className="space-y-3">
          <SubSection
            title="随手拍限分"
            hint="判定理由命中任一关键词时，把这张图的总分压到上限以内。关键词可自己加，不限于「是随手拍」。"
          >
            <label className="flex items-center gap-2 text-[0.72rem]">
              <input
                type="checkbox"
                checked={limit?.enabled ?? false}
                onChange={(event) =>
                  onPatch((next) => {
                    setSnapshotLimitEnabled(next.contract, event.target.checked)
                  })
                }
              />
              <span>启用随手拍限分</span>
            </label>

            {limit?.enabled && (
              <div className="space-y-2">
                <div className="space-y-1">
                  <div className="text-[0.68rem] text-[var(--muted)]">
                    当判定理由包含以下任一关键词（用「、」分隔，失焦生效）
                  </div>
                  <KeywordListInput
                    value={limit.whenReasonContains}
                    placeholder="是随手拍、手机快照"
                    onCommit={(keywords) =>
                      onPatch((next) => {
                        setSnapshotLimitKeywords(next.contract, keywords)
                      })
                    }
                  />
                  {limit.whenReasonContains.length === 0 && (
                    <div className="text-[0.68rem] text-[#8d2924]">
                      至少要配一个关键词，否则保存会被拒绝
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-4 text-[0.72rem]">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      checked={!capByLevel}
                      onChange={() =>
                        onPatch((next) => {
                          setSnapshotLimitMaxScore(next.contract, limit.maxScore ?? 59)
                        })
                      }
                    />
                    <span>按分数封顶</span>
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      checked={capByLevel}
                      onChange={() =>
                        onPatch((next) => {
                          setSnapshotLimitMaxLevel(next.contract, limit.maxLevel ?? "L4")
                        })
                      }
                    />
                    <span>按等级封顶</span>
                  </label>
                </div>

                {!capByLevel && (
                  <div className="flex items-center gap-2 text-[0.72rem]">
                    <span>最高只能得</span>
                    <input
                      className={numberClass}
                      type="number"
                      min={0}
                      max={100}
                      value={limit.maxScore ?? 59}
                      onChange={(event) =>
                        onPatch((next) => {
                          setSnapshotLimitMaxScore(
                            next.contract,
                            Number.parseInt(event.target.value, 10) || 0,
                          )
                        })
                      }
                    />
                    <span>分</span>
                    <span className="text-[0.68rem] text-[var(--muted)]">
                      压到低于 L3 门槛即等于强制降为 L4
                    </span>
                  </div>
                )}

                {capByLevel && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-[0.72rem]">
                      <span>最高只能到</span>
                      <select
                        className={numberClass}
                        value={limit.maxLevel ?? "L4"}
                        onChange={(event) =>
                          onPatch((next) => {
                            setSnapshotLimitMaxLevel(
                              next.contract,
                              event.target.value as SnapshotLimitLevel,
                            )
                          })
                        }
                      >
                        {SNAPSHOT_LIMIT_LEVELS.map((level) => (
                          <option key={level} value={level}>
                            {level}
                          </option>
                        ))}
                      </select>
                    </div>
                    <DimensionCeilingsEditor
                      ceilings={limit.dimensionCeilings}
                      onCommit={(ceilings) =>
                        onPatch((next) => {
                          setSnapshotLimitDimensionCeilings(next.contract, ceilings)
                        })
                      }
                    />
                  </div>
                )}
              </div>
            )}
          </SubSection>

          <SubSection
            title="硬伤例外名单"
            hint="命中硬伤但佐证符合、且指定维度达到档位要求时，这条硬伤不触发降级。条数不限，可以一条都不配。"
          >
            {view.defectExceptions.length === 0 && (
              <div className="text-[0.68rem] text-[var(--muted)]">还没有例外规则</div>
            )}

            <div className="space-y-3">
              {view.defectExceptions.map((exception, index) => (
                <div
                  key={index}
                  className="space-y-2 border border-[var(--line)] bg-[var(--surface-muted)] px-3 py-2"
                >
                  <div className="flex items-start gap-2">
                    <div className="flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-2 text-[0.72rem]">
                        <span className="w-16 shrink-0 text-[var(--muted)]">规则名</span>
                        <input
                          className={inputClass}
                          style={{ maxWidth: "16rem" }}
                          value={exception.name}
                          placeholder="便于运营辨认，如：品牌文字遮挡豁免"
                          onChange={(event) =>
                            updateException(index, { name: event.target.value })
                          }
                        />
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-[0.72rem]">
                        <span className="w-16 shrink-0 text-[var(--muted)]">豁免硬伤</span>
                        <input
                          className={inputClass}
                          style={{ maxWidth: "16rem" }}
                          value={exception.defect}
                          placeholder="硬伤标识，如：subject_obscuring_watermark"
                          onChange={(event) =>
                            updateException(index, { defect: event.target.value })
                          }
                        />
                        <select
                          className={numberClass}
                          value={exception.defectSource}
                          onChange={(event) =>
                            updateException(index, {
                              defectSource: event.target.value as DefectSource,
                            })
                          }
                        >
                          {DEFECT_SOURCES.map((source) => (
                            <option key={source} value={source}>
                              {DEFECT_SOURCE_LABELS[source]}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1 text-[0.72rem]">
                        <span className="text-[var(--muted)]">
                          当硬伤佐证包含以下任一关键词（用「、」分隔，失焦生效）
                        </span>
                        <KeywordListInput
                          value={exception.whenEvidenceContains}
                          placeholder="品牌文字、品牌字样"
                          onCommit={(keywords) =>
                            updateException(index, { whenEvidenceContains: keywords })
                          }
                        />
                      </div>
                      <DimensionRequirementsEditor
                        requirements={exception.requireDimensions}
                        onChange={(requirements) =>
                          updateException(index, { requireDimensions: requirements })
                        }
                      />
                    </div>
                    <IconButton
                      title="删除这条例外"
                      danger
                      onClick={() =>
                        onPatch((next) => {
                          removeDefectException(next.contract, index)
                        })
                      }
                    />
                  </div>
                </div>
              ))}
            </div>

            <button
              type="button"
              className="flex items-center gap-1 border border-[var(--line-strong)] px-2 py-1 text-[0.72rem]"
              onClick={() =>
                onPatch((next) => {
                  appendDefectException(next.contract)
                })
              }
            >
              <Plus className="size-3" />
              新增例外规则
            </button>
          </SubSection>
        </div>
      )}
    </FieldCard>
  )
}

/** 按等级封顶时的维度分上限（可选）。 */
function DimensionCeilingsEditor({
  ceilings,
  onCommit,
}: {
  ceilings: Record<string, number>
  onCommit: (next: Record<string, number>) => void
}) {
  const entries = Object.entries(ceilings)

  return (
    <div className="space-y-1">
      <div className="text-[0.68rem] text-[var(--muted)]">
        另外把这些维度的分数压到上限以内（可不配）
      </div>
      {entries.map(([dimension, limit], index) => (
        <div key={index} className="flex items-center gap-2 text-[0.72rem]">
          <input
            className={inputClass}
            style={{ maxWidth: "14rem" }}
            value={dimension}
            placeholder="维度标识"
            onChange={(event) => {
              const next: Record<string, number> = {}
              entries.forEach(([key, value], position) => {
                next[position === index ? event.target.value : key] = value
              })
              onCommit(next)
            }}
          />
          <span>最高</span>
          <input
            className={numberClass}
            type="number"
            min={1}
            max={5}
            value={limit}
            onChange={(event) =>
              onCommit({
                ...ceilings,
                [dimension]: Number.parseInt(event.target.value, 10) || 1,
              })
            }
          />
          <span>档</span>
          <IconButton
            title="移除这条维度上限"
            danger
            onClick={() => {
              const next = { ...ceilings }
              delete next[dimension]
              onCommit(next)
            }}
          />
        </div>
      ))}
      <button
        type="button"
        className="flex items-center gap-1 border border-[var(--line-strong)] px-2 py-1 text-[0.68rem]"
        onClick={() => onCommit({ ...ceilings, "": 3 })}
      >
        <Plus className="size-3" />
        添加维度上限
      </button>
    </div>
  )
}

/** 豁免生效所需的维度门槛，至少一条，避免无条件豁免。 */
function DimensionRequirementsEditor({
  requirements,
  onChange,
}: {
  requirements: DefectExceptionView["requireDimensions"]
  onChange: (next: DefectExceptionView["requireDimensions"]) => void
}) {
  return (
    <div className="space-y-1 text-[0.72rem]">
      <span className="text-[var(--muted)]">
        且以下维度都达到要求（至少配一条，否则等于无条件豁免）
      </span>
      {requirements.map((requirement, index) => (
        <div key={index} className="flex flex-wrap items-center gap-2">
          <input
            className={inputClass}
            style={{ maxWidth: "14rem" }}
            value={requirement.dimension}
            placeholder="维度标识，如：detail_completion"
            onChange={(event) =>
              onChange(
                requirements.map((item, position) =>
                  position === index ? { ...item, dimension: event.target.value } : item,
                ),
              )
            }
          />
          <span>不低于</span>
          <input
            className={numberClass}
            type="number"
            min={1}
            max={5}
            value={requirement.minGrade}
            onChange={(event) =>
              onChange(
                requirements.map((item, position) =>
                  position === index
                    ? { ...item, minGrade: Number.parseInt(event.target.value, 10) || 1 }
                    : item,
                ),
              )
            }
          />
          <span>档</span>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={requirement.noShortcomings}
              onChange={(event) =>
                onChange(
                  requirements.map((item, position) =>
                    position === index
                      ? { ...item, noShortcomings: event.target.checked }
                      : item,
                  ),
                )
              }
            />
            <span>且无缺点</span>
          </label>
          {requirements.length > 1 && (
            <IconButton
              title="移除这条维度门槛"
              danger
              onClick={() => onChange(requirements.filter((_, position) => position !== index))}
            />
          )}
        </div>
      ))}
      <button
        type="button"
        className="flex items-center gap-1 border border-[var(--line-strong)] px-2 py-1 text-[0.68rem]"
        onClick={() =>
          onChange([...requirements, { dimension: "", minGrade: 4, noShortcomings: true }])
        }
      >
        <Plus className="size-3" />
        添加维度门槛
      </button>
    </div>
  )
}
