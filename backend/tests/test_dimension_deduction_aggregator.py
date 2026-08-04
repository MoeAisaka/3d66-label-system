from __future__ import annotations

from copy import deepcopy

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.dimension_deduction_bridge import compose_rule_deductions, empty_deduction_output
from app.inspiration_category_seed import (
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)


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
