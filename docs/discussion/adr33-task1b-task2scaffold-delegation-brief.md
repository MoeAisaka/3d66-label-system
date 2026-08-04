# ADR-0033 委派任务书（一次迭代：Task1b v3特有维度影子信号 + Task2安全脚手架）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。测试/构建/git 由 OpenClaw 侧做。

本任务两部分，都在**影子/脚手架层**，绝不切换线上默认、绝不动已发布数据。

═══════════════════════════════════════════
## PART A：v3 特有维度影子信号（让非红线灵感图也能产出真实影子分）
═══════════════════════════════════════════

### 背景（先读）
- Task 1 已落地 v3 影子评分（`worker_v3_shadow.py` / `EvaluationResult.v3_shadow_json` / 开关 `ADR33_V3_SHADOW_ENABLED` 默认关）。
- 但非红线灵感图目前 skip：v1 的调用B（`aesthetic["dimensions"]`）不产出 v3 特有维度 key（`spatial_originality`/`design_trendiness`/`product_form_language`/`artistic_expression`/`visual_impact`），所以 `compute_v3_shadow` 拿不到特有维度 grade。
- 现在补这个信号：**加一路专门的「v3 特有维度影子调用B」**，只在影子开关开时、best-effort 地跑，产出这些特有维度的 1-5 grade，喂给 `evaluate_one`。

### 必读
- `backend/app/worker.py`：调用B 发起范式在 ~1395（`await client.chat_json(system_prompt, user_prompt, image_path=..., mime_type=...)` → `response.parsed`）。`model_image_path`/`model_mime_type`/`client`(DoubaoClient) 在算分点已就绪。
- `backend/app/worker_v3_shadow.py`：Task1 的影子模块。你要扩展它。
- `backend/app/inspiration_category_seed.py`：`_SPECIFIC_DIMENSIONS`（各赛道特有维度 key+label）；`evaluate_one` 需要 `specific_grades_by_track`。
- `backend/app/dimension_composition.py`：特有维度组结构。

### 必做
1. **v3 特有维度影子 prompt**（新建 `backend/app/worker_v3_shadow_prompt.py` 或放进 worker_v3_shadow.py）：
   - 一个函数，输入某赛道的特有维度定义（key+label 列表），产出 system+user prompt，要求模型**只**对这些特有维度输出 `{dimensions: {key: {grade: 1-5, evidence: "..."}}}`，严格 JSON、不含其他字段。
   - prompt 用中文，讲清每个特有维度的含义（从 seed 的 label 取），1-5 档语义与 v1 一致（1 最差…5 最好，注意这是**维度 grade** 方向，与最终 L 等级方向无关）。
2. **影子调用B 执行**（在 worker_v3_shadow.py 里，新函数 `async def fetch_v3_specific_grades(client, image_path, mime_type, track_key, specific_dims, *, enabled) -> dict | None`）：
   - 开关关 → None。
   - 开关开 → try/except 整段包住，调 `client.chat_json(...)`，解析出 `{key: grade}` map；任何异常/解析失败 → 返回 `{"status":"error",...}` 形态或 None（让上层 skip），**绝不 raise**。
3. **接进 worker**（worker.py，非侵入）：
   - 影子计算路径里，若解析出的赛道有非空特有维度组，则**先跑这路影子B** 拿到 specific grades，再连同共性 grades 一起喂 `evaluate_one`。
   - ⚠️ 硬约束：这路影子B 是**额外的模型调用**，必须：①只在 `v3_shadow_enabled()` 为真时发起；②失败不影响权威流程也不影响已算好的权威 `scoring`；③不改任何权威 kwarg。worker.py 里只允许在 Task1 已有的影子代码块内扩展，别的地方不许动。
   - 若影子B 失败或拿不到全部特有维度 grade → 影子 payload 记 `{"status":"skipped","reason":"specific_grade_shadow_unavailable"}`（保持 Task1 的 fail-closed 风格）。成功 → `status="ok"` 且含完整 v3 结果。
4. **category_key 对齐**：Task1 DONE 提到 worker 的 `current_job.category_key` 与 v3 config 的 key 可能命名不一致。**本任务不猜映射**——若不一致就仍走 `no_active_v3_config` skip。但请在 worker_v3_shadow.py 顶部用注释记录：期望 category_key 对齐方式待 OpenClaw 确认（可能需要一个 alias 映射表）。
5. **测试**（扩展 `backend/tests/test_worker_v3_shadow.py` 或新建 `test_worker_v3_shadow_specific.py`）：
   - 影子B prompt 生成正确（含全部特有维度 key）。
   - `fetch_v3_specific_grades`：开关关→None；mock client 返回合法 grades→map 正确；mock client 抛异常→不 raise、返回 error/None。
   - 端到端（mock 掉 client）：有 active config + 特有 grades 齐全 → payload status=ok 且含 result；特有 grades 缺失 → skipped。
   - **非侵入证明**：无论影子B 成功/失败，权威 scoring 不变（沿用 Task1 的断言风格）。
   - client 用 mock/monkeypatch，**不真发网络请求**。

═══════════════════════════════════════════
## PART B：Task 2 安全脚手架（level_semantics_version 双轨，绝不翻转已发布数据）
═══════════════════════════════════════════

### 背景与红线
- 现状：v1 `scoring.py` 的 L 方向是 **L5=最高分**；ADR-0033 v3 是 **L5=最差**。两套语义相反。
- 全局翻转迁移是最高风险（动已发布 PublishedLabel），**本任务绝不做翻转**。只做**版本标记脚手架**，让未来能双轨共存、可区分「这条 level 是哪套语义」。
- 🔒 **不改任何现有 level 值**、**不改 scoring.py 的算分**、**不改 PublishedLabel 已有数据**、**不加 CHECK 约束到现有列**、**不切换默认**。

### 必做
1. **常量与工具**（新建 `backend/app/level_semantics.py`）：
   - 定义两个语义版本常量：`LEVEL_SEMANTICS_V1_L5_BEST = "v1-l5-best"`（现状 v1，这个是新的、v1 之前没显式命名过）；v3 的**直接复用现有** `category_evaluation_aggregator.LEVEL_SEMANTICS_VERSION`（值 = `"doc-l5-worst-v1"`，已确认存在，**import 它、不要重新定义**）。
   - 一个纯函数 `def describe_level_semantics(version: str) -> dict`：返回该版本下 L1..L5 的方向说明（哪端最优）。纯数据，无副作用。
   - **不**提供任何「翻转/转换 level」的函数（那是未来门禁任务，不在此）。
2. **只读标注（不迁移）**：
   - 给 `EvaluationResult` 加一个 nullable 列 `level_semantics_version: Mapped[str | None]`（默认 None）。migration **54** `add_evaluation_result_level_semantics`（幂等 + 表存在性守卫，仿 Task1 migration 53 的守卫写法：空 result_columns 直接 return）。
   - worker.py 里，权威 result 构造时**新增一个 kwarg** `level_semantics_version=LEVEL_SEMANTICS_V1_L5_BEST`（如实标注：当前权威分就是 v1 语义）。**其余 kwarg 一字不改。** 这只是如实打标签，不改任何 level 值、不改算分。
   - v3 影子 payload 里已经带 v3 的语义（seed 自带），无需额外处理。
3. **测试**（新建 `backend/tests/test_level_semantics.py`）：
   - 两个版本常量存在且不同；`describe_level_semantics` 对两版本返回正确方向；未知版本安全处理（返回 unknown 而非抛错，或按你判断的 fail-closed）。
   - migration 54 幂等 + 表守卫（缺表不炸）。
   - 断言：加了 `level_semantics_version` 列后，`EvaluationResult` 的 score/level 等权威字段与迁移前一致（非侵入）。

═══════════════════════════════════════════
## 通用约束
- 只用 Read/Edit/Write/Glob/Grep。禁 Bash/git/测试/build/起服务/真实migration。
- migration 号：Task1 已用到 53，你 PART B 用 **54**（先 Grep 确认当前最大号，顺延）。同时同步 `tests/test_migration.py` 的 `MIGRATION_NAMES` 追加 54 的 name、以及 `tests/test_dimension_calibration.py` 里「latest migration」断言（若它硬编号，改成 54）。**这两处测试同步务必做**，否则 OpenClaw 验证会红。
- 非侵入 + 默认关 + best-effort 是最高优先级。任何与现有生产文件冲突、或映射/语义有歧义 → 停下写进 DONE 问，别硬猜。
- 完成写 `ADR33_TASK1B_TASK2SCAFFOLD_DONE.md`：列新建/改动文件、migration 号、开关、你实现了哪些、skip/TODO 了哪些、category_key 对齐建议、任何不确定点。结尾输出 `DONE`。

## 提醒
- 你不能跑测试/build/git。OpenClaw 会 py_compile + pytest + tsc + build + 三平台验证 + 双推 codeup。
- PART A 的影子B 是真实模型调用，务必确认只在开关开时发起、且 mock 覆盖测试不打真实网络。
