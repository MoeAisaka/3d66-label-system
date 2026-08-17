from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


NOW = datetime(2026, 8, 17, 15, tzinfo=timezone.utc)


def _case(
    case_id: int,
    *,
    category: str = "space_image",
    pipeline_kind: str = "incremental",
    generation: int = 1,
    fingerprint: str = "a" * 64,
    route: str = "A",
    prompt: str = "b1",
    state: str = "eligible",
    severity: str = "P2",
    created_at: datetime | None = None,
):
    return SimpleNamespace(
        id=case_id,
        category_key=category,
        pipeline_kind=pipeline_kind,
        automation_generation=generation,
        mechanism_fingerprint=fingerprint,
        route_key=route,
        prompt_version=prompt,
        admission_state=state,
        severity=severity,
        created_at=created_at or NOW,
    )


def _lane(
    *,
    lane_id: int = 1,
    category: str = "space_image",
    pipeline_kind: str = "incremental",
    generation: int = 1,
    fingerprint: str = "a" * 64,
    status: str = "enabled",
    threshold: int = 2,
    min_batch_size: int = 1,
    max_wait_seconds: int = 3600,
):
    return SimpleNamespace(
        id=lane_id,
        category_key=category,
        pipeline_kind=pipeline_kind,
        generation=generation,
        mechanism_fingerprint=fingerprint,
        status=status,
        case_threshold=threshold,
        min_batch_size=min_batch_size,
        max_wait_seconds=max_wait_seconds,
        immediate_severities_json=json.dumps(["P0", "P1"]),
        cooldown_seconds=0,
        max_candidates=1,
        revision=1,
    )


def _policy(*, threshold: int = 2):
    return SimpleNamespace(
        case_threshold=threshold,
        immediate_severities_json=json.dumps(["P0", "P1"]),
        cooldown_seconds=0,
        max_candidates=1,
    )


def _db():
    from app.database import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _persist_lane(db: Session):
    from app.models import AutomationLanePolicy

    lane = AutomationLanePolicy(
        category_key="space_image",
        pipeline_kind="incremental",
        generation=1,
        status="enabled",
        case_threshold=2,
        min_batch_size=1,
        max_wait_seconds=3600,
        immediate_severities_json='["P0","P1"]',
        cooldown_seconds=0,
        max_candidates=1,
        mechanism_snapshot_json='{"model":"m1"}',
        mechanism_fingerprint="a" * 64,
    )
    db.add(lane)
    db.flush()
    return lane


def _persist_case(db: Session, lane, *, case_id: int):
    from app.models import (
        OptimizationCaseEligibilitySnapshot,
        OptimizationCaseQueue,
    )

    case = OptimizationCaseQueue(
        category_key=lane.category_key,
        pipeline_kind=lane.pipeline_kind,
        automation_generation=lane.generation,
        mechanism_fingerprint=lane.mechanism_fingerprint,
        route_key="A",
        admission_state="eligible",
        idempotency_key=f"batch-case-{case_id}",
        evaluation_id=case_id,
        final_review_id=case_id,
        source_type="human_review",
        prompt_version="b1",
        severity="P2",
        case_json='{"truth":"L2"}',
        created_at=NOW,
    )
    db.add(case)
    db.flush()
    snapshot = OptimizationCaseEligibilitySnapshot(
        case_id=case.id,
        lane_policy_id=lane.id,
        category_key=lane.category_key,
        pipeline_kind=lane.pipeline_kind,
        generation=lane.generation,
        mechanism_fingerprint=lane.mechanism_fingerprint,
        route_key="A",
        correction_revision=1,
        evidence_json='{"source":"test"}',
        admission_state="eligible",
        eligible_at=NOW,
    )
    db.add(snapshot)
    db.flush()
    case.eligibility_snapshot_id = snapshot.id
    return case


def test_lane_key_separates_pipeline_generation_mechanism_route_and_prompt():
    from app.automation_batching import build_case_lane_key

    identities = {
        build_case_lane_key(_case(1)),
        build_case_lane_key(_case(2, pipeline_kind="baseline")),
        build_case_lane_key(_case(3, generation=2)),
        build_case_lane_key(_case(4, fingerprint="b" * 64)),
        build_case_lane_key(_case(5, route="B")),
        build_case_lane_key(_case(6, prompt="b2")),
    }

    assert len(identities) == 6


def test_selector_ignores_historical_and_awaiting_evidence_cases():
    from app.automation_batching import select_ready_lane

    ready, skipped = select_ready_lane(
        available=[
            _case(1, state="historical_audit"),
            _case(2, state="awaiting_evidence"),
            _case(3),
        ],
        lane_policies=[_lane(threshold=1)],
        policy=_policy(threshold=1),
        now=NOW,
    )

    assert ready is not None
    assert ready["case_ids"] == [3]
    assert skipped == []


def test_selector_does_not_mix_mechanism_fingerprints_or_routes():
    from app.automation_batching import select_ready_lane

    ready, skipped = select_ready_lane(
        available=[
            _case(1, fingerprint="a" * 64, route="A"),
            _case(2, fingerprint="b" * 64, route="A"),
            _case(3, fingerprint="a" * 64, route="B"),
        ],
        lane_policies=[_lane(threshold=2)],
        policy=_policy(threshold=2),
        now=NOW,
    )

    assert ready is None
    assert {item["code"] for item in skipped} >= {
        "threshold_wait",
        "mechanism_mismatch",
    }


def test_selector_supports_immediate_and_low_volume_timeout_without_cross_lane_mix():
    from app.automation_batching import select_ready_lane

    immediate, _ = select_ready_lane(
        available=[_case(1, severity="P1"), _case(2, route="B")],
        lane_policies=[_lane(threshold=5)],
        policy=_policy(threshold=5),
        now=NOW,
    )
    assert immediate is not None
    assert immediate["trigger_reason"] == "immediate:P1"
    assert immediate["case_ids"] == [1]

    timed, _ = select_ready_lane(
        available=[
            _case(3, created_at=NOW - timedelta(hours=2)),
            _case(4, route="B", created_at=NOW - timedelta(hours=2)),
        ],
        lane_policies=[
            _lane(threshold=5, min_batch_size=1, max_wait_seconds=3600)
        ],
        policy=_policy(threshold=5),
        now=NOW,
    )
    assert timed is not None
    assert timed["trigger_reason"] == "max_wait"
    assert len(timed["case_ids"]) == 1


def test_create_batch_freezes_lane_policy_and_case_set():
    from app.automation_batching import create_automation_batch
    from app.models import AutomationBatchCase, AutomationPolicy

    engine, db = _db()
    try:
        lane = _persist_lane(db)
        case_a = _persist_case(db, lane, case_id=101)
        case_b = _persist_case(db, lane, case_id=102)
        policy = AutomationPolicy(id=1, enabled=True, dry_run=True, case_threshold=2)
        db.add(policy)
        db.flush()

        batch = create_automation_batch(
            db,
            lane=lane,
            selected_cases=[case_a, case_b],
            policy=policy,
            trigger_reason="threshold",
            now=NOW,
        )
        db.commit()

        assert batch.status == "queued"
        assert batch.category_key == lane.category_key
        assert batch.pipeline_kind == lane.pipeline_kind
        assert batch.generation == lane.generation
        assert batch.mechanism_fingerprint == lane.mechanism_fingerprint
        assert json.loads(batch.frozen_policy_json)["case_threshold"] == 2
        assert db.query(AutomationBatchCase).count() == 2
    finally:
        db.close()
        engine.dispose()


def test_create_batch_is_idempotent_for_same_lane_and_case_set():
    from app.automation_batching import create_automation_batch
    from app.models import AutomationBatch, AutomationPolicy

    engine, db = _db()
    try:
        lane = _persist_lane(db)
        case_a = _persist_case(db, lane, case_id=201)
        case_b = _persist_case(db, lane, case_id=202)
        policy = AutomationPolicy(id=1, enabled=True, dry_run=True, case_threshold=2)
        db.add(policy)
        db.flush()

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
            selected_cases=[case_b, case_a],
            policy=policy,
            trigger_reason="threshold",
            now=NOW,
        )
        db.commit()

        assert first.id == second.id
        assert db.query(AutomationBatch).count() == 1
    finally:
        db.close()
        engine.dispose()
