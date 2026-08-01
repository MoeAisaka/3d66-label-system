from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.benchmarking import (
    OpenAICompatibleBenchmarkAdapter,
    calculate_variant_metrics,
    run_benchmark_experiment,
    select_benchmark_candidate,
    snapshot_hash,
)
from app.doubao import DoubaoResponse
from app.database import Base, get_db
from app import (
    benchmarking,
    main,
    optimizer,
    optimization_automation,
    regression as regression_module,
)
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    AgentPlanVersion,
    Asset,
    AuditEvent,
    AutomationBudgetDay,
    AutomationOptimizationRun,
    AutomationPolicy,
    AutomationWorkerStatus,
    EvaluationCategoryProfile,
    ModelBenchmarkExperiment,
    ModelBenchmarkVariant,
    ModelConfig,
    OptimizationCaseQueue,
    ProductionFeedbackEvent,
    PromptVersion,
    PromptRegressionRun,
    SampleSet,
    SamplingPolicy,
    User,
)
from app.optimization_automation import (
    AutomationAdapterResult,
    DeterministicOptimizationAdapter,
    RealOptimizationAdapter,
    automation_runtime_status,
    consume_optimization_queue_once,
    optimization_worker_tick,
    record_automation_worker_status,
    touch_automation_worker_status,
)
from app.regression import (
    _refresh_source_automation_review,
    reconcile_automation_review_states,
    refresh_paired_regression_run,
)
from app.production_feedback import (
    FeedbackConflict,
    ingest_production_feedback,
)
from app.strategy_bundle import get_or_create_bundle


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="phase-b-owner",
        password_hash="unused",
        display_name="Phase B",
    )
    db.add(user)
    db.commit()
    return engine, db, user


def _close(engine, db) -> None:
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _feedback(
    db: Session,
    *,
    event_id: str,
    severity: str = "P2",
    prompt_version: str = "prompt-b-v1",
    occurred_at: datetime | None = None,
):
    return ingest_production_feedback(
        db,
        event_id=event_id,
        schema_version="production-feedback-v1",
        event_type="human_correction_finalized",
        source_system="production-labels",
        occurred_at=occurred_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={
            "production_case_id": f"case-{event_id}",
            "category_key": "space_image",
            "prompt_version": prompt_version,
            "severity": severity,
            "model_output": {"level": "L4"},
            "human_truth": {"level": "L2"},
            "reason_codes": ["over_scored"],
            "production_applied": True,
        },
        received_by="phase-b-owner",
    )


def test_production_feedback_is_idempotent_and_immutable() -> None:
    engine, db, _user = _database()
    try:
        event, case, duplicate = _feedback(db, event_id="event-1")
        db.commit()
        assert duplicate is False
        same_event, same_case, duplicate = _feedback(
            db, event_id="event-1"
        )
        assert duplicate is True
        assert same_event.id == event.id
        assert same_case.id == case.id
        assert same_case.source_type == "production_feedback"
        assert same_case.evaluation_id is None

        with pytest.raises(FeedbackConflict):
            ingest_production_feedback(
                db,
                event_id="event-1",
                schema_version="production-feedback-v1",
                event_type="human_correction_finalized",
                source_system="production-labels",
                occurred_at=datetime.now(timezone.utc),
                payload={
                    "production_case_id": "different",
                    "category_key": "space_image",
                    "prompt_version": "prompt-b-v1",
                    "severity": "P1",
                    "model_output": {},
                    "human_truth": {},
                },
                received_by="phase-b-owner",
            )

        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "UPDATE production_feedback_events "
                    "SET source_system='tampered' WHERE id=:id"
                ),
                {"id": event.id},
            )
            db.commit()
        db.rollback()
        assert (
            db.scalar(select(ProductionFeedbackEvent).where(
                ProductionFeedbackEvent.event_id == "event-1"
            )).source_system
            == "production-labels"
        )
    finally:
        _close(engine, db)


def test_feedback_api_requires_machine_token_and_preserves_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, db, user = _database()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        occurred_at = datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat()
        valid_payload = {
            "event_id": "event-api",
            "schema_version": "production-feedback-v1",
            "event_type": "human_correction_finalized",
            "source_system": "production",
            "occurred_at": occurred_at,
            "payload": {
                "production_case_id": "prod-1",
                "category_key": "space_image",
                "prompt_version": "v1",
                "severity": "P1",
                "model_output": {"level": "L4"},
                "human_truth": {"level": "L2"},
            },
        }
        monkeypatch.setattr(
            main,
            "settings",
            replace(main.settings, production_feedback_token=None),
        )
        unconfigured = client.post(
            "/api/production-feedback-events", json=valid_payload
        )
        assert unconfigured.status_code == 503

        token = "test-only-feedback-token"
        monkeypatch.setattr(
            main,
            "settings",
            replace(main.settings, production_feedback_token=token),
        )
        assert client.post(
            "/api/production-feedback-events", json=valid_payload
        ).status_code == 401
        assert client.post(
            "/api/production-feedback-events",
            headers={"Authorization": "Bearer wrong-test-token"},
            json=valid_payload,
        ).status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        malformed = client.post(
            "/api/production-feedback-events",
            headers=headers,
            json={
                "event_id": "event-api",
                "schema_version": "production-feedback-v1",
                "event_type": "human_correction_finalized",
                "source_system": "production",
                "occurred_at": occurred_at,
                "payload": {"prompt_version": "v1"},
            },
        )
        assert malformed.status_code == 422

        first = client.post(
            "/api/production-feedback-events", headers=headers, json=valid_payload
        )
        assert first.status_code == 200
        assert first.json()["duplicate"] is False
        second = client.post(
            "/api/production-feedback-events", headers=headers, json=valid_payload
        )
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        changed = dict(valid_payload)
        changed["payload"] = dict(valid_payload["payload"], severity="P0")
        conflict = client.post(
            "/api/production-feedback-events", headers=headers, json=changed
        )
        assert conflict.status_code == 409
        serialized = json.dumps(first.json(), ensure_ascii=False)
        assert token not in serialized
        assert "authorization" not in serialized.casefold()
    finally:
        _close(engine, db)


def test_automation_defaults_disabled_then_dry_run_plans_without_model() -> None:
    engine, db, _user = _database()
    try:
        policy = AutomationPolicy(id=1)
        db.add(policy)
        _feedback(db, event_id="dry-1")
        _feedback(db, event_id="dry-2")
        db.commit()

        disabled = consume_optimization_queue_once(
            db, worker_id="worker-1"
        )
        assert disabled["status"] == "disabled"
        assert db.scalar(select(AutomationOptimizationRun.id)) is None

        policy.enabled = True
        policy.dry_run = True
        policy.case_threshold = 2
        db.commit()
        planned = consume_optimization_queue_once(
            db, worker_id="worker-1"
        )
        db.commit()
        assert planned["status"] == "planned"
        run = db.get(AutomationOptimizationRun, planned["run_id"])
        assert run is not None
        assert run.dry_run is True
        assert run.actual_cost_micros == 0
        assert run.result_json == "{}"
        statuses = set(db.scalars(select(OptimizationCaseQueue.status)))
        # The category profile owns its own automation threshold. The seeded
        # profile defaults to one case, so one case is consumed and the next
        # remains pending for the next worker tick.
        assert statuses == {"batched", "pending"}
    finally:
        _close(engine, db)


def test_automation_review_waits_for_all_candidate_regressions() -> None:
    engine, db, _user = _database()
    try:
        source = AutomationOptimizationRun(
            run_key="aggregate-all-candidates",
            base_prompt_version="B-base",
            category_key="space_image",
            policy_revision=1,
            status="succeeded",
            trigger_reason="case_threshold",
            case_ids_json="[]",
            frozen_input_json='{"category_key":"space_image"}',
            result_json="{}",
        )
        db.add(source)
        db.flush()
        candidate_a = PromptVersion(
            stage="B", name="candidate-a", version="auto-a",
            system_prompt="system prompt candidate a", user_prompt="user prompt",
            source_automation_run_id=source.id,
        )
        candidate_b = PromptVersion(
            stage="B", name="candidate-b", version="auto-b",
            system_prompt="system prompt candidate b", user_prompt="user prompt",
            source_automation_run_id=source.id,
        )
        db.add_all([candidate_a, candidate_b])
        db.flush()
        base_a = PromptVersion(
            stage="A", name="base-a", version="auto-base-a",
            system_prompt="system prompt base a", user_prompt="user prompt",
        )
        sample_set = SampleSet(
            name="automation-review-golden", kind="golden", status="locked",
            category_key="space_image",
        )
        db.add_all([base_a, sample_set])
        db.flush()
        first = PromptRegressionRun(
            name="first", sample_set_id=sample_set.id, trigger_prompt_id=candidate_a.id,
            prompt_a_id=base_a.id, prompt_b_id=candidate_a.id,
        )
        second = PromptRegressionRun(
            name="second", sample_set_id=sample_set.id, trigger_prompt_id=candidate_b.id,
            prompt_a_id=base_a.id, prompt_b_id=candidate_b.id,
        )
        db.add_all([first, second])
        db.flush()
        source.result_json = json.dumps({"regression_ids": [first.id, second.id]})
        first.status = "passed"
        _refresh_source_automation_review(db, first)
        assert source.status == "running"
        second.status = "regressed"
        second.finished_at = datetime.now(timezone.utc)
        _refresh_source_automation_review(db, second)
        assert source.status == "awaiting_release_review"

        source.status = "succeeded"
        source.finished_at = None
        assert reconcile_automation_review_states(db) == 1
        assert source.status == "awaiting_release_review"
        assert source.finished_at == second.finished_at
    finally:
        _close(engine, db)


def test_terminal_paired_regression_advances_source_automation(
    monkeypatch,
) -> None:
    assessment = {
        "aesthetic_correct": 8,
        "aesthetic_checked": 8,
        "whole_image_correct": True,
        "level_consistent": True,
    }
    comparison = {
        "baseline": {"assessment": assessment},
        "candidate": {"assessment": assessment},
        "target_error_improved": None,
        "critical_regressions": [],
        "new_severe_errors": [],
    }
    item = SimpleNamespace(
        id=1,
        sample_role="stable_control",
        status="completed",
        passed=True,
        comparison_json=json.dumps(comparison),
    )
    run = SimpleNamespace(
        id=1,
        regression_mode="paired",
        sample_set_version="a" * 64,
        metric_rules_version="paired-v1",
        metric_rules_json=json.dumps(
            {
                "thresholds": {
                    "aesthetic_accuracy_max_drop": 0.0,
                    "whole_image_accuracy_max_drop": 0.0,
                    "level_consistency_max_drop": 0.0,
                }
            }
        ),
        total=0,
        completed=0,
        passed=0,
        failed=0,
        recommendation="pending",
        status="waiting_results",
        summary_json="{}",
        metrics_json="{}",
        finished_at=None,
    )

    class ScalarRows:
        def all(self):
            return [item]

    class FakeDb:
        def scalars(self, _query):
            return ScalarRows()

    advanced: list[object] = []
    monkeypatch.setattr(
        regression_module,
        "_refresh_source_automation_review",
        lambda _db, completed: advanced.append(completed),
    )

    refresh_paired_regression_run(FakeDb(), run)

    assert run.status == "passed"
    assert run.recommendation == "pass"
    assert advanced == [run]


def test_p0_triggers_immediately_and_live_budget_blocks_before_adapter() -> None:
    class CountingAdapter:
        calls = 0

        def estimate_cost_micros(self, *, frozen_input):
            del frozen_input
            return 2500

        def optimize(self, *, frozen_input, max_candidates):
            del frozen_input, max_candidates
            self.calls += 1
            raise AssertionError("预算拦截后不应调用执行器")

    engine, db, _user = _database()
    try:
        policy = AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=True,
            case_threshold=100,
        )
        db.add(policy)
        _feedback(db, event_id="urgent", severity="P0")
        db.commit()
        immediate = consume_optimization_queue_once(
            db, worker_id="worker-p0"
        )
        db.commit()
        assert immediate["status"] == "planned"
        assert db.get(
            AutomationOptimizationRun, immediate["run_id"]
        ).trigger_reason == "immediate:P0"

        _feedback(db, event_id="budget", severity="P0")
        policy.dry_run = False
        policy.daily_budget_micros = 500
        db.commit()
        blocked = consume_optimization_queue_once(
            db, worker_id="worker-budget", adapter=CountingAdapter()
        )
        db.commit()
        assert blocked["status"] == "budget_blocked"
        budget_case = db.scalar(
            select(OptimizationCaseQueue).where(
                OptimizationCaseQueue.idempotency_key
                == "production:budget"
            )
        )
        assert budget_case.status == "pending"
        assert budget_case.attempt_count == 0
        assert CountingAdapter.calls == 0
    finally:
        _close(engine, db)


def test_live_execution_without_optimizer_config_does_not_claim_case() -> None:
    engine, db, _user = _database()
    try:
        db.add(AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=False,
            case_threshold=1,
            daily_budget_micros=100_000,
        ))
        _feedback(db, event_id="missing-optimizer")
        db.commit()

        result = consume_optimization_queue_once(db, worker_id="worker-missing")
        db.commit()

        assert result["status"] == "executor_config_blocked"
        assert result["reason"] == "optimizer_config_incomplete"
        case = db.scalar(select(OptimizationCaseQueue))
        assert case.status == "pending"
        assert case.attempt_count == 0
        assert db.scalar(select(AutomationOptimizationRun.id)) is None
    finally:
        _close(engine, db)


def test_runtime_status_explains_worker_queue_and_configuration_gates() -> None:
    engine, db, _user = _database()
    try:
        policy = AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=False,
            case_threshold=2,
            daily_budget_micros=100_000,
        )
        db.add(policy)
        _feedback(db, event_id="runtime-status")
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        profile.automation_config_json = json.dumps({
            "enabled": True,
            "case_threshold": 2,
            "max_candidates": 1,
        })
        db.commit()

        before = automation_runtime_status(db, policy)
        assert before["status"] == "blocked"
        assert before["worker"]["active_worker_count"] == 0
        assert before["queue"]["available_for_prompt"] == 1
        assert before["queue"]["required_for_prompt"] == 2
        assert {item["code"] for item in before["blockers"]} == {
            "worker_not_seen",
            "threshold_wait",
            "optimizer_config_incomplete",
        }

        record_automation_worker_status(
            db,
            worker_id="worker-runtime",
            status="threshold_wait",
            result={"status": "threshold_wait"},
        )
        db.commit()
        after = automation_runtime_status(db, policy)
        assert after["worker"]["active_worker_count"] == 1
        assert db.get(AutomationWorkerStatus, "worker-runtime").last_status == "threshold_wait"
    finally:
        _close(engine, db)


def test_dry_run_runtime_reports_missing_optimizer_as_warning() -> None:
    engine, db, _user = _database()
    try:
        policy = AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=True,
            case_threshold=1,
        )
        db.add(policy)
        _feedback(db, event_id="dry-run-runtime")
        record_automation_worker_status(
            db,
            worker_id="worker-dry-run",
            status="checking",
            result={"status": "checking"},
        )
        db.commit()

        runtime = automation_runtime_status(db, policy)

        assert runtime["status"] == "waiting"
        blockers = {item["code"]: item["severity"] for item in runtime["blockers"]}
        assert blockers["dry_run_enabled"] == "warning"
        assert blockers["optimizer_config_incomplete"] == "warning"
        assert "worker_not_seen" not in blockers
    finally:
        _close(engine, db)


def test_worker_heartbeat_does_not_overwrite_last_tick_result() -> None:
    engine, db, _user = _database()
    try:
        tick_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        seen_at = tick_at + timedelta(seconds=10)
        record_automation_worker_status(
            db,
            worker_id="worker-heartbeat",
            status="threshold_wait",
            result={"status": "threshold_wait", "available": 1},
            now=tick_at,
        )
        touch_automation_worker_status(
            db,
            worker_id="worker-heartbeat",
            now=seen_at,
        )
        db.commit()

        row = db.get(AutomationWorkerStatus, "worker-heartbeat")
        assert row.last_seen_at.replace(tzinfo=timezone.utc) == seen_at
        assert row.last_tick_at.replace(tzinfo=timezone.utc) == tick_at
        assert row.last_status == "threshold_wait"
        assert json.loads(row.last_result_json)["available"] == 1
    finally:
        _close(engine, db)


def test_worker_tick_records_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, db, _user = _database()

    @contextmanager
    def fake_session_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    try:
        monkeypatch.setattr(optimization_automation, "session_scope", fake_session_scope)

        def fail_consume(*_args, **_kwargs):
            raise RuntimeError("unexpected database detail")

        monkeypatch.setattr(
            optimization_automation,
            "consume_optimization_queue_once",
            fail_consume,
        )
        result = optimization_worker_tick("worker-safe-failure")

        assert result == {
            "status": "worker_error",
            "error_message": "automation_executor_failed",
        }
        worker = db.get(AutomationWorkerStatus, "worker-safe-failure")
        assert worker is not None
        assert worker.last_status == "worker_error"
        assert worker.last_error == "automation_executor_failed"
        assert worker.consecutive_errors == 1
    finally:
        _close(engine, db)


def test_automation_failure_sets_backoff_and_expired_lease_recovers() -> None:
    class RetryableModelError(RuntimeError):
        technical_error_type = "timeout"
        retryable = True

    class FailingAdapter:
        def estimate_cost_micros(self, *, frozen_input):
            del frozen_input
            return 1000

        def optimize(self, *, frozen_input, max_candidates):
            del frozen_input, max_candidates
            raise RetryableModelError("sensitive upstream details")

    engine, db, _user = _database()
    try:
        policy = AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=False,
            case_threshold=1,
            daily_budget_micros=100_000,
            base_retry_seconds=10,
        )
        db.add(policy)
        _feedback(db, event_id="failure")
        db.commit()
        now = datetime.now(timezone.utc)
        failed = consume_optimization_queue_once(
            db,
            worker_id="worker-fail",
            adapter=FailingAdapter(),
            now=now,
        )
        db.commit()
        assert failed["status"] == "failed"
        assert isinstance(failed["retry_at"], str)
        json.dumps(failed)
        case = db.scalar(select(OptimizationCaseQueue))
        assert case.status == "failed"
        assert case.next_attempt_at is not None
        assert case.last_error == "model_timeout"

        case.status = "processing"
        case.lease_owner = "dead-worker"
        case.lease_token = "expired-token"
        case.lease_expires_at = now - timedelta(seconds=1)
        case.next_attempt_at = None
        db.commit()
        recovered = consume_optimization_queue_once(
            db,
            worker_id="worker-recover",
            adapter=FailingAdapter(),
            now=now,
        )
        db.commit()
        assert recovered["recovered_leases"] == 1
    finally:
        _close(engine, db)


def test_test_adapter_succeeds_with_usage_and_never_publishes() -> None:
    engine, db, _user = _database()
    try:
        policy = AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=False,
            case_threshold=1,
            daily_budget_micros=100_000,
        )
        db.add(policy)
        _feedback(db, event_id="adapter-success")
        db.commit()
        adapter = DeterministicOptimizationAdapter(
                AutomationAdapterResult(
                candidates=[{
                    "system_prompt": "candidate system",
                    "user_prompt": "candidate user",
                    "change_note": "test candidate only",
                }],
                regression={
                    "status": "passed",
                    "target_errors": 1,
                    "stable_controls": 1,
                    "blind_holdouts": 1,
                },
                actual_cost_micros=2500,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
            )
        )
        result = consume_optimization_queue_once(
            db, worker_id="worker-test", adapter=adapter
        )
        db.commit()
        assert result["status"] == "succeeded"
        run = db.get(AutomationOptimizationRun, result["run_id"])
        evidence = json.loads(run.result_json)
        assert evidence["release_requires_human_review"] is True
        assert evidence["publishes_automatically"] is False
        assert run.input_tokens == 100
        assert run.output_tokens == 50
        assert run.total_tokens == 150
        assert db.scalar(select(PromptVersion.id)) is None
    finally:
        _close(engine, db)


def _observation(
    *,
    correct: bool,
    severity: str | None = None,
    confidence: float = 0.9,
    human: bool = False,
    latency: int = 100,
    retries: int = 0,
):
    return {
        "correct": correct,
        "error_severity": severity,
        "confidence": confidence,
        "needs_human": human,
        "latency_ms": latency,
        "input_tokens": 100,
        "output_tokens": 50,
        "retry_count": retries,
    }


def test_benchmark_metrics_apply_quality_gate_before_cost() -> None:
    pricing = {
        "input_micros_per_million_tokens": 1_000_000,
        "output_micros_per_million_tokens": 2_000_000,
        "human_review_cost_micros": 5000,
    }
    good = calculate_variant_metrics(
        [_observation(correct=True), _observation(correct=True, latency=200)],
        pricing,
        low_confidence_threshold=0.7,
    )
    cheap_bad = calculate_variant_metrics(
        [
            _observation(correct=False, severity="P0"),
            _observation(correct=True),
        ],
        {key: 0 for key in pricing},
        low_confidence_threshold=0.7,
    )
    decision = select_benchmark_candidate(
        [
            {"model_key": "sol", "metrics": good},
            {"model_key": "terra", "metrics": cheap_bad},
        ],
        {
            "min_quality_accuracy": 0.9,
            "max_p0_p1_errors": 0,
            "min_retry_stability": 0.9,
        },
    )
    assert decision["recommendation"] == "sol"
    assert "terra" not in decision["pareto_model_keys"]
    assert decision["automatically_changes_production"] is False


def test_benchmark_api_freezes_snapshot_and_runs_only_test_double() -> None:
    engine, db, user = _database()
    try:
        plan = AgentPlanVersion(
            name="controlled",
            version="benchmark-plan-v1",
            plan_json='{"roles":["precheck","aesthetic","risk_review"]}',
            status="published",
        )
        prompt_a = PromptVersion(
            stage="A",
            name="A",
            version="benchmark-a-v1",
            system_prompt="A system",
            user_prompt="A user",
            rubric_version="R1",
            status="published",
        )
        prompt_b = PromptVersion(
            stage="B",
            name="B",
            version="benchmark-b-v1",
            system_prompt="B system",
            user_prompt="B user",
            rubric_version="R1",
            status="published",
        )
        model = ModelConfig(
            name="baseline",
            model_id="baseline-model",
            base_url="https://example.test",
            api_path="/chat/completions",
            encrypted_api_key="test-secret-reference",
            benchmark_enabled=True,
            input_micros_per_million_tokens=1_000_000,
            output_micros_per_million_tokens=1_000_000,
            max_input_tokens=100,
            max_tokens=128,
        )
        policy = SamplingPolicy(id=1)
        assets = [
            Asset(
                original_name=f"{index}.jpg",
                stored_name=f"{index}.jpg",
                mime_type="image/jpeg",
                size_bytes=1,
                sha256=str(index) * 64,
            )
            for index in (1, 2)
        ]
        db.add_all([plan, prompt_a, prompt_b, model, policy, *assets])
        db.flush()
        bundle = get_or_create_bundle(
            db=db,
            model_config=model,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            rubric_version="R1",
            engine_version="E1",
            risk_review_version="risk-v1",
            sampling_policy=policy,
            agent_plan_version=plan.version,
        )
        db.commit()

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[current_user] = lambda: user
        client = TestClient(app)
        create_payload = {
            "experiment_key": "benchmark-1",
            "name": "三模型横评",
            "execution_mode": "test",
            "cohort_asset_ids": [asset.id for asset in assets],
            "strategy_bundle_id": bundle.id,
            "variants": [
                {
                    "model_key": key,
                    "provider": "test",
                    "model_id": f"gpt-5.6-{key}",
                    "input_micros_per_million_tokens": 1000,
                    "output_micros_per_million_tokens": 2000,
                    "human_review_cost_micros": 5000,
                }
                for key in ("sol", "terra", "luna")
            ],
            "min_quality_accuracy": 0.9,
            "max_p0_p1_errors": 0,
            "min_retry_stability": 0.9,
        }
        created = client.post("/api/model-benchmarks", json=create_payload)
        assert created.status_code == 200, created.text
        created_json = created.json()
        assert created_json["real_model_calls_enabled"] is False
        assert (
            created_json["frozen_snapshot"]["strategy_bundle"][
                "agent_plan_version"
            ]
            == "benchmark-plan-v1"
        )
        first_hash = created_json["snapshot_hash"]

        observations = {
            "sol": [
                _observation(correct=True),
                _observation(correct=True, latency=110),
            ],
            "terra": [
                _observation(correct=True, latency=80),
                _observation(correct=True, latency=90),
            ],
            "luna": [
                _observation(correct=False, severity="P1"),
                _observation(correct=True),
            ],
        }
        executed = client.post(
            f"/api/model-benchmarks/{created_json['id']}/run-test",
            json={"test_observations": observations},
        )
        assert executed.status_code == 200, executed.text
        result = executed.json()
        assert result["status"] == "completed"
        assert result["snapshot_hash"] == first_hash
        assert result["decision"]["recommendation"] in {"sol", "terra"}
        assert "luna" not in result["decision"]["pareto_model_keys"]

        disabled_payload = dict(
            create_payload,
            experiment_key="benchmark-disabled",
            execution_mode="disabled",
        )
        disabled = client.post(
            "/api/model-benchmarks", json=disabled_payload
        ).json()
        blocked = client.post(
            f"/api/model-benchmarks/{disabled['id']}/run-test",
            json={"test_observations": observations},
        )
        assert blocked.status_code == 409

        real_payload = dict(
            create_payload,
            experiment_key="benchmark-real-budget-blocked",
            execution_mode="real",
            max_round_cost_micros=1,
            quality_gate_approved=True,
            variants=[
                {
                    "model_key": key,
                    "model_config_id": model.id,
                    "human_review_cost_micros": 5000,
                }
                for key in ("sol", "terra", "luna")
            ],
        )
        real_blocked = client.post("/api/model-benchmarks", json=real_payload)
        assert real_blocked.status_code == 409
        assert real_blocked.json()["detail"] == "真实横评预测成本超过单轮上限"

        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "UPDATE model_benchmark_experiments "
                    "SET snapshot_hash=:hash WHERE id=:id"
                ),
                {"hash": "0" * 64, "id": created_json["id"]},
            )
            db.commit()
        db.rollback()
        experiment = db.get(
            ModelBenchmarkExperiment, created_json["id"]
        )
        assert experiment.snapshot_hash == first_hash
    finally:
        _close(engine, db)


def test_optimizer_usage_missing_stops_before_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeClient:
        def __init__(self, _config):
            pass

        async def chat_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return DoubaoResponse(parsed={"summary": "test"}, raw_text="{}", raw_payload={})

    monkeypatch.setattr(optimizer, "DoubaoClient", FakeClient)
    with pytest.raises(RuntimeError, match="optimizer_usage_missing"):
        asyncio.run(
            optimizer.generate_automation_candidates(
                config=SimpleNamespace(),
                base_prompt=SimpleNamespace(
                    stage="B",
                    version="B1",
                    system_prompt="system",
                    user_prompt="user",
                ),
                frozen_input={"cases": []},
                max_candidates=1,
            )
        )
    assert calls == 1


def test_benchmark_usage_missing_stops_before_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeClient:
        def __init__(self, _config):
            pass

        async def chat_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return DoubaoResponse(
                parsed={"classification": {"scope_status": "in_scope"}},
                raw_text="{}",
                raw_payload={},
            )

    monkeypatch.setattr(benchmarking, "DoubaoClient", FakeClient)
    config = ModelConfig(
        input_micros_per_million_tokens=1000,
        output_micros_per_million_tokens=1000,
    )
    adapter = OpenAICompatibleBenchmarkAdapter(
        configs={"sol": config},
        asset_paths={1: Path("unused-by-test.jpg")},
        round_cost_limit_micros=1000,
    )
    with pytest.raises(RuntimeError, match="benchmark_usage_missing"):
        adapter.evaluate(
            model_key="sol",
            frozen_snapshot={
                "prompt_a": {"system_prompt": "A", "user_prompt": "A"},
                "prompt_b": {
                    "system_prompt": "B",
                    "user_prompt": "B",
                    "rubric_version": "R1",
                },
                "samples": [{
                    "asset_id": 1,
                    "image": {"mime_type": "image/jpeg"},
                    "truth": {"level": "L1"},
                }],
            },
        )
    assert calls == 1


def test_real_benchmark_rejects_unapproved_or_changed_snapshot_before_adapter() -> None:
    class CountingAdapter:
        calls = 0

        def evaluate(self, *, model_key, frozen_snapshot):
            del model_key, frozen_snapshot
            self.calls += 1
            raise AssertionError("质量门或快照失败后不应调用执行器")

    engine, db, _user = _database()
    try:
        frozen = {"schema_version": "model-benchmark-v1", "samples": []}
        experiment = ModelBenchmarkExperiment(
            experiment_key="real-gate-first",
            name="real gate first",
            execution_mode="real",
            cohort_hash="1" * 64,
            snapshot_hash=snapshot_hash(frozen),
            frozen_snapshot_json=json.dumps(frozen),
            quality_gate_json=json.dumps({
                "min_quality_accuracy": 0.9,
                "max_p0_p1_errors": 0,
                "min_retry_stability": 0.9,
                "approved_for_real_execution": False,
            }),
        )
        db.add(experiment)
        db.commit()
        adapter = CountingAdapter()
        with pytest.raises(ValueError, match="质量门"):
            run_benchmark_experiment(
                db, experiment=experiment, adapter=adapter, actor="test"
            )
        experiment.quality_gate_json = json.dumps({
            "min_quality_accuracy": 0.9,
            "max_p0_p1_errors": 0,
            "min_retry_stability": 0.9,
            "approved_for_real_execution": True,
        })
        experiment.snapshot_hash = "0" * 64
        with pytest.raises(ValueError, match="哈希"):
            run_benchmark_experiment(
                db, experiment=experiment, adapter=adapter, actor="test"
            )
        assert adapter.calls == 0
    finally:
        _close(engine, db)


def test_benchmark_failures_store_only_sanitized_error_codes() -> None:
    sentinel = "test-only-sensitive-key"

    class FailingAdapter:
        def evaluate(self, *, model_key, frozen_snapshot):
            del model_key, frozen_snapshot
            raise RuntimeError(f"provider failed with {sentinel}")

    engine, db, _user = _database()
    try:
        frozen = {"schema_version": "model-benchmark-v1", "samples": []}
        experiment = ModelBenchmarkExperiment(
            experiment_key="sanitized-failure",
            name="sanitized failure",
            execution_mode="real",
            cohort_hash="2" * 64,
            snapshot_hash=snapshot_hash(frozen),
            frozen_snapshot_json=json.dumps(frozen),
            quality_gate_json=json.dumps({
                "min_quality_accuracy": 0.9,
                "max_p0_p1_errors": 0,
                "min_retry_stability": 0.9,
                "approved_for_real_execution": True,
            }),
        )
        db.add(experiment)
        db.flush()
        db.add(ModelBenchmarkVariant(
            experiment_id=experiment.id,
            model_key="sol",
            provider="test",
            model_id="test",
            pricing_json="{}",
        ))
        db.commit()
        with pytest.raises(RuntimeError):
            run_benchmark_experiment(
                db, experiment=experiment, adapter=FailingAdapter(), actor="test"
            )
        variant = db.scalar(select(ModelBenchmarkVariant))
        audit = db.scalar(select(AuditEvent).where(AuditEvent.category == "model_benchmark"))
        assert variant.error_message == "benchmark_executor_failed"
        assert sentinel not in variant.error_message
        assert sentinel not in audit.payload_json
    finally:
        _close(engine, db)


def test_expired_lease_recovers_once_and_charges_reserved_budget() -> None:
    engine, db, _user = _database()
    try:
        now = datetime(2026, 1, 3, tzinfo=timezone.utc)
        policy = AutomationPolicy(id=1, enabled=False, dry_run=False)
        db.add(policy)
        _event, case, _duplicate = _feedback(db, event_id="lease-expired")
        run = AutomationOptimizationRun(
            run_key="expired-run",
            base_prompt_version="prompt-b-v1",
            policy_revision=1,
            status="processing",
            dry_run=False,
            trigger_reason="case_threshold",
            case_ids_json="[]",
            frozen_input_json="{}",
            estimated_cost_micros=1000,
        )
        db.add(run)
        db.flush()
        case.status = "processing"
        case.automation_run_id = run.id
        case.lease_owner = "dead-worker"
        case.lease_token = "expired-token"
        case.lease_expires_at = now - timedelta(seconds=1)
        db.add(AutomationBudgetDay(
            budget_date=now.date().isoformat(), reserved_micros=1000
        ))
        db.commit()

        first = consume_optimization_queue_once(db, worker_id="recovery", now=now)
        db.commit()
        second = consume_optimization_queue_once(db, worker_id="recovery", now=now)
        db.commit()
        budget = db.get(AutomationBudgetDay, now.date().isoformat())
        assert first["recovered_leases"] == 1
        assert second["recovered_leases"] == 0
        assert run.status == "failed"
        assert run.retryable is True
        assert case.status == "failed"
        assert budget.reserved_micros == 0
        assert budget.spent_micros == 1000
    finally:
        _close(engine, db)


def test_concurrent_workers_only_invoke_one_optimizer(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lease-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine, expire_on_commit=False) as seed:
        seed.add(AutomationPolicy(
            id=1,
            enabled=True,
            dry_run=False,
            case_threshold=1,
            daily_budget_micros=10_000,
        ))
        _feedback(seed, event_id="lease-race")
        seed.commit()

    started = threading.Event()
    release = threading.Event()
    calls = 0
    worker_result: dict[str, object] = {}
    worker_error: list[BaseException] = []

    class BlockingAdapter:
        def estimate_cost_micros(self, *, frozen_input):
            del frozen_input
            return 1000

        def optimize(self, *, frozen_input, max_candidates):
            nonlocal calls
            del frozen_input, max_candidates
            calls += 1
            started.set()
            assert release.wait(timeout=5)
            return AutomationAdapterResult(
                candidates=[{
                    "system_prompt": "candidate system",
                    "user_prompt": "candidate user",
                    "change_note": "test only",
                }],
                regression={},
                actual_cost_micros=500,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            )

    def first_worker() -> None:
        try:
            with Session(engine, expire_on_commit=False) as session:
                worker_result.update(consume_optimization_queue_once(
                    session, worker_id="worker-1", adapter=BlockingAdapter()
                ))
                session.commit()
        except BaseException as exc:  # pragma: no cover - asserted below
            worker_error.append(exc)

    thread = threading.Thread(target=first_worker)
    thread.start()
    assert started.wait(timeout=5)
    try:
        with Session(engine, expire_on_commit=False) as contender:
            second = consume_optimization_queue_once(
                contender, worker_id="worker-2", adapter=BlockingAdapter()
            )
            contender.commit()
        assert second["status"] == "idle"
    finally:
        release.set()
        thread.join(timeout=5)
        engine.dispose()
    assert not thread.is_alive()
    assert worker_error == []
    assert worker_result["status"] == "succeeded"
    assert calls == 1


def test_real_optimizer_materializes_new_draft_with_three_role_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, db, _user = _database()
    try:
        model = ModelConfig(
            name="baseline",
            model_id="baseline-model",
            base_url="https://example.test",
            api_path="/chat/completions",
        )
        prompt_a = PromptVersion(
            stage="A",
            name="A",
            version="automation-a-v1",
            system_prompt="A system",
            user_prompt="A user",
            rubric_version="R1",
            status="published",
        )
        prompt_b = PromptVersion(
            stage="B",
            name="B",
            version="automation-b-v1",
            system_prompt="B system",
            user_prompt="B user",
            rubric_version="R1",
            status="published",
        )
        policy = SamplingPolicy(id=1)
        db.add_all([model, prompt_a, prompt_b, policy])
        db.flush()
        baseline = get_or_create_bundle(
            db=db,
            model_config=model,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            rubric_version="R1",
            engine_version="E1",
            risk_review_version="risk-v1",
            sampling_policy=policy,
        )
        run = AutomationOptimizationRun(
            run_key="materialize-run",
            base_prompt_version=prompt_b.version,
            policy_revision=1,
            status="processing",
            dry_run=False,
            trigger_reason="case_threshold",
            case_ids_json="[1]",
            frozen_input_json=json.dumps({
                "regression_binding": {
                    "sample_set_id": 9,
                    "baseline_strategy_bundle_id": baseline.id,
                    "samples": [
                        {"sample_item_id": 11, "role": "target_error"},
                        {"sample_item_id": 12, "role": "stable_control"},
                        {"sample_item_id": 13, "role": "blind_holdout"},
                    ],
                }
            }),
            estimated_cost_micros=1000,
        )
        db.add(run)
        db.flush()
        captured = []
        expected_db = db

        def fake_create(payload, user, db, *, commit):
            assert db is expected_db
            assert user.username == "worker-test"
            assert commit is False
            captured.append(payload)
            return {"id": 77}

        monkeypatch.setattr(main, "_create_paired_regression", fake_create)
        adapter = RealOptimizationAdapter(
            config=SimpleNamespace(), base_prompt=prompt_b
        )
        materialized = adapter.materialize(
            db,
            run=run,
            result=AutomationAdapterResult(
                candidates=[{
                    "system_prompt": "candidate system",
                    "user_prompt": "candidate user",
                    "change_note": "minimal correction",
                }],
                regression={},
            ),
            worker_id="worker-test",
        )
        candidate = db.get(PromptVersion, materialized["prompt_ids"][0])
        assert candidate.status == "draft"
        assert candidate.source == "optimizer"
        assert candidate.source_automation_run_id == run.id
        assert prompt_b.status == "published"
        assert materialized["regression_ids"] == [77]
        assert [sample.role for sample in captured[0].samples] == [
            "target_error",
            "stable_control",
            "blind_holdout",
        ]
    finally:
        _close(engine, db)


def test_model_config_api_never_returns_or_persists_plaintext_key() -> None:
    engine, db, user = _database()
    try:
        plaintext = "test-only-plaintext-key"
        reference = "keychain:v1:test-model-config"
        config = ModelConfig(
            name="safe config",
            model_id="safe-model",
            encrypted_api_key=reference,
        )
        db.add(config)
        db.commit()

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[current_user] = lambda: user
        response = TestClient(app).get("/api/model-configs")
        assert response.status_code == 200
        serialized = response.text
        assert plaintext not in serialized
        assert reference not in serialized
        assert "encrypted_api_key" not in serialized
        assert config.encrypted_api_key == reference
        assert config.encrypted_api_key != plaintext
    finally:
        _close(engine, db)


def test_feedback_sender_example_defaults_to_safe_dry_run(tmp_path) -> None:
    sentinel = "test-only-payload-secret"
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({
        "event_id": "production-review:test-1",
        "schema_version": "production-feedback-v1",
        "event_type": "human_correction_finalized",
        "source_system": "test-production",
        "occurred_at": "2026-01-01T00:00:00Z",
        "payload": {
            "production_case_id": "case-1",
            "category_key": "space_image",
            "prompt_version": "B1",
            "severity": "P1",
            "model_output": {"private": sentinel},
            "human_truth": {"private": sentinel},
        },
    }), encoding="utf-8")
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts/integration/production_feedback_sender_example.py"
    )
    environment = dict(os.environ)
    environment.pop("LABELLAB_FEEDBACK_URL", None)
    environment.pop("LABELLAB_FEEDBACK_TOKEN", None)
    dry_run = subprocess.run(
        [sys.executable, str(script), str(event_path)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert dry_run.returncode == 0
    assert '"mode":"dry-run"' in dry_run.stdout
    assert sentinel not in dry_run.stdout
    assert "Authorization" not in dry_run.stdout

    blocked_send = subprocess.run(
        [sys.executable, str(script), str(event_path), "--send"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert blocked_send.returncode == 2
    assert "LABELLAB_FEEDBACK_URL" in blocked_send.stderr
    assert sentinel not in blocked_send.stderr


def test_dangerous_defaults_and_non_admin_server_guards() -> None:
    policy_request = main.AutomationPolicyUpdate()
    request = main.BenchmarkCreateRequest(
        experiment_key="default-mode",
        name="default mode",
        cohort_asset_ids=[1],
        strategy_bundle_id=1,
        variants=[
            main.BenchmarkVariantRequest(
                model_key=key,
                provider="test",
                model_id=f"test-{key}",
                human_review_cost_micros=0,
            )
            for key in ("sol", "terra", "luna")
        ],
    )
    assert policy_request.enabled is False
    assert policy_request.dry_run is True
    assert policy_request.daily_budget_micros == 0
    assert request.execution_mode == "test"
    assert request.max_round_cost_micros == 0
    assert request.quality_gate_approved is False

    engine, db, user = _database()
    try:
        persisted_policy = AutomationPolicy(id=1)
        db.add(persisted_policy)
        db.flush()
        assert persisted_policy.enabled is False
        assert persisted_policy.dry_run is True
        assert persisted_policy.daily_budget_micros == 0
        user.is_admin = False
        db.commit()

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[current_user] = lambda: user
        client = TestClient(app)
        real_payload = {
            "experiment_key": "forbidden-real",
            "name": "forbidden real",
            "execution_mode": "real",
            "cohort_asset_ids": [1],
            "strategy_bundle_id": 1,
            "variants": [
                {
                    "model_key": key,
                    "model_config_id": 1,
                    "human_review_cost_micros": 0,
                }
                for key in ("sol", "terra", "luna")
            ],
            "max_round_cost_micros": 1,
            "quality_gate_approved": True,
        }
        assert client.post(
            "/api/model-benchmarks", json=real_payload
        ).status_code == 403
        assert client.post("/api/model-config/test").status_code == 403
        assert client.post("/api/optimizer-config/test").status_code == 403
        assert client.post("/api/automation-runs/consume").status_code == 403
    finally:
        _close(engine, db)
