"""Admission contract for final human correction evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .models import (
    AutomationLanePolicy,
    OptimizationCaseEligibilitySnapshot,
    OptimizationCaseQueue,
)

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_list(value: Any, *, label: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}损坏") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{label}损坏")
    return parsed


def _normalize_node_corrections(evaluation: Any) -> tuple[Mapping[str, Any], ...]:
    raw_history = _json_list(
        getattr(evaluation, "correction_history_json", "[]"),
        label="节点纠偏历史",
    )
    normalized: list[Mapping[str, Any]] = []
    for raw in raw_history:
        if not isinstance(raw, Mapping):
            continue
        event = dict(raw)
        evidence = event.get("evidence")
        event["evidence"] = evidence if isinstance(evidence, list) else []
        if event.get("source") not in {"human", "automatic"}:
            event["source"] = (
                "automatic"
                if event.get("corrector_confidence") is not None
                or str(event.get("corrector_policy") or "").strip()
                else "human"
            )
        normalized.append(event)
    return tuple(normalized)


def _normalize_review(review: Any, *, is_final: bool) -> Mapping[str, Any]:
    corrections = _json_list(
        getattr(review, "corrections_json", None),
        label="最终人工审核纠偏",
    )
    normalized_corrections: list[Mapping[str, Any]] = []
    for raw in corrections:
        if not isinstance(raw, Mapping):
            continue
        reason_codes = raw.get("reason_codes")
        reason = str(raw.get("reason") or raw.get("note") or "").strip()
        if not reason and isinstance(reason_codes, list):
            reason = "、".join(
                str(code).strip() for code in reason_codes if str(code).strip()
            )
        evidence = raw.get("evidence")
        normalized_corrections.append(
            {
                **dict(raw),
                "reason": reason,
                "evidence": evidence if isinstance(evidence, list) else [],
            }
        )
    return {
        "review_id": getattr(review, "id", None),
        "reviewer_name": getattr(review, "reviewer_name", "") or "",
        "stage": getattr(review, "stage", None),
        "decision": getattr(review, "decision", None),
        "corrected_level": getattr(review, "corrected_level", None),
        "corrected_score": getattr(review, "corrected_score", None),
        "note": getattr(review, "note", "") or "",
        "corrections": normalized_corrections,
        "is_final": is_final,
    }


def build_final_correction_evidence(
    *,
    evaluation: Any,
    final_review: Any,
    mechanism_snapshot: Mapping[str, Any] | None = None,
    mechanism_fingerprint: str = "",
    correction_context: Mapping[str, Any] | None = None,
    category_key: str | None = None,
    pipeline_kind: str | None = None,
) -> FinalCorrectionEvidence:
    """Freeze final-review, node-correction and mechanism evidence into one object."""

    context = _as_mapping(correction_context)
    node_corrections = context.get("node_corrections")
    if not isinstance(node_corrections, list):
        node_corrections = list(_normalize_node_corrections(evaluation))
    else:
        node_corrections = tuple(
            dict(item) for item in node_corrections if isinstance(item, Mapping)
        )

    human_reviews = context.get("human_reviews")
    if isinstance(human_reviews, list):
        normalized_reviews = [
            dict(item) for item in human_reviews if isinstance(item, Mapping)
        ]
    else:
        normalized_reviews = []
    final_review_id = getattr(final_review, "id", None)
    if not any(item.get("review_id") == final_review_id for item in normalized_reviews):
        normalized_reviews.append(_normalize_review(final_review, is_final=True))
    else:
        normalized_reviews = [
            {
                **item,
                "is_final": item.get("review_id") == final_review_id,
            }
            for item in normalized_reviews
        ]

    job = getattr(evaluation, "job", None)
    resolved_category = (
        category_key
        or getattr(job, "category_key", None)
        or getattr(evaluation, "category_key", None)
        or ""
    )
    resolved_pipeline = pipeline_kind or (
        "baseline"
        if getattr(job, "baseline_regression_item_id", None) is not None
        or getattr(evaluation, "baseline_regression_item_id", None) is not None
        else "incremental"
    )
    snapshot = dict(mechanism_snapshot or {})
    if not snapshot:
        raw_snapshot = getattr(evaluation, "strategy_snapshot_json", "{}")
        try:
            parsed_snapshot = json.loads(raw_snapshot or "{}")
        except json.JSONDecodeError:
            parsed_snapshot = {}
        if isinstance(parsed_snapshot, Mapping):
            snapshot = dict(parsed_snapshot)

    revision = context.get("review_revision")
    if not isinstance(revision, int) or revision < 1:
        revision = getattr(evaluation, "review_revision", 1) or 1
    correction_values: list[tuple[Any, Any]] = []
    for event in node_corrections:
        correction_values.append((event.get("old_value"), event.get("new_value")))
    for review in normalized_reviews:
        for correction in review.get("corrections") or []:
            if isinstance(correction, Mapping):
                correction_values.append(
                    (correction.get("model_value"), correction.get("human_value"))
                )
    severity = (
        "P1"
        if any(
            isinstance(old, int)
            and isinstance(new, int)
            and abs(new - old) >= 2
            for old, new in correction_values
        )
        else "P2"
    )
    return FinalCorrectionEvidence(
        category_key=str(resolved_category),
        pipeline_kind=str(resolved_pipeline),
        evaluation_id=int(getattr(evaluation, "id", 0) or 0),
        final_review_id=int(final_review_id or 0),
        correction_revision=int(revision),
        node_corrections=tuple(node_corrections),
        human_reviews=tuple(normalized_reviews),
        mechanism_snapshot=snapshot,
        mechanism_fingerprint=str(
            mechanism_fingerprint or snapshot.get("mechanism_fingerprint") or ""
        ),
        severity=severity,
    )


@dataclass(frozen=True)
class FinalCorrectionEvidence:
    category_key: str
    pipeline_kind: str
    evaluation_id: int
    final_review_id: int
    correction_revision: int
    node_corrections: tuple[Mapping[str, Any], ...]
    human_reviews: tuple[Mapping[str, Any], ...]
    mechanism_snapshot: Mapping[str, Any]
    mechanism_fingerprint: str
    severity: str = "P2"


def qualify_correction(
    evidence: FinalCorrectionEvidence,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if not evidence.category_key.strip():
        blockers.append("category_missing")
    if evidence.pipeline_kind not in {"incremental", "baseline"}:
        blockers.append("pipeline_kind_invalid")
    if evidence.evaluation_id < 1:
        blockers.append("evaluation_missing")
    if evidence.final_review_id < 1:
        blockers.append("final_review_missing")
    if evidence.correction_revision < 1:
        blockers.append("correction_revision_invalid")
    if len(evidence.mechanism_fingerprint) != 64:
        blockers.append("mechanism_fingerprint_invalid")
    if not isinstance(evidence.mechanism_snapshot, Mapping):
        blockers.append("mechanism_snapshot_missing")

    human_corrections: list[Mapping[str, Any]] = [
        correction
        for correction in evidence.node_corrections
        if correction.get("source", "human") == "human"
    ]
    for review in evidence.human_reviews:
        for correction in review.get("corrections") or []:
            if isinstance(correction, Mapping):
                human_corrections.append(correction)
    if not human_corrections:
        blockers.append("human_evidence_missing")
    else:
        if any(not str(correction.get("reason") or "").strip() for correction in human_corrections):
            blockers.append("reason_missing")
        if any(
            not isinstance(correction.get("evidence"), list)
            or not correction.get("evidence")
            for correction in human_corrections
        ):
            blockers.append("evidence_missing")

    if not any(review.get("is_final") is True for review in evidence.human_reviews):
        blockers.append("final_review_missing")
    normalized = list(dict.fromkeys(blockers))
    return not normalized, normalized


def admit_final_correction(
    db: Session,
    *,
    evidence: FinalCorrectionEvidence,
    lane: AutomationLanePolicy,
) -> OptimizationCaseEligibilitySnapshot:
    qualified, blockers = qualify_correction(evidence)
    if not qualified:
        raise ValueError(f"final correction evidence blocked: {','.join(blockers)}")
    if evidence.category_key != lane.category_key:
        raise ValueError("final correction category does not match lane")
    if evidence.pipeline_kind != lane.pipeline_kind:
        raise ValueError("final correction pipeline does not match lane")
    if evidence.mechanism_fingerprint != lane.mechanism_fingerprint:
        raise ValueError("final correction mechanism fingerprint does not match lane")

    idempotency_key = (
        f"human-final:{evidence.evaluation_id}:{evidence.final_review_id}:"
        f"revision:{evidence.correction_revision}"
    )
    case = db.scalar(
        select(OptimizationCaseQueue).where(
            OptimizationCaseQueue.idempotency_key == idempotency_key
        )
    )
    if case is None:
        prompt_version = str(
            evidence.mechanism_snapshot.get("prompt_b_version")
            or evidence.mechanism_snapshot.get("prompt_version")
            or "frozen-final-review"
        )
        case = OptimizationCaseQueue(
            category_key=evidence.category_key,
            pipeline_kind=evidence.pipeline_kind,
            automation_generation=lane.generation,
            mechanism_fingerprint=evidence.mechanism_fingerprint,
            route_key=str(evidence.mechanism_snapshot.get("route_key") or "pending-route"),
            admission_state="eligible",
            idempotency_key=idempotency_key,
            evaluation_id=evidence.evaluation_id,
            final_review_id=evidence.final_review_id,
            source_type="human_review",
            prompt_version=prompt_version,
            severity=evidence.severity,
            case_json=canonical_json(
                {
                    "schema_version": "automation-final-correction-v1",
                    "evidence": evidence.__dict__,
                }
            ),
            status="pending",
        )
        db.add(case)
        db.flush()
    elif case.admission_state == "awaiting_evidence":
        # Evidence can arrive after the first final-review event (for example,
        # a node correction is completed in the same operator transaction).
        # Promote the same immutable review key in place rather than creating a
        # second queue case.
        case.category_key = evidence.category_key
        case.pipeline_kind = evidence.pipeline_kind
        case.automation_generation = lane.generation
        case.mechanism_fingerprint = evidence.mechanism_fingerprint
        case.route_key = str(
            evidence.mechanism_snapshot.get("route_key") or "pending-route"
        )
        case.admission_state = "eligible"
        case.prompt_version = str(
            evidence.mechanism_snapshot.get("prompt_b_version")
            or evidence.mechanism_snapshot.get("prompt_version")
            or "frozen-final-review"
        )
        case.case_json = canonical_json(
            {
                "schema_version": "automation-final-correction-v1",
                "evidence": evidence.__dict__,
            }
        )
        case.status = "pending"
        db.flush()

    snapshot = db.scalar(
        select(OptimizationCaseEligibilitySnapshot).where(
            OptimizationCaseEligibilitySnapshot.case_id == case.id
        )
    )
    if snapshot is not None:
        return snapshot

    snapshot = OptimizationCaseEligibilitySnapshot(
        case_id=case.id,
        lane_policy_id=lane.id,
        category_key=evidence.category_key,
        pipeline_kind=evidence.pipeline_kind,
        generation=lane.generation,
        mechanism_fingerprint=evidence.mechanism_fingerprint,
        route_key=str(evidence.mechanism_snapshot.get("route_key") or "pending-route"),
        correction_revision=evidence.correction_revision,
        evidence_json=canonical_json(evidence.__dict__),
        admission_state="eligible",
        eligible_at=datetime.now(timezone.utc),
        historical_source="final_human_review",
    )
    db.add(snapshot)
    db.flush()
    case.eligibility_snapshot_id = snapshot.id
    append_audit_event(
        db,
        category="automation",
        action="admit_final_correction",
        subject_type="optimization_case_queue",
        subject_id=case.id,
        actor="system",
        payload={
            "evaluation_id": evidence.evaluation_id,
            "final_review_id": evidence.final_review_id,
            "correction_revision": evidence.correction_revision,
            "eligibility_snapshot_id": snapshot.id,
        },
        event_key=f"automation:final-correction:{idempotency_key}",
    )
    return snapshot


def _find_current_lane(
    db: Session,
    *,
    category_key: str,
    pipeline_kind: str,
) -> AutomationLanePolicy | None:
    return db.scalar(
        select(AutomationLanePolicy)
        .where(
            AutomationLanePolicy.category_key == category_key,
            AutomationLanePolicy.pipeline_kind == pipeline_kind,
            AutomationLanePolicy.status == "enabled",
        )
        .order_by(
            AutomationLanePolicy.generation.desc(),
            AutomationLanePolicy.revision.desc(),
        )
    )


def _is_valid_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and _FINGERPRINT_RE.fullmatch(value) is not None


def _awaiting_evidence_case(
    db: Session,
    *,
    evidence: FinalCorrectionEvidence,
    lane: AutomationLanePolicy | None,
    blockers: list[str],
    actor: str,
) -> OptimizationCaseQueue:
    idempotency_key = (
        f"human-final:{evidence.evaluation_id}:{evidence.final_review_id}:"
        f"revision:{evidence.correction_revision}"
    )
    case = db.scalar(
        select(OptimizationCaseQueue).where(
            OptimizationCaseQueue.idempotency_key == idempotency_key
        )
    )
    if case is not None:
        return case

    mechanism_fingerprint = (
        evidence.mechanism_fingerprint
        if _is_valid_fingerprint(evidence.mechanism_fingerprint)
        else None
    )
    case = OptimizationCaseQueue(
        category_key=evidence.category_key,
        pipeline_kind=evidence.pipeline_kind,
        automation_generation=lane.generation if lane else 1,
        mechanism_fingerprint=mechanism_fingerprint,
        route_key=str(evidence.mechanism_snapshot.get("route_key") or "pending-route"),
        admission_state="awaiting_evidence",
        idempotency_key=idempotency_key,
        evaluation_id=evidence.evaluation_id,
        final_review_id=evidence.final_review_id,
        source_type="human_review",
        prompt_version=str(
            evidence.mechanism_snapshot.get("prompt_b_version")
            or evidence.mechanism_snapshot.get("prompt_version")
            or "awaiting-final-evidence"
        ),
        severity=evidence.severity,
        case_json=canonical_json(
            {
                "schema_version": "automation-final-correction-v1",
                "evidence": evidence.__dict__,
                "blockers": list(dict.fromkeys(blockers)),
            }
        ),
        status="pending",
    )
    db.add(case)
    db.flush()
    append_audit_event(
        db,
        category="automation",
        action="await_final_correction_evidence",
        subject_type="optimization_case_queue",
        subject_id=case.id,
        actor=actor,
        payload={
            "evaluation_id": evidence.evaluation_id,
            "final_review_id": evidence.final_review_id,
            "blockers": list(dict.fromkeys(blockers)),
            "lane_policy_id": lane.id if lane else None,
        },
        event_key=f"automation:await-evidence:{idempotency_key}",
    )
    return case


def on_final_review_completed(
    db: Session,
    *,
    evaluation: Any,
    final_review: Any,
    mechanism_snapshot: Mapping[str, Any] | None = None,
    mechanism_fingerprint: str = "",
    correction_context: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Route one completed final review into the current category lane.

    Missing human evidence, a missing enabled lane, and mechanism drift all stay
    in ``awaiting_evidence`` so the automated consumer cannot pick them up.
    """

    decision = str(getattr(final_review, "decision", "") or "")
    if decision != "corrected":
        return {
            "status": "ignored",
            "reason": "final_review_not_correction",
            "case_id": None,
            "snapshot_id": None,
            "blockers": [],
        }

    evidence = build_final_correction_evidence(
        evaluation=evaluation,
        final_review=final_review,
        mechanism_snapshot=mechanism_snapshot,
        mechanism_fingerprint=mechanism_fingerprint,
        correction_context=correction_context,
    )
    lane = _find_current_lane(
        db,
        category_key=evidence.category_key,
        pipeline_kind=evidence.pipeline_kind,
    )
    blockers: list[str] = []
    qualified, qualification_blockers = qualify_correction(evidence)
    blockers.extend(qualification_blockers)
    if lane is None:
        blockers.append("enabled_lane_missing")
    elif evidence.mechanism_fingerprint != lane.mechanism_fingerprint:
        blockers.append("mechanism_fingerprint_mismatch")
    if blockers or not qualified or lane is None:
        case = _awaiting_evidence_case(
            db,
            evidence=evidence,
            lane=lane,
            blockers=blockers,
            actor=actor,
        )
        return {
            "status": "awaiting_evidence",
            "case_id": case.id,
            "snapshot_id": None,
            "blockers": list(dict.fromkeys(blockers)),
        }

    snapshot = admit_final_correction(db, evidence=evidence, lane=lane)
    return {
        "status": "eligible",
        "case_id": snapshot.case_id,
        "snapshot_id": snapshot.id,
        "blockers": [],
    }
