from __future__ import annotations

from copy import deepcopy

import pytest

from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
    canonical_hash,
    space_schema_definition_for_version,
)
from app.scoring import (
    DimensionScoringContractError,
    calculate_corrected_score,
    calculate_score,
    dimension_schema_from_strategy_snapshot,
)


SPACE_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)


def _precheck() -> dict:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": 0.95,
        },
        "image_quality": {
            "quality_severity": "normal",
            "confidence": 0.95,
            "evidence": [],
        },
        "media_form": {},
        "needs_review": False,
        "review_reasons": [],
    }


def _aesthetic(
    grades: dict[str, int],
    *,
    scoring_profile: str | None = None,
) -> dict:
    payload = {
        "dimensions": {
            key: {
                "grade": grade,
                "evidence": ["证据一", "证据二", "证据三"],
            }
            for key, grade in grades.items()
        },
        "special_checks": {},
        "assessment_confidence": 0.95,
        "needs_review": False,
        "review_reasons": [],
    }
    if scoring_profile is not None:
        payload["scoring_profile"] = scoring_profile
    return payload


def _three_dimension_schema() -> dict:
    definition = space_schema_definition_for_version(
        ACTIVE_V13_VERSION
    )
    selected = {
        "presentation_integrity",
        "visual_hierarchy",
        "inspiration_reference",
    }
    dimensions = [
        dimension
        for dimension in definition["dimensions"]
        if dimension["key"] in selected
    ]
    weights = {
        "presentation_integrity": 0.34,
        "visual_hierarchy": 0.33,
        "inspiration_reference": 0.33,
    }
    for display_order, dimension in enumerate(dimensions, start=1):
        dimension["weight"] = weights[dimension["key"]]
        dimension["display_order"] = display_order
    definition["dimensions"] = dimensions
    definition["output_contract"]["dimension_output_keys"] = [
        dimension["key"] for dimension in dimensions
    ]
    definition["aggregation"]["collapse_rule"][
        "same_grade_count_for_review"
    ] = 3
    definition["aggregation"]["high_evidence_rule"][
        "dimensions_for_l3_cap"
    ] = 2
    definition["aggregation"]["top_level_rule"][
        "grade_five_minimum_count"
    ] = 2
    return definition


@pytest.mark.parametrize(
    ("version", "profile", "expected_score"),
    (
        (HISTORICAL_DEFAULT_VERSION, None, 71.2),
        (
            ACTIVE_V13_VERSION,
            "space_aesthetic_v1.3",
            69.7,
        ),
    ),
)
def test_space_schema_replay_preserves_legacy_weight_results(
    version: str,
    profile: str | None,
    expected_score: float,
) -> None:
    grades = dict(
        zip(SPACE_KEYS, (4, 3, 2, 5, 4, 3, 1, 4), strict=True)
    )
    definition = space_schema_definition_for_version(version)

    result = calculate_score(
        _precheck(),
        _aesthetic(grades, scoring_profile=profile),
        dimension_schema=definition,
    )

    assert result["raw_score"] == expected_score
    assert result["score"] == expected_score
    assert result["raw_level"] == "L3"
    assert result["level"] == "L3"
    assert result["caps"] == []
    assert {
        key: item["weight"]
        for key, item in result["dimension_points"].items()
    } == {
        dimension["key"]: dimension["weight"]
        for dimension in definition["dimensions"]
    }


def test_non_eight_dimension_schema_scores_without_space_constants() -> None:
    definition = _three_dimension_schema()
    grades = {
        "presentation_integrity": 5,
        "visual_hierarchy": 4,
        "inspiration_reference": 3,
    }

    result = calculate_score(
        _precheck(),
        _aesthetic(
            grades,
            scoring_profile="space_aesthetic_v1.3",
        ),
        dimension_schema=definition,
    )

    assert result["raw_score"] == 80.81
    assert result["raw_level"] == "L4"
    assert result["level"] == "L4"
    assert tuple(result["dimension_points"]) == tuple(
        definition["output_contract"]["dimension_output_keys"]
    )


def test_non_eight_dimension_schema_keeps_dynamic_rule_messages() -> None:
    definition = _three_dimension_schema()
    grades = {
        "presentation_integrity": 4,
        "visual_hierarchy": 4,
        "inspiration_reference": 4,
    }
    weak_evidence = _aesthetic(
        grades,
        scoring_profile="space_aesthetic_v1.3",
    )
    for item in weak_evidence["dimensions"].values():
        item["evidence"] = ["只有一条证据"]

    weak_result = calculate_score(
        _precheck(),
        weak_evidence,
        dimension_schema=definition,
    )

    assert weak_result["caps"] == [
        {
            "cap": "L3",
            "reason": "多个高分维度缺少至少2条独立视觉证据",
        }
    ]

    top_level = _aesthetic(
        {key: 5 for key in grades},
        scoring_profile="space_aesthetic_v1.3",
    )
    top_level["assessment_confidence"] = 0.8
    top_result = calculate_score(
        _precheck(),
        top_level,
        dimension_schema=definition,
    )

    assert top_result["caps"] == [
        {
            "cap": "L4",
            "reason": (
                "L5 需要至少2个5级维度、其余不低于4级、"
                "置信度不低于0.9且满足复核约束"
            ),
        }
    ]


def test_schema_rejects_unknown_or_missing_dimension_output() -> None:
    definition = _three_dimension_schema()
    valid = {
        "presentation_integrity": 4,
        "visual_hierarchy": 4,
        "inspiration_reference": 4,
    }
    unknown = dict(valid, invented_dimension=4)
    with pytest.raises(ValueError, match="未发布的维度"):
        calculate_score(
            _precheck(),
            _aesthetic(unknown),
            dimension_schema=definition,
        )

    missing = dict(valid)
    missing.pop("visual_hierarchy")
    with pytest.raises(ValueError, match="visual_hierarchy"):
        calculate_score(
            _precheck(),
            _aesthetic(missing),
            dimension_schema=definition,
        )


def test_human_correction_uses_bound_schema_keys() -> None:
    definition = _three_dimension_schema()
    source = _aesthetic(
        {
            "presentation_integrity": 4,
            "visual_hierarchy": 4,
            "inspiration_reference": 4,
        }
    )

    corrected = calculate_corrected_score(
        _precheck(),
        source,
        [
            {
                "target_type": "dimension",
                "field_key": "inspiration_reference",
                "human_value": 1,
            }
        ],
        dimension_schema=definition,
    )
    assert corrected["score"] == 61.54
    assert corrected["level"] == "L3"

    with pytest.raises(ValueError, match="未知的纠正维度"):
        calculate_corrected_score(
            _precheck(),
            source,
            [
                {
                    "target_type": "dimension",
                    "field_key": "composition_viewpoint",
                    "human_value": 1,
                }
            ],
            dimension_schema=definition,
        )


def test_invalid_schema_contract_fails_closed() -> None:
    definition = _three_dimension_schema()
    invalid_engine = deepcopy(definition)
    invalid_engine["aggregation"]["engine_version"] = "engine-future"
    with pytest.raises(
        DimensionScoringContractError,
        match="评分引擎版本",
    ):
        calculate_score(
            _precheck(),
            _aesthetic(
                {
                    "presentation_integrity": 4,
                    "visual_hierarchy": 4,
                    "inspiration_reference": 4,
                }
            ),
            dimension_schema=invalid_engine,
        )

    invalid_weights = deepcopy(definition)
    invalid_weights["dimensions"][0]["weight"] = 0.9
    with pytest.raises(
        DimensionScoringContractError,
        match="权重之和",
    ):
        calculate_score(
            _precheck(),
            _aesthetic(
                {
                    "presentation_integrity": 4,
                    "visual_hierarchy": 4,
                    "inspiration_reference": 4,
                }
            ),
            dimension_schema=invalid_weights,
        )


def test_review_recalculation_reads_result_bound_schema_snapshot() -> None:
    bound = _three_dimension_schema()
    snapshot = {
        "schema_version": "strategy-bundle-v2",
        "resolved_dimension_schema_hash": canonical_hash(bound),
        "resolved_dimensions_snapshot": bound,
    }

    resolved = dimension_schema_from_strategy_snapshot(
        snapshot,
        aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
    )
    assert resolved == bound
    resolved["dimensions"][0]["weight"] = 0.9
    assert snapshot["resolved_dimensions_snapshot"] == bound

    tampered = deepcopy(snapshot)
    tampered["resolved_dimensions_snapshot"]["dimensions"][0][
        "weight"
    ] = 0.9
    with pytest.raises(
        DimensionScoringContractError,
        match="无法复算",
    ):
        dimension_schema_from_strategy_snapshot(
            tampered,
            aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
        )


def test_v1_review_recalculation_keeps_profile_compatibility() -> None:
    resolved = dimension_schema_from_strategy_snapshot(
        {"schema_version": "strategy-bundle-v1"},
        aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
    )
    assert resolved == space_schema_definition_for_version(
        ACTIVE_V13_VERSION
    )
