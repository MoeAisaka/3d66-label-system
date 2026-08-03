"""ADR-0033 Phase 3.8 tests: inspiration-image v3 contract seed / full assembly.

Covers: the assembled contract / classification map / per-track dimension
configs all pass the existing validators unchanged; and the end-to-end
``evaluate_one`` orchestrator over the documented scenarios (redline hit,
建筑设计 grade5 real photo → class_one 100/L1, 产品设计 grade5 → class_two ≤80,
low-confidence → class_three default, AI-image -15 penalty visible).  All output
is asserted deterministic and JSON-serializable.
"""

from __future__ import annotations

import json

import pytest

from app.category_evaluation_contract import (
    CATEGORY_EVALUATION_CONTRACT_VERSION,
    validate_category_evaluation_contract,
)
from app.dimension_composition import validate_subcategory_dimensions
from app.subcategory_resolver import validate_classification_map
from app.inspiration_category_seed import (
    INSPIRATION_SEED_VERSION,
    TRACK_CLASS_ONE,
    TRACK_CLASS_THREE,
    TRACK_CLASS_TWO,
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
    evaluate_one,
)


# --- shared grade fixtures (simulate 调用B): all-5 = full marks, zero deductions ---

_COMMON_GRADE5 = {
    "presentation_integrity": 5,
    "visual_hierarchy": 5,
    "inspiration_reference": 5,
}
_SPECIFIC_GRADE5 = {
    TRACK_CLASS_ONE: {"spatial_originality": 5, "design_trendiness": 5},
    TRACK_CLASS_TWO: {"product_form_language": 5, "artistic_expression": 5},
    TRACK_CLASS_THREE: {"visual_impact": 5},
}


def _common_grades_all5() -> dict:
    return {track: dict(_COMMON_GRADE5) for track in _SPECIFIC_GRADE5}


def _specific_grades_all5() -> dict:
    return {track: dict(grades) for track, grades in _SPECIFIC_GRADE5.items()}


def _precheck(*, reason=None, trait="实景照片", category="建筑设计", confidence=0.95,
              scope_status="in_scope", hard_defects=None) -> dict:
    production_fields: dict = {"trait": trait}
    if reason is not None:
        production_fields["reason"] = reason
    precheck: dict = {
        "production_fields": production_fields,
        "classification": {
            "scope_status": scope_status,
            "primary_category": category,
            "primary_confidence": confidence,
        },
    }
    if hard_defects is not None:
        precheck["hard_defects"] = hard_defects
    return precheck


def _run(precheck: dict) -> dict:
    return evaluate_one(
        contract=build_inspiration_v3_contract(),
        classification_map=build_inspiration_classification_map(),
        subcategory_dimensions=build_inspiration_subcategory_dimensions(),
        precheck=precheck,
        common_grades_by_track=_common_grades_all5(),
        specific_grades_by_track=_specific_grades_all5(),
    )


def _assert_json_serializable(value: object) -> None:
    # Round-trips through JSON unchanged → fully JSON-serializable + stable.
    assert json.loads(json.dumps(value, ensure_ascii=False)) == value


# --------------------------------------------------------------------------- #
# 1. The assembled config passes the existing validators unchanged.
# --------------------------------------------------------------------------- #


def test_contract_passes_existing_validator():
    contract = build_inspiration_v3_contract()
    assert contract["schema_version"] == CATEGORY_EVALUATION_CONTRACT_VERSION
    # Must not raise.
    validate_category_evaluation_contract(contract)
    _assert_json_serializable(contract)


def test_classification_map_passes_existing_validator():
    contract = build_inspiration_v3_contract()
    track_keys = {t["key"] for t in contract["track_classification"]["tracks"]}
    classification_map = build_inspiration_classification_map()
    assert classification_map["min_confidence"] == 0.6
    # Every target and out_of_scope target is a real contract track key.
    validate_classification_map(classification_map, valid_track_keys=track_keys)
    assert classification_map["out_of_scope_subcategory"] == TRACK_CLASS_THREE
    _assert_json_serializable(classification_map)


def test_each_subcategory_dimensions_passes_existing_validator():
    configs = build_inspiration_subcategory_dimensions()
    assert set(configs) == {TRACK_CLASS_ONE, TRACK_CLASS_TWO, TRACK_CLASS_THREE}
    expected_max = {TRACK_CLASS_ONE: 60, TRACK_CLASS_TWO: 60, TRACK_CLASS_THREE: 30}
    for track_key, config in configs.items():
        # Must not raise.
        validate_subcategory_dimensions(config)
        assert config["dimension_max"] == expected_max[track_key]
        assert config["sub_category_key"] == track_key
        _assert_json_serializable(config)


def test_seed_version_constant():
    assert INSPIRATION_SEED_VERSION == "inspiration-category-seed-v1"


# --------------------------------------------------------------------------- #
# 2. End-to-end evaluate_one scenarios.
# --------------------------------------------------------------------------- #


def test_redline_hit_screenshot_short_circuits():
    result = _run(_precheck(reason=["是截图"]))
    assert result["redline"]["hit"] is True
    assert result["redline"]["hit_rules"] == ["screenshot"]
    assert result["resolved"] is None
    agg = result["result"]
    assert agg["hard_reject"] is True
    assert agg["terminated_at"] == "redline"
    assert agg["level"] == "L5"
    assert agg["score"] == 49
    _assert_json_serializable(result)


def test_architecture_grade5_real_photo_class_one_100_l1():
    result = _run(_precheck(category="建筑设计", trait="实景照片", confidence=0.95))
    assert result["redline"]["hit"] is False
    assert result["resolved"]["track_key"] == TRACK_CLASS_ONE
    assert result["resolved"]["resolved_by"] == "mapped"
    agg = result["result"]
    assert agg["track_key"] == TRACK_CLASS_ONE
    assert agg["base_score"] == 40
    assert agg["dimension_max"] == 60
    assert agg["score"] == 100
    assert agg["level"] == "L1"
    assert agg["hard_reject"] is False
    _assert_json_serializable(result)


def test_product_grade5_class_two_capped_80():
    result = _run(_precheck(category="产品设计", trait="实景照片", confidence=0.95))
    assert result["resolved"]["track_key"] == TRACK_CLASS_TWO
    agg = result["result"]
    assert agg["track_key"] == TRACK_CLASS_TWO
    assert agg["base_score"] == 20
    assert agg["dimension_max"] == 60
    # 20 + 60 = 80, at (and never above) the class_two track cap of 80.
    assert agg["score"] <= 80
    assert agg["score"] == 80
    assert agg["level"] == "L1"
    _assert_json_serializable(result)


def test_low_confidence_falls_back_to_class_three_default():
    # 建筑设计 would map to class_one, but confidence < min_confidence (0.6)
    # forces the default track (class_three).
    result = _run(_precheck(category="建筑设计", trait="实景照片", confidence=0.4))
    assert result["resolved"]["track_key"] == TRACK_CLASS_THREE
    assert result["resolved"]["resolved_by"] == "low_confidence"
    assert result["resolved"]["needs_review"] is True
    agg = result["result"]
    assert agg["track_key"] == TRACK_CLASS_THREE
    assert agg["base_score"] == 40
    assert agg["dimension_max"] == 30
    # 40 + 30 = 70 = track cap; all-5 grades → no deductions.
    assert agg["score"] == 70
    assert agg["level"] == "L2"
    _assert_json_serializable(result)


def test_ai_image_media_penalty_minus_15_visible():
    real = _run(_precheck(category="建筑设计", trait="实景照片", confidence=0.95))
    ai = _run(_precheck(category="建筑设计", trait="AI图", confidence=0.95))
    # Both resolve to class_one; only the media type differs.
    assert real["result"]["track_key"] == TRACK_CLASS_ONE
    assert ai["result"]["track_key"] == TRACK_CLASS_ONE
    # AI image incurs the fixed -15 relative to the real-photo baseline.
    assert real["result"]["score"] == 100
    assert ai["result"]["score"] == 85
    assert real["result"]["score"] - ai["result"]["score"] == 15
    # The penalty is explainable in the media step evidence.
    media_step = next(s for s in ai["result"]["steps"] if s["step"] == "media")
    assert "-15" in media_step["note"]
    _assert_json_serializable(ai)


def test_out_of_scope_routes_to_class_three():
    result = _run(_precheck(category="任意", scope_status="out_of_scope", confidence=0.95))
    assert result["resolved"]["track_key"] == TRACK_CLASS_THREE
    assert result["resolved"]["resolved_by"] == "out_of_scope"


def test_evaluate_one_is_deterministic():
    precheck = _precheck(category="建筑设计", trait="实景照片", confidence=0.95)
    first = _run(precheck)
    second = _run(precheck)
    assert first == second
