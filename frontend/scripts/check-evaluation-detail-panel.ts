import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
  buildEvaluationDetailSections,
  detailSectionOrder,
  detailSectionTitles,
  fieldSpecsFromContractNodes,
  readableValue,
} from "../src/features/evaluation-detail/detail-model.ts"

/**
 * 人工纠偏三段式评测细节合同。
 *
 * 锁三件事：段的顺序与命名、每段必须列出评测细节（不只是输入框）、两个纠偏入口都已接入。
 */

// —— 1. 三段顺序与中文命名固定 ——
assert.deepEqual(detailSectionOrder, ["A", "B", "V3"])
assert.equal(detailSectionTitles.A, "调用A")
assert.equal(detailSectionTitles.B, "调用B")
assert.equal(detailSectionTitles.V3, "等级撮合器")

// —— 2. 用一条贴近真实的评测结果验证三段内容 ——
const sections = buildEvaluationDetailSections({
  precheck: {
    production_fields: {
      title: "现代简约客厅",
      seotitle: "现代简约客厅设计效果图",
      category: "客厅,大平层",
      style: "现代简约",
      tags: ["客厅", "现代", "简约", "米色"],
      cons: "沙发背景墙略空",
      design: "以米色为主调",
      score: 82,
      reason: [],
      image_defects: "",
      trait: "3D数字效果图",
    },
    redline_triggered: {
      screenshot: false,
      casual_photo: false,
      text_heavy: true,
      qr_code_heavy: false,
    },
    decisive_evidence: {
      redline_triggered: { text_heavy: ["画面下方三分之一为文字排版"] },
      hard_defects: [{ key: "blurry_grayish", evidence: "整体发灰" }],
      image_defects: [],
    },
    hard_defects: ["blurry_grayish"],
    image_defects: [],
    image_quality: { quality_severity: "moderate" },
    classification: { primary_category: "室内空间" },
    decision_status: "complete",
    uncertain_fields: [],
  },
  aesthetic: {
    aesthetic_score: 78,
    evidence: ["构图均衡", "色彩关系克制"],
    confidence: 0.86,
    dimensions: {
      composition: {
        hit_rules: [
          { rule_id: "subject_offset", deduction: 4, confidence: "high", evidence: "主体偏左" },
        ],
      },
      color: { hit_rules: [] },
    },
  },
  scoring: {
    track_key: "interior",
    initial_score: 78,
    dimension_scoring_mode: "rule_deduction",
    dimension_evidence: {
      applied_deduction_total: 4,
      clamped_to_dimension_max: false,
      deductions: { composition: 4 },
    },
    steps: [
      { step: "redline", score_after: 78, note: "红线命中 text_heavy，总分封顶至 60" },
      { step: "b_aesthetic_foundation", score_after: 78, note: "等级撮合器以调用B aesthetic_score 作为初始分" },
      { step: "dimension_rule_deduction", score_after: 74, note: "维度扣分（规则命中）：应用扣分 4" },
      { step: "veto_skipped", score_after: 74, note: "未触发高分否决" },
      { step: "level", score_after: 60, note: "分数 60 → L4（未压分前为 L3）" },
    ],
    caps: [{ cap: "redline", reason: "红线命中 text_heavy，总分封顶至 60" }],
    score: 60,
    level: "L4",
    raw_level: "L3",
  },
  dimensionLabels: { composition: "构图秩序", color: "色彩关系" },
  correctedFieldKeys: ["title"],
})

assert.deepEqual(sections.map((section) => section.key), ["A", "B", "V3"])

// 调用A：逐字段列出结果，红线与缺陷带证据
const [callA, callB, matcher] = sections
assert.equal(callA.headline, "82 分")
assert.equal(callA.unavailableReason, null)
const productionGroup = callA.groups.find((group) => group.title === "生产消费字段")
assert.ok(productionGroup, "调用A必须列出生产消费字段")
assert.equal(productionGroup.rows.length, 11, "11 个生产字段应逐项列出")
assert.equal(productionGroup.rows[0].label, "素材标题")
assert.equal(productionGroup.rows[0].value, "现代简约客厅")
assert.equal(productionGroup.rows[0].corrected, true, "已纠偏字段要打标")
assert.equal(
  productionGroup.rows.find((row) => row.label === "素材标签")?.value,
  "客厅、现代、简约、米色",
  "列表字段要转成可读文本",
)
const redlineGroup = callA.groups.find((group) => group.title === "红线信号")
assert.ok(redlineGroup, "调用A必须列出红线信号")
const textHeavy = redlineGroup.rows.find((row) => row.label === "有大面积文字说明")
assert.equal(textHeavy?.value, "命中")
assert.equal(textHeavy?.tone, "danger")
assert.deepEqual(textHeavy?.evidence, ["画面下方三分之一为文字排版"])
const defectGroup = callA.groups.find((group) => group.title === "缺陷判定")
assert.ok(defectGroup?.rows.some((row) => row.label === "模糊发灰"), "硬缺陷要翻译成中文")

// 调用B：美感分要作为段头结论，并列出判断依据
assert.equal(callB.headline, "78 分")
assert.match(String(callB.headlineHint), /美感分/)
const evidenceGroup = callB.groups.find((group) => group.title === "美感分判断依据")
assert.ok(evidenceGroup, "调用B必须列出美感分判断依据")
assert.deepEqual(
  evidenceGroup.rows.map((row) => row.value),
  ["构图均衡", "色彩关系克制"],
)
const ruleGroup = callB.groups.find((group) => group.title === "逐维度规则命中")
assert.ok(ruleGroup, "调用B必须列出逐维度规则命中")
const composition = ruleGroup.rows.find((row) => row.label === "构图秩序")
assert.equal(composition?.value, "命中 1 条 · 合计扣 4 分")
assert.ok(
  composition?.evidence?.[0]?.includes("主体偏左"),
  "规则命中要带可定位证据",
)
assert.equal(ruleGroup.rows.find((row) => row.label === "色彩关系")?.value, "未命中")

// 等级撮合器：逐判断项与结果，封顶原因单列
assert.equal(matcher.headline, "L4")
const stepGroup = matcher.groups.find((group) => group.title === "逐步判断链")
assert.ok(stepGroup, "撮合器必须列出逐步判断链")
assert.deepEqual(
  stepGroup.rows.map((row) => row.label),
  ["红线判断", "调用B美感基础分", "维度扣分（规则命中）", "高分否决（未触发）", "分数转等级"],
  "每一步都要有中文名",
)
assert.equal(stepGroup.rows[0].value, "78 分", "每步都要给出执行后分数")
const capGroup = matcher.groups.find((group) => group.title === "封顶与否决")
assert.ok(capGroup, "撮合器必须列出封顶与否决")
assert.equal(capGroup.rows[0].label, "红线封顶")
// caps 的真实结构只有 {cap, reason}，上限数值在 reason 文案里，不得臆造 score_cap 字段
assert.equal(capGroup.rows[0].value, "已生效")
assert.ok(
  capGroup.rows[0].evidence?.[0]?.includes("封顶至 60"),
  "封顶上限来自 reason 文案",
)
const resultGroup = matcher.groups.find((group) => group.title === "撮合结果")
assert.equal(resultGroup?.rows.find((row) => row.label === "最终等级")?.value, "L4")
assert.equal(
  resultGroup?.rows.find((row) => row.label === "未压分前等级")?.value,
  "L3",
  "被压分时要显示未压分前等级，否则运营看不出封顶影响",
)

// —— 3. 数据缺失要给原因，不能只给空表（技术失败与业务误判分离）——
const emptySections = buildEvaluationDetailSections({})
for (const section of emptySections) {
  assert.ok(
    section.unavailableReason,
    `${section.title} 数据缺失时必须说明原因而不是空表`,
  )
}

// 空值统一显示为「—」，不能漏出 undefined
assert.equal(readableValue(undefined), "—")
assert.equal(readableValue(null), "—")
assert.equal(readableValue([]), "—")
assert.equal(readableValue(""), "—")
assert.equal(readableValue(true), "是")

// —— 4. 自适应：机制增删字段、规则、赛道时前端不改代码也能显示 ——
// 用一条「全是前端没见过的新东西」的评测结果来验证。
const adaptive = buildEvaluationDetailSections({
  precheck: {
    production_fields: {
      title: "已知字段仍在最前",
      // 机制新增的两个生产字段，前端从未认识过
      material_tags: ["实木", "黄铜"],
      spatial_layout: "L 型动线",
    },
    // 机制新增的红线信号
    redline_triggered: { screenshot: false, ai_watermark: true },
    decisive_evidence: {
      redline_triggered: { ai_watermark: ["右下角有生成水印"] },
      hard_defects: [{ key: "unseen_defect_kind", evidence: "新缺陷证据" }],
      image_defects: [],
    },
    // 机制新增的硬缺陷类型
    hard_defects: ["unseen_defect_kind"],
    image_defects: [],
  },
  aesthetic: {
    aesthetic_score: 70,
    evidence: ["证据"],
    // 机制新增的维度
    dimensions: { unseen_dimension: { hit_rules: [{ rule_id: "new_rule", deduction: 3 }] } },
  },
  scoring: {
    // 机制新增的赛道，且合同里已收录它
    track_key: "class_four",
    steps: [
      { step: "redline", score_after: 70, note: "已知步骤" },
      // 机制新增的判定规则
      { step: "brand_conflict_check", score_after: 65, note: "品牌冲突检查未通过" },
      // 历史脏数据：步骤不是对象
      "legacy_plain_step",
    ],
    caps: [
      // 机制新增的封顶类型
      { cap: "brand_conflict_cap", reason: "品牌冲突封顶至 65" },
      // 历史脏数据：cap 是纯字符串（见 baseline_regression.py 的兼容分支）
      "redline",
    ],
    score: 65,
    level: "L3",
    v3_context: {
      contract: {
        track_classification: {
          tracks: [{ key: "class_four", label: "四类（新增赛道）", track_cap: 88 }],
        },
        // 阈值档位数量也可变，不假定固定 L1–L5
        level_thresholds: [
          { min_score: 81, level: "L1" },
          { min_score: 61, level: "L3" },
          { min_score: 0, level: "L5" },
        ],
      },
    },
  },
  fieldSpecs: {
    // 机制下发字段规格时，前端优先用它给新字段配中文名
    material_tags: { label: "材质标签", hint: "至少 1 个" },
  },
})

const [adaptiveA, adaptiveB, adaptiveMatcher] = adaptive

// 新增生产字段自动追加在已知字段之后，不能被丢掉
const adaptiveFields = adaptiveA.groups.find((group) => group.title === "生产消费字段")
assert.ok(adaptiveFields)
assert.deepEqual(
  adaptiveFields.rows.map((row) => row.label),
  ["素材标题", "材质标签", "spatial_layout"],
  "已知字段保持顺序在前，新增字段追加在后",
)
assert.equal(adaptiveFields.rows[1].hint, "至少 1 个", "机制下发的 hint 要用上")
assert.equal(adaptiveFields.rows[1].isNew, true, "新增字段要标记为新增")
assert.equal(
  adaptiveFields.rows[2].label,
  "spatial_layout",
  "没有 label 时回落到原始键，便于运营反馈缺名字",
)
assert.equal(adaptiveFields.rows[2].hint, "机制新增项")
assert.equal(adaptiveFields.rows[0].isNew, false, "已知字段不应被标为新增")

// 新增红线信号自动出现
const adaptiveRedline = adaptiveA.groups.find((group) => group.title === "红线信号")
const aiWatermark = adaptiveRedline?.rows.find((row) => row.label === "ai_watermark")
assert.ok(aiWatermark, "机制新增的红线信号必须显示出来")
assert.equal(aiWatermark.value, "命中")
assert.equal(aiWatermark.isNew, true)
assert.deepEqual(aiWatermark.evidence, ["右下角有生成水印"])

// 新增硬缺陷类型自动出现
const adaptiveDefects = adaptiveA.groups.find((group) => group.title === "缺陷判定")
const newDefect = adaptiveDefects?.rows.find((row) => row.label === "unseen_defect_kind")
assert.ok(newDefect, "机制新增的缺陷类型必须显示出来")
assert.equal(newDefect.isNew, true)
assert.match(String(newDefect.hint), /硬缺陷/)

// 新增维度自动出现
const adaptiveRules = adaptiveB.groups.find((group) => group.title === "逐维度规则命中")
const newDimension = adaptiveRules?.rows.find((row) => row.label === "unseen_dimension")
assert.ok(newDimension, "机制新增的维度必须显示出来")
assert.equal(newDimension.isNew, true)

// 新增判定规则自动出现；脏数据不丢
const adaptiveSteps = adaptiveMatcher.groups.find((group) => group.title === "逐步判断链")
assert.ok(adaptiveSteps)
assert.deepEqual(
  adaptiveSteps.rows.map((row) => row.label),
  ["红线判断", "brand_conflict_check", "未命名步骤"],
  "新增判定规则与历史脏数据都要显示，不能静默丢弃",
)
assert.equal(adaptiveSteps.rows[1].isNew, true)
assert.equal(adaptiveSteps.rows[1].value, "65 分")

// 新增封顶类型自动出现；字符串形态的 cap 也不丢
const adaptiveCaps = adaptiveMatcher.groups.find((group) => group.title === "封顶与否决")
assert.ok(adaptiveCaps)
assert.deepEqual(
  adaptiveCaps.rows.map((row) => row.label),
  ["brand_conflict_cap", "红线封顶"],
  "新增封顶与字符串形态的 cap 都要显示",
)
assert.equal(adaptiveCaps.rows[0].isNew, true)

// 新增赛道从合同里读出中文名与上限
const adaptiveInput = adaptiveMatcher.groups.find((group) => group.title === "撮合输入")
const trackRow = adaptiveInput?.rows.find((row) => row.label === "赛道归属")
assert.ok(trackRow, "撮合输入必须列出赛道归属")
assert.equal(
  trackRow.value,
  "四类（新增赛道）",
  "赛道中文名必须从冻结合同读取，而不是前端硬编码",
)
assert.equal(trackRow.hint, "赛道上限 88 分", "赛道上限也来自合同")
assert.equal(trackRow.isNew, false, "合同已收录的赛道不算新增项")

// 阈值档位数量可变，并标出本次落档
const thresholdGroup = adaptiveMatcher.groups.find((group) => group.title === "等级分数阈值")
assert.ok(thresholdGroup, "撮合器必须列出等级分数阈值")
assert.deepEqual(thresholdGroup.rows.map((row) => row.label), ["L1", "L3", "L5"])
assert.equal(
  thresholdGroup.rows.find((row) => row.label === "L3")?.hint,
  "本次落档",
  "要标出本次落在哪一档",
)

// 合同未收录的赛道要标为新增项而不是显示成空
const unknownTrack = buildEvaluationDetailSections({
  scoring: {
    track_key: "class_nine",
    steps: [{ step: "level", score_after: 50, note: "" }],
    v3_context: {
      contract: { track_classification: { tracks: [{ key: "class_one", label: "一类" }] } },
    },
  },
})[2]
const unknownTrackRow = unknownTrack.groups
  .find((group) => group.title === "撮合输入")
  ?.rows.find((row) => row.label === "赛道归属")
assert.ok(unknownTrackRow, "合同未收录的赛道也要列出赛道归属")
assert.equal(
  unknownTrackRow.value,
  "class_nine",
  "合同未收录的赛道要显示原始键，而不是显示成空",
)
assert.match(String(unknownTrackRow.hint), /合同未收录/)
assert.equal(unknownTrackRow.isNew, true, "合同未收录的赛道要标为新增项")

// 冻结合同是字段中文名的权威源：机制新增字段时后端已带 label，前端据此显示
const contractSpecs = fieldSpecsFromContractNodes([
  {
    node_key: "call_a.material_tags",
    layer: "A",
    label: "材质标签",
    description: "素材可见的主要材质",
    metadata: { field_key: "material_tags" },
  },
  // B/V3 层节点不属于调用A字段，必须被过滤掉
  { node_key: "call_b.composition.subject_offset", layer: "B", label: "构图秩序" },
  { node_key: "v3.final_level", layer: "V3", label: "最终等级" },
  // 没有 metadata.field_key 时从 node_key 推导
  { node_key: "call_a.spatial_layout", layer: "A", label: "空间动线" },
])
assert.deepEqual(Object.keys(contractSpecs).sort(), ["material_tags", "spatial_layout"])
assert.equal(contractSpecs.material_tags.label, "材质标签")
assert.equal(contractSpecs.material_tags.hint, "素材可见的主要材质")
assert.equal(
  contractSpecs.spatial_layout.label,
  "空间动线",
  "缺 metadata.field_key 时要从 node_key 推导字段名",
)
assert.deepEqual(fieldSpecsFromContractNodes(null), {}, "无合同时要安全回落")

// 合同下发的中文名要真正生效到展示层
const withContractSpecs = buildEvaluationDetailSections({
  precheck: { production_fields: { material_tags: ["实木"] } },
  fieldSpecs: contractSpecs,
})[0]
assert.equal(
  withContractSpecs.groups.find((group) => group.title === "生产消费字段")?.rows[0].label,
  "材质标签",
  "合同下发的 label 必须生效，否则新字段仍显示英文键",
)

// —— 5. 可纠偏与否由冻结合同决定，且不得向运营承诺做不到的重算 ——
// 这一节锁的是「诚实性」：按钮只出现在真能改的行上，弹窗对「会不会重算」的说法
// 必须与服务端 apply_node_correction 的 downstream_recomputed 严格一致。
const judgementContract = [
  {
    node_key: "call_a.title",
    layer: "A",
    path: "production_fields.title",
    label: "素材标题",
    type: "text",
    metadata: { node_type: "call_a_field", field_key: "title" },
  },
  {
    node_key: "call_a.score",
    layer: "A",
    path: "production_fields.score",
    label: "素材分数",
    type: "integer",
    minimum: 0,
    maximum: 100,
    metadata: { node_type: "call_a_field", field_key: "score" },
  },
  {
    node_key: "call_a.reason",
    layer: "A",
    path: "production_fields.reason",
    label: "过滤原因",
    type: "list",
    options: ["是截图", "是随手拍"],
    metadata: { node_type: "call_a_field", field_key: "reason" },
  },
  {
    node_key: "call_a.hard_defects",
    layer: "A",
    path: "precheck.hard_defects",
    label: "硬缺陷判定",
    type: "list",
    options: ["blurry_grayish"],
    metadata: {
      node_type: "precheck_field",
      option_labels: { blurry_grayish: "画面模糊发灰" },
    },
  },
  {
    node_key: "call_b.aesthetic_score",
    layer: "B",
    path: "aesthetic.aesthetic_score",
    label: "调用B美感分",
    type: "integer",
    minimum: 0,
    maximum: 100,
    metadata: { node_type: "aesthetic_score" },
  },
  {
    node_key: "v3.track_key",
    layer: "V3",
    path: "track_key",
    label: "赛道归属",
    type: "enum",
    options: ["class_one"],
    metadata: { node_type: "track" },
  },
  {
    node_key: "v3.final_level",
    layer: "V3",
    path: "level",
    label: "最终等级",
    type: "enum",
    options: ["L1", "L2", "L3"],
    metadata: { node_type: "final_level" },
  },
]

const judgementInput = {
  precheck: {
    production_fields: { title: "现代客厅", score: 84, reason: [] },
    redline_triggered: { screenshot: false },
    hard_defects: ["blurry_grayish"],
    image_defects: [],
  },
  aesthetic: { aesthetic_score: 86, evidence: ["构图均衡"], dimensions: {} },
  scoring: {
    track_key: "class_one",
    steps: [{ step: "veto", score_after: 60, note: "高分一票压分" }],
    caps: [{ cap: "high_score_veto", reason: "命中硬伤压至 60" }],
    score: 60,
    level: "L4",
    v3_context: {
      contract: {
        track_classification: { tracks: [{ key: "class_one", label: "一类", track_cap: 100 }] },
        level_thresholds: [{ min_score: 61, level: "L2" }, { min_score: 0, level: "L4" }],
      },
    },
  },
}

/** 跨分组按行名查找，避免把分组标题写死在断言里 */
function findRow(section: (typeof withJudgement)[number], label: string) {
  for (const group of section.groups) {
    const row = group.rows.find((item) => item.label === label)
    if (row) return row
  }
  return undefined
}

const withJudgement = buildEvaluationDetailSections({
  ...judgementInput,
  contractNodes: judgementContract,
})
const [judgementA, judgementB, judgementV3] = withJudgement

// recomputes 必须逐项与服务端行为对齐，否则弹窗会承诺一次不存在的重算
const recomputeExpectations: Array<[typeof judgementA, string, boolean]> = [
  [judgementA, "素材标题", false],
  [judgementA, "素材分数", true],
  [judgementA, "过滤原因", false],
  [judgementA, "硬缺陷清单", true],
  [judgementB, "调用B美感分", true],
  [judgementV3, "赛道归属", true],
  [judgementV3, "最终等级", false],
]
for (const [section, label, recomputes] of recomputeExpectations) {
  const row = findRow(section, label)
  assert.ok(row, `${label} 应当出现在面板里`)
  assert.ok(row.correction, `${label} 在合同已收录时必须可纠偏`)
  assert.equal(
    row.correction.recomputes,
    recomputes,
    `${label} 的重算标记必须与服务端 downstream_recomputed 一致，否则会对运营说谎`,
  )
}

// 控件形态要跟着合同类型走
assert.equal(findRow(judgementB, "调用B美感分")?.correction?.valueKind, "integer")
assert.equal(findRow(judgementB, "调用B美感分")?.correction?.maximum, 100)
assert.equal(findRow(judgementA, "过滤原因")?.correction?.valueKind, "multi_enum")
assert.equal(findRow(judgementA, "素材标题")?.correction?.valueKind, "text")
assert.deepEqual(
  findRow(judgementA, "硬缺陷清单")?.correction?.options,
  [{ value: "blurry_grayish", label: "画面模糊发灰" }],
  "硬缺陷候选项与中文名都必须来自冻结合同",
)

// 提交要带上服务端存的原始值，供乐观并发校验
assert.equal(findRow(judgementB, "调用B美感分")?.correction?.currentValue, 86)
assert.deepEqual(findRow(judgementA, "硬缺陷清单")?.correction?.currentValue, ["blurry_grayish"])

// 算出来的行不给按钮，只说明该去改哪个上游判断。
// 不写 `if (!row) continue`：行名写错时必须报错，否则这条断言会静默变成假绿。
for (const label of ["高分否决", "高分否决封顶"]) {
  const row = findRow(judgementV3, label)
  assert.ok(row, `${label} 应当出现在撮合器段里`)
  assert.equal(row.correction, undefined, `${label} 由撮合器算出，不能直接改`)
  assert.match(String(row.derivedNote), /纠偏/, `${label} 必须告诉运营该去改什么`)
}

// 红线留痕行：既不能给按钮，也不能承诺「改过滤原因分数就会变」
const redlineRow = findRow(judgementA, "是截图")
assert.ok(redlineRow, "红线信号必须显示出来")
assert.equal(redlineRow.correction, undefined, "红线留痕不是可提交节点")
assert.match(
  String(redlineRow.derivedNote),
  /最终等级/,
  "纠偏过滤原因不会重新套用红线封顶，必须如实指向最终等级",
)

// 没有冻结合同时整段只读——这正是旧引擎结果的处境
const withoutContract = buildEvaluationDetailSections(judgementInput)
assert.equal(
  withoutContract.flatMap((section) =>
    section.groups.flatMap((group) => group.rows.filter((row) => row.correction)),
  ).length,
  0,
  "没有冻结合同时不能出现任何纠偏入口",
)

// 纠偏历史要把人工结论并列到对应行上，且同一节点取最近一次
const withHistory = buildEvaluationDetailSections({
  ...judgementInput,
  contractNodes: judgementContract,
  correctionHistory: [
    { node_path: "aesthetic.aesthetic_score", new_value: 70 },
    { node_path: "aesthetic.aesthetic_score", new_value: 60 },
    { node_path: "production_fields.title", new_value: "北欧客厅" },
  ],
})
assert.equal(
  findRow(withHistory[1], "调用B美感分")?.humanValue,
  "60 分",
  "同一节点反复纠偏时应展示最近一次的人工结论",
)
assert.equal(findRow(withHistory[0], "素材标题")?.humanValue, "北欧客厅")
assert.equal(
  findRow(withHistory[0], "素材分数")?.humanValue,
  undefined,
  "没纠偏过的行不应显示人工值",
)

// 弹窗源码层面的诚实性与口径一致
const dialogSource = readFileSync(
  new URL("../src/features/evaluation-detail/row-correction-dialog.tsx", import.meta.url),
  "utf8",
)
assert.match(
  dialogSource,
  /target\?\.recomputes/,
  "弹窗必须按 recomputes 区分说法，不能一律宣称会重算",
)
assert.match(dialogSource, /请至少选一个纠偏理由/, "纠偏理由不能为空，否则纠偏分析无从归因")
assert.match(dialogSource, /reasonCodes/, "必须提交结构化归因码供纠偏分析聚合")
const formSource = readFileSync(
  new URL("../src/pages/review-correction-form.tsx", import.meta.url),
  "utf8",
)
for (const [, code] of dialogSource.matchAll(/\["([a-z_]+)", "/g)) {
  assert.ok(
    formSource.includes(`"${code}"`),
    `归因码 ${code} 必须与存量纠偏表单同口径，否则纠偏分析统计会分裂`,
  )
}

// —— 6. 两个纠偏入口都必须接入同一个面板 ——
const panelSource = readFileSync(
  new URL("../src/features/evaluation-detail/evaluation-detail-panel.tsx", import.meta.url),
  "utf8",
)
assert.match(panelSource, /调用A读图产字段、调用B判美感、等级撮合器出最终等级/)
assert.match(panelSource, /evaluation-detail-panel/)

for (const page of ["review-page.tsx", "baseline-regression-page.tsx"]) {
  const source = readFileSync(new URL(`../src/pages/${page}`, import.meta.url), "utf8")
  assert.match(
    source,
    /EvaluationDetailPanel/,
    `${page} 必须接入三段式评测细节面板`,
  )
  assert.match(
    source,
    /features\/evaluation-detail\/evaluation-detail-panel/,
    `${page} 必须从公共面板导入，不允许各自实现一套`,
  )
}

// —— 7. 定案必须经过确认框，导航不得顺手定案 ——
// 服务端三种决定都会把 review_stage 置为 completed，其中 corrected 还会写进类目黄金集。
// 这一节锁的是：翻页不定案、定案必确认、写真值前必须警示——该项目已因黄金集真值污染吃过一次亏。
const reviewPageSource = readFileSync(
  new URL("../src/pages/review-page.tsx", import.meta.url),
  "utf8",
)

// 定案入口一律走 requestDecision 暂存，唯一真实提交点在确认框的 onConfirm 上
assert.equal(
  (reviewPageSource.match(/requestDecision\(\{ decision:/g) ?? []).length,
  5,
  "五个定案入口（文档类与常规类的采纳/退回、纠偏）都必须先过确认框",
)
assert.equal(
  (reviewPageSource.match(/review\.mutate\(/g) ?? []).length,
  1,
  "review.mutate 只允许在确认框的 onConfirm 里出现一次，按钮不得直接提交",
)
assert.match(
  reviewPageSource,
  /onConfirm=\{\(\) => \{\s*if \(pendingDecision\) review\.mutate\(pendingDecision\)/,
  "确认框必须提交暂存的 payload，不能另造一份",
)

// go() 是纯导航：翻页不能顺带把样本标成已复核
const goBody = reviewPageSource.slice(
  reviewPageSource.indexOf("function go(offset: number)"),
  reviewPageSource.indexOf("function go(offset: number)") + 400,
)
for (const forbidden of ["review.mutate", "requestDecision", "submitReviewDecision"]) {
  assert.ok(
    !goBody.includes(forbidden),
    `上一张/下一张必须是纯导航，不能触发 ${forbidden}——翻页即定案会污染人工真值`,
  )
}

// 提交成功后要清掉暂存，否则下一条会带着上一条的 payload
assert.match(
  reviewPageSource,
  /setPendingDecision\(null\)/,
  "定案成功后必须清空待确认状态",
)

const confirmDialogSource = readFileSync(
  new URL("../src/features/evaluation-detail/review-decision-confirm-dialog.tsx", import.meta.url),
  "utf8",
)

// 三种决定都要有确认文案，漏一种就会出现「点了没反应」的空弹窗
for (const decision of ["approved", "corrected", "rejected"]) {
  assert.match(
    confirmDialogSource,
    new RegExp(`${decision}:\\s*\\{`),
    `确认框必须覆盖 ${decision} 决定`,
  )
}

// 只有 corrected 写黄金集，所以只有它带真值警示——别把警示铺满三种，会钝化
assert.match(
  confirmDialogSource,
  /goldenSetWarning[\s\S]{0,400}黄金集/,
  "纠偏定案必须警示会写入黄金集真值",
)
const approvedBlock = confirmDialogSource.slice(
  confirmDialogSource.indexOf("approved: {"),
  confirmDialogSource.indexOf("corrected: {"),
)
assert.ok(
  !approvedBlock.includes("goldenSetWarning"),
  "采纳模型结果不写黄金集，不应挂真值警示",
)

// 提交中不能被 Esc / 点遮罩关掉，否则运营会以为取消了但请求已经发出
assert.match(
  confirmDialogSource,
  /if \(!next && !submitting\) onCancel\(\)/,
  "提交进行中必须锁住确认框，避免运营误以为已取消",
)
assert.match(confirmDialogSource, /disabled=\{submitting\}/, "提交中必须禁用按钮，防止重复定案")

console.log("evaluation detail panel contract: passed")
