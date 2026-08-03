from __future__ import annotations

import pytest

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.category_evaluation_contract import CATEGORY_EVALUATION_CONTRACT_VERSION
from app.dimension_grade_bridge import (
    GRADE_BRIDGE_VERSION,
    DimensionGradeBridgeError,
    deductions_from_bridge,
    grades_to_deductions,
)
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    _GRADE_POINTS,
    space_schema_definition_for_version,
)
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


# --- Mini schema: 2-3 dimensions, weights sum to 1, real _GRADE_POINTS. ---


def _mini_schema_two(*, w_a: float = 0.5, w_b: float = 0.5) -> dict:
    """Two-dimension schema carrying dimension-level grade_points."""
    return {
        "dimensions": [
            {"key": "dim_a", "weight": w_a, "grade_points": dict(_GRADE_POINTS)},
            {"key": "dim_b", "weight": w_b, "grade_points": dict(_GRADE_POINTS)},
        ]
    }


def _mini_schema_three() -> dict:
    return {
        "dimensions": [
            {"key": "dim_a", "weight": 0.5, "grade_points": dict(_GRADE_POINTS)},
            {"key": "dim_b", "weight": 0.3, "grade_points": dict(_GRADE_POINTS)},
            {"key": "dim_c", "weight": 0.2, "grade_points": dict(_GRADE_POINTS)},
        ]
    }


# --- grade 5 → no deduction; grade 1 → full share; round-trip sum == max. ---


def test_all_grade_five_yields_zero_deductions() -> None:
    result = grades_to_deductions(
        dimension_grades={"dim_a": 5, "dim_b": 5},
        dimension_schema_definition=_mini_schema_two(),
        dimension_max=60,
    )
    assert result["deductions"] == {"dim_a": 0.0, "dim_b": 0.0}
    assert result["bridge_version"] == GRADE_BRIDGE_VERSION
    for key in ("dim_a", "dim_b"):
        assert result["evidence"][key]["ratio"] == 1.0
        assert result["evidence"][key]["deduction"] == 0.0


def test_all_grade_one_deducts_full_dimension_max() -> None:
    dimension_max = 60
    result = grades_to_deductions(
        dimension_grades={"dim_a": 1, "dim_b": 1},
        dimension_schema_definition=_mini_schema_two(),
        dimension_max=dimension_max,
    )
    # grade 1 → ratio 0 → deduction == share == weight * dimension_max.
    assert result["deductions"] == {"dim_a": 30.0, "dim_b": 30.0}
    assert sum(result["deductions"].values()) == float(dimension_max)


# --- Hand-computed example from the brief: 0.5 * 60, grade 3 → 12.0. ---


def test_hand_computed_example_weight_half_grade_three_is_twelve() -> None:
    result = grades_to_deductions(
        dimension_grades={"dim_a": 3, "dim_b": 5},
        dimension_schema_definition=_mini_schema_two(w_a=0.5, w_b=0.5),
        dimension_max=60,
    )
    # share = 0.5 * 60 = 30; ratio = (65-20)/(95-20) = 0.6; deduction = 30*0.4 = 12.0.
    assert result["deductions"]["dim_a"] == 12.0
    ev = result["evidence"]["dim_a"]
    assert ev["grade"] == 3
    assert ev["share"] == 30.0
    assert ev["ratio"] == 0.6
    assert ev["deduction"] == 12.0
    # dim_b is full marks → 0.
    assert result["deductions"]["dim_b"] == 0.0


def test_deduction_matches_share_times_one_minus_ratio_three_dims() -> None:
    dimension_max = 50
    grades = {"dim_a": 2, "dim_b": 4, "dim_c": 3}
    result = grades_to_deductions(
        dimension_grades=grades,
        dimension_schema_definition=_mini_schema_three(),
        dimension_max=dimension_max,
    )
    min_p, max_p = _GRADE_POINTS["1"], _GRADE_POINTS["5"]
    weights = {"dim_a": 0.5, "dim_b": 0.3, "dim_c": 0.2}
    for key, grade in grades.items():
        share = weights[key] * dimension_max
        ratio = (_GRADE_POINTS[str(grade)] - min_p) / (max_p - min_p)
        expected = round(share * (1 - ratio), 4)
        assert result["deductions"][key] == expected


# --- Deduction sum never exceeds dimension_max, for any grade mix. ---


@pytest.mark.parametrize(
    "grades",
    [
        {"dim_a": 1, "dim_b": 1, "dim_c": 1},
        {"dim_a": 1, "dim_b": 2, "dim_c": 3},
        {"dim_a": 2, "dim_b": 3, "dim_c": 4},
        {"dim_a": 5, "dim_b": 5, "dim_c": 5},
    ],
)
def test_deduction_sum_never_exceeds_dimension_max(grades) -> None:
    dimension_max = 60
    result = grades_to_deductions(
        dimension_grades=grades,
        dimension_schema_definition=_mini_schema_three(),
        dimension_max=dimension_max,
    )
    assert sum(result["deductions"].values()) <= dimension_max + 1e-9


# --- Bridge → aggregator round-trip on the inspiration class_one contract. ---


def _redline_policy() -> dict:
    return {
        "format_version": REDLINE_POLICY_FORMAT_VERSION,
        "enabled": True,
        "hit_level": "L5",
        "hit_score_cap": 49,
        "rules": [
            {
                "key": "screenshot",
                "signal": "production_fields.reason",
                "match_any": ["是截图"],
                "exemptions": [],
            }
        ],
    }


def _contract() -> dict:
    # Inspiration-image class_one track: base 40 + dimension_max 60, cap 100.
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "redline_policy": _redline_policy(),
        "track_classification": {
            "format_version": "track-classification-v1",
            "tracks": [
                {
                    "key": "class_one",
                    "label": "一类",
                    "base_score": 40,
                    "dimension_max": 60,
                    "track_cap": 100,
                    "dimension_schema_ref": {
                        "schema_key": "space_aesthetic",
                        "version": ACTIVE_V13_VERSION,
                    },
                }
            ],
            "default_track": "class_one",
        },
        "common_modifiers": {
            "format_version": "common-modifiers-v1",
            "media_type_penalty": {
                "baseline": "real_photo",
                "penalties": {
                    "real_photo": 0,
                    "render_3d": -5,
                    "ai_image": -15,
                    "other": 0,
                },
            },
            "high_score_veto": {"threshold": 80, "cap_to": 79},
        },
    }


def _precheck() -> dict:
    return {"production_fields": {"trait": "实景照片", "reason": []}}


def _v13_schema() -> dict:
    return space_schema_definition_for_version(ACTIVE_V13_VERSION)


def _grades_all(grade: int) -> dict[str, int]:
    schema = _v13_schema()
    return {dimension["key"]: grade for dimension in schema["dimensions"]}


CLASS_ONE_DIMENSION_MAX = 60


def test_roundtrip_all_grade_five_scores_100_l1() -> None:
    schema = _v13_schema()
    dimension_result = deductions_from_bridge(
        dimension_grades=_grades_all(5),
        dimension_schema_definition=schema,
        dimension_max=CLASS_ONE_DIMENSION_MAX,
    )
    result = aggregate_category_evaluation(
        _contract(), _precheck(), dimension_result, track_key="class_one"
    )
    assert result["score"] == 100
    assert result["level"] == "L1"


def test_roundtrip_all_grade_one_deducts_block_to_base_score_l3() -> None:
    schema = _v13_schema()
    bridged = grades_to_deductions(
        dimension_grades=_grades_all(1),
        dimension_schema_definition=schema,
        dimension_max=CLASS_ONE_DIMENSION_MAX,
    )
    # Whole dimension block deducted: sum == dimension_max (60).
    assert sum(bridged["deductions"].values()) == pytest.approx(60.0)

    result = aggregate_category_evaluation(
        _contract(),
        _precheck(),
        {"deductions": bridged["deductions"], "evidence": bridged["evidence"]},
        track_key="class_one",
    )
    # base 40 + dim 60 - 60 = 40; real-photo penalty 0 → score 40 → L3.
    assert result["score"] == 40
    assert result["level"] == "L3"


# --- Fail-closed guards. ---


def test_grade_keys_mismatch_fails_closed_missing() -> None:
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": 3},  # dim_b missing
            dimension_schema_definition=_mini_schema_two(),
            dimension_max=60,
        )
    assert excinfo.value.code == "grade_keys_mismatch"


def test_grade_keys_mismatch_fails_closed_extra() -> None:
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": 3, "dim_b": 3, "dim_x": 3},
            dimension_schema_definition=_mini_schema_two(),
            dimension_max=60,
        )
    assert excinfo.value.code == "grade_keys_mismatch"


@pytest.mark.parametrize("grade", [0, 6, -1, 3.0, True, "3", None])
def test_grade_out_of_range_fails_closed(grade) -> None:
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": grade, "dim_b": 5},
            dimension_schema_definition=_mini_schema_two(),
            dimension_max=60,
        )
    assert excinfo.value.code == "grade_out_of_range"


def test_weights_not_normalized_fails_closed() -> None:
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": 3, "dim_b": 3},
            dimension_schema_definition=_mini_schema_two(w_a=0.5, w_b=0.4),
            dimension_max=60,
        )
    assert excinfo.value.code == "weights_not_normalized"


def test_grade_points_missing_fails_closed() -> None:
    schema = {
        "dimensions": [
            {"key": "dim_a", "weight": 0.5},
            {"key": "dim_b", "weight": 0.5},
        ]
    }
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": 3, "dim_b": 3},
            dimension_schema_definition=schema,
            dimension_max=60,
        )
    assert excinfo.value.code == "grade_points_missing"


def test_grade_points_fallback_to_aggregation_level() -> None:
    # No dimension-level grade_points, but aggregation.grade_points is present.
    schema = {
        "aggregation": {"grade_points": dict(_GRADE_POINTS)},
        "dimensions": [
            {"key": "dim_a", "weight": 0.5},
            {"key": "dim_b", "weight": 0.5},
        ],
    }
    result = grades_to_deductions(
        dimension_grades={"dim_a": 3, "dim_b": 5},
        dimension_schema_definition=schema,
        dimension_max=60,
    )
    assert result["deductions"]["dim_a"] == 12.0


def test_grade_points_fallback_to_top_level() -> None:
    schema = {
        "grade_points": dict(_GRADE_POINTS),
        "dimensions": [
            {"key": "dim_a", "weight": 0.5},
            {"key": "dim_b", "weight": 0.5},
        ],
    }
    result = grades_to_deductions(
        dimension_grades={"dim_a": 3, "dim_b": 5},
        dimension_schema_definition=schema,
        dimension_max=60,
    )
    assert result["deductions"]["dim_a"] == 12.0


@pytest.mark.parametrize("dimension_max", [-1, -0.5, "60", None])
def test_negative_or_invalid_dimension_max_fails_closed(dimension_max) -> None:
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": 3, "dim_b": 5},
            dimension_schema_definition=_mini_schema_two(),
            dimension_max=dimension_max,
        )
    assert excinfo.value.code == "dimension_max_invalid"


def test_grade_points_max_not_greater_than_min_fails_closed() -> None:
    flat = {"1": 50.0, "2": 50.0, "3": 50.0, "4": 50.0, "5": 50.0}
    schema = {
        "dimensions": [
            {"key": "dim_a", "weight": 0.5, "grade_points": flat},
            {"key": "dim_b", "weight": 0.5, "grade_points": flat},
        ]
    }
    with pytest.raises(DimensionGradeBridgeError) as excinfo:
        grades_to_deductions(
            dimension_grades={"dim_a": 3, "dim_b": 3},
            dimension_schema_definition=schema,
            dimension_max=60,
        )
    assert excinfo.value.code == "grade_points_missing"


# --- Determinism: same input → identical output across calls. ---


def test_determinism_same_input_same_output() -> None:
    grades = {"dim_a": 2, "dim_b": 4, "dim_c": 3}
    first = grades_to_deductions(
        dimension_grades=grades,
        dimension_schema_definition=_mini_schema_three(),
        dimension_max=60,
    )
    second = grades_to_deductions(
        dimension_grades=dict(grades),
        dimension_schema_definition=_mini_schema_three(),
        dimension_max=60,
    )
    assert first == second


def test_output_is_json_serializable() -> None:
    import json

    result = grades_to_deductions(
        dimension_grades={"dim_a": 2, "dim_b": 4, "dim_c": 3},
        dimension_schema_definition=_mini_schema_three(),
        dimension_max=60,
    )
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
