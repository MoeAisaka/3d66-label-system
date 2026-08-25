"""Shared v3 full/simple pipeline primitives.

The full worker supplies calling-A precheck and, for rule contracts, obtains
calling-B rule hits before entering this deterministic layer.  The simple
pipeline is the same layer exposed for already-qualified assets: callers pass
frozen precheck + rule hits and no model is invoked.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .category_evaluation_aggregator import aggregate_category_evaluation
from .dimension_deduction_bridge import (
    compose_rule_deductions,
    empty_deduction_output,
    foundation_required,
    has_deduction_rules,
    rule_scoring_mode,
)
from .redline_policy import evaluate_redlines
from .subcategory_resolver import resolve_subcategory


class V3PipelineError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _has_aesthetic_foundation(dimension_output: Any) -> bool:
    """Whether this stored Call-B output actually carries a usable base score.

    Replays read historical rows.  Rows written before the Call-B aesthetic
    foundation existed — and rows whose Call-B request failed — hold no score,
    and demanding one would fail their corrections closed.  Rows that do hold a
    score must keep it, otherwise the matcher restarts from full marks.
    """
    if not isinstance(dimension_output, Mapping):
        return False
    score = dimension_output.get("aesthetic_score")
    if isinstance(score, bool) or not isinstance(score, int):
        return False
    return 0 <= score <= 100


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
        active_rule_mode = rule_scoring_mode(config)
        public_scoring_mode = (
            "bonus_cap_v2"
            if active_rule_mode == "bonus_cap_v2"
            else "rule_deduction"
        )
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
            reset_output = empty_deduction_output(config)
            # The Call-B aesthetic score belongs to the image, not to the track,
            # so a track correction must not silently drop the matcher's
            # starting score along with the track-specific rule hits.
            if isinstance(rule_output, dict):
                for key in (
                    "aesthetic_score",
                    "aesthetic_evidence",
                    "aesthetic_confidence",
                    "raw_payload",
                ):
                    if rule_output.get(key) is not None:
                        reset_output[key] = deepcopy(rule_output[key])
                if rule_output.get("warning") is None:
                    reset_output.pop("warning", None)
            rule_output = reset_output
        # The Call-B aesthetic score is the matcher's starting score, so a
        # replay that drops it restarts from a full-marks baseline and inflates
        # every corrected result.  Unlike the full worker — which always holds a
        # fresh Call-B answer — a replay reads historical rows, and rows stored
        # before the foundation existed carry no score at all.  So the switch
        # follows the stored data: honour the frozen contract only when this row
        # actually has a score, and stay in the legacy path when it does not.
        composed = compose_rule_deductions(
            config=config,
            dimension_output=rule_output,
            require_foundation=(
                _has_aesthetic_foundation(rule_output)
                and foundation_required(contract)
                and foundation_required(config)
            ),
        )
        result = aggregate_category_evaluation(
            contract,
            precheck,
            composed,
            track_key=resolved_track,
            initial_score=composed.get("aesthetic_score"),
        )
        result["dimension_scoring_mode"] = public_scoring_mode
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
