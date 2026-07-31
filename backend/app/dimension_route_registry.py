from __future__ import annotations

from copy import deepcopy
from typing import Any

from .dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    DEFINITION_FORMAT_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
    canonical_json,
    space_schema_definition_for_version,
)


ROUTE_POLICY_DEFINITION_FORMAT_VERSION = "dimension-route-policy-definition-v1"
ROUTE_POLICY_KEY = "material-family-routing"
ROUTE_POLICY_VERSION = "0.1.0-candidate"
CORE_SCHEMA_KEY = "common_core"
CORE_SCHEMA_VERSION = "1.0.0"
PRODUCT_SCHEMA_KEY = "product_aesthetic"
PRODUCT_SCHEMA_VERSION = "0.1.0-candidate"

CORE_DIMENSION_KEYS = (
    "presentation_integrity",
    "visual_hierarchy",
    "inspiration_reference",
)
PRODUCT_FAMILY_DIMENSION_KEYS = (
    "product_form_proportion",
    "material_craft_detail",
    "functional_clarity",
    "scene_styling_fit",
)

_GRADE_POINTS = {"1": 20.0, "2": 45.0, "3": 65.0, "4": 82.0, "5": 95.0}
_LEVEL_THRESHOLDS = {"L2": 40.0, "L3": 60.0, "L4": 75.0, "L5": 90.0}


def _active_space_definition() -> dict[str, Any]:
    return space_schema_definition_for_version(ACTIVE_V13_VERSION)


def _core_dimension_sources() -> list[dict[str, Any]]:
    dimensions = {
        item["key"]: deepcopy(item)
        for item in _active_space_definition()["dimensions"]
    }
    return [dimensions[key] for key in CORE_DIMENSION_KEYS]


def common_core_definition() -> dict[str, Any]:
    dimensions: list[dict[str, Any]] = []
    for order, source in enumerate(_core_dimension_sources(), start=1):
        dimension = deepcopy(source)
        dimension.update(
            {
                "layer": "core",
                "display_order": order,
                "weight": None,
                "aggregation_role": "shared_semantic",
                "prompt_fragment_id": None,
            }
        )
        dimensions.append(dimension)
    return {
        "format_version": DEFINITION_FORMAT_VERSION,
        "package_key": "common",
        "package_version": "v1",
        "compatibility_revision": "cross_family_core_v1",
        "core_dimension_keys": list(CORE_DIMENSION_KEYS),
        "dimensions": dimensions,
        "grade_points": deepcopy(_GRADE_POINTS),
        "semantic_contract": {
            "keys_are_cross_family_stable": True,
            "weights_are_assigned_by_family_pack": True,
            "standalone_total_score": False,
        },
        "output_contract": {
            "output_contract_version": "common-core-output-v1",
            "dimension_output_keys": list(CORE_DIMENSION_KEYS),
            "unknown_key_policy": "reject",
            "extra_evidence_policy": "allow_text_only",
        },
    }


def _family_dimension(
    *,
    key: str,
    label: str,
    display_order: int,
    weight: float,
    anchors: dict[str, str],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "layer": "family",
        "execution_mode": "deterministic",
        "value_type": "integer_1_to_5",
        "required": True,
        "display_order": display_order,
        "weight": weight,
        "grade_points": deepcopy(_GRADE_POINTS),
        "anchors": anchors,
        "evidence_rule": {
            "minimum": 1,
            "grade_4_minimum": 2,
            "grade_5_minimum": 3,
        },
        "missing_policy": "reject",
        "aggregation_role": "score",
        "prompt_fragment_id": None,
        "aliases": [],
        "label_field_bindings": [],
    }


def product_candidate_definition() -> dict[str, Any]:
    core_definition = common_core_definition()
    core_hash = canonical_hash(core_definition)
    core_weights = {
        "presentation_integrity": 0.15,
        "visual_hierarchy": 0.10,
        "inspiration_reference": 0.10,
    }
    dimensions: list[dict[str, Any]] = []
    for source in core_definition["dimensions"]:
        dimension = deepcopy(source)
        dimension.update(
            {
                "display_order": len(dimensions) + 1,
                "weight": core_weights[dimension["key"]],
                "aggregation_role": "score",
            }
        )
        dimensions.append(dimension)
    dimensions.extend(
        (
            _family_dimension(
                key="product_form_proportion",
                label="造型与比例",
                display_order=4,
                weight=0.20,
                anchors={
                    "1": "造型关系明显失衡，比例或结构妨碍理解和使用",
                    "3": "造型和比例基本成立，但常规或存在局部不协调",
                    "5": "造型语言清晰，比例成熟，结构关系具有代表性",
                },
            ),
            _family_dimension(
                key="material_craft_detail",
                label="材质与工艺细节",
                display_order=5,
                weight=0.18,
                anchors={
                    "1": "材质表达失真或工艺问题明显，细节缺乏可信度",
                    "3": "材质与工艺基本可辨，完成度普通",
                    "5": "材质选择、工艺节点和细节完成度成熟且可信",
                },
            ),
            _family_dimension(
                key="functional_clarity",
                label="功能表达与可理解性",
                display_order=6,
                weight=0.15,
                anchors={
                    "1": "主要用途难以理解，关键功能关系缺失或冲突",
                    "3": "基本用途可理解，但表达普通或信息不够充分",
                    "5": "用途、结构与操作关系清楚，功能价值可直接理解",
                },
            ),
            _family_dimension(
                key="scene_styling_fit",
                label="场景搭配适配",
                display_order=7,
                weight=0.12,
                anchors={
                    "1": "对象与场景、搭配或尺度关系明显冲突",
                    "3": "基础适配成立，但搭配常规或环境信息有限",
                    "5": "对象与场景关系自然，搭配强化产品特点与使用想象",
                },
            ),
        )
    )
    dimension_keys = [item["key"] for item in dimensions]
    return {
        "format_version": DEFINITION_FORMAT_VERSION,
        "package_key": "product",
        "package_version": "v0.1-candidate",
        "compatibility_revision": "first_product_calibration_candidate",
        "core_schema_ref": {
            "schema_key": CORE_SCHEMA_KEY,
            "version": CORE_SCHEMA_VERSION,
            "canonical_hash": core_hash,
        },
        "core_dimension_keys": list(CORE_DIMENSION_KEYS),
        "family_dimension_keys": list(PRODUCT_FAMILY_DIMENSION_KEYS),
        "dimensions": dimensions,
        "aggregation": {
            "engine_version": "engine-v2.5.0",
            "grade_points": deepcopy(_GRADE_POINTS),
            "level_thresholds": deepcopy(_LEVEL_THRESHOLDS),
            "weight_sum_rule": "strictly_equals_1",
            "collapse_rule": {
                "same_grade_ratio_for_review": 0.75,
                "minimum_dimension_count": 3,
            },
            "high_evidence_rule": {
                "high_grade_minimum": 4,
                "insufficient_evidence_ratio_for_cap": 0.25,
                "cap_level": "L3",
            },
            "top_level_rule": {
                "grade_five_ratio_minimum": 0.6,
                "other_dimension_minimum_grade": 4,
                "minimum_confidence": 0.9,
                "requires_no_model_review": True,
            },
            "decision_rule_policy": {
                "hard_gate_target": "L1",
                "allowed_level_caps": ["L1", "L2", "L3", "L4"],
            },
            "score_round_digits": 2,
        },
        "risk_review": {
            "version": "product-risk-review-candidate-v0.1",
            "dimension_keys": dimension_keys,
            "conservative_rules": {
                "may_raise_conclusion": False,
                "uncertain_or_confidence_below": 0.7,
                "uncertain_action": "needs_review",
            },
        },
        "output_contract": {
            "output_contract_version": "product-aesthetic-output-candidate-v0.1",
            "dimension_output_keys": dimension_keys,
            "label_field_set_id": "product-label-fields-candidate-v0.1",
            "label_fields_snapshot": [],
            "unknown_key_policy": "reject",
            "extra_evidence_policy": "allow_text_only",
        },
        "prompt_contract": {
            "status": "missing",
            "required_stage": "B",
            "publishing_blocked": True,
        },
        "release_gate": {
            "minimum_calibration_samples": 50,
            "target_calibration_samples": 100,
            "required_sample_roles": [
                "target_error",
                "stable_control",
                "blind_holdout",
            ],
            "completed_calibration_samples": 0,
            "status": "not_started",
            "publishing_blocked": True,
            "blocked_reasons": [
                "manual_calibration_incomplete",
                "prompt_contract_missing",
            ],
        },
    }


def _schema_ref(
    *,
    schema_key: str,
    version: str,
    family_key: str,
    status: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_key": schema_key,
        "version": version,
        "family_key": family_key,
        "status": status,
        "canonical_hash": canonical_hash(definition),
    }


def route_policy_definition() -> dict[str, Any]:
    space_definition = _active_space_definition()
    core_definition = common_core_definition()
    product_definition = product_candidate_definition()
    core_ref = _schema_ref(
        schema_key=CORE_SCHEMA_KEY,
        version=CORE_SCHEMA_VERSION,
        family_key="common",
        status="published",
        definition=core_definition,
    )
    return {
        "format_version": ROUTE_POLICY_DEFINITION_FORMAT_VERSION,
        "policy_key": ROUTE_POLICY_KEY,
        "policy_version": ROUTE_POLICY_VERSION,
        "activation_scope": "calibration_only",
        "input_contract": {
            "allowed_paths": [
                "classification.scope_status",
                "classification.primary_category",
                "classification.primary_confidence",
                "scene_scope.type",
                "media_form.white_background_product.status",
                "image_quality.quality_severity",
                "needs_review",
            ],
            "unknown_value_policy": "core_fallback_needs_review",
        },
        "unassessable_rules": [
            {
                "path": "image_quality.quality_severity",
                "operator": "equal",
                "value": "unusable",
                "reason": "image_unusable",
            }
        ],
        "category_family_map": {
            "住宅设计": "space",
            "酒店民宿": "space",
            "餐饮店铺": "space",
            "地产/办公": "space",
            "娱乐场所": "space",
            "文体医疗": "space",
            "通用空间": "space",
            "建筑景观": "space",
            "硬装结构": "space",
            "软装家具": "product",
            "软装饰品": "product",
            "灯具照明": "product",
            "布艺地毯": "product",
            "平面设计": "graphic",
            "意向图": "intent",
        },
        "family_routes": {
            "space": {
                "mode": "family_pack",
                "schema_ref": _schema_ref(
                    schema_key=SPACE_SCHEMA_KEY,
                    version=ACTIVE_V13_VERSION,
                    family_key="space",
                    status="published",
                    definition=space_definition,
                ),
            },
            "product": {
                "mode": "family_pack",
                "schema_ref": _schema_ref(
                    schema_key=PRODUCT_SCHEMA_KEY,
                    version=PRODUCT_SCHEMA_VERSION,
                    family_key="product",
                    status="candidate",
                    definition=product_definition,
                ),
            },
            "graphic": {
                "mode": "core_fallback",
                "schema_ref": deepcopy(core_ref),
            },
            "intent": {
                "mode": "core_fallback",
                "schema_ref": deepcopy(core_ref),
            },
            "common": {
                "mode": "core_fallback",
                "schema_ref": deepcopy(core_ref),
            },
        },
        "product_signals": {
            "white_background_product_status": "yes",
            "scene_scope_types": ["object_only"],
        },
        "conflict_policy": "core_fallback_needs_review",
        "unknown_family_policy": "core_fallback_needs_review",
    }


def materialized_p2_dimension_schema_rows() -> list[dict[str, Any]]:
    core_definition = common_core_definition()
    product_definition = product_candidate_definition()
    rows = [
        {
            "schema_key": CORE_SCHEMA_KEY,
            "version": CORE_SCHEMA_VERSION,
            "schema_type": "core",
            "family_key": "common",
            "display_name": "通用核心维 v1",
            "status": "published",
            "definition": core_definition,
        },
        {
            "schema_key": PRODUCT_SCHEMA_KEY,
            "version": PRODUCT_SCHEMA_VERSION,
            "schema_type": "family_pack",
            "family_key": "product",
            "display_name": "单品包 v0.1｜人工校准候选",
            "status": "candidate",
            "core_schema_ref": {
                "schema_key": CORE_SCHEMA_KEY,
                "version": CORE_SCHEMA_VERSION,
                "canonical_hash": canonical_hash(core_definition),
            },
            "definition": product_definition,
        },
    ]
    result = deepcopy(rows)
    for row in result:
        definition = row.pop("definition")
        row["definition_json"] = canonical_json(definition)
        row["canonical_hash"] = canonical_hash(definition)
    return result


def materialized_route_policy_rows() -> list[dict[str, Any]]:
    definition = route_policy_definition()
    return [
        {
            "policy_key": ROUTE_POLICY_KEY,
            "version": ROUTE_POLICY_VERSION,
            "display_name": "四素材族路由策略 v0.1｜校准候选",
            "status": "candidate",
            "definition_json": canonical_json(definition),
            "canonical_hash": canonical_hash(definition),
        }
    ]
