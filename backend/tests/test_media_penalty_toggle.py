from __future__ import annotations

from copy import deepcopy

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.inspiration_category_seed import build_inspiration_v3_contract


def _precheck() -> dict:
    return {"production_fields": {"reason": [], "trait": "AI图"}}


def test_media_penalty_enabled_and_disabled_paths() -> None:
    enabled_contract = build_inspiration_v3_contract()
    disabled_contract = deepcopy(enabled_contract)
    disabled_contract["common_modifiers"]["media_type_penalty"]["enabled"] = False
    dimension_result = {"deductions": {}, "mode": "rule_deduction"}

    enabled = aggregate_category_evaluation(
        enabled_contract, _precheck(), dimension_result, track_key="class_three"
    )
    disabled = aggregate_category_evaluation(
        disabled_contract, _precheck(), dimension_result, track_key="class_three"
    )
    assert enabled["media_penalty"] == -15
    assert enabled["media_penalty_enabled"] is True
    assert disabled["media_penalty"] == 0
    assert disabled["media_penalty_enabled"] is False
    assert disabled["score"] == enabled["score"] + 15
    assert any(step["step"] == "media_skipped" for step in disabled["steps"])
