from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .dimension_schema_registry import canonical_hash
from .models import (
    EvaluationResult,
    HumanReview,
    PromptRegressionItem,
    PromptRegressionRun,
    AutomationOptimizationRun,
    PromptVersion,
    SampleSetItem,
)
from .scoring import (
    DimensionScoringContractError,
    dimension_schema_from_strategy_snapshot,
    normalize_dimension_aliases,
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

MEDIA_TYPE_KEYS = (
    "real_photo",
    "rendering",
    "ai_generated",
    "collage_or_multiview",
    "unfinished_scene",
    "white_background_product",
)

SHOOTING_METHOD_KEYS = (
    "professional_photography",
    "casual_snapshot",
    "documentary_record",
)

SAMPLE_ROLES = (
    "target_error",
    "stable_control",
    "blind_holdout",
)

_CAP_RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5, "none": 6}

_CRITICAL_FIELD_PREFIXES = (
    "dimension_schema.",
    "scope_status",
    "primary_category",
    "media_type.",
    "shooting_method.",
    "quality_severity",
    "hard_gate.",
    "level_cap",
    "level",
)

_SEVERE_ERROR_LEVELS = {
    "out_of_scope_misrelease": "P0",
    "severe_damage_auto_pass": "P0",
    "low_grade_raised_l4_l5": "P1",
    "hard_gate_missed": "P0",
    "level_cap_missed": "P0",
}

_SEVERE_ERROR_FIELDS = {
    "out_of_scope_misrelease": {"scope_status", "level"},
    "severe_damage_auto_pass": {"quality_severity", "level"},
    "low_grade_raised_l4_l5": {"level"},
    "hard_gate_missed": {"hard_gate.triggered", "hard_gate.target"},
    "level_cap_missed": {"level_cap", "level"},
}

_FAILURE_MESSAGES = {
    "target_error_not_improved": "目标错例没有改善",
    "critical_field_regression": "候选在关键字段发生回退",
    "out_of_scope_misrelease": "新增范围外素材误放",
    "severe_damage_auto_pass": "新增严重损坏素材自动通过",
    "low_grade_raised_l4_l5": "新增低等级错误升至 L4/L5",
    "hard_gate_missed": "新增硬门槛漏判",
    "level_cap_missed": "新增等级封顶漏判",
    "comparison_error": "样本配对比较失败",
    "dimension_schema_mismatch": "比较结果使用了不同的维度规则",
}


def paired_gate_policy() -> dict[str, Any]:
    """Return the explicit policy snapshot persisted with every paired run."""
    return {
        "critical_fields_policy": "paired-critical-fields-v1",
        "critical_field_prefixes": list(_CRITICAL_FIELD_PREFIXES),
        "severe_error_policy": "paired-p0-p1-v1",
        "blocking_error_levels": dict(_SEVERE_ERROR_LEVELS),
    }


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _media_statuses(precheck: dict[str, Any], keys: tuple[str, ...]) -> dict[str, str]:
    media = precheck.get("media_form") or {}
    statuses: dict[str, str] = {}
    for key in keys:
        status = (media.get(key) or {}).get("status")
        if status in {"yes", "no", "uncertain"}:
            statuses[key] = status
    return statuses


def _effective_level_cap(
    aesthetic: dict[str, Any], scoring: dict[str, Any]
) -> str:
    candidates: list[str] = []
    declared = str(
        ((aesthetic.get("decision_rules") or {}).get("level_cap") or "none")
    )
    if declared in _CAP_RANK:
        candidates.append(declared)
    for cap in scoring.get("caps") or []:
        value = str((cap or {}).get("cap") or "")
        if value in _CAP_RANK:
            candidates.append(value)
    return min(candidates, key=lambda value: _CAP_RANK[value]) if candidates else "none"


def dimension_contract_for_result(
    result: EvaluationResult,
) -> tuple[dict[str, Any], tuple[str, ...], dict[str, Any] | None]:
    """Resolve the exact dimension definition and optional v2 identity."""
    aesthetic = _loads(result.aesthetic_json, {})
    definition = dimension_schema_from_strategy_snapshot(
        result.strategy_snapshot_json,
        aesthetic=aesthetic,
    )
    output_contract = definition.get("output_contract")
    dimension_keys = (
        output_contract.get("dimension_output_keys")
        if isinstance(output_contract, dict)
        else None
    )
    if (
        not isinstance(dimension_keys, list)
        or not dimension_keys
        or len(dimension_keys) != len(set(dimension_keys))
        or not all(
            isinstance(key, str) and key for key in dimension_keys
        )
    ):
        raise DimensionScoringContractError(
            "DimensionSchema 输出维度合同不完整"
        )

    payload = _loads(result.strategy_snapshot_json, {})
    if payload.get("schema_version") != "strategy-bundle-v2":
        return definition, tuple(dimension_keys), None

    identity_fields = {
        "schema_id": payload.get("resolved_dimension_schema_id"),
        "schema_key": payload.get("resolved_dimension_schema_key"),
        "version": payload.get("resolved_dimension_schema_version"),
        "canonical_hash": payload.get("resolved_dimension_schema_hash"),
    }
    if (
        not isinstance(identity_fields["schema_id"], int)
        or identity_fields["schema_id"] <= 0
        or not all(
            isinstance(identity_fields[key], str)
            and bool(identity_fields[key])
            for key in ("schema_key", "version")
        )
        or not isinstance(identity_fields["canonical_hash"], str)
        or len(identity_fields["canonical_hash"]) != 64
    ):
        raise DimensionScoringContractError(
            "结果策略快照缺少完整 DimensionSchema 身份"
        )
    identity = {
        "binding_version": "dimension-truth-binding-v1",
        **identity_fields,
        "definition": definition,
    }
    return definition, tuple(dimension_keys), identity


def result_fields(result: EvaluationResult) -> dict[str, Any]:
    """Return the auditable field contract used by paired regression."""
    precheck = _loads(result.precheck_json, {})
    aesthetic = _loads(result.aesthetic_json, {})
    scoring = _loads(result.scoring_json, {})
    _, dimension_keys, dimension_identity = (
        dimension_contract_for_result(result)
    )
    classification = precheck.get("classification") or {}
    quality = precheck.get("image_quality") or {}
    decision_rules = aesthetic.get("decision_rules") or {}
    dimensions: dict[str, int] = {}
    raw_dimensions = aesthetic.get("dimensions") or {}
    if not isinstance(raw_dimensions, dict):
        raw_dimensions = {}
    normalized_dimensions = normalize_dimension_aliases(
        raw_dimensions,
        dimension_keys,
    )
    for key in dimension_keys:
        grade = (normalized_dimensions.get(key) or {}).get("grade")
        if isinstance(grade, int) and 1 <= grade <= 5:
            dimensions[key] = grade
    return {
        "dimension_schema": dimension_identity,
        "scope_status": classification.get("scope_status") or "out_of_scope",
        "primary_category": classification.get("primary_category") or "无法判断",
        "media_type": _media_statuses(precheck, MEDIA_TYPE_KEYS),
        "shooting_method": _media_statuses(precheck, SHOOTING_METHOD_KEYS),
        "quality_severity": quality.get("quality_severity") or "uncertain",
        "hard_gate": {
            "triggered": decision_rules.get("hard_gate_triggered") is True,
            "target": str(decision_rules.get("hard_gate_target") or "none"),
        },
        "level_cap": _effective_level_cap(aesthetic, scoring),
        "dimensions": dimensions,
        "level": result.level,
        "score": result.score,
        "formal": bool(scoring.get("formal", result.level is not None)),
        "needs_review": bool(result.needs_review),
    }


def latest_review_for_result(
    result: EvaluationResult,
) -> HumanReview | None:
    if not result.reviews:
        return None
    latest = max(
        result.reviews,
        key=lambda review: (
            (
                review.created_at
                if review.created_at.tzinfo
                else review.created_at.replace(tzinfo=timezone.utc)
            ),
            review.id or 0,
        ),
    )
    if result.review_stage == "completed":
        return latest
    # Migration 19 marks persisted legacy approved/corrected reviews completed.
    # This narrow fallback keeps detached legacy/test objects readable without
    # allowing a new in-progress staged chain (revision > 0) to form Gold.
    if (
        result.review_revision == 0
        and latest.decision in {"approved", "corrected"}
    ):
        return latest
    return None


def truth_from_result(result: EvaluationResult, expected_level: str | None = None) -> dict[str, Any]:
    """Freeze the reviewed result into a complete, model-independent reference label."""
    fields = result_fields(result)
    latest_review = latest_review_for_result(result)
    level = expected_level or result.level
    if latest_review and latest_review.decision == "corrected":
        level = latest_review.corrected_level or level

    dimensions = dict(fields["dimensions"])
    _, bound_dimension_keys, _ = dimension_contract_for_result(result)
    dimension_keys = set(bound_dimension_keys)
    if latest_review:
        for correction in _loads(latest_review.corrections_json, []):
            if correction.get("target_type") != "dimension":
                continue
            key = str(correction.get("field_key") or "")
            value = correction.get("human_value")
            if (
                key in dimension_keys
                and isinstance(value, int)
                and 1 <= value <= 5
            ):
                dimensions[key] = value

    media_form = {
        **fields["media_type"],
        **fields["shooting_method"],
    }
    truth = {
        "level": level,
        "scope_status": fields["scope_status"],
        "category": fields["primary_category"],
        "primary_category": fields["primary_category"],
        "quality_severity": fields["quality_severity"],
        "media_form": media_form,
        "media_type": fields["media_type"],
        "shooting_method": fields["shooting_method"],
        "hard_gate": fields["hard_gate"],
        "level_cap": fields["level_cap"],
        "dimensions": dimensions,
    }
    if fields["dimension_schema"] is not None:
        truth["dimension_schema"] = fields["dimension_schema"]
    return truth


def reviewed_truth_snapshot(
    result: EvaluationResult, role: str
) -> tuple[dict[str, Any], HumanReview]:
    """Freeze truth only when the concrete evaluation has current human evidence."""
    if role not in SAMPLE_ROLES:
        raise ValueError("样本角色无效")
    review = latest_review_for_result(result)
    if not review or review.decision not in {"approved", "corrected"}:
        raise ValueError(
            f"evaluation_id={result.id} 未经人工确认，不能成为回归真值"
        )
    if role == "target_error" and review.decision != "corrected":
        raise ValueError("target_error 必须来自人工纠偏结果")
    if role == "stable_control" and review.decision != "approved":
        raise ValueError("stable_control 必须来自人工确认正确的结果")
    truth = truth_from_result(result)
    snapshot = {
        "schema_version": (
            "paired-truth-v2"
            if truth.get("dimension_schema") is not None
            else "paired-truth-v1"
        ),
        "truth": truth,
        "source": {
            "evaluation_id": result.id,
            "review_id": review.id,
            "reviewer_name": review.reviewer_name,
            "decision": review.decision,
            "corrected_level": review.corrected_level,
            "corrected_score": review.corrected_score,
            "corrections": _loads(review.corrections_json, []),
            "note": review.note,
            "reviewed_at": review.created_at.isoformat(),
        },
    }
    return snapshot, review


def _is_critical(field: str) -> bool:
    return field.startswith(_CRITICAL_FIELD_PREFIXES)


def _flatten_contract_fields(fields: dict[str, Any]) -> dict[str, Any]:
    flattened = {
        "scope_status": fields.get("scope_status"),
        "primary_category": fields.get("primary_category")
        or fields.get("category"),
        "quality_severity": fields.get("quality_severity"),
        "hard_gate.triggered": (fields.get("hard_gate") or {}).get("triggered"),
        "hard_gate.target": (fields.get("hard_gate") or {}).get("target"),
        "level_cap": fields.get("level_cap"),
        "level": fields.get("level"),
    }
    for group in ("media_type", "shooting_method", "dimensions"):
        for key, value in (fields.get(group) or {}).items():
            flattened[f"{group}.{key}"] = value
    dimension_identity = fields.get("dimension_schema")
    if isinstance(dimension_identity, dict):
        for key in ("schema_id", "schema_key", "version", "canonical_hash"):
            flattened[f"dimension_schema.{key}"] = dimension_identity.get(key)
    return flattened


def _dimension_schema_mismatches(
    truth: dict[str, Any],
    baseline_fields: dict[str, Any],
    candidate_fields: dict[str, Any],
) -> list[str]:
    expected = truth.get("dimension_schema")
    if not isinstance(expected, dict):
        return []
    mismatches: list[str] = []
    for side, fields in (
        ("baseline", baseline_fields),
        ("candidate", candidate_fields),
    ):
        actual = fields.get("dimension_schema")
        for key in ("schema_id", "schema_key", "version", "canonical_hash"):
            if (
                not isinstance(actual, dict)
                or actual.get(key) != expected.get(key)
            ):
                mismatches.append(f"{side}.dimension_schema.{key}")
    return mismatches


def _validate_truth_dimension_identity(
    truth: dict[str, Any],
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    identity = truth.get("dimension_schema")
    if identity is None and not required:
        return None
    if not isinstance(identity, dict):
        raise DimensionScoringContractError(
            "回归真值缺少 DimensionSchema 身份"
        )
    definition = identity.get("definition")
    expected_hash = identity.get("canonical_hash")
    if (
        not isinstance(identity.get("schema_id"), int)
        or identity["schema_id"] <= 0
        or not isinstance(identity.get("schema_key"), str)
        or not identity["schema_key"]
        or not isinstance(identity.get("version"), str)
        or not identity["version"]
        or not isinstance(definition, dict)
        or not isinstance(expected_hash, str)
        or canonical_hash(definition) != expected_hash
    ):
        raise DimensionScoringContractError(
            "回归真值的 DimensionSchema 身份无法复算"
        )
    return identity


def _assessment(truth: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    expected = _flatten_contract_fields(truth)
    actual = _flatten_contract_fields(fields)
    checks: list[dict[str, Any]] = []
    for field, expected_value in expected.items():
        if expected_value is None or expected_value == "":
            continue
        actual_value = actual.get(field)
        checks.append(
            {
                "field": field,
                "expected": expected_value,
                "actual": actual_value,
                "passed": actual_value == expected_value,
                "critical": _is_critical(field),
            }
        )
    dimension_checks = [
        check for check in checks if check["field"].startswith("dimensions.")
    ]
    return {
        "checks": checks,
        "error_count": sum(1 for check in checks if not check["passed"]),
        "critical_error_count": sum(
            1 for check in checks if check["critical"] and not check["passed"]
        ),
        "aesthetic_correct": sum(1 for check in dimension_checks if check["passed"]),
        "aesthetic_checked": len(dimension_checks),
        "whole_image_correct": bool(checks)
        and all(check["passed"] for check in checks),
        "level_consistent": fields.get("level") == truth.get("level"),
    }


def _severe_errors(
    truth: dict[str, Any], fields: dict[str, Any]
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def add(code: str) -> None:
        errors.append({"code": code, "severity": _SEVERE_ERROR_LEVELS[code]})

    if truth.get("scope_status") == "out_of_scope" and (
        fields.get("scope_status") != "out_of_scope"
        or fields.get("formal") is True
        or fields.get("level") is not None
    ):
        add("out_of_scope_misrelease")
    if truth.get("quality_severity") in {"severe", "unusable"} and (
        fields.get("level") not in {None, "L1"}
        or fields.get("needs_review") is False
    ):
        add("severe_damage_auto_pass")
    if truth.get("level") in {"L1", "L2", "L3"} and fields.get("level") in {
        "L4",
        "L5",
    }:
        add("low_grade_raised_l4_l5")

    truth_gate = truth.get("hard_gate") or {}
    actual_gate = fields.get("hard_gate") or {}
    if truth_gate.get("triggered") is True and (
        actual_gate.get("triggered") is not True
        or (
            truth_gate.get("target") not in {None, "", "none"}
            and actual_gate.get("target") != truth_gate.get("target")
        )
    ):
        add("hard_gate_missed")

    truth_cap = str(truth.get("level_cap") or "none")
    actual_cap = str(fields.get("level_cap") or "none")
    actual_level = str(fields.get("level") or "none")
    if truth_cap in {"L1", "L2", "L3", "L4"} and (
        _CAP_RANK.get(actual_cap, 6) > _CAP_RANK[truth_cap]
        or (
            actual_level in _CAP_RANK
            and _CAP_RANK[actual_level] > _CAP_RANK[truth_cap]
        )
    ):
        add("level_cap_missed")
    return errors


def compare_paired_results(
    *,
    truth_snapshot: dict[str, Any],
    role: str,
    baseline: EvaluationResult,
    candidate: EvaluationResult,
) -> dict[str, Any]:
    """Compare two immutable strategy results against one frozen human truth."""
    truth = truth_snapshot.get("truth") or {}
    truth_version = (
        truth_snapshot.get("schema_version")
        or "paired-truth-v1"
    )
    if truth_version not in {"paired-truth-v1", "paired-truth-v2"}:
        raise ValueError("配对回归真值版本不受支持")
    _validate_truth_dimension_identity(
        truth,
        required=truth_version == "paired-truth-v2",
    )
    baseline_fields = result_fields(baseline)
    candidate_fields = result_fields(candidate)
    schema_mismatches = _dimension_schema_mismatches(
        truth,
        baseline_fields,
        candidate_fields,
    )
    baseline_assessment = _assessment(truth, baseline_fields)
    candidate_assessment = _assessment(truth, candidate_fields)
    baseline_severe = {
        item["code"] for item in _severe_errors(truth, baseline_fields)
    }
    candidate_severe = _severe_errors(truth, candidate_fields)
    new_severe = [
        item for item in candidate_severe if item["code"] not in baseline_severe
    ]
    new_severe_codes = {item["code"] for item in new_severe}
    candidate_checks = {
        check["field"]: check for check in candidate_assessment["checks"]
    }
    diffs = []
    critical_regressions: list[str] = []
    for baseline_check in baseline_assessment["checks"]:
        candidate_check = candidate_checks.get(baseline_check["field"])
        if candidate_check is None:
            continue
        if (
            baseline_check["critical"]
            and baseline_check["passed"]
            and not candidate_check["passed"]
        ):
            critical_regressions.append(baseline_check["field"])
        improved = not baseline_check["passed"] and candidate_check["passed"]
        regressed = baseline_check["passed"] and not candidate_check["passed"]
        diffs.append(
            {
                "field": baseline_check["field"],
                "expected": baseline_check["expected"],
                "baseline": baseline_check["actual"],
                "candidate": candidate_check["actual"],
                "baseline_passed": baseline_check["passed"],
                "candidate_passed": candidate_check["passed"],
                "improved": improved,
                "regressed": regressed,
                "change": (
                    "improved"
                    if improved
                    else "regressed"
                    if regressed
                    else "unchanged"
                ),
                "severe_error_codes": sorted(
                    code
                    for code in new_severe_codes
                    if baseline_check["field"] in _SEVERE_ERROR_FIELDS[code]
                ),
            }
        )

    target_improved = (
        candidate_assessment["error_count"] < baseline_assessment["error_count"]
        if role == "target_error"
        else None
    )
    item_passed = (
        not schema_mismatches
        and not critical_regressions
        and not new_severe
        and (target_improved is not False)
    )
    failure_reasons: list[dict[str, Any]] = []
    if schema_mismatches:
        failure_reasons.append(
            {
                "code": "dimension_schema_mismatch",
                "severity": "P0",
                "fields": schema_mismatches,
                "message": _FAILURE_MESSAGES["dimension_schema_mismatch"],
            }
        )
    if target_improved is False:
        failure_reasons.append(
            {
                "code": "target_error_not_improved",
                "severity": "P1",
                "fields": [],
                "message": _FAILURE_MESSAGES["target_error_not_improved"],
            }
        )
    if critical_regressions:
        failure_reasons.append(
            {
                "code": "critical_field_regression",
                "severity": "P1",
                "fields": critical_regressions,
                "message": _FAILURE_MESSAGES["critical_field_regression"],
            }
        )
    failure_reasons.extend(
        {
            "code": error["code"],
            "severity": error["severity"],
            "fields": sorted(_SEVERE_ERROR_FIELDS[error["code"]]),
            "message": _FAILURE_MESSAGES[error["code"]],
        }
        for error in new_severe
    )
    return {
        "schema_version": "paired-comparison-v1",
        "sample_role": role,
        "truth_source": truth_snapshot.get("source") or {},
        "baseline": {
            "evaluation_id": baseline.id,
            "strategy_bundle_id": baseline.strategy_bundle_id,
            "fields": baseline_fields,
            "assessment": baseline_assessment,
        },
        "candidate": {
            "evaluation_id": candidate.id,
            "strategy_bundle_id": candidate.strategy_bundle_id,
            "fields": candidate_fields,
            "assessment": candidate_assessment,
        },
        "diffs": diffs,
        "target_error_improved": target_improved,
        "critical_regressions": critical_regressions,
        "dimension_schema_mismatches": schema_mismatches,
        "new_severe_errors": new_severe,
        "passed": item_passed,
        "failed": not item_passed,
        "failure_reasons": failure_reasons,
    }


def _aggregate_pair_metrics(
    comparisons: list[dict[str, Any]], side: str
) -> dict[str, float | int | None]:
    aesthetic_correct = 0
    aesthetic_checked = 0
    whole_correct = 0
    level_consistent = 0
    for comparison in comparisons:
        assessment = comparison[side]["assessment"]
        aesthetic_correct += assessment["aesthetic_correct"]
        aesthetic_checked += assessment["aesthetic_checked"]
        whole_correct += int(assessment["whole_image_correct"])
        level_consistent += int(assessment["level_consistent"])
    total = len(comparisons)
    return {
        "aesthetic_correct": aesthetic_correct,
        "aesthetic_checked": aesthetic_checked,
        "aesthetic_accuracy": (
            round(aesthetic_correct / aesthetic_checked, 4)
            if aesthetic_checked
            else None
        ),
        "whole_image_correct": whole_correct,
        "whole_image_checked": total,
        "whole_image_accuracy": round(whole_correct / total, 4) if total else None,
        "level_consistent": level_consistent,
        "level_checked": total,
        "level_consistency": round(level_consistent / total, 4) if total else None,
    }


def _error_item_summary(
    item: PromptRegressionItem, comparison: dict[str, Any]
) -> dict[str, Any]:
    reasons = comparison.get("failure_reasons") or []
    if item.status == "error":
        reasons = [
            {
                "code": "comparison_error",
                "severity": "P0",
                "fields": [],
                "message": str(
                    comparison.get("error")
                    or _FAILURE_MESSAGES["comparison_error"]
                ),
            }
        ]
    severity_order = {"P0": 0, "P1": 1, "P2": 2}
    severities = [
        str(reason.get("severity") or "P1") for reason in reasons
    ]
    highest_severity = min(
        severities or ["P1"],
        key=lambda severity: severity_order.get(severity, 99),
    )
    asset = item.sample_item.asset
    return {
        "item_id": item.id,
        "sample_item_id": item.sample_item_id,
        "asset_id": item.sample_item.asset_id,
        "asset_name": asset.original_name,
        "image_url": f"/api/assets/{item.sample_item.asset_id}/file",
        "sample_role": item.sample_role,
        "status": item.status,
        "passed": item.passed,
        "baseline_evaluation_id": item.baseline_evaluation_id,
        "candidate_evaluation_id": item.candidate_evaluation_id,
        "severity": highest_severity,
        "failure_reasons": reasons,
        "critical_regressions": (
            comparison.get("critical_regressions") or []
        ),
        "new_severe_errors": comparison.get("new_severe_errors") or [],
    }


def refresh_paired_regression_run(
    db: Session, run: PromptRegressionRun
) -> None:
    if run.regression_mode != "paired":
        raise ValueError("该回归任务不是配对策略回归")
    items = db.scalars(
        select(PromptRegressionItem).where(PromptRegressionItem.run_id == run.id)
    ).all()
    terminal = {"completed", "error"}
    run.total = len(items)
    run.completed = sum(1 for item in items if item.status in terminal)
    blind_holdout_revealed = run.total > 0 and run.completed == run.total
    visible_items = [
        item
        for item in items
        if blind_holdout_revealed or item.sample_role != "blind_holdout"
    ]
    run.passed = sum(1 for item in visible_items if item.passed is True)
    run.failed = sum(
        1
        for item in visible_items
        if item.status == "error" or item.passed is False
    )
    completed_items = [item for item in items if item.status == "completed"]
    visible_completed_items = [
        item
        for item in completed_items
        if blind_holdout_revealed or item.sample_role != "blind_holdout"
    ]
    visible_comparisons = [
        _loads(item.comparison_json, {}) for item in visible_completed_items
    ]
    rules = _loads(run.metric_rules_json, {})
    thresholds = rules.get("thresholds") or {}
    error_items = [
        _error_item_summary(item, _loads(item.comparison_json, {}))
        for item in visible_items
        if item.status == "error" or item.passed is False
    ]
    summary: dict[str, Any] = {
        "schema_version": "paired-regression-summary-v1",
        "sample_set_version": run.sample_set_version,
        "metric_rules_version": run.metric_rules_version,
        "thresholds": thresholds,
        "blind_holdout_revealed": blind_holdout_revealed,
        "role_counts": {
            role: sum(1 for item in items if item.sample_role == role)
            for role in SAMPLE_ROLES
        },
        "outcome_counts": {
            "passed": sum(
                1 for item in visible_items if item.passed is True
            ),
            "failed": len(error_items),
            "pending": sum(
                1
                for item in items
                if (
                    not blind_holdout_revealed
                    and item.sample_role == "blind_holdout"
                )
                or item.status not in terminal
            ),
        },
        "error_items": error_items,
        "failed_item_ids": [item["item_id"] for item in error_items],
        "baseline": _aggregate_pair_metrics(
            visible_comparisons, "baseline"
        ),
        "candidate": _aggregate_pair_metrics(
            visible_comparisons, "candidate"
        ),
        "gate_checks": [],
    }
    if run.completed < run.total:
        run.status = "waiting_results"
        run.recommendation = "pending"
        run.summary_json = json.dumps(summary, ensure_ascii=False)
        run.metrics_json = run.summary_json
        return

    comparisons = [_loads(item.comparison_json, {}) for item in completed_items]
    target_failures = [
        item.id
        for item, comparison in zip(completed_items, comparisons, strict=True)
        if item.sample_role == "target_error"
        and comparison.get("target_error_improved") is not True
    ]
    critical_regressions = [
        {"item_id": item.id, "fields": comparison.get("critical_regressions") or []}
        for item, comparison in zip(completed_items, comparisons, strict=True)
        if comparison.get("critical_regressions")
    ]
    severe_errors = [
        {"item_id": item.id, "errors": comparison.get("new_severe_errors") or []}
        for item, comparison in zip(completed_items, comparisons, strict=True)
        if comparison.get("new_severe_errors")
    ]
    gate_checks: list[dict[str, Any]] = [
        {
            "gate": "target_errors_improve",
            "passed": not target_failures,
            "failed_item_ids": target_failures,
        },
        {
            "gate": "critical_fields_zero_regression",
            "passed": not critical_regressions,
            "regressions": critical_regressions,
        },
        {
            "gate": "no_new_p0_p1_errors",
            "passed": not severe_errors,
            "errors": severe_errors,
        },
    ]
    metric_contract = (
        ("aesthetic_accuracy", "aesthetic_accuracy_max_drop"),
        ("whole_image_accuracy", "whole_image_accuracy_max_drop"),
        ("level_consistency", "level_consistency_max_drop"),
    )
    for metric, threshold_key in metric_contract:
        baseline_value = summary["baseline"].get(metric)
        candidate_value = summary["candidate"].get(metric)
        max_drop = thresholds.get(threshold_key)
        if not isinstance(max_drop, (int, float)):
            raise ValueError(f"指标规则缺少显式阈值：{threshold_key}")
        applicable = baseline_value is not None and candidate_value is not None
        drop = (
            round(float(baseline_value) - float(candidate_value), 4)
            if applicable
            else None
        )
        gate_checks.append(
            {
                "gate": f"{metric}_fluctuation",
                "passed": not applicable or (drop is not None and drop <= max_drop),
                "applicable": applicable,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "drop": drop,
                "max_drop": max_drop,
            }
        )
    if any(item.status == "error" for item in items):
        gate_checks.append(
            {
                "gate": "all_samples_compared",
                "passed": False,
                "error_item_ids": [
                    item.id for item in items if item.status == "error"
                ],
            }
        )
    summary["gate_checks"] = gate_checks
    passed = all(check["passed"] for check in gate_checks)
    run.recommendation = "pass" if passed else "fail"
    run.status = "passed" if passed else "regressed"
    run.finished_at = datetime.now(timezone.utc)
    run.summary_json = json.dumps(summary, ensure_ascii=False)
    run.metrics_json = run.summary_json
    _refresh_source_automation_review(db, run)


def complete_paired_regression_item(
    db: Session,
    *,
    item: PromptRegressionItem,
    baseline: EvaluationResult,
    candidate: EvaluationResult,
) -> dict[str, Any]:
    run = item.run
    if run.regression_mode != "paired":
        raise ValueError("该回归项不是配对策略回归")
    if item.status == "completed":
        if (
            item.baseline_evaluation_id == baseline.id
            and item.candidate_evaluation_id == candidate.id
        ):
            return _loads(item.comparison_json, {})
        raise ValueError("已完成的配对结果不可替换")
    if baseline.asset_id != item.sample_item.asset_id or candidate.asset_id != item.sample_item.asset_id:
        raise ValueError("基线与候选结果必须属于冻结样本的同一素材")
    if baseline.strategy_bundle_id != run.baseline_strategy_bundle_id:
        raise ValueError("基线结果与回归任务的 StrategyBundle 不一致")
    if candidate.strategy_bundle_id != run.candidate_strategy_bundle_id:
        raise ValueError("候选结果与回归任务的 StrategyBundle 不一致")
    truth_snapshot = _loads(item.truth_snapshot_json, {})
    comparison = compare_paired_results(
        truth_snapshot=truth_snapshot,
        role=str(item.sample_role),
        baseline=baseline,
        candidate=candidate,
    )
    item.baseline_evaluation_id = baseline.id
    item.candidate_evaluation_id = candidate.id
    item.evaluation_id = candidate.id
    item.baseline_result_json = json.dumps(
        comparison["baseline"], ensure_ascii=False
    )
    item.candidate_result_json = json.dumps(
        comparison["candidate"], ensure_ascii=False
    )
    item.comparison_json = json.dumps(comparison, ensure_ascii=False)
    item.passed = bool(comparison["passed"])
    item.status = "completed"
    item.finished_at = datetime.now(timezone.utc)
    refresh_paired_regression_run(db, run)
    return comparison


def compare_truth(truth: dict[str, Any], result: EvaluationResult) -> dict[str, Any]:
    precheck = _loads(result.precheck_json, {})
    aesthetic = _loads(result.aesthetic_json, {})
    actual_dimensions = aesthetic.get("dimensions") or {}
    actual_fields = result_fields(result)
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
    truth_dimension_schema = _validate_truth_dimension_identity(truth)
    if truth_dimension_schema is not None:
        actual_dimension_schema = actual_fields.get("dimension_schema")
        for key in ("schema_id", "schema_key", "version", "canonical_hash"):
            expected = truth_dimension_schema.get(key)
            actual = (
                actual_dimension_schema.get(key)
                if isinstance(actual_dimension_schema, dict)
                else None
            )
            add_check(
                f"dimension_schema.{key}",
                expected,
                actual,
                expected == actual,
            )
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
        _refresh_source_automation_review(db, run)
    elif run.completed:
        run.status = "running"


def _refresh_source_automation_review(
    db: Session, completed_regression: PromptRegressionRun
) -> None:
    """Advance an optimizer run only after every generated regression settles.

    A single optimizer run can emit several candidate prompts. The first
    candidate finishing is not sufficient evidence for review; the parent run
    remains ``running`` until every regression id recorded in its immutable
    result payload reaches a terminal status.
    """
    if completed_regression.trigger_prompt_id is None:
        return
    source_run = db.scalar(
        select(AutomationOptimizationRun).where(
            AutomationOptimizationRun.id
            == select(PromptVersion.source_automation_run_id)
            .where(PromptVersion.id == completed_regression.trigger_prompt_id)
            .scalar_subquery()
        )
    )
    if source_run is None or source_run.status not in {"succeeded", "running"}:
        return
    _refresh_automation_review_from_source(
        db,
        source_run,
        finished_at=completed_regression.finished_at,
    )


def _refresh_automation_review_from_source(
    db: Session,
    source_run: AutomationOptimizationRun,
    *,
    finished_at: datetime | None = None,
) -> None:
    try:
        payload = json.loads(source_run.result_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    raw_ids = payload.get("regression_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return
    regression_ids = [int(value) for value in raw_ids if str(value).isdigit()]
    if len(regression_ids) != len(raw_ids):
        return
    regressions = db.scalars(
        select(PromptRegressionRun).where(PromptRegressionRun.id.in_(regression_ids))
    ).all()
    terminal = {"passed", "regressed", "failed", "error", "cancelled"}
    if len(regressions) != len(regression_ids) or not all(
        regression.status in terminal for regression in regressions
    ):
        source_run.status = "running"
        return
    source_run.status = "awaiting_release_review"
    terminal_times = [
        regression.finished_at
        for regression in regressions
        if regression.finished_at is not None
    ]
    source_run.finished_at = (
        finished_at
        or max(terminal_times, default=None)
        or datetime.now(timezone.utc)
    )


def reconcile_automation_review_states(db: Session) -> int:
    sources = db.scalars(
        select(AutomationOptimizationRun).where(
            AutomationOptimizationRun.status.in_({"succeeded", "running"})
        )
    ).all()
    advanced = 0
    for source_run in sources:
        previous = source_run.status
        _refresh_automation_review_from_source(db, source_run)
        if (
            previous != "awaiting_release_review"
            and source_run.status == "awaiting_release_review"
        ):
            advanced += 1
    return advanced


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
