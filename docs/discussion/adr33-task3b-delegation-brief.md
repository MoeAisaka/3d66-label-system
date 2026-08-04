# ADR-0033 Task 3b 委派任务书：inspiration_image 真实合同（方案 A，6/5 维度）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。
**先完整读** `docs/discussion/adr33-task3b-inspiration-real-contract-spec.md`（冻结规格，一切以它为准）。

## 定位与边界
用产品真实的 6/5 维度体系重建 inspiration_image 的 v3 合同，A/B 剥离。
**只用 Read/Edit/Write/Glob/Grep。禁 Bash/git/网络/安装/运行测试。**
**不改引擎核心**：`redline_policy.py` / `category_evaluation_aggregator.py` /
`dimension_grade_bridge.py` / `dimension_composition.py` / `category_evaluation_contract.py`
/ `subcategory_resolver.py` 全部一字不改（它们已通过全部规则，你只复用）。
不改 worker.py / scoring.py / 老类目 / 已发布数据 / 任何 migration。

## 必做

### 1. 重写 `backend/app/inspiration_category_seed.py` 的维度部分
- `build_inspiration_subcategory_dimensions()`：每赛道输出规格里的真实维度，
  **全部放 common_group（group_weight=1.0），specific_group 置空**（`{"schema_definition":{"dimensions":[]}}` 或引擎认可的空组表达——参考 dimension_composition 对空组的处理）。
  - class_one/class_two：6 维度（visual_structure/color_aesthetics/emotional_expression/design_aesthetics/originality/design_trendiness），weight=满分/60，dimension_max=60。
  - class_three：5 维度（subject_focus/mood_atmosphere/composition_lighting/reference_value/visual_impact），weight=0.2 each，dimension_max=30。
  - grade_points 全用 `{"1":0,"2":25,"3":50,"4":75,"5":100}`。
  - 每组 weight 和严格=1（末位吸收浮点漂移，参考原 `_common_group_from_v13` 的 drift 处理）。
- 删除/替换旧的 `_SPECIFIC_DIMENSIONS`（spatial_originality 等占位维度）。
- `build_inspiration_v3_contract()` / `build_inspiration_classification_map()` / redline / track / media / veto **保持不变**（已对齐产品）。
- 必须通过 `validate_category_evaluation_contract` / `validate_classification_map` / `validate_subcategory_dimensions`（模块自带 self-check）。

### 2. 校订 A/B prompt（已起草，按规格改）
- `backend/prompts/inspiration_image_call_a.txt`：在输出 JSON 加 **`hard_defects`**（数组，规格第三节的 10 条硬伤命中项），保留 redline/classification/media 字段。
- `backend/prompts/inspiration_image_call_b.txt`：改成 **6/5 维度**（规格第二节的 key），每维度写 1-5 档 rubric（把产品"扣3~5分"规则转成"grade5=无硬伤满分 … grade1=严重缺陷"的评级标准）。删掉旧 8 维度。special_checks 保留但注明"仅记录不算分"。

### 3. seed_defaults（`backend/app/seed.py`）
- 加 inspiration_image 的 A/B `PromptVersion` 入库（幂等：不存在才建）：
  `stage="A"/"B", category_key="inspiration_image", status="published", pipeline_scope="full_pipeline", version="inspiration-a-v1"/"inspiration-b-v1"`，
  system_prompt/user_prompt 从两个 prompt 文件读（参考现有 split_prompts 的 load 方式，或直接内联文件内容）。
- 现有的 inspiration_image v3 config seed（我上一轮加的）保持。

### 4. 更新受影响测试
- 框架层测试里引用旧占位维度（spatial_originality/design_trendiness/product_form_language/artistic_expression 等）的断言 → 改为新 6/5 维度。
- worker_v3_shadow / worker_v3_authoritative / seed 相关测试若断言旧维度，一并同步。
- 新增：验证 build_inspiration_subcategory_dimensions 产出 6/5 维度、权重和=1、能过校验器；端到端 evaluate_one 一类全 grade5 → 高分 L1、一类 ≥80 且 hard_defects 非空 → 压 79、红线 → L5/≤49。

## 输出
- 完成写 `ADR33_TASK3B_DONE.md`：文件清单、维度 key 与权重、A/B 剥离说明、seed 方式、测试改动、不确定点。
- 报告 migration：本任务**不需要 migration**（无新列/表）。
- 你无法跑 pytest/git，静态 `ast.parse` 自检即可，其余交 OpenClaw。
