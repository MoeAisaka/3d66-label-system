from __future__ import annotations

import json

import pytest

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.category_evaluation_contract import CATEGORY_EVALUATION_CONTRACT_VERSION
from app.dimension_composition import (
    COMPOSITION_VERSION,
    SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
    DimensionCompositionError,
    compose_deductions,
    validate_subcategory_dimensions,
)
from app.dimension_grade_bridge import DimensionGradeBridgeError
from app.dimension_schema_registry import ACTIVE_V13_VERSION, _GRADE_POINTS
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


# --- Config fixtures: two dimension groups, each weights sum to 1. ---


def _group(keys_weights: dict[str, float], group_weight: float) -> dict:
    """Build one dimension group with dimension-level grade_points."""
    return {
        "group_weight": group_weight,
        "schema_definition": {
            "dimensions": [
                {"key": key, "weight": weight, "grade_points": dict(_GRADE_POINTS)}
                for key, weight in keys_weights.items()
            ]
        },
    }


def _config(
    *,
    dimension_max: float = 60,
    common_weight: float = 0.4,
    specific_weight: float = 0.6,
) -> dict:
    """Legal config: common(2 dims, 0.4) + specific(2 dims, 0.6), max 60."""
    return {
        "format_version": SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
        "sub_category_key": "class_one",
        "dimension_max": dimension_max,
        "common_group": _group({"c_a": 0.5, "c_b": 0.5}, common_weight),
        "specific_group": _group({"s_a": 0.5, "s_b": 0.5}, specific_weight),
    }


# --- All grade 5 → merged deductions all 0. ---


def test_all_grade_five_yields_zero_merged_deductions() -> None:
    result = compose_deductions(
        config=_config(),
        common_grades={"c_a": 5, "c_b": 5},
        specific_grades={"s_a": 5, "s_b": 5},
    )
    assert result["deductions"] == {"c_a": 0.0, "c_b": 0.0, "s_a": 0.0, "s_b": 0.0}
    assert sum(result["deductions"].values()) == 0.0
    assert result["composition_version"] == COMPOSITION_VERSION
    assert result["sub_category_key"] == "class_one"


# --- All grade 1 → merged deduction sum == dimension_max. ---


def test_all_grade_one_deducts_full_dimension_max() -> None:
    result = compose_deductions(
        config=_config(),
        common_grades={"c_a": 1, "c_b": 1},
        specific_grades={"s_a": 1, "s_b": 1},
    )
    # common effective_max 24 (12+12); specific effective_max 36 (18+18).
    assert result["deductions"] == {
        "c_a": 12.0,
        "c_b": 12.0,
        "s_a": 18.0,
        "s_b": 18.0,
    }
    assert sum(result["deductions"].values()) == 60.0
    assert result["common"]["dimension_max"] == 24.0
    assert result["specific"]["dimension_max"] == 36.0


# --- Hand-computed mixed value from the brief. ---


def test_hand_computed_mixed_common_dimension() -> None:
    result = compose_deductions(
        config=_config(),
        # common effective_max 24; c_a weight 0.5 → share 12; grade 3 → ratio 0.6
        # → deduction 12 * 0.4 = 4.8.
        common_grades={"c_a": 3, "c_b": 5},
        specific_grades={"s_a": 5, "s_b": 5},
    )
    assert result["deductions"]["c_a"] == 4.8
    ev = result["common"]["evidence"]["c_a"]
    assert ev["grade"] == 3
    assert ev["share"] == 12.0
    assert ev["ratio"] == 0.6
    assert ev["deduction"] == 4.8
    # Every other dimension is full marks → 0.
    assert result["deductions"]["c_b"] == 0.0
    assert result["deductions"]["s_a"] == 0.0
    assert result["deductions"]["s_b"] == 0.0


# --- validate_subcategory_dimensions accepts a legal config. ---


def test_validate_accepts_legal_config() -> None:
    assert validate_subcategory_dimensions(_config()) is None


# --- Compose → aggregator round-trip on the inspiration class_one contract. ---


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


def test_roundtrip_all_grade_five_scores_100_l1() -> None:
    composed = compose_deductions(
        config=_config(),
        common_grades={"c_a": 5, "c_b": 5},
        specific_grades={"s_a": 5, "s_b": 5},
    )
    result = aggregate_category_evaluation(
        _contract(), _precheck(), composed, track_key="class_one"
    )
    assert result["score"] == 100
    assert result["level"] == "L1"


def test_roundtrip_all_grade_one_scores_40_l3() -> None:
    composed = compose_deductions(
        config=_config(),
        common_grades={"c_a": 1, "c_b": 1},
        specific_grades={"s_a": 1, "s_b": 1},
    )
    # Whole dimension block deducted: merged sum == dimension_max (60).
    assert sum(composed["deductions"].values()) == pytest.approx(60.0)
    result = aggregate_category_evaluation(
        _contract(), _precheck(), composed, track_key="class_one"
    )
    # base 40 + dim 60 - 60 = 40; real-photo penalty 0 → score 40 → L3.
    assert result["score"] == 40
    assert result["level"] == "L3"


# --- Fail-closed: two groups' keys overlap. ---


def test_key_overlap_fails_closed() -> None:
    config = _config()
    config["specific_group"] = _group({"c_a": 0.5, "s_b": 0.5}, 0.6)
    with pytest.raises(DimensionCompositionError) as excinfo:
        compose_deductions(
            config=config,
            common_grades={"c_a": 5, "c_b": 5},
            specific_grades={"c_a": 5, "s_b": 5},
        )
    assert excinfo.value.code == "dimension_key_overlap"


def test_key_overlap_fails_closed_in_validate() -> None:
    config = _config()
    config["specific_group"] = _group({"c_b": 0.5, "s_b": 0.5}, 0.6)
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert excinfo.value.code == "dimension_key_overlap"


# --- Fail-closed: group_weights do not sum to 1. ---


def test_group_weights_not_normalized_fails_closed() -> None:
    with pytest.raises(DimensionCompositionError) as excinfo:
        compose_deductions(
            config=_config(common_weight=0.4, specific_weight=0.5),
            common_grades={"c_a": 5, "c_b": 5},
            specific_grades={"s_a": 5, "s_b": 5},
        )
    assert excinfo.value.code == "group_weights_not_normalized"


@pytest.mark.parametrize("group_weight", [-0.1, "0.4", None, True])
def test_group_weight_invalid_fails_closed(group_weight) -> None:
    config = _config()
    config["common_group"]["group_weight"] = group_weight
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert excinfo.value.code == "common_group.group_weight_invalid"


@pytest.mark.parametrize("dimension_max", [-1, "60", None, True])
def test_dimension_max_invalid_fails_closed(dimension_max) -> None:
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(_config(dimension_max=dimension_max))
    assert excinfo.value.code == "dimension_max_invalid"


def test_format_version_invalid_fails_closed() -> None:
    config = _config()
    config["format_version"] = "wrong-version"
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert excinfo.value.code == "format_version_invalid"


def test_missing_group_fails_closed() -> None:
    config = _config()
    del config["specific_group"]
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert excinfo.value.code == "specific_group.missing"


# --- Fail-closed via the reused bridge (re-tagged with group prefix). ---


def test_internal_weights_not_normalized_fails_closed_via_bridge() -> None:
    config = _config()
    config["common_group"] = _group({"c_a": 0.5, "c_b": 0.4}, 0.4)
    with pytest.raises(DimensionCompositionError) as excinfo:
        compose_deductions(
            config=config,
            common_grades={"c_a": 5, "c_b": 5},
            specific_grades={"s_a": 5, "s_b": 5},
        )
    assert excinfo.value.code == "common.weights_not_normalized"


def test_grade_out_of_range_fails_closed_via_bridge() -> None:
    with pytest.raises(DimensionCompositionError) as excinfo:
        compose_deductions(
            config=_config(),
            common_grades={"c_a": 9, "c_b": 5},
            specific_grades={"s_a": 5, "s_b": 5},
        )
    assert excinfo.value.code == "common.grade_out_of_range"


def test_grade_keys_mismatch_fails_closed_via_bridge() -> None:
    with pytest.raises(DimensionCompositionError) as excinfo:
        compose_deductions(
            config=_config(),
            common_grades={"c_a": 5, "c_b": 5},
            specific_grades={"s_a": 5},  # s_b missing
        )
    assert excinfo.value.code == "specific.grade_keys_mismatch"


def test_bridge_error_is_subclass_of_value_error() -> None:
    # DimensionCompositionError and DimensionGradeBridgeError are both
    # ValueError subclasses; composition re-raises bridge failures as the former.
    assert issubclass(DimensionCompositionError, ValueError)
    assert issubclass(DimensionGradeBridgeError, ValueError)


# --- Determinism + JSON serializability. ---


def test_determinism_same_input_same_output() -> None:
    first = compose_deductions(
        config=_config(),
        common_grades={"c_a": 3, "c_b": 4},
        specific_grades={"s_a": 2, "s_b": 5},
    )
    second = compose_deductions(
        config=_config(),
        common_grades={"c_a": 3, "c_b": 4},
        specific_grades={"s_a": 2, "s_b": 5},
    )
    assert first == second


def test_output_is_json_serializable() -> None:
    result = compose_deductions(
        config=_config(),
        common_grades={"c_a": 3, "c_b": 4},
        specific_grades={"s_a": 2, "s_b": 5},
    )
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
