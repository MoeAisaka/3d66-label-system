/** 图片规则子编辑器 —— 从 image-rule-editor.tsx 抽出的四个独立编辑面板。 */

import { Button } from "@/components/ui/button"
import { Plus } from "@phosphor-icons/react"
import type { Editable } from "./types"
import { FieldCard, IconButton, inputClass, numberClass } from "./mechanism-form-primitives"

export function RedlineEditor({
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

export function TrackEditor({
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

export const LEGACY_MEDIA_LABELS: Record<string, string> = {
  real_photo: "实拍照片",
  render_3d: "3D 效果图",
  ai_image: "AI 图片",
  other: "其他媒介",
}

export function MediaPenaltyEditor({
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

export function ClassificationMapEditor({
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
