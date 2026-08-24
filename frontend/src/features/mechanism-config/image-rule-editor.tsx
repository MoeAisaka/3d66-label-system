import { Check, CheckCircle, FloppyDisk, Plus, Trash, WarningCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { baselineRegressionApi } from "@/lib/api"

import { isNewMechanismDraft } from "./types"
import {
  applyImageRuleBinding,
  imageRuleBindingView,
  imageRuleViewDefaults,
  setAestheticFoundationEnabled,
} from "./image-rule-contract"
import type {
  Editable,
  JsonObject,
  MechanismEditorProps,
  ValidationErrorItem,
} from "./types"
import { FieldCard, IconButton, inputClass, numberClass } from "./mechanism-form-primitives"
import { levelScaleForEditor } from "./level-scale-model"
import type { LevelScaleEntry } from "./level-scale-model"
import { ClassificationMapEditor, MediaPenaltyEditor, RedlineEditor, TrackEditor } from "./image-rule-subeditors"

type Json = JsonObject

const SUBCATEGORY_DIMENSIONS_FORMAT_VERSION = "subcategory-dimensions-v1"

export function ImageRuleEditor({
  draft,
  busy,
  banner,
  errors,
  onPatch,
  onValidate,
  onCreateCandidate,
  runtimeRevision,
  selectedRevision,
}: MechanismEditorProps) {
  const isNew = isNewMechanismDraft(runtimeRevision, selectedRevision)
  return (
    <V3ConfigEditor
      draft={draft}
      isNew={isNew}
      busy={busy}
      banner={banner}
      errors={errors}
      foundationTemplate={
        selectedRevision?.contract?.aesthetic_foundation
        ?? runtimeRevision?.contract?.aesthetic_foundation
        ?? null
      }
      onDisplayName={(value) => onPatch((next) => { next.display_name = value })}
      onKey={(value) => onPatch((next) => { next.category_key = value })}
      onPatch={onPatch}
      onValidate={onValidate}
      onSave={onCreateCandidate}
    />
  )
}

function V3ConfigEditor({
  draft,
  isNew,
  busy,
  banner,
  errors,
  foundationTemplate,
  onDisplayName,
  onKey,
  onPatch,
  onValidate,
  onSave,
}: {
  draft: Editable
  isNew: boolean
  busy: boolean
  banner: string | null
  errors: ValidationErrorItem[]
  foundationTemplate: Json | null
  onDisplayName: (value: string) => void
  onKey: (value: string) => void
  onPatch: (mutator: (next: Editable) => void) => void
  onValidate: () => void
  onSave: () => void
}) {
  const tracks: any[] = draft.contract?.track_classification?.tracks ?? []
  const trackKeys = tracks.map((t) => t.key).filter(Boolean)
  const bannerIsError = banner != null && !banner.startsWith("校验通过") && !banner.startsWith("候选") && !banner.startsWith("初始草稿")

  return (
    <div className="space-y-5">
      {/* 工具条 */}
      <div className="flex flex-wrap items-center justify-between gap-3 border border-[var(--line)] bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className="font-bold">{isNew ? "新建等级规则" : `编辑 ${draft.category_key}`}</span>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onValidate} disabled={busy}>
            <Check />校验
          </Button>
          <Button size="sm" onClick={onSave} disabled={busy}>
            <FloppyDisk />创建候选等级规则版本
          </Button>
        </div>
      </div>

      {banner && (
        <div
          className={`flex items-start gap-2 border px-3 py-2 text-xs ${
            bannerIsError
              ? "border-[#e4b9b6] bg-[#fdf3f2] text-[#8d2924]"
              : "border-[#bdd8c7] bg-[#edf7f0] text-[#245b3b]"
          }`}
        >
          {bannerIsError ? <WarningCircle size={15} weight="fill" className="mt-0.5" /> : <CheckCircle size={15} weight="fill" className="mt-0.5" />}
          <span>{banner}</span>
        </div>
      )}

      {errors.length > 0 && (
        <div className="border border-[#e4b9b6] bg-[#fdf3f2] px-3 py-2 text-xs text-[#8d2924]">
          <p className="font-semibold">校验错误（{errors.length}）</p>
          <ul className="mt-1 space-y-1">
            {errors.map((err, i) => (
              <li key={i}>
                <span className="font-data font-semibold">{err.target}</span>
                {" · "}<span className="font-data">{err.code}</span>：{err.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 基本信息 */}
      <FieldCard title="基本信息">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-xs">
            <span className="font-semibold">类目标识</span>
            <input
              className={inputClass}
              value={draft.category_key}
              disabled={!isNew}
              placeholder="如 inspiration_image"
              onChange={(e) => onKey(e.target.value)}
            />
            {!isNew && <span className="text-[0.68rem] text-[var(--muted)]">key 保存后不可改</span>}
          </label>
          <label className="grid gap-1 text-xs">
            <span className="font-semibold">中文名称</span>
            <input
              className={inputClass}
              value={draft.display_name}
              onChange={(e) => onDisplayName(e.target.value)}
            />
          </label>
        </div>
      </FieldCard>

      <PromptBindingEditor
        draft={draft}
        foundationTemplate={foundationTemplate}
        onPatch={onPatch}
      />
      <RedlineEditor draft={draft} onPatch={onPatch} />
      <LevelScaleEditor
        draft={draft}
        onPatch={onPatch}
      />
      <TrackEditor draft={draft} onPatch={onPatch} />
      <MediaPenaltyEditor draft={draft} onPatch={onPatch} />
      <DimensionGroupsEditor draft={draft} trackKeys={trackKeys} onPatch={onPatch} />
      <ClassificationMapEditor draft={draft} trackKeys={trackKeys} onPatch={onPatch} />
    </div>
  )
}

function LevelScaleEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const levels = levelScaleForEditor(draft.contract)
  const enabled = levels.filter((entry) => entry.enabled)
  const validationErrors: string[] = []
  if (enabled.length === 0) validationErrors.push("至少保留一个启用档位")
  if (enabled.some((entry) => !Number.isInteger(entry.min_score) || (entry.min_score ?? -1) < 0 || (entry.min_score ?? 101) > 100)) {
    validationErrors.push("启用档位切点必须是 0-100 的整数")
  }
  for (let index = 0; index < enabled.length - 1; index += 1) {
    if ((enabled[index].min_score ?? -1) <= (enabled[index + 1].min_score ?? -1)) {
      validationErrors.push("切点必须随 L 序号增大而严格下降")
      break
    }
  }
  if (enabled.length > 0 && enabled[enabled.length - 1].min_score !== 0) {
    validationErrors.push(`${enabled[enabled.length - 1].level} 是当前最差档，切点必须为 0`)
  }
  const redlineLevel = draft.contract?.redline_policy?.hit_level
  if (typeof redlineLevel === "string" && !enabled.some((entry) => entry.level === redlineLevel)) {
    validationErrors.push(`红线命中档 ${redlineLevel} 已关闭`)
  }

  const commit = (mutator: (next: LevelScaleEntry[]) => void) => {
    onPatch((next) => {
      const entries = levelScaleForEditor(next.contract)
      mutator(entries)
      next.contract.level_scale = {
        version: "category-level-scale-v1",
        levels: entries.map((entry) => entry.enabled
          ? entry
          : { level: entry.level, enabled: false, display_name: entry.display_name }),
      }
      delete next.contract.level_thresholds
    })
  }

  return (
    <FieldCard title="等级档位（L1 最优，L 序号越大质量越差）">
      <div className="overflow-x-auto border-y border-[var(--line)]">
        <div className="grid min-w-[620px] grid-cols-[80px_90px_140px_minmax(180px,1fr)] bg-[#f6f8f3] px-3 py-2 text-[0.68rem] font-semibold text-[var(--muted)]">
          <span>档位</span><span>启用</span><span>最低美感分</span><span>展示名称</span>
        </div>
        {levels.map((entry, index) => (
          <div key={entry.level} className="grid min-w-[620px] grid-cols-[80px_90px_140px_minmax(180px,1fr)] items-center border-t border-[var(--line)] px-3 py-2 text-xs">
            <strong className="font-data">{entry.level}</strong>
            <input
              type="checkbox"
              checked={entry.enabled}
              aria-label={`${entry.level} 启用`}
              onChange={(event) => commit((next) => {
                next[index].enabled = event.target.checked
                next[index].min_score = event.target.checked ? (next[index].min_score ?? 0) : undefined
              })}
            />
            <input
              type="number"
              min={0}
              max={100}
              className={numberClass}
              disabled={!entry.enabled}
              value={entry.min_score ?? ""}
              onChange={(event) => commit((next) => { next[index].min_score = Number(event.target.value) })}
            />
            <input
              className={inputClass}
              maxLength={40}
              value={entry.display_name}
              onChange={(event) => commit((next) => { next[index].display_name = event.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-h-8 text-xs text-[#8d2924]">
          {validationErrors.map((message) => <p key={message}>{message}</p>)}
        </div>
        <p className="text-xs text-[var(--muted)]">等级档位会随整份等级规则创建候选版本。</p>
      </div>
    </FieldCard>
  )
}

function PromptBindingEditor({
  draft,
  foundationTemplate,
  onPatch,
}: {
  draft: Editable
  foundationTemplate: Json | null
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const binding = imageRuleBindingView(draft.contract)
  const categoryKey = draft.category_key.trim()
  const promptsQuery = useQuery({
    queryKey: ["mechanism-config", "prompts", categoryKey],
    queryFn: () => baselineRegressionApi.listPrompts(categoryKey),
    enabled: categoryKey.length > 0,
  })
  const prompts = promptsQuery.data?.items ?? []
  const optionsFor = (stage: "A" | "B") => {
    const versions = prompts
      .filter((item) => item.stage === stage && item.status !== "archived")
      .map((item) => item.version)
    return Array.from(new Set(versions)).sort()
  }
  const current = { A: binding.callAVersion, B: binding.callBVersion }
  const canRestoreFoundation = binding.foundationEnabled || foundationTemplate !== null

  return (
    <FieldCard title="A / B 调用绑定与美感前置基座">
      <p className="mb-3 text-[0.68rem] text-[var(--muted)]">
        这里声明的 A/B 版本就是这份修订会实际执行的版本。维度规则和权重是按某一对 A/B
        标定出来的，换绑等于换掉标定前提，改完请重新跑一轮回归再启用。
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {(["A", "B"] as const).map((stage) => {
          const options = optionsFor(stage)
          const value = current[stage]
          const missing = value.length > 0 && !options.includes(value)
          // 调用 A 在执行侧是必填（prompt_a_version: str），空着存下去这份修订永远
          // 对不上执行版本，只会在发起时被 prompt_bindings_mismatch 拒掉。调用 B
          // 允许为空，表示这条修订不走调用 B。
          const emptyLabel = stage === "A" ? "请选择调用 A 版本" : "不走调用 B"
          return (
            <label key={stage} className="grid gap-1 text-xs">
              <span className="font-semibold">调用 {stage} 版本</span>
              <select
                className={inputClass}
                value={value}
                onChange={(event) => onPatch((next) => {
                  applyImageRuleBinding(next.contract, stage, event.target.value)
                })}
              >
                <option value="">{emptyLabel}</option>
                {missing && <option value={value}>{value}（清单里没有）</option>}
                {options.map((version) => (
                  <option key={version} value={version}>{version}</option>
                ))}
              </select>
              {stage === "A" && value.length === 0 && (
                <span className="text-[0.68rem] text-[#8d2924]">
                  调用 A 必须绑定一个版本，留空的修订发起时会被直接拒单。
                </span>
              )}
              {promptsQuery.isError && (
                <span className="text-[0.68rem] text-[#8d2924]">
                  版本清单没取到，可手工核对后再改；此时下拉只有当前值。
                </span>
              )}
              {missing && !promptsQuery.isError && (
                <span className="text-[0.68rem] text-[#8d2924]">
                  当前绑定的版本不在该类目的 {stage} 清单里，可能已归档或属于别的类目。
                </span>
            )}
            </label>
          )
        })}
      </div>
      <label className="mt-4 flex items-start gap-2 text-xs font-semibold">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={binding.foundationEnabled}
          disabled={!canRestoreFoundation}
          onChange={(event) => onPatch((next) => {
            setAestheticFoundationEnabled(
              next.contract,
              event.target.checked,
              foundationTemplate,
            )
          })}
        />
        <span>
          启用美感前置基座（锚图赛道）
          <span className="mt-1 block font-normal text-[0.68rem] text-[var(--muted)]">
            {binding.foundationEnabled
              ? "关掉就从这份合同里删掉整个 aesthetic_foundation，锚图赛道随之停用。"
              : canRestoreFoundation
                ? "从所选修订恢复基座，恢复后 call_b_version 跟随上面的调用 B 版本。"
                : "所选修订里没有基座内容可恢复。基座含标定过的锚图与分档，界面无法凭空生成，请改从带基座的修订派生。"}
          </span>
        </span>
      </label>
    </FieldCard>
  )
}

function emptyGroup(): Json {
  return {
    group_weight: 0.5,
    schema_definition: {
      format_version: "dimension-schema-definition-v1",
      schema_key: "inspiration_specific",
      version: "v1",
      dimensions: [],
    },
  }
}

function DimensionGroupsEditor({
  draft,
  trackKeys,
  onPatch,
}: {
  draft: Editable
  trackKeys: string[]
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const dims = draft.subcategory_dimensions ?? {}
  const configuredKeys = Object.keys(dims)
  return (
    <FieldCard title="每子类目维度组（共性 + 特有，均可增删维度 / 调权重 / 置空）">
      <div className="space-y-4">
        {configuredKeys.length === 0 && (
          <p className="text-xs text-[var(--muted)]">尚无维度配置。为下方赛道补充配置后可编辑。</p>
        )}
        {configuredKeys.map((trackKey) => {
          const cfg = dims[trackKey] ?? {}
          return (
            <div key={trackKey} className="border border-[var(--line)] px-3 py-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-data text-xs font-semibold">{trackKey}</span>
                <label className="flex items-center gap-1 text-[0.68rem]">
                  维度满分
                  <input
                    type="number"
                    className={numberClass}
                    value={cfg.dimension_max ?? 0}
                    onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey].dimension_max = Number(e.target.value) })}
                  />
                </label>
              </div>
              {(["common_group", "specific_group"] as const).map((groupKey) => (
                <GroupEditor
                  key={groupKey}
                  trackKey={trackKey}
                  groupKey={groupKey}
                  group={cfg[groupKey]}
                  onPatch={onPatch}
                />
              ))}
            </div>
          )
        })}
      </div>
      {trackKeys.some((k) => !configuredKeys.includes(k)) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {trackKeys.filter((k) => !configuredKeys.includes(k)).map((k) => (
            <Button
              key={k}
              variant="secondary"
              size="sm"
              onClick={() => onPatch((n) => {
                n.subcategory_dimensions[k] = {
                  format_version: SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
                  sub_category_key: k,
                  dimension_max: 30,
                  common_group: null,
                  specific_group: null,
                }
              })}
            >
              <Plus />为 {k} 补维度配置
            </Button>
          ))}
        </div>
      )}
    </FieldCard>
  )
}

function placeholderRules(label: string): Json[] {
  return [
    { rule_id: "minor_defect", description: `${label}存在局部轻微缺陷`, deduction: 10, tags: ["占位"] },
    { rule_id: "obvious_defect", description: `${label}存在明显缺陷`, deduction: 25, tags: ["占位"] },
    { rule_id: "severe_defect", description: `${label}存在严重缺陷`, deduction: 50, tags: ["占位"] },
  ]
}

function GroupEditor({
  trackKey,
  groupKey,
  group,
  onPatch,
}: {
  trackKey: string
  groupKey: "common_group" | "specific_group"
  group: Json | null | undefined
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const label = groupKey === "common_group" ? "共性维度组" : "特有维度组"
  const dimensions: any[] = group?.schema_definition?.dimensions ?? []
  const enabled = group != null
  return (
    <div className="mt-2 rounded-[4px] bg-[#f8fbef] px-3 py-2">
      <div className="flex items-center justify-between text-xs">
        <label className="flex items-center gap-2 font-semibold">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onPatch((n) => {
              n.subcategory_dimensions[trackKey][groupKey] = e.target.checked ? emptyGroup() : null
            })}
          />
          {label}{!enabled && "（已置空）"}
        </label>
        {enabled && (
          <label className="flex items-center gap-1 text-[0.68rem]">
            组权重
            <input
              type="number"
              step={0.05}
              className="h-8 w-20 rounded-[4px] border border-[var(--line-strong)] px-2 font-data"
              value={group?.group_weight ?? 0}
              onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].group_weight = Number(e.target.value) })}
            />
          </label>
        )}
      </div>
      {enabled && (
        <>
          <div className="mt-2 space-y-2">
            {dimensions.map((dim, idx) => (
              <div key={idx} className="border border-[var(--line)] bg-white px-3 py-3">
                {isImageRuleDimension(dim) && (() => {
                  const defaults = imageRuleViewDefaults(dim)
                  const duplicateRuleIds = duplicateIds(defaults.deductionRules, defaults.bonusRules)
                  return <div className="mb-3 grid gap-2 border-b border-dashed border-[var(--line)] pb-3 sm:grid-cols-[180px_1fr] sm:items-end">
                    <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度分数上限</span>
                      <input type="number" min={0} max={100} step={1} className={numberClass} value={defaults.dimensionScoreCap} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].dimension_score_cap = Number(e.target.value) })} />
                    </label>
                    <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度累计扣分上限</span>
                      <input type="number" min={0} max={100} step={1} className={numberClass} value={defaults.dimensionDeductionCap} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].dimension_deduction_cap = Number(e.target.value) })} />
                    </label>
                    <div className="text-[0.68rem] leading-5 text-[var(--muted)]">分数上限限制最终维度得分；累计扣分上限限制本维度所有命中规则最多扣多少分。{duplicateRuleIds.length > 0 && <span className="ml-2 font-semibold text-[#8d2924]">规则 ID 重复：{duplicateRuleIds.join("、")}</span>}</div>
                  </div>
                })()}
                <div className="grid gap-2 sm:grid-cols-[1fr_1fr_110px_auto] sm:items-end">
                  <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度标识</span>
                    <input className={inputClass} placeholder="如 color_aesthetics" value={dim.key ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].key = e.target.value })} />
                  </label>
                  <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度中文名</span>
                    <input className={inputClass} placeholder="如 色彩美感" value={dim.label ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].label = e.target.value })} />
                  </label>
                  <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度权重</span>
                    <input type="number" step={0.05} className="h-9 w-full rounded-[4px] border border-[var(--line-strong)] px-2 text-xs font-data" value={dim.weight ?? 0} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].weight = Number(e.target.value) })} />
                  </label>
                  <IconButton danger title="删除维度" onClick={() => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions.splice(idx, 1) })} />
                </div>
                <div className="mt-3 border-t border-dashed border-[var(--line)] pt-2">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[0.68rem] font-semibold">
                      扣分规则（调用B逐条判定） · 累计扣分上限 {typeof dim.dimension_deduction_cap === "number" ? dim.dimension_deduction_cap : 100}
                    </span>
                    <Button variant="ghost" size="sm" onClick={() => onPatch((n) => {
                      const rules = n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules ??= []
                      rules.push({ rule_id: "", description: "", deduction: 10, tags: [] })
                    })}><Plus />新增规则</Button>
                  </div>
                  <div className="space-y-1">
                    {(dim.deduction_rules ?? []).map((rule: Json, ruleIdx: number) => (
                      <div key={ruleIdx} className="grid gap-2 sm:grid-cols-[160px_1fr_110px_1fr_auto] sm:items-end">
                        <label className="grid gap-1 text-[0.68rem]"><span>规则标识</span><input className={inputClass} value={rule.rule_id ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].rule_id = e.target.value })} /></label>
                        <label className="grid gap-1 text-[0.68rem]"><span>中文规则描述</span><input className={inputClass} value={rule.description ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].description = e.target.value })} /></label>
                        <label className="grid gap-1 text-[0.68rem]"><span>扣分值</span><input type="number" min={0.1} max={100} className={numberClass} value={rule.deduction ?? 0} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].deduction = Number(e.target.value) })} /></label>
                        <label className="grid gap-1 text-[0.68rem]"><span>标签（逗号分隔）</span><input className={inputClass} value={(rule.tags ?? []).join(",")} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].tags = e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
                        <IconButton danger title="删除扣分规则" onClick={() => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules.splice(ruleIdx, 1) })} />
                      </div>
                    ))}
                  </div>
                </div>
                {isImageRuleDimension(dim) && <BonusRuleEditor trackKey={trackKey} groupKey={groupKey} dimensionIndex={idx} dimension={dim} onPatch={onPatch} />}
              </div>
            ))}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => onPatch((n) => {
              n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions.push({
                key: "", label: "新维度", weight: 1,
                deduction_rules: placeholderRules("新维度"),
                bonus_rules: [],
                dimension_score_cap: 100,
                dimension_deduction_cap: 100,
                grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
              })
            })}
          >
            <Plus />新增维度
          </Button>
        </>
      )}
    </div>
  )
}

function isImageRuleDimension(dimension: Json): boolean {
  return Array.isArray(dimension.deduction_rules) || "bonus_rules" in dimension || "dimension_score_cap" in dimension || "dimension_deduction_cap" in dimension
}

function duplicateIds(deductionRules: Json[], bonusRules: Json[]): string[] {
  const seen = new Set<string>()
  const duplicates = new Set<string>()
  for (const rule of [...deductionRules, ...bonusRules]) {
    const id = typeof rule.rule_id === "string" ? rule.rule_id.trim() : ""
    if (!id) continue
    if (seen.has(id)) duplicates.add(id)
    seen.add(id)
  }
  return [...duplicates]
}

function BonusRuleEditor({
  trackKey,
  groupKey,
  dimensionIndex,
  dimension,
  onPatch,
}: {
  trackKey: string
  groupKey: "common_group" | "specific_group"
  dimensionIndex: number
  dimension: Json
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const rules: Json[] = Array.isArray(dimension.bonus_rules) ? dimension.bonus_rules : []
  return <div className="mt-3 border-t border-dashed border-[var(--line)] pt-2">
    <div className="mb-2 flex items-center justify-between">
      <span className="text-[0.68rem] font-semibold">加分规则（调用B逐条判定）</span>
      <Button variant="ghost" size="sm" onClick={() => onPatch((n) => {
        const target = n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[dimensionIndex]
        const next = target.bonus_rules ??= []
        next.push({ rule_id: "", description: "", bonus: 5, tags: [] })
      })}><Plus />新增加分规则</Button>
    </div>
    <div className="space-y-1">
      {rules.map((rule, ruleIdx) => (
        <div key={ruleIdx} className="grid gap-2 sm:grid-cols-[160px_1fr_110px_1fr_auto] sm:items-end">
          <label className="grid gap-1 text-[0.68rem]"><span>规则标识</span><input className={inputClass} value={rule.rule_id ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[dimensionIndex].bonus_rules[ruleIdx].rule_id = e.target.value })} /></label>
          <label className="grid gap-1 text-[0.68rem]"><span>中文规则描述</span><input className={inputClass} value={rule.description ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[dimensionIndex].bonus_rules[ruleIdx].description = e.target.value })} /></label>
          <label className="grid gap-1 text-[0.68rem]"><span>加分值</span><input type="number" min={0.1} max={100} className={numberClass} value={rule.bonus ?? 0} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[dimensionIndex].bonus_rules[ruleIdx].bonus = Number(e.target.value) })} /></label>
          <label className="grid gap-1 text-[0.68rem]"><span>标签（逗号分隔）</span><input className={inputClass} value={(rule.tags ?? []).join(",")} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[dimensionIndex].bonus_rules[ruleIdx].tags = e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
          <IconButton danger title="删除加分规则" onClick={() => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[dimensionIndex].bonus_rules.splice(ruleIdx, 1) })} />
        </div>
      ))}
    </div>
  </div>
}
