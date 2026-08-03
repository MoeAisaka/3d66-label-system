"""ADR-0033 Phase 3.6 subcategory dimension composition (framework-first, pure).

The authoritative ADR-0033 pipeline is::

    红线(直筛L5) → 分类器(类目→子类目) → 子类目: 提示词A/B
        → 维度评测(子类目共性维度 + 子类目特有维度) → 产出(等级+分数+固定字段)

The load-bearing correction this module encodes: a subcategory's dimension
evaluation is **not** one flat schema.  It is two groups — a *common* group
(shared across subcategories) and a *specific* group (subcategory-owned) — each
carrying its own weights and taking its own slice of the subcategory's
``dimension_max``.  调用B grades each group independently; this module turns both
grade maps into a single merged ``deductions`` mapping ready for
``category_evaluation_aggregator.aggregate_category_evaluation``.

Like the rest of the ADR-0033 framework layer this is a **pure function**: no
IO, no network, no database and no model calls.  It does NOT re-implement the
grade→deduction math — it reuses ``dimension_grade_bridge.grades_to_deductions``
once per group, with each group's own schema, grades and effective_max.  Output
is a fixed-shape, JSON-serializable, input-stable ``dict`` so the composition
decision is fully regressible.

Scope note: this module intentionally does NOT touch the frozen v3 contract
(``category_evaluation_contract.py``).  The "two dimension refs per subcategory"
shape lives here as a standalone optional config so the frozen contract and its
tests stay untouched; folding it into the contract is a later phase.
"""

from __future__ import annotations

import math
from typing import Any

from .dimension_grade_bridge import (
    DimensionGradeBridgeError,
    grades_to_deductions,
)


COMPOSITION_VERSION = "dimension-composition-v1"
SUBCATEGORY_DIMENSIONS_FORMAT_VERSION = "subcategory-dimensions-v1"

_GROUP_KEYS = ("common_group", "specific_group")
_WEIGHT_SUM_TOLERANCE = 1e-9


class DimensionCompositionError(ValueError):
    """Raised when subcategory dimension composition cannot proceed (fail-closed).

    Carries a stable ``code`` for programmatic branching independent of the
    (localized) message text, matching the ADR-0033 error convention.  Failures
    surfaced by the reused grade bridge are re-raised as this type with the
    original bridge ``code`` prefixed by the group name (e.g.
    ``common.weights_not_normalized``) so the offending group is unambiguous.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _group_dimension_keys(group_name: str, schema_definition: Any) -> list[str]:
    """Extract a group's dimension keys for the overlap check, fail-closed.

    Only the structure needed to read keys is validated here; the reused bridge
    performs the full per-group schema/weight/grade validation when it runs.
    """
    if not isinstance(schema_definition, dict):
        raise DimensionCompositionError(
            f"{group_name}.schema_not_object",
            f"{group_name}.schema_definition 必须是对象",
        )
    dimensions = schema_definition.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise DimensionCompositionError(
            f"{group_name}.schema_dimensions_missing",
            f"{group_name}.schema_definition.dimensions 必须是非空数组",
        )
    keys: list[str] = []
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise DimensionCompositionError(
                f"{group_name}.schema_dimension_invalid",
                f"{group_name} 的每个维度定义必须是对象",
            )
        key = dimension.get("key")
        if not isinstance(key, str) or not key:
            raise DimensionCompositionError(
                f"{group_name}.schema_dimension_key_invalid",
                f"{group_name} 的维度定义 key 必须是非空字符串",
            )
        keys.append(key)
    return keys


def validate_subcategory_dimensions(config: Any) -> None:
    """Fail-closed validation of a ``subcategory-dimensions-v1`` config.

    Checks (in order): object shape, format_version, sub_category_key,
    dimension_max >= 0, both groups present with a numeric group_weight >= 0 and
    an object schema_definition, group_weights sum to 1 (tolerance 1e-9), and
    the two groups' dimension keys do not overlap (``dimension_key_overlap``).
    The per-group schema/weight/grade rules are left to the reused bridge.
    """
    if not isinstance(config, dict):
        raise DimensionCompositionError(
            "config_invalid", "subcategory-dimensions 配置必须是对象"
        )
    if config.get("format_version") != SUBCATEGORY_DIMENSIONS_FORMAT_VERSION:
        raise DimensionCompositionError(
            "format_version_invalid",
            f"format_version 必须是 {SUBCATEGORY_DIMENSIONS_FORMAT_VERSION}",
        )
    sub_category_key = config.get("sub_category_key")
    if not isinstance(sub_category_key, str) or not sub_category_key:
        raise DimensionCompositionError(
            "sub_category_key_invalid", "sub_category_key 必须是非空字符串"
        )
    dimension_max = config.get("dimension_max")
    if not _is_number(dimension_max) or dimension_max < 0:
        raise DimensionCompositionError(
            "dimension_max_invalid", "dimension_max 必须是 >=0 的数值"
        )

    weight_sum = 0.0
    group_keys: dict[str, list[str]] = {}
    for group_name in _GROUP_KEYS:
        group = config.get(group_name)
        if not isinstance(group, dict):
            raise DimensionCompositionError(
                f"{group_name}.missing", f"{group_name} 必须是对象"
            )
        group_weight = group.get("group_weight")
        if not _is_number(group_weight) or group_weight < 0:
            raise DimensionCompositionError(
                f"{group_name}.group_weight_invalid",
                f"{group_name}.group_weight 必须是 >=0 的数值",
            )
        weight_sum += float(group_weight)
        group_keys[group_name] = _group_dimension_keys(
            group_name, group.get("schema_definition")
        )

    if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise DimensionCompositionError(
            "group_weights_not_normalized",
            f"两组 group_weight 求和必须=1（实际 {weight_sum}）",
        )

    common_keys = set(group_keys["common_group"])
    specific_keys = set(group_keys["specific_group"])
    overlap = sorted(common_keys & specific_keys)
    if overlap:
        raise DimensionCompositionError(
            "dimension_key_overlap",
            f"共性维度与特有维度的 key 不得重叠（重叠 {overlap}）",
        )


def _bridge_group(
    group_name: str,
    group: dict[str, Any],
    grades: Any,
    dimension_max: float,
) -> dict[str, Any]:
    """Run the reused grade bridge for one group, re-tagging bridge failures.

    ``effective_max = group_weight * dimension_max``.  Any
    ``DimensionGradeBridgeError`` is re-raised as ``DimensionCompositionError``
    with the group name prefixed onto the original bridge code.
    """
    if not isinstance(grades, dict):
        raise DimensionCompositionError(
            f"{group_name}.grades_invalid", f"{group_name} 的 grades 必须是对象"
        )
    effective_max = float(group["group_weight"]) * float(dimension_max)
    try:
        return grades_to_deductions(
            dimension_grades=grades,
            dimension_schema_definition=group["schema_definition"],
            dimension_max=effective_max,
        )
    except DimensionGradeBridgeError as exc:
        raise DimensionCompositionError(
            f"{group_name}.{exc.code}", str(exc)
        ) from exc


def compose_deductions(
    *,
    config: dict,
    common_grades: dict[str, int],
    specific_grades: dict[str, int],
) -> dict:
    """Merge a subcategory's common + specific dimension grades into deductions.

    Pure function, deterministic: no IO/network/DB/model, same input → same
    output.  Validates ``config`` (``subcategory-dimensions-v1``), splits
    ``dimension_max`` between the two groups by ``group_weight``
    (``effective_max = group_weight * dimension_max``), runs the reused
    ``grades_to_deductions`` bridge once per group with its own schema, grades
    and effective_max, then merges the two non-overlapping ``deductions`` maps.

    Returns ``{"composition_version", "sub_category_key", "dimension_max",
    "deductions", "common", "specific", "evidence"}`` where ``deductions`` is the
    merged per-dimension deduction map.  The dict is shaped so it can be passed
    straight to ``aggregate_category_evaluation`` as the ``dimension_result``
    (it carries both ``deductions`` and an ``evidence`` dict).
    """
    validate_subcategory_dimensions(config)

    dimension_max = float(config["dimension_max"])
    common_group = config["common_group"]
    specific_group = config["specific_group"]

    common_bridge = _bridge_group(
        "common", common_group, common_grades, dimension_max
    )
    specific_bridge = _bridge_group(
        "specific", specific_group, specific_grades, dimension_max
    )

    merged: dict[str, float] = {}
    for group_name, bridge in (
        ("common", common_bridge),
        ("specific", specific_bridge),
    ):
        for key, deduction in bridge["deductions"].items():
            if key in merged:
                # Defensive: validate_subcategory_dimensions already guarantees
                # the two groups' keys are disjoint.
                raise DimensionCompositionError(
                    "dimension_key_overlap",
                    f"合并时出现重复维度 key：{key}",
                )
            merged[key] = deduction

    evidence = {
        "common": {
            "group_weight": float(common_group["group_weight"]),
            "effective_max": common_bridge["dimension_max"],
            "deductions": dict(common_bridge["deductions"]),
            "evidence": common_bridge["evidence"],
        },
        "specific": {
            "group_weight": float(specific_group["group_weight"]),
            "effective_max": specific_bridge["dimension_max"],
            "deductions": dict(specific_bridge["deductions"]),
            "evidence": specific_bridge["evidence"],
        },
    }

    return {
        "composition_version": COMPOSITION_VERSION,
        "sub_category_key": config["sub_category_key"],
        "dimension_max": dimension_max,
        "deductions": merged,
        "common": common_bridge,
        "specific": specific_bridge,
        "evidence": evidence,
    }
