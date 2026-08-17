# LabelLab 全类目自动组批与机制迭代 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不吞入历史积压、不跨类目或机制混批、且不自动发布的前提下，把人工最终纠偏接通到候选生成、三角色配对回归和人工二审，并为灵感图五档准召提供可审计的候选评测闭环。

**Architecture:** 复用现有 `OptimizationCaseQueue`、常驻自动优化工作进程、`PromptRegressionRun` 三角色配对回归和人工采用门禁；新增版本化类目泳道、纠偏资格快照、组批批次与候选包。全局层只负责总预算、总并发、公平调度、租约和熔断，类目泳道冻结增量/存量、机制指纹、路由和黄金集，任何候选在人工采用前都不能改变现役机制、存量结果或标签事实。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy、SQLite、pytest；React 19、TypeScript、Vite、TanStack Query；数据库迁移统一使用 `backend/app/migrations/runner.py`。

## Global Constraints

- 基线为 `origin/main@50e5b1572dd3ea5b65a7641ca50ae32fd850df07`；实施前必须先完成 3D/SU dry-run 包的唯一合流，保留其 72 号迁移能力，功能迁移使用合流后的下一空号（当前预期为 73）。
- 所有正在纠偏的类目同时开放能力；每个“类目 + 增量/存量 + 启用代次 + 完整机制指纹 + 节点路由”都是独立泳道，禁止跨泳道组批、共享黄金集或共享候选结论。
- 全局自动化启用前约 2,286 条历史待处理案例统一为 `historical_audit`，原始案例 JSON、人工真值和来源引用不可改写；只有人工“纳入当前泳道”才追加新的可消费副本。
- 自动消费只接受最终人工纠偏，必须同时具备人工身份、纠偏节点、改前值、改后值、结构化理由、所需证据、类目、链路和冻结机制身份；缺字段进入待补证据，不猜测补齐。
- 机制指纹必须覆盖模型、调用甲、调用乙、维度合同、第三版规则、评分引擎和等级映射；不能只使用单一提示词版本。
- 候选固定包含目标错例、稳定对照、盲测保留三类回归；技术失败与业务失败分开统计和重试，技术覆盖不足时不输出业务结论。
- 灵感图继续五档：推荐档按第一档/第二档合并计算，精确率和召回率均不低于 80%，素材占比不超过 35%（运营目标约 30%）；第三档、第四档、第五档各自精确率和召回率均不低于 80%；第一档误升按精确率优先，第五档漏判按召回率优先。
- 调用甲负责范围、内容事实、画质、媒介、硬伤和过滤信号；调用乙负责美感维度、视觉证据、优缺点和推荐资格；第三版规则负责权重、扣分、封顶、豁免、阈值和等级映射；多节点纠偏生成一个组合候选包并记录依赖顺序。
- 人工纠偏与候选二审之间不得要求运营点击检查队列、选择模型、启动分析、创建候选、创建回归或刷新状态；管理员只保留失败重试和历史案例纳入入口。
- AI 不得自动启用机制、覆盖存量结果、发布标签事实或启动存量重跑；采用、标签发布、存量重跑保持独立幂等键。
- 不推送、不合并、不部署、不打开真实预算和不启动真实模型调用，除非另行获得明确授权；本计划的本地测试全部使用隔离数据库和测试替身。

## Pre-execution Gate: 唯一合流基线

在 Task 1 前，读取标签实验台会话最终包台账，确认 dry-run 分支的完整提交号、未提交文件、迁移号和专项测试结果。以最新 `origin/main` 创建独立合流工作树，选择性吸收 `codex/3d-shadow-dry-run-prep-20260816`，禁止整支合并历史 `codex/3d-shadow-consumption-mvp-v1`。必须保留 `PROJECT_STATUS.md` 两侧状态段、`backend/app/model_3d_su_category_seed.py`、`backend/app/worker.py`、`backend/app/worker_v3_authoritative.py` 两侧能力，并确认合流后的最高迁移号为 72；若出现额外迁移或无法证明包已收口，停在合流门禁，不改本计划涉及的业务文件。

```bash
git fetch origin main
git rev-parse origin/main
git show --stat --oneline codex/3d-shadow-dry-run-prep-20260816
git log --oneline origin/main..codex/3d-shadow-dry-run-prep-20260816
python3 -m pytest -q backend/tests/test_readonly_sources.py backend/tests/test_shadow_projection.py backend/tests/test_three_d_shadow_consumption_flow.py
```

验收：形成 `docs/superpowers/receipts/2026-08-17-combined-baseline-ledger.md`，列出合流提交、文件、迁移、测试和未部署状态；未满足时不进入业务实现。

---

### Task 1: 版本化类目泳道与资格快照数据合同

**Files:**
- Modify: `backend/app/models.py`（在 `OptimizationCaseQueue`、`AutomationPolicy` 后增加泳道与快照模型，并为案例队列增加链路/代次/机制指纹/资格快照引用字段）
- Create: `backend/app/automation_lanes.py`
- Create: `backend/tests/test_automation_lanes.py`

**Interfaces:**
- `build_mechanism_fingerprint(*, model_snapshot: Mapping[str, Any], call_a_snapshot: Mapping[str, Any], call_b_snapshot: Mapping[str, Any], dimension_contract: Mapping[str, Any], v3_rules: Mapping[str, Any], scoring_engine_version: str, level_mapping: Mapping[str, Any]) -> str`
- `build_lane_key(*, category_key: str, pipeline_kind: Literal["incremental", "baseline"], generation: int, mechanism_fingerprint: str, route_key: str) -> str`
- `validate_lane_snapshot(snapshot: Mapping[str, Any]) -> None`
- 新表 `AutomationLanePolicy`：`category_key`、`pipeline_kind`、`generation`、`status`、`enabled_at`、`case_threshold`、`min_batch_size`、`max_wait_seconds`、`immediate_severities_json`、`daily_budget_micros`、`cooldown_seconds`、`max_candidates`、`max_consecutive_batches`、`target_sample_set_id`、`stable_control_set_id`、`blind_holdout_set_id`、`mechanism_snapshot_json`、`mechanism_fingerprint`、`revision`；唯一键为 `(category_key, pipeline_kind, generation)`。
- 新表 `OptimizationCaseEligibilitySnapshot`：`case_id` 唯一、`lane_policy_id`、`category_key`、`pipeline_kind`、`generation`、`mechanism_fingerprint`、`route_key`、`correction_revision`、`evidence_json`、`admission_state`、`eligible_at`、`historical_source`；`admission_state` 只允许 `awaiting_evidence`、`historical_audit`、`eligible`、`admitted`、`rejected`。
- 新表 `AutomationBatch` 与 `AutomationBatchCase`：批次保存完整 `batch_key`、冻结策略、组批键、案例集合哈希、状态、租约和错误；关联表保证一个资格快照最多进入一个自动批次。

- [ ] **Step 1: 写失败测试，锁定指纹字段和泳道隔离**

```python
def test_mechanism_fingerprint_changes_when_v3_mapping_changes():
    base = dict(model_snapshot={"model": "m1"}, call_a_snapshot={"v": "a1"},
                call_b_snapshot={"v": "b1"}, dimension_contract={"hash": "d1"},
                v3_rules={"threshold": 80}, scoring_engine_version="score-v4",
                level_mapping={"L1": [90, 100]})
    first = build_mechanism_fingerprint(**base)
    second = build_mechanism_fingerprint(**{**base, "v3_rules": {"threshold": 81}})
    assert len(first) == 64 and first != second

def test_lane_key_separates_incremental_baseline_and_route():
    assert build_lane_key(category_key="space_image", pipeline_kind="incremental",
                          generation=2, mechanism_fingerprint="a" * 64,
                          route_key="A") != build_lane_key(
                              category_key="space_image", pipeline_kind="baseline",
                              generation=2, mechanism_fingerprint="a" * 64,
                              route_key="A")
```

Run: `python3 -m pytest -q backend/tests/test_automation_lanes.py -k "fingerprint or lane_key"`
Expected: FAIL because the functions and tables do not exist.

- [ ] **Step 2: 实现最小不可变快照与校验**

`build_mechanism_fingerprint` 对六类机制快照做规范化 JSON 后计算 SHA-256；`build_lane_key` 对完整键做规范化 JSON 后计算可读前缀加哈希；`validate_lane_snapshot` 拒绝缺少链路、代次、机制指纹或路由的快照。模型上的 `CheckConstraint` 禁止跨类别、跨链路和非正代次。

- [ ] **Step 3: 运行测试并提交**

```bash
python3 -m pytest -q backend/tests/test_automation_lanes.py
git add backend/app/models.py backend/app/automation_lanes.py backend/tests/test_automation_lanes.py
git commit -m "feat: add versioned automation lane contracts"
```

### Task 2: 历史积压审计态与安全启用代次

**Files:**
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/automation_lanes.py`
- Modify: `backend/app/optimization_automation.py`
- Create: `backend/tests/test_automation_history_isolation.py`
- Modify: `backend/tests/test_migration.py`

**Interfaces:**
- `quarantine_pre_enable_cases(db: Session, *, enabled_at: datetime, actor: str) -> int`
- `admit_historical_case(db: Session, *, case_id: int, lane_policy_id: int, actor: str, reason: str) -> OptimizationCaseQueue`
- `case_is_dispatchable(case: OptimizationCaseQueue, *, now: datetime) -> bool`
- `OptimizationCaseQueue` 增加 `pipeline_kind`、`automation_generation`、`mechanism_fingerprint`、`route_key`、`eligibility_snapshot_id`、`admission_state`；历史行只更新这些新字段和审计事件，不改 `case_json`、`evaluation_id`、`final_review_id`。

- [ ] **Step 1: 写失败测试，证明旧案例不会被全局开关吞入**

```python
def test_pre_enable_cases_are_historical_audit_and_not_dispatchable(db):
    old = _case(db, created_at=datetime(2026, 8, 16, tzinfo=timezone.utc))
    new = _case(db, created_at=datetime(2026, 8, 17, 1, tzinfo=timezone.utc))
    quarantine_pre_enable_cases(db, enabled_at=datetime(2026, 8, 17, tzinfo=timezone.utc), actor="admin")
    db.refresh(old); db.refresh(new)
    assert old.admission_state == "historical_audit"
    assert case_is_dispatchable(old, now=datetime.now(timezone.utc)) is False
    assert new.admission_state != "historical_audit"

def test_admit_historical_case_creates_new_idempotent_copy_without_mutating_source(db):
    source = _case(db, admission_state="historical_audit")
    admitted = admit_historical_case(db, case_id=source.id, lane_policy_id=1, actor="reviewer", reason="人工复核")
    assert admitted.id != source.id
    assert db.get(OptimizationCaseQueue, source.id).admission_state == "historical_audit"
```

Run: `python3 -m pytest -q backend/tests/test_automation_history_isolation.py -k "pre_enable or historical"`
Expected: FAIL because historical admission fields and migration are absent.

- [ ] **Step 2: 添加合流后下一迁移**

在 `MIGRATIONS` 最高号为 72 的 combined 基线上追加 `Migration(73, "add_global_automation_lanes", _migration_073_add_global_automation_lanes)`。迁移使用 `CREATE TABLE IF NOT EXISTS`、逐列 `ALTER TABLE`、显式默认安全值和重复执行保护；把启用前既有待处理案例标为 `historical_audit`，不回填类目、链路、机制指纹或证据。若 combined 基线最高号不是 72，先停下并重新编号，不在本提交中抢占迁移号。

- [ ] **Step 3: 接入消费者过滤、迁移回归与提交**

```bash
python3 -m pytest -q backend/tests/test_migration.py -k "global_automation or idempotent"
python3 -m pytest -q backend/tests/test_automation_history_isolation.py
git add backend/app/migrations/runner.py backend/app/automation_lanes.py backend/app/optimization_automation.py backend/tests/test_migration.py backend/tests/test_automation_history_isolation.py
git commit -m "feat: isolate historical automation backlog"
```

### Task 3: 最终人工纠偏证据门禁与零点击入队

**Files:**
- Create: `backend/app/automation_case_intake.py`
- Modify: `backend/app/baseline_regression.py`
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/production_feedback.py`
- Modify: `backend/app/main.py`（只在最终人工纠偏完成和生产回流入口调用新模块）
- Create: `backend/tests/test_automation_case_intake.py`
- Extend: `backend/tests/test_baseline_correction_human_evidence.py`

**Interfaces:**
- `FinalCorrectionEvidence`：`category_key`、`pipeline_kind`、`evaluation_id`、`final_review_id`、`correction_revision`、`node_corrections`、`human_reviews`、`mechanism_snapshot`、`mechanism_fingerprint`。
- `build_final_correction_evidence(item: BaselineRegressionItem | ProductionFeedbackEvent, db: Session) -> FinalCorrectionEvidence`
- `qualify_correction(evidence: FinalCorrectionEvidence) -> tuple[bool, list[str]]`
- `admit_final_correction(db: Session, *, evidence: FinalCorrectionEvidence, lane: AutomationLanePolicy) -> OptimizationCaseEligibilitySnapshot`
- `on_final_review_completed(db: Session, *, evaluation_id: int, actor: str) -> OptimizationCaseEligibilitySnapshot | None`

- [ ] **Step 1: 写失败测试，缺证据不得入队**

```python
def test_final_correction_requires_reason_and_evidence():
    evidence = _evidence(reason="", evidence=[])
    qualified, blockers = qualify_correction(evidence)
    assert qualified is False
    assert set(blockers) >= {"reason_missing", "evidence_missing"}

def test_same_final_review_revision_is_idempotent(db):
    evidence = _evidence(correction_revision=3, reason="画面主体被遮挡", evidence=[{"field": "watermark"}])
    first = admit_final_correction(db, evidence=evidence, lane=_lane(db))
    second = admit_final_correction(db, evidence=evidence, lane=_lane(db))
    assert first.id == second.id
```

Run: `python3 -m pytest -q backend/tests/test_automation_case_intake.py -k "requires or idempotent"`
Expected: FAIL because the final-evidence contract and eligibility snapshot do not exist.

- [ ] **Step 2: 从现有冻结输入复用人工证据**

复用 `baseline_regression._correction_context` 的节点纠偏与最终审核证据、`correction_input_snapshot` 的机制/执行快照和 `production_feedback.ingest_production_feedback` 的事件幂等性；只接受 `source == "human"` 的节点纠偏和 `review_stage == "completed"` 的最终审核。自动纠偏记录没有人工最终确认时进入 `awaiting_evidence`，不直接形成可消费案例。

- [ ] **Step 3: 在最终人工提交事务末尾触发入队，运行专项测试并提交**

```bash
python3 -m pytest -q backend/tests/test_automation_case_intake.py backend/tests/test_baseline_correction_human_evidence.py
git add backend/app/automation_case_intake.py backend/app/baseline_regression.py backend/app/baseline_correction_orchestration.py backend/app/production_feedback.py backend/app/main.py backend/tests/test_automation_case_intake.py backend/tests/test_baseline_correction_human_evidence.py
git commit -m "feat: admit only evidenced final corrections"
```

### Task 4: 类目/链路/机制隔离组批、公平调度与恢复

**Files:**
- Create: `backend/app/automation_batching.py`
- Modify: `backend/app/optimization_automation.py`
- Modify: `backend/app/worker.py`（只增加批次状态检查点和零点击唤醒，不改变 3D/SU 处理语义）
- Create: `backend/tests/test_automation_batching.py`
- Extend: `backend/tests/test_phase_b_automation.py`

**Interfaces:**
- `BatchCohort`：`lane_key`、`category_key`、`pipeline_kind`、`generation`、`mechanism_fingerprint`、`route_key`、`case_ids`。
- `select_ready_cohort(db: Session, *, now: datetime) -> tuple[BatchCohort | None, list[dict[str, Any]]]`
- `create_or_get_batch(db: Session, *, cohort: BatchCohort, trigger_reason: str, now: datetime) -> AutomationBatch`
- `dispatch_next_lane(db: Session, *, global_policy: AutomationPolicy, worker_id: str, now: datetime) -> AutomationBatch | None`
- `recover_expired_batch_leases(db: Session, *, now: datetime) -> int`

- [ ] **Step 1: 写失败测试，验证不混批且阻塞隔离**

```python
def test_cohort_never_mixes_pipeline_generation_or_mechanism(db):
    _eligible(db, category="space_image", pipeline="incremental", generation=2, fingerprint="a" * 64)
    _eligible(db, category="space_image", pipeline="baseline", generation=2, fingerprint="a" * 64)
    _eligible(db, category="space_image", pipeline="incremental", generation=3, fingerprint="a" * 64)
    cohort, _ = select_ready_cohort(db, now=NOW)
    assert cohort.pipeline_kind == "incremental"
    assert cohort.generation == 2

def test_blocked_category_does_not_starve_other_lane(db):
    _blocked_lane(db, "space_image")
    _ready_lane(db, "material_image")
    batch = dispatch_next_lane(db, global_policy=_policy(), worker_id="w1", now=NOW)
    assert batch.category_key == "material_image"
```

Run: `python3 -m pytest -q backend/tests/test_automation_batching.py -k "mixes or starve"`
Expected: FAIL because the existing selector only separates category and prompt version.

- [ ] **Step 2: 以 lane_key 扩展现有消费者**

把 `optimization_automation._select_ready_case_cohort`、`consume_optimization_queue_once` 和 `_fair_category_order` 改为使用 `BatchCohort`；阈值、最长等待、严重度即时触发、预算和最大连续占用均从 `AutomationLanePolicy` 的冻结快照读取。批次创建使用 `sha256(canonical_json(lane_key + sorted(case_ids)))`，重复调用只返回已有批次。技术失败只写 `AutomationBatch.error_code/error_message` 并按租约/重试恢复，业务失败进入候选风险，不伪造成功。

- [ ] **Step 3: 加入 worker 检查点、租约回收和提交**

```bash
python3 -m pytest -q backend/tests/test_automation_batching.py backend/tests/test_phase_b_automation.py -k "category or lease or round_robin or automation"
git add backend/app/automation_batching.py backend/app/optimization_automation.py backend/app/worker.py backend/tests/test_automation_batching.py backend/tests/test_phase_b_automation.py
git commit -m "feat: batch automation by isolated category lanes"
```

### Task 5: 运营理由/证据驱动的调用甲、调用乙、第三版规则路由

**Files:**
- Create: `backend/app/automation_routing.py`
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/baseline_regression.py`
- Create: `backend/tests/test_automation_routing.py`
- Extend: `backend/tests/test_baseline_correction_human_evidence.py`

**Interfaces:**
- `RouteLayer = Literal["A", "B", "V3"]`
- `route_correction_evidence(evidence: FinalCorrectionEvidence) -> RouteDecision`
- `RouteDecision`：`layers`、`route_key`、`reason_codes`、`evidence_paths`、`dependency_order`、`confidence`。
- `validate_route_against_frozen_mechanism(decision: RouteDecision, mechanism_snapshot: Mapping[str, Any]) -> None`

- [ ] **Step 1: 写失败测试，固定四类路由**

```python
def test_route_scope_fact_quality_media_and_hard_defect_to_call_a():
    decision = route_correction_evidence(_evidence(node_type="call_a_field", reason="是截图且有大面积文字"))
    assert decision.layers == ("A",)

def test_route_aesthetic_evidence_to_call_b():
    decision = route_correction_evidence(_evidence(node_type="dimension_rule", reason="构图平衡度被低估", evidence=[{"path": "dimensions.balance"}]))
    assert decision.layers == ("B",)

def test_route_threshold_cap_and_level_mapping_to_v3():
    decision = route_correction_evidence(_evidence(node_type="final_level", reason="89分应升入L1"))
    assert decision.layers == ("V3",)

def test_multi_node_correction_produces_one_ordered_combined_route():
    decision = route_correction_evidence(_evidence(node_types=["call_a_field", "dimension_rule", "final_level"]))
    assert decision.layers == ("A", "B", "V3")
    assert decision.dependency_order == ("A", "B", "V3")
```

Run: `python3 -m pytest -q backend/tests/test_automation_routing.py -k "route"`
Expected: FAIL because route decisions are currently derived only inside candidate orchestration and do not emit a first-class frozen object.

- [ ] **Step 2: 实现显式路由并阻止证据冲突**

按节点类型、结构化理由和证据路径匹配规则；最终等级差异本身不能生成路由。路由结果写入候选冻结输入，候选生成器只能修改命中的层；不符合机制指纹、缺证据或路由与人工节点冲突时返回 `route_conflict`，不得调用模型。

- [ ] **Step 3: 运行专项测试并提交**

```bash
python3 -m pytest -q backend/tests/test_automation_routing.py backend/tests/test_baseline_correction_human_evidence.py
git add backend/app/automation_routing.py backend/app/baseline_correction_orchestration.py backend/app/baseline_regression.py backend/tests/test_automation_routing.py backend/tests/test_baseline_correction_human_evidence.py
git commit -m "feat: route correction evidence to mechanism layers"
```

### Task 6: 不可变候选包与三角色配对回归自动推进

**Files:**
- Create: `backend/app/automation_candidate_pipeline.py`
- Modify: `backend/app/optimizer.py`
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/regression.py`
- Modify: `backend/app/models.py`（增加 `AutomationCandidatePackage`，保存候选差异、路由、风险、三角色回归号和决策状态）
- Create: `backend/tests/test_automation_candidate_pipeline.py`
- Extend: `backend/tests/test_paired_regression.py`

**Interfaces:**
- `materialize_candidate_package(db: Session, *, batch: AutomationBatch, generated: Mapping[str, Any], route: RouteDecision, actor: str) -> AutomationCandidatePackage`
- `create_three_role_regressions(db: Session, *, package: AutomationCandidatePackage, lane: AutomationLanePolicy) -> tuple[PromptRegressionRun, ...]`
- `refresh_candidate_package(db: Session, *, package_id: int) -> AutomationCandidatePackage`
- `candidate_package_decision(db: Session, *, package_id: int, decision: Literal["approved", "rejected"], actor: str, note: str) -> AutomationCandidatePackage`

- [ ] **Step 1: 写失败测试，保证候选不可变、回归三角色齐全、采用前不改变现役**

```python
def test_candidate_package_freezes_route_diff_and_regression_roles(db):
    package = _materialize_package(db)
    runs = create_three_role_regressions(db, package=package, lane=_lane(db))
    assert len(runs) == 1
    assert {item.sample_role for item in runs[0].items} == {"target_error", "stable_control", "blind_holdout"}
    assert package.approval_status == "pending"

def test_candidate_approval_requires_all_regression_gates_and_does_not_publish(db):
    package = _materialize_package(db)
    with pytest.raises(ValueError, match="回归未完成"):
        candidate_package_decision(db, package_id=package.id, decision="approved", actor="admin", note="采用")
    assert _active_mechanism(db).id == package.baseline_mechanism_id
```

Run: `python3 -m pytest -q backend/tests/test_automation_candidate_pipeline.py -k "candidate or approval"`
Expected: FAIL because candidate package and automatic paired-run creation are absent.

- [ ] **Step 2: 接通已有三角色配对回归**

使用 `PromptRegressionRun.regression_mode="paired"`、`reviewed_truth_snapshot`、`compare_paired_results` 和 `refresh_paired_regression_run`；同一候选只能创建一组冻结回归。盲测保留未完成前不揭示结果。技术错误项保留 `error_code` 和脱敏信息，不计入业务准召；业务门槛不达标则候选 `rejected_by_gate`，不自动重试成成功。

- [ ] **Step 3: 运行回归专项并提交**

```bash
python3 -m pytest -q backend/tests/test_automation_candidate_pipeline.py backend/tests/test_paired_regression.py -k "paired or blind or approval or immutable"
git add backend/app/automation_candidate_pipeline.py backend/app/optimizer.py backend/app/baseline_correction_orchestration.py backend/app/regression.py backend/app/models.py backend/tests/test_automation_candidate_pipeline.py backend/tests/test_paired_regression.py
git commit -m "feat: generate immutable candidates with paired regressions"
```

### Task 7: 常驻工作进程零点击推进、技术失败恢复与二审状态机

**Files:**
- Modify: `backend/app/optimization_automation.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/regression.py`
- Create: `backend/tests/test_automation_zero_click_flow.py`
- Extend: `backend/tests/test_phase_b_automation.py`

**Interfaces:**
- `advance_automation_batch(db: Session, *, batch_id: int, worker_id: str, now: datetime) -> dict[str, Any]`
- `reconcile_automation_batch(db: Session, *, batch_id: int) -> dict[str, Any]`
- `retry_technical_failure(db: Session, *, batch_id: int, actor: str) -> AutomationBatch`
- `assert_no_publish_side_effects(db: Session, *, before: Mapping[str, Any], after: Mapping[str, Any]) -> None`

- [ ] **Step 1: 写失败测试，最终纠偏后无需人工点击即可到候选二审**

```python
def test_worker_tick_advances_final_correction_to_review_without_manual_consume(db):
    _finalized_case(db)
    result = optimization_worker_tick("worker-1", db=db)
    db.commit()
    assert result["status"] in {"planned", "processing", "awaiting_release_review"}
    assert _latest_candidate(db).approval_status == "pending"

def test_technical_failure_retries_but_business_failure_does_not(db):
    technical = _batch_with_failure(db, error_code="timeout")
    business = _batch_with_failure(db, error_code="target_error_not_improved")
    retry_technical_failure(db, batch_id=technical.id, actor="worker-1")
    assert db.get(AutomationBatch, technical.id).status == "queued"
    assert db.get(AutomationBatch, business.id).status == "failed"
```

Run: `python3 -m pytest -q backend/tests/test_automation_zero_click_flow.py -k "worker or failure"`
Expected: FAIL because the worker currently only consumes the old queue shape and does not progress lane batches end-to-end.

- [ ] **Step 2: 将检查点接入已有常驻 worker**

在 `optimization_worker_tick` 现有租约循环内依次执行“资格快照 → 组批 → 路由 → 候选 → 三角色回归 → 二审状态刷新”；不新增第二个常驻进程。`timeout`、限流、结构解析失败等技术错误按现有重试/租约回收；准召、覆盖率和业务护栏失败只进入候选风险/失败状态。每个阶段在短事务中写检查点，模型调用不持有数据库事务。

- [ ] **Step 3: 运行专项、全量自动化测试并提交**

```bash
python3 -m pytest -q backend/tests/test_automation_zero_click_flow.py backend/tests/test_phase_b_automation.py
git add backend/app/optimization_automation.py backend/app/worker.py backend/app/regression.py backend/tests/test_automation_zero_click_flow.py backend/tests/test_phase_b_automation.py
git commit -m "feat: advance automation batches without operator clicks"
```

### Task 8: 后端接口与全局/泳道/历史/候选二审证据面

**Files:**
- Create: `backend/app/automation_api.py`
- Modify: `backend/app/main.py`（只增加 router 注册和兼容旧接口的委托）
- Create: `backend/tests/test_automation_api.py`

**Interfaces:**
- `build_automation_router(current_user: Callable[..., Any], admin_user: Callable[..., Any]) -> APIRouter`
- `GET /api/automation/overview`：总预算、总并发、活动泳道、阻塞泳道、历史审计数、运行批次、待二审候选、最近失败。
- `GET /api/automation/lanes?pipeline_kind=incremental|baseline`：每条泳道的启用代次、下一批门槛、黄金集就绪、预算、阻塞原因。
- `GET /api/automation/batches/{batch_id}`：冻结组批键、案例证据、技术失败、路由、候选和回归进度。
- `GET /api/automation/historical-audit` 与 `POST /api/automation/historical-audit/{case_id}/admit`：历史审计和人工纳入；不得提供批量自动纳入。
- `GET /api/automation/candidates` 与 `POST /api/automation/candidates/{candidate_id}/decision`：统一展示候选差异、路由、三角色指标、风险和采用/拒绝；采用接口只改变候选决策，不发布标签或重跑存量。

- [ ] **Step 1: 写失败测试，验证接口不泄露密钥并保留发布门禁**

```python
def test_automation_overview_separates_lanes_and_history(client, auth_headers):
    response = client.get("/api/automation/overview", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "lanes" in body and "historical_audit" in body
    assert body["auto_publish_enabled"] is False

def test_candidate_decision_does_not_publish_or_rerun_stock(client, auth_headers):
    response = client.post("/api/automation/candidates/1/decision", headers=auth_headers,
                           json={"decision": "approved", "note": "采用"})
    assert response.status_code in {200, 409}
    assert client.get("/api/automation/overview", headers=auth_headers).json()["auto_publish_enabled"] is False
```

Run: `python3 -m pytest -q backend/tests/test_automation_api.py`
Expected: FAIL because the router and endpoints do not exist.

- [ ] **Step 2: 实现只读总览和双人工门**

将现有 `/api/automation-policy`、`/api/automation-runs`、`/api/optimization-cases` 作为兼容旧客户端保留，新 router 返回泳道/链路/代次/机制指纹和技术失败分层证据。所有写接口依赖 `admin_user`；候选采用前强制检查三角色回归、风险门和人工身份，写 `decision` 审计事件但不触碰 `MechanismRelease`、标签事实或存量任务。

- [ ] **Step 3: 运行 API 专项和提交**

```bash
python3 -m pytest -q backend/tests/test_automation_api.py backend/tests/test_phase_b_automation.py -k "api or publish or policy"
git add backend/app/automation_api.py backend/app/main.py backend/tests/test_automation_api.py
git commit -m "feat: expose lane automation evidence APIs"
```

### Task 9: 前端全局总览、类目泳道、历史审计和统一候选二审

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/pages/automation-overview-page.tsx`
- Create: `frontend/src/pages/automation-lanes-page.tsx`
- Create: `frontend/src/pages/automation-candidate-review-page.tsx`
- Modify: `frontend/src/pages/workflow-pages.tsx`（保留旧入口，链接到新证据页）
- Modify: `frontend/src/pages/operations-center-page.tsx`（增加全局自动组批摘要，不改变运行中心既有队列语义）
- Modify: `frontend/src/App.tsx`
- Create: `frontend/scripts/check-global-automation-contract.ts`

**Interfaces:**
- TypeScript 类型 `AutomationOverview`、`AutomationLaneSummary`、`AutomationBatchDetail`、`AutomationCandidateReview` 与后端字段一一对应。
- `automationApi.getOverview()`、`automationApi.listLanes(pipelineKind)`、`automationApi.getBatch(id)`、`automationApi.listHistoricalAudit()`、`automationApi.admitHistorical(id, reason)`、`automationApi.listCandidates()`、`automationApi.decideCandidate(id, payload)`。

- [ ] **Step 1: 写前端合同检查**

```ts
const source = read("src/pages/automation-overview-page.tsx")
assert(source.includes("总预算") && source.includes("历史审计"))
assert(source.includes("自动发布已关闭"))
assert(read("src/pages/automation-candidate-review-page.tsx").includes("目标错例"))
```

Run: `cd frontend && node --experimental-strip-types scripts/check-global-automation-contract.ts`
Expected: FAIL because the pages and API types do not exist.

- [ ] **Step 2: 实现四个证据视图**

全局总览只显示总预算/并发、活动和阻塞泳道、历史审计、运行批次、待二审候选和最近失败；泳道页按增量/存量分栏显示门槛、代次、机制指纹前 12 位、黄金集、预算和阻塞；历史审计页只能逐条人工纳入；候选二审页同屏展示运营理由/证据、A/B/V3 路由、目标错例/稳定对照/盲测指标、技术失败和采用/拒绝按钮。页面显式展示“自动发布已关闭”，不提供自动启用或存量重跑按钮。

- [ ] **Step 3: 运行合同、类型、构建并提交**

```bash
cd frontend
node --experimental-strip-types scripts/check-global-automation-contract.ts
npm run lint
npm run build
cd ..
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/pages/automation-overview-page.tsx frontend/src/pages/automation-lanes-page.tsx frontend/src/pages/automation-candidate-review-page.tsx frontend/src/pages/workflow-pages.tsx frontend/src/pages/operations-center-page.tsx frontend/src/App.tsx frontend/scripts/check-global-automation-contract.ts
git commit -m "feat: add global automation evidence workbench"
```

### Task 10: 灵感图五档准召与三档兜底指标

**Files:**
- Create: `backend/app/inspiration_quality_metrics.py`
- Modify: `backend/app/inspiration_category_seed.py`
- Modify: `backend/app/baseline_regression.py`
- Modify: `backend/app/automation_candidate_pipeline.py`
- Create: `backend/tests/test_inspiration_quality_metrics.py`
- Modify: `frontend/src/features/baseline-regression/level-metrics.ts`
- Modify: `frontend/src/features/baseline-regression/level-performance-summary.tsx`
- Create: `frontend/scripts/check-inspiration-quality-contract.ts`

**Interfaces:**
- `compute_inspiration_quality_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]`
- `recommendation_metrics(rows)`: 第一档/第二档合并为 `recommendation`，同时输出五档矩阵。
- `three_bucket_fallback_metrics(rows)`: `recommendation=L1|L2`、`ordinary=L3|L4`、`filter=L5`，输出精确率、召回率和样本占比。
- `quality_gate(metrics) -> list[dict[str, Any]]`：强制推荐档占比 `<= 0.35`，推荐档/第三档/第四档/第五档精确率与召回率 `>= 0.80`，并分别标记第一档误升和第五档漏判。

- [ ] **Step 1: 写失败测试，固定五档与三档口径**

```python
def test_recommendation_merges_l1_l2_and_three_bucket_fallback():
    metrics = compute_inspiration_quality_metrics([
        {"truth": "L1", "pred": "L2"}, {"truth": "L2", "pred": "L1"},
        {"truth": "L3", "pred": "L4"}, {"truth": "L5", "pred": "L4"},
    ])
    assert metrics["recommendation"]["denominator"] == 2
    assert metrics["three_bucket"]["filter"]["recall"] == 0.0
    assert metrics["diagnostics"]["l1_overpromotion_cost"] > 0

def test_quality_gate_rejects_recommendation_share_over_thirty_five_percent():
    metrics = _metrics_with_recommendation_share(0.36)
    failures = quality_gate(metrics)
    assert any(item["gate"] == "recommendation_share" for item in failures)
```

Run: `python3 -m pytest -q backend/tests/test_inspiration_quality_metrics.py`
Expected: FAIL because current level metrics do not expose merged recommendation, three-bucket fallback, or asymmetric business costs.

- [ ] **Step 2: 接入灵感图候选门槛和报告**

候选回归必须同时保存五档混淆矩阵、精确等级命中、相邻等级命中、三档兜底口径、技术覆盖率、疑难案例（L1/L2 边界、L5 正例、L5 困难负例）。第一档误升单独计数并进入精确率护栏；第五档漏判单独计数并进入召回率护栏；不把相邻命中直接替代精确率/召回率。

- [ ] **Step 3: 运行后端/前端合同并提交**

```bash
python3 -m pytest -q backend/tests/test_inspiration_quality_metrics.py backend/tests/test_baseline_regression.py -k "metric or level"
cd frontend && node --experimental-strip-types scripts/check-inspiration-quality-contract.ts && npm run lint && npm run build
cd ..
git add backend/app/inspiration_quality_metrics.py backend/app/inspiration_category_seed.py backend/app/baseline_regression.py backend/app/automation_candidate_pipeline.py backend/tests/test_inspiration_quality_metrics.py frontend/src/features/baseline-regression/level-metrics.ts frontend/src/features/baseline-regression/level-performance-summary.tsx frontend/scripts/check-inspiration-quality-contract.ts
git commit -m "feat: add five-level and fallback inspiration quality gates"
```

### Task 11: 全局配置、失败矩阵、浏览器和联合回归验收

**Files:**
- Create: `backend/tests/test_global_automation_end_to_end.py`
- Modify: `backend/tests/test_automation_fault_matrix.py`
- Modify: `backend/tests/test_migration.py`
- Modify: `frontend/scripts/check-global-automation-contract.ts`
- Create: `docs/superpowers/receipts/2026-08-17-global-automation-local-verification.md`
- Create: `docs/superpowers/receipts/2026-08-17-global-automation-package-ledger.md`

**Interfaces:**
- `seed_all_active_category_lanes(db: Session, *, enabled_at: datetime) -> list[AutomationLanePolicy]`
- `assert_global_automation_invariants(db: Session) -> None`
- 验收输出必须包含：类目泳道数、增量/存量隔离数、历史审计数、混批数、候选数、技术失败/业务失败数、自动发布/存量重跑计数。

- [ ] **Step 1: 写端到端失败测试**

```python
def test_all_active_categories_progress_independently_without_publish(db):
    seed_all_active_category_lanes(db, enabled_at=NOW)
    _finalize_cases_for_all_categories(db)
    first = optimization_worker_tick("worker-e2e", db=db)
    assert first["status"] != "global_blocked"
    assert db.scalar(select(AutomationBatch).where(AutomationBatch.pipeline_kind == "incremental")) is not None
    assert db.scalar(select(MechanismRelease.id)) is None
    assert _count_stock_reruns(db) == 0

def test_end_to_end_invariants_hold_after_restart(db):
    optimization_worker_tick("worker-e2e", db=db)
    optimization_worker_tick("worker-e2e-restarted", db=db)
    assert_global_automation_invariants(db)
```

Run: `python3 -m pytest -q backend/tests/test_global_automation_end_to_end.py`
Expected: FAIL until all previous tasks are integrated.

- [ ] **Step 2: 运行联合本地验收**

```bash
python3 -m pytest -q backend/tests/test_automation_lanes.py backend/tests/test_automation_history_isolation.py backend/tests/test_automation_case_intake.py backend/tests/test_automation_batching.py backend/tests/test_automation_routing.py backend/tests/test_automation_candidate_pipeline.py backend/tests/test_automation_zero_click_flow.py backend/tests/test_automation_api.py backend/tests/test_inspiration_quality_metrics.py backend/tests/test_global_automation_end_to_end.py
python3 -m pytest -q
cd frontend && npm run lint && npm run build && node --experimental-strip-types scripts/check-global-automation-contract.ts && node --experimental-strip-types scripts/check-inspiration-quality-contract.ts
```

验收硬门：历史约 2,286 条自动消费数为 0；任何批次都不存在跨类目/链路/代次/机制指纹混批；技术失败与业务失败可分开计数；人工采用前现役机制指针、标签事实和存量任务无变化；SQLite `PRAGMA integrity_check` 返回 `ok`、`PRAGMA foreign_key_check` 返回空集、迁移重复执行不改变结果。

- [ ] **Step 3: 完成浏览器合同和本地回执**

使用本地浏览器验证全局总览、泳道详情、历史审计逐条纳入、候选二审、错误状态和“自动发布已关闭”文案；不点击真实启用、采用、存量重跑或模型调用。将命令输出、测试数量、数据库完整性、浏览器截图路径和停止条件写入两份回执。

```bash
git status --short --branch
git log --oneline --decorate -12
git add backend/tests frontend/scripts docs/superpowers/receipts
git commit -m "test: verify global automation closure locally"
```

## Self-review checklist

- 规格覆盖：数据合同/历史隔离在 Task 1-2；零点击入队在 Task 3/7；全局调度和泳道在 Task 4；A/B/V3 路由在 Task 5；不可变候选和三角色回归在 Task 6；接口与工作台在 Task 8-9；灵感图五档与三档兜底在 Task 10；合流、迁移、全量和浏览器验收在 Pre-execution Gate/Task 11。
- 占位符扫描：计划没有未定义的实现占位；未确定的真实预算和部署仅作为明确停止条件，不伪造配置。
- 类型一致性：`AutomationLanePolicy → OptimizationCaseEligibilitySnapshot → AutomationBatch → AutomationCandidatePackage → PromptRegressionRun` 的字段名和接口签名在各任务中保持一致；`RouteDecision.layers` 固定为 `A/B/V3`，`pipeline_kind` 固定为 `incremental/baseline`。
- 兼容性：旧 `/api/automation-policy`、`/api/automation-runs` 和 `OptimizationCaseQueue` 保留；新接口通过独立 router 增量接入；3D/SU dry-run 冲突文件只在唯一合流基线处理。
- 安全性：未授权动作（推送、合并、部署、真实模型调用、自动采用、存量重跑、标签发布）均没有计划命令。
