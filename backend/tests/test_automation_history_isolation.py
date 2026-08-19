from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


NOW = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


def _db():
    from app.database import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _case(db: Session, *, key: str, created_at: datetime, state: str = "eligible"):
    from app.models import OptimizationCaseQueue

    case = OptimizationCaseQueue(
        category_key="space_image",
        pipeline_kind="incremental",
        automation_generation=1,
        mechanism_fingerprint="a" * 64,
        route_key="A",
        admission_state=state,
        idempotency_key=key,
        evaluation_id=1,
        final_review_id=1,
        source_type="human_review",
        prompt_version="prompt-v1",
        severity="P2",
        case_json='{"truth":"L2"}',
        created_at=created_at,
    )
    db.add(case)
    db.flush()
    return case


def test_pre_enable_cases_are_historical_audit_and_not_dispatchable():
    from app.automation_lanes import case_is_dispatchable, quarantine_pre_enable_cases

    engine, db = _db()
    try:
        old = _case(db, key="old", created_at=datetime(2026, 8, 16, tzinfo=timezone.utc))
        new = _case(db, key="new", created_at=datetime(2026, 8, 17, 13, tzinfo=timezone.utc))
        db.commit()

        changed = quarantine_pre_enable_cases(db, enabled_at=NOW, actor="admin")
        db.refresh(old)
        db.refresh(new)

        assert changed == 1
        assert old.admission_state == "historical_audit"
        assert case_is_dispatchable(old, now=NOW) is False
        assert new.admission_state == "eligible"
        assert case_is_dispatchable(new, now=NOW) is True
    finally:
        db.close()
        engine.dispose()


def test_admit_historical_case_creates_new_idempotent_copy_without_mutating_source():
    from app.automation_lanes import admit_historical_case
    from app.models import AutomationLanePolicy

    engine, db = _db()
    try:
        source = _case(
            db,
            key="historical-source",
            created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
            state="historical_audit",
        )
        lane = AutomationLanePolicy(
            category_key="space_image",
            pipeline_kind="incremental",
            generation=2,
            mechanism_fingerprint="b" * 64,
            mechanism_snapshot_json="{}",
        )
        db.add(lane)
        db.commit()

        first = admit_historical_case(
            db,
            case_id=source.id,
            lane_policy_id=lane.id,
            actor="reviewer",
            reason="人工复核后纳入当前泳道",
        )
        second = admit_historical_case(
            db,
            case_id=source.id,
            lane_policy_id=lane.id,
            actor="reviewer",
            reason="人工复核后纳入当前泳道",
        )
        db.refresh(source)

        assert first.id != source.id
        assert second.id == first.id
        assert first.case_json == source.case_json
        assert source.admission_state == "historical_audit"
        assert first.admission_state == "admitted"
        assert first.pipeline_kind == "incremental"
        assert first.automation_generation == 2
        assert first.mechanism_fingerprint == "b" * 64
    finally:
        db.close()
        engine.dispose()


def test_review_round_migration_follows_global_automation_contract():
    from app.migrations.runner import MIGRATIONS

    by_version = {migration.version: migration for migration in MIGRATIONS}
    assert by_version[73].name == "add_global_automation_lanes"
    assert by_version[74].name == "add_review_rounds"
    assert by_version[75].name == "add_correction_contract_snapshots"
    assert by_version[76].name == "add_nas_asset_source_references"


def test_consumer_does_not_dispatch_historical_audit_cases():
    from app.models import AutomationPolicy
    from app.optimization_automation import consume_optimization_queue_once

    engine, db = _db()
    try:
        _case(
            db,
            key="historical-not-dispatchable",
            created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
            state="historical_audit",
        )
        db.add(
            AutomationPolicy(
                id=1,
                enabled=True,
                dry_run=True,
                case_threshold=1,
            )
        )
        db.commit()

        result = consume_optimization_queue_once(
            db,
            worker_id="history-test-worker",
            now=NOW,
        )

        assert result["status"] == "idle"
    finally:
        db.close()
        engine.dispose()
