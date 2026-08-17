"""Deterministic lane selection and immutable batch persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .models import AutomationLanePolicy, AutomationPolicy, OptimizationCaseQueue

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
