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
 * 造不出来（其总开关已随拆分移除，旧修订带的基座只作遗留形态展示）。锚点
 * 机制只有图片和等级，所以**不需要 template**，运营可以从零配起——这正是
 * 拆分要换来的能力。
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

/* ------------------------------------------------------------------ *
 * 质量规则机制（quality-rules-v1）
 *
 * 唯一职责，只有两项：
 *   随手拍限分     snapshot_limit    —— 命中信号时把总分压到上限
 *   硬伤例外名单   defect_exceptions —— 满足佐证条件时硬伤不触发降级
 *
 * 严禁混入其它机制——
 *   分数阈值   → 合同顶层 level_thresholds / level_scale
 *   维度与权重 → Call B 的 dimensions
 *   红线策略   → 顶层 redline_policy
 *   锚点图片   → anchor_mechanism
 *
 * 与 aesthetic_foundation 的关键差异：旧基座把关键词、豁免条数、维度全写死了
 * （关键词必须精确等于「是随手拍」、豁免必须恰好 1 条），运营改任何一处都被拒。
 * 本机制只校验结构与取值域，不锁业务内容——这正是拆分要换来的能力。
 * ------------------------------------------------------------------ */

export const QUALITY_RULES_KEY = "quality_rules"
export const DEFECT_SOURCES = ["image_defects", "content_defects"] as const
export const SNAPSHOT_LIMIT_LEVELS = ["L1", "L2", "L3", "L4", "L5"] as const
const DEFAULT_SNAPSHOT_SIGNAL = "production_fields.reason"

export type DefectSource = (typeof DEFECT_SOURCES)[number]
export type SnapshotLimitLevel = (typeof SNAPSHOT_LIMIT_LEVELS)[number]

export type SnapshotLimitView = {
  enabled: boolean
  name: string
  signal: string
  whenReasonContains: string[]
  /** 与 maxLevel 互斥：配了分数就不配等级。 */
  maxScore: number | null
  maxLevel: SnapshotLimitLevel | null
  dimensionCeilings: Record<string, number>
}

export type DimensionRequirementView = {
  dimension: string
  /** 档位门槛（1-5）；仅在调用B输出八维档位的评测路径可核实。null 表示不配。 */
  minGrade: number | null
  noShortcomings: boolean
  /** 该维度未命中任何扣分规则；规则计分路径用现成的每维扣分核实。 */
  noDeductionHits: boolean
  /** 该维度累计扣分不超过 N 分；规则计分路径可核实。null 表示不配。 */
  maxDeduction: number | null
}

export type DefectExceptionView = {
  name: string
  defect: string
  defectSource: DefectSource
  whenEvidenceContains: string[]
  requireDimensions: DimensionRequirementView[]
}

export type QualityRulesView = {
  present: boolean
  enabled: boolean
  snapshotLimit: SnapshotLimitView | null
  defectExceptions: DefectExceptionView[]
}

/** 与后端 _FOREIGN_KEYS 对齐：界面永远不该往质量规则块写这些键。 */
const QUALITY_RULES_FOREIGN_KEYS = new Set([
  "score_thresholds",
  "level_thresholds",
  "level_scale",
  "bands",
  "anchors",
  "anchor_samples",
  "anchor_mechanism",
  "dimensions",
  "dimension_keys",
  "dimension_weights",
  "redline_policy",
  "redlines",
  "boundary_policy",
  "prompt_template",
  "call_b_version",
  "calibration_status",
  "aesthetic_foundation",
])

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === "string")
}

function readSnapshotLimit(raw: unknown): SnapshotLimitView | null {
  if (!isRecord(raw)) return null
  const ceilings: Record<string, number> = {}
  const rawCeilings = raw.dimension_ceilings
  if (isRecord(rawCeilings)) {
    for (const [key, limit] of Object.entries(rawCeilings)) {
      if (typeof limit === "number") ceilings[key] = limit
    }
  }
  const maxScore = typeof raw.max_score === "number" ? raw.max_score : null
  const maxLevel = SNAPSHOT_LIMIT_LEVELS.includes(raw.max_level as SnapshotLimitLevel)
    ? (raw.max_level as SnapshotLimitLevel)
    : null
  return {
    enabled: raw.enabled !== false,
    name: typeof raw.name === "string" ? raw.name : "随手拍限分",
    signal: typeof raw.signal === "string" ? raw.signal : DEFAULT_SNAPSHOT_SIGNAL,
    whenReasonContains: stringList(raw.when_reason_contains),
    maxScore,
    maxLevel,
    dimensionCeilings: ceilings,
  }
}

function readDefectExceptions(raw: unknown): DefectExceptionView[] {
  if (!Array.isArray(raw)) return []
  const items: DefectExceptionView[] = []
  for (const entry of raw) {
    if (!isRecord(entry)) continue
    const requirements: DimensionRequirementView[] = []
    if (Array.isArray(entry.require_dimensions)) {
      for (const requirement of entry.require_dimensions) {
        if (!isRecord(requirement)) continue
        if (typeof requirement.dimension !== "string") continue
        requirements.push({
          dimension: requirement.dimension,
          minGrade: typeof requirement.min_grade === "number" ? requirement.min_grade : null,
          noShortcomings: requirement.no_shortcomings === true,
          noDeductionHits: requirement.no_deduction_hits === true,
          maxDeduction:
            typeof requirement.max_deduction === "number" ? requirement.max_deduction : null,
        })
      }
    }
    items.push({
      name: typeof entry.name === "string" ? entry.name : "",
      defect: typeof entry.defect === "string" ? entry.defect : "",
      defectSource: DEFECT_SOURCES.includes(entry.defect_source as DefectSource)
        ? (entry.defect_source as DefectSource)
        : "image_defects",
      whenEvidenceContains: stringList(entry.when_evidence_contains),
      requireDimensions: requirements,
    })
  }
  return items
}

/** 读出质量规则块的界面视图。合同没有本块时 present=false。 */
export function readQualityRules(contract: unknown): QualityRulesView {
  const block = isRecord(contract) ? contract[QUALITY_RULES_KEY] : null
  if (!isRecord(block)) {
    return { present: false, enabled: false, snapshotLimit: null, defectExceptions: [] }
  }
  return {
    present: true,
    enabled: block.enabled !== false,
    snapshotLimit: readSnapshotLimit(block.snapshot_limit),
    defectExceptions: readDefectExceptions(block.defect_exceptions),
  }
}

/** 前端侧隔离守卫：返回混进本块的外来机制键，供界面直接报错。 */
export function qualityRulesIntruders(contract: unknown): string[] {
  const block = isRecord(contract) ? contract[QUALITY_RULES_KEY] : null
  if (!isRecord(block)) return []
  const found = new Set<string>()
  const scan = (candidate: unknown) => {
    if (!isRecord(candidate)) return
    for (const key of Object.keys(candidate)) {
      if (QUALITY_RULES_FOREIGN_KEYS.has(key)) found.add(key)
    }
  }
  scan(block)
  scan(block.snapshot_limit)
  if (Array.isArray(block.defect_exceptions)) {
    for (const entry of block.defect_exceptions) scan(entry)
  }
  return [...found].sort()
}

function defaultSnapshotLimitBlock(): Json {
  return {
    enabled: true,
    name: "随手拍限分",
    signal: DEFAULT_SNAPSHOT_SIGNAL,
    when_reason_contains: ["是随手拍"],
    max_score: 59,
  }
}

function emptyQualityRulesBlock(): Json {
  return {
    enabled: true,
    snapshot_limit: defaultSnapshotLimitBlock(),
    defect_exceptions: [],
  }
}

/**
 * 原地修改合同上的质量规则块。
 *
 * 必须是原地语义（返回 void）——界面的调用形态是
 * ``onPatch((next) => { setXxx(next.contract, …) })``，返回值会被丢弃。
 * 若在此 clone 出新对象再返回，运营的操作会静默失效。
 */
function mutateQualityRules(contract: Json, mutate: (block: Json) => void): void {
  const existing = contract[QUALITY_RULES_KEY]
  const block = isRecord(existing) ? existing : emptyQualityRulesBlock()
  mutate(block)
  contract[QUALITY_RULES_KEY] = block
}

/** 开关整个质量规则机制。关掉时限分与豁免都不生效。 */
export function setQualityRulesEnabled(contract: Json, enabled: boolean): void {
  mutateQualityRules(contract, (block) => {
    block.enabled = enabled
  })
}

/** 单独开关随手拍限分，保留已配的豁免。 */
export function setSnapshotLimitEnabled(contract: Json, enabled: boolean): void {
  mutateQualityRules(contract, (block) => {
    const limit = isRecord(block.snapshot_limit)
      ? block.snapshot_limit
      : defaultSnapshotLimitBlock()
    limit.enabled = enabled
    block.snapshot_limit = limit
  })
}

/** 改随手拍限分的判定关键词。旧基座锁死为「是随手拍」，这里放开。 */
export function setSnapshotLimitKeywords(contract: Json, keywords: string[]): void {
  mutateQualityRules(contract, (block) => {
    const limit = isRecord(block.snapshot_limit)
      ? block.snapshot_limit
      : defaultSnapshotLimitBlock()
    limit.when_reason_contains = keywords.map((item) => item.trim()).filter(Boolean)
    block.snapshot_limit = limit
  })
}

/** 按分数封顶。与按等级封顶互斥，写入时清掉另一侧。 */
export function setSnapshotLimitMaxScore(contract: Json, maxScore: number): void {
  mutateQualityRules(contract, (block) => {
    const limit = isRecord(block.snapshot_limit)
      ? block.snapshot_limit
      : defaultSnapshotLimitBlock()
    limit.max_score = maxScore
    delete limit.max_level
    delete limit.dimension_ceilings
    block.snapshot_limit = limit
  })
}

/** 按等级封顶。与按分数封顶互斥，写入时清掉另一侧。 */
export function setSnapshotLimitMaxLevel(
  contract: Json,
  maxLevel: SnapshotLimitLevel,
): void {
  mutateQualityRules(contract, (block) => {
    const limit = isRecord(block.snapshot_limit)
      ? block.snapshot_limit
      : defaultSnapshotLimitBlock()
    limit.max_level = maxLevel
    delete limit.max_score
    block.snapshot_limit = limit
  })
}

/** 按等级封顶时可选的维度分上限。 */
export function setSnapshotLimitDimensionCeilings(
  contract: Json,
  ceilings: Record<string, number>,
): void {
  mutateQualityRules(contract, (block) => {
    const limit = isRecord(block.snapshot_limit)
      ? block.snapshot_limit
      : defaultSnapshotLimitBlock()
    limit.dimension_ceilings = { ...ceilings }
    block.snapshot_limit = limit
  })
}

function serializeDefectException(item: DefectExceptionView): Json {
  return {
    name: item.name.trim(),
    defect: item.defect.trim(),
    defect_source: item.defectSource,
    when_evidence_contains: item.whenEvidenceContains
      .map((token) => token.trim())
      .filter(Boolean),
    require_dimensions: item.requireDimensions.map((requirement) => {
      const serialized: Json = {
        dimension: requirement.dimension.trim(),
        no_shortcomings: requirement.noShortcomings,
      }
      if (requirement.minGrade != null) serialized.min_grade = requirement.minGrade
      if (requirement.noDeductionHits) serialized.no_deduction_hits = true
      if (requirement.maxDeduction != null) serialized.max_deduction = requirement.maxDeduction
      return serialized
    }),
  }
}

/** 整体替换硬伤例外名单。旧基座锁死为恰好 1 条，这里放开条数。 */
export function setDefectExceptions(
  contract: Json,
  exceptions: DefectExceptionView[],
): void {
  mutateQualityRules(contract, (block) => {
    block.defect_exceptions = exceptions.map(serializeDefectException)
  })
}

/** 追加一条空白豁免，供界面新建后再填。 */
export function appendDefectException(contract: Json): void {
  mutateQualityRules(contract, (block) => {
    const existing = Array.isArray(block.defect_exceptions)
      ? block.defect_exceptions
      : []
    block.defect_exceptions = [
      ...existing,
      serializeDefectException({
        name: "",
        defect: "",
        defectSource: "image_defects",
        whenEvidenceContains: [],
        requireDimensions: [{
          dimension: "",
          minGrade: 4,
          noShortcomings: true,
          noDeductionHits: false,
          maxDeduction: null,
        }],
      }),
    ]
  })
}

/** 删除指定下标的豁免。 */
export function removeDefectException(contract: Json, index: number): void {
  mutateQualityRules(contract, (block) => {
    const existing = Array.isArray(block.defect_exceptions)
      ? block.defect_exceptions
      : []
    block.defect_exceptions = existing.filter((_, position) => position !== index)
  })
}
