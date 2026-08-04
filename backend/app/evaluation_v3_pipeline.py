"""Shared v3 full/simple pipeline primitives.

The full worker supplies calling-A precheck and, for rule contracts, obtains
calling-B rule hits before entering this deterministic layer.  The simple
pipeline is the same layer exposed for already-qualified assets: callers pass
frozen precheck + rule hits and no model is invoked.
"""

from __future__ import annotations

from typing import Any

from .category_evaluation_aggregator import aggregate_category_evaluation
from .dimension_deduction_bridge import (
    compose_rule_deductions,
    empty_deduction_output,
    has_deduction_rules,
)
from .redline_policy import evaluate_redlines
from .subcategory_resolver import resolve_subcategory


class V3PipelineError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve_v3_track(
    *, v3_context: dict[str, Any], precheck: dict[str, Any]
) -> str:
    resolved = resolve_subcategory(
        precheck,
        classification_map=v3_context["classification_map"],
        track_classification=v3_context["contract"]["track_classification"],
    )
    return str(resolved["track_key"])


def recompute_qualified_v3(
    *,
    v3_context: dict[str, Any],
    precheck: dict[str, Any],
    dimension_output: dict[str, Any] | None,
    track_key: str | None = None,
) -> dict[str, Any]:
    """Run the simple v3 pipeline without calling A or B.

    ``dimension_output`` is the normalized calling-B rule-hit structure stored
    on the original result (or manually supplied by a correction).  Redlines
    terminate before dimensions, so they remain recomputable even without it.
    """
    if not isinstance(v3_context, dict):
        raise V3PipelineError("v3_context_invalid", "缺少冻结的 v3_context")
    contract = v3_context.get("contract")
    dimensions_by_track = v3_context.get("subcategory_dimensions")
    if not isinstance(contract, dict) or not isinstance(dimensions_by_track, dict):
        raise V3PipelineError("v3_context_invalid", "冻结 v3 合同或维度配置无效")

    redline = evaluate_redlines(precheck, policy=contract["redline_policy"])
    if redline.get("hit"):
        result = aggregate_category_evaluation(
            contract, precheck, {"deductions": {}}, track_key=track_key
        )
        result["dimension_scoring_mode"] = "rule_deduction"
        result["dimension_deduction_output"] = dimension_output
        return result

    resolved_track = track_key or resolve_v3_track(
        v3_context=v3_context, precheck=precheck
    )
    config = dimensions_by_track.get(resolved_track)
    if not isinstance(config, dict):
        raise V3PipelineError(
            "track_config_missing", f"赛道 {resolved_track} 缺少冻结维度配置"
        )

    if has_deduction_rules(config):
        rule_output = dimension_output
        expected_keys = set(empty_deduction_output(config)["dimensions"])
        raw_dimensions = (
            rule_output.get("dimensions")
            if isinstance(rule_output, dict)
            else None
        )
        if isinstance(raw_dimensions, dict):
            actual_keys = set(raw_dimensions)
        else:
            actual_keys = {
                item.get("dimension_key")
                for item in raw_dimensions or []
                if isinstance(item, dict)
            }
        # A corrected track can have another dimension set.  Starting that new
        # branch with empty hits is deterministic and does not invent evidence.
        if not isinstance(rule_output, dict) or actual_keys != expected_keys:
            rule_output = empty_deduction_output(config)
        composed = compose_rule_deductions(
            config=config, dimension_output=rule_output
        )
        result = aggregate_category_evaluation(
            contract, precheck, composed, track_key=resolved_track
        )
        result["dimension_scoring_mode"] = "rule_deduction"
        result["dimension_deduction_output"] = rule_output
        return result

    # @deprecated grade fallback.  Stored dimension evidence carries the exact
    # deductions already produced by the historic bridge, so downstream nodes
    # can still be replayed without another model call.
    evidence = (dimension_output or {}).get("legacy_dimension_result")
    if not isinstance(evidence, dict):
        raise V3PipelineError(
            "legacy_dimension_result_missing",
            "旧 grade_points 结果缺少可重放的 legacy_dimension_result",
        )
    result = aggregate_category_evaluation(
        contract, precheck, evidence, track_key=resolved_track
    )
    result["dimension_scoring_mode"] = "grade_fallback"
    return result
