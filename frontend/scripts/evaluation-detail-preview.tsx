import { useState } from "react"
import { createRoot } from "react-dom/client"

import { EvaluationDetailPanel } from "../src/features/evaluation-detail/evaluation-detail-panel"
import { fieldSpecsFromContractNodes } from "../src/features/evaluation-detail/detail-model"
import "../src/index.css"

/**
 * 人工纠偏三段式评测细节的视觉预览。
 *
 * 用 mock 数据跑真实组件，不需要登录也不依赖后端。三个案例分别覆盖：
 * 典型压分案例、机制升级后的自适应案例、旧引擎数据缺失案例。
 */

// 冻结的 v3 合同片段：赛道与等级阈值都从这里读，前端不硬编码
const v3Contract = {
  track_classification: {
    tracks: [
      { key: "class_one", label: "一类（建筑/室内/景观/规划）", track_cap: 100 },
      { key: "class_two", label: "二类（家具/单品/局部）", track_cap: 85 },
      { key: "class_three", label: "三类（其它）", track_cap: 70 },
    ],
  },
  level_thresholds: [
    { min_score: 81, level: "L1" },
    { min_score: 71, level: "L2" },
    { min_score: 61, level: "L3" },
    { min_score: 41, level: "L4" },
    { min_score: 0, level: "L5" },
  ],
}

/**
 * mock 冻结合同节点：决定哪一行可点击纠偏。
 *
 * 真实环境由后端 `freeze_contract_from_execution_snapshot` 下发；这里手写一份等价的，
 * 让预览页不连后端也能看到纠偏入口。
 */
const correctionContractNodes = [
  ...[
    ["title", "素材标题", "10 字内", "text"],
    ["seotitle", "搜索标题", "28 字内", "text"],
    ["category", "素材类目", "一级分类，二级分类", "text"],
    ["style", "素材风格", "无法判断时写「无法判断」", "text"],
    ["tags", "素材标签", "至少 4 个", "list"],
    ["cons", "素材缺点", "只依据可见证据", "text"],
    ["design", "设计说明", "无法判断时不得编造", "text"],
    ["score", "素材分数", "0-100 整数", "integer"],
    ["image_defects", "图片缺陷", "空或「有水印」", "text"],
  ].map(([key, label, description, type]) => ({
    node_key: `call_a.${key}`,
    layer: "A",
    path: `production_fields.${key}`,
    label,
    description,
    type,
    metadata: { node_type: "call_a_field", field_key: key },
  })),
  // 过滤原因是红线的真实输入，给出候选项供多选
  {
    node_key: "call_a.reason",
    layer: "A",
    path: "production_fields.reason",
    label: "过滤原因",
    description: "命中即触发红线，可多选；清空表示撤掉误判的红线",
    type: "list",
    options: ["是截图", "是随手拍", "文字过多", "二维码过多"],
    metadata: { node_type: "call_a_field", field_key: "reason" },
  },
  {
    node_key: "call_a.trait",
    layer: "A",
    path: "production_fields.trait",
    label: "素材媒介",
    description: "决定是否启用媒介扣分",
    type: "enum",
    options: ["AI图", "实景照片", "3D数字效果图", "其它"],
    metadata: { node_type: "call_a_field", field_key: "trait" },
  },
  // 本轮新增：硬缺陷清单
  {
    node_key: "call_a.hard_defects",
    layer: "A",
    path: "precheck.hard_defects",
    label: "硬缺陷判定",
    description: "调用A判定的硬缺陷；命中会压分或封顶，改动后服务端重算",
    type: "list",
    options: ["blurry_grayish", "flat_lighting", "distorted_perspective"],
    metadata: {
      node_type: "precheck_field",
      option_labels: {
        blurry_grayish: "画面模糊发灰",
        flat_lighting: "光影平淡无层次",
        distorted_perspective: "透视明显变形",
      },
    },
  },
  // 本轮新增：调用B美感分
  {
    node_key: "call_b.aesthetic_score",
    layer: "B",
    path: "aesthetic.aesthetic_score",
    label: "调用B美感分",
    description: "调用B给出的 0-100 美感分，是等级撮合器的初始分",
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
    description: "决定封顶上限",
    type: "enum",
    options: ["class_one", "class_two", "class_three"],
    metadata: {
      node_type: "track",
      option_labels: {
        class_one: "一类（建筑/室内/景观/规划）",
        class_two: "二类（家具/单品/局部）",
        class_three: "三类（其它）",
      },
    },
  },
  {
    node_key: "v3.final_level",
    layer: "V3",
    path: "level",
    label: "最终等级",
    description: "直接改等级不会重算分数，仅在撮合器结论确实判错时使用",
    type: "enum",
    options: ["L1", "L2", "L3", "L4", "L5"],
    metadata: { node_type: "final_level" },
  },
]

/** 案例一：高分被硬缺陷一票压分——运营最常质疑「为什么 86 分只给 L4」 */
const typicalCase = {
  label: "典型案例 · 高分被硬缺陷压至 L4",
  summary: "调用B给了 86 分，被硬缺陷一票压到 60 分 / L4。运营需要看懂这一跳是怎么发生的。",
  precheck: {
    production_fields: {
      title: "现代简约客厅",
      seotitle: "现代简约客厅装修设计效果图参考",
      category: "客厅,大平层",
      style: "现代简约",
      tags: ["客厅", "现代简约", "米色系", "大平层", "落地窗"],
      cons: "沙发背景墙略空，缺少视觉焦点",
      design: "以米白与浅木色为主调，通过落地窗引入自然光",
      score: 84,
      reason: [],
      image_defects: "",
      trait: "3D数字效果图",
    },
    redline_triggered: {
      screenshot: false,
      casual_photo: false,
      text_heavy: false,
      qr_code_heavy: false,
    },
    hard_defects: ["blurry_grayish"],
    image_defects: ["corner_small_watermark"],
    decisive_evidence: {
      redline_triggered: {},
      hard_defects: [
        { key: "blurry_grayish", evidence: "画面整体发灰，窗外过曝导致中间调丢失，墙面与地面缺乏层次" },
      ],
      image_defects: [
        { key: "corner_small_watermark", evidence: "右下角有约 40×15 像素的站点水印" },
      ],
    },
    image_quality: {
      quality_severity: "moderate",
      evidence: ["整体锐度偏低，家具边缘有轻微涂抹感"],
    },
    classification: {
      primary_category: "室内空间",
      evidence: ["画面主体为完整客厅空间，含沙发、茶几、电视墙"],
    },
    decision_status: "complete",
    uncertain_fields: [],
  },
  aesthetic: {
    aesthetic_score: 86,
    confidence: 0.88,
    bridge_version: "dimension-deduction-bridge-v2",
    evidence: [
      "构图采用一点透视，沙发与电视墙形成稳定对称关系",
      "米白、浅木、灰蓝三色控制得当，无明显撞色",
      "落地窗自然光形成明确主光源，明暗过渡合理",
    ],
    dimensions: {
      composition: {
        label: "构图秩序",
        hit_rules: [
          {
            rule_id: "subject_offset",
            deduction: 4,
            confidence: "high",
            evidence: "沙发主体明显偏左，右侧留白面积过大且无内容承接",
          },
        ],
      },
      color: { label: "色彩关系", hit_rules: [] },
      lighting: {
        label: "光影质量",
        hit_rules: [
          {
            rule_id: "flat_lighting",
            deduction: 5,
            confidence: "medium",
            evidence: "顶部筒灯未形成明显光斑，天花与墙面交界处缺少明暗对比",
          },
        ],
      },
      material: { label: "材质表现", hit_rules: [] },
      furnishing: {
        label: "软装陈设",
        hit_bonus_rules: [
          {
            rule_id: "layered_decor",
            bonus: 2,
            confidence: "high",
            evidence: "茶几与边几形成高低错落，绿植与抱枕呼应主色",
          },
        ],
      },
    },
  },
  scoring: {
    track_key: "class_one",
    initial_score: 86,
    dimension_scoring_mode: "rule_deduction",
    dimension_evidence: {
      applied_deduction_total: 9,
      clamped_to_dimension_max: false,
      deductions: { composition: 4, lighting: 5 },
    },
    steps: [
      { step: "track", score_after: 86, note: "赛道 class_one：调用B美感基础分 86，赛道上限 100" },
      { step: "b_aesthetic_foundation", score_after: 86, note: "等级撮合器以调用B aesthetic_score 作为初始分" },
      { step: "dimension_rule_deduction", score_after: 77, note: "维度扣分（规则命中）：应用扣分 9" },
      { step: "media_skipped", score_after: 77, note: "素材媒介为 3D数字效果图，未启用媒介扣分" },
      { step: "hard_defect_penalty", score_after: 77, note: "硬缺陷 blurry_grayish 已计入维度扣分，不重复扣分" },
      { step: "veto", score_after: 60, note: "高分一票压分触发：封顶至 60" },
      { step: "track_cap", score_after: 60, note: "赛道封顶至 100 后取整" },
      { step: "level", score_after: 60, note: "分数 60 → L4（未压分前为 L2）" },
    ],
    caps: [
      {
        cap: "high_score_veto",
        reason: "分数 77 达到 75 且命中硬伤 ['blurry_grayish']，强制压至 60",
      },
    ],
    score: 60,
    level: "L4",
    raw_level: "L2",
    v3_context: { contract: v3Contract },
  },
  dimensionLabels: {
    composition: "构图秩序",
    color: "色彩关系",
    lighting: "光影质量",
    material: "材质表现",
    furnishing: "软装陈设",
  },
  correctedFieldKeys: ["title"],
  contractNodes: [
    {
      node_key: "call_a.title",
      layer: "A",
      label: "素材标题",
      description: "用于检索与推荐的素材标题",
      metadata: { field_key: "title" },
    },
  ],
}

/** 案例二：机制升级后新增字段、规则、赛道——验证前端不改代码也能显示 */
const adaptiveCase = {
  label: "自适应案例 · 机制新增字段与规则",
  summary:
    "机制新增了 2 个生产字段、1 条红线、1 个维度、1 条判定规则和 1 个赛道。前端未改代码，全部自动显示并标「新增」。",
  precheck: {
    production_fields: {
      title: "北欧风卧室",
      seotitle: "北欧风主卧设计效果图",
      category: "卧室,主卧",
      style: "北欧",
      tags: ["卧室", "北欧", "原木", "低饱和"],
      cons: "床头背景略单调",
      design: "以原木与白色为基底",
      score: 79,
      reason: [],
      image_defects: "",
      trait: "3D数字效果图",
      // 机制新增：合同里带了中文名，所以显示为「材质标签」
      material_tags: ["实木", "亚麻", "黄铜"],
      // 机制新增：合同里没有中文名，回落显示原始键，提示运营来补
      spatial_layout: "L 型动线",
    },
    redline_triggered: {
      screenshot: false,
      casual_photo: false,
      text_heavy: false,
      qr_code_heavy: false,
      // 机制新增的红线信号
      ai_watermark: true,
    },
    hard_defects: ["unseen_defect_kind"],
    image_defects: [],
    decisive_evidence: {
      redline_triggered: {
        ai_watermark: ["右下角有生成式模型水印，疑似 AI 直出未做后处理"],
      },
      hard_defects: [
        { key: "unseen_defect_kind", evidence: "床品褶皱走向不符合重力方向" },
      ],
      image_defects: [],
    },
    image_quality: { quality_severity: "slight" },
    classification: { primary_category: "室内空间" },
    decision_status: "uncertain",
    uncertain_fields: ["material_tags"],
  },
  aesthetic: {
    aesthetic_score: 74,
    confidence: 0.62,
    evidence: ["构图规整，床居中对称", "低饱和配色统一"],
    dimensions: {
      composition: { label: "构图秩序", hit_rules: [] },
      // 机制新增的维度，前端没有中文名
      unseen_dimension: {
        hit_rules: [
          {
            rule_id: "new_rule",
            deduction: 3,
            confidence: "low",
            evidence: "新规则命中，证据待人工复核",
          },
        ],
      },
    },
  },
  scoring: {
    // 机制新增的赛道，合同已收录
    track_key: "class_four",
    initial_score: 74,
    dimension_scoring_mode: "rule_deduction",
    dimension_evidence: {
      applied_deduction_total: 3,
      clamped_to_dimension_max: false,
      deductions: { unseen_dimension: 3 },
    },
    steps: [
      { step: "track", score_after: 74, note: "赛道 class_four：调用B美感基础分 74，赛道上限 88" },
      { step: "dimension_rule_deduction", score_after: 71, note: "维度扣分（规则命中）：应用扣分 3" },
      // 机制新增的判定规则
      {
        step: "brand_conflict_check",
        score_after: 65,
        note: "品牌冲突检查未通过：画面含第三方品牌可识别标识",
      },
      // 历史脏数据：步骤不是对象，也必须显示出来而不是静默丢弃
      "legacy_plain_step",
      { step: "level", score_after: 65, note: "分数 65 → L3" },
    ],
    caps: [
      // 机制新增的封顶类型
      { cap: "brand_conflict_cap", reason: "品牌冲突封顶至 65" },
      // 历史脏数据：cap 是纯字符串
      "redline",
    ],
    score: 65,
    level: "L3",
    v3_context: {
      contract: {
        ...v3Contract,
        track_classification: {
          tracks: [
            ...v3Contract.track_classification.tracks,
            { key: "class_four", label: "四类（AI 生成图专用赛道）", track_cap: 88 },
          ],
        },
      },
    },
  },
  dimensionLabels: { composition: "构图秩序" },
  correctedFieldKeys: [],
  contractNodes: [
    {
      node_key: "call_a.material_tags",
      layer: "A",
      label: "材质标签",
      description: "素材可见的主要材质，至少 1 个",
      metadata: { field_key: "material_tags" },
    },
  ],
}

/** 案例三：旧引擎结果——三段都缺数据，验证「技术失败与业务误判分离」 */
const legacyCase = {
  label: "旧引擎案例 · 数据缺失",
  summary: "三段都缺少上下文。每段给出具体原因和下一步动作，而不是显示成空表让运营以为数据丢了。",
  precheck: {},
  aesthetic: {},
  scoring: {},
  dimensionLabels: {},
  correctedFieldKeys: [],
  contractNodes: [],
}

const cases = [typicalCase, adaptiveCase, legacyCase]

/** 1440 / 1280 双视口是 6 条强制交互合同之一，预览页直接提供切换 */
const viewports = [
  { label: "1440 视口", width: 1188, hint: "内容区 1188px（1440 − 252 侧栏）" },
  { label: "1280 视口", width: 1028, hint: "内容区 1028px（1280 − 252 侧栏）" },
  { label: "纠偏抽屉", width: 720, hint: "存量回归逐条纠偏的右栏宽度" },
]

function Preview() {
  const [caseIndex, setCaseIndex] = useState(0)
  const [viewportIndex, setViewportIndex] = useState(0)
  // mock 纠偏历史：真实环境由服务端返回，这里存在内存里，用来演示「模型 vs 人工」对照
  const [history, setHistory] = useState<Record<number, unknown[]>>({})
  const active = cases[caseIndex]
  const viewport = viewports[viewportIndex]
  const caseHistory = history[caseIndex] ?? []
  // 旧引擎案例没有冻结合同，整段应当只读——这正是「合同决定能不能纠偏」的体现。
  const caseContractNodes = active === legacyCase ? [] : correctionContractNodes

  return (
    <div className="min-h-dvh bg-[#f1f3ef] pb-16">
      <header className="border-b border-[var(--line-strong)] bg-white px-6 py-5">
        <p className="font-data text-[0.68rem] text-[var(--muted)]">
          视觉预览 · 不连后端 · mock 数据
        </p>
        <h1 className="font-editorial mt-1 text-2xl font-bold">
          人工纠偏三段式评测细节
        </h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
          调用A → 调用B → 等级撮合器，与实际执行顺序一致。默认展开撮合器，因为它最常被质疑。
        </p>

        <div className="mt-4 flex flex-wrap gap-6">
          <div>
            <p className="mb-2 text-xs font-bold">案例</p>
            <div className="flex flex-wrap gap-1.5">
              {cases.map((item, index) => (
                <button
                  key={item.label}
                  type="button"
                  onClick={() => setCaseIndex(index)}
                  className={`min-h-9 rounded-[4px] border px-3 text-xs font-semibold transition-colors ${
                    index === caseIndex
                      ? "border-[#7f991b] bg-[#f0f8c8] text-[#263000]"
                      : "border-[var(--line-strong)] bg-white hover:bg-[#fafbf8]"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-bold">视口宽度</p>
            <div className="flex flex-wrap gap-1.5">
              {viewports.map((item, index) => (
                <button
                  key={item.label}
                  type="button"
                  onClick={() => setViewportIndex(index)}
                  className={`min-h-9 rounded-[4px] border px-3 text-xs font-semibold transition-colors ${
                    index === viewportIndex
                      ? "border-[#7f991b] bg-[#f0f8c8] text-[#263000]"
                      : "border-[var(--line-strong)] bg-white hover:bg-[#fafbf8]"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <p className="mt-4 border-t border-[var(--line)] pt-3 text-xs leading-5 text-[var(--muted)]">
          {active.summary}
        </p>
      </header>

      <main className="px-6 py-6">
        <p className="font-data mb-3 text-[0.68rem] text-[var(--muted)]">
          {viewport.hint}
        </p>
        <div
          className="mx-auto bg-white shadow-[0_16px_48px_rgba(17,19,15,0.1)]"
          style={{ width: viewport.width, maxWidth: "100%" }}
        >
          <EvaluationDetailPanel
            key={`${caseIndex}-${viewportIndex}`}
            precheck={active.precheck}
            aesthetic={active.aesthetic}
            scoring={active.scoring}
            dimensionLabels={active.dimensionLabels}
            correctedFieldKeys={active.correctedFieldKeys}
            fieldSpecs={fieldSpecsFromContractNodes(active.contractNodes)}
            contractNodes={caseContractNodes}
            correctionHistory={caseHistory}
            onCorrect={(payload) => {
              // 预览页没有后端，只把人工值记到内存里演示对照展示；真实环境会重算分数。
              setHistory((prev) => ({
                ...prev,
                [caseIndex]: [
                  ...(prev[caseIndex] ?? []),
                  {
                    node_path: payload.nodePath,
                    new_value: payload.newValue,
                    reason: payload.reason,
                    reason_codes: payload.reasonCodes,
                  },
                ],
              }))
            }}
          />
        </div>
      </main>
    </div>
  )
}

createRoot(document.getElementById("root")!).render(<Preview />)
