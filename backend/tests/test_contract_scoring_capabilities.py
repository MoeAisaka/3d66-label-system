from __future__ import annotations

from app.category_evaluation_contract import (
    CATEGORY_EVALUATION_CONTRACT_VERSION,
    resolve_scoring_capabilities,
)
from app.dimension_deduction_bridge import rule_scoring_mode
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
                    "key": "class_one",
                    "label": "一类",
                    "base_score": 40,
                    "dimension_max": 60,
                    "track_cap": 100,
                    "dimension_schema_ref": {"schema_key": "custom", "version": "1"},
                },
                {
                    "key": "class_two",
                    "label": "二类",
                    "base_score": 20,
                    "dimension_max": 60,
                    "track_cap": 80,
                    "dimension_schema_ref": {"schema_key": "custom", "version": "1"},
                },
            ],
        },
        "common_modifiers": {
            "format_version": "common-modifiers-v1",
            "media_type_penalty": {
                "enabled": True,
                "baseline": "real_photo",
                "penalties": {
                    "real_photo": 0,
                    "render_3d": -5,
                    "ai_image": -10,
                    "other": -10,
                },
            },
            "high_score_veto": {"enabled": False, "threshold": 80, "cap_to": 79},
            "hard_defect_penalty": {
                "enabled": True,
                "per_hit": 10,
                "source": "hard_defects",
            },
        },
        "track_adjustments": {"class_two": {"deduction": 20}},
    }


def _dimensions(*, with_rules: bool) -> dict:
    dimension = {
        "key": "composition",
        "label": "构图",
        "weight": 1.0,
        "grade_points": {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0},
    }
    if with_rules:
        dimension["deduction_rules"] = [
            {
                "rule_id": "minor_defect",
                "description": "存在轻微构图缺陷",
                "deduction": 20,
                "tags": [],
            }
        ]
    return {
        "format_version": "subcategory-dimensions-v1",
        "sub_category_key": "class_one",
        "dimension_max": 60,
        "grade_output_contract": {
            "format_version": "dimension-grade-output-v1",
            "require_exact_keys": True,
            "evidence_required": True,
        },
        "common_group": {
            "group_weight": 1.0,
            "schema_definition": {
                "format_version": "dimension-schema-definition-v1",
                "schema_key": "custom",
                "version": "1",
                "dimensions": [dimension],
            },
        },
        "specific_group": {"schema_definition": {"dimensions": []}},
    }


def test_explicit_dimension_rules_override_static_grade_marker() -> None:
    assert rule_scoring_mode(_dimensions(with_rules=True)) == "deduction_v1"


def test_capability_resolution_lists_all_declared_executable_primitives() -> None:
    capabilities = resolve_scoring_capabilities(
        _contract(), {"class_one": _dimensions(with_rules=True)}
    )

    assert capabilities["execution_mode"] == "rule_deduction"
    assert capabilities["primitives"] == [
        "redline",
        "track_adjustment",
        "dimension_rule_deduction",
        "media_penalty",
        "hard_defect_penalty",
        "level_scale",
    ]


def test_capability_resolution_keeps_grade_fallback_for_legacy_dimension() -> None:
    capabilities = resolve_scoring_capabilities(
        _contract(), {"class_one": _dimensions(with_rules=False)}
    )

    assert capabilities["execution_mode"] == "grade_fallback"
    assert "dimension_rule_deduction" not in capabilities["primitives"]


def test_capability_resolution_is_per_track_when_tracks_use_different_modes() -> None:
    legacy = _dimensions(with_rules=False)
    rule_based = _dimensions(with_rules=True)
    capabilities = resolve_scoring_capabilities(
        _contract(),
        {"class_one": rule_based, "class_two": legacy},
    )

    assert capabilities["execution_mode"] == "per_track"
    assert capabilities["track_modes"] == {
        "class_one": "rule_deduction",
        "class_two": "grade_fallback",
    }
    assert "dimension_rule_deduction" in capabilities["primitives"]
