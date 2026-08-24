import type { Editable, JsonObject } from "./types"

type Json = JsonObject

export type ImageRuleViewDefaults = {
  dimensionScoreCap: number
  dimensionDeductionCap: number
  deductionRules: Json[]
  bonusRules: Json[]
}

function isRecord(value: unknown): value is Json {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

/* ------------------------------------------------------------------ *
 * 锚点图机制（anchor-mechanism-v1）
 *
 * 唯一职责：哪些图片代表哪个等级。严禁混入其它机制——
 *   分数阈值   → 合同顶层 level_scale
 *   维度与权重 → Call B 的 dimensions
 *   红线与封顶 → Call A 与顶层 redline_policy
 *
 * 与 aesthetic_foundation 的关键差异：基座含标定过的维度与分档，界面凭空
 * 造不出来，只能从既有修订恢复（见 setAestheticFoundationEnabled 的
 * template 参数）。锚点机制只有图片和等级，所以**不需要 template**，运营
 * 可以从零配起——这正是拆分要换来的能力。
 * ------------------------------------------------------------------ */

export const ANCHOR_MECHANISM_KEY = "anchor_mechanism"
export const ANCHOR_MECHANISM_SPEC_VERSION = "anchor-mechanism-v1"
export const ANCHOR_LEVELS = ["L1", "L2", "L3", "L4", "L5"] as const
export const ANCHOR_MIME_TYPES = ["image/jpeg", "image/png"] as const
export const MAX_ANCHOR_IMAGES_CEILING = 20
const DEFAULT_MAX_ANCHOR_IMAGES = ANCHOR_LEVELS.length

export type AnchorLevel = (typeof ANCHOR_LEVELS)[number]

export type AnchorMechanismEntry = {
  level: AnchorLevel
  assetId: number
  mimeType: string
  sha256: string
  note?: string
}

export type AnchorMechanismView = {
  present: boolean
  enabled: boolean
  maxAnchorImages: number
  anchors: AnchorMechanismEntry[]
  levelsCovered: AnchorLevel[]
}

/** 与后端 FOREIGN_MECHANISM_KEYS 对齐：界面永远不该往锚点块写这些键。 */
const FOREIGN_MECHANISM_KEYS = new Set([
  "score_thresholds",
  "level_thresholds",
  "thresholds",
  "level_scale",
  "dimension_keys",
  "dimensions",
  "dimension_scoring_mode",
  "weights",
  "redline_policy",
  "redlines",
  "hard_defect_exemptions",
  "hard_defect_cap",
  "score_cap",
  "casual_snapshot_soft_cap",
  "quality_rules",
  "boundary_policy",
  "fallback_policy",
  "floor_to_lower_band",
])

/** 前端侧隔离守卫，命中即视为块非法（与后端同判据，避免存脏数据）。 */
export function anchorMechanismIntruders(block: unknown): string[] {
  if (!isRecord(block)) return []
  return Object.keys(block)
    .filter((key) => FOREIGN_MECHANISM_KEYS.has(key))
    .sort()
}

const SHA256_RE = /^[0-9a-f]{64}$/

function isAnchorLevel(value: unknown): value is AnchorLevel {
  return typeof value === "string" && (ANCHOR_LEVELS as readonly string[]).includes(value)
}

function readAnchorEntry(raw: unknown): AnchorMechanismEntry | null {
  if (!isRecord(raw)) return null
  const level = raw.level
  const assetId = raw.asset_id
  const mimeType = raw.mime_type
  const sha256 = raw.sha256
  if (!isAnchorLevel(level)) return null
  if (typeof assetId !== "number" || !Number.isInteger(assetId) || assetId < 1) return null
  if (typeof mimeType !== "string" || !(ANCHOR_MIME_TYPES as readonly string[]).includes(mimeType)) {
    return null
  }
  if (typeof sha256 !== "string" || !SHA256_RE.test(sha256)) return null
  const entry: AnchorMechanismEntry = { level, assetId, mimeType, sha256 }
  if (typeof raw.note === "string" && raw.note.trim()) entry.note = raw.note.trim()
  return entry
}

function sortAnchors(anchors: AnchorMechanismEntry[]): AnchorMechanismEntry[] {
  const rank = new Map<AnchorLevel, number>(ANCHOR_LEVELS.map((l, i) => [l, i]))
  return [...anchors].sort(
    (a, b) => (rank.get(a.level)! - rank.get(b.level)!) || (a.assetId - b.assetId),
  )
}

/** 读出锚点机制的界面视图。块缺失时 present=false，其余字段给安全默认值。 */
export function readAnchorMechanism(contract: unknown): AnchorMechanismView {
  const absent: AnchorMechanismView = {
    present: false,
    enabled: false,
    maxAnchorImages: DEFAULT_MAX_ANCHOR_IMAGES,
    anchors: [],
    levelsCovered: [],
  }
  if (!isRecord(contract)) return absent
  const block = contract[ANCHOR_MECHANISM_KEY]
  if (!isRecord(block)) return absent

  const rawAnchors = Array.isArray(block.anchors) ? block.anchors : []
  const anchors = sortAnchors(
    rawAnchors.map(readAnchorEntry).filter((e): e is AnchorMechanismEntry => e !== null),
  )
  const rawMax = block.max_anchor_images
  const maxAnchorImages =
    typeof rawMax === "number" && Number.isInteger(rawMax) && rawMax >= 1
      ? Math.min(rawMax, MAX_ANCHOR_IMAGES_CEILING)
      : DEFAULT_MAX_ANCHOR_IMAGES
  const covered = new Set(anchors.map((a) => a.level))

  return {
    present: true,
    enabled: block.enabled !== false,
    maxAnchorImages,
    anchors,
    levelsCovered: ANCHOR_LEVELS.filter((l) => covered.has(l)),
  }
}

function writeAnchorMechanism(contract: Json, view: AnchorMechanismView): void {
  contract[ANCHOR_MECHANISM_KEY] = {
    spec_version: ANCHOR_MECHANISM_SPEC_VERSION,
    enabled: view.enabled,
    max_anchor_images: view.maxAnchorImages,
    anchors: sortAnchors(view.anchors).map((entry) => {
      const out: Json = {
        level: entry.level,
        asset_id: entry.assetId,
        mime_type: entry.mimeType,
        sha256: entry.sha256,
      }
      if (entry.note) out.note = entry.note
      return out
    }),
  }
}

/**
 * 开关锚点图机制。关闭即移除整块；开启时若原本没有块就**新建空块**——
 * 不需要 template，这是与基座最重要的区别。
 */
export function setAnchorMechanismEnabled(contract: Json, enabled: boolean): void {
  if (!enabled) {
    delete contract[ANCHOR_MECHANISM_KEY]
    return
  }
  const view = readAnchorMechanism(contract)
  writeAnchorMechanism(contract, { ...view, present: true, enabled: true })
}

/** 新增或替换某等级的锚点图片；同 assetId 视为同一张，按 level 覆盖。 */
export function upsertAnchorMechanismAnchor(
  contract: Json,
  entry: AnchorMechanismEntry,
): { ok: true } | { ok: false; reason: string } {
  const view = readAnchorMechanism(contract)
  const others = view.anchors.filter((a) => a.assetId !== entry.assetId)
  if (others.length + 1 > view.maxAnchorImages) {
    return {
      ok: false,
      reason: `锚点图片数将超过上限 ${view.maxAnchorImages}，请先提高上限或移除其它锚点图`,
    }
  }
  writeAnchorMechanism(contract, {
    ...view,
    present: true,
    enabled: true,
    anchors: [...others, entry],
  })
  return { ok: true }
}

/** 移除一张锚点图片。 */
export function removeAnchorMechanismAnchor(contract: Json, assetId: number): void {
  const view = readAnchorMechanism(contract)
  if (!view.present) return
  writeAnchorMechanism(contract, {
    ...view,
    anchors: view.anchors.filter((a) => a.assetId !== assetId),
  })
}

/** 调整送图上限；低于当前锚点数时拒绝，避免静默丢图。 */
export function setAnchorMechanismMaxImages(
  contract: Json,
  next: number,
): { ok: true } | { ok: false; reason: string } {
  if (!Number.isInteger(next) || next < 1) {
    return { ok: false, reason: "送图上限必须是正整数" }
  }
  if (next > MAX_ANCHOR_IMAGES_CEILING) {
    return { ok: false, reason: `送图上限不得超过 ${MAX_ANCHOR_IMAGES_CEILING}` }
  }
  const view = readAnchorMechanism(contract)
  if (next < view.anchors.length) {
    return {
      ok: false,
      reason: `已配 ${view.anchors.length} 张锚点图，上限不能低于此数`,
    }
  }
  writeAnchorMechanism(contract, { ...view, present: true, maxAnchorImages: next })
  return { ok: true }
}

export function imageRuleViewDefaults(dimension: Json): ImageRuleViewDefaults {
  return {
    dimensionScoreCap: typeof dimension.dimension_score_cap === "number"
      ? dimension.dimension_score_cap
      : 100,
    dimensionDeductionCap: typeof dimension.dimension_deduction_cap === "number"
      ? dimension.dimension_deduction_cap
      : 100,
    deductionRules: Array.isArray(dimension.deduction_rules)
      ? cloneJson(dimension.deduction_rules)
      : [],
    bonusRules: Array.isArray(dimension.bonus_rules)
      ? cloneJson(dimension.bonus_rules)
      : [],
  }
}

export type ImageRuleBindingView = {
  callAVersion: string
  callBVersion: string
  foundationEnabled: boolean
}

export function imageRuleBindingView(contract: Json | null | undefined): ImageRuleBindingView {
  const source = isRecord(contract) ? contract : {}
  const bindings = isRecord(source.prompt_bindings) ? source.prompt_bindings : {}
  return {
    callAVersion: typeof bindings.call_a_version === "string" ? bindings.call_a_version : "",
    callBVersion: typeof bindings.call_b_version === "string" ? bindings.call_b_version : "",
    foundationEnabled: isRecord(source.aesthetic_foundation),
  }
}

/**
 * 写入运营手选的 A/B 绑定。
 *
 * 美感前置基座自己也声明一份 call_b_version，后端门禁要求两处相等，不等就以
 * aesthetic_foundation_prompt_binding_mismatch 拒单。所以改 B 必须一并改基座，
 * 否则运营在界面上存出来的修订一定跑不起来。
 */
export function applyImageRuleBinding(
  contract: Json,
  stage: "A" | "B",
  version: string,
): void {
  const bindings = isRecord(contract.prompt_bindings) ? contract.prompt_bindings : {}
  contract.prompt_bindings = bindings
  // 「未绑定」的规范值是 null，不是空串：后端 call_b_version 允许 None 表示这条
  // 修订不走调用 B，空串会变成一个声明了却对不上任何版本的假绑定。
  const trimmed = version.trim()
  const next = trimmed === "" ? null : trimmed
  if (stage === "A") {
    bindings.call_a_version = next
    return
  }
  bindings.call_b_version = next
  const foundation = contract.aesthetic_foundation
  if (isRecord(foundation)) {
    foundation.call_b_version = next
  }
}

/**
 * 开关美感前置基座（锚图赛道）。
 *
 * 关掉就是从合同里删掉整个 aesthetic_foundation——worker 侧正是以「合同里有没有
 * 这个块」判断锚图赛道是否激活的。重新开启只能从原修订恢复：基座里的锚图资产、
 * 维度键、分档切点都是标定过的内容，界面凭空造不出来，没有模板时如实拒绝。
 */
export function setAestheticFoundationEnabled(
  contract: Json,
  enabled: boolean,
  template: Json | null | undefined,
): boolean {
  if (!enabled) {
    delete contract.aesthetic_foundation
    return true
  }
  if (isRecord(contract.aesthetic_foundation)) return true
  if (!isRecord(template)) return false
  const restored = cloneJson(template)
  const bindings = isRecord(contract.prompt_bindings) ? contract.prompt_bindings : {}
  // 一律对齐当前绑定（含未绑定的 null），否则模板里的旧版本号会留下来，
  // 直接撞上 aesthetic_foundation_prompt_binding_mismatch。
  const bound = bindings.call_b_version
  restored.call_b_version = typeof bound === "string" ? bound : null
  contract.aesthetic_foundation = restored
  return true
}

function isRuleDimension(dimension: Json): boolean {
  return Array.isArray(dimension.deduction_rules)
    || "bonus_rules" in dimension
    || "dimension_score_cap" in dimension
    || "dimension_deduction_cap" in dimension
}

export function prepareImageRulePayload(draft: Editable): Editable {
  const next = cloneJson(draft)
  for (const config of Object.values(next.subcategory_dimensions ?? {})) {
    if (!isRecord(config)) continue
    for (const groupKey of ["common_group", "specific_group"]) {
      const group = config[groupKey]
      const schema = isRecord(group) ? group.schema_definition : null
      const dimensions = isRecord(schema) ? schema.dimensions : null
      if (!Array.isArray(dimensions)) continue
      for (const dimension of dimensions) {
        if (!isRecord(dimension) || !isRuleDimension(dimension)) continue
        if (typeof dimension.dimension_score_cap !== "number") {
          dimension.dimension_score_cap = 100
        }
        if (typeof dimension.dimension_deduction_cap !== "number") {
          dimension.dimension_deduction_cap = 100
        }
        if (!Array.isArray(dimension.bonus_rules)) {
          dimension.bonus_rules = []
        }
      }
    }
  }
  return next
}
