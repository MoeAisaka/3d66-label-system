"""Immutable prompt-version accuracy calculations over frozen evaluations."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import EvaluationResult, HumanReview, ReviewPanel


def frozen_task_set_hash(evaluation_ids: list[int]) -> str:
    canonical = json.dumps(
        sorted(evaluation_ids),
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def final_review(
    result: EvaluationResult,
    panels_by_evaluation: dict[int, ReviewPanel],
    reviews_by_id: dict[int, HumanReview],
) -> HumanReview | None:
    panel = panels_by_evaluation.get(result.id)
    if panel is not None:
        if panel.status != "completed" or panel.final_review_id is None:
            return None
        return reviews_by_id.get(panel.final_review_id)
    if result.review_stage != "completed":
        return None
    legacy = [
        review
        for review in result.reviews
        if review.panel_id is None
    ]
    return legacy[-1] if legacy else None


def calculate_prompt_metrics(
    results: list[EvaluationResult],
    *,
    panels_by_evaluation: dict[int, ReviewPanel],
    reviews_by_id: dict[int, HumanReview],
) -> dict[str, Any]:
    if not results:
        raise ValueError("冻结任务集至少需要一条评测结果")
    reviewed: list[tuple[EvaluationResult, HumanReview]] = []
    for result in results:
        review = final_review(
            result,
            panels_by_evaluation,
            reviews_by_id,
        )
        if review is not None:
            reviewed.append((result, review))

    corrected_sample_count = sum(
        1 for _result, review in reviewed
        if review.decision != "approved"
    )
    reviewed_count = len(reviewed)
    sample_accuracy = (
        1 - corrected_sample_count / reviewed_count
        if reviewed_count
        else None
    )
    grade_correct = sum(
        1
        for result, review in reviewed
        if (
            review.corrected_level
            if review.decision == "corrected"
            and review.corrected_level is not None
            else result.level
        )
        == result.level
    )
    dimension_reviewed: dict[str, int] = {}
    dimension_corrected: dict[str, int] = {}
    for result, review in reviewed:
        aesthetic = json.loads(result.aesthetic_json or "{}")
        dimension_keys = set((aesthetic.get("dimensions") or {}).keys())
        for key in dimension_keys:
            dimension_reviewed[key] = dimension_reviewed.get(key, 0) + 1
        for correction in json.loads(review.corrections_json or "[]"):
            if correction.get("target_type") != "dimension":
                continue
            key = str(correction.get("field_key") or "")
            if key in dimension_keys:
                dimension_corrected[key] = (
                    dimension_corrected.get(key, 0) + 1
                )
    dimension_accuracy = {
        key: 1 - dimension_corrected.get(key, 0) / count
        for key, count in sorted(dimension_reviewed.items())
        if count
    }
    return {
        "schema_version": "prompt-accuracy-v1",
        "N": len(results),
        "reviewed_sample_count": reviewed_count,
        "corrected_sample_count": corrected_sample_count,
        "sample_accuracy": sample_accuracy,
        "dimension_accuracy": dimension_accuracy,
        "grade_accuracy": (
            grade_correct / reviewed_count if reviewed_count else None
        ),
        "review_coverage": reviewed_count / len(results),
        "unreviewed_count": len(results) - reviewed_count,
        "denominator_policy": "completed_human_initial_review_only",
    }
