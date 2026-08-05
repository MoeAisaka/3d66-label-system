# 标签实验台 v3-only 类目合同迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development task-by-task. This production migration is intentionally executed inline in one isolated worktree and delivered as one reviewable commit; do not deploy, push, or run the 100-image baseline.

**Goal:** 让空间图、灵感图、材质图、PDF 文本四类目的新完整流水线与简易流水线统一只使用 active `CategoryEvaluationV3Config`，彻底停止运行时 v1 回退和旧维度写入口，同时保留历史结果与旧 schema 的只读解释能力。

**Architecture:** `CategoryEvaluationV3Config` 成为新任务唯一权威类目合同。seed 以灵感图人工校准合同和 A/B prompt 为模板，为其余三类目生成 category-safe 的独立 active 初版；完整入队与基准 run 在创建时强制解析并冻结 active v3 bundle，worker 只消费冻结/active v3 bundle，缺失或损坏时中文 fail-closed。`DimensionSchema` 和 category-pipeline v1 仅保留历史读取，不再接受写入。

**Tech Stack:** FastAPI、SQLAlchemy 2、SQLite migration/seed、pytest、React 19、TypeScript 7、Vite 8。

---

## 根因图（按真实调用点）

1. **seed 留下三个 v1 回退孔**：`backend/app/seed.py::_seed_legacy_placeholder_v3_configs` 为 `space_image/material_image/pdf_text` 写入 8 维 `draft` 占位；`worker_v3_authoritative._load_active_v3_config` 因查不到 active 返回空。
2. **worker 将空配置解释成 v1**：`backend/app/worker_v3_authoritative.py::v3_authoritative_category` 对缺失、非 active、损坏 JSON 均返回 `None`；`backend/app/worker.py::evaluate_job` 随后保留提前计算的 `calculate_score` / prompt-only v1 结果。
3. **两条新任务入口仍冻结 v1**：`backend/app/main.py::_enqueue_jobs` 和 `create_baseline_run` 仍解析 `EvaluationCategoryProfile.dimension_schema_*` / `DimensionSchema`；完整任务快照不冻结 v3 bundle，基准 run 只有“查到才附加”。
4. **旧写面仍可达**：`POST/PUT/DELETE/publish /api/dimension-schemas*` 仍写 registry；`PUT /api/evaluation-categories/{category_key}` 仍可修改 `pipeline_config.dimensions` 与 schema 绑定。
5. **前端旧入口仍有消费者**：`frontend/src/App.tsx` 仍 lazy import 并路由 `DimensionManagerPage`；`frontend/src/lib/evaluation-packages.ts` 仍跳旧路径；基准页仍 GET `/api/dimension-schemas` 并可提交 `dimension_schema_id` / `none`。

## 文件边界

- `backend/app/seed.py`：四类目 active v3 seed、独立 prompt clone、旧占位升级幂等性。
- `backend/app/worker_v3_authoritative.py`：active v3 bundle 的唯一严格解析器与中文 fail-closed 错误。
- `backend/app/worker.py`：删除运行时 v1 权威分支，只执行 v3 authoritative。
- `backend/app/main.py`：完整/基准入口强制并冻结 active v3；旧维度写端点返回 410；类目 profile 仅禁止维度字段变更。
- `backend/app/dimension_schema_registry.py`、`backend/app/models.py`：注明 registry/model 仅为历史只读兼容；canonical JSON/hash 继续供 v3 内部复用。
- `backend/tests/test_inspiration_seed_persistence.py`：四类目 clone、prompt category isolation、校验和幂等升级。
- `backend/tests/test_worker_v3_authoritative.py`：缺/坏 active v3 中文 fail-closed，禁止 v1 回退。
- `backend/tests/test_job_controls.py`、`backend/tests/test_baseline_regression.py`：完整/简易入口冻结 v3、缺合同拒绝、旧选择拒绝。
- `backend/tests/test_dimension_schema.py`、`backend/tests/test_material_packages_api.py`：GET 兼容、所有旧写 410、category dimensions 变化 410。
- `frontend/src/App.tsx`、`frontend/src/pages/system-management-page.tsx`、`frontend/src/lib/evaluation-packages.ts`：旧入口彻底不可路由，唯一指向 v3 合同配置。
- `frontend/src/pages/baseline-regression-page.tsx`、`frontend/src/lib/api.ts`：移除新 run 的旧 schema/关闭维度选择。
- `frontend/scripts/check-v3-only-contract.ts`、`frontend/package.json`：静态契约证明无旧 route/lazy/API selection 消费者。
- `PROJECT_STATUS.md`、`docs/decisions/0033-category-custom-evaluation-base-and-redline.md`、`docs/decisions/README.md`、`docs/manual/v3-config-tutorial-newbie.md`：记录 v3-only 决策、历史读取边界和回滚口径。

### Task 1: RED — 四类目 seed 与 category-safe prompts

- [x] 在 `backend/tests/test_inspiration_seed_persistence.py` 新增测试：执行 seed 两次后四个 `CategoryEvaluationV3Config` 均为 active；三类目不再是 8 维 placeholder；每个合同 `category_key`、`spec_version`、`prompt_bindings` 独立；每个绑定版本存在且 `PromptVersion.category_key` 与类目一致；三块合同继续通过现有 validator；重复执行不增行、不增 revision。
- [x] 运行：`cd backend && /Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -X utf8 -m pytest tests/test_inspiration_seed_persistence.py -q`。预期 RED：三类目为 draft placeholder 且无 category-safe prompt。
- [x] 最小实现：把 `_seed_legacy_placeholder_v3_configs` 替换为 `_seed_v3_only_category_clones`。对每个目标深拷贝人工校准合同，重写 `category_key/spec_version/prompt_bindings`，克隆 A/B prompt 为唯一版本并重写 name/source/change_note；验证后插入或仅升级系统 placeholder，全部 active。
- [x] 复跑同一命令，预期 GREEN。

### Task 2: RED — active v3 resolver 与 worker fail-closed

- [x] 修改 `backend/tests/test_worker_v3_authoritative.py`：原“无 active 返回 None / 老类目 byte-identical v1”断言改为稳定 `V3AuthoritativeError(code=v3_active_config_missing|v3_active_config_invalid)`；冻结 bundle 缺块同样拒绝；active bundle 仍通过。
- [x] 运行：`cd backend && /Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -X utf8 -m pytest tests/test_worker_v3_authoritative.py -q`。预期 RED：当前返回 None。
- [x] 最小实现：严格 resolver 对合法 category key 必须返回完整 active bundle，否则抛中文错误；`v3_authoritative_for_job` 对任何新/旧未完成 job 都不得回退 v1；`worker.py` 删除 `if bundle is not None` 分叉，让异常统一形成 v3 fail-closed scoring。
- [x] 复跑同一命令，预期 GREEN。

### Task 3: RED — 完整/简易入口只冻结 active v3

- [x] 在 `backend/tests/test_job_controls.py` 新增：完整 enqueue 缺 active v3 返回 409 + `v3_active_config_missing`；有 active config 的 job snapshot 必含 `v3_authoritative_bundle`。
- [x] 在 `backend/tests/test_baseline_regression.py` 新增：基准 run 缺 active v3 返回 409；成功 run 必冻结 v3；提交 `dimension_schema_id` 或 `dimension_mode=none/all` 返回 410。
- [x] 分别运行 `cd backend && /Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -m pytest tests/test_job_controls.py -q` 和对应的 `tests/test_baseline_regression.py -q`，确认 RED。
- [x] 在 `main.py` 抽取 HTTP 边界 helper，把严格 resolver 错误映射为中文 409；完整和基准都在写 job 前解析 active v3 并写入 execution snapshot。基准不再解析新任务的 `DimensionSchema`，只允许 `category_default`。
- [x] 复跑精确测试，预期 GREEN。

### Task 4: RED — 下线旧维度写 API，保留 GET

- [x] 将 `backend/tests/test_dimension_schema.py` 旧 CRUD 成功用例改为四类写请求统一 410，detail 指向“类目评测 v3 合同配置”；保留 registry list/detail GET 200 与历史版本 404 测试。
- [x] 在 `backend/tests/test_material_packages_api.py` 新增：类目 profile 的 dimension selection/schema binding 变化返回 410；只改模型/提示词等非维度字段仍沿用原 API。
- [x] 运行 `cd backend && /Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -m pytest tests/test_dimension_schema.py tests/test_material_packages_api.py -q` 确认 RED。
- [x] `main.py` 的 DimensionSchema POST/PUT/DELETE/publish 使用统一 410 helper；`_apply_category_update` 比对当前/候选 dimensions 与 schema binding，只有发生维度写时 410。
- [x] 复跑精确测试，预期 GREEN。

### Task 5: RED — 前端只保留 v3 配置入口

- [x] 新建 `frontend/scripts/check-v3-only-contract.ts`，读取 `App.tsx`、system management、evaluation packages、baseline page，断言：不存在旧 lazy/route/href；存在 v3 href；基准页不存在 `/api/dimension-schemas`、`dimension_schema_id`、`dimension_mode: "none"`。
- [x] 在 `package.json` 增加 `contract:v3-only` 并运行，预期 RED。
- [x] 移除 `App.tsx` 的旧 lazy/route；保留 `dimension-manager-page.tsx` 文件（搜索已证明没有其他 route consumer，但历史代码仍可供审计）；评测包 readiness 跳 v3；基准页删除 schema query/选择状态与旧 payload，仅展示 active v3 合同提示并提交 category default。
- [x] 复跑 `npm run contract:v3-only && npm run lint && npm run build`，预期 GREEN。

### Task 6: 文档、回归与提交

- [x] 更新 ADR-0033 为 v3-only 事实，registry 明确仅历史只读；更新 ADR 索引、PROJECT_STATUS 和新手教程，删除“三类目 draft 8 维待激活”旧描述。
- [x] 运行后端相关集合：`/Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -m pytest backend/tests/test_inspiration_seed_persistence.py backend/tests/test_worker_v3_authoritative.py backend/tests/test_v3_only_migration.py backend/tests/test_job_controls.py backend/tests/test_baseline_regression.py backend/tests/test_dimension_schema.py backend/tests/test_material_packages_api.py backend/tests/test_category_evaluation_v3_config_api.py backend/tests/test_node_correction.py -q`（112 passed）。
- [x] 运行后端全量：`/Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -m pytest backend/tests -q`（1051 passed，1 skipped）。
- [x] 运行前端：`npm run contract:v3-only`、`npm run contract:dimensions`、`npm run contract:node-correction`、`npm run lint`、`npm run build`。
- [x] 运行：`git diff --check`、`git status --short`、凭据关键词检查；确认主仓的 `ADR33_TASK3B_DONE.md` 与 `task3b_claude.log` 不在隔离 worktree diff。
- [x] 自审需求覆盖与历史读取兼容后，只提交一次：`git commit -m "refactor: make category evaluation v3-only"`。不 push、不部署、不运行 100 张基线。

## 数据迁移与回滚

- **迁移方式**：不新增破坏性表迁移。应用启动 seed 幂等插入目标类目的独立 A/B prompt，并把仅由系统生成的三份旧 8 维 placeholder 升级为 active 人工校准 clone；已有 inspiration active 人工版保持。旧 `DimensionSchema`、旧 `EvaluationCategoryProfile` 字段、旧 job/result snapshot 均不删除、不重写。
- **并发/幂等**：依赖现有 `PromptVersion.version` 和 `CategoryEvaluationV3Config.category_key` 唯一约束；重复 seed 通过目标 spec/version 短路。升级仅识别系统 placeholder，遇到无法证明来源的人工配置 fail-closed，不静默覆盖。
- **运行时门禁**：新完整/基准任务在事务写入前必须找到合法 active v3 并冻结 bundle；缺失或损坏不产生 job/run。worker 再次校验冻结 bundle，绝不改用 v1。
- **回滚**：代码回滚该提交即可恢复旧写端点和旧调度逻辑；数据侧无需删除新 rows。若必须逻辑回退，仅可把四类目 active v3 改为 draft 并回滚代码后再接单；在本提交仍运行时改 draft 会按设计阻止新任务。旧 schema/history 始终可读，所以回滚不需要迁移旧评测记录。
- **明确不做**：不迁移旧评测记录、不修改资产 `category_key`、不写公司服务器 DB、不部署、不 push、不触碰黄金集、不跑 100 张基线。
