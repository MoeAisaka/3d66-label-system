from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.dimension_deduction_bridge import (
    RULE_COMPOSITION_V2,
    compose_rule_deductions,
    compose_rule_scores,
    empty_deduction_output,
)
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


def _bonus_cap_config(
    *, cap: float = 90, deduction: float = 20, bonus: float = 8,
    deduction_cap: float | None = None,
) -> dict:
    return {
        "format_version": "subcategory-dimensions-v1",
        "sub_category_key": "class_one",
        "dimension_max": 30,
        "common_group": {
            "group_weight": 1.0,
            "schema_definition": {
                "dimensions": [
                    {
                        "key": "visual_structure",
                        "label": "视觉结构",
                        "weight": 1.0,
                        "dimension_score_cap": cap,
                        "deduction_rules": [
                            {
                                "rule_id": "structure_defect",
                                "description": "视觉结构存在明显缺陷",
                                "deduction": deduction,
                                "tags": ["结构"],
                            }
                        ],
                        "bonus_rules": [
                            {
                                "rule_id": "structure_strength",
                                "description": "视觉结构层级清晰完整",
                                "bonus": bonus,
                                "tags": ["结构"],
                            }
                        ],
                    }
                ]
            },
        },
        "specific_group": None,
    }


def _set_dimension_deduction_cap(config: dict, cap: float | None) -> dict:
    dimension = config["common_group"]["schema_definition"]["dimensions"][0]
    if cap is None:
        dimension.pop("dimension_deduction_cap", None)
    else:
        dimension["dimension_deduction_cap"] = cap
    return config


def _bonus_cap_hits(*, deduction: bool = False, bonus: bool = False) -> dict:
    return {
        "dimensions": {
            "visual_structure": {
                "hit_rules": (
                    [
                        {
                            "rule_id": "structure_defect",
                            "confidence": "high",
                            "evidence": "主体结构出现断裂",
                        }
                    ]
                    if deduction
                    else []
                ),
                "hit_bonus_rules": (
                    [
                        {
                            "rule_id": "structure_strength",
                            "confidence": "high",
                            "evidence": "前中后景层次明确",
                        }
                    ]
                    if bonus
                    else []
                ),
            }
        },
        "overall_note": "",
    }


def test_bonus_offsets_deduction_before_cap() -> None:
    result = compose_rule_scores(
        config=_bonus_cap_config(),
        dimension_output=_bonus_cap_hits(deduction=True, bonus=True),
    )
    evidence = result["evidence"]["visual_structure"]
    assert result["composition_version"] == RULE_COMPOSITION_V2
    assert evidence["raw_dimension_score"] == 88
    assert evidence["dimension_score"] == 88
    assert evidence["point_contribution"] == 26.4
    assert result["deductions"]["visual_structure"] == 3.6


def test_dimension_cap_limits_unpenalized_dimension() -> None:
    result = compose_rule_scores(
        config=_bonus_cap_config(cap=80),
        dimension_output=_bonus_cap_hits(),
    )
    evidence = result["evidence"]["visual_structure"]
    assert evidence["raw_dimension_score"] == 100
    assert evidence["dimension_score"] == 80
    assert evidence["cap_applied"] is True
    assert result["deductions"]["visual_structure"] == 6


def test_dimension_deduction_cap_limits_only_cumulative_deductions() -> None:
    config = _set_dimension_deduction_cap(_bonus_cap_config(deduction=40, bonus=8), 50)
    dimension = config["common_group"]["schema_definition"]["dimensions"][0]
    dimension["deduction_rules"] += [
        {
            "rule_id": f"structure_defect_{index}",
            "description": f"视觉结构规则{index}命中",
            "deduction": 40,
            "tags": ["结构"],
        }
        for index in range(2)
    ]
    hits = _bonus_cap_hits(deduction=True, bonus=True)
    hits["dimensions"]["visual_structure"]["hit_rules"] += [
        {
            "rule_id": f"structure_defect_{index}",
            "confidence": "high",
            "evidence": "同一维度继续命中",
        }
        for index in range(2)
    ]

    result = compose_rule_scores(config=config, dimension_output=hits)
    evidence = result["evidence"]["visual_structure"]
    assert evidence["raw_rule_deduction"] == 120
    assert evidence["dimension_deduction_cap"] == 50
    assert evidence["applied_rule_deduction"] == 50
    assert evidence["cap_applied"] is True
    assert evidence["dimension_score"] == 58
    assert evidence["cap_reason"] == "维度累计扣分按 dimension_deduction_cap=50 封顶"


def test_dimension_deduction_cap_also_applies_to_deduction_v1_contracts() -> None:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    for dimension in config["common_group"]["schema_definition"]["dimensions"]:
        dimension["dimension_deduction_cap"] = 10
    first = config["common_group"]["schema_definition"]["dimensions"][0]
    output = empty_deduction_output(config)
    output["dimensions"][first["key"]]["hit_rules"] = [
        {
            "rule_id": rule["rule_id"],
            "confidence": "high",
            "evidence": "规则命中",
        }
        for rule in first["deduction_rules"][:2]
    ]
    result = compose_rule_deductions(config=config, dimension_output=output)
    evidence = result["evidence"][first["key"]]
    assert evidence["raw_rule_deduction"] > 10
    assert evidence["applied_rule_deduction"] == 10
    assert evidence["dimension_deduction_cap"] == 10


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
