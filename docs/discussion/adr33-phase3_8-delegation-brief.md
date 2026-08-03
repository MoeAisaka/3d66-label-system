# ADR-0033 Phase 3.8 委派任务书（灵感图 v3 合同 seed / 完整装配样板 · 纯函数）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 定位

前六个框架件（红线 redline_policy / v3 合同 category_evaluation_contract / 聚合器 category_evaluation_aggregator / grade→deduction 桥 dimension_grade_bridge / 共性+特有维度组合 dimension_composition / 分类器 subcategory_resolver）已就绪。本阶段做**最后一块框架件**：把它们**装配成一套完整、自洽、可跑通全链的「灵感图」样板配置**，并提供一个端到端"跑一张图"的确定性编排函数（纯函数，仍不接 worker/DB/模型）。目的：证明六件能无缝拼成一个真实类目，并为 Phase 4 worker 接线提供参照装配。

规则来源：`docs/reference/category-inspiration-image-rules-20260803.md`（红线四类、三赛道 40/60/100·20/60/80·40/30/70、媒介降权、80 分压分、一级分类）。

## 边界（硬约束，同前几期）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- **不改任何现有文件**。只新建：
  1. `backend/app/inspiration_category_seed.py`
  2. `backend/tests/test_inspiration_category_seed.py`

## 背景（先读全部）

- `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`
- `docs/reference/category-inspiration-image-rules-20260803.md`
- 六个模块的导出：redline_policy(REDLINE_POLICY_FORMAT_VERSION, validate_redline_policy, evaluate_redlines)、category_evaluation_contract(CATEGORY_EVALUATION_CONTRACT_VERSION, validate_category_evaluation_contract)、category_evaluation_aggregator(aggregate_category_evaluation)、dimension_grade_bridge、dimension_composition(SUBCATEGORY_DIMENSIONS_FORMAT_VERSION, compose_deductions)、subcategory_resolver(CLASSIFICATION_MAP_FORMAT_VERSION, resolve_subcategory)。
- `dimension_schema_registry.space_schema_definition_for_version(ACTIVE_V13_VERSION)` 可作共性维度来源；`ACTIVE_V13_VERSION`。

## 必做（`inspiration_category_seed.py`）

全部返回普通 dict（可 JSON 序列化），纯函数、无副作用。

1. `INSPIRATION_SEED_VERSION = "inspiration-category-seed-v1"`
2. `build_inspiration_v3_contract() -> dict`：返回一份合法的 v3 合同（`evaluation-category-profile-v3`），含：
   - `redline_policy`：四类红线（screenshot/casual_snapshot/large_text/qr_code，signal=production_fields.reason，match_any 用真实 reason 枚举，large_text 带专业海报等豁免），hit_level=L5，hit_score_cap=49，enabled=true。
   - `track_classification`：三子类目 class_one(40/60/100)、class_two(20/60/80)、class_three(40/30/70)，default_track=class_three，dimension_schema_ref 用 space_aesthetic/ACTIVE_V13_VERSION。
   - `common_modifiers`：媒介 real_photo0/render_3d-5/ai_image-15/other0，veto 80→79。
   - 通过 `validate_category_evaluation_contract` 自校验（函数内部断言/校验，非法则由校验器抛错）。
3. `build_inspiration_classification_map() -> dict`：`subcategory-classification-map-v1`，min_confidence 0.6，把灵感图一级分类词按赛道归类映射到 class_one/two/three（建筑/室内/景观/规划→class_one；产品/雕塑/装置/美术/游戏→class_two；其它→class_three），out_of_scope_subcategory=class_three。目标必须都是合同 track key。
4. `build_inspiration_subcategory_dimensions() -> dict[str, dict]`：为每个子类目建 `subcategory-dimensions-v1` 配置（common_group + specific_group），dimension_max 对应各赛道（class_one/two=60、class_three=30）。共性组用 space schema 的 core_dimension_keys 那几维（或从 v13 schema 取一个非空子集，权重组内和=1）；特有组给 1-2 个该子类目自定义维度（权重和=1）。group_weight 两组和=1。全部能过 dimension_composition.validate_subcategory_dimensions。
5. `evaluate_one(*, contract, classification_map, subcategory_dimensions, precheck, common_grades_by_track, specific_grades_by_track) -> dict`：端到端确定性编排（纯函数）：
   - 先 `evaluate_redlines`；命中则直接 `aggregate_category_evaluation` 走红线分支返回（或直接返回聚合器红线结果）。
   - 未命中：`resolve_subcategory` 定 track_key → 取该 track 的 subcategory_dimensions 配置 → `compose_deductions`（用该 track 对应的 common/specific grades）→ `aggregate_category_evaluation(contract, precheck, composed, track_key=track_key)`。
   - 返回 `{"redline": <evaluate_redlines>, "resolved": <resolve_subcategory or None>, "result": <aggregate result>}`。
   - 不调用模型：grades 由入参提供（模拟调用B 输出）。

## 测试（`test_inspiration_category_seed.py`）

- `build_inspiration_v3_contract()` 过 `validate_category_evaluation_contract`。
- classification_map 过 `validate_classification_map`（用合同 track keys）。
- 每个子类目 dimensions 配置过 `validate_subcategory_dimensions`。
- 端到端 `evaluate_one`：
  - 红线命中（reason=["是截图"]）→ result hard_reject、level=L5、score=49、terminated_at=redline。
  - 建筑设计 + 高置信 + 全维度 grade5 + 实拍 → 走 class_one → score 100 / L1。
  - 产品设计 + grade5 → class_two → score ≤80（封顶 80）。
  - 低置信 → 落 class_three（default）→ 对应基底分/封顶正确。
  - AI 图媒介 → 相应 -15 降权体现。
- 全流程确定性 + JSON 可序列化。

## 完成信号

写 `ADR33_PHASE3_8_DONE.md`（文件、导出名、装配内容、端到端覆盖场景），写完即停，等 OpenClaw 验收。
