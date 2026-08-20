export type NodeCorrectionType =
  | "call_a_field"
  | "precheck_field"
  | "redline"
  | "track"
  | "dimension_rule"
  | "final_level"

export type NodeCorrectionConfidence = "high" | "medium" | "low"

const CONFIDENCE_LABELS: Record<NodeCorrectionConfidence, string> = {
  high: "高",
  medium: "中",
  low: "低",
}

const LEGACY_CONFIDENCE_VALUES: Record<string, NodeCorrectionConfidence> = {
  高: "high",
  中: "medium",
  低: "low",
}

export const EMPTY_CORRECTION_HISTORY_TEXT = "暂无纠偏历史。旧评测没有纠偏记录时会安全显示为空。"

export type NodeCorrectionEvidence = {
  rule_id: string
  old_confidence: NodeCorrectionConfidence | null
  new_confidence: NodeCorrectionConfidence | null
  old_evidence: string
  new_evidence: string
}

export type NodeCorrectionHistoryItem = {
  correction_key?: string | null
  node_type: NodeCorrectionType
  node_path: string
  old_value: unknown
  new_value: unknown
  evidence?: NodeCorrectionEvidence[]
  reason: string
  corrector: string
  corrected_at: string
  downstream_recomputed: boolean
}

export type RuleDefinition = {
  rule_id: string
  description: string
  kind: "deduction" | "bonus"
  value: number
  tags?: string[]
}

export type RuleHit = {
  rule_id: string
  confidence: NodeCorrectionConfidence
  evidence: string
}

export type CorrectionNode = {
  id: string
  stage: 1 | 2 | 3 | 4 | 5
  nodeType: NodeCorrectionType
  nodePath: string
  label: string
  summary: string
  evidenceLines: string[]
  currentValue: unknown
  editor: "value" | "category" | "tags" | "redline" | "track" | "dimension_rules" | "level"
  valueKind?: "text" | "multiline" | "number" | "score" | "string_list" | "enum"
  group?: "rating" | "copy" | "classification" | "defect"
  maxLength?: number
  options?: Array<{ value: string; label: string }>
  redlineRule?: {
    key: string
    matchAny: string[]
    exemptions: string[]
  }
  ruleDefinitions?: RuleDefinition[]
  readOnly?: boolean
  compatibilityMessage?: string
}

type EvaluationLike = {
  precheck?: Record<string, unknown> | null
  aesthetic?: Record<string, unknown> | null
  scoring?: Record<string, unknown> | null
  score?: number | null
  level?: string | null
}

export const NODE_STAGE_META = [
  { stage: 1, label: "调用A字段", description: "素材分类与媒介信号" },
  { stage: 2, label: "红线判断", description: "淘汰规则命中状态" },
  { stage: 3, label: "赛道归属", description: "子类目评分分支" },
  { stage: 4, label: "维度规则", description: "逐条规则命中与证据" },
  { stage: 5, label: "最终等级", description: "L1 最好，L5 最差" },
] as const

export const CALL_A_GROUP_META = [
  { key: "rating", label: "评分类", description: "综合评分与等级" },
  { key: "copy", label: "文案类", description: "标题、风格与说明" },
  { key: "classification", label: "分类类", description: "分类、标签与媒介" },
  { key: "defect", label: "缺陷类", description: "红线与水印缺陷" },
] as const

const PRIMARY_CATEGORIES = [
  "建筑设计", "景观设计", "规划设计", "居住空间", "酒店民宿", "办公空间",
  "商业空间", "公共空间", "展示设计", "软装设计", "硬装结构", "意向图",
  "视觉设计", "产品设计", "美术类", "游戏设计", "其它",
]

const CALL_A_FIELD_META: Array<{
  field: string
  label: string
  group: NonNullable<CorrectionNode["group"]>
  editor: CorrectionNode["editor"]
  valueKind: NonNullable<CorrectionNode["valueKind"]>
  maxLength?: number
  options?: Array<{ value: string; label: string }>
}> = [
  { field: "score", label: "综合评分", group: "rating", editor: "value", valueKind: "score" },
  {
    field: "grade",
    label: "等级",
    group: "rating",
    editor: "level",
    valueKind: "enum",
    options: ["L1", "L2", "L3", "L4", "L5"].map((value) => ({ value, label: value })),
  },
  { field: "title", label: "专业标题", group: "copy", editor: "value", valueKind: "text", maxLength: 10 },
  { field: "seotitle", label: "中文SEO标题", group: "copy", editor: "value", valueKind: "text", maxLength: 28 },
  { field: "style", label: "风格", group: "copy", editor: "value", valueKind: "text", maxLength: 80 },
  { field: "cons", label: "犀利点评缺点", group: "copy", editor: "value", valueKind: "multiline", maxLength: 1000 },
  { field: "design", label: "设计理念说明", group: "copy", editor: "value", valueKind: "multiline", maxLength: 1000 },
  {
    field: "category",
    label: "分类",
    group: "classification",
    editor: "category",
    valueKind: "text",
    maxLength: 120,
    options: PRIMARY_CATEGORIES.map((value) => ({ value, label: value })),
  },
  { field: "tags", label: "主要标签", group: "classification", editor: "tags", valueKind: "string_list" },
  {
    field: "trait",
    label: "媒介类型",
    group: "classification",
    editor: "value",
    valueKind: "enum",
    options: ["AI图", "实景照片", "3D数字效果图", "其它"].map((value) => ({ value, label: value })),
  },
  {
    field: "reason",
    label: "红线短语",
    group: "defect",
    editor: "value",
    valueKind: "string_list",
    options: [
      { value: "是截图", label: "截图" },
      { value: "有大面积文字说明", label: "大面积文字说明" },
      { value: "是多拼图", label: "多拼图" },
      { value: "有二维码", label: "二维码" },
      { value: "是随手拍", label: "随手拍" },
      { value: "是颠倒图", label: "颠倒图" },
    ],
  },
  {
    field: "image_defects",
    label: "图片缺陷",
    group: "defect",
    editor: "value",
    valueKind: "enum",
    options: [{ value: "", label: "无" }, { value: "有水印", label: "有水印" }],
  },
]

const FIELD_META: Array<{
  path: string
  label: string
  valueKind: CorrectionNode["valueKind"]
  options?: Array<{ value: string; label: string }>
}> = [
  { path: "classification.primary_category", label: "主分类信号", valueKind: "text" },
  { path: "classification.primary_confidence", label: "主分类置信度", valueKind: "number" },
  {
    path: "classification.scope_status",
    label: "评测范围",
    valueKind: "enum",
    options: [
      { value: "in_scope", label: "范围内" },
      { value: "out_of_scope", label: "范围外" },
    ],
  },
  { path: "hard_defects", label: "高分硬伤信号", valueKind: "string_list" },
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

export function getPath(root: unknown, path: string): unknown {
  let current = root
  for (const part of path.split(".")) {
    if (!isRecord(current) || !(part in current)) return undefined
    current = current[part]
  }
  return current
}

export function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function valuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function correctionValueLabel(value: unknown): string {
  if (value == null) return "无"
  if (typeof value === "boolean") return value ? "命中" : "未命中"
  if (Array.isArray(value)) return value.length ? value.map((item) => correctionValueLabel(item)).join("、") : "空"
  if (isRecord(value)) {
    const ruleId = typeof value.rule_id === "string" ? value.rule_id : ""
    const confidence = confidenceLabel(value.confidence)
    if (ruleId) return `${ruleId}${confidence ? `（${confidence}）` : ""}`
    const text = JSON.stringify(value)
    return text.length > 80 ? `${text.slice(0, 77)}…` : text
  }
  return String(value)
}

export function confidenceLabel(value: unknown): string {
  const normalized = normalizeConfidence(value)
  return normalized ? CONFIDENCE_LABELS[normalized] : ""
}

export function formatRuleConfidence(value: unknown): string {
  const label = confidenceLabel(value)
  if (label) return label

  const numeric = typeof value === "number"
    ? value
    : typeof value === "string" && value.trim() !== ""
      ? Number(value)
      : Number.NaN
  if (Number.isFinite(numeric) && numeric >= 0 && numeric <= 1) {
    return `${Math.round(numeric * 100)}%`
  }
  return "未知"
}

export function normalizeConfidence(value: unknown): NodeCorrectionConfidence | null {
  if (value === "high" || value === "medium" || value === "low") return value
  if (typeof value === "string") return LEGACY_CONFIDENCE_VALUES[value.trim()] ?? null
  return null
}

export function normalizeRuleHits(value: unknown): RuleHit[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!isRecord(item) || typeof item.rule_id !== "string") return []
    const confidence = normalizeConfidence(item.confidence) ?? "medium"
    return [{
      rule_id: item.rule_id,
      confidence,
      evidence: typeof item.evidence === "string" ? item.evidence : "",
    } satisfies RuleHit]
  })
}

function dimensionMap(aesthetic: unknown): Record<string, Record<string, unknown>> {
  const raw = isRecord(aesthetic) ? aesthetic.dimensions : null
  if (Array.isArray(raw)) {
    const normalized: Record<string, Record<string, unknown>> = {}
    for (const item of raw) {
      if (isRecord(item) && typeof item.dimension_key === "string") normalized[item.dimension_key] = item
    }
    return normalized
  }
  if (!isRecord(raw)) return {}
  return Object.fromEntries(Object.entries(raw).filter(([, item]) => isRecord(item))) as Record<string, Record<string, unknown>>
}

function definitionsForTrack(config: unknown): Array<{
  key: string
  label: string
  deductionRules: RuleDefinition[]
  bonusRules: RuleDefinition[]
}> {
  if (!isRecord(config)) return []
  const definitions: Array<{
    key: string
    label: string
    deductionRules: RuleDefinition[]
    bonusRules: RuleDefinition[]
  }> = []
  for (const groupName of ["common_group", "specific_group"]) {
    const group = config[groupName]
    const schema = isRecord(group) ? group.schema_definition : null
    const schemaDimensions = isRecord(schema) ? schema.dimensions : null
    const legacyDimensions = isRecord(group) ? group.dimensions : null
    const dimensions = Array.isArray(schemaDimensions)
      ? schemaDimensions
      : legacyDimensions
    if (!Array.isArray(dimensions)) continue
    for (const dimension of dimensions) {
      if (!isRecord(dimension) || typeof dimension.key !== "string") continue
      const deductionRules = (Array.isArray(dimension.deduction_rules) ? dimension.deduction_rules : []).flatMap((rule) => {
        if (!isRecord(rule) || typeof rule.rule_id !== "string") return []
        return [{
          rule_id: rule.rule_id,
          description: typeof rule.description === "string" ? rule.description : rule.rule_id,
          kind: "deduction",
          value: Number(rule.deduction) || 0,
          tags: Array.isArray(rule.tags) ? rule.tags.map((tag) => String(tag)) : undefined,
        } satisfies RuleDefinition]
      })
      const bonusRules = (Array.isArray(dimension.bonus_rules) ? dimension.bonus_rules : []).flatMap((rule) => {
        if (!isRecord(rule) || typeof rule.rule_id !== "string") return []
        return [{
          rule_id: rule.rule_id,
          description: typeof rule.description === "string" ? rule.description : rule.rule_id,
          kind: "bonus",
          value: Number(rule.bonus) || 0,
          tags: Array.isArray(rule.tags) ? rule.tags.map((tag) => String(tag)) : undefined,
        } satisfies RuleDefinition]
      })
      definitions.push({
        key: dimension.key,
        label: typeof dimension.label === "string" ? dimension.label : dimension.key,
        deductionRules,
        bonusRules,
      })
    }
  }
  return definitions
}

export function buildCorrectionNodes(evaluation: EvaluationLike): CorrectionNode[] {
  const precheck = isRecord(evaluation.precheck) ? evaluation.precheck : {}
  const scoring = isRecord(evaluation.scoring) ? evaluation.scoring : {}
  const context = isRecord(scoring.v3_context) ? scoring.v3_context : {}
  const contract = isRecord(context.contract) ? context.contract : {}
  const redlinePolicy = isRecord(contract.redline_policy) ? contract.redline_policy : {}
  const redlineRules = Array.isArray(redlinePolicy.rules) ? redlinePolicy.rules : []
  const contractReasonOptions = [...new Set(redlineRules.flatMap((rawRule) => (
    isRecord(rawRule) && Array.isArray(rawRule.match_any)
      ? rawRule.match_any
        .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
        .map((item) => item.trim())
      : []
  )))].map((value) => ({ value, label: value }))
  const nodes: CorrectionNode[] = []

  const productionFields = isRecord(precheck.production_fields) ? precheck.production_fields : {}
  for (const field of CALL_A_FIELD_META) {
    const currentValue = field.field === "score"
      ? evaluation.score
      : field.field === "grade"
        ? evaluation.level
        : productionFields[field.field]
    const missing = currentValue === undefined || currentValue === null
    nodes.push({
      id: `call-a:${field.field}`,
      stage: 1,
      nodeType: "call_a_field",
      nodePath: `call_a.${field.field}`,
      label: field.label,
      summary: missing ? "未存储" : correctionValueLabel(currentValue),
      evidenceLines: missing
        ? ["该旧评测未存储此调用A字段，当前仅显示为空；请使用当前配置重跑后再纠偏。"]
        : field.field === "score"
          ? ["修改后按 L1 81-100 / L2 61-80 / L3 41-60 / L4 21-40 / L5 0-20 自动重算等级。"]
          : field.field === "grade"
            ? ["直接修改等级时以人工等级为准，综合评分保持不变并单独留痕。"]
            : ["来自调用A规范化输出；修改只更新该字段，不改变综合评分。"],
      currentValue: missing ? null : cloneJson(currentValue),
      editor: field.editor,
      valueKind: field.valueKind,
      group: field.group,
      maxLength: field.maxLength,
      options: field.field === "reason" && contractReasonOptions.length
        ? contractReasonOptions
        : field.options,
      readOnly: missing,
      compatibilityMessage: missing
        ? "该旧评测未存储此调用A字段，当前仅显示为空；请使用当前配置重跑后再纠偏。"
        : undefined,
    })
  }

  for (const field of FIELD_META) {
    const currentValue = getPath(precheck, field.path)
    if (currentValue === undefined) continue
    nodes.push({
      id: `precheck:${field.path}`,
      stage: 1,
      nodeType: "precheck_field",
      nodePath: `precheck.${field.path}`,
      label: field.label,
      summary: correctionValueLabel(currentValue),
      evidenceLines: field.path.includes("confidence") ? ["来自调用A的原始置信度"] : ["来自调用A冻结输出"],
      currentValue: cloneJson(currentValue),
      editor: "value",
      valueKind: field.valueKind,
      options: field.options,
    })
  }

  const reasons = Array.isArray(getPath(precheck, "production_fields.reason"))
    ? (getPath(precheck, "production_fields.reason") as unknown[]).map((item) => String(item))
    : []
  const scoringHitRules = Array.isArray(scoring.hit_rules) ? scoring.hit_rules.map((item) => String(item)) : []
  for (const rawRule of redlineRules) {
    if (!isRecord(rawRule) || typeof rawRule.key !== "string") continue
    const matchAny = Array.isArray(rawRule.match_any) ? rawRule.match_any.map((item) => String(item)) : []
    const exemptions = Array.isArray(rawRule.exemptions) ? rawRule.exemptions.map((item) => String(item)) : []
    const hit = scoringHitRules.includes(rawRule.key)
    nodes.push({
      id: `redline:${rawRule.key}`,
      stage: 2,
      nodeType: "redline",
      nodePath: "redline.production_fields.reason",
      label: typeof rawRule.label === "string" ? rawRule.label : rawRule.key,
      summary: hit ? "已命中" : "未命中",
      evidenceLines: [
        `判定信号：${matchAny.join(" / ") || "未配置"}`,
        `当前调用A信号：${reasons.join("、") || "无"}`,
        ...(exemptions.length ? [`豁免：${exemptions.join("、")}`] : []),
      ],
      currentValue: cloneJson(reasons),
      editor: "redline",
      redlineRule: { key: rawRule.key, matchAny, exemptions },
    })
  }

  const trackClassification = isRecord(contract.track_classification) ? contract.track_classification : {}
  const rawTracks = Array.isArray(trackClassification.tracks) ? trackClassification.tracks : []
  const trackOptions = rawTracks.flatMap((track) => (
    isRecord(track) && typeof track.key === "string"
      ? [{ value: track.key, label: typeof track.label === "string" ? track.label : track.key }]
      : []
  ))
  const trackKey = typeof scoring.track_key === "string" ? scoring.track_key : ""
  const trackLabel = trackOptions.find((option) => option.value === trackKey)?.label || trackKey || "红线终止，尚无赛道"
  nodes.push({
    id: "track:track_key",
    stage: 3,
    nodeType: "track",
    nodePath: "track_key",
    label: "子类目赛道",
    summary: trackLabel,
    evidenceLines: [
      `调用A主分类：${correctionValueLabel(getPath(precheck, "classification.primary_category"))}`,
      `冻结配置版本：${correctionValueLabel(context.config_revision)}`,
    ],
    currentValue: trackKey || null,
    editor: "track",
    options: trackOptions,
  })

  const hasDimensionConfig = isRecord(context.subcategory_dimensions)
  const dimensionsByTrack = isRecord(context.subcategory_dimensions) ? context.subcategory_dimensions : {}
  const config = trackKey ? dimensionsByTrack[trackKey] : null
  const currentDimensions = dimensionMap(evaluation.aesthetic)
  const definitions = definitionsForTrack(config)
  const definitionKeys = new Set(definitions.map((definition) => definition.key))
  const compatibilityMessage = "该结果由旧引擎产出，维度规则版本不一致，建议用新引擎重跑后再逐维纠偏。"
  for (const definition of definitions) {
    const currentDimension = currentDimensions[definition.key]
    const hits = normalizeRuleHits(currentDimension?.hit_rules)
    const configuredRuleIds = new Set(definition.deductionRules.map((rule) => rule.rule_id))
    const unknownRuleIds = hits.filter((hit) => !configuredRuleIds.has(hit.rule_id))
    const aligned = Boolean(currentDimension) && definition.deductionRules.length > 0 && unknownRuleIds.length === 0
    const evidenceLines = !aligned
      ? [compatibilityMessage]
      : hits.length
      ? hits.map((hit) => `${hit.rule_id} · 置信度${confidenceLabel(hit.confidence)} · ${hit.evidence}`)
      : ["当前未命中任何扣分规则"]
    nodes.push({
      id: `dimension:${definition.key}`,
      stage: 4,
      nodeType: "dimension_rule",
      nodePath: `dimension.${definition.key}.hit_rules`,
      label: definition.label,
      summary: hits.length ? `命中 ${hits.length} / ${definition.deductionRules.length} 条` : `未命中（共 ${definition.deductionRules.length} 条）`,
      evidenceLines,
      currentValue: hits,
      editor: "dimension_rules",
      ruleDefinitions: definition.deductionRules,
      readOnly: !aligned,
      compatibilityMessage: aligned ? undefined : compatibilityMessage,
    })

    if (definition.bonusRules.length || Array.isArray(currentDimension?.hit_bonus_rules)) {
      const bonusHits = normalizeRuleHits(currentDimension?.hit_bonus_rules)
      const configuredBonusIds = new Set(definition.bonusRules.map((rule) => rule.rule_id))
      const unknownBonusIds = bonusHits.filter((hit) => !configuredBonusIds.has(hit.rule_id))
      const bonusAligned = Boolean(currentDimension)
        && definition.bonusRules.length > 0
        && unknownBonusIds.length === 0
      const bonusEvidenceLines = !bonusAligned
        ? [compatibilityMessage]
        : bonusHits.length
          ? bonusHits.map((hit) => `加分 · ${hit.rule_id} · 置信度${confidenceLabel(hit.confidence)} · ${hit.evidence}`)
          : ["当前未命中任何加分规则"]
      nodes.push({
        id: `dimension:${definition.key}:bonus`,
        stage: 4,
        nodeType: "dimension_rule",
        nodePath: `dimension.${definition.key}.hit_bonus_rules`,
        label: `${definition.label} · 加分规则`,
        summary: bonusHits.length
          ? `命中 ${bonusHits.length} / ${definition.bonusRules.length} 条`
          : `未命中（共 ${definition.bonusRules.length} 条）`,
        evidenceLines: bonusEvidenceLines,
        currentValue: bonusHits,
        editor: "dimension_rules",
        ruleDefinitions: definition.bonusRules,
        readOnly: !bonusAligned,
        compatibilityMessage: bonusAligned ? undefined : compatibilityMessage,
      })
    }
  }

  for (const [dimensionKey, dimension] of hasDimensionConfig ? Object.entries(currentDimensions) : []) {
    if (definitionKeys.has(dimensionKey)) continue
    const hits = normalizeRuleHits(dimension.hit_rules)
    nodes.push({
      id: `dimension:${dimensionKey}`,
      stage: 4,
      nodeType: "dimension_rule",
      nodePath: `dimension.${dimensionKey}.hit_rules`,
      label: dimensionKey,
      summary: "仅可查看（历史维度）",
      evidenceLines: [
        compatibilityMessage,
        ...hits.map((hit) => `${hit.rule_id} · 置信度${confidenceLabel(hit.confidence)} · ${hit.evidence}`),
      ],
      currentValue: hits,
      editor: "dimension_rules",
      ruleDefinitions: [],
      readOnly: true,
      compatibilityMessage,
    })
    const bonusHits = normalizeRuleHits(dimension.hit_bonus_rules)
    if (bonusHits.length) {
      nodes.push({
        id: `dimension:${dimensionKey}:bonus`,
        stage: 4,
        nodeType: "dimension_rule",
        nodePath: `dimension.${dimensionKey}.hit_bonus_rules`,
        label: `${dimensionKey} · 加分规则`,
        summary: "仅可查看（历史维度）",
        evidenceLines: [
          compatibilityMessage,
          ...bonusHits.map((hit) => `加分 · ${hit.rule_id} · 置信度${confidenceLabel(hit.confidence)} · ${hit.evidence}`),
        ],
        currentValue: bonusHits,
        editor: "dimension_rules",
        ruleDefinitions: [],
        readOnly: true,
        compatibilityMessage,
      })
    }
  }

  const level = evaluation.level || (typeof scoring.level === "string" ? scoring.level : null)
  nodes.push({
    id: "level:final_level",
    stage: 5,
    nodeType: "final_level",
    nodePath: "final_level",
    label: "最终等级",
    summary: level || "尚无等级",
    evidenceLines: [
      `当前权威分：${correctionValueLabel(scoring.score)}`,
      "手动改等级不会改写权威分；该事件单独留痕",
    ],
    currentValue: level,
    editor: "level",
    options: ["L1", "L2", "L3", "L4", "L5"].map((value) => ({ value, label: value })),
  })

  return nodes
}

export function redlineReasonsAfterToggle(
  currentValue: unknown,
  rule: NonNullable<CorrectionNode["redlineRule"]>,
  hit: boolean,
): string[] {
  const current = Array.isArray(currentValue) ? currentValue.map((item) => String(item)) : []
  const removed = hit ? [...rule.matchAny, ...rule.exemptions] : rule.matchAny
  const next = current.filter((item) => !removed.includes(item))
  if (hit && rule.matchAny[0]) next.push(rule.matchAny[0])
  return [...new Set(next)]
}

export function ruleEvidenceDelta(oldValue: unknown, newValue: unknown): NodeCorrectionEvidence[] {
  const oldHits = new Map(normalizeRuleHits(oldValue).map((hit) => [hit.rule_id, hit]))
  const newHits = new Map(normalizeRuleHits(newValue).map((hit) => [hit.rule_id, hit]))
  const ids = [...new Set([...oldHits.keys(), ...newHits.keys()])]
  return ids.flatMap((ruleId) => {
    const oldHit = oldHits.get(ruleId)
    const newHit = newHits.get(ruleId)
    if (valuesEqual(oldHit, newHit)) return []
    return [{
      rule_id: ruleId,
      old_confidence: oldHit?.confidence ?? null,
      new_confidence: newHit?.confidence ?? null,
      old_evidence: oldHit?.evidence ?? "",
      new_evidence: newHit?.evidence ?? "",
    }]
  })
}
