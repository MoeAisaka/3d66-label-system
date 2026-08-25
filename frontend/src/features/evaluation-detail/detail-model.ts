/**
 * 人工纠偏三段式评测细节的纯逻辑层。
 *
 * 把一条评测结果拆成运营可直接识别的三大部分：调用A、调用B、等级撮合器。
 * 这里只做「读取与翻译」，不做任何评分计算——最终分数与等级仍由服务端权威引擎决定。
 */

export type DetailTone = "neutral" | "active" | "warning" | "danger" | "success"

export type DetailRow = {
  /** 字段中文名，运营看到的第一列 */
  label: string
  /** 模型给出的结果，已转成可读文本 */
  value: string
  /** 该行的判定色，用于让异常项自己跳出来 */
  tone?: DetailTone
  /** 字段口径提示，例如「0-100 整数」 */
  hint?: string
  /** 模型的判断依据原文 */
  evidence?: string[]
  /** 该行是否已被人工改过 */
  corrected?: boolean
  /** 该项是机制新增、前端还没配中文名的；用于提示运营「这是新项不是异常」 */
  isNew?: boolean
  /** 可点击纠偏时的提交目标；缺省表示这一行不能直接改 */
  correction?: RowCorrectionTarget
  /** 不能直接改的原因，例如「由撮合器算出」；与 correction 互斥 */
  derivedNote?: string
  /** 人工已纠偏后的值，用于和模型判断并列对照 */
  humanValue?: string
}

/** 纠偏弹窗需要的控件形态 */
export type RowValueKind =
  | "text"
  | "multiline"
  | "integer"
  | "enum"
  | "multi_enum"
  | "string_list"
  | "rule_hit"

export type RowCorrectionTarget = {
  /** 服务端 node_type，决定回写分支 */
  nodeType:
    | "call_a_field"
    | "precheck_field"
    | "aesthetic_score"
    | "dimension_rule"
    | "track"
    | "final_level"
  /** 服务端 node_path */
  nodePath: string
  valueKind: RowValueKind
  /** 提交时用于乐观并发校验的当前值，必须是服务端存的原始形态 */
  currentValue: unknown
  options?: Array<{ value: string; label: string }>
  minimum?: number
  maximum?: number
  hint?: string
  /** rule_hit 专用：提交新命中对象时服务端要求 rule_id 与节点一致 */
  ruleId?: string
  /** 改完是否会带动分数与等级重算；用于在弹窗里如实告知运营 */
  recomputes: boolean
}

export type DetailGroup = {
  title: string
  description?: string
  rows: DetailRow[]
  /** 该组为空时显示的说明，避免运营误判成「数据丢了」 */
  emptyText?: string
  note?: string
}

export type DetailSectionKey = "A" | "B" | "V3"

export type DetailSection = {
  key: DetailSectionKey
  title: string
  /** 该段一句话职责，帮运营建立心智 */
  description: string
  /** 该段最关键的一个结论，显示在段头右侧 */
  headline: string | null
  headlineHint: string | null
  headlineTone: DetailTone
  groups: DetailGroup[]
  /** 该段数据缺失的原因；有值时整段降级为说明而不是空表 */
  unavailableReason: string | null
}

/**
 * 已知生产字段的展示顺序与口径提示。
 *
 * 这是「排序与美化」用的，不是白名单：调用A新增字段时不在这里的会自动追加到末尾并标记为新增，
 * 不需要改前端就能看到。要给新字段配中文名和口径提示时，再往这里补一行。
 */
const KNOWN_PRODUCTION_FIELDS: Array<{
  key: string
  label: string
  hint?: string
  valueKind: RowValueKind
}> = [
  { key: "title", label: "素材标题", hint: "10 字内", valueKind: "text" },
  { key: "seotitle", label: "搜索标题", hint: "28 字内", valueKind: "text" },
  { key: "category", label: "素材类目", hint: "一级分类，二级分类", valueKind: "text" },
  { key: "style", label: "素材风格", hint: "无法判断时写「无法判断」", valueKind: "text" },
  { key: "tags", label: "素材标签", hint: "至少 4 个", valueKind: "string_list" },
  { key: "cons", label: "素材缺点", hint: "只依据可见证据", valueKind: "multiline" },
  { key: "design", label: "设计说明", hint: "无法判断时不得编造", valueKind: "multiline" },
  { key: "score", label: "素材分数", hint: "0-100 整数，不是最终等级", valueKind: "integer" },
  { key: "reason", label: "过滤原因", hint: "命中即触发红线", valueKind: "multi_enum" },
  { key: "image_defects", label: "图片缺陷", hint: "空或「有水印」", valueKind: "enum" },
  {
    key: "trait",
    label: "素材媒介",
    hint: "AI图 / 实景照片 / 3D数字效果图 / 其它",
    valueKind: "enum",
  },
]

/** 值非空就意味着命中了负面信号的字段；用于让异常项自己跳出来 */
const SIGNAL_FIELDS: Record<string, DetailTone> = {
  reason: "danger",
  image_defects: "warning",
}

const REDLINE_LABELS: Record<string, string> = {
  screenshot: "是截图",
  casual_photo: "是随手拍",
  text_heavy: "有大面积文字说明",
  qr_code_heavy: "有二维码",
}

const HARD_DEFECT_LABELS: Record<string, string> = {
  blurry_grayish: "模糊发灰",
  careless_composition: "构图随意",
  garish_color: "颜色刺眼",
  large_dead_black: "大面积死黑",
  distorted_viewpoint: "视角变形",
  fake_material: "材质失真",
  fisheye_distortion: "鱼眼畸变",
  invalid_black_border: "无效黑边",
  severe_color_cast: "严重偏色",
  known_real_photo_defect: "已知实拍缺陷",
}

const IMAGE_DEFECT_LABELS: Record<string, string> = {
  corner_small_watermark: "角落小水印",
  subject_obscuring_watermark: "水印遮挡主体",
  large_area_watermark: "大面积水印",
}

/** 撮合器每一步的中文名，键与后端 aggregator 的 step 值一一对应 */
const STEP_LABELS: Record<string, string> = {
  redline: "红线判断",
  // 不叫「赛道归属」——那是「撮合输入」里的行名，同名会让运营分不清输入与步骤
  track: "赛道与基础分",
  b_aesthetic_foundation: "调用B美感基础分",
  dimensions: "维度扣分（旧口径）",
  dimension_rule_deduction: "维度扣分（规则命中）",
  track_adjustment: "赛道修正",
  track_adjustment_skipped: "赛道修正（未启用）",
  media: "媒介扣分",
  media_skipped: "媒介扣分（未启用）",
  hard_defect_penalty: "硬缺陷扣分",
  hard_defect_severity: "硬缺陷严重度封顶",
  veto: "高分否决",
  veto_skipped: "高分否决（未触发）",
  track_cap: "赛道封顶",
  level: "分数转等级",
}

const CAP_LABELS: Record<string, string> = {
  redline: "红线封顶",
  hard_defect_severity: "硬缺陷严重度封顶",
  high_score_veto: "高分否决封顶",
  track_cap: "赛道上限封顶",
}

const QUALITY_SEVERITY_LABELS: Record<string, string> = {
  normal: "正常",
  slight: "轻微",
  moderate: "中等",
  severe: "严重",
  unusable: "不可用",
  uncertain: "无法判断",
}

/** 未命中或未启用的步骤不代表出错，单独标色避免运营误读成异常 */
const NEUTRAL_STEPS = new Set([
  "track_adjustment_skipped",
  "media_skipped",
  "veto_skipped",
])

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** 机制新增项的统一标记：让运营知道这项是新的、还没配中文名，而不是显示成乱码 */
export const NEW_ITEM_HINT = "机制新增项"

/**
 * 按「已知顺序在前、新增项追加在后」合并键。
 *
 * 机制升级新增字段或规则时，前端无需改动就能把新项显示出来；已知项仍保持人工排好的顺序。
 */
export function mergeKeyOrder(
  knownKeys: readonly string[],
  actualKeys: readonly string[],
): Array<{ key: string; known: boolean }> {
  const actual = new Set(actualKeys)
  const ordered = knownKeys
    .filter((key) => actual.has(key))
    .map((key) => ({ key, known: true }))
  const extras = actualKeys
    .filter((key) => !knownKeys.includes(key))
    .map((key) => ({ key, known: false }))
  return [...ordered, ...extras]
}

/**
 * 取中文标签：优先用机制自带的 label，其次查前端已知映射，都没有就回落到原始键。
 *
 * 回落到原始键是有意的——显示英文键比编一个错译更有利于运营反馈「这项缺名字」。
 */
export function resolveLabel(
  key: string,
  knownLabels: Record<string, string>,
  dynamicLabel?: unknown,
): string {
  if (typeof dynamicLabel === "string" && dynamicLabel.trim()) return dynamicLabel.trim()
  return knownLabels[key] ?? key
}

/** 把任意模型输出转成运营能读的一行文本；空值统一显示为「—」而不是 undefined */
export function readableValue(value: unknown): string {
  if (value === undefined || value === null) return "—"
  if (typeof value === "string") return value.trim() || "—"
  if (typeof value === "boolean") return value ? "是" : "否"
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "—"
  if (Array.isArray(value)) {
    const items = value
      .map((item) => (typeof item === "string" ? item.trim() : readableValue(item)))
      .filter((item) => item && item !== "—")
    return items.length ? items.join("、") : "—"
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value)
    if (!entries.length) return "—"
    return entries
      .map(([key, item]) => `${key}：${readableValue(item)}`)
      .join("；")
  }
  return String(value)
}

function stringList(value: unknown): string[] {
  if (typeof value === "string") return value.trim() ? [value.trim()] : []
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (typeof item === "string" ? item.trim() : readableValue(item)))
    .filter((item) => item && item !== "—")
}

export function percentLabel(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null
  const ratio = value > 1 ? value / 100 : value
  return `${Math.round(ratio * 100)}%`
}

/** 数值型置信度转中文档位，与节点纠偏工作台的高/中/低口径保持一致 */
export function confidenceTone(value: unknown): DetailTone {
  if (typeof value !== "number" || !Number.isFinite(value)) return "neutral"
  const ratio = value > 1 ? value / 100 : value
  if (ratio >= 0.8) return "success"
  if (ratio >= 0.5) return "warning"
  return "danger"
}

function ruleConfidenceLabel(value: unknown): string {
  if (typeof value === "string") {
    const mapped: Record<string, string> = { high: "高", medium: "中", low: "低" }
    return mapped[value] ?? value
  }
  return percentLabel(value) ?? "—"
}

/** 单条规则的模型判定摘要；证据单独展示，这里只拼命中状态、扣/加分与置信度 */
function ruleHitSummary(
  hit: unknown,
  ruleKind: "deduction" | "bonus" | undefined,
): string {
  if (!isPlainObject(hit)) return "未命中"
  const parts = ["命中"]
  const delta = hit.deduction ?? hit.bonus ?? hit.value
  if (typeof delta === "number") {
    parts.push(ruleKind === "bonus" ? `加 ${delta} 分` : `扣 ${delta} 分`)
  }
  if (hit.confidence !== undefined) {
    parts.push(`置信度 ${ruleConfidenceLabel(hit.confidence)}`)
  }
  return parts.join(" · ")
}

/** 人工纠偏后的规则结论；人工值不带扣分数值（重放后由服务端按合同累计） */
function humanRuleHitSummary(value: unknown): string {
  if (value === null) return "未命中"
  if (!isPlainObject(value)) return readableValue(value)
  const parts = ["命中"]
  if (value.confidence !== undefined) {
    parts.push(`置信度 ${ruleConfidenceLabel(value.confidence)}`)
  }
  const evidence = typeof value.evidence === "string" ? value.evidence.trim() : ""
  const head = parts.join(" · ")
  return evidence ? `${head}：${evidence}` : head
}

/** 调用A：列出每个生产字段与模型给出的结果，以及决定性信号的证据 */
export function buildCallASection(
  precheck: Record<string, unknown> | null | undefined,
  options: {
    correctedFieldKeys?: readonly string[]
    /** 机制下发的字段规格（label/hint），有则优先，使前端无需先认识新字段 */
    fieldSpecs?: Record<string, unknown> | null
    /** 冻结合同索引，决定哪一行可点击纠偏 */
    contractIndex?: Map<string, ContractNodeSpec>
    /** 人工已纠偏的值，按 node_key 索引，用于与模型判断并列对照 */
    humanValues?: Record<string, unknown>
  } = {},
): DetailSection {
  const corrected = new Set(options.correctedFieldKeys ?? [])
  const contractIndex = options.contractIndex ?? new Map<string, ContractNodeSpec>()
  const humanValues = options.humanValues ?? {}
  const source = isPlainObject(precheck) ? precheck : {}
  const productionFields = isPlainObject(source.production_fields)
    ? source.production_fields
    : {}
  /** 字段规格（可选）。机制若下发了 label/hint，优先用它，前端不必先认识这个字段。 */
  const fieldSpecs = isPlainObject(options.fieldSpecs) ? options.fieldSpecs : {}
  const knownFieldLabels = Object.fromEntries(
    KNOWN_PRODUCTION_FIELDS.map((field) => [field.key, field.label]),
  )
  const knownFieldHints = Object.fromEntries(
    KNOWN_PRODUCTION_FIELDS.map((field) => [field.key, field.hint ?? ""]),
  )
  // 遍历实际存在的字段而不是固定清单，调用A新增字段时无需改前端即可显示
  const fieldOrder = mergeKeyOrder(
    KNOWN_PRODUCTION_FIELDS.map((field) => field.key),
    Object.keys(productionFields),
  )
  const hasAnyField = fieldOrder.length > 0

  const groups: DetailGroup[] = []

  groups.push({
    title: "生产消费字段",
    description: "搜索与推荐直接消费；修改会进入人工真值与正式标签快照。",
    emptyText: "这条结果没有存下调用A的生产字段，需重新评测后才能逐字段纠偏。",
    rows: fieldOrder.map(({ key, known }) => {
      const raw = productionFields[key]
      const spec = isPlainObject(fieldSpecs[key]) ? fieldSpecs[key] : {}
      const signalTone = SIGNAL_FIELDS[key]
      const hasSignal = Boolean(signalTone) && readableValue(raw) !== "—"
      const hint = typeof spec.hint === "string" && spec.hint.trim()
        ? spec.hint.trim()
        : knownFieldHints[key] || undefined
      const nodeKey = `call_a.${key}`
      const contractSpec = contractIndex.get(nodeKey)
      const humanValue = humanValues[nodeKey]
      return {
        label: resolveLabel(key, knownFieldLabels, spec.label),
        hint: known ? hint : hint || NEW_ITEM_HINT,
        value: readableValue(raw),
        isNew: !known,
        corrected: corrected.has(key) || corrected.has(`production_fields.${key}`),
        tone: hasSignal ? signalTone : "neutral",
        humanValue: humanValue === undefined ? undefined : readableValue(humanValue),
        correction: correctionTargetFor(contractSpec, raw),
        derivedNote: contractSpec ? undefined : "该字段未纳入本轮纠偏合同",
      }
    }),
  })

  const redlineTriggered = isPlainObject(source.redline_triggered)
    ? source.redline_triggered
    : {}
  const decisive = isPlainObject(source.decisive_evidence) ? source.decisive_evidence : {}
  const redlineEvidence = isPlainObject(decisive.redline_triggered)
    ? decisive.redline_triggered
    : {}
  // 同理遍历实际下发的红线信号，机制增删红线规则时自动跟随
  const redlineRows: DetailRow[] = mergeKeyOrder(
    Object.keys(REDLINE_LABELS),
    Object.keys(redlineTriggered),
  ).map(({ key, known }) => ({
    label: resolveLabel(key, REDLINE_LABELS),
    hint: known ? undefined : NEW_ITEM_HINT,
    isNew: !known,
    value: redlineTriggered[key] ? "命中" : "未命中",
    tone: redlineTriggered[key] ? "danger" : "success",
    evidence: stringList(redlineEvidence[key]),
    // 红线判定只读 production_fields.reason，这里是入库时的核对留痕。
    // 注意：纠偏「过滤原因」只写入字段值，不会重新套用红线封顶（既有设计），
    // 所以如实告知运营要改的是最终等级，别让人以为改了原因分数就会跟着变。
    derivedNote: "红线取自「过滤原因」；改该字段只留痕，要改结果请纠偏最终等级",
  }))
  if (redlineRows.length) {
    groups.push({
      title: "红线信号",
      description: "命中任一条即直接封顶或直出等级；实际判定取自「过滤原因」。",
      rows: redlineRows,
    })
  }

  // 缺陷本身就按实际命中列表遍历，机制新增缺陷类型会自动出现；未配中文名的标为新增项
  const defectRows: DetailRow[] = []
  const collectDefects = (
    keys: string[],
    rawEvidence: unknown,
    knownLabels: Record<string, string>,
    kind: string,
    tone: DetailTone,
  ) => {
    const evidenceList = Array.isArray(rawEvidence) ? rawEvidence : []
    for (const key of keys) {
      const entry = evidenceList.find((item) => isPlainObject(item) && item.key === key)
      const known = key in knownLabels
      defectRows.push({
        label: resolveLabel(key, knownLabels),
        value: "命中",
        tone,
        hint: known ? kind : `${kind} · ${NEW_ITEM_HINT}`,
        isNew: !known,
        evidence: isPlainObject(entry) ? stringList(entry.evidence) : [],
      })
    }
  }
  // 硬缺陷的纠偏节点是整份清单，所以先给一行可纠偏的汇总，再逐条列出证据。
  const hardDefectKeys = stringList(source.hard_defects)
  const hardDefectSpec = contractIndex.get("call_a.hard_defects")
  const hardDefectHuman = humanValues["call_a.hard_defects"]
  if (hardDefectSpec || hardDefectKeys.length) {
    defectRows.push({
      label: "硬缺陷清单",
      hint: "命中会压分或封顶",
      value: hardDefectKeys.length
        ? hardDefectKeys.map((key) => resolveLabel(key, HARD_DEFECT_LABELS)).join("、")
        : "未判定硬缺陷",
      tone: hardDefectKeys.length ? "danger" : "success",
      humanValue:
        hardDefectHuman === undefined
          ? undefined
          : stringList(hardDefectHuman)
              .map((key) => resolveLabel(key, HARD_DEFECT_LABELS))
              .join("、") || "未判定硬缺陷",
      correction: correctionTargetFor(hardDefectSpec, source.hard_defects ?? []),
      derivedNote: hardDefectSpec ? undefined : "本轮合同未开放硬缺陷纠偏",
    })
  }
  collectDefects(
    hardDefectKeys,
    decisive.hard_defects,
    HARD_DEFECT_LABELS,
    "硬缺陷",
    "danger",
  )
  collectDefects(
    stringList(source.image_defects),
    decisive.image_defects,
    IMAGE_DEFECT_LABELS,
    "图片缺陷",
    "warning",
  )
  groups.push({
    title: "缺陷判定",
    description: "硬缺陷会压分或封顶，图片缺陷只降权。",
    emptyText: "模型未判定任何硬缺陷或图片缺陷。",
    rows: defectRows,
  })

  const qualityRows: DetailRow[] = []
  const imageQuality = isPlainObject(source.image_quality) ? source.image_quality : {}
  if (imageQuality.quality_severity !== undefined) {
    const severity = String(imageQuality.quality_severity ?? "")
    qualityRows.push({
      label: "画质严重度",
      value: QUALITY_SEVERITY_LABELS[severity] ?? readableValue(severity),
      tone:
        severity === "severe" || severity === "unusable"
          ? "danger"
          : severity === "moderate"
            ? "warning"
            : severity === "uncertain"
              ? "neutral"
              : "success",
      evidence: stringList(imageQuality.evidence),
    })
  }
  const classification = isPlainObject(source.classification) ? source.classification : {}
  if (classification.primary_category !== undefined) {
    qualityRows.push({
      label: "一级分类",
      value: readableValue(classification.primary_category),
      evidence: stringList(classification.evidence),
    })
  }
  const decisionStatus = source.decision_status
  if (decisionStatus !== undefined) {
    qualityRows.push({
      label: "判定完整度",
      value: decisionStatus === "complete" ? "判断完整" : "存在无法确定项",
      tone: decisionStatus === "complete" ? "success" : "warning",
      evidence: stringList(source.uncertain_fields).map((field) => `无法确定：${field}`),
    })
  }
  if (qualityRows.length) {
    groups.push({
      title: "画质与分类判定",
      rows: qualityRows,
    })
  }

  const score = productionFields.score
  return {
    key: "A",
    title: "调用A",
    description: "读图产出生产字段、红线与缺陷信号；不决定最终等级。",
    headline: typeof score === "number" ? `${score} 分` : null,
    headlineHint: typeof score === "number" ? "调用A初步分" : null,
    headlineTone: "neutral",
    groups,
    unavailableReason: hasAnyField
      ? null
      : "这条结果没有存下调用A的字段结果，可能来自旧引擎，建议用当前版本重新评测。",
  }
}

/** 调用B：突出美感分，并列出模型给出的判断依据与逐维度规则命中 */
export function buildCallBSection(
  aesthetic: Record<string, unknown> | null | undefined,
  scoring: Record<string, unknown> | null | undefined,
  dimensionLabels: Record<string, string> = {},
  options: {
    contractIndex?: Map<string, ContractNodeSpec>
    humanValues?: Record<string, unknown>
  } = {},
): DetailSection {
  const source = isPlainObject(aesthetic) ? aesthetic : {}
  const scoringSource = isPlainObject(scoring) ? scoring : {}
  const contractIndex = options.contractIndex ?? new Map<string, ContractNodeSpec>()
  const humanValues = options.humanValues ?? {}
  const rawScore = source.aesthetic_score
  const aestheticScore = typeof rawScore === "number" && Number.isFinite(rawScore)
    ? rawScore
    : null

  const groups: DetailGroup[] = []

  // 美感分是撮合器的起点，也是运营最常质疑的一项，所以单列一行可直接纠偏。
  const aestheticSpec = contractIndex.get("call_b.aesthetic_score")
  const aestheticHuman = humanValues["call_b.aesthetic_score"]
  if (aestheticSpec || aestheticScore !== null) {
    groups.push({
      title: "美感分",
      description: "调用B给出的 0-100 分，等级撮合器以它为初始分。",
      rows: [
        {
          label: "调用B美感分",
          hint: "0-100 整数",
          value: aestheticScore === null ? "—" : `${aestheticScore} 分`,
          tone: "neutral",
          humanValue:
            aestheticHuman === undefined
              ? undefined
              : `${readableValue(aestheticHuman)} 分`,
          correction: correctionTargetFor(aestheticSpec, rawScore),
          derivedNote: aestheticSpec
            ? undefined
            : "本轮合同未开放美感分纠偏，可改维度规则间接影响",
        },
      ],
    })
  }

  const evidence = stringList(source.evidence)
  groups.push({
    title: "美感分判断依据",
    description: "模型给出这个分数的可见证据；证据站不住就该纠偏。",
    emptyText: "模型没有给出美感分证据，这种情况应当纠偏或退回。",
    rows: evidence.map((text, index) => ({
      label: `依据 ${index + 1}`,
      value: text,
    })),
  })

  const confidence = source.confidence
  const metaRows: DetailRow[] = []
  if (confidence !== undefined && confidence !== null) {
    metaRows.push({
      label: "模型置信度",
      value: percentLabel(confidence) ?? readableValue(confidence),
      tone: confidenceTone(confidence),
    })
  }
  if (source.bridge_version !== undefined) {
    metaRows.push({ label: "聚合版本", value: readableValue(source.bridge_version) })
  }
  if (source.schema_version !== undefined) {
    metaRows.push({ label: "结构版本", value: readableValue(source.schema_version) })
  }
  if (metaRows.length) {
    groups.push({ title: "调用B运行信息", rows: metaRows })
  }

  const dimensions = isPlainObject(source.dimensions) ? source.dimensions : {}
  const dimensionEvidence = isPlainObject(scoringSource.dimension_evidence)
    ? scoringSource.dimension_evidence
    : {}
  const deductions = isPlainObject(dimensionEvidence.deductions)
    ? dimensionEvidence.deductions
    : {}

  // 合同里的维度规则节点按维度归拢。只对模型输出里真实存在的维度开放逐条纠偏：
  // 合同可能包含该素材没跑的维度（子类目全集），对它们提交会被服务端重放拒绝。
  const ruleSpecsByDimension = new Map<string, ContractNodeSpec[]>()
  for (const spec of contractIndex.values()) {
    if (spec.nodeType !== "dimension_rule" || !spec.dimensionKey || !spec.ruleId) {
      continue
    }
    const list = ruleSpecsByDimension.get(spec.dimensionKey)
    if (list) list.push(spec)
    else ruleSpecsByDimension.set(spec.dimensionKey, [spec])
  }

  const dimensionRows: DetailRow[] = []
  for (const [key, rawDimension] of Object.entries(dimensions)) {
    if (!isPlainObject(rawDimension)) continue
    const hits = Array.isArray(rawDimension.hit_rules) ? rawDimension.hit_rules : []
    const bonusHits = Array.isArray(rawDimension.hit_bonus_rules)
      ? rawDimension.hit_bonus_rules
      : []
    const deduction = deductions[key]
    // 维度标签三级回落：机制自带 label → 调用方传入的映射 → 原始键
    const schemaLabel = readableValue(rawDimension.label)
    const hasLabel = Boolean(dimensionLabels[key]) || schemaLabel !== "—"
    const dimensionLabel = dimensionLabels[key] || (schemaLabel === "—" ? key : schemaLabel)
    const ruleSpecs = ruleSpecsByDimension.get(key)

    if (ruleSpecs?.length) {
      // 合同覆盖了该维度：一条配置规则一行，未命中的也列出来——
      // 模型漏判时运营才有落点把它改成命中。
      const matchedHits = new Set<string>()
      for (const spec of ruleSpecs) {
        const pool = spec.ruleKind === "bonus" ? bonusHits : hits
        const hit = pool.find(
          (item) => isPlainObject(item) && item.rule_id === spec.ruleId,
        ) ?? null
        if (hit) matchedHits.add(`${spec.ruleKind}:${spec.ruleId}`)
        const humanValue = humanValues[spec.nodeKey]
        dimensionRows.push({
          label: spec.label || `${dimensionLabel}：${spec.ruleId}`,
          value: ruleHitSummary(hit, spec.ruleKind),
          tone: hit ? (spec.ruleKind === "bonus" ? "success" : "warning") : "neutral",
          evidence: isPlainObject(hit) ? stringList(hit.evidence) : undefined,
          humanValue:
            humanValue === undefined
              ? undefined
              : humanRuleHitSummary(humanValue),
          correction: correctionTargetFor(spec, hit),
        })
      }
      // 命中了但合同没收录的规则（数据与合同漂移）：宁可只读展示也不静默丢弃。
      const collectStray = (pool: unknown[], ruleKind: "deduction" | "bonus") => {
        for (const hit of pool) {
          if (!isPlainObject(hit)) continue
          const ruleId = readableValue(hit.rule_id)
          if (matchedHits.has(`${ruleKind}:${hit.rule_id}`)) continue
          dimensionRows.push({
            label: `${dimensionLabel}：${ruleId}`,
            hint: `${NEW_ITEM_HINT} · 合同未收录该规则`,
            isNew: true,
            value: ruleHitSummary(hit, ruleKind),
            tone: ruleKind === "bonus" ? "success" : "warning",
            evidence: stringList(hit.evidence),
          })
        }
      }
      collectStray(hits, "deduction")
      collectStray(bonusHits, "bonus")
      continue
    }

    // 合同未覆盖该维度（旧结果或只读入口）：保持一维度一行的汇总展示
    const evidenceLines: string[] = []
    const collectHits = (rawHits: unknown[], isBonus: boolean) => {
      for (const hit of rawHits) {
        if (!isPlainObject(hit)) continue
        const parts = [readableValue(hit.rule_id)]
        const delta = hit.deduction ?? hit.bonus ?? hit.value
        if (typeof delta === "number") {
          parts.push(isBonus ? `加 ${delta} 分` : `扣 ${delta} 分`)
        }
        if (hit.confidence !== undefined) {
          parts.push(`置信度 ${ruleConfidenceLabel(hit.confidence)}`)
        }
        const header = parts.join(" · ")
        const detail = readableValue(hit.evidence)
        evidenceLines.push(detail === "—" ? header : `${header}：${detail}`)
      }
    }
    collectHits(hits, false)
    collectHits(bonusHits, true)
    const hitCount = hits.length + bonusHits.length
    dimensionRows.push({
      label: dimensionLabel,
      hint: hasLabel ? undefined : NEW_ITEM_HINT,
      isNew: !hasLabel,
      value: hitCount
        ? `命中 ${hitCount} 条${typeof deduction === "number" ? ` · 合计扣 ${deduction} 分` : ""}`
        : "未命中",
      tone: hitCount ? "warning" : "success",
      evidence: evidenceLines,
    })
  }
  groups.push({
    title: "逐维度规则命中",
    description: "每条命中都必须有可定位的证据；扣分由服务端按规则合同累计。",
    emptyText: "本次没有按维度规则命中的记录。",
    rows: dimensionRows,
    note:
      typeof dimensionEvidence.applied_deduction_total === "number"
        ? `应用扣分合计 ${dimensionEvidence.applied_deduction_total} 分${
            dimensionEvidence.clamped_to_dimension_max ? "（已封顶到维度满分）" : ""
          }`
        : undefined,
  })

  return {
    key: "B",
    title: "调用B",
    description: "只负责美感判断，产出 0-100 美感分与逐维度规则证据。",
    headline: aestheticScore === null ? null : `${aestheticScore} 分`,
    headlineHint: aestheticScore === null ? null : "美感分（撮合器初始分）",
    headlineTone: "neutral",
    groups,
    unavailableReason:
      aestheticScore === null && !dimensionRows.length
        ? "这条结果没有调用B的美感判断，可能被判为范围外或调用B未执行。"
        : null,
  }
}

/**
 * 从冻结的 v3 合同里读出赛道的中文名与上限。
 *
 * 赛道由机制合同定义，增删赛道不需要改前端——这里只做读取。
 */
export function resolveTrackInfo(
  scoring: Record<string, unknown> | null | undefined,
  trackKey: unknown,
): { label: string; cap: number | null; known: boolean } {
  const key = typeof trackKey === "string" ? trackKey : ""
  const source = isPlainObject(scoring) ? scoring : {}
  const context = isPlainObject(source.v3_context) ? source.v3_context : {}
  const contract = isPlainObject(context.contract) ? context.contract : {}
  const block = isPlainObject(contract.track_classification)
    ? contract.track_classification
    : {}
  const tracks = Array.isArray(block.tracks) ? block.tracks : []
  const matched = tracks.find(
    (track) => isPlainObject(track) && String(track.key ?? "") === key,
  )
  if (isPlainObject(matched)) {
    const cap = matched.track_cap
    return {
      label: typeof matched.label === "string" && matched.label.trim()
        ? matched.label.trim()
        : key,
      cap: typeof cap === "number" && Number.isFinite(cap) ? cap : null,
      known: true,
    }
  }
  return { label: key || "—", cap: null, known: tracks.length === 0 }
}

/** 等级撮合器：逐条列出判断项与结果，让运营看懂分数是怎么走到这个等级的 */
export function buildMatcherSection(
  scoring: Record<string, unknown> | null | undefined,
  options: {
    contractIndex?: Map<string, ContractNodeSpec>
    humanValues?: Record<string, unknown>
  } = {},
): DetailSection {
  const source = isPlainObject(scoring) ? scoring : {}
  const contractIndex = options.contractIndex ?? new Map<string, ContractNodeSpec>()
  const humanValues = options.humanValues ?? {}
  const steps = Array.isArray(source.steps) ? source.steps : []
  const caps = Array.isArray(source.caps) ? source.caps : []

  const groups: DetailGroup[] = []

  const contextRows: DetailRow[] = []
  if (source.track_key !== undefined) {
    const track = resolveTrackInfo(scoring, source.track_key)
    contextRows.push({
      label: "赛道归属",
      value: track.label,
      hint: track.cap === null
        ? track.known
          ? "决定封顶上限"
          : `${NEW_ITEM_HINT} · 合同未收录该赛道`
        : `赛道上限 ${track.cap} 分`,
      isNew: !track.known,
      humanValue:
        humanValues["v3.track_key"] === undefined
          ? undefined
          : resolveTrackInfo(scoring, humanValues["v3.track_key"]).label,
      correction: correctionTargetFor(
        contractIndex.get("v3.track_key"),
        source.track_key,
      ),
    })
  }
  if (source.initial_score !== undefined && source.initial_score !== null) {
    contextRows.push({
      label: "初始分",
      value: readableValue(source.initial_score),
      hint: "来自调用B美感分",
    })
  }
  if (source.base_score !== undefined && source.base_score !== null) {
    contextRows.push({
      label: "基准分",
      value: readableValue(source.base_score),
      hint: "旧口径基准",
    })
  }
  if (source.dimension_scoring_mode !== undefined) {
    contextRows.push({
      label: "维度计分方式",
      value:
        source.dimension_scoring_mode === "rule_deduction"
          ? "规则命中扣分"
          : readableValue(source.dimension_scoring_mode),
    })
  }
  if (contextRows.length) {
    groups.push({ title: "撮合输入", rows: contextRows })
  }

  // 按服务端实际下发的步骤逐条列出：机制增删判定规则时自动跟随，
  // 未配中文名的步骤显示原始键并标为新增项，绝不静默丢弃。
  groups.push({
    title: "逐步判断链",
    description: "按服务端确定性顺序执行；每步都给出执行后的分数。",
    emptyText: "这条结果没有存下撮合器的分步记录，无法复盘分数来源。",
    rows: steps.flatMap((step): DetailRow[] => {
      if (!isPlainObject(step)) {
        // 兼容纯字符串或异常形态的步骤，宁可显示原文也不丢
        const text = readableValue(step)
        return text === "—" ? [] : [{ label: "未命名步骤", value: text, isNew: true }]
      }
      const key = String(step.step ?? "")
      const known = key in STEP_LABELS
      const scoreAfter = step.score_after
      const skipped = NEUTRAL_STEPS.has(key) || key.endsWith("_skipped")
      return [{
        label: resolveLabel(key, STEP_LABELS),
        hint: known ? undefined : NEW_ITEM_HINT,
        isNew: !known,
        value:
          typeof scoreAfter === "number" && Number.isFinite(scoreAfter)
            ? `${scoreAfter} 分`
            : readableValue(scoreAfter),
        tone: skipped ? "neutral" : "active",
        evidence: stringList(step.note),
        // 每一步都是服务端按冻结规则算出的，直接改它会让结果与评分引擎脱钩。
        derivedNote: "由撮合器算出，请纠偏上游的调用A或调用B判断",
      }]
    }),
  })

  // caps 的真实结构是 {cap, reason}；部分历史数据里 cap 直接是字符串。
  // 上限数值只出现在 reason 文案里，所以这里不臆造 score_cap 之类的字段。
  groups.push({
    title: "封顶与否决",
    description: "任一封顶生效后，分数不会超过对应上限。",
    emptyText: "本次没有触发任何封顶或否决。",
    rows: caps.flatMap((cap): DetailRow[] => {
      if (!isPlainObject(cap)) {
        const text = readableValue(cap)
        if (text === "—") return []
        return [{
          label: resolveLabel(text, CAP_LABELS),
          value: "已生效",
          tone: "danger" as DetailTone,
          isNew: !(text in CAP_LABELS),
        }]
      }
      const key = String(cap.cap ?? "")
      const known = key in CAP_LABELS
      return [{
        label: resolveLabel(key, CAP_LABELS, cap.label),
        hint: known ? undefined : NEW_ITEM_HINT,
        isNew: !known,
        derivedNote: "由撮合器算出，请纠偏触发它的上游判断",
        value: "已生效",
        tone: "danger" as DetailTone,
        evidence: stringList(cap.reason),
      }]
    }),
  })

  // 阈值表由机制合同冻结，档位增删（不止 L1–L5）都按实际下发内容展示
  const context = isPlainObject(source.v3_context) ? source.v3_context : {}
  const contract = isPlainObject(context.contract) ? context.contract : {}
  const thresholds = Array.isArray(contract.level_thresholds)
    ? contract.level_thresholds
    : []
  if (thresholds.length) {
    const currentLevel = readableValue(source.level)
    groups.push({
      title: "等级分数阈值",
      description: "本轮冻结的分数到等级映射；阈值只能通过候选机制版本修改。",
      rows: thresholds.flatMap((item) => {
        if (!isPlainObject(item)) return []
        const level = readableValue(item.level)
        const min = item.min_score
        return [{
          label: level,
          value: typeof min === "number" ? `≥ ${min} 分` : readableValue(min),
          tone: level === currentLevel ? "active" as DetailTone : undefined,
          hint: level === currentLevel ? "本次落档" : undefined,
        }]
      }),
    })
  }

  const resultRows: DetailRow[] = []
  if (source.score !== undefined) {
    resultRows.push({
      label: "最终分数",
      value: readableValue(source.score),
      hint: "服务端权威计算",
    })
  }
  if (source.level !== undefined) {
    resultRows.push({
      label: "最终等级",
      value: readableValue(source.level),
      hint: "L1 最好、L5 最差",
      tone: "active",
      humanValue:
        humanValues["v3.final_level"] === undefined
          ? undefined
          : readableValue(humanValues["v3.final_level"]),
      correction: correctionTargetFor(
        contractIndex.get("v3.final_level"),
        source.level,
      ),
    })
  }
  if (source.raw_level !== undefined && source.raw_level !== source.level) {
    resultRows.push({
      label: "未压分前等级",
      value: readableValue(source.raw_level),
      hint: "封顶前本应落在这一档",
      tone: "warning",
    })
  }
  if (resultRows.length) {
    groups.push({
      title: "撮合结果",
      rows: resultRows,
      note: "等级只由服务端评分引擎按冻结规则计算，人工纠偏后会重算。",
    })
  }

  const level = source.level
  return {
    key: "V3",
    title: "等级撮合器",
    description: "用冻结规则把调用A、调用B的结论撮合成最终分数与等级。",
    headline: level === undefined || level === null ? null : readableValue(level),
    headlineHint:
      typeof source.score === "number" ? `${source.score} / 100` : "最终等级",
    headlineTone: "active",
    groups,
    unavailableReason: steps.length
      ? null
      : "这条结果缺少撮合器的分步上下文，无法确定性复盘，建议用当前机制版本重跑。",
  }
}

/**
 * 从纠偏历史里取出每个节点最近一次的人工值。
 *
 * 历史是追加写的，同一节点可以反复纠偏，所以后写的覆盖先写的——列表里要对照的是
 * 「模型判断」与「人工最新结论」，不是中间过程。
 */
export function humanValuesFromHistory(
  history: readonly unknown[] | null | undefined,
): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  if (!Array.isArray(history)) return values
  for (const entry of history) {
    if (!isPlainObject(entry)) continue
    const path = String(entry.node_path ?? "").trim()
    if (!path) continue
    values[nodeKeyForPath(path)] = entry.new_value
  }
  return values
}

/** 把服务端的 node_path 折算成前端用的 node_key */
function nodeKeyForPath(path: string): string {
  if (path.startsWith("production_fields.")) {
    return `call_a.${path.slice("production_fields.".length)}`
  }
  if (path === "precheck.hard_defects") return "call_a.hard_defects"
  if (
    path === "aesthetic.aesthetic_score"
    || path === "aesthetic_score"
    || path === "call_b.aesthetic_score"
  ) {
    return "call_b.aesthetic_score"
  }
  const dimensionRule = path.match(
    /^dimension\.([^.]+)\.hit_(?:bonus_)?rules\.([^.]+)$/,
  )
  if (dimensionRule) return `call_b.${dimensionRule[1]}.${dimensionRule[2]}`
  if (path === "track" || path === "track_key" || path === "scoring.track_key") {
    return "v3.track_key"
  }
  if (path === "level" || path === "scoring.level") return "v3.final_level"
  return path
}

/** 组装三段。顺序固定为调用A → 调用B → 等级撮合器，与实际执行顺序一致。 */
export function buildEvaluationDetailSections(input: {
  precheck?: Record<string, unknown> | null
  aesthetic?: Record<string, unknown> | null
  scoring?: Record<string, unknown> | null
  dimensionLabels?: Record<string, string>
  correctedFieldKeys?: readonly string[]
  /** 机制下发的调用A字段规格；不传时回落到前端已知清单 */
  fieldSpecs?: Record<string, unknown> | null
  /** 冻结合同节点，决定哪一行可点击纠偏；不传则全部只读 */
  contractNodes?: readonly unknown[] | null
  /** 纠偏历史，用于在列表里并列展示人工结论 */
  correctionHistory?: readonly unknown[] | null
}): DetailSection[] {
  const contractIndex = contractCorrectionIndex(input.contractNodes)
  const humanValues = humanValuesFromHistory(input.correctionHistory)
  return [
    buildCallASection(input.precheck, {
      correctedFieldKeys: input.correctedFieldKeys,
      fieldSpecs: input.fieldSpecs,
      contractIndex,
      humanValues,
    }),
    buildCallBSection(input.aesthetic, input.scoring, input.dimensionLabels ?? {}, {
      contractIndex,
      humanValues,
    }),
    buildMatcherSection(input.scoring, { contractIndex, humanValues }),
  ]
}

/**
 * 从冻结纠偏合同的 A 层节点提取调用A字段规格。
 *
 * 合同是字段中文名的权威源：机制新增生产字段时，后端在合同里已经带上了 label 与说明，
 * 前端据此直接显示中文名，不必等着往 KNOWN_PRODUCTION_FIELDS 补一行。
 */
/** 合同节点解析出的可纠偏规格 */
export type ContractNodeSpec = {
  nodeKey: string
  nodeType: RowCorrectionTarget["nodeType"]
  nodePath: string
  valueKind: RowValueKind
  label?: string
  hint?: string
  options?: Array<{ value: string; label: string }>
  minimum?: number
  maximum?: number
  /** dimension_rule 专用：节点归属的维度、规则与加/扣分方向 */
  dimensionKey?: string
  ruleId?: string
  ruleKind?: "deduction" | "bonus"
  readOnly: boolean
  recomputes: boolean
}

const CONTRACT_RUNTIME_TYPES = new Set([
  "call_a_field",
  "precheck_field",
  "aesthetic_score",
  "dimension_rule",
  "track",
  "final_level",
])

function valueKindFromContractType(
  type: string,
  hasOptions: boolean,
): RowValueKind {
  const normalized = type.toLowerCase()
  if (normalized === "enum" || normalized === "enumeration") return "enum"
  if (normalized === "rule_hit") return "rule_hit"
  if (normalized === "integer" || normalized === "int") return "integer"
  if (normalized === "list" || normalized === "array") {
    return hasOptions ? "multi_enum" : "string_list"
  }
  return "text"
}

/**
 * 把冻结合同的节点索引成「哪一行可以纠偏」。
 *
 * 可纠偏与否完全由合同决定，不由前端写死：机制新增一个节点，对应的信息行就自动
 * 变成可点击纠偏；机制撤掉节点，那一行自动回到只读。
 */
export function contractCorrectionIndex(
  nodes: readonly unknown[] | null | undefined,
): Map<string, ContractNodeSpec> {
  const index = new Map<string, ContractNodeSpec>()
  if (!Array.isArray(nodes)) return index
  for (const node of nodes) {
    if (!isPlainObject(node)) continue
    const nodeKey = String(node.node_key ?? "").trim()
    if (!nodeKey) continue
    const metadata = isPlainObject(node.metadata) ? node.metadata : {}
    const runtimeType = String(metadata.node_type ?? "").trim()
    if (!CONTRACT_RUNTIME_TYPES.has(runtimeType)) continue

    const rawOptions = Array.isArray(node.options)
      ? node.options
      : Array.isArray(node.allowed_values)
        ? node.allowed_values
        : []
    const optionLabels = isPlainObject(metadata.option_labels)
      ? metadata.option_labels
      : {}
    const options = rawOptions.flatMap((item) => {
      const value = typeof item === "string" ? item : String(item ?? "")
      if (!value) return []
      const label = optionLabels[value]
      return [{ value, label: typeof label === "string" && label ? label : value }]
    })

    const editable = node.editable !== false
      && metadata.editable !== false
      && node.read_only !== true

    index.set(nodeKey, {
      nodeKey,
      nodeType: runtimeType as RowCorrectionTarget["nodeType"],
      nodePath: String(node.path ?? nodeKey),
      valueKind: valueKindFromContractType(
        String(node.type ?? "text"),
        options.length > 0,
      ),
      label: typeof node.label === "string" && node.label.trim()
        ? node.label.trim()
        : undefined,
      hint: typeof node.description === "string" && node.description.trim()
        ? node.description.trim()
        : undefined,
      options: options.length ? options : undefined,
      minimum: typeof node.minimum === "number" ? node.minimum : undefined,
      maximum: typeof node.maximum === "number" ? node.maximum : undefined,
      dimensionKey:
        typeof metadata.dimension_key === "string" && metadata.dimension_key
          ? metadata.dimension_key
          : undefined,
      ruleId:
        typeof metadata.rule_id === "string" && metadata.rule_id
          ? metadata.rule_id
          : undefined,
      ruleKind:
        metadata.rule_kind === "bonus"
          ? "bonus"
          : metadata.rule_kind === "deduction"
            ? "deduction"
            : undefined,
      readOnly: !editable,
      // 与服务端 apply_node_correction 的 downstream_recomputed 严格对齐，
      // 否则弹窗会向运营承诺一次它其实不会做的重算：
      // - call_a 只有 score 会按分数重算等级，其余字段只落库；
      // - final_level 是人工覆盖等级，不重跑评分链路。
      recomputes:
        runtimeType === "call_a_field"
          ? nodeKey === "call_a.score"
          : runtimeType !== "final_level",
    })
  }
  return index
}

/** 把合同规格与该行的当前值组合成可提交的纠偏目标 */
function correctionTargetFor(
  spec: ContractNodeSpec | undefined,
  currentValue: unknown,
): RowCorrectionTarget | undefined {
  if (!spec || spec.readOnly) return undefined
  return {
    nodeType: spec.nodeType,
    nodePath: spec.nodePath,
    valueKind: spec.valueKind,
    currentValue,
    options: spec.options,
    minimum: spec.minimum,
    maximum: spec.maximum,
    hint: spec.hint,
    ruleId: spec.ruleId,
    recomputes: spec.recomputes,
  }
}

export function fieldSpecsFromContractNodes(
  nodes: readonly unknown[] | null | undefined,
): Record<string, { label?: string; hint?: string }> {
  if (!Array.isArray(nodes)) return {}
  const specs: Record<string, { label?: string; hint?: string }> = {}
  for (const node of nodes) {
    if (!isPlainObject(node) || node.layer !== "A") continue
    const metadata = isPlainObject(node.metadata) ? node.metadata : {}
    const fieldKey = typeof metadata.field_key === "string" && metadata.field_key.trim()
      ? metadata.field_key.trim()
      : String(node.node_key ?? "").replace(/^call_a\./, "")
    if (!fieldKey) continue
    const label = typeof node.label === "string" && node.label.trim()
      ? node.label.trim()
      : undefined
    const hint = typeof node.description === "string" && node.description.trim()
      ? node.description.trim()
      : undefined
    specs[fieldKey] = { label, hint }
  }
  return specs
}

export const detailSectionOrder: readonly DetailSectionKey[] = ["A", "B", "V3"]

export const detailSectionTitles: Record<DetailSectionKey, string> = {
  A: "调用A",
  B: "调用B",
  V3: "等级撮合器",
}
