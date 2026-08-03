"""ADR-0033 Phase 3.5 grade→deduction bridge (framework-first, pure function).

This module supplies the one missing link between 调用B and the deterministic
category-evaluation aggregator: it converts 调用B's per-dimension ``grade``
(an integer 1-5) into the ``deductions[dimension_key]`` map that
``category_evaluation_aggregator.aggregate_category_evaluation`` consumes.

Like the rest of the ADR-0033 framework layer it is a **pure function**: no IO,
no network, no database and no model calls.  Output is a fixed-shape,
JSON-serializable, input-stable ``dict`` so the grade→deduction decision is
fully regressible.

The math (per dimension ``key``) is:

- ``share      = weight * dimension_max``                 (the dimension's slice
  of the track's dimension block)
- ``ratio      = (grade_points[grade] - min) / (max - min)``   with
  ``min = grade_points["1"]`` and ``max = grade_points["5"]``; ``ratio`` ∈ [0, 1],
  grade 5 → 1 (full marks, no deduction), grade 1 → 0 (the whole share is lost)
- ``deduction  = share * (1 - ratio)``                    (>= 0, rounded to 4dp)

The sum of deductions is ``dimension_max * (1 - mean(ratio))`` which is naturally
<= ``dimension_max``, matching the aggregator's cumulative clamp semantics.

Out of scope (later phases): worker wiring, the global L-direction migration and
the frontend.  This module imports nothing from those layers.
"""

from __future__ import annotations

import math
from typing import Any


GRADE_BRIDGE_VERSION = "dimension-grade-bridge-v1"

_MIN_GRADE = 1
_MAX_GRADE = 5
_WEIGHT_SUM_TOLERANCE = 1e-9
_DEDUCTION_DIGITS = 4
_EVIDENCE_DIGITS = 6


class DimensionGradeBridgeError(ValueError):
    """Raised when the grade→deduction conversion cannot proceed (fail-closed).

    Carries a stable ``code`` for programmatic branching independent of the
    (localized) message text, matching the ADR-0033 error convention.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _dimension_definitions(schema_definition: Any) -> list[dict[str, Any]]:
    """Return the ``dimensions`` list from a schema definition, fail-closed."""
    if not isinstance(schema_definition, dict):
        raise DimensionGradeBridgeError(
            "schema_not_object", "dimension_schema_definition 必须是对象"
        )
    dimensions = schema_definition.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise DimensionGradeBridgeError(
            "schema_dimensions_missing",
            "dimension_schema_definition.dimensions 必须是非空数组",
        )
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise DimensionGradeBridgeError(
                "schema_dimension_invalid", "每个维度定义必须是对象"
            )
        key = dimension.get("key")
        if not isinstance(key, str) or not key:
            raise DimensionGradeBridgeError(
                "schema_dimension_key_invalid", "维度定义的 key 必须是非空字符串"
            )
    return dimensions


def _resolve_grade_points(
    dimension: dict[str, Any], schema_definition: dict[str, Any]
) -> dict[str, float]:
    """Resolve a dimension's grade_points, falling back to schema-level maps.

    Preference order: the dimension's own ``grade_points`` →
    ``aggregation.grade_points`` → top-level ``grade_points``.  The resolved
    map must contain numeric points for grades "1".."5" with
    ``max_points > min_points``; anything else fails closed.
    """
    candidates: list[Any] = [dimension.get("grade_points")]
    aggregation = schema_definition.get("aggregation")
    if isinstance(aggregation, dict):
        candidates.append(aggregation.get("grade_points"))
    candidates.append(schema_definition.get("grade_points"))

    grade_points = next((c for c in candidates if c is not None), None)
    if not isinstance(grade_points, dict):
        raise DimensionGradeBridgeError(
            "grade_points_missing",
            f"维度 {dimension['key']} 缺少 grade_points（维度/aggregation/顶层均无）",
        )

    resolved: dict[str, float] = {}
    for grade in range(_MIN_GRADE, _MAX_GRADE + 1):
        point = grade_points.get(str(grade))
        if not _is_number(point):
            raise DimensionGradeBridgeError(
                "grade_points_missing",
                f"维度 {dimension['key']} 的 grade_points 缺少 {grade} 档或非数值",
            )
        resolved[str(grade)] = float(point)

    if not resolved[str(_MAX_GRADE)] > resolved[str(_MIN_GRADE)]:
        raise DimensionGradeBridgeError(
            "grade_points_missing",
            f"维度 {dimension['key']} 的 grade_points 必须满足 max_points>min_points",
        )
    return resolved


def _validate_grade(key: str, grade: Any) -> int:
    if not _is_int(grade) or not _MIN_GRADE <= grade <= _MAX_GRADE:
        raise DimensionGradeBridgeError(
            "grade_out_of_range",
            f"维度 {key} 的 grade 必须是 {_MIN_GRADE}-{_MAX_GRADE} 的整数",
        )
    return grade


def grades_to_deductions(
    *,
    dimension_grades: dict[str, int],
    dimension_schema_definition: dict,
    dimension_max: int | float,
) -> dict:
    """Convert 调用B per-dimension grades into aggregator deductions.

    Pure function, deterministic: no IO/network/DB/model, same input → same
    output.  Returns ``{"bridge_version", "dimension_max", "deductions", "evidence"}``
    where ``deductions[key]`` is a ``>= 0`` per-dimension deduction (rounded to
    4dp) ready to feed straight into
    ``category_evaluation_aggregator.aggregate_category_evaluation`` via a
    ``{"deductions": ...}`` dimension_result, and ``evidence[key]`` records
    ``{grade, ratio, share, deduction}`` for regression explainability.
    """
    if not isinstance(dimension_grades, dict):
        raise DimensionGradeBridgeError(
            "dimension_grades_invalid", "dimension_grades 必须是对象"
        )
    if not _is_number(dimension_max) or dimension_max < 0:
        raise DimensionGradeBridgeError(
            "dimension_max_invalid", "dimension_max 必须是 >=0 的数值"
        )

    dimensions = _dimension_definitions(dimension_schema_definition)

    schema_keys = [dimension["key"] for dimension in dimensions]
    schema_key_set = set(schema_keys)
    if len(schema_key_set) != len(schema_keys):
        raise DimensionGradeBridgeError(
            "schema_dimension_key_invalid", "维度定义存在重复 key"
        )
    grade_key_set = set(dimension_grades)
    if grade_key_set != schema_key_set:
        missing = sorted(schema_key_set - grade_key_set)
        extra = sorted(grade_key_set - schema_key_set)
        raise DimensionGradeBridgeError(
            "grade_keys_mismatch",
            f"dimension_grades 的 key 必须与 Schema 完全一致（缺失 {missing}，多余 {extra}）",
        )

    weight_sum = 0.0
    for dimension in dimensions:
        weight = dimension.get("weight")
        if not _is_number(weight) or weight < 0:
            raise DimensionGradeBridgeError(
                "weight_invalid",
                f"维度 {dimension['key']} 的 weight 必须是 >=0 的数值",
            )
        weight_sum += float(weight)
    if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise DimensionGradeBridgeError(
            "weights_not_normalized",
            f"维度 weights 求和必须严格=1（实际 {weight_sum}）",
        )

    deductions: dict[str, float] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for dimension in dimensions:
        key = dimension["key"]
        grade = _validate_grade(key, dimension_grades[key])
        grade_points = _resolve_grade_points(dimension, dimension_schema_definition)

        min_points = grade_points[str(_MIN_GRADE)]
        max_points = grade_points[str(_MAX_GRADE)]
        share = float(dimension["weight"]) * float(dimension_max)
        ratio = (grade_points[str(grade)] - min_points) / (max_points - min_points)
        deduction = round(share * (1.0 - ratio), _DEDUCTION_DIGITS)
        # round() may yield -0.0 for a full-marks dimension; normalize to 0.0.
        if deduction == 0.0:
            deduction = 0.0

        deductions[key] = deduction
        evidence[key] = {
            "grade": grade,
            "ratio": round(ratio, _EVIDENCE_DIGITS),
            "share": round(share, _EVIDENCE_DIGITS),
            "deduction": deduction,
        }

    return {
        "bridge_version": GRADE_BRIDGE_VERSION,
        "dimension_max": float(dimension_max),
        "deductions": deductions,
        "evidence": evidence,
    }


def deductions_from_bridge(
    *,
    dimension_grades: dict[str, int],
    dimension_schema_definition: dict,
    dimension_max: int | float,
) -> dict:
    """Convenience wrapper returning just the ``{"deductions", "evidence"}`` map.

    The returned dict is shaped exactly like a 调用B ``dimension_result`` so it
    can be passed straight to the aggregator without reshaping.  Pure function;
    does NOT import or invoke the worker.
    """
    bridged = grades_to_deductions(
        dimension_grades=dimension_grades,
        dimension_schema_definition=dimension_schema_definition,
        dimension_max=dimension_max,
    )
    return {"deductions": bridged["deductions"], "evidence": bridged["evidence"]}
