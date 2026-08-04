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
from .category_evaluation_contract import (
    CategoryEvaluationContractError,
    validate_deduction_rules,
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

    A group may legally be **empty** (zero dimensions): 共性维度 and 特有维度 can
    each be freely reduced to 0.  An empty group contributes no dimensions and,
    critically, reserves no ``dimension_max`` slice (its weight is dropped and
    the remaining non-empty groups renormalize).  Only the structure needed to
    read keys is validated here; the reused bridge performs the full per-group
    schema/weight/grade validation when it runs on a non-empty group.
    """
    if schema_definition is None:
        return []
    if not isinstance(schema_definition, dict):
        raise DimensionCompositionError(
            f"{group_name}.schema_not_object",
            f"{group_name}.schema_definition 必须是对象或缺省",
        )
    dimensions = schema_definition.get("dimensions")
    if dimensions is None:
        return []
    if not isinstance(dimensions, list):
        raise DimensionCompositionError(
            f"{group_name}.schema_dimensions_invalid",
            f"{group_name}.schema_definition.dimensions 必须是数组",
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
        try:
            validate_deduction_rules(
                dimension.get("deduction_rules"), dimension_key=key
            )
        except CategoryEvaluationContractError as exc:
            raise DimensionCompositionError(
                f"{group_name}.{exc.code}", str(exc)
            ) from exc
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

    group_keys: dict[str, list[str]] = {}
    rule_mode_flags: list[bool] = []
    non_empty_weight_sum = 0.0
    non_empty_count = 0
    for group_name in _GROUP_KEYS:
        group = config.get(group_name)
        # A group may be entirely absent or present-but-empty; both mean
        # "0 dimensions" (共性/特有维度 can each be freely reduced to 0).
        if group is None:
            group_keys[group_name] = []
            continue
        if not isinstance(group, dict):
            raise DimensionCompositionError(
                f"{group_name}.invalid", f"{group_name} 必须是对象或缺省"
            )
        keys = _group_dimension_keys(group_name, group.get("schema_definition"))
        group_keys[group_name] = keys
        schema_definition = group.get("schema_definition")
        if isinstance(schema_definition, dict):
            for dimension in schema_definition.get("dimensions") or []:
                rule_mode_flags.append("deduction_rules" in dimension)
        group_weight = group.get("group_weight")
        if keys:
            # A non-empty group must carry a positive share; an empty group's
            # group_weight (if any) is ignored and reserves no dimension_max.
            if not _is_number(group_weight) or group_weight <= 0:
                raise DimensionCompositionError(
                    f"{group_name}.group_weight_invalid",
                    f"非空的 {group_name} 的 group_weight 必须是 >0 的数值",
                )
            non_empty_weight_sum += float(group_weight)
            non_empty_count += 1
        elif group_weight is not None and (
            not _is_number(group_weight) or group_weight < 0
        ):
            raise DimensionCompositionError(
                f"{group_name}.group_weight_invalid",
                f"{group_name}.group_weight 必须是 >=0 的数值或缺省",
            )

    # Both groups empty → prompt-only (no dimension scoring); weights irrelevant.
    # One or two non-empty groups → their weights renormalize among themselves
    # and split dimension_max (ADR-0028 renormalize_selected_to_one precedent),
    # so an empty group never leaks its slice as free points.
    if non_empty_count > 0 and non_empty_weight_sum <= 0:
        raise DimensionCompositionError(
            "group_weights_not_normalized",
            "非空维度组的 group_weight 之和必须 >0",
        )

    common_keys = set(group_keys["common_group"])
    specific_keys = set(group_keys["specific_group"])
    overlap = sorted(common_keys & specific_keys)
    if overlap:
        raise DimensionCompositionError(
            "dimension_key_overlap",
            f"共性维度与特有维度的 key 不得重叠（重叠 {overlap}）",
        )
    if rule_mode_flags and any(rule_mode_flags) and not all(rule_mode_flags):
        raise DimensionCompositionError(
            "deduction_rule_mode_mixed",
            "同一赛道的维度必须全部使用 deduction_rules，或全部使用已废弃的 grade_points fallback",
        )


def _bridge_group(
    group_name: str,
    group: dict[str, Any],
    grades: Any,
    effective_max: float,
) -> dict[str, Any]:
    """Run the reused grade bridge for one non-empty group.

    ``effective_max`` is the group's renormalized slice of ``dimension_max``.
    Any ``DimensionGradeBridgeError`` is re-raised as ``DimensionCompositionError``
    with the group name prefixed onto the original bridge code.
    """
    if not isinstance(grades, dict):
        raise DimensionCompositionError(
            f"{group_name}.grades_invalid", f"{group_name} 的 grades 必须是对象"
        )
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


def _group_is_empty(group: Any) -> bool:
    """True when a group is absent or carries zero dimensions."""
    if not isinstance(group, dict):
        return True
    schema = group.get("schema_definition")
    if not isinstance(schema, dict):
        return True
    dimensions = schema.get("dimensions")
    return not isinstance(dimensions, list) or not dimensions


def compose_deductions(
    *,
    config: dict,
    common_grades: dict[str, int] | None = None,
    specific_grades: dict[str, int] | None = None,
) -> dict:
    """Merge a subcategory's common + specific dimension grades into deductions.

    Pure function, deterministic: no IO/network/DB/model, same input → same
    output.  Validates ``config`` (``subcategory-dimensions-v1``).

    共性维度 and 特有维度 can each be freely reduced to **0**:
    - Both groups empty → **prompt-only** (``dimensions_enabled=False``): no
      dimension scoring, ``deductions`` is empty, and the aggregator receives an
      empty deduction map (the dimension block is awarded in full only because
      the operator explicitly disabled dimensions — this mirrors ADR-0031's
      prompt-only mode; callers that must not award free points should route to
      the prompt-only scoring contract instead).
    - One or two non-empty groups → their ``group_weight``s **renormalize among
      themselves** and split ``dimension_max`` (ADR-0028
      ``renormalize_selected_to_one`` precedent), so an empty group never leaks
      its slice as free points.

    Returns ``{"composition_version", "sub_category_key", "dimension_max",
    "dimensions_enabled", "deductions", "common", "specific", "evidence"}``.  The
    dict is shaped so it can be passed straight to
    ``aggregate_category_evaluation`` as the ``dimension_result``.
    """
    validate_subcategory_dimensions(config)

    dimension_max = float(config["dimension_max"])
    common_group = config.get("common_group")
    specific_group = config.get("specific_group")
    common_empty = _group_is_empty(common_group)
    specific_empty = _group_is_empty(specific_group)

    # Renormalize non-empty group weights among themselves, then split max.
    non_empty = [
        (name, grp)
        for name, grp, empty in (
            ("common", common_group, common_empty),
            ("specific", specific_group, specific_empty),
        )
        if not empty
    ]
    weight_total = sum(float(grp["group_weight"]) for _, grp in non_empty)
    effective_max = {
        name: (float(grp["group_weight"]) / weight_total) * dimension_max
        for name, grp in non_empty
    } if weight_total > 0 else {}

    common_bridge = (
        None
        if common_empty
        else _bridge_group(
            "common", common_group, common_grades or {}, effective_max["common"]
        )
    )
    specific_bridge = (
        None
        if specific_empty
        else _bridge_group(
            "specific", specific_group, specific_grades or {}, effective_max["specific"]
        )
    )

    merged: dict[str, float] = {}
    for bridge in (common_bridge, specific_bridge):
        if bridge is None:
            continue
        for key, deduction in bridge["deductions"].items():
            if key in merged:
                # Defensive: validate_subcategory_dimensions already guarantees
                # the two groups' keys are disjoint.
                raise DimensionCompositionError(
                    "dimension_key_overlap",
                    f"合并时出现重复维度 key：{key}",
                )
            merged[key] = deduction

    def _group_evidence(bridge: dict[str, Any] | None) -> dict[str, Any]:
        if bridge is None:
            return {"enabled": False, "effective_max": 0.0, "deductions": {}, "evidence": {}}
        return {
            "enabled": True,
            "effective_max": bridge["dimension_max"],
            "deductions": dict(bridge["deductions"]),
            "evidence": bridge["evidence"],
        }

    return {
        "composition_version": COMPOSITION_VERSION,
        "sub_category_key": config["sub_category_key"],
        "dimension_max": dimension_max,
        "dimensions_enabled": not (common_empty and specific_empty),
        "deductions": merged,
        "common": common_bridge,
        "specific": specific_bridge,
        "evidence": {
            "common": _group_evidence(common_bridge),
            "specific": _group_evidence(specific_bridge),
        },
    }
