from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.benchmarking import (
    calculate_variant_metrics,
    select_benchmark_candidate,
)
from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    AgentPlanVersion,
    Asset,
    AutomationOptimizationRun,
    AutomationPolicy,
    ModelBenchmarkExperiment,
    ModelConfig,
    OptimizationCaseQueue,
    ProductionFeedbackEvent,
    PromptVersion,
    SamplingPolicy,
    User,
)
from app.optimization_automation import (
    AutomationAdapterResult,
    DeterministicOptimizationAdapter,
    consume_optimization_queue_once,
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
):
    return ingest_production_feedback(
        db,
        event_id=event_id,
        schema_version="production-feedback-v1",
        event_type="human_correction_finalized",
        source_system="production-labels",
        occurred_at=datetime.now(timezone.utc),
        payload={
            "production_case_id": f"case-{event_id}",
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


def test_feedback_api_rejects_malformed_and_conflicting_payloads() -> None:
    engine, db, user = _database()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        malformed = client.post(
            "/api/production-feedback-events",
            json={
                "event_id": "event-api",
                "schema_version": "production-feedback-v1",
                "event_type": "human_correction_finalized",
                "source_system": "production",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"prompt_version": "v1"},
            },
        )
        assert malformed.status_code == 422

        valid_payload = {
            "event_id": "event-api",
            "schema_version": "production-feedback-v1",
            "event_type": "human_correction_finalized",
            "source_system": "production",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "production_case_id": "prod-1",
                "prompt_version": "v1",
                "severity": "P1",
                "model_output": {"level": "L4"},
                "human_truth": {"level": "L2"},
            },
        }
        first = client.post(
            "/api/production-feedback-events", json=valid_payload
        )
        assert first.status_code == 200
        assert first.json()["duplicate"] is False
        second = client.post(
            "/api/production-feedback-events", json=valid_payload
        )
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        changed = dict(valid_payload)
        changed["payload"] = dict(valid_payload["payload"], severity="P0")
        conflict = client.post(
            "/api/production-feedback-events", json=changed
        )
        assert conflict.status_code == 409
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
        assert statuses == {"batched"}
    finally:
        _close(engine, db)


def test_p0_triggers_immediately_but_budget_blocks_live_mode() -> None:
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
            db, worker_id="worker-budget"
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
    finally:
        _close(engine, db)


def test_automation_failure_sets_backoff_and_expired_lease_recovers() -> None:
    class FailingAdapter:
        def optimize(self, *, frozen_input, max_candidates):
            del frozen_input, max_candidates
            raise RuntimeError("synthetic failure")

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
        case = db.scalar(select(OptimizationCaseQueue))
        assert case.status == "failed"
        assert case.next_attempt_at is not None
        assert case.last_error == "synthetic failure"

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


def test_test_adapter_never_publishes_and_waits_for_release_review() -> None:
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
                candidates=[{"version": "candidate-v1"}],
                regression={
                    "status": "passed",
                    "target_errors": 1,
                    "stable_controls": 1,
                    "blind_holdouts": 1,
                },
                actual_cost_micros=2500,
            )
        )
        result = consume_optimization_queue_once(
            db, worker_id="worker-test", adapter=adapter
        )
        db.commit()
        assert result["status"] == "awaiting_release_review"
        run = db.get(AutomationOptimizationRun, result["run_id"])
        evidence = json.loads(run.result_json)
        assert evidence["release_requires_human_review"] is True
        assert evidence["publishes_automatically"] is False
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
