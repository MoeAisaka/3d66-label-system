from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base, get_db
from app.doubao import DoubaoHTTPError
from app.main import app, current_user
from app.models import (
    Asset,
    CircuitBreaker,
    DimensionSchema,
    EvaluationControl,
    EvaluationJob,
    LoopAttempt,
    LoopRun,
    ModelConfig,
    PromptVersion,
    StrategyBundle,
    User,
)
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
    canonical_json,
    space_schema_definition_for_version,
)
from app.queue_scheduler import (
    DEFAULT_SHARES,
    DeterministicQueueScheduler,
    QueueJob,
    QueuePolicy,
    classify_technical_failure,
    queue_capacities,
    record_breaker_failure,
    reserved_capacities,
    retry_delay_seconds,
)


def test_default_reserved_shares_and_small_concurrency_guarantees() -> None:
    expected = {
        "production_batch": 50,
        "interactive": 20,
        "validation": 15,
        "canary": 10,
        "recovery": 5,
    }
    policy = QueuePolicy(global_limit=20)
    assert DEFAULT_SHARES == expected
    assert policy.shares == expected
    assert policy.weights == expected
    assert reserved_capacities(policy) == {
        "production_batch": 10,
        "interactive": 4,
        "validation": 3,
        "canary": 2,
        "recovery": 1,
    }
    small = reserved_capacities(QueuePolicy(global_limit=2))
    assert small["interactive"] == 1
    assert small["production_batch"] == 1


def test_fifo_is_preserved_inside_selected_queue() -> None:
    scheduler = DeterministicQueueScheduler(QueuePolicy(global_limit=3))
    now = datetime.now(timezone.utc)
    selected = scheduler.choose_job(
        [
            QueueJob(3, "interactive", now),
            QueueJob(1, "interactive", now),
            QueueJob(2, "interactive", now),
        ],
        running={},
    )
    assert selected is not None
    assert selected.id == 1


def test_idle_capacity_is_borrowed_and_reclaimed_without_preemption() -> None:
    policy = QueuePolicy(global_limit=10)
    borrowed = queue_capacities(
        policy,
        pending={
            "validation": 0,
            "interactive": 10,
            "production_batch": 0,
            "canary": 0,
            "recovery": 0,
        },
        running={
            "validation": 0,
            "interactive": 5,
            "production_batch": 0,
            "canary": 0,
            "recovery": 0,
        },
    )
    assert borrowed["interactive"].borrowed > 0
    assert borrowed["interactive"].effective_limit == 10

    scheduler = DeterministicQueueScheduler(policy)
    selected = scheduler.choose_queue(
        pending={
            "validation": 1,
            "interactive": 10,
            "production_batch": 0,
            "canary": 0,
            "recovery": 0,
        },
        running={
            "validation": 0,
            "interactive": 5,
            "production_batch": 0,
            "canary": 0,
            "recovery": 0,
        },
    )
    assert selected == "validation"


def test_validation_boost_recovery_limit_and_long_term_fairness() -> None:
    policy = QueuePolicy(global_limit=1)
    scheduler = DeterministicQueueScheduler(policy)
    counts: Counter[str] = Counter()
    pending = {
        "validation": 10000,
        "interactive": 10000,
        "production_batch": 10000,
        "canary": 10000,
        "recovery": 10000,
    }
    for _ in range(500):
        selected = scheduler.choose_queue(pending=pending, running={})
        assert selected is not None
        counts[selected] += 1
    assert set(counts) == set(pending)
    assert all(counts[queue] > 0 for queue in pending)
    assert counts["production_batch"] > max(
        count
        for queue, count in counts.items()
        if queue != "production_batch"
    )
    assert counts["validation"] > 500 * DEFAULT_SHARES["validation"] / 100

    boosted = DeterministicQueueScheduler(
        QueuePolicy(global_limit=1, validation_boost=60)
    )
    boosted_counts: Counter[str] = Counter()
    for _ in range(500):
        selected = boosted.choose_queue(pending=pending, running={})
        assert selected is not None
        boosted_counts[selected] += 1
    assert boosted_counts["validation"] > boosted_counts["production_batch"]

    capacities = queue_capacities(
        QueuePolicy(global_limit=20),
        pending={"recovery": 100},
        running={"recovery": 0},
    )
    assert capacities["recovery"].effective_limit == 1


def test_scheduler_state_survives_reconstruction_deterministically() -> None:
    policy = QueuePolicy(global_limit=3)
    pending = {queue: 100 for queue in DEFAULT_SHARES}
    continuous = DeterministicQueueScheduler(policy)
    expected = [
        continuous.choose_queue(pending=pending, running={})
        for _ in range(40)
    ]

    first = DeterministicQueueScheduler(policy)
    actual = [
        first.choose_queue(pending=pending, running={})
        for _ in range(13)
    ]
    state = first.export_state()
    rebuilt = DeterministicQueueScheduler(
        policy,
        deficits=state["deficits"],
        dispatch_count=state["dispatch_count"],
        last_recovery_dispatch=state["last_recovery_dispatch"],
    )
    actual.extend(
        rebuilt.choose_queue(pending=pending, running={})
        for _ in range(27)
    )
    assert actual == expected


def test_global_limit_blocks_dispatch() -> None:
    scheduler = DeterministicQueueScheduler(QueuePolicy(global_limit=2))
    selected = scheduler.choose_queue(
        pending={"interactive": 1},
        running={"validation": 1, "production_batch": 1},
    )
    assert selected is None


def test_retry_classification_budget_backoff_and_retry_after() -> None:
    timeout = classify_technical_failure(TimeoutError("timed out"))
    assert timeout.retryable is True
    assert timeout.error_type == "timeout"
    assert classify_technical_failure(
        "provider error", status_code=503
    ).error_type == "provider5xx"
    assert classify_technical_failure(
        "rate limit", status_code=429, retry_after_seconds=7
    ).retry_after_seconds == 7
    non_retryable = classify_technical_failure("invalid credentials")
    assert non_retryable.retryable is False
    assert non_retryable.priority == "P0"
    assert (
        classify_technical_failure(
            "business result has low confidence and parse conflict"
        ).retryable
        is False
    )
    http_429 = worker._technical_failure_from_exception(
        DoubaoHTTPError(429, {"Retry-After": "11"})
    )
    assert http_429.error_type == "429"
    assert http_429.retry_after_seconds == 11
    retry_date = format_datetime(
        datetime.now(timezone.utc) + timedelta(seconds=30),
        usegmt=True,
    )
    dated_429 = worker._technical_failure_from_exception(
        DoubaoHTTPError(429, {"Retry-After": retry_date})
    )
    assert dated_429.retry_after_seconds is not None
    assert 0 < dated_429.retry_after_seconds <= 30

    first = retry_delay_seconds(1, jitter_key="job")
    second = retry_delay_seconds(2, jitter_key="job")
    assert second > first
    assert retry_delay_seconds(
        1, retry_after_seconds=9, jitter_key="job"
    ) == 9


def test_breaker_opens_in_short_window_and_requires_explicit_reset_state() -> None:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    first = record_breaker_failure(now=now)
    second = record_breaker_failure(
        state=first.state,
        failure_count=first.failure_count,
        window_started_at=first.window_started_at,
        now=now,
    )
    third = record_breaker_failure(
        state=second.state,
        failure_count=second.failure_count,
        window_started_at=second.window_started_at,
        now=now,
    )
    assert third.state == "open"
    assert third.reason == "SHORT_WINDOW_FAILURE_THRESHOLD"
    assert third.cooldown_until is not None

    p0 = record_breaker_failure(now=now, retryable=False)
    assert p0.state == "open"
    assert p0.reason == "NON_RETRYABLE_P0"


def test_worker_technical_retry_uses_recovery_and_never_exceeds_two(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="retry.jpg",
        stored_name="retry.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="1" * 64,
    )
    bundle = StrategyBundle(
        canonical_hash="2" * 64,
        model_id="retry-model",
        model_config_snapshot="{}",
        prompt_a_version="retry-A",
        rubric_version="retry-rubric",
        engine_version="retry-engine",
    )
    db.add_all([asset, bundle])
    db.flush()
    loop_run = LoopRun(
        idempotency_key="retry-loop-key",
        request_fingerprint="3" * 64,
        asset_id=asset.id,
        strategy_bundle_id=bundle.id,
        status="waiting_result",
        current_round=2,
        decision_json="{}",
    )
    loop_attempt = LoopAttempt(
        business_round=2,
        kind="targeted_recheck",
        target_dimensions_json='["lighting"]',
        input_evidence_json="{}",
        status="waiting_result",
    )
    loop_run.attempts.append(loop_attempt)
    db.add(loop_run)
    db.flush()
    original = EvaluationJob(
        asset=asset,
        category_profile_snapshot_json='{"schema_version":"evaluation-category-profile-v1","category_key":"space_image"}',
        prompt_a_id=101,
        prompt_b_id=102,
        regression_item_id=201,
        strategy_bundle_id=bundle.id,
        status="processing",
        queue_class="production_batch",
        origin_queue_class="production_batch",
        batch_key="batch:retry",
        loop_attempt_id=loop_attempt.id,
    )
    db.add(original)
    db.commit()
    duplicate_root = EvaluationJob(
        asset_id=asset.id,
        prompt_a_id=101,
        prompt_b_id=102,
        regression_item_id=201,
        strategy_bundle_id=bundle.id,
        status="queued",
        queue_class="validation",
        origin_queue_class="validation",
        technical_attempt=0,
    )
    db.add(duplicate_root)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        assert worker._handle_technical_failure(
            original.id, TimeoutError("timed out")
        )
        jobs = db.scalars(
            select(EvaluationJob).order_by(EvaluationJob.id)
        ).all()
        assert [job.technical_attempt for job in jobs] == [0, 1]
        assert jobs[1].queue_class == "recovery"
        assert jobs[1].origin_queue_class == "production_batch"
        assert jobs[1].parent_job_id == original.id
        assert jobs[1].root_job_id == original.id
        assert jobs[1].loop_attempt_id == loop_attempt.id
        assert jobs[1].regression_item_id == 201
        assert jobs[1].prompt_a_id == 101
        assert jobs[1].prompt_b_id == 102
        assert (
            jobs[1].category_profile_snapshot_json
            == original.category_profile_snapshot_json
        )
        assert worker._handle_technical_failure(
            original.id, TimeoutError("duplicate callback")
        )
        assert (
            db.scalar(
                select(func.count(EvaluationJob.id)).where(
                    EvaluationJob.root_job_id == original.id
                )
            )
            == 2
        )
        jobs[1].status = "processing"
        db.commit()

        assert worker._handle_technical_failure(
            jobs[1].id, TimeoutError("timed out")
        )
        jobs = db.scalars(
            select(EvaluationJob).order_by(EvaluationJob.id)
        ).all()
        assert [job.technical_attempt for job in jobs] == [0, 1, 2]
        assert {job.root_job_id for job in jobs} == {original.id}
        assert {job.loop_attempt_id for job in jobs} == {loop_attempt.id}
        assert {job.regression_item_id for job in jobs} == {201}
        assert {job.prompt_a_id for job in jobs} == {101}
        assert {job.prompt_b_id for job in jobs} == {102}
        persisted_attempts = db.scalars(
            select(LoopAttempt).where(
                LoopAttempt.loop_run_id == loop_run.id
            )
        ).all()
        assert len(persisted_attempts) == 1
        assert persisted_attempts[0].business_round == 2
        jobs[2].status = "processing"
        db.commit()
        assert worker._handle_technical_failure(
            jobs[2].id, TimeoutError("timed out")
        ) is False
        assert (
            db.scalar(select(EvaluationJob).where(EvaluationJob.id > jobs[2].id))
            is None
        )
        breaker = db.scalar(
            select(CircuitBreaker).where(
                CircuitBreaker.scope_type == "batch",
                CircuitBreaker.scope_key == "batch:retry",
            )
        )
        assert breaker is not None
        assert breaker.state == "open"
        assert jobs[2].technical_attempt == 2
    finally:
        db.close()
        engine.dispose()


def test_worker_non_retryable_failure_is_p0_without_recovery(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="p0.jpg",
        stored_name="p0.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="2" * 64,
    )
    job = EvaluationJob(
        asset=asset,
        status="processing",
        batch_key="batch:p0",
    )
    db.add(job)
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        assert worker._handle_technical_failure(
            job.id, RuntimeError("invalid request")
        ) is False
        db.refresh(job)
        assert job.status == "failed"
        assert job.stage == "p0_error"
        assert job.error_message == "technical:non_retryable"
        assert db.scalar(select(EvaluationJob).where(EvaluationJob.parent_job_id == job.id)) is None
        breaker = db.scalar(
            select(CircuitBreaker).where(
                CircuitBreaker.scope_key == "batch:p0"
            )
        )
        assert breaker is not None
        assert breaker.state == "open"
    finally:
        db.close()
        engine.dispose()


def test_retry_after_attack_values_never_leave_jobs_processing(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    values = ("inf", "nan", "1e309", "-1", "invalid", "999999")
    jobs = []
    for index, value in enumerate(values, start=1):
        asset = Asset(
            original_name=f"retry-after-{index}.jpg",
            stored_name=f"retry-after-{index}.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256=(str(index) * 64)[:64],
        )
        job = EvaluationJob(
            asset=asset,
            status="processing",
            queue_class="production_batch",
            origin_queue_class="production_batch",
        )
        db.add(job)
        jobs.append((job, value))
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    started = datetime.now(timezone.utc)
    try:
        for job, value in jobs:
            assert worker._handle_technical_failure(
                job.id,
                DoubaoHTTPError(429, {"Retry-After": value}),
            )
        all_jobs = db.scalars(
            select(EvaluationJob).order_by(EvaluationJob.id)
        ).all()
        roots = [job for job in all_jobs if job.technical_attempt == 0]
        children = {
            job.parent_job_id: job
            for job in all_jobs
            if job.technical_attempt == 1
        }
        assert len(roots) == len(values)
        assert len(children) == len(values)
        assert {job.status for job in roots} == {"retrying"}
        assert {job.status for job in children.values()} == {"queued"}
        for root, value in jobs:
            child = children[root.id]
            retry_at = child.retry_after_at
            assert retry_at is not None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - started).total_seconds()
            if value == "999999":
                assert 3599 <= delay <= 3601
            else:
                assert 0 <= delay <= 5
    finally:
        db.close()
        engine.dispose()


def test_concurrent_technical_failure_callbacks_create_one_child(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'retry-race.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        asset = Asset(
            original_name="race.jpg",
            stored_name="race.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256="8" * 64,
        )
        bundle = StrategyBundle(
            canonical_hash="9" * 64,
            model_id="race-model",
            model_config_snapshot="{}",
            prompt_a_version="race-A",
            rubric_version="race-rubric",
            engine_version="race-engine",
        )
        db.add_all([asset, bundle])
        db.flush()
        job = EvaluationJob(
            asset=asset,
            regression_item_id=901,
            strategy_bundle_id=bundle.id,
            status="processing",
            queue_class="production_batch",
            origin_queue_class="production_batch",
            batch_key="batch:race",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    @contextmanager
    def concurrent_scope():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(worker, "session_scope", concurrent_scope)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _index: worker._handle_technical_failure(
                        job_id, TimeoutError("race")
                    ),
                    range(2),
                )
            )
        assert results == [True, True]
        with factory() as db:
            jobs = db.scalars(
                select(EvaluationJob).order_by(EvaluationJob.id)
            ).all()
            assert [job.technical_attempt for job in jobs] == [0, 1]
            assert jobs[1].root_job_id == jobs[0].id
            assert jobs[1].regression_item_id == 901
            assert jobs[1].strategy_bundle_id == bundle.id
    finally:
        engine.dispose()


def test_enqueue_compatibility_manual_canary_queue_status_and_breaker_api() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="queue-tester",
        password_hash="unused",
        display_name="Queue Tester",
    )
    asset = Asset(
        original_name="queue.jpg",
        stored_name="queue.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="3" * 64,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="A",
        version="queue-A",
        system_prompt="classification system prompt",
        user_prompt="classification input",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="B",
        version="queue-B",
        system_prompt="aesthetic system prompt",
        user_prompt="aesthetic input",
        status="published",
    )
    model = ModelConfig(
        max_concurrency=20,
        encrypted_api_key="configured-test-reference",
    )
    dimension_definition = space_schema_definition_for_version(
        ACTIVE_V13_VERSION
    )
    dimension_schema = DimensionSchema(
        schema_key=SPACE_SCHEMA_KEY,
        version=ACTIVE_V13_VERSION,
        schema_type="family_pack",
        family_key="space",
        display_name="空间现役维度",
        status="published",
        definition_json=canonical_json(dimension_definition),
        canonical_hash=canonical_hash(dimension_definition),
        created_by="test",
        published_by="test",
        published_at=datetime.now(timezone.utc),
    )
    db.add_all([user, asset, prompt_a, prompt_b, model, dimension_schema])
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        common = {
            "asset_ids": [asset.id],
            "prompt_a_id": prompt_a.id,
            "prompt_b_id": prompt_b.id,
        }
        default = client.post("/api/jobs/enqueue", json=common)
        manual = client.post(
            "/api/jobs/enqueue",
            json={**common, "manual_recheck": True},
        )
        canary = client.post(
            "/api/jobs/enqueue",
            json={**common, "queue_class": "canary"},
        )
        assert default.json()["queue_class"] == "production_batch"
        assert manual.json()["queue_class"] == "interactive"
        assert canary.json()["queue_class"] == "canary"
        rows = db.scalars(
            select(EvaluationJob).order_by(EvaluationJob.id)
        ).all()
        assert [row.queue_class for row in rows] == [
            "production_batch",
            "interactive",
            "canary",
        ]
        delayed = EvaluationJob(
            asset_id=asset.id,
            prompt_a_id=prompt_a.id,
            prompt_b_id=prompt_b.id,
            queue_class="production_batch",
            origin_queue_class="production_batch",
            retry_after_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
            status="queued",
        )
        db.add(delayed)
        db.commit()

        status = client.get("/api/queues/status")
        assert status.status_code == 200
        expected = {
            "production_batch": 50,
            "interactive": 20,
            "validation": 15,
            "canary": 10,
            "recovery": 5,
        }
        assert status.json()["shares"] == expected
        assert status.json()["weights"] == expected
        assert status.json()["validation_boost"] == 10
        assert status.json()["global_limit"] == 20
        queue_rows = {
            row["queue_class"]: row
            for row in status.json()["queues"]
        }
        assert queue_rows["interactive"]["pending"] == 1
        assert queue_rows["production_batch"]["pending"] == 2
        assert queue_rows["production_batch"]["pending_total"] == 2
        assert status.json()["credentials_configured"] is True
        assert status.json()["control_paused"] is False
        assert (
            queue_rows["production_batch"]["dispatchable_pending"] == 1
        )
        assert queue_rows["production_batch"]["blocked_by_credentials"] == 0
        assert queue_rows["production_batch"]["blocked_by_control"] == 0
        assert (
            queue_rows["production_batch"]["delayed_by_retry_after"] == 1
        )
        assert {
            queue: row["reserved"]
            for queue, row in queue_rows.items()
        } == {
            "production_batch": 10,
            "interactive": 4,
            "validation": 3,
            "canary": 2,
            "recovery": 1,
        }
        assert {
            queue: row["weight"]
            for queue, row in queue_rows.items()
        } == expected
        assert queue_rows["validation"]["effective_weight"] == 25
        assert queue_rows["production_batch"]["effective_weight"] == 50

        opened = client.post(
            "/api/circuit-breakers/batch/batch-api/open",
            json={"reason": "manual_test", "cooldown_seconds": 30},
        )
        assert opened.status_code == 200
        assert opened.json()["state"] == "open"
        reset = client.post(
            "/api/circuit-breakers/batch/batch-api/reset"
        )
        assert reset.status_code == 200
        assert reset.json()["state"] == "closed"
        assert reset.json()["reset_by"] == "queue-tester"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_queue_status_matches_worker_credential_and_pause_gates(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="queue-observer",
        password_hash="unused",
        display_name="Queue Observer",
    )
    model = ModelConfig(max_concurrency=2)
    asset = Asset(
        original_name="blocked.jpg",
        stored_name="blocked.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="9" * 64,
    )
    job = EvaluationJob(
        asset=asset,
        queue_class="interactive",
        origin_queue_class="interactive",
    )
    control = EvaluationControl(id=1, paused=False)
    db.add_all([user, model, job, control])
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        model.encrypted_api_key = "   "
        db.commit()
        blank = client.get("/api/queues/status").json()
        blank_row = next(
            item
            for item in blank["queues"]
            if item["queue_class"] == "interactive"
        )
        assert blank["credentials_configured"] is False
        assert blank_row["blocked_by_credentials"] == 1

        @contextmanager
        def test_scope():
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

        monkeypatch.setattr(worker, "session_scope", test_scope)
        assert worker.claim_next_job() is None

        model.encrypted_api_key = None
        db.commit()
        missing = client.get("/api/queues/status").json()
        missing_row = next(
            item
            for item in missing["queues"]
            if item["queue_class"] == "interactive"
        )
        assert missing["credentials_configured"] is False
        assert missing_row["pending_total"] == 1
        assert missing_row["blocked_by_credentials"] == 1
        assert missing_row["dispatchable_pending"] == 0

        model.encrypted_api_key = "configured-test-reference"
        control.paused = True
        db.commit()
        paused = client.get("/api/queues/status").json()
        paused_row = next(
            item
            for item in paused["queues"]
            if item["queue_class"] == "interactive"
        )
        assert paused["credentials_configured"] is True
        assert paused["control_paused"] is True
        assert paused_row["blocked_by_credentials"] == 0
        assert paused_row["blocked_by_control"] == 1
        assert paused_row["dispatchable_pending"] == 0
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_queue_status_and_worker_use_each_jobs_frozen_model_credentials(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="frozen-credential-observer",
        password_hash="unused",
        display_name="Frozen Credential Observer",
    )
    fallback_model = ModelConfig(
        name="configured fallback",
        encrypted_api_key="configured-test-reference",
        max_concurrency=4,
        active=False,
    )
    missing_model = ModelConfig(
        name="frozen model without key",
        encrypted_api_key=None,
    )
    asset = Asset(
        original_name="frozen-model.jpg",
        stored_name="frozen-model.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="8" * 64,
    )
    db.add_all([user, fallback_model, missing_model, asset])
    db.flush()
    blocked = EvaluationJob(
        asset=asset,
        queue_class="interactive",
        origin_queue_class="interactive",
        category_profile_snapshot_json=json.dumps(
            {
                "model_config_id": missing_model.id,
                "pdf_summary_model_config_id": None,
            }
        ),
    )
    available = EvaluationJob(
        asset=asset,
        queue_class="interactive",
        origin_queue_class="interactive",
        category_profile_snapshot_json=json.dumps(
            {
                "model_config_id": fallback_model.id,
                "pdf_summary_model_config_id": None,
            }
        ),
    )
    db.add_all([blocked, available])
    db.commit()

    def test_db():
        yield db

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    monkeypatch.setattr(worker, "session_scope", test_scope)
    client = TestClient(app)
    try:
        status = client.get("/api/queues/status").json()
        row = next(
            item
            for item in status["queues"]
            if item["queue_class"] == "interactive"
        )
        assert status["credentials_configured"] is True
        assert row["pending_total"] == 2
        assert row["blocked_by_credentials"] == 1
        assert row["dispatchable_pending"] == 1

        assert worker.claim_next_job() == available.id
        db.refresh(blocked)
        db.refresh(available)
        assert blocked.status == "queued"
        assert available.status == "processing"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_open_breaker_preserves_queued_job_and_claims_other_batch(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="breaker.jpg",
        stored_name="breaker.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="4" * 64,
    )
    model = ModelConfig(encrypted_api_key="not-used-by-claim")
    blocked = EvaluationJob(
        asset=asset,
        batch_key="batch:blocked",
        queue_class="production_batch",
    )
    available = EvaluationJob(
        asset=asset,
        batch_key="batch:available",
        queue_class="production_batch",
    )
    breaker = CircuitBreaker(
        scope_type="batch",
        scope_key="batch:blocked",
        state="open",
        failure_count=3,
    )
    db.add_all([model, blocked, available, breaker])
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        claimed = worker.claim_next_job()
        assert claimed == available.id
        db.refresh(blocked)
        db.refresh(available)
        assert blocked.status == "queued"
        assert available.status == "processing"
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("scope_type", "scope_key"),
    [
        ("batch", "batch:cooldown"),
        ("strategy", "1"),
    ],
)
def test_expired_breaker_resets_and_job_becomes_claimable(
    monkeypatch,
    scope_type: str,
    scope_key: str,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="expired-breaker.jpg",
        stored_name="expired-breaker.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="5" * 64,
    )
    model = ModelConfig(encrypted_api_key="not-used-by-claim")
    bundle = StrategyBundle(
        canonical_hash="6" * 64,
        model_id="expired-breaker-model",
        model_config_snapshot="{}",
        prompt_a_version="expired-breaker-A",
        rubric_version="expired-breaker-rubric",
        engine_version="expired-breaker-engine",
    )
    db.add_all([asset, model, bundle])
    db.flush()
    job = EvaluationJob(
        asset=asset,
        batch_key="batch:cooldown",
        strategy_bundle_id=bundle.id,
        queue_class="production_batch",
    )
    breaker = CircuitBreaker(
        scope_type=scope_type,
        scope_key=(
            str(bundle.id) if scope_type == "strategy" else scope_key
        ),
        state="open",
        failure_count=3,
        window_started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        last_failure_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        cooldown_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        reason="SHORT_WINDOW_FAILURE_THRESHOLD",
    )
    db.add_all([job, breaker])
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        assert worker.claim_next_job() == job.id
        db.refresh(breaker)
        assert breaker.state == "closed"
        assert breaker.failure_count == 0
        assert breaker.window_started_at is None
        assert breaker.opened_at is None
        assert breaker.cooldown_until is None
        assert breaker.reason is None
        assert breaker.reset_by == "system:cooldown"
        assert breaker.reset_at is not None
        assert breaker.last_failure_at is not None
    finally:
        db.close()
        engine.dispose()


def test_future_breaker_cooldown_keeps_job_blocked(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="future-breaker.jpg",
        stored_name="future-breaker.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="7" * 64,
    )
    model = ModelConfig(encrypted_api_key="not-used-by-claim")
    job = EvaluationJob(
        asset=asset,
        batch_key="batch:future-cooldown",
        queue_class="production_batch",
    )
    breaker = CircuitBreaker(
        scope_type="batch",
        scope_key="batch:future-cooldown",
        state="open",
        failure_count=3,
        cooldown_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        reason="SHORT_WINDOW_FAILURE_THRESHOLD",
    )
    db.add_all([model, job, breaker])
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        assert worker.claim_next_job() is None
        db.refresh(breaker)
        assert breaker.state == "open"
        assert breaker.reset_by is None
        assert job.status == "queued"
    finally:
        db.close()
        engine.dispose()
