# TPENG 标签实验台双工作区与中台投影 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 TPENG 标签实验台收敛为“增量评测 / 存量回归”双工作区共享底座，补齐黄金数据集、类目 profile、质量指标、并发运行承载和统一大维表/小表本地投影合同，同时保持当前冻结执行合同、人工发布闸门和事实主权边界不变。

**Architecture:** 前端以 `workflow_kind=incremental|stock` 隔离两条串行链路，复杂编辑与证据下沉到现有抽屉/Dialog；后端沿用现有 `MaterialPackage`、`EvaluationProductionRun`、`BaselineRegressionRun`、`SampleSet`、双发布轴和 Outbox，在其上增加显式工作流上下文、接入幂等组包、质量资产导出和 Projection Contract Registry。真实业务数据库、真实上游系统、搜索/推荐生产和大规模存量 Worker 只保留可验证的本地模拟适配器与 Gap 记录。

**Tech Stack:** FastAPI + SQLAlchemy + SQLite migrations；React 19 + TypeScript + Vite + TanStack Query + Radix Dialog；pytest；前端 `tsc -b`、契约脚本和 Edge 桌面验收。

## Global Constraints

- 产品统一命名为 `TPENG 标签实验台（LabelLab）`；“标签体系重构”不再作为独立项目线。
- 业务类目只提供 profile 合同、字段、提示词、规则、门槛和专用视图，不复制平台通用能力。
- 机制发布轴与标签事实发布轴独立；候选机制必须人工明确启用或拒绝，不能自动采纳。
- `semantic.*`、`quality.*`、`governance.*` 及人工真值、证据、来源、模型/规则/机制版本、审核和发布状态必须可追溯。
- 搜索索引、知识图谱、向量索引以及下游数据库表都是可重建消费投影，不是 Canonical 事实主库。
- 下游默认形态为“一个统一大维表 + 数个职责明确的小表”；本轮只实现 Registry、Manifest、本地模拟适配器和对账验证。
- 桌面端是唯一验收形态；不新增移动端布局目标；主视觉保持荧光绿行动色。
- 不连接或写入真实公司业务数据库，不接真实上游，不部署到 `192.168.1.35:8081`，不推送 Codeup，除非 Owner 另行授权。
- 每个任务先写失败测试，再写最小实现，再运行确定性验证；提交保持小步、可回退。

---

## 文件与边界地图

### 后端

- Modify `backend/app/models.py`: 增加工作流上下文和投影 Registry/Manifest/对账模型；不改变现有 Canonical 发布模型的事实主权。
- Modify `backend/app/migrations/runner.py`: 增加单向、幂等的 v66+ 增量迁移；不重写历史迁移。
- Create `backend/app/projection_contracts.py`: Projection Contract 校验、Manifest 生成、行数/缺失/哈希/版本对账和本地适配器。
- Modify `backend/app/label_governance.py`: 将合法的 `content.created/updated` 本地事件转成可追踪增量组包；重复事件只返回已有记录。
- Modify `backend/app/main.py`: 工作流上下文、质量资产导出、投影 Registry/Manifest/对账、接入组包和字段级指标 API。
- Create `backend/tests/test_workflow_context_api.py`、`test_content_ingress_incremental_routing.py`、`test_projection_contracts.py`、`test_quality_metrics_api.py`；扩展现有样本集、迁移、统一标签平台测试。

### 前端

- Modify `frontend/src/App.tsx`: 增加双工作区一级路由和旧入口兼容重定向，保留当前可用页面。
- Modify `frontend/src/components/app-shell.tsx`: 一级导航改为增量评测、存量回归、运行中心、质量资产、治理与发布；复杂配置只保留入口。
- Create `frontend/src/pages/incremental-workspace-page.tsx`、`frontend/src/pages/stock-workspace-page.tsx`、`frontend/src/pages/operations-center-page.tsx`、`frontend/src/pages/quality-assets-page.tsx`。
- Create `frontend/src/components/workflow-stepper.tsx`、`frontend/src/components/workflow-context-badge.tsx`、`frontend/src/components/projection-reconciliation-drawer.tsx`；复用 `workspace-page.tsx` 的 Drawer/Dialog/焦点恢复。
- Modify `frontend/src/pages/evaluation-packages-page.tsx`、`frontend/src/pages/baseline-regression-page.tsx`、`frontend/src/pages/sample-sets-page.tsx`: 下沉二级内容，保留主线所需状态和动作。
- Modify `frontend/src/lib/api.ts`、`frontend/src/lib/evaluation-packages.ts`、`frontend/src/lib/types.ts`: 增加工作流、质量指标、接入组包和投影 API 类型。
- Create `frontend/scripts/check-dual-workspaces-contract.ts` and extend `package.json` with `contract:dual-workspaces`.

### 文档

- Modify `PROJECT_STATUS.md`: 对齐统一产品命名、当前实施范围、Gap 清单和非目标。
- Create `docs/decisions/0045-dual-workspaces-and-table-projection-contract.md`: 记录双工作区、统一大维表/小表投影和本轮边界；不改写 ADR-0041。
- Create `docs/discussion/tpeng-labellab-gap-register-20260813.md`: 记录真实上游、真实数据库、Embedding、Worker、3D 搜索首个消费切片等后续冻结输入。

---

### Task 1: Add explicit workflow context and preserve the two release axes

**Files:**
- Modify: `backend/app/models.py` around `EvaluationProductionRun` and `BaselineRegressionRun`
- Modify: `backend/app/migrations/runner.py` and migration ledger
- Modify: `backend/app/main.py` production-run and baseline-run payload/request handlers
- Test: `backend/tests/test_workflow_context_api.py`
- Test: `backend/tests/test_migration.py`

**Interfaces:**
- `EvaluationProductionRun.workflow_kind: Literal["incremental", "stock"]`, default `incremental` for existing rows.
- `GET /api/evaluation-production-runs?workflow_kind=incremental|stock` returns only the requested context and includes `workflow_kind` in every item.
- `POST /api/evaluation-production-runs` accepts `workflow_kind` and rejects invalid values before creating jobs.
- Existing mechanism release and label release endpoints keep independent status transitions; no endpoint may infer label publication from mechanism activation.

- [ ] **Step 1: Write the failing migration/API tests.**

```python
def test_production_run_context_is_explicit(client, db):
    response = client.post("/api/evaluation-production-runs", json={
        "material_package_id": 1,
        "category_key": "space_image",
        "workflow_kind": "incremental",
        "idempotency_key": "incremental:test-1",
    })
    assert response.status_code in {201, 409}
    assert response.json()["workflow_kind"] == "incremental"

def test_invalid_workflow_kind_is_rejected(client):
    response = client.post("/api/evaluation-production-runs", json={
        "material_package_id": 1,
        "category_key": "space_image",
        "workflow_kind": "unknown",
        "idempotency_key": "invalid:test-1",
    })
    assert response.status_code == 422
```

- [ ] **Step 2: Run the focused tests and confirm failure.**

Run: `pytest backend/tests/test_workflow_context_api.py backend/tests/test_migration.py -q`

Expected: FAIL because the schema and request/response contracts do not yet contain `workflow_kind`.

- [ ] **Step 3: Add the additive migration and response/request fields.**

Use a new migration that adds a nullable column, backfills existing production runs to `incremental`, then adds a check constraint or application validation. Keep baseline runs explicitly `stock` in their API projection rather than merging their tables. Add a query filter without changing the existing default listing behavior.

- [ ] **Step 4: Run focused and regression tests.**

Run: `pytest backend/tests/test_workflow_context_api.py backend/tests/test_migration.py backend/tests/test_mechanism_release_axes.py -q`

Expected: PASS; mechanism activation and label publishing remain independently gated.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/models.py backend/app/migrations/runner.py backend/app/main.py backend/tests/test_workflow_context_api.py backend/tests/test_migration.py
git commit -m "feat: make workflow context explicit"
```

### Task 2: Create isolated incremental and stock workspaces with a shared stepper

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/app-shell.tsx`
- Create: `frontend/src/pages/incremental-workspace-page.tsx`
- Create: `frontend/src/pages/stock-workspace-page.tsx`
- Create: `frontend/src/components/workflow-stepper.tsx`
- Create: `frontend/src/components/workflow-context-badge.tsx`
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/evaluation-packages.ts`
- Test: `frontend/scripts/check-dual-workspaces-contract.ts`

**Interfaces:**
- Routes: `/workflow/incremental`, `/workflow/stock`, `/workflow/operations`, `/workflow/quality-assets`, `/workflow/governance`.
- `WorkflowKind = "incremental" | "stock"`.
- `WorkflowStep` contains `key`, `label`, `state`, `href`, `required` and `actionLabel`.
- `WorkflowStepper` renders the same shared step component with different step definitions and never renders the other workflow’s actions.

- [ ] **Step 1: Add the contract script with failing assertions.**

```ts
const app = read("src/App.tsx")
const shell = read("src/components/app-shell.tsx")
assert(app.includes('/workflow/incremental'))
assert(app.includes('/workflow/stock'))
assert(shell.includes("增量评测"))
assert(shell.includes("存量回归"))
assert(!shell.includes("开始评测") || shell.includes("增量评测"))
```

- [ ] **Step 2: Run the script and confirm failure.**

Run: `cd frontend && node --experimental-strip-types scripts/check-dual-workspaces-contract.ts`

Expected: FAIL because the new routes and workspaces do not exist.

- [ ] **Step 3: Implement the smallest working route shell.**

Keep existing production-line and baseline-regression pages reachable through secondary links. The incremental page must show: category, ingress/material package, evaluation mechanism, start evaluation, current stage, correction entry, candidate decision entry and publish entry. The stock page must show: category, existing package/golden set, mechanism, start regression, correction entry, candidate decision entry and stock rerun entry. Use drawers for detailed configuration.

- [ ] **Step 4: Run frontend checks.**

Run: `cd frontend && node --experimental-strip-types scripts/check-dual-workspaces-contract.ts && npm run contract:information-architecture && npm run build`

Expected: PASS with desktop layout and no mobile-specific branch.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/App.tsx frontend/src/components/app-shell.tsx frontend/src/pages/incremental-workspace-page.tsx frontend/src/pages/stock-workspace-page.tsx frontend/src/components/workflow-stepper.tsx frontend/src/components/workflow-context-badge.tsx frontend/src/lib/types.ts frontend/src/lib/evaluation-packages.ts frontend/scripts/check-dual-workspaces-contract.ts frontend/package.json
git commit -m "feat: add isolated incremental and stock workspaces"
```

### Task 3: Route local upstream ingress events into incremental packages idempotently

**Files:**
- Modify: `backend/app/label_governance.py`
- Modify: `backend/app/main.py` ingress request/response and package listing
- Modify: `backend/app/models.py` only if an additive routing reference is required
- Create: `backend/tests/test_content_ingress_incremental_routing.py`
- Modify: `frontend/src/pages/incremental-workspace-page.tsx` and `frontend/src/lib/api.ts`

**Interfaces:**
- `POST /api/content-ingress/events` retains `content-ingress-v1`, adds response fields `workflow_kind`, `material_package_id`, `package_created`, `routing_status`.
- Valid non-delete events with a resolvable active category create or reuse one `MaterialPackage` with `source="production_import"`, `category_key` from the event, and a deterministic idempotency key derived from source/event/category.
- Duplicate event payload returns the original package and `duplicate=true`; conflicting payload remains `409 INGRESS_EVENT_CONFLICT`.
- No real remote fetch is introduced; an event without a local asset stays `awaiting_material` and cannot be queued.

- [ ] **Step 1: Write the failing ingress tests.**

```python
def test_content_created_builds_incremental_package(client, sender_headers):
    response = client.post("/api/content-ingress/events", headers=sender_headers, json={
        "event_id": "evt-1",
        "schema_version": "content-ingress-v1",
        "event_type": "content.created",
        "source_system": "upstream-sim",
        "occurred_at": "2026-08-13T00:00:00Z",
        "payload": {"content_id": "asset-1", "category_key": "space_image", "asset_id": 1, "version": "v1"},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_kind"] == "incremental"
    assert body["material_package_id"] is not None

def test_duplicate_content_event_does_not_create_second_package(client, sender_headers):
    first = post_event(client, sender_headers, "evt-dup")
    second = post_event(client, sender_headers, "evt-dup")
    assert second.json()["duplicate"] is True
    assert second.json()["material_package_id"] == first.json()["material_package_id"]
```

- [ ] **Step 2: Run the focused tests and confirm failure.**

Run: `pytest backend/tests/test_content_ingress_incremental_routing.py -q`

Expected: FAIL because ingress currently returns `writes_evaluation_job: false` without package routing.

- [ ] **Step 3: Implement deterministic local routing.**

Resolve the profile before package creation; on missing/incompatible profile return a fail-closed status and do not enqueue evaluation. Use an existing package for the same event-derived key, append only the local asset when absent, and keep the event and package writes in one short transaction.

- [ ] **Step 4: Add the incremental page’s ingress status panel.**

Show received, awaiting material, packaged, blocked and duplicate counts; never show a “production connected” claim for the simulator.

- [ ] **Step 5: Run tests and commit.**

Run: `pytest backend/tests/test_content_ingress_incremental_routing.py backend/tests/test_unified_label_platform.py -q && cd frontend && npm run build`

```bash
git add backend/app/label_governance.py backend/app/main.py backend/app/models.py backend/tests/test_content_ingress_incremental_routing.py frontend/src/pages/incremental-workspace-page.tsx frontend/src/lib/api.ts
git commit -m "feat: route local ingress events into incremental packages"
```

### Task 4: Add operations center for queue, concurrency, retry and recovery evidence

**Files:**
- Create: `frontend/src/pages/operations-center-page.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/app-shell.tsx`
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`
- Test: `frontend/scripts/check-dual-workspaces-contract.ts`
- Optional backend changes only if an existing queue payload lacks a required field; otherwise consume `/api/queues/status`, `/api/jobs`, `/api/circuit-breakers` as-is.

**Interfaces:**
- Operations page reads `/api/queues/status`, `/api/jobs`, `/api/circuit-breakers` and groups by `workflow_kind` when present, otherwise labels legacy jobs `incremental` only when linked to a production run and `stock` when linked to baseline regression.
- The page displays queue depth, active workers, quota, retry count, recovery state, failed jobs and last checkpoint; details open in a drawer.

- [ ] **Step 1: Add contract assertions for the primary operations region.**

```ts
const page = read("src/pages/operations-center-page.tsx")
assert(page.includes("运行中心"))
assert(page.includes("队列"))
assert(page.includes("重试"))
assert(page.includes("恢复"))
assert(page.includes("SecondaryDrawer"))
```

- [ ] **Step 2: Run the script and confirm failure.**

Run: `cd frontend && node --experimental-strip-types scripts/check-dual-workspaces-contract.ts`

- [ ] **Step 3: Implement compact summary cards and drawer detail.**

Keep the primary page focused on actionability; do not flatten raw scheduler JSON or every job history into the first viewport.

- [ ] **Step 4: Verify with API mocks and build.**

Run: `cd frontend && npm run contract:information-architecture && npm run build`

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/pages/operations-center-page.tsx frontend/src/App.tsx frontend/src/components/app-shell.tsx frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/scripts/check-dual-workspaces-contract.ts
git commit -m "feat: add operations center for queue recovery evidence"
```

### Task 5: Consolidate golden datasets into a quality-assets workspace with immutable exports

**Files:**
- Modify: `backend/app/main.py` sample-set APIs and export handlers
- Modify: `backend/app/models.py` only for additive export/version metadata if required
- Modify: `backend/app/migrations/runner.py` if new metadata is required
- Create: `backend/tests/test_quality_assets_api.py`
- Create: `frontend/src/pages/quality-assets-page.tsx`
- Modify: `frontend/src/pages/sample-sets-page.tsx`, `frontend/src/App.tsx`, `frontend/src/components/app-shell.tsx`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`

**Interfaces:**
- `GET /api/quality-assets/summary` returns counts by `kind`, `category_key`, `status`, `truth_complete`.
- Existing `/api/sample-sets` remains backward compatible; the new page uses the same source of truth.
- `POST /api/sample-sets/{id}/export` accepts `{format:"csv"|"json"|"manifest"}` and returns a download with `X-Export-Row-Count`, version, hash and field manifest.
- Locked golden sets reject direct item mutation; every truth edit while draft creates a new `SampleTruthRevision`. No endpoint overwrites a locked version silently.

- [x] **Step 1: Write failing tests for lock/export semantics.**

```python
def test_locked_golden_set_cannot_be_mutated(client, locked_golden_set):
    response = client.patch(f"/api/sample-sets/{locked_golden_set.id}/items/1", json={"note": "change"})
    assert response.status_code == 409

def test_golden_manifest_export_contains_hash_and_revision(client, locked_golden_set):
    response = client.post(f"/api/sample-sets/{locked_golden_set.id}/export", json={"format": "manifest"})
    assert response.status_code == 200
    assert response.headers["X-Export-Row-Count"] == "1"
    body = response.json()
    assert body["sample_set_status"] == "locked"
    assert body["truth_revision"] >= 1
    assert len(body["manifest_hash"]) == 64
```

- [x] **Step 2: Run the tests and confirm failure.**

Run: `pytest backend/tests/test_quality_assets_api.py backend/tests/test_sample_sets.py -q`

- [x] **Step 3: Implement the export contract and lock guard.**

Use canonical JSON ordering for hashes; manifest must include sample-set id/name/category/status, item count, truth revision range, field descriptions and source/version metadata. Exclude credentials, raw model secrets, candidate mechanisms and internal tokens.

- [x] **Step 4: Build the compact quality-assets page.**

Primary view shows dataset list, lock state, completeness, latest revision, export and “open detail” actions. Item-level truth editor, revision history and regression launch remain in a drawer.

- [x] **Step 5: Run backend/frontend checks and commit.**

Run: `pytest backend/tests/test_quality_assets_api.py backend/tests/test_sample_sets.py backend/tests/test_golden_regression.py -q && cd frontend && npm run build`

```bash
git add backend/app/main.py backend/app/models.py backend/app/migrations/runner.py backend/tests/test_quality_assets_api.py frontend/src/pages/quality-assets-page.tsx frontend/src/pages/sample-sets-page.tsx frontend/src/App.tsx frontend/src/components/app-shell.tsx frontend/src/lib/api.ts frontend/src/lib/types.ts
git commit -m "feat: consolidate quality assets and golden exports"
```

### Task 6: Implement versioned Projection Contract Registry and local large-table/small-table adapters

**Files:**
- Create: `backend/app/projection_contracts.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_projection_contracts.py`
- Create: `frontend/src/components/projection-reconciliation-drawer.tsx`
- Modify: `frontend/src/pages/workflow-pages.tsx` or create a focused governance projection page
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`

**Interfaces:**
- `ProjectionContract` fields: `contract_key`, `version`, `target_role`, `table_name`, `environment`, `primary_key`, `field_mappings`, `input_versions`, `mode`, `idempotency_key_template`, `checkpoint`, `reconciliation`, `rollback`, `owner`, `status`.
- `POST /api/projection-contracts` creates a draft contract; `GET /api/projection-contracts` lists versions; `POST /api/projection-contracts/{id}/manifest` builds a deterministic projection manifest from formally published labels only; `POST /api/projection-contracts/{id}/reconcile` runs the local adapter and returns row count, missing count, payload hash and version match.
- Built-in local targets: `unified_dimension_table`, `search_labels_small_table`, `quality_governance_small_table`, all persisted under test/local storage only.
- The adapter rejects candidate mechanisms, unpublished labels, raw model responses and manual-process fields.

- [ ] **Step 1: Write failing contract/adapter tests.**

```python
def test_manifest_reads_only_published_labels(db, published_label, candidate_release):
    manifest = build_projection_manifest(db, contract=dimension_contract())
    assert manifest.row_count == 1
    assert candidate_release.content_key not in manifest.content_keys

def test_reconcile_detects_hash_and_version_drift(db, published_label):
    adapter = LocalProjectionAdapter()
    result = adapter.reconcile(expected_hash="a" * 64, actual_rows=[{"asset_id": "1"}])
    assert result.status == "drift"
    assert result.reason == "payload_hash_mismatch"
```

- [ ] **Step 2: Run the focused tests and confirm failure.**

Run: `pytest backend/tests/test_projection_contracts.py -q`

- [ ] **Step 3: Add immutable/versioned registry models and deterministic manifest logic.**

Use append-only contract versions. Every manifest records asset/version, mechanism version, model version and label release version. A failed table projection never mutates Canonical label rows and creates a reconciliation record with retry/compensation metadata.

- [ ] **Step 4: Add the governance UI drawer.**

Show the unified large table and small-table contracts as compact rows; open mapping, checkpoint, drift and rollback details in a drawer. Do not render full payload JSON on the primary page.

- [ ] **Step 5: Run tests and commit.**

Run: `pytest backend/tests/test_projection_contracts.py backend/tests/test_unified_label_platform.py -q && cd frontend && npm run build`

```bash
git add backend/app/projection_contracts.py backend/app/models.py backend/app/migrations/runner.py backend/app/main.py backend/tests/test_projection_contracts.py frontend/src/components/projection-reconciliation-drawer.tsx frontend/src/lib/api.ts frontend/src/lib/types.ts
git commit -m "feat: add local projection contract registry"
```

### Task 7: Expose field-level accuracy, recall and regression evidence

**Files:**
- Modify: `backend/app/baseline_regression.py` and/or existing regression aggregation module
- Modify: `backend/app/main.py` regression detail payloads
- Create: `backend/tests/test_quality_metrics_api.py`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`, `frontend/src/pages/quality-assets-page.tsx`, `frontend/src/lib/types.ts`

**Interfaces:**
- `GET /api/baseline-regressions/{run_id}/metrics` returns `field_metrics[]` with field key, support, tp, fp, fn, accuracy, recall, macro/micro aggregates, confusion matrix and failure sample ids.
- A candidate with a key-field regression or a golden-set failure remains non-approvable; metrics are evidence only and do not auto-decide a candidate.
- The primary page shows accuracy/recall summary cards and a “查看字段证据” drawer.

- [ ] **Step 1: Write failing metric tests.**

```python
def test_field_metrics_include_accuracy_and_recall(client, regression_run):
    response = client.get(f"/api/baseline-regressions/{regression_run.id}/metrics")
    assert response.status_code == 200
    item = response.json()["field_metrics"][0]
    assert {"accuracy", "recall", "tp", "fp", "fn"} <= item.keys()
```

- [ ] **Step 2: Run and confirm failure.**

Run: `pytest backend/tests/test_quality_metrics_api.py backend/tests/test_golden_regression.py -q`

- [ ] **Step 3: Implement deterministic field metric aggregation.**

Compare formal truth fields to candidate output; use support-aware macro and micro averages; preserve missing/unknown as explicit errors. Include model, prompt, mechanism, asset and truth revisions in the response metadata.

- [ ] **Step 4: Update the regression UI.**

Move detailed matrices and failed rows into the existing metrics drawer; retain only headline accuracy/recall, gate state and the next human action on the first viewport.

- [ ] **Step 5: Run tests and commit.**

Run: `pytest backend/tests/test_quality_metrics_api.py backend/tests/test_golden_regression.py backend/tests/test_baseline_regression.py -q && cd frontend && npm run build`

```bash
git add backend/app/baseline_regression.py backend/app/main.py backend/tests/test_quality_metrics_api.py frontend/src/pages/baseline-regression-page.tsx frontend/src/pages/quality-assets-page.tsx frontend/src/lib/types.ts
git commit -m "feat: expose accuracy and recall evidence"
```

### Task 8: Make profile plugins the single extension point for category-specific editors

**Files:**
- Modify: `backend/app/mechanism_profiles.py` and category profile APIs if needed
- Modify: `frontend/src/features/mechanism-config/registry.ts`, `mechanism-editor-boundary.tsx`, existing image/Proposal editors
- Create: `frontend/src/features/mechanism-config/profile-capability-summary.tsx`
- Create: `backend/tests/test_mechanism_profile_boundaries.py`
- Modify: `frontend/scripts/check-mechanism-editor-contract.ts`

**Interfaces:**
- Profile registry response includes `profile_type`, `version`, `capabilities`, `editor_route`, `read_only_fallback`.
- Supported profiles remain image-rule, Proposal PDF and future 3D/SU controlled profiles; unknown profiles render read-only JSON diagnosis and cannot execute arbitrary code.
- Profile editors are reachable from both workspaces through the same mechanism step and receive `workflow_kind` without duplicating platform APIs.

- [ ] **Step 1: Add failing boundary tests.**

```python
def test_unknown_profile_is_read_only(profile_registry):
    summary = profile_registry.resolve("unknown-v99")
    assert summary.read_only is True
    assert summary.can_execute is False
```

- [ ] **Step 2: Run and confirm failure.**

Run: `pytest backend/tests/test_mechanism_profile_boundaries.py backend/tests/test_mechanism_profiles.py -q`

- [ ] **Step 3: Implement capability metadata and safe fallback.**

Do not add category-specific production logic to the shared workflow pages. The editor boundary must validate profile version and refuse writes for unknown or incompatible profiles.

- [ ] **Step 4: Add contract assertions for 3D/SU extension slots.**

Run: `cd frontend && npm run contract:mechanism-editor && npm run contract:v3-only && npm run build`

- [ ] **Step 5: Commit.**

```bash
git add backend/app/mechanism_profiles.py backend/tests/test_mechanism_profile_boundaries.py frontend/src/features/mechanism-config frontend/scripts/check-mechanism-editor-contract.ts
git commit -m "feat: formalize category profile extension boundary"
```

### Task 9: Align documentation, status, and gap register without changing the frozen contract

**Files:**
- Modify: `PROJECT_STATUS.md`
- Create: `docs/decisions/0045-dual-workspaces-and-table-projection-contract.md`
- Create: `docs/discussion/tpeng-labellab-gap-register-20260813.md`
- Modify: `PRODUCT.md` only where naming still claims two independent projects or an evaluation-only tool
- Test: `frontend/scripts/check-dual-workspaces-contract.ts` and `git diff --check`

**Interfaces:**
- ADR-0045 states the unified product name, dual-workspace model, one-large-table-plus-small-tables projection boundary and current non-goals.
- `PROJECT_STATUS.md` records implemented vs planned vs blocked capabilities and explicitly says the current batch does not authorize production database writes or external deployment.
- Gap register returns to the 【标签体系】重构会话 as the next-range input; this session remains the sole code-writing path after future scope is frozen.

- [ ] **Step 1: Add document-level assertions.**

```bash
rg -n "TPENG 标签实验台|统一大维表|数个小表|不连接真实|不自动采纳|双工作区" PROJECT_STATUS.md docs/decisions/0045-dual-workspaces-and-table-projection-contract.md docs/discussion/tpeng-labellab-gap-register-20260813.md
```

- [ ] **Step 2: Review for forbidden language.**

Run: `rg -n "两套独立项目|仅为评测工具|标签体系重构.*独立" PRODUCT.md PROJECT_STATUS.md docs/decisions docs/discussion`

Expected: no current-position statement uses those descriptions; historical ADR text remains unchanged where it is archival.

- [ ] **Step 3: Update status and gap evidence.**

Separate “implemented in this batch”, “existing but not yet unified”, “not authorized this batch”, and “requires next Owner freeze”. Include the 90-day evidence chain: label production → formal release → table projection → first real consumer slice → effect measurement → Badcase feedback.

- [ ] **Step 4: Run documentation and repository checks.**

Run: `git diff --check && cd frontend && node --experimental-strip-types scripts/check-dual-workspaces-contract.ts && npm run contract:information-architecture`

- [ ] **Step 5: Commit.**

```bash
git add PRODUCT.md PROJECT_STATUS.md docs/decisions/0045-dual-workspaces-and-table-projection-contract.md docs/discussion/tpeng-labellab-gap-register-20260813.md frontend/scripts/check-dual-workspaces-contract.ts
git commit -m "docs: align unified labellab platform boundaries"
```

### Task 10: Full verification and Edge desktop acceptance preparation

**Files:**
- No new product files unless verification exposes a defect.
- Review: all changed files from Tasks 1–9.

**Interfaces:**
- Backend verification must cover migrations from a clean SQLite database and an existing database at migration v65.
- Frontend verification must cover TypeScript/Vite build, all contract scripts, and the principal incremental/stock/quality/operations routes.
- Browser acceptance is desktop Edge only and must not require mobile breakpoints.

- [ ] **Step 1: Run backend focused suites.**

Run: `pytest backend/tests/test_workflow_context_api.py backend/tests/test_content_ingress_incremental_routing.py backend/tests/test_quality_assets_api.py backend/tests/test_projection_contracts.py backend/tests/test_quality_metrics_api.py backend/tests/test_mechanism_profile_boundaries.py -q`

Expected: PASS.

- [ ] **Step 2: Run backend full suite.**

Run: `pytest backend/tests -q`

Expected: PASS; if an existing fixture intentionally models pre-v66 schema, update only the fixture migration setup, not production behavior.

- [ ] **Step 3: Run frontend contracts and build.**

Run: `cd frontend && npm run contract:information-architecture && npm run contract:mechanism-editor && npm run contract:model-registry && npm run contract:v3-only && npm run contract:dual-workspaces && npm run build`

Expected: PASS.

- [ ] **Step 4: Run repository hygiene checks.**

Run: `git diff --check && git status --short --branch`

Expected: no whitespace errors; only intentional files changed; no generated credentials, tokens or database dumps.

- [ ] **Step 5: Prepare Edge acceptance without deploying.**

Start the existing local dev/preview commands only if needed, open `/workflow/incremental`, `/workflow/stock`, `/workflow/operations`, `/workflow/quality-assets` in Edge, and verify route isolation, drawer behavior, primary action visibility, fluorescence-green action color and no white-screen navigation. Do not claim deployment or production readiness from local browser evidence.

- [ ] **Step 6: Commit verification receipt.**

```bash
git add -A
git commit -m "test: verify dual workspace platform batch"
```

## Rollback and stop conditions

- Every schema change is additive and can be rolled back by disabling the new route/API surface; do not drop or rewrite existing tables in this batch.
- If a migration fails, stop before serving the new routes and restore from the pre-migration SQLite snapshot; do not run destructive repair commands.
- If any path can automatically activate a candidate mechanism, publish labels without an explicit human action, expose candidate/manual-process fields to projection targets, or write a real business database, stop immediately and return the evidence.
- If a profile is unknown or incompatible, fail closed and expose a read-only diagnostic instead of executing it.
- If projection reconciliation detects drift, keep Canonical facts unchanged, record the drift and offer retry/compensation only.

## Acceptance contract

The batch is complete only when all of the following are true:

1. Incremental and stock routes show isolated, serial steps and cannot start a downstream action before prerequisites are satisfied.
2. Local ingress events route by category into idempotent incremental packages; duplicates do not duplicate tasks.
3. Operations center proves queue, concurrency, retry and recovery state without flattening scheduler internals.
4. Golden datasets can be created, truth-versioned, locked, reused and exported as CSV/JSON/Manifest.
5. Field-level accuracy and recall, confusion matrices and failed sample evidence are visible without replacing human decisions.
6. Projection Registry can build and reconcile a unified large-table manifest plus several small-table manifests using formally published labels only.
7. Mechanism and label release axes, Canonical fact boundaries, permissions, audit trails and desktop visual language do not regress.
8. Backend tests, frontend contracts/build, `git diff --check` and Edge desktop smoke checks pass.
9. No real upstream, search/recommendation, business database, production deployment or Codeup push is performed under this plan.
