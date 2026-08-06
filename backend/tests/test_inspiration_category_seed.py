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

from app.category_evaluation_aggregator import aggregate_category_evaluation
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
    build_inspiration_v3_rev3_contract,
    evaluate_one,
)


# --- shared grade fixtures (simulate 调用B): all-5 = full marks, zero deductions ---

# 方案 A 真实维度：一类/二类 6 维度、三类 5 维度，全部在 common_group，specific 置空。
_CLASS_ONE_TWO_KEYS = (
    "visual_structure",
    "color_aesthetics",
    "emotional_expression",
    "design_aesthetics",
    "originality",
    "design_trendiness",
)
_CLASS_THREE_KEYS = (
    "subject_focus",
    "mood_atmosphere",
    "composition_lighting",
    "reference_value",
    "visual_impact",
)
_COMMON_KEYS_BY_TRACK = {
    TRACK_CLASS_ONE: _CLASS_ONE_TWO_KEYS,
    TRACK_CLASS_TWO: _CLASS_ONE_TWO_KEYS,
    TRACK_CLASS_THREE: _CLASS_THREE_KEYS,
}


def _common_grades_all5() -> dict:
    return {
        track: {key: 5 for key in keys}
        for track, keys in _COMMON_KEYS_BY_TRACK.items()
    }


def _specific_grades_all5() -> dict:
    # specific_group 现为空组，不再需要特有 grade；保留空映射以对齐 evaluate_one 签名。
    return {track: {} for track in _COMMON_KEYS_BY_TRACK}


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


def _run(precheck: dict, *, contract: dict | None = None) -> dict:
    return evaluate_one(
        contract=contract or build_inspiration_v3_rev3_contract(),
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
    assert INSPIRATION_SEED_VERSION == "inspiration-category-seed-v7-quality-gates"


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
    assert agg["score"] == 20
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
    assert agg["level"] == "L2"
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


def test_media_type_penalty_is_disabled_for_new_inspiration_contract():
    real = _run(_precheck(category="建筑设计", trait="实景照片", confidence=0.95))
    ai = _run(_precheck(category="建筑设计", trait="AI图", confidence=0.95))
    # Both resolve to class_one; only the media type differs.
    assert real["result"]["track_key"] == TRACK_CLASS_ONE
    assert ai["result"]["track_key"] == TRACK_CLASS_ONE
    # 新体系只保留 trait 供下游消费，不按媒介扣分。
    assert real["result"]["score"] == 100
    assert ai["result"]["score"] == 100
    assert real["result"]["score"] == ai["result"]["score"]
    assert ai["result"]["media_penalty_enabled"] is False
    assert any(s["step"] == "media_skipped" for s in ai["result"]["steps"])
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


# --------------------------------------------------------------------------- #
# 3. 方案 A 真实 6/5 维度：key/权重/结构断言 + hard_defects 一票压分。
# --------------------------------------------------------------------------- #

_WEIGHT_TOLERANCE = 1e-9


def _common_dims(config: dict) -> list[dict]:
    return config["common_group"]["schema_definition"]["dimensions"]


def test_class_one_two_have_six_real_dimensions_with_raw_business_weights():
    configs = build_inspiration_subcategory_dimensions()
    for track_key in (TRACK_CLASS_ONE, TRACK_CLASS_TWO):
        config = configs[track_key]
        dims = _common_dims(config)
        assert [d["key"] for d in dims] == list(_CLASS_ONE_TWO_KEYS)
        assert config["dimension_max"] == 60
        # 全部维度在 common_group，specific_group 为空。
        assert config["common_group"]["group_weight"] == 1.0
        assert _common_dims and not config["specific_group"]["schema_definition"]["dimensions"]
        assert [d["weight"] for d in dims] == [0.10, 0.10, 0.05, 0.10, 0.10, 0.15]
        assert abs(sum(d["weight"] for d in dims) - 0.60) <= _WEIGHT_TOLERANCE
        for d in dims:
            assert d["grade_points"] == {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0}


def test_class_three_has_five_real_dimensions_with_raw_business_weights():
    config = build_inspiration_subcategory_dimensions()[TRACK_CLASS_THREE]
    dims = _common_dims(config)
    assert [d["key"] for d in dims] == list(_CLASS_THREE_KEYS)
    assert config["dimension_max"] == 30
    assert config["common_group"]["group_weight"] == 1.0
    assert not config["specific_group"]["schema_definition"]["dimensions"]
    assert abs(sum(d["weight"] for d in dims) - 0.30) <= _WEIGHT_TOLERANCE
    # 业务权重每维 0.06，对应 100 分维度分中的 6 分。
    for d in dims:
        assert abs(d["weight"] - 0.06) <= _WEIGHT_TOLERANCE


def test_contract_freezes_level_boundaries_and_versions_rev3_rev4_actions():
    contract = build_inspiration_v3_contract()
    assert contract["spec_version"] == "inspiration-v3-aesthetic-evidence-v4-quality-gates-20260807"
    assert contract["level_thresholds"] == [
        {"min_score": 90, "level": "L1"},
        {"min_score": 75, "level": "L2"},
        {"min_score": 60, "level": "L3"},
        {"min_score": 0, "level": "L4"},
    ]
    modifiers = contract["common_modifiers"]
    assert modifiers["format_version"] == "common-modifiers-v2"
    assert modifiers["media_type_penalty"]["enabled"] is False
    veto = modifiers["high_score_veto"]
    assert veto["policy_version"] == "hard-defect-severity-v1"
    assert veto["tiers"]["A"]["cap_to"] == 20
    assert veto["tiers"]["B"]["cap_to"] == 60
    assert veto["tiers"]["record_only"]["action"] == "record_only"

    rev3 = build_inspiration_v3_rev3_contract()
    assert rev3["spec_version"] == "inspiration-v2-human-calibrated-20260805"
    legacy_veto = rev3["common_modifiers"]["high_score_veto"]
    assert (legacy_veto["threshold"], legacy_veto["cap_to"]) == (80, 79)
    assert len(legacy_veto["rules"]) == 10


def test_level_mapping_boundaries_80_is_l2_and_81_is_l1():
    contract = build_inspiration_v3_rev3_contract()
    precheck = _precheck(category="建筑设计", trait="实景照片")
    score_80 = aggregate_category_evaluation(
        contract, precheck, {"deductions": {"d": 20}}, track_key=TRACK_CLASS_ONE
    )
    score_81 = aggregate_category_evaluation(
        contract, precheck, {"deductions": {"d": 19}}, track_key=TRACK_CLASS_ONE
    )
    assert (score_80["score"], score_80["level"]) == (80, "L2")
    assert (score_81["score"], score_81["level"]) == (81, "L1")


def test_rev3_high_score_veto_replays_cap_79():
    # 历史 rev3 一类全 grade5 命中硬伤仍按冻结行为压至 79。
    result = _run(
        _precheck(
            category="建筑设计",
            trait="实景照片",
            confidence=0.95,
            hard_defects=["garish_color"],
        ),
        contract=build_inspiration_v3_rev3_contract(),
    )
    agg = result["result"]
    assert agg["track_key"] == TRACK_CLASS_ONE
    assert agg["score"] == 79
    # raw_level（未压分前）仍是 L1，压分后落 L2。
    assert agg["raw_level"] == "L1"
    assert agg["level"] == "L2"
    veto_cap = next(c for c in agg["caps"] if c["cap"] == "high_score_veto")
    assert "79" in veto_cap["reason"]
    _assert_json_serializable(result)


def test_high_score_veto_not_triggered_without_hard_defects():
    # 无 hard_defects → 不压分，维持 100/L1（验证压分是条件性的）。
    result = _run(_precheck(category="建筑设计", trait="实景照片", confidence=0.95))
    assert result["result"]["score"] == 100
    assert result["result"]["level"] == "L1"
