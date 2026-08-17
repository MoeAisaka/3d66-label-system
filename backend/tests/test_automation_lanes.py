from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def _mechanism_snapshot() -> dict[str, object]:
    return {
        "model_snapshot": {"model": "m1", "revision": "r1"},
        "call_a_snapshot": {"version": "a1"},
        "call_b_snapshot": {"version": "b1"},
        "dimension_contract": {"hash": "d1"},
        "v3_rules": {"threshold": 80},
        "scoring_engine_version": "score-v4",
        "level_mapping": {"L1": [90, 100]},
    }


def test_mechanism_fingerprint_changes_when_v3_mapping_changes():
    from app import automation_lanes

    base = _mechanism_snapshot()
    first = automation_lanes.build_mechanism_fingerprint(**base)
    second = automation_lanes.build_mechanism_fingerprint(
        **{**base, "v3_rules": {"threshold": 81}}
    )

    assert len(first) == 64
    assert first != second


def test_lane_key_separates_incremental_baseline_and_route():
    from app import automation_lanes

    incremental = automation_lanes.build_lane_key(
        category_key="space_image",
        pipeline_kind="incremental",
        generation=2,
        mechanism_fingerprint="a" * 64,
        route_key="A",
    )
    baseline = automation_lanes.build_lane_key(
        category_key="space_image",
        pipeline_kind="baseline",
        generation=2,
        mechanism_fingerprint="a" * 64,
        route_key="A",
    )
    other_route = automation_lanes.build_lane_key(
        category_key="space_image",
        pipeline_kind="incremental",
        generation=2,
        mechanism_fingerprint="a" * 64,
        route_key="B",
    )

    assert incremental != baseline
    assert incremental != other_route


def test_lane_snapshot_requires_pipeline_generation_fingerprint_and_route():
    from app import automation_lanes

    with pytest.raises(ValueError, match="pipeline_kind"):
        automation_lanes.validate_lane_snapshot(
            {
                "category_key": "space_image",
                "generation": 1,
                "mechanism_fingerprint": "a" * 64,
                "route_key": "A",
            }
        )


def test_case_queue_rejects_unknown_pipeline_kind():
    from app.database import Base
    from app.models import OptimizationCaseQueue

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(
                OptimizationCaseQueue(
                    category_key="space_image",
                    pipeline_kind="cross_lane",
                    automation_generation=1,
                    idempotency_key="invalid-pipeline-kind",
                    source_type="production_feedback",
                    source_event_id=1,
                    prompt_version="prompt-v1",
                    severity="P2",
                    case_json="{}",
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        engine.dispose()
