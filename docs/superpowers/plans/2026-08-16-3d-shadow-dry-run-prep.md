# 3D/SU Shadow 与确定性闭环预备批次 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `main@9943b8c` 上移植只读 3D/SU shadow 能力，并用 deterministic fixture 证明完整标签闭环的状态、人工门、投影对账和恢复行为。

**Architecture:** 以现有 `ProductionRun`/`ProductionStepAttempt`、五队列 `DeterministicQueueScheduler` 和发布事实边界为执行内核；新增来源合同、字段需求合同、3D/SU Profile 和 shadow projection 只作为可插拔模块。所有本批运行环境固定 `dry_run`，shadow 只能消费正式事实，人工纠偏门与标签事实发布门保持独立。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、SQLite migration runner、pytest、React/TypeScript、Vite、现有 Edge 浏览器合同脚本。

## Global Constraints

- 只在隔离工作树 `codex/3d-shadow-dry-run-prep-20260816` 修改。
- 不连接真实上游、真实模型、真实 DataWorks、大维表/小表或外部数据库。
- 不启动正式标签发布、自动候选启用、存量覆盖或测试服部署。
- 不新增第六队列，不修改双人工门、双发布轴和现有历史事实。
- 旧 `codex/3d-shadow-consumption-mvp-v1@7b4ebce` 分支保持原样，不删除、不推送、不合并。

---

### Task 1: Add failing shadow/domain contract tests from the old branch

**Files:**
- Create: `backend/tests/test_field_demand_contracts.py`
- Create: `backend/tests/test_readonly_sources.py`
- Create: `backend/tests/test_shadow_projection.py`
- Create: `backend/tests/test_three_d_profile.py`
- Create: `backend/tests/test_three_d_shadow_consumption_flow.py`

**Interfaces:**
- Tests import the intended public functions: `create_field_demand_contract`, `create_upstream_source_contract`, `poll_upstream_source`, `create_shadow_projection_target`, `enqueue_shadow_projection_run`, `shadow_projection_worker_tick`, `build_three_d_profile`, and the full fixture flow helper.

- [ ] **Step 1: Copy only the test files from `7b4ebce` into this worktree.**
- [ ] **Step 2: Run the focused tests and verify RED.**

Run:

```bash
DATA_DIR="$(mktemp -d)" uv run --with-requirements backend/requirements.txt python -X utf8 -m pytest -q \
  backend/tests/test_field_demand_contracts.py \
  backend/tests/test_readonly_sources.py \
  backend/tests/test_shadow_projection.py \
  backend/tests/test_three_d_profile.py \
  backend/tests/test_three_d_shadow_consumption_flow.py
```

Expected: collection/import failures for missing shadow/domain modules; no production code is changed before this RED result.

### Task 2: Port field-demand and read-only source contracts

**Files:**
- Create: `backend/app/field_demand_contracts.py`
- Create: `backend/app/readonly_sources.py`
- Modify: `backend/app/models.py` (only missing field-demand/source entities)
- Modify: `backend/app/migrations/runner.py` (idempotent migrations for the new entities)
- Modify: `backend/app/main.py` (only route registrations required by the contracts)
- Test: `backend/tests/test_field_demand_contracts.py`, `backend/tests/test_readonly_sources.py`, `backend/tests/test_migration.py`

**Interfaces:**
- `create_field_demand_contract(...) -> FieldDemandContract` validates field keys, source paths, data types, thresholds, and one active version per contract key.
- `FixtureReadOnlySourceAdapter.verify_read_only() -> SourceSafetyEvidence` and `.fetch_page(...) -> SourcePage` provide deterministic source polling without external I/O.
- `poll_upstream_source(...)` records cursor, schema fingerprint, imported content evidence, and idempotent replay.

- [ ] **Step 1: Implement the smallest model/migration additions needed for the copied tests.**
- [ ] **Step 2: Run Task 2 focused tests and confirm GREEN.**
- [ ] **Step 3: Run migration tests for empty, current, and repeated application.**
- [ ] **Step 4: Commit `feat: add readonly 3d source and field contracts`.**

### Task 3: Port 3D/SU Profile and shadow projection worker

**Files:**
- Create: `backend/app/three_d_profile.py`
- Create: `backend/app/shadow_projection.py`
- Modify: `backend/app/models.py` (shadow target/run/lease entities only)
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/projection_contracts.py` (only shadow contract compatibility)
- Modify: `backend/app/label_governance.py` and `backend/app/production_feedback.py` (only bounded evidence hooks)
- Modify: `backend/app/main.py` and `backend/app/worker.py` (only route/tick registration, default-off)
- Test: `backend/tests/test_shadow_projection.py`, `backend/tests/test_three_d_profile.py`, `backend/tests/test_three_d_shadow_consumption_flow.py`

**Interfaces:**
- `build_three_d_profile(...)` returns a validated `CategoryProfile` capability descriptor for `model_3d_su` and fail-closes unknown/disabled profiles.
- `create_shadow_projection_target(...)` enforces `environment="shadow"`, `shadow_only=True`, safe logical references, and SHA-256 schema fingerprints.
- `shadow_projection_worker_tick(...)` consumes only published facts and writes fixture batches with row-count/payload-hash/version reconciliation.
- Adapter failures become retryable or blocked with stable error codes; target lease prevents concurrent writers.

- [ ] **Step 1: Add one failing assertion for the target safety and published-fact-only behavior.**
- [ ] **Step 2: Port the minimal model/migration and implementation pieces.**
- [ ] **Step 3: Run all shadow/profile tests; verify no candidate/process/raw-response fields reach the manifest.**
- [ ] **Step 4: Commit `feat: add 3d shadow projection foundation`.**

### Task 4: Add a complete deterministic 3D/SU workflow fixture

**Files:**
- Create: `backend/app/three_d_workflow_fixture.py`
- Create: `backend/tests/test_three_d_workflow_fixture.py`
- Modify: `backend/app/workflow_fixture_executor.py` only if a new deterministic fixture kind is required
- Modify: `backend/tests/test_workflow_runtime_e2e.py` only for shared runtime assertions

**Interfaces:**
- `build_three_d_dry_run_manifest(...) -> dict[str, Any]` returns a `workflow-v1` DAG using existing script versions and standard step types.
- `run_three_d_dry_run(...) -> ThreeDDryRunReceipt` creates an active workflow run and processes it through the existing runtime without external side effects.
- The receipt records `run_id`, ordered steps, independent `human_correction_gate` and `label_fact_gate`, projection reconciliation, feedback case key, snapshot hash, and recovery evidence.

- [ ] **Step 1: Write a failing test for the full ordered flow and two independent human gates.**
- [ ] **Step 2: Write a failing test for duplicate idempotency, projection failure/retry, and checkpoint recovery.**
- [ ] **Step 3: Implement the manifest builder and the smallest fixture executor extensions.**
- [ ] **Step 4: Run the focused fixture tests and then the existing runtime E2E tests.**
- [ ] **Step 5: Commit `feat: add 3d su deterministic dry run`.**

### Task 5: Add desktop evidence for the new local flow

**Files:**
- Modify: `frontend/src/pages/operations-center-page.tsx` only for the minimal 3D/SU dry-run summary.
- Modify: `frontend/src/components/shadow-projection-run-drawer.tsx` if the ported evidence contract requires it.
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` only for typed read-only evidence.
- Create: `frontend/scripts/check-three-d-dry-run-contract.ts`
- Modify: `frontend/package.json` to register the contract script.

**Interfaces:**
- The一级运行中心 shows only category, workflow version, current gate, status, checkpoint and blocker.
- The二级 drawer shows source evidence, snapshot hash, projection batch, reconciliation and feedback case; it never shows candidate mechanism data as formal facts.

- [ ] **Step 1: Add the contract script and make it fail against the missing summary/evidence.**
- [ ] **Step 2: Implement the minimum typed UI change.**
- [ ] **Step 3: Run the browser contract under the required local-listen permission, then lint/build.**
- [ ] **Step 4: Commit `feat: expose 3d su dry run evidence`.**

### Task 6: Full verification and handoff

**Files:**
- Modify: `docs/superpowers/receipts/2026-08-16-3d-shadow-dry-run-prep.md`
- Modify: `PROJECT_STATUS.md` only if the local receipt needs a concise status entry.

- [ ] **Step 1: Run backend full suite with a fresh temporary `DATA_DIR`.**
- [ ] **Step 2: Run all frontend contracts, new dry-run contract, lint and build serially.**
- [ ] **Step 3: Run `git diff --check` and verify no external remotes changed.**
- [ ] **Step 4: Record exact test counts, branch, commit SHA and explicit no-push/no-merge/no-deploy boundary.**
- [ ] **Step 5: Stop and report; do not push, merge or deploy without a separate user authorization.**

