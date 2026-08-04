# ADR-0033 Task 2b 委派任务书（v3 引擎权威化 · 仅 inspiration_image 新类目 · 直接换）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。

## 决策背景（Owner 已拍板）

Owner 选择「直接换」（Path B）：新建 `inspiration_image` 类目，直接用 v3 引擎产出**权威** `score`(0-100，越高越好) + `level`，不做影子、不做下游双轨。**下游直接取 `score` 百分值。**

**为什么这是安全的**：`inspiration_image` 在 v1 里**完全不存在**（v1 只有 space_image/material_image/pdf_text），零历史标签、零已发布数据。worker 当前无按类目选引擎的分支。所以这是**给新类目加一条新分支**，不是翻转历史数据——老类目一个字节都不能动。

## 硬边界（违反即失败）

- 只用 Read / Edit / Write / Glob / Grep。禁 Bash/git/网络/安装/运行测试。
- **老类目（space_image/material_image/pdf_text）算分路径必须逐字节不变**。你的分支只对「有 active v3 config 的类目」生效。
- **不得创建、激活、seed 任何生产 v3 config**（那是 Owner 的操作开关）。你只接线「能力」——DB 里没有 active config 时，行为必须与现状完全一致。
- **不改** `scoring.py` / `calculate_score` / L 方向语义 / `PublishedLabel` / 已发布数据 / 任何 migration（列已在 Task1b/2 齐备，无需新 migration）。
- v3 分支的任何异常**不得**污染或中断老类目流程。

## 背景（先读，理解现有机件）

- `backend/app/worker.py`：
  - 权威 `scoring` dict 在 **1620 行**构建（`calculate_score` / `calculate_prompt_only_result` / freeform 三选一），随后 `scoring["dimension_mode"]`、`scoring["dimension_selection"]` 补字段，最终写入 `EvaluationResult.score/level/confidence/needs_review/scoring_json`。
  - 1687 行附近已有 Task1/1b 的**影子块**（`compute_v3_shadow` / `fetch_v3_specific_grades` / `resolve_specific_shadow_targets`）——这些机件你要**复用并提升为权威**，不是另造。
  - `EvaluationResult(...)` 构造已有 `level_semantics_version` kwarg（Task2 脚手架，当前恒填 `LEVEL_SEMANTICS_V1_L5_BEST`）。
- `backend/app/worker_v3_shadow.py`：`_load_active_v3_config(db, category_key)`（只读取 active 记录）、`compute_v3_shadow`、`_common_grades_from_aesthetic`（v1 aesthetic → v3 共性 grade 映射，fail-closed）、`fetch_v3_specific_grades`（发特有维度调用B）、`resolve_specific_shadow_targets`。
- `backend/app/inspiration_category_seed.py`：`build_inspiration_v3_contract` / `build_inspiration_classification_map` / `build_inspiration_subcategory_dimensions` / `evaluate_one(...)`。`evaluate_one` 返回 `{"redline","resolved","result"}`，`result` 含 `score`(0-100高好)、`level`、`raw_level`、`track_key`、`level_semantics_version="doc-l5-worst-v1"`、`steps`。
- `backend/app/category_evaluation_v3_config_api.py`：v3 config CRUD（Task3）。config 存 `contract_json`/`classification_map_json`/`subcategory_dimensions_json`。

## 必做

### 1. 新建 `backend/app/worker_v3_authoritative.py`（权威路由的唯一新载体）

纯函数 + 一个 async 编排函数，完全隔离、可脱离 worker 单测：

- `def v3_authoritative_category(db, category_key) -> dict | None`
  只读。查 `_load_active_v3_config(db, category_key)`；有 active config 且能装配出合法 contract/classification_map/subcategory_dimensions → 返回 `{"contract":..., "classification_map":..., "subcategory_dimensions":...}`（从 config 的三个 json 字段解析）；否则返回 `None`。任何异常 → 返回 `None`（fail-closed 到老引擎）。**绝不 raise。**

- `async def evaluate_v3_authoritative(client, image_path, mime_type, *, v3_bundle, precheck, aesthetic) -> dict`
  编排权威 v3 评分：
  1. 从 `aesthetic` 用 `_common_grades_from_aesthetic` 映射共性 grade（复用 shadow 模块的函数）。
  2. 用 `resolve_subcategory`（seed/subcategory_resolver）从 precheck 解析 track_key；若该 track 有非空特有维度组 → 调 `fetch_v3_specific_grades`（**权威模式**：这里 enabled 恒为 True，因为已确定要走 v3）拿特有 grade。
  3. 调 `evaluate_one(contract=..., classification_map=..., subcategory_dimensions=..., precheck=..., common_grades_by_track={track_key: common}, specific_grades_by_track={track_key: specific})`。
  4. 返回 `evaluate_one` 的 `result`（含 score/level/level_semantics_version/track_key/steps）。
  - **与影子的区别**：影子失败 → skip/None（不影响权威）；这里是权威路径，若共性/特有 grade 拿不齐 → **必须 fail-closed 报明确错误**（raise 一个专用异常 `V3AuthoritativeError`），由 worker 侧决定如何处理（见第 2 点），**绝不静默降级成老引擎给出误导性分数**。

### 2. `backend/app/worker.py`：加权威分支（最小侵入）

- 在 1620 行构建 `scoring` 之前，先判断：`v3_bundle = v3_authoritative_category(db_or_readonly, job.category_key)`。
  - **注意**：1620 行处于 `session_scope()` 之前。你需要一个只读 DB 句柄来查 active config。参照影子块（它在 `session_scope` 内用 `db`）。**推荐**：把 v3 判定与评分挪到 `session_scope()` 内、紧邻影子块之前，拿到 `scoring` 后再进入 `EvaluationResult` 构造。保持老类目路径的原有时序不变。
  - 若 `v3_bundle is not None`（该类目走 v3 权威）：
    - `v3_result = await evaluate_v3_authoritative(client, model_image_path, model_mime_type, v3_bundle=v3_bundle, precheck=precheck, aesthetic=aesthetic)`
    - 构建 `scoring` dict，**映射成与老 scoring 同结构**：`score=v3_result["score"]`、`level=v3_result["level"]`、`confidence` 用 precheck 的 primary_confidence（或 None）、`needs_review` 依 redline/raw_level 规则、`engine_version` 用 v3 的 aggregator_version、加 `"scoring_mode":"v3_authoritative"`、`"track_key"`、`"steps"`、`"level_semantics_version": v3_result["level_semantics_version"]`。
    - `EvaluationResult(...)` 的 `level_semantics_version` kwarg 对 v3 类目填 `v3_result["level_semantics_version"]`（即 `doc-l5-worst-v1`）；老类目仍填 `LEVEL_SEMANTICS_V1_L5_BEST`。
    - 若 `evaluate_v3_authoritative` 抛 `V3AuthoritativeError`：按现有失败语义处理（记录到 scoring 的 review_reasons + needs_review=True + score/level=None + 一个 `not_formal_reason`），**不得**掉进 `calculate_score` 给出老引擎的分。
  - 若 `v3_bundle is None`（老类目）：**完全走现有 `calculate_score` 路径，一行不改。**
- 影子块（Task1/1b）：v3 权威类目下影子已无意义，可跳过（`if v3_bundle is None:` 才跑影子），避免重复调用与浪费。老类目影子行为不变。

### 3. 确认下游 `score` 暴露

- 核对 `label_export.py` 的 `export_row` 已含 `score` 字段（现状已含）。若 v3 score 能正常落到 `EvaluationResult.score` → PublishedLabel payload → 导出，则无需改动；如发现断链，记 TODO 给 OpenClaw（**不要**擅改发布/导出逻辑）。

### 4. 测试 `backend/tests/test_worker_v3_authoritative.py`（独立内存 SQLite，StaticPool）

- `v3_authoritative_category`：无 active config → None；有 active config → 返回装配好的 bundle；config json 损坏 → None（fail-closed）。
- `evaluate_v3_authoritative`（用 fake client，不打真实网络，沿用仓库 `asyncio.run` 约定）：
  - 红线命中 → result 为 hard_reject / L5 / score≤49。
  - 正常 in_scope → 产出 score(0-100) + level + level_semantics_version=doc-l5-worst-v1 + track_key。
  - 共性 grade 拿不齐 → raise `V3AuthoritativeError`（fail-closed，不降级）。
  - 特有 grade 调用B 异常 → raise `V3AuthoritativeError`。
- **非侵入证明（最重要）**：构造一个 `space_image`（无 v3 config）场景，断言 `v3_authoritative_category` 返回 None、老 `calculate_score` 路径产出与未接入前完全一致的 scoring（可用固定输入对比关键字段 score/level/engine_version）。
- score 方向断言：v3 高质量输入 → 高 score + 低 L（L1/L2）；低质量 → 低 score + 高 L（L4/L5）。验证「score 越高越好」与「level L5 最差」并存且不矛盾。

## 完成后写 `ADR33_TASK2B_DONE.md`

报告：新建/修改文件清单；worker 分支的精确插入位置与时序；老类目非侵入如何保证（对照断言）；`evaluate_v3_authoritative` 的 fail-closed 行为；score→下游链路是否完整或 TODO；level_semantics 打标；不确定点。**声明未创建/激活任何生产 v3 config、未跑 Bash/git/测试。** 结尾 `DONE`。

## 交接

写完 commit 到 `agent/adr33-eval-base-framework`，`git bundle create /Users/yukina/OpenClaw/105-研发-标签实验台PhaseB-20260729-artifacts/task2b.bundle b7994d7..HEAD`（base 用你当前 HEAD 的实际值，OpenClaw 会核对），OpenClaw 侧 fetch + 全量三平台验证 + 注册 + 提交 codeup。
