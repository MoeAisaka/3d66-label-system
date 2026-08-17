"""Deterministic lane selection and immutable batch persistence helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .models import (
    AutomationBatch,
    AutomationBatchCase,
    AutomationLanePolicy,
    AutomationPolicy,
    OptimizationCaseEligibilitySnapshot,
    OptimizationCaseQueue,
)

LaneKey = tuple[str, str, int, str, str, str]
LaneIdentity = tuple[str, str, int, str]


def build_case_lane_key(case: OptimizationCaseQueue) -> LaneKey:
    """Return the complete identity that must match before two cases can mix."""

    return (
        str(case.category_key),
        str(case.pipeline_kind),
        int(case.automation_generation),
        str(case.mechanism_fingerprint or ""),
        str(case.route_key or ""),
        str(case.prompt_version),
    )


def _lane_identity(lane: AutomationLanePolicy) -> LaneIdentity:
    return (
        str(lane.category_key),
        str(lane.pipeline_kind),
        int(lane.generation),
        str(lane.mechanism_fingerprint),
    )


def _safe_string_list(value: Any, fallback: Any) -> list[str]:
    for candidate in (value, fallback):
        if isinstance(candidate, list):
            return [str(item) for item in candidate]
        if isinstance(candidate, str):
            try:
                parsed = json.loads(candidate or "[]")
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
    return []


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _complete_lane_identity(case: OptimizationCaseQueue) -> bool:
    key = build_case_lane_key(case)
    return bool(
        key[0]
        and key[1] in {"incremental", "baseline"}
        and key[2] >= 1
        and len(key[3]) == 64
        and key[4]
        and key[5]
    )


def select_ready_lane(
    *,
    available: Sequence[OptimizationCaseQueue],
    lane_policies: Sequence[AutomationLanePolicy],
    policy: AutomationPolicy,
    now: datetime,
    last_triggered_at_by_lane: Mapping[LaneKey, datetime] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select one strict lane without mixing pipeline or mechanism identities."""

    current = _aware(now)
    eligible = [
        case
        for case in available
        if case.admission_state in {"eligible", "admitted"}
        and _complete_lane_identity(case)
    ]
    grouped: dict[LaneKey, list[OptimizationCaseQueue]] = {}
    for case in eligible:
        grouped.setdefault(build_case_lane_key(case), []).append(case)
    for cases in grouped.values():
        cases.sort(key=lambda case: (_aware(case.created_at), int(case.id)))

    policies_by_scope: dict[tuple[str, str, int], list[AutomationLanePolicy]] = {}
    policies_by_identity: dict[LaneIdentity, list[AutomationLanePolicy]] = {}
    for lane in lane_policies:
        scope = (str(lane.category_key), str(lane.pipeline_kind), int(lane.generation))
        policies_by_scope.setdefault(scope, []).append(lane)
        policies_by_identity.setdefault(_lane_identity(lane), []).append(lane)
    skipped: list[dict[str, Any]] = []
    triggered = last_triggered_at_by_lane or {}

    for key in sorted(grouped):
        category_key, pipeline_kind, generation, fingerprint, route_key, prompt = key
        cases = grouped[key]
        scope = (category_key, pipeline_kind, generation)
        matching = policies_by_identity.get(
            (category_key, pipeline_kind, generation, fingerprint), []
        )
        if not matching:
            code = "mechanism_mismatch" if policies_by_scope.get(scope) else "lane_missing"
            skipped.append(
                {
                    "code": code,
                    "category_key": category_key,
                    "pipeline_kind": pipeline_kind,
                    "generation": generation,
                    "mechanism_fingerprint": fingerprint,
                    "route_key": route_key,
                    "prompt_version": prompt,
                    "available": len(cases),
                    "severity": "blocking",
                    "message": (
                        "案例机制指纹与当前泳道不一致。"
                        if code == "mechanism_mismatch"
                        else "案例没有匹配的自动化泳道。"
                    ),
                }
            )
            continue
        lane = sorted(matching, key=lambda item: (int(item.revision), int(item.id)))[-1]
        if lane.status != "enabled":
            skipped.append(
                {
                    "code": "lane_paused",
                    "category_key": category_key,
                    "pipeline_kind": pipeline_kind,
                    "generation": generation,
                    "route_key": route_key,
                    "prompt_version": prompt,
                    "available": len(cases),
                    "severity": "blocking",
                    "message": "匹配的自动化泳道尚未启用。",
                }
            )
            continue

        immediate_severities = set(
            _safe_string_list(
                getattr(lane, "immediate_severities_json", None),
                getattr(policy, "immediate_severities_json", None),
            )
        )
        trigger_case = next(
            (case for case in cases if case.severity in immediate_severities),
            None,
        )
        threshold = max(
            1,
            int(
                getattr(lane, "case_threshold", None)
                or getattr(policy, "case_threshold", 1)
            ),
        )
        min_batch_size = max(1, int(getattr(lane, "min_batch_size", 1) or 1))
        max_wait_seconds = max(0, int(getattr(lane, "max_wait_seconds", 0) or 0))
        oldest_age = current - _aware(cases[0].created_at)
        waited_long_enough = (
            max_wait_seconds == 0
            or oldest_age >= timedelta(seconds=max_wait_seconds)
        )
        trigger_reason: str | None = None
        if trigger_case is not None:
            trigger_reason = f"immediate:{trigger_case.severity}"
        elif len(cases) >= threshold:
            trigger_reason = "threshold"
        elif len(cases) >= min_batch_size and waited_long_enough:
            trigger_reason = "max_wait"
        if trigger_reason is None:
            skipped.append(
                {
                    "code": "threshold_wait",
                    "category_key": category_key,
                    "pipeline_kind": pipeline_kind,
                    "generation": generation,
                    "mechanism_fingerprint": fingerprint,
                    "route_key": route_key,
                    "prompt_version": prompt,
                    "available": len(cases),
                    "required": threshold,
                    "minimum": min_batch_size,
                    "severity": "waiting",
                    "message": (
                        f"当前泳道案例 {len(cases)}/{threshold}，尚未达到组批条件。"
                    ),
                }
            )
            continue

        cooldown_seconds = max(
            0,
            int(
                getattr(lane, "cooldown_seconds", None)
                if getattr(lane, "cooldown_seconds", None) is not None
                else getattr(policy, "cooldown_seconds", 0)
            ),
        )
        last_triggered = triggered.get(key)
        cooldown_until = (
            _aware(last_triggered) + timedelta(seconds=cooldown_seconds)
            if last_triggered is not None
            else None
        )
        if (
            trigger_case is None
            and cooldown_until is not None
            and cooldown_until > current
        ):
            skipped.append(
                {
                    "code": "cooldown",
                    "category_key": category_key,
                    "pipeline_kind": pipeline_kind,
                    "generation": generation,
                    "route_key": route_key,
                    "prompt_version": prompt,
                    "cooldown_until": cooldown_until.isoformat(),
                    "severity": "waiting",
                    "message": "当前泳道仍在冷却窗口。",
                }
            )
            continue

        selected = cases[:threshold]
        return (
            {
                "lane": lane,
                "lane_key": key,
                "case_ids": [int(case.id) for case in selected],
                "selected_cases": selected,
                "category_key": category_key,
                "pipeline_kind": pipeline_kind,
                "generation": generation,
                "mechanism_fingerprint": fingerprint,
                "route_key": route_key,
                "prompt_version": prompt,
                "case_threshold": threshold,
                "min_batch_size": min_batch_size,
                "max_candidates": max(
                    1,
                    int(
                        getattr(lane, "max_candidates", None)
                        or getattr(policy, "max_candidates", 1)
                    ),
                ),
                "trigger_case": trigger_case,
                "trigger_reason": trigger_reason,
            },
            skipped,
        )
    return None, skipped


def _frozen_batch_policy(
    *,
    lane: AutomationLanePolicy,
    policy: AutomationPolicy,
    prompt_version: str,
) -> dict[str, Any]:
    return {
        "schema_version": "automation-batch-policy-v1",
        "case_threshold": int(lane.case_threshold),
        "min_batch_size": int(lane.min_batch_size),
        "max_candidates": int(lane.max_candidates),
        "global_policy": {
            "revision": int(policy.revision),
            "dry_run": bool(policy.dry_run),
            "case_threshold": int(policy.case_threshold),
            "immediate_severities": _safe_string_list(
                policy.immediate_severities_json, []
            ),
            "daily_budget_micros": int(policy.daily_budget_micros),
            "cooldown_seconds": int(policy.cooldown_seconds),
            "max_candidates": int(policy.max_candidates),
        },
        "lane_policy": {
            "id": int(lane.id),
            "revision": int(lane.revision),
            "category_key": str(lane.category_key),
            "pipeline_kind": str(lane.pipeline_kind),
            "generation": int(lane.generation),
            "mechanism_fingerprint": str(lane.mechanism_fingerprint),
            "case_threshold": int(lane.case_threshold),
            "min_batch_size": int(lane.min_batch_size),
            "max_wait_seconds": int(lane.max_wait_seconds),
            "immediate_severities": _safe_string_list(
                lane.immediate_severities_json,
                policy.immediate_severities_json,
            ),
            "daily_budget_micros": int(lane.daily_budget_micros),
            "cooldown_seconds": int(lane.cooldown_seconds),
            "max_candidates": int(lane.max_candidates),
            "max_consecutive_batches": int(lane.max_consecutive_batches),
        },
        "prompt_version": prompt_version,
    }


def create_automation_batch(
    db: Session,
    *,
    lane: AutomationLanePolicy,
    selected_cases: Sequence[OptimizationCaseQueue],
    policy: AutomationPolicy,
    trigger_reason: str,
    now: datetime,
) -> AutomationBatch:
    """Persist one immutable batch for an already-selected strict lane."""

    if lane.status != "enabled":
        raise ValueError("automation lane is not enabled")
    cases = sorted(selected_cases, key=lambda case: int(case.id))
    if not cases:
        raise ValueError("automation batch requires at least one case")
    first_key = build_case_lane_key(cases[0])
    expected_identity = _lane_identity(lane)
    if first_key[:4] != expected_identity:
        raise ValueError("automation case identity does not match lane")
    if any(build_case_lane_key(case) != first_key for case in cases):
        raise ValueError("automation batch cannot mix lane identities")
    if any(case.admission_state not in {"eligible", "admitted"} for case in cases):
        raise ValueError("automation batch contains an ineligible case")

    snapshots: list[OptimizationCaseEligibilitySnapshot] = []
    for case in cases:
        snapshot = (
            db.get(
                OptimizationCaseEligibilitySnapshot,
                case.eligibility_snapshot_id,
            )
            if case.eligibility_snapshot_id is not None
            else None
        )
        if (
            snapshot is None
            or snapshot.case_id != case.id
            or snapshot.lane_policy_id != lane.id
            or snapshot.admission_state not in {"eligible", "admitted"}
            or snapshot.category_key != lane.category_key
            or snapshot.pipeline_kind != lane.pipeline_kind
            or snapshot.generation != lane.generation
            or snapshot.mechanism_fingerprint != lane.mechanism_fingerprint
            or snapshot.route_key != first_key[4]
        ):
            raise ValueError("automation case eligibility snapshot does not match lane")
        snapshots.append(snapshot)

    case_set_payload = {
        "case_ids": [int(case.id) for case in cases],
        "eligibility_snapshot_ids": [int(snapshot.id) for snapshot in snapshots],
    }
    case_set_hash = hashlib.sha256(
        canonical_json(case_set_payload).encode("utf-8")
    ).hexdigest()
    batch_key = f"automation-batch:{lane.id}:{case_set_hash}"
    existing = db.scalar(
        select(AutomationBatch).where(AutomationBatch.batch_key == batch_key)
    )
    if existing is not None:
        return existing

    frozen_policy = _frozen_batch_policy(
        lane=lane,
        policy=policy,
        prompt_version=first_key[5],
    )
    batch = AutomationBatch(
        batch_key=batch_key,
        lane_policy_id=lane.id,
        category_key=lane.category_key,
        pipeline_kind=lane.pipeline_kind,
        generation=lane.generation,
        mechanism_fingerprint=lane.mechanism_fingerprint,
        route_key=first_key[4],
        case_set_hash=case_set_hash,
        frozen_policy_json=canonical_json(frozen_policy),
        status="queued",
        trigger_reason=trigger_reason,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()
    for case, snapshot in zip(cases, snapshots, strict=True):
        db.add(
            AutomationBatchCase(
                batch_id=batch.id,
                eligibility_snapshot_id=snapshot.id,
                case_id=case.id,
                created_at=now,
            )
        )
    db.flush()
    append_audit_event(
        db,
        category="automation",
        action="batch_created",
        subject_type="automation_batch",
        subject_id=batch.id,
        actor="system",
        payload={
            "lane_policy_id": lane.id,
            "case_ids": case_set_payload["case_ids"],
            "eligibility_snapshot_ids": case_set_payload[
                "eligibility_snapshot_ids"
            ],
            "case_set_hash": case_set_hash,
            "trigger_reason": trigger_reason,
        },
        event_key=f"automation:batch-created:{batch_key}",
    )
    return batch
