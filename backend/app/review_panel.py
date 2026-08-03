"""Deterministic blind-panel consensus for human initial review."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import EvaluationResult, HumanReview, ReviewPanel
from .schema_adapter import PRODUCTION_FIELD_KEYS


KEY_FIELD_PATHS = (
    "classification.scope_status",
    "classification.primary_category",
    "image_quality.quality_severity",
    "media_form",
    *(f"production_fields.{key}" for key in PRODUCTION_FIELD_KEYS),
)


class ReviewPanelRevisionConflict(RuntimeError):
    """The caller tried to mutate a panel revision that no longer exists."""


def claim_review_panel_revision(
    db: Session,
    *,
    panel_id: int,
    expected_revision: int,
) -> int:
    """Atomically reserve the next panel revision for one write transaction."""
    next_revision = expected_revision + 1
    claimed = db.execute(
        update(ReviewPanel)
        .where(
            ReviewPanel.id == panel_id,
            ReviewPanel.revision == expected_revision,
        )
        .values(revision=next_revision)
        .execution_options(synchronize_session="fetch")
    )
    if claimed.rowcount != 1:
        raise ReviewPanelRevisionConflict
    return next_revision


def panel_is_ready_to_settle(
    *,
    submitted_count: int,
    required_reviewers: int,
) -> bool:
    """Keep the current all-frozen-seats settlement rule in one switch point."""
    # Owner 待决：A=达到提前严格多数即结算；B=收齐全部冻结席位后结算。
    # 当前继续执行 B，禁止在产品拍板前改变结算时机。
    return submitted_count >= required_reviewers


def _model_truth(evaluation: EvaluationResult) -> dict[str, Any]:
    precheck = json.loads(evaluation.precheck_json or "{}")
    aesthetic = json.loads(evaluation.aesthetic_json or "{}")
    dimensions = {
        key: value.get("grade")
        for key, value in (aesthetic.get("dimensions") or {}).items()
        if isinstance(value, dict) and isinstance(value.get("grade"), int)
    }
    def nested(path: str) -> Any:
        value: Any = precheck
        for key in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value
    return {
        "dimensions": dimensions,
        "key_fields": {path: nested(path) for path in KEY_FIELD_PATHS},
        "level": evaluation.level,
        "score": evaluation.score,
    }


def review_truth(
    evaluation: EvaluationResult,
    *,
    decision: str,
    corrected_level: str | None,
    corrected_score: float | None,
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expand a sparse review into a complete vote-safe truth."""
    truth = _model_truth(evaluation)
    normalized_corrections: list[dict[str, Any]] = []
    for correction in corrections:
        target_type = correction.get("target_type")
        field_key = str(correction.get("field_key") or "")
        human_value = correction.get("human_value")
        if target_type == "dimension":
            truth["dimensions"][field_key] = human_value
        elif target_type == "key_field":
            truth["key_fields"][field_key] = human_value
        normalized_corrections.append(dict(correction))
    truth["decision"] = decision
    truth["level"] = corrected_level or truth["level"]
    truth["score"] = (
        corrected_score if corrected_score is not None else truth["score"]
    )
    truth["corrections"] = normalized_corrections
    return truth


def truth_from_review(
    evaluation: EvaluationResult, review: HumanReview
) -> dict[str, Any]:
    return review_truth(
        evaluation,
        decision=review.decision,
        corrected_level=review.corrected_level,
        corrected_score=review.corrected_score,
        corrections=json.loads(review.corrections_json or "[]"),
    )


def _majority(
    values: Iterable[Any], required_reviewers: int
) -> tuple[bool, Any]:
    serialized = [
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        for value in values
    ]
    if not serialized:
        return False, None
    winner, count = Counter(serialized).most_common(1)[0]
    if count < required_reviewers // 2 + 1:
        return False, None
    return True, json.loads(winner)


def resolve_panel_consensus(
    evaluation: EvaluationResult,
    reviews: list[HumanReview],
    *,
    required_reviewers: int,
) -> dict[str, Any]:
    """Return collecting, lead_adjudication, or a complete field consensus."""
    if not panel_is_ready_to_settle(
        submitted_count=len(reviews),
        required_reviewers=required_reviewers,
    ):
        return {
            "status": "collecting",
            "submitted_count": len(reviews),
            "required_reviewers": required_reviewers,
        }
    votes = [
        truth_from_review(evaluation, review)
        for review in reviews[:required_reviewers]
    ]
    model = _model_truth(evaluation)
    dimension_keys = sorted(
        {
            key
            for vote in votes
            for key in vote["dimensions"]
        }
        | set(model["dimensions"])
    )
    key_field_keys = sorted(
        {
            key
            for vote in votes
            for key in vote["key_fields"]
        }
        | set(KEY_FIELD_PATHS)
    )
    dimensions: dict[str, Any] = {}
    key_fields: dict[str, Any] = {}
    unresolved: list[str] = []
    for key in dimension_keys:
        resolved, value = _majority(
            [vote["dimensions"].get(key) for vote in votes],
            required_reviewers,
        )
        if not resolved:
            unresolved.append(f"dimension:{key}")
        else:
            dimensions[key] = value
    for key in key_field_keys:
        resolved, value = _majority(
            [vote["key_fields"].get(key) for vote in votes],
            required_reviewers,
        )
        if not resolved:
            unresolved.append(f"key_field:{key}")
        else:
            key_fields[key] = value
    decision_resolved, decision = _majority(
        [vote["decision"] for vote in votes],
        required_reviewers,
    )
    if not decision_resolved:
        unresolved.append("decision")
    if unresolved:
        return {
            "status": "lead_adjudication",
            "submitted_count": len(reviews),
            "required_reviewers": required_reviewers,
            "unresolved_fields": unresolved,
        }

    consensus_corrections: list[dict[str, Any]] = []
    for target_type, resolved_values, model_values in (
        ("dimension", dimensions, model["dimensions"]),
        ("key_field", key_fields, model["key_fields"]),
    ):
        for field_key, human_value in resolved_values.items():
            model_value = model_values.get(field_key)
            if human_value == model_value:
                continue
            supporting = [
                correction
                for vote in votes
                for correction in vote["corrections"]
                if correction.get("target_type") == target_type
                and correction.get("field_key") == field_key
                and correction.get("human_value") == human_value
            ]
            reason_codes = sorted(
                {
                    str(code)
                    for correction in supporting
                    for code in (correction.get("reason_codes") or [])
                    if str(code).strip()
                }
            )
            notes = [
                str(correction.get("note") or "").strip()
                for correction in supporting
                if str(correction.get("note") or "").strip()
            ]
            consensus_corrections.append(
                {
                    "target_type": target_type,
                    "field_key": field_key,
                    "model_value": model_value,
                    "human_value": human_value,
                    "reason_codes": reason_codes or ["panel_majority"],
                    "note": "；".join(dict.fromkeys(notes))[:1000],
                }
            )
    if decision == "approved" and consensus_corrections:
        decision = "corrected"
    return {
        "status": "completed",
        "submitted_count": len(reviews),
        "required_reviewers": required_reviewers,
        "decision": decision,
        "dimensions": dimensions,
        "key_fields": key_fields,
        "corrections": consensus_corrections,
    }
