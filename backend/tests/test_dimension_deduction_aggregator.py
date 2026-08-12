from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.dimension_deduction_bridge import compose_rule_deductions, empty_deduction_output
from app.inspiration_category_seed import (
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
    build_inspiration_v3_rev3_contract,
)
from app.schema_adapter import adapt_inspiration_call_a_precheck


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "inspiration_run14_hard_defects.json"


def _precheck() -> dict:
    return {
        "classification": {"primary_category": "建筑设计", "primary_confidence": 0.95},
        "production_fields": {"reason": [], "trait": "实景照片"},
    }


def test_rule_hits_accumulate_into_weighted_dimension_deductions() -> None:
    contract = build_inspiration_v3_contract()
    config = build_inspiration_subcategory_dimensions()["class_one"]
    output = empty_deduction_output(config)
    first_definition = config["common_group"]["schema_definition"]["dimensions"][0]
    first_key = first_definition["key"]
    first = output["dimensions"][first_key]
    rules = first_definition["deduction_rules"]
    first["hit_rules"] = [
        {"rule_id": rules[0]["rule_id"], "confidence": "high", "evidence": "主体偏移"},
        {"rule_id": rules[1]["rule_id"], "confidence": "medium", "evidence": "重心不稳"},
    ]
    composed = compose_rule_deductions(config=config, dimension_output=output)
    result = aggregate_category_evaluation(contract, _precheck(), composed, track_key="class_one")
    assert composed["evidence"][first_key]["applied_rule_deduction"] == sum(
        rule["deduction"] for rule in rules[:2]
    )
    assert result["score"] < 100
    assert result["steps"][2]["step"] == "dimension_rule_deduction"
    assert "维度扣分（规则命中）" in result["steps"][2]["note"]


def test_dimension_rule_sum_is_clamped_at_zero_score() -> None:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    dimension = config["common_group"]["schema_definition"]["dimensions"][0]
    dimension["deduction_rules"] = [
        {"rule_id": "severe_a", "description": "严重缺陷一", "deduction": 80, "tags": []},
        {"rule_id": "severe_b", "description": "严重缺陷二", "deduction": 80, "tags": []},
    ]
    output = empty_deduction_output(config)
    output["dimensions"][dimension["key"]]["hit_rules"] = [
        {"rule_id": "severe_a", "confidence": "high", "evidence": "缺陷一"},
        {"rule_id": "severe_b", "confidence": "high", "evidence": "缺陷二"},
    ]
    composed = compose_rule_deductions(config=config, dimension_output=output)
    evidence = composed["evidence"][dimension["key"]]
    assert evidence["raw_rule_deduction"] == 160
    assert evidence["applied_rule_deduction"] == 100
    assert evidence["dimension_score"] == 0


def test_raw_business_weights_reproduce_manual_example_as_78() -> None:
    """一类 [80,70,60,90,50,40] → 维度池38 → 40+38=78。"""
    contract = build_inspiration_v3_contract()
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    dimensions = config["common_group"]["schema_definition"]["dimensions"]
    desired_scores = [80, 70, 60, 90, 50, 40]
    output = empty_deduction_output(config)
    for dimension, desired_score in zip(dimensions, desired_scores, strict=True):
        deduction = 100 - desired_score
        dimension["deduction_rules"] = [
            {
                "rule_id": "manual_example",
                "description": "手算样例命中",
                "deduction": deduction,
                "tags": ["手算验证"],
            }
        ]
        output["dimensions"][dimension["key"]]["hit_rules"] = [
            {
                "rule_id": "manual_example",
                "confidence": "high",
                "evidence": "手算样例",
            }
        ]
    composed = compose_rule_deductions(config=config, dimension_output=output)
    result = aggregate_category_evaluation(
        contract, _precheck(), composed, track_key="class_one"
    )
    assert sum(composed["deductions"].values()) == 22
    assert result["score"] == 78


def test_run14_hard_defect_fixtures_replay_rev3_exactly() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    contract = build_inspiration_v3_rev3_contract()
    dimensions = build_inspiration_subcategory_dimensions()

    for item_id, fixture in fixtures.items():
        call_a = fixture["call_a"]
        track_key = call_a["track_classification"]
        precheck = adapt_inspiration_call_a_precheck(call_a)
        composed = compose_rule_deductions(
            config=dimensions[track_key],
            dimension_output=fixture["call_b"],
        )
        result = aggregate_category_evaluation(
            contract, precheck, composed, track_key=track_key
        )
        expected = fixture["expected_rev3"]
        assert (result["score"], result["level"]) == (
            expected["score"],
            expected["level"],
        ), item_id

def _deductions(total: int) -> dict:
    return {"deductions": {"fixture": total}}


def _defect_precheck(
    *,
    hard_defects: list[str] | None = None,
    image_defects: list[str] | None = None,
) -> dict:
    precheck = _precheck()
    precheck["hard_defects"] = hard_defects or []
    precheck["image_defects"] = image_defects or []
    return precheck


def test_run14_tier_a_defects_are_l5_only_under_rev4() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    contract = build_inspiration_v3_contract()
    dimensions = build_inspiration_subcategory_dimensions()

    for item_id, fixture in fixtures.items():
        call_a = fixture["call_a"]
        track_key = call_a["track_classification"]
        composed = compose_rule_deductions(
            config=dimensions[track_key],
            dimension_output=fixture["call_b"],
        )
        result = aggregate_category_evaluation(
            contract,
            adapt_inspiration_call_a_precheck(call_a),
            composed,
            track_key=track_key,
        )
        assert (result["score"], result["level"]) == (20, "L4"), item_id
        assert result["hard_defect_action"]["resolved_tier"] == "A"


def test_rev4_veto_is_monotonic_and_escalates_three_tier_b_hits() -> None:
    contract = build_inspiration_v3_contract()
    one_b = _defect_precheck(hard_defects=["severe_color_cast"])
    at_80 = aggregate_category_evaluation(
        contract, one_b, _deductions(20), track_key="class_one"
    )
    at_79 = aggregate_category_evaluation(
        contract, one_b, _deductions(21), track_key="class_one"
    )
    assert at_80["score"] == at_79["score"] == 60
    assert at_80["hard_defect_action"]["resolved_tier"] == "B"
    assert at_79["hard_defect_action"]["resolved_tier"] == "B"

    escalated = aggregate_category_evaluation(
        contract,
        _defect_precheck(
            hard_defects=["severe_color_cast", "fake_material", "garish_color"]
        ),
        _deductions(0),
        track_key="class_one",
    )
    assert (escalated["score"], escalated["level"]) == (20, "L4")
    assert escalated["hard_defect_action"]["escalated"] is True


def test_rev4_watermark_actions_and_known_photo_modifier() -> None:
    contract = build_inspiration_v3_contract()
    corner = aggregate_category_evaluation(
        contract,
        _defect_precheck(image_defects=["corner_small_watermark"]),
        _deductions(0),
        track_key="class_one",
    )
    assert (corner["score"], corner["level"]) == (100, "L1")
    assert corner["hard_defect_action"]["resolved_tier"] == "record_only"

    for watermark in ("subject_obscuring_watermark", "large_area_watermark"):
        result = aggregate_category_evaluation(
            contract,
            _defect_precheck(image_defects=[watermark]),
            _deductions(0),
            track_key="class_one",
        )
        assert (result["score"], result["level"]) == (20, "L4")

    modifier = aggregate_category_evaluation(
        contract,
        _defect_precheck(
            hard_defects=["known_real_photo_defect", "careless_composition"]
        ),
        _deductions(0),
        track_key="class_one",
    )
    assert modifier["score"] == 60
    assert modifier["hard_defect_action"]["modifier_applied"] is True


def test_rev4_high_quality_guardrail_and_four_redlines() -> None:
    contract = build_inspiration_v3_contract()
    good = aggregate_category_evaluation(
        contract, _defect_precheck(), _deductions(0), track_key="class_one"
    )
    assert (good["score"], good["level"]) == (100, "L1")
    for reason in ("是截图", "有大面积文字说明", "有二维码"):
        precheck = _defect_precheck()
        precheck["production_fields"]["reason"] = [reason]
        result = aggregate_category_evaluation(
            contract, precheck, _deductions(0), track_key="class_one"
        )
        assert (result["score"], result["level"]) == (20, "L5")

    casual_only = _defect_precheck()
    casual_only["production_fields"]["reason"] = ["是随手拍"]
    without_disorder = aggregate_category_evaluation(
        contract, casual_only, _deductions(0), track_key="class_one"
    )
    assert (without_disorder["score"], without_disorder["level"]) == (100, "L1")

    casual_disorder = _defect_precheck()
    casual_disorder["production_fields"]["reason"] = ["是随手拍"]
    casual_disorder["hard_defects"] = ["careless_composition"]
    with_disorder = aggregate_category_evaluation(
        contract, casual_disorder, _deductions(0), track_key="class_one"
    )
    assert (with_disorder["score"], with_disorder["level"]) == (20, "L5")
