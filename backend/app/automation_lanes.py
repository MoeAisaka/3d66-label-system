"""Versioned category/pipeline lanes for safe automation dispatch.

This module intentionally contains only deterministic, side-effect-free lane
contracts. Persistence and dispatch are layered on top in later tasks.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit_event
from .models import AutomationLanePolicy, OptimizationCaseQueue

PipelineKind = Literal["incremental", "baseline"]
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_mechanism_fingerprint(
    *,
    model_snapshot: Mapping[str, Any],
    call_a_snapshot: Mapping[str, Any],
    call_b_snapshot: Mapping[str, Any],
    dimension_contract: Mapping[str, Any],
    v3_rules: Mapping[str, Any],
    scoring_engine_version: str,
    level_mapping: Mapping[str, Any],
) -> str:
    """Return a stable SHA-256 over every mechanism input that affects output."""

    if not scoring_engine_version or not scoring_engine_version.strip():
        raise ValueError("scoring_engine_version must be non-empty")
    return _sha256(
        {
            "model_snapshot": dict(model_snapshot),
            "call_a_snapshot": dict(call_a_snapshot),
            "call_b_snapshot": dict(call_b_snapshot),
            "dimension_contract": dict(dimension_contract),
            "v3_rules": dict(v3_rules),
            "scoring_engine_version": scoring_engine_version,
            "level_mapping": dict(level_mapping),
        }
    )


def build_lane_key(
    *,
    category_key: str,
    pipeline_kind: PipelineKind,
    generation: int,
    mechanism_fingerprint: str,
    route_key: str,
) -> str:
    """Build a readable, deterministic identity for one isolated automation lane."""

    _validate_lane_identity(
        category_key=category_key,
        pipeline_kind=pipeline_kind,
        generation=generation,
        mechanism_fingerprint=mechanism_fingerprint,
        route_key=route_key,
    )
    digest = _sha256(
        {
            "category_key": category_key,
            "pipeline_kind": pipeline_kind,
            "generation": generation,
            "mechanism_fingerprint": mechanism_fingerprint,
            "route_key": route_key,
        }
    )
    return f"lane:{category_key}:{pipeline_kind}:g{generation}:{route_key}:{digest}"


def validate_lane_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Validate the immutable fields required before a case can join a lane."""

    missing = [
        field
        for field in (
            "category_key",
            "pipeline_kind",
            "generation",
            "mechanism_fingerprint",
            "route_key",
        )
        if field not in snapshot
    ]
    if missing:
        raise ValueError(f"lane snapshot missing required fields: {', '.join(missing)}")
    _validate_lane_identity(
        category_key=snapshot["category_key"],
        pipeline_kind=snapshot["pipeline_kind"],
        generation=snapshot["generation"],
        mechanism_fingerprint=snapshot["mechanism_fingerprint"],
        route_key=snapshot["route_key"],
    )


def case_is_dispatchable(
    case: OptimizationCaseQueue,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a queue case is eligible for the automated consumer."""

    current = now or datetime.now(timezone.utc)
    if case.status not in {"pending", "failed"}:
        return False
    if case.admission_state not in {"eligible", "admitted"}:
        return False
    if case.next_attempt_at is not None and case.next_attempt_at > current:
        return False
    if case.lease_expires_at is not None and case.lease_expires_at > current:
        return False
    return True


def quarantine_pre_enable_cases(
    db: Session,
    *,
    enabled_at: datetime,
    actor: str,
) -> int:
    """Move pre-enable pending cases into audit-only state without rewriting facts."""

    cases = db.scalars(
        select(OptimizationCaseQueue).where(
            OptimizationCaseQueue.created_at < enabled_at,
            OptimizationCaseQueue.status.in_(["pending", "failed"]),
            OptimizationCaseQueue.admission_state != "historical_audit",
        )
    ).all()
    for case in cases:
        case.admission_state = "historical_audit"
        append_audit_event(
            db,
            category="automation",
            action="quarantine_pre_enable_case",
            subject_type="optimization_case_queue",
            subject_id=case.id,
            actor=actor,
            payload={
                "enabled_at": enabled_at.isoformat(),
                "admission_state": "historical_audit",
            },
            event_key=f"automation:quarantine:{case.id}:{enabled_at.isoformat()}",
        )
    db.flush()
    return len(cases)


def admit_historical_case(
    db: Session,
    *,
    case_id: int,
    lane_policy_id: int,
    actor: str,
    reason: str,
) -> OptimizationCaseQueue:
    """Create one idempotent, lane-frozen copy of a historical human-review case."""

    source = db.get(OptimizationCaseQueue, case_id)
    if source is None:
        raise ValueError("historical source case does not exist")
    lane = db.get(AutomationLanePolicy, lane_policy_id)
    if lane is None:
        raise ValueError("automation lane policy does not exist")
    if source.admission_state != "historical_audit":
        raise ValueError("only historical_audit cases can be admitted")
    if source.source_type != "human_review":
        raise ValueError("historical admission currently requires a human_review source")
    if source.evaluation_id is None or source.final_review_id is None:
        raise ValueError("human_review source is missing immutable review references")

    idempotency_key = (
        f"historical-admission:{source.id}:lane:{lane.id}:revision:{lane.revision}"
    )
    existing = db.scalar(
        select(OptimizationCaseQueue).where(
            OptimizationCaseQueue.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return existing

    admitted = OptimizationCaseQueue(
        category_key=lane.category_key,
        pipeline_kind=lane.pipeline_kind,
        automation_generation=lane.generation,
        mechanism_fingerprint=lane.mechanism_fingerprint,
        route_key=source.route_key or "historical-admission",
        eligibility_snapshot_id=None,
        admission_state="admitted",
        idempotency_key=idempotency_key,
        evaluation_id=source.evaluation_id,
        final_review_id=source.final_review_id,
        source_type="human_review",
        prompt_version=source.prompt_version,
        severity=source.severity,
        case_json=source.case_json,
        status="pending",
    )
    db.add(admitted)
    db.flush()
    append_audit_event(
        db,
        category="automation",
        action="admit_historical_case",
        subject_type="optimization_case_queue",
        subject_id=admitted.id,
        actor=actor,
        payload={
            "source_case_id": source.id,
            "lane_policy_id": lane.id,
            "reason": reason,
            "pipeline_kind": lane.pipeline_kind,
            "generation": lane.generation,
            "mechanism_fingerprint": lane.mechanism_fingerprint,
        },
        event_key=f"automation:admit:{idempotency_key}",
    )
    return admitted


def _validate_lane_identity(
    *,
    category_key: Any,
    pipeline_kind: Any,
    generation: Any,
    mechanism_fingerprint: Any,
    route_key: Any,
) -> None:
    if not isinstance(category_key, str) or not category_key.strip():
        raise ValueError("category_key must be non-empty")
    if pipeline_kind not in ("incremental", "baseline"):
        raise ValueError("pipeline_kind must be 'incremental' or 'baseline'")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("generation must be a positive integer")
    if not isinstance(mechanism_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
        mechanism_fingerprint
    ):
        raise ValueError("mechanism_fingerprint must be a lowercase SHA-256 hex digest")
    if not isinstance(route_key, str) or not route_key.strip():
        raise ValueError("route_key must be non-empty")
