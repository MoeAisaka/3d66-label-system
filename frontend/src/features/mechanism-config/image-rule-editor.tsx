import { Check, CheckCircle, FloppyDisk, Plus, Trash, WarningCircle } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"

import { isNewMechanismDraft } from "./types"
import { imageRuleViewDefaults } from "./image-rule-contract"
import type {
  Editable,
  JsonObject,
  MechanismEditorProps,
  ValidationErrorItem,
} from "./types"

type Json = JsonObject

type LevelScaleEntry = {
  level: "L1" | "L2" | "L3" | "L4" | "L5"
  enabled: boolean
  min_score?: number
  display_name: string
}

const LEVELS: LevelScaleEntry["level"][] = ["L1", "L2", "L3", "L4", "L5"]
const SUBCATEGORY_DIMENSIONS_FORMAT_VERSION = "subcategory-dimensions-v1"

function levelScaleForEditor(contract: Json): LevelScaleEntry[] {
  const configured = contract?.level_scale?.levels
  if (Array.isArray(configured)) {
    return LEVELS.map((level) => {
      const entry = configured.find((item: any) => item?.level === level)
      return {
        level,
        enabled: entry?.enabled !== false,
        min_score: typeof entry?.min_score === "number" ? entry.min_score : undefined,
        display_name: typeof entry?.display_name === "string" ? entry.display_name : level,
      }
    })
  }
  const thresholds = Array.isArray(contract?.level_thresholds) ? contract.level_thresholds : []
  return LEVELS.map((level) => {
    const threshold = thresholds.find((item: any) => item?.level === level)
    return {
      level,
      enabled: Boolean(threshold),
      min_score: typeof threshold?.min_score === "number" ? threshold.min_score : undefined,
      display_name: level,
    }
  })
}

const inputClass = "h-9 w-full rounded-[4px] border border-[var(--line-strong)] px-2 text-xs"
const numberClass = "h-9 w-24 rounded-[4px] border border-[var(--line-strong)] px-2 text-xs font-data"

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

function FieldCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-[var(--line)] bg-white">
      <div className="border-b border-[var(--line)] px-4 py-3">
        <h3 className="text-sm font-bold">{title}</h3>
      </div>
      <div className="px-4 py-4">{children}</div>
    </section>
  )
}

function IconButton({ onClick, title, danger }: { onClick: () => void; title: string; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-[4px] border border-[var(--line-strong)] bg-white [&_svg]:size-4 ${
        danger ? "text-[#8d2924] hover:bg-[#fdf3f2]" : "hover:bg-[#f8f9f6]"
      }`}
    >
      {danger ? <Trash /> : <Plus />}
    </button>
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

function RedlineEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const policy = draft.contract.redline_policy ?? {}
  const rules: any[] = policy.rules ?? []
  return (
    <FieldCard title="红线规则（命中直筛 L5，可增删 / 开关 / 改 match 词）">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={policy.enabled !== false}
            onChange={(e) => onPatch((n) => { n.contract.redline_policy.enabled = e.target.checked })}
          />
          <span className="font-semibold">启用红线阶段</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="font-semibold">命中等级</span>
          <select
            className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2"
            value={policy.hit_level ?? "L5"}
            onChange={(e) => onPatch((n) => { n.contract.redline_policy.hit_level = e.target.value })}
          >
            {["L1", "L2", "L3", "L4", "L5"].map((lv) => <option key={lv}>{lv}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="font-semibold">命中后分数上限</span>
          <input
            type="number"
            className={numberClass}
            value={policy.hit_score_cap ?? 49}
            onChange={(e) => onPatch((n) => { n.contract.redline_policy.hit_score_cap = Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="space-y-2">
        {rules.map((rule, idx) => (
          <div key={idx} className="grid gap-2 border border-[var(--line)] px-3 py-2 sm:grid-cols-[110px_1fr_1fr_auto] sm:items-center">
            <input
              className={inputClass}
              placeholder="规则标识"
              value={rule.key ?? ""}
              onChange={(e) => onPatch((n) => { n.contract.redline_policy.rules[idx].key = e.target.value })}
            />
            <input
              className={inputClass}
              placeholder="规则中文名"
              value={rule.label ?? ""}
              onChange={(e) => onPatch((n) => { n.contract.redline_policy.rules[idx].label = e.target.value })}
            />
            <input
              className={inputClass}
              placeholder="match_any（逗号分隔，如 是截图）"
              value={(rule.match_any ?? []).join(",")}
              onChange={(e) => onPatch((n) => {
                n.contract.redline_policy.rules[idx].match_any = e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
              })}
            />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1 text-[0.68rem]">
                <input
                  type="checkbox"
                  checked={rule.enabled !== false}
                  onChange={(e) => onPatch((n) => { n.contract.redline_policy.rules[idx].enabled = e.target.checked })}
                />
                启用
              </label>
              <IconButton danger title="删除规则" onClick={() => onPatch((n) => { n.contract.redline_policy.rules.splice(idx, 1) })} />
            </div>
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        onClick={() => onPatch((n) => {
          n.contract.redline_policy.rules.push({
            key: "", label: "", signal: "production_fields.reason",
            match_any: [], exemptions: [], enabled: true,
          })
        })}
      >
        <Plus />新增红线规则
      </Button>
      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
        信号源固定为 production_fields.reason（调用A 事实字段）；match_any 业务值由当前版本合同声明，平台只校验其结构。
      </p>
    </FieldCard>
  )
}

function TrackEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const tc = draft.contract.track_classification ?? {}
  const tracks: any[] = tc.tracks ?? []
  return (
    <FieldCard title="子类目赛道（分数基底 / 维度满分 / 赛道上限 / 默认兜底）">
      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="font-semibold">默认赛道</span>
        <select
          className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2"
          value={tc.default_track ?? ""}
          onChange={(e) => onPatch((n) => { n.contract.track_classification.default_track = e.target.value })}
        >
          {tracks.map((t) => <option key={t.key} value={t.key}>{t.key}</option>)}
        </select>
      </div>
      <div className="space-y-2">
        {tracks.map((track, idx) => (
          <div key={idx} className="grid gap-2 border border-[var(--line)] px-3 py-2 sm:grid-cols-[1fr_1fr_auto] sm:items-start">
            <div className="grid gap-2">
              <input className={inputClass} placeholder="赛道标识" value={track.key ?? ""} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].key = e.target.value })} />
              <input className={inputClass} placeholder="赛道中文名" value={track.label ?? ""} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].label = e.target.value })} />
            </div>
            <div className="flex flex-wrap gap-2">
              <label className="grid gap-0.5 text-[0.68rem]"><span>基础分</span>
                <input type="number" className={numberClass} value={track.base_score ?? 0} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].base_score = Number(e.target.value) })} />
              </label>
              <label className="grid gap-0.5 text-[0.68rem]"><span>维度满分</span>
                <input type="number" className={numberClass} value={track.dimension_max ?? 0} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].dimension_max = Number(e.target.value) })} />
              </label>
              <label className="grid gap-0.5 text-[0.68rem]"><span>赛道分数上限</span>
                <input type="number" className={numberClass} value={track.track_cap ?? 0} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].track_cap = Number(e.target.value) })} />
              </label>
            </div>
            <IconButton danger title="删除赛道" onClick={() => onPatch((n) => { n.contract.track_classification.tracks.splice(idx, 1) })} />
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        onClick={() => onPatch((n) => {
          n.contract.track_classification.tracks.push({
            key: "", label: "", base_score: 40, dimension_max: 30, track_cap: 70,
            dimension_schema_ref: { schema_key: "space_v13", version: "v13" },
          })
        })}
      >
        <Plus />新增赛道
      </Button>
      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
        约束：base_score + dimension_max ≤ track_cap ≤ 100；default_track 必须是已定义的 key。
      </p>
    </FieldCard>
  )
}

const LEGACY_MEDIA_LABELS: Record<string, string> = {
  real_photo: "实拍照片",
  render_3d: "3D 效果图",
  ai_image: "AI 图片",
  other: "其他媒介",
}

function MediaPenaltyEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const config = draft.contract?.common_modifiers?.media_type_penalty ?? {}
  const enabled = config.enabled !== false
  const penalties = config.penalties ?? {}
  const mediaEntries = Object.entries(penalties)
  return (
    <FieldCard title="媒介降权（可独立关闭）">
      <label className="mb-3 flex items-center gap-2 text-xs font-semibold">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => onPatch((next) => {
            next.contract.common_modifiers.media_type_penalty.enabled = event.target.checked
          })}
        />
        启用媒介降权
      </label>
      <div className={`grid gap-2 sm:grid-cols-2 lg:grid-cols-4 ${enabled ? "" : "opacity-45"}`}>
        {mediaEntries.map(([key, value]) => (
          <div key={key} className="grid gap-1 border border-[var(--line)] p-2 text-xs">
            <div className="flex items-center gap-2">
              <input
                className={`${inputClass} min-w-0 flex-1`}
                disabled={!enabled}
                aria-label={`${key}媒介名称`}
                value={key}
                onChange={(event) => onPatch((next) => {
                  const media = next.contract.common_modifiers.media_type_penalty
                  const nextKey = event.target.value.trim()
                  if (!nextKey || nextKey === key || Object.prototype.hasOwnProperty.call(media.penalties ?? {}, nextKey)) return
                  const nextPenalties = { ...(media.penalties ?? {}) }
                  delete nextPenalties[key]
                  nextPenalties[nextKey] = value
                  media.penalties = nextPenalties
                  if (media.baseline === key) media.baseline = nextKey
                  if (media.fallback === key) media.fallback = nextKey
                  if (media.aliases && typeof media.aliases === "object") {
                    for (const alias of Object.keys(media.aliases)) {
                      if (media.aliases[alias] === key) media.aliases[alias] = nextKey
                    }
                  }
                })}
              />
              <IconButton
                danger
                title={`删除媒介 ${key}`}
                onClick={() => onPatch((next) => {
                  const media = next.contract.common_modifiers.media_type_penalty
                  if (media.penalties) delete media.penalties[key]
                  if (media.baseline === key) media.baseline = Object.keys(media.penalties ?? {})[0] ?? ""
                  if (media.fallback === key) media.fallback = undefined
                  if (media.aliases && typeof media.aliases === "object") {
                    for (const alias of Object.keys(media.aliases)) {
                      if (media.aliases[alias] === key) delete media.aliases[alias]
                    }
                  }
                })}
              />
            </div>
            <span className="font-semibold">{LEGACY_MEDIA_LABELS[key] ?? "媒介扣分值"}</span>
            <input
              type="number"
              className={numberClass}
              disabled={!enabled}
              value={Number(value ?? 0)}
              onChange={(event) => onPatch((next) => {
                next.contract.common_modifiers.media_type_penalty.penalties[key] = Number(event.target.value)
              })}
            />
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => onPatch((next) => {
          const media = next.contract.common_modifiers.media_type_penalty
          media.penalties = { ...(media.penalties ?? {}), 新媒介: 0 }
          if (!media.baseline) media.baseline = "新媒介"
        })}
      >
        <Plus />新增媒介
      </Button>
      {!enabled && <p className="mt-2 text-[0.68rem] text-[var(--muted)]">关闭后聚合器跳过此节点，媒介扣分固定为 0。</p>}
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
                    <div className="text-[0.68rem] leading-5 text-[var(--muted)]">该维度按 0–100 内部分数计算，最终贡献受当前维度权重和上限共同约束。{duplicateRuleIds.length > 0 && <span className="ml-2 font-semibold text-[#8d2924]">规则 ID 重复：{duplicateRuleIds.join("、")}</span>}</div>
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
                      扣分规则（调用B逐条判定） · 总扣分上限 {Math.min(100, (dim.deduction_rules ?? []).reduce((sum: number, rule: Json) => sum + Number(rule.deduction || 0), 0))}
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
  return Array.isArray(dimension.deduction_rules) || "bonus_rules" in dimension || "dimension_score_cap" in dimension
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

function ClassificationMapEditor({
  draft,
  trackKeys,
  onPatch,
}: {
  draft: Editable
  trackKeys: string[]
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const map = draft.classification_map ?? {}
  const entries = Object.entries<string>(map.category_to_subcategory ?? {})
  return (
    <FieldCard title="分类映射（一级类目 → 子类目赛道）">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-2">
          <span className="font-semibold">最低分类置信度</span>
          <input
            type="number"
            step={0.05}
            min={0}
            max={1}
            className={numberClass}
            value={map.min_confidence ?? 0.6}
            onChange={(e) => onPatch((n) => { n.classification_map.min_confidence = Number(e.target.value) })}
          />
        </label>
        <label className="flex items-center gap-2">
          <span className="font-semibold">范围外兜底赛道 → </span>
          <select
            className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2"
            value={map.out_of_scope_subcategory ?? ""}
            onChange={(e) => onPatch((n) => { n.classification_map.out_of_scope_subcategory = e.target.value })}
          >
            <option value="">选择赛道…</option>
            {trackKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
      </div>
      <div className="space-y-2">
        {entries.map(([category, target], idx) => (
          <div key={idx} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-center">
            <input
              className={inputClass}
              placeholder="一级类目（如 建筑设计）"
              value={category}
              onChange={(e) => onPatch((n) => {
                const current = n.classification_map.category_to_subcategory ?? {}
                const next: Record<string, string> = {}
                Object.entries<string>(current).forEach(([c, t], i) => {
                  next[i === idx ? e.target.value : c] = t
                })
                n.classification_map.category_to_subcategory = next
              })}
            />
            <select
              className="h-9 rounded-[4px] border border-[var(--line-strong)] px-2 text-xs"
              value={target}
              onChange={(e) => onPatch((n) => { n.classification_map.category_to_subcategory[category] = e.target.value })}
            >
              <option value="">选择赛道…</option>
              {trackKeys.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <IconButton danger title="删除映射" onClick={() => onPatch((n) => { delete n.classification_map.category_to_subcategory[category] })} />
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        onClick={() => onPatch((n) => {
          const map = n.classification_map.category_to_subcategory ?? {}
          if (!("" in map)) map[""] = trackKeys[0] ?? ""
          n.classification_map.category_to_subcategory = map
        })}
      >
        <Plus />新增映射
      </Button>
      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
        每个映射目标与 out_of_scope 都必须是上方已定义的赛道 key，否则校验会拦下。
      </p>
    </FieldCard>
  )
}
