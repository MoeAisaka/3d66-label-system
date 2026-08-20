from __future__ import annotations

from app.category_evaluation_aggregator import aggregate_category_evaluation
from copy import deepcopy

from app.category_evaluation_contract import (
    CATEGORY_EVALUATION_CONTRACT_VERSION,
    validate_category_evaluation_contract,
)
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


def _contract() -> dict:
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "category_key": "operator_custom",
        "level_scale": {
            "version": "category-level-scale-v1",
            "levels": [
                {"level": "L1", "enabled": True, "min_score": 80},
                {"level": "L2", "enabled": True, "min_score": 60},
                {"level": "L3", "enabled": True, "min_score": 40},
                {"level": "L4", "enabled": True, "min_score": 0},
                {"level": "L5", "enabled": False},
            ],
        },
        "redline_policy": {
            "format_version": REDLINE_POLICY_FORMAT_VERSION,
            "enabled": False,
            "hit_level": "L4",
            "hit_score_cap": 40,
            "rules": [],
        },
        "track_classification": {
            "format_version": "track-classification-v1",
            "default_track": "class_one",
            "tracks": [
                {
                    "key": "class_one", "label": "一类", "base_score": 40,
                    "dimension_max": 60, "track_cap": 100,
                    "dimension_schema_ref": {"schema_key": "custom", "version": "1"},
                },
                {
                    "key": "class_two", "label": "二类", "base_score": 20,
                    "dimension_max": 60, "track_cap": 80,
                    "dimension_schema_ref": {"schema_key": "custom", "version": "1"},
                },
                {
                    "key": "class_three", "label": "三类", "base_score": 40,
                    "dimension_max": 30, "track_cap": 100,
                    "dimension_schema_ref": {"schema_key": "custom", "version": "1"},
                },
            ],
        },
        "common_modifiers": {
            "format_version": "common-modifiers-v1",
            "media_type_penalty": {
                "enabled": True, "baseline": "real_photo",
                "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -10, "other": -10},
            },
            "high_score_veto": {"enabled": False, "threshold": 80, "cap_to": 79},
            "hard_defect_penalty": {"enabled": True, "per_hit": 10, "source": "hard_defects"},
        },
        "track_adjustments": {
            "class_two": {"deduction": 20},
            "class_three": {"bonus": 30},
        },
    }


def _precheck(trait: str, hard_defects: list[str] | None = None) -> dict:
    payload = {"production_fields": {"trait": trait, "reason": []}}
    if hard_defects is not None:
        payload["hard_defects"] = hard_defects
    return payload


def test_custom_track_adjustments_and_hard_defect_penalty_are_applied() -> None:
    result = aggregate_category_evaluation(
        _contract(), _precheck("实景照片", ["a", "b"]), {"deductions": {}}, track_key="class_two"
    )

    assert result["score"] == 40
    assert result["track_adjustment"] == {"deduction": 20.0, "bonus": 0.0}
    assert result["hard_defect_penalty"] == 20.0
    assert {item["primitive"] for item in result["common_modifier_evidence"]} == {
        "track_adjustment", "media_penalty", "hard_defect_penalty",
    }


def test_custom_media_aliases_execute_render_ai_and_other_penalties() -> None:
    contract = _contract()
    render = aggregate_category_evaluation(contract, _precheck("3d_render"), {"deductions": {}}, track_key="class_one")
    ai = aggregate_category_evaluation(contract, _precheck("ai_generated"), {"deductions": {}}, track_key="class_one")
    other = aggregate_category_evaluation(contract, _precheck("other"), {"deductions": {}}, track_key="class_one")

    assert render["score"] == 95
    assert ai["score"] == 90
    assert other["score"] == 90


def _contract_owned_modifier_names() -> dict:
    contract = deepcopy(_contract())
    contract["common_modifiers"] = {
        "format_version": "common-modifiers-v2",
        "media_type_penalty": {
            "enabled": True,
            "baseline": "摄影成片",
            "fallback": "其它媒介",
            "aliases": {
                "实景照片": "摄影成片",
                "3D数字效果图": "模型渲染",
            },
            "penalties": {
                "摄影成片": 0,
                "模型渲染": -6,
                "其它媒介": -2,
            },
        },
        "high_score_veto": {"enabled": False},
        "hard_defect_penalty": {
            "enabled": True,
            "per_hit": 10,
            "source": "hard_defects",
        },
    }
    return contract


def test_contract_owned_modifier_names_validate_without_legacy_veto_tiers() -> None:
    validate_category_evaluation_contract(_contract_owned_modifier_names())


def test_contract_owned_media_alias_and_uncapped_hard_defects_execute() -> None:
    result = aggregate_category_evaluation(
        _contract_owned_modifier_names(),
        _precheck("3D数字效果图", ["透明棋盘格", "未完成手绘草稿"]),
        {"deductions": {}},
        track_key="class_one",
    )

    assert result["score"] == 74
    assert result["media_key"] == "模型渲染"
    assert result["media_penalty"] == -6
    assert result["hard_defect_penalty"] == 20
    assert result["hard_defect_action"] is None
