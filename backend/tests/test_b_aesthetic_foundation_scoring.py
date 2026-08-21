from __future__ import annotations

import pytest

from app.b_aesthetic_foundation import (
    BAestheticFoundationError,
    normalize_b_aesthetic_foundation,
)
from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.category_evaluation_contract import CATEGORY_EVALUATION_CONTRACT_VERSION
from app.dimension_deduction_bridge import (
    build_dimension_deduction_prompt,
    normalize_dimension_deduction_output,
)
from app.model_3d_su_category_seed import build_model_3d_su_contract
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


def _contract() -> dict:
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "level_scale": {
            "version": "category-level-scale-v1",
            "levels": [
                {"level": "L1", "min_score": 80, "enabled": True},
                {"level": "L2", "min_score": 60, "enabled": True},
                {"level": "L3", "min_score": 40, "enabled": True},
                {"level": "L4", "min_score": 21, "enabled": True},
                {"level": "L5", "min_score": 0, "enabled": True},
            ],
        },
        "redline_policy": {
            "format_version": REDLINE_POLICY_FORMAT_VERSION,
            "enabled": True,
            "hit_level": "L5",
            "hit_score_cap": 20,
            "rules": [],
        },
        "track_classification": {
            "format_version": "track-classification-v1",
            "default_track": "class_one",
            "tracks": [
                {
                    "key": "class_one",
                    "label": "一类",
                    "base_score": 40,
                    "dimension_max": 60,
                    "track_cap": 100,
                    "dimension_schema_ref": {
                        "schema_key": "space_aesthetic",
                        "version": "1.3.0",
                    },
                }
            ],
        },
        "common_modifiers": {
            "format_version": "common-modifiers-v1",
            "media_type_penalty": {
                "enabled": False,
                "penalties": {"other": 0},
            },
            "high_score_veto": {"enabled": False, "threshold": 80, "cap_to": 79, "rules": []},
        },
    }


def _precheck() -> dict:
    return {"production_fields": {"trait": "其它", "reason": []}}


def test_matcher_starts_from_call_b_aesthetic_score_before_rule_deduction() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(),
        {"deductions": {"composition": 12}},
        track_key="class_one",
        initial_score=88,
    )

    assert result["initial_score"] == 88
    assert result["score"] == 76
    assert result["base_score"] is None
    assert any(step["step"] == "b_aesthetic_foundation" for step in result["steps"])


def test_matcher_does_not_use_track_base_score_when_foundation_is_present() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(),
        {"deductions": {}},
        track_key="class_one",
        initial_score=55,
    )

    assert result["score"] == 55
    assert result["score"] != 40 + 60


def test_b_foundation_requires_aesthetic_score_and_evidence() -> None:
    normalized = normalize_b_aesthetic_foundation(
        {
            "contract_version": "b-aesthetic-foundation-v1",
            "aesthetic_score": 88,
            "overall_evidence": ["主体轮廓和材质关系清晰"],
            "confidence": 0.8,
        }
    )
    assert normalized["aesthetic_score"] == 88
    assert normalized["schema_version"] == "b-aesthetic-foundation-v1"

    normalized_alias = normalize_b_aesthetic_foundation(
        {
            "aesthetic_score": 88,
            "aesthetic_evidence": ["主体轮廓和材质关系清晰"],
        }
    )
    assert normalized_alias["evidence"] == ["主体轮廓和材质关系清晰"]

    with pytest.raises(BAestheticFoundationError) as exc_info:
        normalize_b_aesthetic_foundation({"overall_evidence": ["有证据"]})
    assert exc_info.value.code == "aesthetic_score_missing"


def test_3d_su_contract_declares_the_unified_foundation_contract() -> None:
    contract = build_model_3d_su_contract()
    assert contract["b_aesthetic_foundation"]["format_version"] == (
        "b-aesthetic-foundation-v1"
    )


def test_rule_call_b_preserves_the_unified_aesthetic_score() -> None:
    config = build_model_3d_su_contract()
    track = build_model_3d_su_contract()["track_classification"]["tracks"][0]["key"]
    from app.model_3d_su_category_seed import build_model_3d_su_subcategory_dimensions

    track_config = build_model_3d_su_subcategory_dimensions()[track]
    dimensions = track_config["common_group"]["schema_definition"]["dimensions"]
    output = {
        "aesthetic_score": 88,
        "aesthetic_evidence": ["模型细节与材质关系清楚"],
        "dimensions": {dimension["key"]: {"hit_rules": []} for dimension in dimensions},
        "overall_note": "整体完成度较好",
    }
    normalized = normalize_dimension_deduction_output(output, track_config)
    assert normalized["aesthetic_score"] == 88
    system, user = build_dimension_deduction_prompt(track_config)
    assert "aesthetic_score" in system
    assert "aesthetic_score" in user
