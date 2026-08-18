# 版本合同驱动的自适应纠偏面板实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让存量回归、增量审核和候选回归都按各自冻结的机制合同生成可继承、可校验、可重算的自适应纠偏面板。

**Architecture:** 后端新增纯函数合同规范化、哈希、完整性门禁、跨版本继承和节点值校验模块；回归创建时把不可变合同写入运行快照，纠偏详情和提交接口只接受该快照。前端以共享合同渲染器替代固定字段清单，V3 规则只显示服务端提供的可读步骤并由服务端重算。旧字段/API 保留兼容读取，任何历史合同不足的记录降级为明确只读状态。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy、SQLite 增量迁移、Pydantic、React 18、TypeScript、TanStack Query、现有 Tailwind/UI 组件。

**Spec:** `docs/superpowers/specs/2026-08-18-adaptive-correction-contract-design.md`

## Global Constraints

- 只在 `/Volumes/WorkSSD/Codex/2026-08-11/labellab/work/correction-contract-adaptive-20260818` 工作树修改；不触碰其他会话工作树。
- 本阶段只本地实现和验证，不推送、创建合并请求、合并、部署，不做 NAS 迁移、真实模型批量调用、自动启用候选或存量重跑。
- 不改写历史评测、人工真值、模型原始响应、现役机制或标签事实发布轴。
- 合同快照使用规范 JSON 和 SHA-256；旧运行不得读取现役合同补齐。
- 浏览器不得执行或提交规则代码；V3 重算唯一权威是服务端评分引擎。
- 所有新增 API 错误使用稳定错误码和中文消息；不得返回密钥、令牌或 Cookie。
- 数据库只做可回退的增量迁移；每个任务先写失败测试，再实现，再运行专项验证并提交。

---

### Task 1: 建立合同规范化、哈希和完整性门禁

**Files:**
- Create: `backend/app/correction_contract.py`
- Create: `backend/tests/test_correction_contract.py`
- Modify: `backend/app/category_evaluation_v3_revisions.py`（在候选启用前调用门禁）
- Test: `backend/tests/test_category_evaluation_v3_revisions.py`

**Interfaces:**
- `normalize_correction_contract(raw: Mapping[str, Any], *, category_key: str) -> dict[str, Any]`
- `correction_contract_hash(contract: Mapping[str, Any]) -> str`
- `validate_correction_contract(contract: Mapping[str, Any]) -> list[str]`
- `assert_correction_contract_complete(contract: Mapping[str, Any]) -> None`
- `validate_node_value(node: Mapping[str, Any], value: Any) -> None`
- `inherit_correction_node(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]`
- `ContractValidationError(code: str, message: str, fields: list[str])`

- [ ] **Step 1: Write failing tests for canonical shape and hash stability**

```python
def test_normalize_contract_sorts_nodes_and_hash_is_stable():
    raw = {"contract_version": "1", "category_key": "inspiration_image", "nodes": [
        {"node_key": "b", "layer": "B", "label": "字段B", "type": "text", "semantic_version": "1"},
        {"node_key": "a", "layer": "A", "label": "字段A", "type": "enum", "options": ["x"], "semantic_version": "1"},
    ]}
    normalized = normalize_correction_contract(raw, category_key="inspiration_image")
    assert [node["node_key"] for node in normalized["nodes"]] == ["a", "b"]
    assert correction_contract_hash(normalized) == correction_contract_hash(normalized.copy())
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `cd backend && pytest tests/test_correction_contract.py::test_normalize_contract_sorts_nodes_and_hash_is_stable -q`

Expected: `FAIL` because `app.correction_contract` does not exist.

- [ ] **Step 3: Implement normalization and validation**

Implement the exact interfaces above. Require every node to have `node_key`, `layer` in `A/B/V3`, non-empty Chinese `label` and `description`, `type`, `semantic_version`, `compatibility_key`, and an explicit `required`/`evidence` description. Require enum options or numeric bounds where applicable; require `recompute_ref` for V3 nodes. Normalize node order by `(layer order, path, order, node_key)`, preserve unknown metadata under `metadata`, and hash the canonical payload without `contract_hash`.

- [ ] **Step 4: Add inheritance and value-validation tests**

```python
def test_inherit_only_compatible_stable_node():
    previous = {"node_key": "v3.final", "type": "enum", "semantic_version": "2", "compatibility_key": "final-level", "human_value": "L2", "reason": "证据", "evidence": [{"text": "边界"}]}
    current = {"node_key": "v3.final", "type": "enum", "semantic_version": "2", "compatibility_key": "final-level"}
    inherited = inherit_correction_node(previous, current)
    assert inherited["inheritance"]["status"] == "inherited"
    assert inherited["human_value"] == "L2"

def test_semantic_change_does_not_inherit():
    previous = {"node_key": "v3.final", "type": "enum", "semantic_version": "1", "compatibility_key": "old", "human_value": "L2"}
    current = {"node_key": "v3.final", "type": "enum", "semantic_version": "2", "compatibility_key": "new"}
    assert inherit_correction_node(previous, current)["inheritance"]["status"] == "changed"
```

- [ ] **Step 5: Add candidate-release gate regression tests**

Create an incomplete candidate with a V3 node missing `recompute_ref`; assert the existing activation path raises `ContractValidationError` with code `CORRECTION_CONTRACT_INCOMPLETE` and leaves the candidate status unchanged. Add a valid contract case that preserves the existing activation behavior.

- [ ] **Step 6: Wire the gate and run tests**

Call `assert_correction_contract_complete` from the candidate activation boundary in `category_evaluation_v3_revisions.py`, translate the exception to the existing domain error shape, and run:

`cd backend && pytest tests/test_correction_contract.py tests/test_category_evaluation_v3_revisions.py -q`

- [ ] **Step 7: Commit**

```bash
git add backend/app/correction_contract.py backend/tests/test_correction_contract.py backend/app/category_evaluation_v3_revisions.py backend/tests/test_category_evaluation_v3_revisions.py
git commit -m "feat: add versioned correction contract validation"
```

### Task 2: Freeze contracts in run snapshots with additive migration

**Files:**
- Modify: `backend/app/models.py` (`BaselineRegressionRun`, `EvaluationProductionRun`, and `PromptRegressionRun`)
- Modify: `backend/app/migrations/runner.py` (migration 75, immediately after the existing migration 74)
- Create: `backend/tests/test_correction_contract_migration.py`
- Modify: `backend/app/main.py` (baseline, incremental, and candidate run snapshot construction)
- Test: `backend/tests/test_migration.py`, `backend/tests/test_baseline_regression.py`, `backend/tests/test_content_ingress_incremental_routing.py`

**Interfaces:**
- `freeze_correction_contract(*, category_key: str, prompt_snapshot: Mapping[str, Any], dimension_snapshot: Mapping[str, Any], production_field_snapshot: Mapping[str, Any], v3_snapshot: Mapping[str, Any]) -> dict[str, Any]`
- `correction_contract_from_run_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any] | None`
- Each of the three run tables gets nullable `correction_contract_json` and `correction_contract_hash` columns. New rows populate both; legacy rows remain `NULL` and retain every original snapshot byte-for-byte.

- [ ] **Step 1: Write migration and snapshot failure tests**

Assert a pre-migration SQLite database receives only the new nullable `correction_contract_json` and `correction_contract_hash` columns on `baseline_regression_runs`, `evaluation_production_runs`, and `prompt_regression_runs`; rerunning migration 75 is a no-op and existing rows remain byte-for-byte unchanged. Assert a newly built baseline snapshot contains a contract hash.

- [ ] **Step 2: Run the tests to verify failure**

Run: `cd backend && pytest tests/test_correction_contract_migration.py tests/test_baseline_regression.py -q`

Expected: `FAIL` because no migration or snapshot contract exists.

- [ ] **Step 3: Implement the additive migration and freeze helper**

Implement `_migration_075_add_correction_contract_snapshots` and register `Migration(75, "add_correction_contract_snapshots", ...)`. Guard each column with `PRAGMA table_info`, add the same JSON/hash checks used by the existing run tables, update each run's immutable trigger to include the two new columns, and never rewrite old snapshot values. Build the contract from the selected prompt, dimension schema, production-field snapshot, and V3 authoritative bundle; normalize and hash it once during run creation.

- [ ] **Step 4: Cover all run creation modes**

Add tests for baseline, incremental, and candidate run creation. Candidate snapshots must use the candidate revision contract, while historical/active snapshots use the selected immutable revision. Assert changing the active projection after creation does not change the stored hash or node list.

- [ ] **Step 5: Run migration and integration tests**

Run: `cd backend && pytest tests/test_correction_contract_migration.py tests/test_migration.py tests/test_baseline_regression.py tests/test_content_ingress_incremental_routing.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/migrations/runner.py backend/app/strategy_bundle.py backend/tests/test_correction_contract_migration.py backend/tests/test_migration.py backend/tests/test_baseline_regression.py backend/tests/test_content_ingress_incremental_routing.py
git commit -m "feat: freeze correction contracts per evaluation run"
```

### Task 3: Add a shared server-side correction view and submission boundary

**Files:**
- Create: `backend/app/correction_view.py`
- Create: `backend/tests/test_correction_view.py`
- Modify: `backend/app/main.py` (request models and correction endpoints around `BaselineCorrectionCreateRequest` and `/api/baseline-corrections`)
- Modify: `backend/app/baseline_regression.py` (`result_snapshot`, correction context, and frozen-contract lookup)
- Test: `backend/tests/test_baseline_regression.py`, `backend/tests/test_baseline_correction_human_evidence.py`

**Interfaces:**
- `build_correction_view(db: Session, *, run: Any, item: Any, previous_item: Any | None = None) -> dict[str, Any]`
- `build_correction_nodes(contract: Mapping[str, Any], *, model_values: Mapping[str, Any], human_values: Mapping[str, Any], previous_values: Mapping[str, Any] | None) -> list[dict[str, Any]]`
- `submit_correction_nodes(db: Session, *, run: Any, item: Any, contract_hash: str, nodes: list[Mapping[str, Any]], review_revision: int, idempotency_key: str, actor: str) -> dict[str, Any]`
- `BaselineCorrectionNodeRequest(node_key: str, human_value: Any, reason: str, evidence: list[dict[str, Any]])`
- `BaselineCorrectionSubmitRequest(contract_hash: str, nodes: list[BaselineCorrectionNodeRequest], review_revision: int, idempotency_key: str)`

- [ ] **Step 1: Write failing view tests**

Build two contract versions containing an added, deleted, and semantic-changed node. Assert the view returns frozen identity, model/current/human values, node metadata, inheritance status, and a V3 readable recompute path. Assert an old run never sees the newer node.

- [ ] **Step 2: Write failing submission tests**

Assert stale hash, unknown node, invalid enum, missing required evidence, and stale review revision each fail closed with stable Chinese error codes. Assert a valid V3 submission calls the existing authoritative scoring function and appends a human correction history event without modifying `raw_response_json` or old review rows.

- [ ] **Step 3: Run focused tests and verify failure**

Run: `cd backend && pytest tests/test_correction_view.py -q`

- [ ] **Step 4: Implement view construction and validation**

Read only the run's frozen snapshot, derive model/current/human values from the item and existing review history, use `inherit_correction_node` by `node_key`, and mark incomplete legacy contracts as `read_only` with an explicit `unavailable_reason`. For V3 nodes, expose `recompute_ref`, human-readable `steps`, and current decision values; never expose executable rule text.

- [ ] **Step 5: Implement append-only submission and idempotency**

Validate all nodes against the stored hash and contract, enforce review revision and idempotency, append a new human evidence event, invoke the authoritative server-side V3 recompute, and return the refreshed view. Keep old correction history and raw model payload immutable.

- [ ] **Step 6: Wire API routes and compatibility payloads**

Add `GET /api/baseline-regressions/{run_id}/items/{item_id}/correction-view` and `POST /api/baseline-regressions/{run_id}/items/{item_id}/corrections`. Extend existing baseline correction payloads with `correction_contract` identity while retaining all existing fields. Use `HTTP 409` for stale/contract conflicts and `HTTP 422` for node-value/evidence errors.

- [ ] **Step 7: Run backend tests and commit**

Run: `cd backend && pytest tests/test_correction_view.py tests/test_baseline_regression.py tests/test_baseline_correction_human_evidence.py -q`

```bash
git add backend/app/correction_view.py backend/tests/test_correction_view.py backend/app/main.py backend/app/baseline_regression.py
git commit -m "feat: expose contract-driven correction views"
```

### Task 4: Connect the three correction entry paths and V3 recomputation

**Files:**
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/main.py` (baseline, incremental, and candidate correction adapters)
- Modify: `backend/app/content_ingress_incremental_routing.py` or the existing incremental review service found by `rg "incremental.*review|human.*correction" backend/app -n`
- Modify: `backend/app/category_evaluation_v3_revisions.py`
- Create: `backend/tests/test_adaptive_correction_three_lanes.py`
- Test: `backend/tests/test_baseline_correction_human_evidence.py`, `backend/tests/test_content_ingress_incremental_routing.py`, `backend/tests/test_category_evaluation_v3_revisions.py`

**Interfaces:**
- `correction_lane_for_run(run: Any) -> Literal["baseline", "incremental", "candidate"]`
- `prepare_correction_generation(...)` consumes the same `correction_view` evidence for all lanes.
- `route_human_evidence(report: Mapping[str, Any]) -> Literal["A", "B", "V3", "A+B"]`
- `recompute_v3_from_correction(...) -> dict[str, Any]`

- [ ] **Step 1: Write failing three-lane tests**

Construct one fixture for each lane and submit the same node evidence. Assert all three use the same contract hash validation, preserve lane-specific frozen snapshots, and route A-only, B-only, mixed, and V3 evidence deterministically.

- [ ] **Step 2: Write failing V3 parity tests**

Change a threshold/level-mapping node through a human submission and assert the returned final level and score equal the existing authoritative scoring engine output for the frozen contract. Assert the browser cannot supply a rule expression or threshold override.

- [ ] **Step 3: Implement shared lane adapter and routing**

Refactor only shared input preparation; retain existing candidate generation, regression, and approval semantics. Add explicit V3 evidence routing and preserve the existing `OptimizationCaseQueue.source_type='baseline_regression'` contract for baseline-derived cases.

- [ ] **Step 4: Run lane and orchestration tests**

Run: `cd backend && pytest tests/test_adaptive_correction_three_lanes.py tests/test_baseline_correction_human_evidence.py tests/test_content_ingress_incremental_routing.py tests/test_category_evaluation_v3_revisions.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/baseline_correction_orchestration.py backend/app/main.py backend/app/content_ingress_incremental_routing.py backend/app/category_evaluation_v3_revisions.py backend/tests/test_adaptive_correction_three_lanes.py
git commit -m "feat: unify correction evidence across evaluation lanes"
```

### Task 5: Build the TypeScript contract model and shared dynamic renderer

**Files:**
- Create: `frontend/src/features/correction-contract/types.ts`
- Create: `frontend/src/features/correction-contract/contract-renderer.tsx`
- Create: `frontend/src/features/correction-contract/contract-renderer.test.tsx`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/features/baseline-regression/correction-workbench.tsx`
- Test: `frontend/scripts/check-baseline-correction-workbench.ts`, `frontend/scripts/check-baseline-v3-run-config.ts`

**Interfaces:**
- `CorrectionContract`, `CorrectionContractNode`, `CorrectionNodeValue`, `CorrectionView`, `CorrectionInheritance`
- `renderCorrectionNode(node: CorrectionContractNode, value: CorrectionNodeValue, onChange: ...) -> JSX.Element`
- `groupCorrectionNodes(nodes: readonly CorrectionContractNode[]) -> Record<"A" | "B" | "V3", CorrectionContractNode[]>`
- API methods: `getCorrectionView(runId, itemId)` and `submitCorrectionNodes(runId, itemId, request)`

- [ ] **Step 1: Write failing renderer tests**

Render enum, integer, decimal, boolean, text, list, and V3 decision nodes from fixture JSON. Assert Chinese labels, required/evidence markers, read-only changed/legacy states, and no fixed production-field list. Assert nodes are grouped A/B/V3 in contract order.

- [ ] **Step 2: Run the focused frontend test to verify failure**

Run: `cd frontend && npm run test -- contract-renderer.test.tsx` (or the repository's existing Vitest command after inspecting `package.json`).

- [ ] **Step 3: Implement typed contract model and renderer**

Use discriminated unions for node types and preserve unknown metadata. Render server-provided V3 steps as read-only rows. Show contract version/hash and snapshot status at the top; show “新增待确认”, “合同已变化/不可继承”, and legacy read-only reasons explicitly.

- [ ] **Step 4: Wire workbench without changing navigation behavior**

Replace only the hard-coded correction controls in `CorrectionWorkbench` with the shared renderer; keep existing thumbnail/lightbox, previous/next, explicit save, and no-auto-navigation behavior intact. Fetch the view by run/item and submit with the stored review revision and idempotency key.

- [ ] **Step 5: Run frontend typecheck/build and commit**

Run: `cd frontend && npm run build` and the focused renderer/check scripts.

```bash
git add frontend/src/features/correction-contract frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/baseline-regression/correction-workbench.tsx frontend/scripts/check-baseline-correction-workbench.ts
git commit -m "feat: render correction panel from frozen contracts"
```

### Task 6: Integrate baseline, incremental, and candidate pages with persistence

**Files:**
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/src/pages/review-page.tsx`
- Modify: `frontend/src/pages/incremental-workspace-page.tsx`
- Modify: `frontend/src/pages/review-correction-form.tsx`
- Modify: `frontend/src/lib/review-submit.ts`
- Create: `frontend/src/features/correction-contract/correction-view-state.ts`
- Create: `frontend/src/features/correction-contract/correction-view-state.test.ts`

**Interfaces:**
- `correctionDraftFromView(view: CorrectionView) -> CorrectionDraft`
- `mergeCorrectionResponse(previous: CorrectionDraft, response: CorrectionView) -> CorrectionDraft`
- `correctionSubmissionPayload(draft: CorrectionDraft, view: CorrectionView) -> BaselineCorrectionSubmitRequest`

- [ ] **Step 1: Write failing persistence and refresh tests**

Assert save returns the same item view with human values/reasons/evidence; reopening loads them as editable initial values; saving again appends a new revision rather than overwriting; refresh and previous/next navigation keep the current contract hash and do not auto-advance.

- [ ] **Step 2: Implement shared draft state and API integration**

Use query-keyed state by `(lane, runId, itemId, contractHash)`, invalidate only the current item after save, and preserve draft values on validation errors. Reuse the same submission adapter for all three pages.

- [ ] **Step 3: Add browser-facing empty/legacy/error states**

Show explicit read-only legacy notices, stale-contract retry controls, and per-node validation messages in Chinese. Do not silently omit deleted or unavailable historical nodes.

- [ ] **Step 4: Run frontend checks and commit**

Run the focused state tests, existing correction scripts, `npm run build`, and `git diff --check`.

```bash
git add frontend/src/pages/baseline-regression-page.tsx frontend/src/pages/review-page.tsx frontend/src/pages/incremental-workspace-page.tsx frontend/src/pages/review-correction-form.tsx frontend/src/lib/review-submit.ts frontend/src/features/correction-contract
git commit -m "feat: persist adaptive correction across review lanes"
```

### Task 7: Add historical compatibility and candidate refresh behavior

**Files:**
- Modify: `backend/app/correction_view.py`
- Modify: `backend/app/main.py` candidate decision/activation response
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/src/lib/api.ts`
- Create: `backend/tests/test_correction_contract_legacy.py`
- Create: `frontend/src/features/correction-contract/candidate-refresh.test.ts`

**Interfaces:**
- `legacy_correction_view_from_archived_fields(item: Any) -> CorrectionView`
- Activation response includes `mechanism_refresh: { category_key, prompt_version_ids, v3_revision_id, contract_hash }`.

- [ ] **Step 1: Write legacy and refresh failure tests**

Assert a legacy result with a complete archived field subset is editable only for those fields; an incomplete archive returns `read_only=true` and lists every unavailable node. Assert candidate approval returns refresh metadata and the currently open old run keeps its old contract.

- [ ] **Step 2: Implement legacy fallback and refresh metadata**

Never query active configuration when building a legacy view. After successful candidate activation, invalidate version queries for new runs only and leave existing run query data untouched.

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && pytest tests/test_correction_contract_legacy.py -q`; `cd frontend && npm run build`.

```bash
git add backend/app/correction_view.py backend/app/main.py backend/tests/test_correction_contract_legacy.py frontend/src/pages/baseline-regression-page.tsx frontend/src/lib/api.ts frontend/src/features/correction-contract/candidate-refresh.test.ts
git commit -m "feat: preserve legacy snapshots and refresh new runs"
```

### Task 8: End-to-end validation, documentation, and acceptance evidence

**Files:**
- Modify: `PROJECT_STATUS.md` (append implementation status and local verification only)
- Modify: `docs/decisions/0049-versioned-adaptive-correction-contract.md` (change `Proposed` to `Accepted` only after all gates pass)
- Create: `backend/tests/test_adaptive_correction_e2e.py`
- Create: `frontend/scripts/check-correction-contract.ts`
- Create: `outputs/adaptive_correction_contract_local_validation_20260818.md`

**Interfaces:**
- No new runtime interface; this task records evidence for the interfaces from Tasks 1–7.

- [ ] **Step 1: Write the end-to-end test before final claims**

Create two mechanism snapshots and exercise baseline, incremental, and candidate views: add/delete/type-change/semantic-change nodes, inherit compatible evidence, submit V3 correction, reject stale hash, and verify candidate remains disabled until explicit approval.

- [ ] **Step 2: Run complete backend verification**

Run: `cd backend && pytest -q`; require all tests pass with no new failures and record the exact count.

- [ ] **Step 3: Run frontend verification**

Run: `cd frontend && npm run build`; run `node --import tsx scripts/check-correction-contract.ts` plus existing correction/workbench scripts and record outputs.

- [ ] **Step 4: Run browser critical path locally**

Start the existing local backend/frontend test servers only if needed, exercise contract metadata, dynamic nodes, save/reopen, stale-hash error, V3 steps, and previous/next controls in a real browser, and save a screenshot/evidence note. Do not connect to the test server or production.

- [ ] **Step 5: Run hygiene and coverage checks**

Run `git diff --check`, scan the plan/spec for `TODO|TBD|placeholder`, verify no key/token/Cookie strings were added, and inspect `git status --short` for unrelated files or the `frontend/node_modules` symlink.

- [ ] **Step 6: Update status and ADR after successful gates**

Append only local implementation and test evidence to `PROJECT_STATUS.md`; change ADR-0049 to `Accepted` only if every acceptance item passes. If any gate fails, leave ADR `Proposed`, document the blocker, and stop without external delivery.

- [ ] **Step 7: Commit final local evidence**

```bash
git add PROJECT_STATUS.md docs/decisions/0049-versioned-adaptive-correction-contract.md backend/tests/test_adaptive_correction_e2e.py frontend/scripts/check-correction-contract.ts outputs/adaptive_correction_contract_local_validation_20260818.md
git commit -m "test: validate adaptive correction contract end to end"
```

## Self-Review Checklist

- [ ] Spec coverage: contract schema, per-run freeze, unified view/submit API, inheritance, dynamic UI, legacy fallback, candidate gate, three lanes, V3 parity, browser verification, and rollback boundaries each have a task.
- [ ] Placeholder scan: no `TODO`, `TBD`, “later”, or unspecified implementation step appears in this plan.
- [ ] Type consistency: backend and frontend names used by later tasks are defined in earlier tasks and use `contract_hash`, `node_key`, `review_revision`, and `idempotency_key` consistently.
- [ ] Parallel-worktree safety: only the dedicated worktree is listed; no 3D/SU or other session files are included.
