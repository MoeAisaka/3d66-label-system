# 全局自动组批 Task 4 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让自动消费者按类目、增量/存量链路、启用代次、机制指纹、路由键和提示词版本严格隔离组批，并把每次组批落账为可审计的 `AutomationBatch`。

**Architecture:** 新增一个纯确定性的组批模块，负责生成泳道身份、筛选可消费案例、应用门槛/即时触发/冷却/公平轮转并创建批次及批次明细；现有 `optimization_automation.consume_optimization_queue_once` 只负责调用该模块、冻结输入和承接原有预算、租约、模型调用及发布门禁。历史审计、待补证据和机制漂移案例在选择层即被排除。

**Tech Stack:** Python 3.12、SQLAlchemy、SQLite、pytest、现有 `AutomationLanePolicy` / `OptimizationCaseQueue` / `AutomationBatch` / `AutomationBatchCase` 模型。

## Global Constraints

- 只修改当前工作树 `/Volumes/WorkSSD/Codex/2026-08-11/labellab/work/global-auto-batching-mechanism-20260817`。
- 不读取或保存密钥，不调用真实模型，不启用真实自动化，不推送、不创建合并请求、不合并、不部署。
- 不改变增量/存量既有语义；两条链路只允许在同一执行内核复用代码。
- `historical_audit`、`awaiting_evidence`、`rejected` 案例永远不可被自动消费者选中。
- 组批键必须包含类目、链路类型、代次、机制指纹、路由键；提示词版本作为额外隔离字段。
- 既有全局预算、租约、重试、人工发布门禁和 `OptimizationOptimizationRun` 状态机保持兼容。
- 迁移 73 已包含自动泳道与批次表；本任务不新增迁移。

---

### Task 1: 提取可测试的泳道身份与候选分组

**Files:**
- Create: `backend/app/automation_batching.py`
- Test: `backend/tests/test_automation_batching.py`

**Interfaces:**
- Consumes: `OptimizationCaseQueue`, `AutomationLanePolicy`, `AutomationPolicy`。
- Produces: `build_case_lane_key(case: OptimizationCaseQueue) -> tuple[str, str, int, str, str, str]`、`select_ready_lane(*, available: list[OptimizationCaseQueue], lane_policies: list[AutomationLanePolicy], policy: AutomationPolicy, now: datetime) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]`。

- [ ] **Step 1: Write the failing tests**

```python
def test_lane_key_separates_pipeline_generation_mechanism_route_and_prompt():
    first = build_case_lane_key(_case(pipeline_kind="incremental", generation=1, route="A", prompt="b1"))
    second = build_case_lane_key(_case(pipeline_kind="baseline", generation=1, route="A", prompt="b1"))
    third = build_case_lane_key(_case(pipeline_kind="incremental", generation=2, route="A", prompt="b1"))
    fourth = build_case_lane_key(_case(pipeline_kind="incremental", generation=1, route="B", prompt="b1"))
    fifth = build_case_lane_key(_case(pipeline_kind="incremental", generation=1, route="A", prompt="b2"))
    assert len({first, second, third, fourth, fifth}) == 5

def test_selector_ignores_historical_and_awaiting_evidence_cases():
    ready, skipped = select_ready_lane(
        available=[_case(state="historical_audit"), _case(state="awaiting_evidence"), _case(state="eligible")],
        lane_policies=[_lane(status="enabled")],
        policy=_policy(threshold=1),
        now=NOW,
    )
    assert ready is not None
    assert ready["case_ids"] == [eligible_case.id]

def test_selector_does_not_mix_two_mechanism_fingerprints():
    ready, skipped = select_ready_lane(
        available=[_case(fingerprint="a" * 64), _case(fingerprint="b" * 64)],
        lane_policies=[_lane(fingerprint="a" * 64, threshold=2)],
        policy=_policy(threshold=2),
        now=NOW,
    )
    assert ready is None
    assert skipped[0]["code"] == "threshold_wait"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_automation_batching.py`

Expected: FAIL because `backend/app/automation_batching.py` and the selector functions do not exist.

- [ ] **Step 3: Implement the deterministic selector**

Implement `automation_batching.py` with:

```python
LaneKey = tuple[str, str, int, str, str, str]

def build_case_lane_key(case: OptimizationCaseQueue) -> LaneKey:
    return (
        case.category_key,
        case.pipeline_kind,
        case.automation_generation,
        case.mechanism_fingerprint or "",
        case.route_key or "",
        case.prompt_version,
    )
```

The selector must group only identical keys, require an enabled policy whose category/pipeline/generation/fingerprint match, apply immediate severities before threshold, then use `case_threshold`, `min_batch_size`, cooldown, and deterministic category/lane order. It must return explicit skip records for `lane_missing`, `lane_paused`, `mechanism_mismatch`, `threshold_wait`, and `cooldown`.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_automation_batching.py`

Expected: all selector tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/automation_batching.py backend/tests/test_automation_batching.py
git commit -m "feat: isolate automation batching lanes"
```

### Task 2: Persist an immutable automation batch and its case set

**Files:**
- Modify: `backend/app/automation_batching.py`
- Test: `backend/tests/test_automation_batching.py`

**Interfaces:**
- Consumes: `select_ready_lane` output and an enabled `AutomationLanePolicy`。
- Produces: `create_automation_batch(db, *, lane, selected_cases, policy, trigger_reason, now) -> AutomationBatch`。

- [ ] **Step 1: Write the failing persistence tests**

```python
def test_create_batch_freezes_lane_policy_and_case_set():
    batch = create_automation_batch(
        db,
        lane=lane,
        selected_cases=[case_a, case_b],
        policy=policy,
        trigger_reason="threshold",
        now=NOW,
    )
    assert batch.status == "queued"
    assert batch.category_key == lane.category_key
    assert batch.pipeline_kind == lane.pipeline_kind
    assert batch.generation == lane.generation
    assert batch.mechanism_fingerprint == lane.mechanism_fingerprint
    assert json.loads(batch.frozen_policy_json)["case_threshold"] == policy.case_threshold
    assert db.query(AutomationBatchCase).count() == 2

def test_create_batch_is_idempotent_for_same_lane_and_case_set():
    first = create_automation_batch(
        db,
        lane=lane,
        selected_cases=[case_a, case_b],
        policy=policy,
        trigger_reason="threshold",
        now=NOW,
    )
    second = create_automation_batch(
        db,
        lane=lane,
        selected_cases=[case_a, case_b],
        policy=policy,
        trigger_reason="threshold",
        now=NOW,
    )
    assert first.id == second.id
    assert db.query(AutomationBatch).count() == 1
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_automation_batching.py -k batch`

Expected: FAIL because `create_automation_batch` is not implemented.

- [ ] **Step 3: Implement batch persistence**

Canonicalize selected case IDs in ascending order, hash the tuple with SHA-256, and derive `batch_key` from the lane identity plus case-set hash. Insert `AutomationBatch` and `AutomationBatchCase` rows in one transaction. On duplicate `batch_key`, return the existing batch without mutating its frozen policy or case set. Do not change case status in this helper; status transitions remain owned by the consumer.

- [ ] **Step 4: Run the persistence tests**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_automation_batching.py -k batch`

Expected: all persistence tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/automation_batching.py backend/tests/test_automation_batching.py
git commit -m "feat: persist frozen automation batches"
```

### Task 3: Integrate lane selection and batch creation into the existing consumer

**Files:**
- Modify: `backend/app/optimization_automation.py:1096-1610,2068-2098`
- Test: `backend/tests/test_phase_b_automation.py`
- Test: `backend/tests/test_automation_batching.py`

**Interfaces:**
- Consumes: `select_ready_lane`, `create_automation_batch`。
- Produces: existing `consume_optimization_queue_once` responses with `batch_id`, `lane_key`, and `skipped_cohorts` while retaining `run_id`, budget, lease, and dry-run fields.

- [ ] **Step 1: Write failing integration tests**

```python
def test_consumer_batches_only_one_lane_and_returns_batch_id():
    _seed_enabled_lanes(db)
    _seed_cases_for_two_routes(db)
    result = consume_optimization_queue_once(db, worker_id="lane-worker")
    assert result["status"] == "planned"
    assert result["batch_id"] is not None
    assert result["lane_key"][4] in {"A", "B"}
    assert db.query(AutomationBatch).count() == 1

def test_consumer_never_dispatches_awaiting_evidence_case():
    _seed_case(db, admission_state="awaiting_evidence")
    result = consume_optimization_queue_once(db, worker_id="evidence-worker")
    assert result["status"] in {"idle", "threshold_wait", "lane_missing"}
    assert db.query(AutomationBatch).count() == 0
```

- [ ] **Step 2: Run the integration tests and verify they fail**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_phase_b_automation.py -k lane`

Expected: FAIL because the consumer still groups only by category/prompt and does not create `AutomationBatch` rows.

- [ ] **Step 3: Integrate without changing model execution semantics**

Replace the current category/prompt cohort call with the selector. Keep the existing adapter binding, regression binding, budget reservation, lease claim, `AutomationOptimizationRun` creation, and dry-run behavior. After selecting cases, create or fetch the frozen `AutomationBatch`; include its ID and lane key in `frozen_input`, audit payloads, and the returned status. If a batch already exists, do not claim cases a second time.

- [ ] **Step 4: Run focused integration tests**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_phase_b_automation.py -k lane tests/test_automation_batching.py`

Expected: all lane-aware consumer tests pass and existing phase-B tests remain green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/optimization_automation.py backend/tests/test_phase_b_automation.py backend/tests/test_automation_batching.py
git commit -m "feat: route consumer through isolated automation batches"
```

### Task 4: Add regression coverage and final verification

**Files:**
- Modify: `backend/tests/test_automation_history_isolation.py`
- Modify: `backend/tests/test_phase_b_automation.py`
- Modify: `docs/superpowers/receipts/2026-08-17-global-auto-batching-task4-receipt.md`

- [ ] **Step 1: Add cross-lane safety tests**

Cover incremental versus baseline, generation changes, mechanism fingerprint changes, route changes, historical audit exclusion, awaiting-evidence exclusion, deterministic retry after a failed batch, and idempotent restart after a valid lease.

- [ ] **Step 2: Run the focused regression suite**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q tests/test_automation_batching.py tests/test_automation_case_intake.py tests/test_automation_history_isolation.py tests/test_phase_b_automation.py`

Expected: all focused tests pass with no new failures.

- [ ] **Step 3: Run the backend full suite and compile check**

Run: `PYTHONPATH=.. .venv312/bin/pytest -q` from `backend/`, then `python3 -m compileall -q backend/app` from the worktree root.

Expected: zero failed tests, at most the existing skipped test and warnings, compile check exit 0.

- [ ] **Step 4: Write the receipt and verify the worktree**

Record commits, test counts, the fact that no real model call or automation enablement occurred, and any remaining Task 5 work (route-specific candidate generation and UI). Run `git diff --check` and `git status --short`.

- [ ] **Step 5: Commit the receipt**

```bash
git add docs/superpowers/receipts/2026-08-17-global-auto-batching-task4-receipt.md
git commit -m "docs: record global automation batching verification"
```
