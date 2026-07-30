from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


DEFINITION_FORMAT_VERSION = "dimension-schema-definition-v1"
SPACE_SCHEMA_KEY = "space_aesthetic"
SPACE_PACKAGE_VERSION = "v1"
HISTORICAL_DEFAULT_VERSION = "1.0.0-legacy-default"
ACTIVE_V13_VERSION = "1.3.0"

_GRADE_POINTS = {"1": 20.0, "2": 45.0, "3": 65.0, "4": 82.0, "5": 95.0}
_LEVEL_THRESHOLDS = {"L2": 40.0, "L3": 60.0, "L4": 75.0, "L5": 90.0}
_HISTORICAL_DEFAULT_WEIGHTS = {
    "composition_viewpoint": 0.15,
    "lighting_atmosphere": 0.12,
    "color_material": 0.12,
    "spatial_design_furnishing": 0.18,
    "visual_hierarchy": 0.10,
    "detail_completion": 0.10,
    "inspiration_reference": 0.08,
    "presentation_integrity": 0.15,
}
_ACTIVE_V13_WEIGHTS = {
    "composition_viewpoint": 0.15,
    "lighting_atmosphere": 0.12,
    "color_material": 0.12,
    "spatial_design_furnishing": 0.16,
    "visual_hierarchy": 0.10,
    "detail_completion": 0.10,
    "inspiration_reference": 0.10,
    "presentation_integrity": 0.15,
}

_COMMON_GRADE_ANCHORS = {
    "1": "核心关系严重失效，几乎没有有效推荐价值",
    "2": "问题较多，明显低于普通合格水平，使用价值有限",
    "3": "基础成立、普通可用，但存在明确提升空间",
    "4": "明显良好、专业成熟，少量问题不影响整体",
    "5": "代表性优秀，优势具体且充分，具有很强推荐价值",
}

_DIMENSIONS = (
    {
        "key": "composition_viewpoint",
        "label": "构图与视角",
        "layer": "family",
        "anchors": {
            "1": "主体混乱、严重倾斜遮挡或几乎不能展示设计",
            "3": "基本可用但普通，展示作用大于摄影表达",
            "5": "视角经过明确选择，空间关系清晰，构图具有代表性并强化主题",
        },
    },
    {
        "key": "lighting_atmosphere",
        "label": "光线与氛围",
        "layer": "family",
        "anchors": {
            "1": "光线严重妨碍观察，主体和材质大量丢失",
            "3": "空间可辨认，但光线普通、偏平或控制有限",
            "5": "光影表达明确、层次丰富，主体与材质得到充分塑造",
        },
    },
    {
        "key": "color_material",
        "label": "色彩与材质",
        "layer": "family",
        "anchors": {
            "1": "色彩材质严重冲突或失真，无法形成有效表达",
            "3": "基础关系成立，但常规或缺少精细控制",
            "5": "配色与材质关系成熟、层次清晰、有辨识度且高度统一",
        },
    },
    {
        "key": "spatial_design_furnishing",
        "label": "空间设计与家具软装",
        "layer": "family",
        "anchors": {
            "1": "多个核心功能、比例或搭配关系严重错误",
            "3": "基础设计成立，但普通、保守或局部不协调",
            "5": "功能、比例、概念、家具软装和空间关系高度成熟",
        },
    },
    {
        "key": "visual_hierarchy",
        "label": "视觉层级",
        "layer": "core",
        "anchors": {
            "1": "没有有效焦点，视觉秩序混乱",
            "3": "主体可辨认，但层级普通",
            "5": "焦点明确，层级丰富，视线引导自然成熟",
        },
    },
    {
        "key": "detail_completion",
        "label": "细节完成度",
        "layer": "family",
        "anchors": {
            "1": "明显未完成或严重细节问题破坏整体",
            "3": "基础完整，但细节普通",
            "5": "节点和细节精确完整，经得起局部观察",
        },
    },
    {
        "key": "inspiration_reference",
        "label": "设计时效性与灵感参考价值",
        "layer": "core",
        "anchors": {
            "1": "缺少当前转化和有效参考价值",
            "3": "中性常规或偏保守，没有严重过时但新鲜感有限",
            "5": "具有趋势引领性，或是完成度极高、长期有效的经典设计",
        },
    },
    {
        "key": "presentation_integrity",
        "label": "呈现完整性",
        "layer": "core",
        "anchors": {
            "1": "呈现严重受损，几乎不具备推荐使用价值",
            "3": "正常可用，但存在一般画质或展示不足",
            "5": "技术质量和展示完整度达到优秀代表图水平",
        },
    },
)

_RISK_REVIEW = {
    "version": "risk-review-v1.1",
    "dimension_keys": [
        "composition_viewpoint",
        "lighting_atmosphere",
        "color_material",
        "spatial_design_furnishing",
        "visual_hierarchy",
        "detail_completion",
        "inspiration_reference",
        "presentation_integrity",
    ],
    "quality_rank": {
        "normal": 0,
        "slight": 1,
        "uncertain": 1,
        "moderate": 2,
        "severe": 3,
        "unusable": 4,
    },
    "cap_rank": {"none": 6, "L5": 5, "L4": 4, "L3": 3, "L2": 2, "L1": 1},
    "trigger_rules": {
        "professional_photography_status": "yes",
        "levels": ["L4", "L5"],
        "trigger_when_grade_five_count_gte": 1,
    },
    "conservative_rules": {
        "may_raise_conclusion": False,
        "uncertain_or_confidence_below": 0.7,
        "uncertain_action": "needs_review",
    },
}


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _space_definition(
    *,
    compatibility_revision: str,
    scoring_profile_match: dict[str, str],
    weights: dict[str, float],
) -> dict[str, Any]:
    dimensions = []
    for source in _DIMENSIONS:
        dimension = deepcopy(source)
        dimension.update(
            {
                "execution_mode": "deterministic",
                "value_type": "integer_1_to_5",
                "required": True,
                "display_order": len(dimensions) + 1,
                "weight": weights[dimension["key"]],
                "grade_points": deepcopy(_GRADE_POINTS),
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
        )
        dimensions.append(dimension)

    return {
        "format_version": DEFINITION_FORMAT_VERSION,
        "package_key": "space",
        "package_version": SPACE_PACKAGE_VERSION,
        "compatibility_revision": compatibility_revision,
        "scoring_profile_match": scoring_profile_match,
        "core_dimension_keys": [
            "presentation_integrity",
            "visual_hierarchy",
            "inspiration_reference",
        ],
        "common_grade_anchors": deepcopy(_COMMON_GRADE_ANCHORS),
        "dimensions": dimensions,
        "aggregation": {
            "engine_version": "engine-v2.5.0",
            "grade_points": deepcopy(_GRADE_POINTS),
            "level_thresholds": deepcopy(_LEVEL_THRESHOLDS),
            "weight_sum_rule": "strictly_equals_1",
            "collapse_rule": {
                "applies_to_scoring_profile": "space_aesthetic_v1.3",
                "all_equal_minimum_grade": 4,
                "same_grade_count_for_review": 6,
            },
            "high_evidence_rule": {
                "high_grade_minimum": 4,
                "minimum_evidence": 2,
                "dimensions_for_l3_cap": 2,
            },
            "top_level_rule": {
                "grade_five_minimum_count": 5,
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
        "risk_review": deepcopy(_RISK_REVIEW),
        "output_contract": {
            "output_contract_version": "space-aesthetic-output-v1",
            "dimension_output_keys": list(weights),
            "label_field_set_id": "space-label-fields-v1",
            "label_fields_snapshot": [],
            "unknown_key_policy": "reject",
            "extra_evidence_policy": "allow_text_only",
        },
    }


_MATERIALIZED_SCHEMAS = (
    {
        "schema_key": SPACE_SCHEMA_KEY,
        "version": HISTORICAL_DEFAULT_VERSION,
        "schema_type": "family_pack",
        "family_key": "space",
        "display_name": "空间包 v1｜历史默认修订",
        "status": "published",
        "definition": _space_definition(
            compatibility_revision="historical_default",
            scoring_profile_match={
                "operator": "not_equal",
                "value": "space_aesthetic_v1.3",
            },
            weights=_HISTORICAL_DEFAULT_WEIGHTS,
        ),
    },
    {
        "schema_key": SPACE_SCHEMA_KEY,
        "version": ACTIVE_V13_VERSION,
        "schema_type": "family_pack",
        "family_key": "space",
        "display_name": "空间包 v1｜现役 v1.3 修订",
        "status": "published",
        "definition": _space_definition(
            compatibility_revision="active_v1_3",
            scoring_profile_match={
                "operator": "equal",
                "value": "space_aesthetic_v1.3",
            },
            weights=_ACTIVE_V13_WEIGHTS,
        ),
    },
)


def materialized_space_schema_rows() -> list[dict[str, Any]]:
    rows = deepcopy(list(_MATERIALIZED_SCHEMAS))
    for row in rows:
        definition = row.pop("definition")
        row["definition_json"] = canonical_json(definition)
        row["canonical_hash"] = canonical_hash(definition)
    return rows
