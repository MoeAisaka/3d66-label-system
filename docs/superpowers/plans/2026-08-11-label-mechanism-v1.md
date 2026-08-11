# TPENG 标签实验台统一底座：标签机制与模型管理 v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 在不破坏现有评测/回归/发布链的前提下，为 TPENG 标签实验台统一底座落地双发布轴、类目机制合同与列表型统一模型管理中心，并为存量重跑建立可追溯门禁。

**Architecture:** 复用现有 `CategoryEvaluationV3Config`、`EvaluationPackage`、`BaselineRegressionRun`、`ReviewPanel` 和 `LabelRelease/PublishedLabel/Outbox`。新增一层增量模型注册表与机制/重跑发布记录，旧 API 继续作为兼容适配；所有任务与回归在入队时冻结非密快照，发布由服务端事务门禁控制。

**Tech Stack:** FastAPI、SQLAlchemy、SQLite additive migrations、Pydantic、React 19、TypeScript、React Query、Vite、pytest。

## Global Constraints

- 双发布轴独立：机制启用不自动触发存量重跑，也不覆盖历史标签。
- AI 只能生成候选机制和回归报告，不能直接发布。
- 新机制无执行器、合同损坏或回归证据不足时 fail-closed。
- API Key 只保存安全引用；不得输出真实密钥、正式库数据或真实模型批量调用。
- 下游只读取正式发布事实。
- 产品定位与命名按 ADR-0042 对齐，但不改变本计划的范围、非目标、权限、回退和验收。
- 修改后运行后端测试、前端正式构建、浏览器验收、`git diff --check`，并更新状态/ADR。

### Task 1: Contract and inventory

**Files:**
- Create: `docs/decisions/0041-label-mechanism-v1-execution-contract.md`
- Create: `docs/superpowers/plans/2026-08-11-label-mechanism-v1.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Produces: frozen terms for model roles, protocol allowlist, release axes, rerun gates, rollback.

- [x] **Step 1: Record the accepted contract and explicit non-goals.**
- [x] **Step 2: Map existing entities and compatibility endpoints.**
- [x] **Step 3: Update project status only after implementation evidence exists.**

### Task 2: Model registry domain and migration

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/seed.py`
- Create: `backend/tests/test_model_registry.py`

**Interfaces:**
- Consumes: existing `ModelConfig`, `OptimizerConfig`, `ModelNodeBinding`, credential helpers.
- Produces: `ModelRegistryEntry` payload with `role in {main,tuning,benchmark}`, `protocol`, `capabilities`, `max_input_tokens`, pricing, concurrency, budget, `thinking_mode`, `level`, `active`, and `has_api_key`.

- [x] **Step 1: Write failing migration/model tests.**

```python
def test_model_registry_migrates_existing_configs_and_defaults_role(client):
    response = client.get("/api/model-registry")
    assert response.status_code == 200
    assert all(item["role"] in {"main", "tuning", "benchmark"} for item in response.json()["items"])
```

- [x] **Step 2: Run `pytest backend/tests/test_model_registry.py -q` and confirm the endpoint/role is missing.**
- [x] **Step 3: Add an additive `model_registry_entries` table and immutable role/snapshot fields; seed compatibility rows from existing main and optimizer configs without copying secrets.**
- [x] **Step 4: Run the focused tests and confirm migration is idempotent and foreign-key safe.**
- [x] **Step 5: Refactor only after green; keep old tables as compatibility storage.**

### Task 3: Registry API and safe operations

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/security.py` only if a scoped credential-reference helper is needed
- Modify: `backend/tests/test_model_registry.py`

**Interfaces:**
- Produces:
  - `GET /api/model-registry?role=&active=`
  - `POST /api/model-registry`
  - `PUT /api/model-registry/{id}`
  - `POST /api/model-registry/{id}/test`
  - `POST /api/model-registry/{id}/activate`
  - `POST /api/model-registry/{id}/deactivate`
  - `GET /api/model-registry/{id}/bindings`

- [x] **Step 1: Add failing API tests for list filtering, validation, safe key handling, activation/deactivation and protocol allowlist.**
- [x] **Step 2: Run the focused tests and verify expected failures.**
- [x] **Step 3: Implement minimal DTOs and routes.**
- [x] **Step 4: Add fail-closed checks: tuning entries cannot bind to evaluation nodes; unsupported protocol or missing required capability is rejected; deactivation preserves snapshots.**
- [x] **Step 5: Run focused API tests and then the existing model protocol/security tests.**

### Task 4: Mechanism contract and dual release axes

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/category_evaluation_contract.py`
- Modify: `backend/app/category_evaluation_v3_config_api.py`
- Modify: `backend/app/evaluation_packages.py`
- Modify: `backend/app/label_governance.py`
- Create: `backend/tests/test_mechanism_release_axes.py`

**Interfaces:**
- Produces: mechanism activation and label-fact publication records with independent revision IDs; candidate publication rejects missing executor or regression evidence; rerun records freeze scope and snapshots.

- [x] **Step 1: Write failing tests for independent mechanism activation, no implicit rerun, label publication, rollback, and rerun snapshot immutability.**
- [x] **Step 2: Run `pytest backend/tests/test_mechanism_release_axes.py -q` and confirm failures.**
- [x] **Step 3: Add additive release/rerun tables and transactional service functions.**
- [x] **Step 4: Wire existing package release through the mechanism gate while preserving the independent existing label publication/rollback axis and old history.**
- [x] **Step 5: Run focused release tests plus existing package, governance and regression suites.**

### Task 5: List-oriented model management UI

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts` only for typed errors/registry helpers if needed
- Modify: `frontend/src/pages/model-page.tsx`
- Modify: `frontend/src/pages/system-management-page.tsx`
- Create: `frontend/scripts/check-model-registry-contract.ts`

**Interfaces:**
- Consumes: `/api/model-registry` and binding endpoints.
- Produces: a dense table with role, provider/protocol, model ID, capabilities, limits/pricing, thinking, level, credential status and active status; new/edit drawer; safe test and activation controls.

- [x] **Step 1: Add a failing contract script asserting role labels, drawer entry points, masked credentials and no raw `api_key` rendering.**
- [x] **Step 2: Run `npm run contract:model-registry` and verify failure.**
- [x] **Step 3: Implement the list + drawer UI using existing visual rules and React Query invalidation.**
- [x] **Step 4: Run TypeScript build and the contract script.**
- [x] **Step 5: Browser-check desktop and 390×844 flows, including create/edit/test/toggle and no console errors.**

### Task 6: Verification and handoff

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/decisions/0041-label-mechanism-v1-execution-contract.md` if implementation notes are needed

- [x] **Step 1: Run backend focused and full suites.**
- [x] **Step 2: Run frontend contract scripts, TypeScript build and Vite production build.**
- [x] **Step 3: Run `git diff --check` and inspect `git status --short`.**
- [x] **Step 4: Perform browser acceptance and capture evidence paths.**
- [x] **Step 5: Update status with actual results, remaining risks, rollback location and uncompleted items.**

### Task 7: Align the frozen TPENG product positioning and publish the branch

**Files:**
- Create: `docs/decisions/0042-unified-labellab-product-carrier.md`
- Modify: `docs/decisions/README.md`
- Modify: `docs/decisions/0041-label-mechanism-v1-execution-contract.md`
- Modify: `PRODUCT.md`
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/superpowers/plans/2026-08-11-label-mechanism-v1.md`

**Interfaces:**
- Consumes: the accepted v1 execution contract and the Owner-frozen TPENG product-positioning decision.
- Produces: one canonical product name and PRD naming rule without changing code, database, API, permissions, non-goals or acceptance gates.

- [x] **Step 1: Record the product-positioning decision as ADR-0042.**
- [x] **Step 2: Align ADR-0041, `PRODUCT.md` and `PROJECT_STATUS.md` to the unified carrier terminology.**
- [x] **Step 3: Search for conflicting “two projects” or “evaluation-only tool” wording and correct only active authoritative documents.**
- [x] **Step 4: Run the existing deterministic verification commands and `git diff --check`.**
- [ ] **Step 5: Commit the complete v1 candidate, push `codex/label-mechanism-v1` to Codeup and create a merge request targeting `main`.**
