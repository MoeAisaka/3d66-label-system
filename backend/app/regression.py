from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    EvaluationResult,
    PromptRegressionItem,
    PromptRegressionRun,
    SampleSetItem,
)


DIMENSION_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)

MEDIA_KEYS = (
    "real_photo",
    "rendering",
    "ai_generated",
    "professional_photography",
    "casual_snapshot",
    "documentary_record",
    "collage_or_multiview",
    "unfinished_scene",
    "white_background_product",
)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def truth_from_result(result: EvaluationResult, expected_level: str | None = None) -> dict[str, Any]:
    """Freeze the reviewed result into a complete, model-independent reference label."""
    precheck = _loads(result.precheck_json, {})
    aesthetic = _loads(result.aesthetic_json, {})
    latest_review = result.reviews[-1] if result.reviews else None
    level = expected_level or result.level
    if latest_review and latest_review.decision == "corrected":
        level = latest_review.corrected_level or level

    dimensions: dict[str, int] = {}
    for key in DIMENSION_KEYS:
        grade = ((aesthetic.get("dimensions") or {}).get(key) or {}).get("grade")
        if isinstance(grade, int) and 1 <= grade <= 5:
            dimensions[key] = grade
    if latest_review:
        for correction in _loads(latest_review.corrections_json, []):
            if correction.get("target_type") != "dimension":
                continue
            key = str(correction.get("field_key") or "")
            value = correction.get("human_value")
            if key in DIMENSION_KEYS and isinstance(value, int) and 1 <= value <= 5:
                dimensions[key] = value

    media_form = {}
    for key in MEDIA_KEYS:
        status = ((precheck.get("media_form") or {}).get(key) or {}).get("status")
        if status in {"yes", "no", "uncertain"}:
            media_form[key] = status

    classification = precheck.get("classification") or {}
    quality = precheck.get("image_quality") or {}
    return {
        "level": level,
        "category": classification.get("primary_category") or "无法判断",
        "quality_severity": quality.get("quality_severity") or "uncertain",
        "media_form": media_form,
        "dimensions": dimensions,
    }


def compare_truth(truth: dict[str, Any], result: EvaluationResult) -> dict[str, Any]:
    precheck = _loads(result.precheck_json, {})
    aesthetic = _loads(result.aesthetic_json, {})
    actual_dimensions = aesthetic.get("dimensions") or {}
    checks: list[dict[str, Any]] = []

    def add_check(field: str, expected: Any, actual: Any, passed: bool, critical: bool = True) -> None:
        if expected is None or expected == "":
            return
        checks.append(
            {
                "field": field,
                "expected": expected,
                "actual": actual,
                "passed": passed,
                "critical": critical,
            }
        )

    add_check("level", truth.get("level"), result.level, truth.get("level") == result.level)
    actual_category = (precheck.get("classification") or {}).get("primary_category")
    add_check(
        "category",
        truth.get("category"),
        actual_category,
        truth.get("category") == actual_category,
        critical=False,
    )
    actual_quality = (precheck.get("image_quality") or {}).get("quality_severity")
    add_check(
        "quality_severity",
        truth.get("quality_severity"),
        actual_quality,
        truth.get("quality_severity") == actual_quality,
    )
    for key, expected in (truth.get("media_form") or {}).items():
        actual = ((precheck.get("media_form") or {}).get(key) or {}).get("status")
        add_check(f"media_form.{key}", expected, actual, expected == actual)

    dimension_deltas: list[int] = []
    for key, expected in (truth.get("dimensions") or {}).items():
        actual = (actual_dimensions.get(key) or {}).get("grade")
        delta = abs(int(actual) - int(expected)) if isinstance(actual, int) else 9
        dimension_deltas.append(delta)
        add_check(f"dimensions.{key}", expected, actual, delta <= 1, critical=False)

    dimension_mae = (
        round(sum(dimension_deltas) / len(dimension_deltas), 3) if dimension_deltas else None
    )
    critical_pass = all(check["passed"] for check in checks if check["critical"])
    dimensions_pass = not dimension_deltas or (
        dimension_mae is not None and dimension_mae <= 0.5 and max(dimension_deltas) <= 1
    )
    passed = bool(checks) and critical_pass and dimensions_pass
    return {
        "passed": passed,
        "checks": checks,
        "dimension_mae": dimension_mae,
        "matched": sum(1 for check in checks if check["passed"]),
        "checked": len(checks),
    }


def refresh_regression_run(db: Session, run: PromptRegressionRun) -> None:
    items = db.scalars(
        select(PromptRegressionItem).where(PromptRegressionItem.run_id == run.id)
    ).all()
    run.total = len(items)
    run.completed = sum(1 for item in items if item.status in {"passed", "failed", "error"})
    run.passed = sum(1 for item in items if item.passed is True)
    run.failed = sum(1 for item in items if item.status in {"failed", "error"})
    pass_rate = round(run.passed / run.completed, 4) if run.completed else 0.0
    run.metrics_json = json.dumps(
        {
            "pass_rate": pass_rate,
            "threshold": run.threshold,
            "release_gate_passed": run.completed == run.total and pass_rate >= run.threshold,
        },
        ensure_ascii=False,
    )
    if run.total and run.completed == run.total:
        run.status = "passed" if pass_rate >= run.threshold else "regressed"
        run.finished_at = datetime.now(timezone.utc)
    elif run.completed:
        run.status = "running"


def complete_regression_item(db: Session, item_id: int, result: EvaluationResult) -> None:
    item = db.get(PromptRegressionItem, item_id)
    if not item:
        return
    sample_item = db.get(SampleSetItem, item.sample_item_id)
    truth = _loads(sample_item.truth_json if sample_item else None, {})
    if not truth and sample_item:
        truth = truth_from_result(sample_item.source_result, sample_item.expected_level)
    comparison = compare_truth(truth, result)
    item.evaluation_id = result.id
    item.passed = comparison["passed"]
    item.status = "passed" if item.passed else "failed"
    item.comparison_json = json.dumps(comparison, ensure_ascii=False)
    item.finished_at = datetime.now(timezone.utc)
    refresh_regression_run(db, item.run)


def fail_regression_item(db: Session, item_id: int, error: str) -> None:
    item = db.get(PromptRegressionItem, item_id)
    if not item:
        return
    item.passed = False
    item.status = "error"
    item.comparison_json = json.dumps({"passed": False, "error": error}, ensure_ascii=False)
    item.finished_at = datetime.now(timezone.utc)
    refresh_regression_run(db, item.run)
