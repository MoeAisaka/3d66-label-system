# TPENG 标签实验台脚本注册与工作流运行时底座实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 LabelLab 五队列调度内核上，交付可审计、可恢复的本地 dry-run 脚本注册、工作流版本和通用运行时底座。

**Architecture:** 新增脚本注册、工作流注册和通用运行时模块；使用不可变版本与 canonical hash 冻结执行快照。`runtime_dispatch_items` 作为既有 `DeterministicQueueScheduler` 的适配记录，不新增队列、配额或调度状态；现有 `EvaluationProductionRun` 与历史评测任务保持兼容。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLAlchemy、SQLite migration runner、React/TypeScript、TanStack Query、Vitest/前端合同脚本、pytest。

## Global Constraints

- 本阶段运行模式固定为本地 `dry_run`；不接真实 DataWorks、真实模型、真实大维表/小表、外部数据库或外部网络。
- 不允许页面上传或执行任意 Python、JavaScript、SQL、Shell；首批执行器只允许 `deterministic_fixture`。
- 只复用既有五队列：`validation`、`interactive`、`production_batch`、`canary`、`recovery`；不得新增第六队列或第二套调度器。
- 脚本版本、工作流版本、运行快照、检查点和审计证据不可变；任何内容变化创建新版本/新运行。
- 保留机制发布门与标签事实发布门的独立性；本阶段不执行真实发布或数据库 DML。
- 不改写 `evaluation_production_runs`、`evaluation_jobs`、纠偏、机制发布和标签发布历史。
- migration 版本为 71，必须增量、幂等、可重复执行。
- 工作台为桌面端，一级页面只留当前操作所需信息，高级 manifest/Schema/错误细节进入二级抽屉。

---

## Task 1: Add migration 71 and runtime ORM models

**Files:**
- Modify: `backend/app/models.py`（在现有调度/运行模型附近新增脚本、工作流、运行时模型）
- Modify: `backend/app/migrations/runner.py`（新增 `_migration_071_add_script_workflow_runtime` 并加入 `MIGRATIONS`）
- Modify: `backend/tests/test_migration.py`（新增 migration 71 空库、当前库、重复运行和历史不变断言）
- Create: `backend/tests/test_workflow_runtime_models.py`

**Interfaces:**
- Produces ORM classes `ScriptDefinition`, `ScriptVersion`, `WorkflowDefinition`, `WorkflowVersion`, `ProductionRun`, `ProductionStepAttempt`, `RuntimeDispatchItem`, `RuntimeAuditEvent`。
- Produces migration registry entry `Migration(71, "add_script_workflow_runtime", _migration_071_add_script_workflow_runtime)`。
- `ProductionRun` 的 `source_type/source_id` 可选关联现有评测聚合；不得要求历史 `EvaluationProductionRun` 回填。

- [ ] **Step 1: Write failing migration/model tests**

在 `backend/tests/test_migration.py` 增加：

```python
def test_migration_71_creates_runtime_tables_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    with engine.begin() as connection:
        run_migrations(connection)
        first = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()
        run_migrations(connection)
        second = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()
        assert first == second
        assert first[-1] == 71
        tables = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "script_definitions", "script_versions", "workflow_definitions",
            "workflow_versions", "production_runs", "production_step_attempts",
            "runtime_dispatch_items", "runtime_audit_events",
        } <= tables
```

在 `backend/tests/test_workflow_runtime_models.py` 增加外键、唯一键、状态 CHECK、哈希 CHECK 和 JSON 合同测试。

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
cd backend && pytest -q tests/test_migration.py -k 'migration_71' tests/test_workflow_runtime_models.py
```

Expected: FAIL because migration 71 and the ORM classes do not exist。

- [ ] **Step 3: Implement additive ORM models**

Add models with these immutable/unique boundaries:

```python
class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("script_definition_id", "version"),
        CheckConstraint(
            "status IN ('draft','validating','active','deprecated','retired','blocked')",
            name="ck_script_versions_status",
        ),
        CheckConstraint("length(artifact_sha256) = 64", name="ck_script_versions_sha256"),
    )

class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_definition_id", "version"),
        CheckConstraint(
            "status IN ('draft','validating','active','deprecated','retired','blocked')",
            name="ck_workflow_versions_status",
        ),
        CheckConstraint("length(canonical_hash) = 64", name="ck_workflow_versions_hash"),
    )

class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(
            "queue_class IN ('validation','interactive','production_batch','canary','recovery')",
            name="ck_production_runs_queue_class",
        ),
        CheckConstraint(
            "status IN ('planned','queued','running','paused','succeeded','failed','retryable','blocked','canceled')",
            name="ck_production_runs_status",
        ),
    )
```

Use JSON text columns with application-level canonical validation; do not add a second version table for category mechanisms。

- [ ] **Step 4: Implement migration 71**

Create tables with `CREATE TABLE IF NOT EXISTS`, create indexes with `CREATE INDEX IF NOT EXISTS`, use `ON DELETE RESTRICT` for version/run evidence, and run `PRAGMA foreign_key_check`. Do not update existing rows except inserting migration ledger version 71。

- [ ] **Step 5: Run migration/model tests and commit**

Run:

```bash
cd backend && pytest -q tests/test_migration.py -k 'migration_71' tests/test_workflow_runtime_models.py
```

Expected: PASS. Commit:

```bash
git add backend/app/models.py backend/app/migrations/runner.py backend/tests/test_migration.py backend/tests/test_workflow_runtime_models.py
git commit -m "feat: add workflow runtime persistence foundation"
```

## Task 2: Implement controlled script registry and lifecycle validation

**Files:**
- Create: `backend/app/script_registry.py`
- Create: `backend/app/script_registry_api.py`
- Modify: `backend/app/main.py`（导入并 include router；只加入路由，不在 main.py 重复业务逻辑）
- Modify: `backend/app/authz.py`（增加 `scripts:read`、`scripts:write`、`workflows:read`、`workflows:write` 权限到现有角色）
- Create: `backend/tests/test_script_registry.py`

**Interfaces:**
- `validate_script_version_payload(payload: Mapping[str, Any]) -> ValidationReport`
- `transition_script_version(db: Session, version_id: int, target: str, actor: User) -> ScriptVersion`
- `script_registry_router(require_user: Callable[..., Any]) -> APIRouter`
- API prefix `/api/scripts`，错误返回稳定 `code` 和字段路径。

- [ ] **Step 1: Write failing pure validator tests**

覆盖：缺少 hash、非法 executor、Schema 不是 object、超时越界、重试次数越界、任意代码字段、非法生命周期转移和 blocked 版本拒绝新引用。

```python
def test_script_validation_rejects_arbitrary_executor():
    report = validate_script_version_payload({
        "executor_kind": "python",
        "artifact_sha256": "a" * 64,
        "input_schema": {},
        "output_schema": {},
    })
    assert report.errors[0].code == "executor_kind_unsupported"
```

- [ ] **Step 2: Run validator tests and confirm failure**

Run `cd backend && pytest -q tests/test_script_registry.py`；expected FAIL because module and function do not exist。

- [ ] **Step 3: Implement pure script contract validator**

Use `canonical_json`/SHA-256 conventions already used by the repository. Reject extra executable/source keys (`source`, `code`, `command`, `shell`, `sql`, `script`) before persistence. Return:

```python
@dataclass(frozen=True)
class ValidationErrorItem:
    path: str
    code: str
    message: str

@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[ValidationErrorItem, ...]
```

- [ ] **Step 4: Add lifecycle service and API**

Implement list/create/version/validate/transition handlers. `active` transition requires a successful validation report; `deprecated` and `retired` never mutate artifact hash/schema. Ordinary users are read-only; admin/manager write permissions follow existing RBAC patterns。

- [ ] **Step 5: Add API tests and commit**

Assert `GET /api/scripts`, `POST /api/scripts/{key}/versions`, validation errors, duplicate version `409`, illegal transition `409`, and ordinary user `403`。Run `cd backend && pytest -q tests/test_script_registry.py`，then commit:

```bash
git add backend/app/script_registry.py backend/app/script_registry_api.py backend/app/main.py backend/app/authz.py backend/tests/test_script_registry.py
git commit -m "feat: add controlled script registry"
```

## Task 3: Implement workflow definition, DAG and route validation

**Files:**
- Create: `backend/app/workflow_registry.py`
- Create: `backend/app/workflow_registry_api.py`
- Modify: `backend/app/main.py`（include workflow router）
- Create: `backend/tests/test_workflow_registry.py`

**Interfaces:**
- `validate_workflow_manifest(db: Session, manifest: Mapping[str, Any]) -> ValidationReport`
- `canonical_workflow_snapshot(db: Session, workflow_version_id: int, runtime_context: Mapping[str, Any]) -> tuple[dict[str, Any], str]`
- `transition_workflow_version(...) -> WorkflowVersion`
- API prefix `/api/workflows`。

- [ ] **Step 1: Write failing DAG/DSL tests**

Cover unknown script, blocked/deprecated script, missing input source, type-incompatible edge, duplicate step key, cycle, orphan node, unsupported operator, dynamic expression, recursion/depth overflow and sixth queue。

```python
def test_workflow_validator_rejects_cycle(db):
    report = validate_workflow_manifest(db, {
        "schema_version": "workflow-v1",
        "steps": [
            {"key": "a", "type": "transform", "script_version": "fixture.transform@1"},
            {"key": "b", "type": "transform", "script_version": "fixture.transform@1"},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        "queue_class": "validation",
    })
    assert any(item.code == "workflow_cycle" for item in report.errors)
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run `cd backend && pytest -q tests/test_workflow_registry.py`；expected FAIL because registry module does not exist。

- [ ] **Step 3: Implement strict manifest validator**

Use only standard step types and the allow-list operators from the design. Build a deterministic topological order; validate all referenced script versions, queue class, Schema fields and condition paths. Never call `eval` or interpret source strings。

- [ ] **Step 4: Implement immutable workflow API and snapshot service**

Create definition/version endpoints, validation and legal transitions. `canonical_workflow_snapshot` must include workflow hash, all script artifact hashes, category/profile/contract/model/mechanism references supplied by `runtime_context`, queue policy version and environment; return canonical JSON plus lowercase SHA-256。

- [ ] **Step 5: Run tests and commit**

Run `cd backend && pytest -q tests/test_workflow_registry.py` and `git diff --check`; commit:

```bash
git add backend/app/workflow_registry.py backend/app/workflow_registry_api.py backend/app/main.py backend/tests/test_workflow_registry.py
git commit -m "feat: add validated workflow registry"
```

## Task 4: Implement ProductionRun/StepAttempt state machine and scheduler adapter

**Files:**
- Create: `backend/app/workflow_runtime.py`
- Modify: `backend/app/queue_scheduler.py`（增加 dispatch adapter 所需的纯计数/选择接口，不改变 `QUEUE_CLASSES` 或 `QueuePolicy`）
- Create: `backend/tests/test_workflow_runtime.py`
- Create: `backend/tests/test_runtime_scheduler_adapter.py`

**Interfaces:**
- `create_production_run(db, request, actor) -> ProductionRun`
- `claim_next_runtime_step(db, worker_id) -> int | None`
- `heartbeat_runtime_step(db, attempt_id, lease_token, worker_id) -> ProductionStepAttempt`
- `complete_runtime_step(db, attempt_id, lease_token, output_manifest) -> ProductionStepAttempt`
- `recover_expired_runtime_steps(db, now) -> int`
- `resume_from_checkpoint(db, run_id) -> ProductionRun`

- [ ] **Step 1: Write failing state/idempotency tests**

Cover duplicate run idempotency, immutable snapshot, only-one-lease, stale-token rejection, success transition, retryable transition, terminal transition, checkpoint hash, and no re-run of completed steps。

- [ ] **Step 2: Run tests and confirm failure**

Run `cd backend && pytest -q tests/test_workflow_runtime.py tests/test_runtime_scheduler_adapter.py`；expected FAIL because runtime service does not exist。

- [ ] **Step 3: Implement short-transaction run creation**

Freeze the workflow snapshot before inserting `ProductionRun`; compute `run_key` and `idempotency_key` deterministically. A duplicate key returns the existing row and never creates new dispatch items。

- [ ] **Step 4: Implement generic dispatch adapter over existing scheduler**

Create/update `RuntimeDispatchItem` rows with the five existing queue classes. Adapt pending/running counts into `DeterministicQueueScheduler`; persist deficit and dispatch count through `QueueSchedulerState` in the same claim transaction. Do not add a second policy or process-global queue state。

- [ ] **Step 5: Implement lease/checkpoint methods**

Use compare-and-set predicates on status and `lease_token`; reject stale owners with coded conflicts. Persist `input_hash`, `output_hash`, `checkpoint_hash`, worker id and script/workflow versions. A successful step is idempotently read back on duplicate completion。

- [ ] **Step 6: Run focused tests and commit**

Run the two focused suites and existing `tests/test_queue_scheduler.py`; expected all pass. Commit:

```bash
git add backend/app/workflow_runtime.py backend/app/queue_scheduler.py backend/tests/test_workflow_runtime.py backend/tests/test_runtime_scheduler_adapter.py
git commit -m "feat: add resumable production runtime"
```

## Task 5: Add deterministic fixture executor and worker recovery loop

**Files:**
- Create: `backend/app/workflow_fixture_executor.py`
- Modify: `backend/app/worker.py`（调用 runtime dispatch adapter；保留现有 EvaluationJob 路径）
- Modify: `backend/app/launcher.py`（只接入 dry-run runtime recovery tick，不改变启动拓扑）
- Create: `backend/tests/test_workflow_fixture_executor.py`
- Create: `backend/tests/test_workflow_runtime_recovery.py`

**Interfaces:**
- `execute_fixture(kind: str, input_manifest: Mapping[str, Any], manifest: Mapping[str, Any]) -> FixtureResult`
- `process_runtime_step_once(worker_id: str) -> bool`
- `recover_runtime_once(now: datetime | None = None) -> int`

- [ ] **Step 1: Write failing fixture/recovery tests**

Cover identity normalization, allow-listed transform, route branch selection, noop output, fail-once behavior, network/source access rejection, timeout conversion, lease expiry and worker restart recovery。

- [ ] **Step 2: Run tests and confirm failure**

Run `cd backend && pytest -q tests/test_workflow_fixture_executor.py tests/test_workflow_runtime_recovery.py`；expected FAIL because executor/recovery functions do not exist。

- [ ] **Step 3: Implement deterministic fixtures**

Use canonical JSON and SHA-256 for every output. `fixture.fail_once` fails only when `attempt_no == 1`; no fixture imports subprocess, executes source, reads secrets or accesses network。

- [ ] **Step 4: Integrate worker processing**

The worker claims a runtime step after the existing EvaluationJob claim path through the same scheduler policy, performs execution outside a write transaction, and commits output/checkpoint in a short transaction. SQLite lock conflicts use existing bounded retry behavior and never terminate the worker。

- [ ] **Step 5: Implement recovery scan**

Expired runtime leases become `retryable` and create/reuse a `recovery` dispatch item. Valid checkpoints are reused; stale input/checkpoint hashes become `blocked` with `CHECKPOINT_INPUT_DRIFT`。

- [ ] **Step 6: Run focused and existing worker tests, commit**

Run the two new suites plus `tests/test_queue_scheduler.py`, `tests/test_worker_failure_trace.py`, and `tests/test_launcher.py`; commit:

```bash
git add backend/app/workflow_fixture_executor.py backend/app/worker.py backend/app/launcher.py backend/tests/test_workflow_fixture_executor.py backend/tests/test_workflow_runtime_recovery.py
git commit -m "feat: run and recover deterministic workflow fixtures"
```

## Task 6: Add runtime APIs, permissions and audit views

**Files:**
- Create: `backend/app/workflow_runtime_api.py`
- Modify: `backend/app/main.py`（include runtime router）
- Modify: `backend/app/authz.py`（完成运行时权限矩阵）
- Create: `backend/tests/test_workflow_runtime_api.py`

**Interfaces:**
- `GET /api/runtime/runs`
- `POST /api/runtime/runs`
- `GET /api/runtime/runs/{run_key}`
- `POST /api/runtime/runs/{run_key}/pause`
- `POST /api/runtime/runs/{run_key}/resume`
- `POST /api/runtime/runs/{run_key}/retry`
- `POST /api/runtime/runs/{run_key}/cancel`
- `GET /api/runtime/runs/{run_key}/timeline`
- `GET /api/runtime/runs/{run_key}/snapshot`

- [ ] **Step 1: Write failing API/RBAC tests**

Assert dry-run creation, duplicate idempotency response, list filters, timeline/checkpoint response, blocked reason, state-specific action visibility, ordinary-user `403`, and no endpoint accepts `source/code/command/sql/shell` fields。

- [ ] **Step 2: Run tests and confirm failure**

Run `cd backend && pytest -q tests/test_workflow_runtime_api.py`；expected FAIL because router does not exist。

- [ ] **Step 3: Implement API schemas and handlers**

Use Pydantic `extra="forbid"`, map service errors to stable HTTP 400/403/404/409, and keep handlers short. Creation must persist snapshot and return before any fixture execution；execution remains worker-owned。

- [ ] **Step 4: Add audit events**

Write append-only `runtime_audit_events` for registration, validation, transition, claim, heartbeat, recovery, pause, resume, retry and cancel; redact credentials and source payloads。

- [ ] **Step 5: Run API tests and commit**

Run the focused suite and `tests/test_account_permissions.py`; commit:

```bash
git add backend/app/workflow_runtime_api.py backend/app/main.py backend/app/authz.py backend/tests/test_workflow_runtime_api.py
git commit -m "feat: expose workflow runtime operations"
```

## Task 7: Extend desktop operations center with runtime evidence drawer

**Files:**
- Modify: `frontend/src/lib/types.ts`（新增 `ScriptSummary`、`WorkflowSummary`、`ProductionRunSummary`、`RuntimeTimelineItem`）
- Modify: `frontend/src/pages/operations-center-page.tsx`
- Create: `frontend/src/components/runtime-run-drawer.tsx`
- Create: `frontend/src/lib/runtime-api.ts`
- Create: `frontend/scripts/runtime-center-contract.mjs`（沿用现有前端合同脚本风格）

**Interfaces:**
- `runtimeApi.listRuns(filters): Promise<{ items: ProductionRunSummary[] }>`
- `runtimeApi.getTimeline(runKey): Promise<{ items: RuntimeTimelineItem[] }>`
- `runtimeApi.action(runKey, action): Promise<ProductionRunSummary>`

- [ ] **Step 1: Write failing front-end contract assertions**

Assert the page renders five queue labels, workflow/script version, current step, checkpoint, owner, blocker and the drawer entry; assert advanced manifest text is not rendered in the first-level card。

- [ ] **Step 2: Run the contract and confirm failure**

Run `node frontend/scripts/runtime-center-contract.mjs`; expected FAIL because types, API helper and drawer are absent。

- [ ] **Step 3: Implement typed runtime API and drawer**

Use existing `api<T>` and `SecondaryDrawer`. Poll runtime runs at the same operational cadence as jobs. Show only actionable summary at level one; put snapshot, steps, hashes, retries and errors in the drawer。

- [ ] **Step 4: Add state-aware actions**

Show pause/resume/retry/cancel only when the backend response marks the action allowed; after an action, invalidate the runtime query and preserve the current drawer selection。

- [ ] **Step 5: Run front-end checks and commit**

Run `node frontend/scripts/runtime-center-contract.mjs`, the existing frontend contract scripts, `pnpm lint`, and `pnpm build`; commit:

```bash
git add frontend/src/lib/types.ts frontend/src/pages/operations-center-page.tsx frontend/src/components/runtime-run-drawer.tsx frontend/src/lib/runtime-api.ts frontend/scripts/runtime-center-contract.mjs
git commit -m "feat: show resumable workflow runs in operations center"
```

## Task 8: End-to-end dry-run verification and handoff receipt

**Files:**
- Create: `backend/tests/test_workflow_runtime_e2e.py`
- Create: `docs/superpowers/receipts/2026-08-15-script-registry-workflow-runtime-dry-run.md`
- Modify: `docs/superpowers/specs/2026-08-15-script-registry-workflow-runtime-design.md` only if verification reveals a concrete contract correction

**Interfaces:**
- No new production interface; this task exercises the APIs and worker path delivered above。

- [ ] **Step 1: Add an end-to-end dry-run test**

Register `fixture.identity@1`, `fixture.fail_once@1`, a two-step validation workflow, create a `ProductionRun` with idempotency key, process first attempt, force retry, process recovery, and assert final success with exactly one successful checkpoint per step and no external side effects。

- [ ] **Step 2: Run deterministic verification**

Run:

```bash
cd backend && pytest -q tests/test_workflow_runtime_e2e.py tests/test_script_registry.py tests/test_workflow_registry.py tests/test_workflow_runtime.py tests/test_runtime_scheduler_adapter.py tests/test_workflow_fixture_executor.py tests/test_workflow_runtime_recovery.py tests/test_workflow_runtime_api.py
pytest -q
cd ../frontend && node scripts/runtime-center-contract.mjs && pnpm lint && pnpm build
cd .. && git diff --check
```

Expected: all focused/backend tests pass, full backend suite passes, frontend contract/lint/build pass, and only the existing main-chunk size warning remains if present。

- [ ] **Step 3: Write the dry-run receipt**

Record exact commit SHA, migration version 71, test counts, queue classes observed, idempotency/recovery evidence, and explicit statements: no Codeup push, no MR, no test-server deployment, no real model calls, no external DB DML。

- [ ] **Step 4: Commit receipt and stop for next authorization**

```bash
git add backend/tests/test_workflow_runtime_e2e.py docs/superpowers/receipts/2026-08-15-script-registry-workflow-runtime-dry-run.md
git commit -m "test: record workflow runtime dry run receipt"
```

Stop after the local receipt. Any push, merge, deployment, real model call or database DML requires a separate explicit Owner authorization and release checklist。

---

## Plan self-review

- **Spec coverage:** Tasks 1–2 cover persistence and script lifecycle; Task 3 covers workflow/DAG/DSL/freeze; Tasks 4–5 cover run state, scheduler reuse, fixtures, leases, checkpoints and recovery; Task 6 covers API/RBAC/audit; Task 7 covers desktop runtime center and drawer; Task 8 covers end-to-end evidence and dry-run handoff。
- **Placeholder scan:** No placeholder phrase or unspecified edge-case instruction appears in the task steps。
- **Type consistency:** `ScriptVersion` and `WorkflowVersion` identifiers feed `canonical_workflow_snapshot`; `ProductionRun` feeds `ProductionStepAttempt`; `RuntimeDispatchItem` is the only scheduler adapter; API and frontend types use `run_key` consistently。
- **Scope check:** All tasks are one cohesive local runtime foundation and share the same migration, scheduler adapter and dry-run acceptance; no separate product line is introduced。
- **Safety check:** No task authorizes push, merge, deployment, external calls, arbitrary code execution, historical rewrite or production DML。


