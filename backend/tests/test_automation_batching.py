from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


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
