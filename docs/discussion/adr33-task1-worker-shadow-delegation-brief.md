# ADR-0033 Task 1 委派任务书（Phase 4 worker 灰度旁挂 · v3 影子评分）

给执行器（MacBook Claude Fable 5）。OpenClaw 控制与验收；你是唯一写入者。测试/构建/git 由 OpenClaw 侧做。

## 定位与安全边界（先读，这是全任务的核心约束）

目标：让 worker 在跑完**现有权威 v1 算分**之后，**额外**用 ADR-0033 v3 引擎对同一张图算一遍「影子分」，把影子结果**旁存**到 EvaluationResult 的一个新 nullable 字段里，用于日后对比/金丝雀。

**灰度旁挂 = 绝对非侵入。以下是硬红线，违反任一条即任务失败**：
- 🔒 **权威结果一个比特都不能变**：`result.score` / `result.level` / `result.confidence` / `result.needs_review` / `result.scoring_json` / `result.engine_version` 全部保持现状，仍由现有 `calculate_score` / `calculate_prompt_only_result` 决定。v3 只写它自己的新影子字段。
- 🔒 **默认关**：影子计算受一个默认 `False` 的开关控制（见下）。开关关时 worker 行为与现在**逐字节一致**。
- 🔒 **best-effort 且吞异常**：v3 影子计算**整体包在 try/except** 里，任何异常只记 log + 把影子字段写成 `{"status":"error","error":"..."}`，**绝不 raise、绝不中断**权威流程。v3 引擎再怎么炸都不能影响一张图的正常评测和入库。
- 🔒 **不改** `scoring.py` / `calculate_score` / `category_pipeline.py` / L 方向语义 / `PublishedLabel` / 任何已发布数据。
- 🔒 **不切换默认引擎**：本任务只「旁存影子」，不让 v3 结果参与任何线上判定、复审、发布、回归判定。真正切换是后续单独门禁任务。

**边界**：只用 Read / Edit / Write / Glob / Grep。禁 Bash / git / 网络 / 安装 / 运行测试 / 起服务 / 真实 migration。

## 必读（理解现状，照抄风格）

- `backend/app/worker.py`：
  - 算分调用点在 ~1520 / ~1633（`calculate_score(...)` / `calculate_prompt_only_result(...)`），结果进 `preliminary_scoring` → 最终 `scoring`。
  - result 持久化块在 ~1700-1748（`result = EvaluationResult(... score=scoring.get("score"), level=scoring.get("level") ...)`）。**你的影子字段在这个构造里多加一个 kwarg，别的 kwarg 不许动。**
  - `precheck`、`aesthetic`（调用B的维度结果）、`dimension_definition` 这些变量在算分点已就绪——v3 影子计算复用它们，不额外调用模型。
- `backend/app/inspiration_category_seed.py`：`evaluate_one(contract, classification_map, subcategory_dimensions, precheck, common_grades_by_track, specific_grades_by_track)` —— v3 端到端确定性评分入口。
- `backend/app/category_evaluation_v3_config_api.py` + 新表 `CategoryEvaluationV3Config`（models.py，Task 3 刚加）：v3 合同的持久化来源。影子计算要用**已落库的 active v3 config**（按 category_key 查；查不到或无 active 就跳过影子，记 `{"status":"skipped","reason":"no_active_v3_config"}`）。
- `backend/app/category_evaluation_preview_api.py`：`evaluate_one` 的调用范式 + `_coded_400` 风格（本任务不需要 400，但学它怎么把框架结果转 dict）。
- `backend/app/models.py`：`EvaluationResult`(1523 行) 字段风格；`CategoryEvaluationV3Config`。
- `backend/app/migrations/runner.py`：migration 登记范式（**集中在 runner.py 的 MIGRATIONS 列表**，不是独立文件；当前最大 = 52，你加 **53**）。

## 必做

### 1. 新增开关（配置读取，默认关）
- 影子开关默认 `False`。实现方式：读环境变量 `ADR33_V3_SHADOW_ENABLED`（`"1"/"true"` 才开），封成一个小函数 `def _v3_shadow_enabled() -> bool` 放在 worker.py 顶部工具区。**不要**改 model_config 表或加数据库开关（避免动生产配置结构）。

### 2. 新增 result 影子字段（models.py + migration 53）
- `EvaluationResult` 追加：`v3_shadow_json: Mapped[str | None] = mapped_column(Text, nullable=True)`（放在 `scoring_json` 附近）。
- migration **53** `add_evaluation_result_v3_shadow`：`ALTER TABLE evaluation_results ADD COLUMN v3_shadow_json TEXT`（幂等：先 PRAGMA table_info 查列是否存在，仿照 runner.py 里已有的「加列前先查」范式，如 `_migration_005`）。登记进 MIGRATIONS 列表。

### 3. worker 影子计算（worker.py，唯一逻辑改动）
- 在权威 `scoring` 算完、`result = EvaluationResult(...)` 构造**之前**，插入一段影子计算，产出 `v3_shadow_payload: dict`：
  - 开关关 → `v3_shadow_payload = None`（字段留空）。
  - 开关开 → 整段包 try/except：
    1. 取 `category_key`（从 job/asset 的类目；worker 里已有类目上下文，复用现成变量，找不到就 skip）。
    2. 查该 category_key 的 **active** `CategoryEvaluationV3Config`（用传入的 `db` session，只读 SELECT）。无 → `{"status":"skipped","reason":"no_active_v3_config"}`。
    3. 有 → 反序列化 contract/classification_map/subcategory_dimensions，把**调用A的 precheck**、**调用B的维度 grade**（从 `aesthetic` 里映射成 evaluate_one 需要的 `common_grades_by_track`/`specific_grades_by_track`；映射不确定时用最保守方式：能映射的映射，映射不出就 skip 并在 payload 里记 reason）喂给 `evaluate_one`。
    4. 成功 → `{"status":"ok","engine":"adr33-v3","config_revision":..., "result": <evaluate_one 输出>}`。
    5. 任何异常 → `{"status":"error","error":str(e)[:500]}` + `logging` 记 warning。**不 raise。**
- 在 `EvaluationResult(...)` 构造里加一个 kwarg：`v3_shadow_json=(json.dumps(v3_shadow_payload, ensure_ascii=False) if v3_shadow_payload is not None else None)`。**其余 kwarg 一字不改。**
- ⚠️ 若你发现「把 aesthetic 映射成 evaluate_one 的 grades」这一步有歧义/风险（v3 的 track/维度 key 与 v1 的 aesthetic 维度不一定一一对应），**不要硬猜**：这种情况就让影子 payload 记 `{"status":"skipped","reason":"grade_mapping_unavailable","detail":"..."}`，把「精确映射」留成 TODO 写进 DONE 文件问 OpenClaw。**保证非侵入优先于影子功能完整。**

### 4. 测试（新建 `backend/tests/test_worker_v3_shadow.py`）
- 用独立临时 DB（照 test_category_evaluation_v3_config_api.py 的隔离）。
- 覆盖（**不需要真起 worker 全流程**，可直接测你抽出来的影子函数 + 开关）：
  1. **开关关**（默认）：影子函数返回 None / 不产生任何写入副作用。
  2. 开关开 + 无 active v3 config → payload `status=skipped`。
  3. 开关开 + 有 active config + 正常输入 → payload `status=ok` 且含 result。
  4. 开关开 + evaluate_one 抛异常（可 monkeypatch）→ payload `status=error`，**不抛出**。
  5. **非侵入证明**：构造一个已知权威 scoring，断言无论影子开关开/关、影子成功/失败，`result.score/level/scoring_json` 完全一致。
- 为可测，**建议把影子计算抽成一个纯函数** `def compute_v3_shadow(db, category_key, precheck, aesthetic, *, enabled) -> dict | None` 放 worker.py（或新建 `backend/app/worker_v3_shadow.py`），worker 主流程只调它。这样既好测又隔离。优先新建 `worker_v3_shadow.py`，worker.py 只加 import + 一行调用 + 一个 kwarg。

## 完成信号
- 写 `ADR33_TASK1_DONE.md`：列新建/修改文件、migration 版本号(53)、开关名、**grade 映射你是精确实现了还是 skip 留 TODO**（重点说明）、任何不确定点。
- 结尾输出 `DONE`。

## 提醒
- 你不能跑测试/build/git。写完就停，OpenClaw 会 py_compile + pytest + tsc + build + 双推 codeup + 三平台验证。
- **非侵入是第一优先级**。宁可影子功能 skip、留 TODO，也不能让 v3 影响权威评测或让 worker 崩。有任何冲突停下写进 DONE 问。
