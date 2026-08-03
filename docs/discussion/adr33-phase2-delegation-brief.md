# ADR-0033 Phase 2 委派任务书（确定性评测聚合器 · 纯函数）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 边界（硬约束，同 Phase 1）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- 工作根：本 worktree。基线由 OpenClaw 指定（已含 Phase 1 的 `redline_policy.py` 和 `category_evaluation_contract.py`）。
- 只新建下列文件，**不改任何现有文件**、不接 worker 生产路径、不做 L 翻转迁移、不碰前端/迁移 runner/ADR：
  1. `backend/app/category_evaluation_aggregator.py`
  2. `backend/tests/test_category_evaluation_aggregator.py`

## 背景（先读）

- `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`
- `docs/reference/category-inspiration-image-rules-20260803.md`（分数计算顺序：基底分 = 赛道基准分 + 维度满分；依次扣减维度扣分与媒介固定扣分；最后硬性封顶）
- `backend/app/redline_policy.py`、`backend/app/category_evaluation_contract.py`（Phase 1，直接复用其校验器与数据结构）

## 语义（关键）

本项目采用**钉钉文档语义：L5 = 最差（红线/废图档），L1 = 最优**。聚合器必须按这个方向产出 L 等级。分数越高越好（0-100），但 L 等级方向与分数相反：高分 → 低 L 数字（L1 最优）。请在模块内用一个显式常量表把「最终分数 → L 等级」映射写清楚，并可被合同覆盖。

> 注意：现有 `scoring.py` 用的是相反方向（L5=最优），本阶段**不要改它**。本聚合器是**独立的新语义引擎**，带 `level_semantics_version = "doc-l5-worst-v1"` 标注，后续阶段才做全局迁移与接线。

## 任务：`category_evaluation_aggregator.py`（纯函数确定性聚合）

实现一个**无 IO/网络/DB/模型调用**的纯函数聚合器，输入是「已冻结的 v3 合同 + 调用A precheck + 调用B 维度评审输出」，输出最终 score / L 等级 / 证据链。

### 常量
```python
AGGREGATOR_VERSION = "category-evaluation-aggregator-v1"
LEVEL_SEMANTICS_VERSION = "doc-l5-worst-v1"
```

### 分数→L 映射（doc-l5-worst-v1，可被合同的 level_thresholds 覆盖；默认用文档区间）
默认阈值（分数越高 L 数字越小=越好）：
- score >= 80 → L1（最优，注意：≥80 还要过「高分一票压分」，见下）
- 60 <= score < 80 → L2
- 40 <= score < 60 → L3
- 20 <= score < 40 → L4
- score < 20 → L5（最差）
（这是首版默认，用一个可覆盖的有序阈值表实现；红线命中是独立的强制 L5，不走分数映射。）

### 主函数
`aggregate_category_evaluation(contract, precheck, dimension_result, *, track_key=None) -> dict`

严格按此确定性顺序（每一步产出可解释证据，全部记入返回的 `steps`/`caps`）：

1. **校验**：先 `category_evaluation_contract.validate_category_evaluation_contract(contract)`。非法抛 `CategoryEvaluationAggregatorError`（继承 ValueError，带 `.code`）。
2. **红线（节点0）**：调 `redline_policy.evaluate_redlines(precheck, policy=contract["redline_policy"])`。命中 → 直接返回 `hard_reject=True`、`level=contract.redline_policy.hit_level`（本项目为 L5）、`score=min(hit_score_cap, ...)`（封顶到 hit_score_cap，本例 49）、`hit_rules`、`terminated_at="redline"`，**不再进入后续步骤**。
3. **赛道解析（节点1）**：入参 `track_key` 为空时用 `contract.track_classification.default_track`；必须是合同里已定义的赛道，否则 fail-closed。取出该赛道 `base_score / dimension_max / track_cap / dimension_schema_ref`。
4. **维度扣分（节点2）**：`dimension_result` 给出每个维度的**扣分**（本阶段约定 `dimension_result["deductions"]` 为 `{dimension_key: 扣分(>=0 整数或浮点)}`，和 `dimension_result["evidence"]` 可选）。初始分 = `base_score + dimension_max`；逐项减去扣分；单项扣分不得使该维度贡献为负（clamp 每维度净贡献 >=0，即累计维度扣分不超过 dimension_max）。产出 `score_after_dimensions`。
5. **媒介固定扣分（固定通用维度）**：从 `precheck.production_fields.trait` 读媒介类型，映射到 `common_modifiers.media_type_penalty`（trait 文案→键的映射在模块内定义：实拍→real_photo、3D效果图→render_3d、AI图→ai_image、其它/无法判断→other；未知安全落 other 并记 uncertainty）。按 `penalties` 相应值扣分（<=0）。产出 `score_after_media`。
6. **高分一票压分（veto）**：若 `score_after_media >= common_modifiers.high_score_veto.threshold` 且 precheck 命中任一硬伤信号（本阶段：`precheck.get("hard_defects")` 为非空 list 即视为命中；无该字段则不触发），强制 `score = min(score, cap_to)`（本例 79），记 cap 原因。
7. **赛道封顶**：`score = min(score, track_cap)`；再 clamp 到 [0,100] 整数（四舍五入后取整）。
8. **分数→L 等级**：用 doc-l5-worst 阈值表映射；产出 `level`、`raw_level`（未压分前）。
9. 返回固定结构、JSON 可序列化、对同输入稳定：
```
{
  "aggregator_version": "...", "level_semantics_version": "doc-l5-worst-v1",
  "hard_reject": false, "terminated_at": null|"redline",
  "track_key": "...", "base_score": .., "dimension_max": ..,
  "score": <int 0-100>, "level": "L?", "raw_level": "L?",
  "hit_rules": [...], "caps": [{"cap": "...", "reason": "..."}],
  "steps": [ {"step":"redline|track|dimensions|media|veto|track_cap|level","score_after":.., "note":".."} , ... ]
}
```

不做任何 IO/网络/DB/模型调用；纯函数、可回归。复用 Phase 1 的校验器和常量，不要重抄红线/合同校验逻辑。

## 测试：`test_category_evaluation_aggregator.py`

用灵感图三赛道合同（40/60/100、20/60/80、40/30/70）+ 媒介降权（实拍0/效果图-5/AI-15）+ veto(80→79) 覆盖：
- 红线命中直出 L5 + hard_reject + score 封顶 49，且不进入维度步骤。
- 一类满分链路（无扣分、实拍）：base40+dim60=100 → L1。
- AI 图降权：100 - 15 = 85 → 仍 L1，但记录媒介扣分证据。
- 维度扣分累计不超过 dimension_max（clamp 边界）。
- 高分一票压分：score>=80 且有 hard_defects → 封到 79 → L2。
- 赛道封顶：二类即使满分也 <=80；三类 <=70。
- 分数→L 各档边界（80/60/40/20）映射正确（L5=最差方向）。
- 默认赛道回退（track_key 缺省用 default_track）。
- 非法合同 / 未知 track_key / 维度扣分为负 等 fail-closed。
- 确定性：同输入多次同输出。

## 完成信号

写 `ADR33_PHASE2_DONE.md`（文件清单、导出名、覆盖场景），写完即停，等 OpenClaw 验收。
