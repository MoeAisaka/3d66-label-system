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
    dimension_rule_mode,
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


def _deduction_rule(rule_id: str = "defect") -> dict:
    return {
        "rule_id": rule_id,
        "description": "画面存在明显缺陷",
        "deduction": 10,
        "tags": ["缺陷"],
    }


def _bonus_rule(rule_id: str = "strength") -> dict:
    return {
        "rule_id": rule_id,
        "description": "画面层级清晰完整",
        "bonus": 8,
        "tags": ["优势"],
    }


def _v2_dimension(
    *,
    key: str = "c_a",
    deduction_rules: list[dict] | None = None,
    bonus_rules: list[dict] | None = None,
    cap: float = 100,
) -> dict:
    return {
        "key": key,
        "weight": 1.0,
        "dimension_score_cap": cap,
        "deduction_rules": [] if deduction_rules is None else deduction_rules,
        "bonus_rules": [] if bonus_rules is None else bonus_rules,
    }


def _single_group_config(dimensions: list[dict]) -> dict:
    return {
        "format_version": SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
        "sub_category_key": "class_one",
        "dimension_max": 60,
        "common_group": {
            "group_weight": 1.0,
            "schema_definition": {"dimensions": dimensions},
        },
        "specific_group": None,
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


@pytest.mark.parametrize("cap", [0, 100])
def test_bonus_cap_v2_accepts_boundary_caps(cap: float) -> None:
    dimension = _v2_dimension(bonus_rules=[_bonus_rule()], cap=cap)
    assert dimension_rule_mode(dimension) == "bonus_cap_v2"
    assert validate_subcategory_dimensions(_single_group_config([dimension])) is None


@pytest.mark.parametrize("cap", [-1, 101, float("nan")])
def test_bonus_cap_v2_rejects_invalid_caps(cap: float) -> None:
    config = _single_group_config([_v2_dimension(bonus_rules=[_bonus_rule()], cap=cap)])
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert excinfo.value.code.endswith("dimension_score_cap_invalid")


@pytest.mark.parametrize(
    ("deduction_rules", "bonus_rules"),
    [
        ([_deduction_rule()], []),
        ([], [_bonus_rule()]),
        ([_deduction_rule()], [_bonus_rule()]),
    ],
)
def test_bonus_cap_v2_allows_only_deduction_only_bonus_or_mixed(
    deduction_rules: list[dict], bonus_rules: list[dict]
) -> None:
    dimension = _v2_dimension(
        deduction_rules=deduction_rules,
        bonus_rules=bonus_rules,
    )
    assert validate_subcategory_dimensions(_single_group_config([dimension])) is None


def test_bonus_cap_v2_requires_both_rule_arrays_and_at_least_one_rule() -> None:
    missing_bonus = _v2_dimension(deduction_rules=[_deduction_rule()])
    del missing_bonus["bonus_rules"]
    with pytest.raises(DimensionCompositionError) as missing_exc:
        validate_subcategory_dimensions(_single_group_config([missing_bonus]))
    assert missing_exc.value.code.endswith("bonus_rules_missing")

    # 扣分与加分规则同时为空是合法的「纯基础分」模式：维度保留只为锚定
    # 规则命中管线（调用B直接给 aesthetic_score、零命中零扣分），媒介降权、
    # 赛道封顶、红线、硬伤封顶与等级映射照常执行。运营清空规则即可启用，
    # 不必置空维度组（那会静默切到美感基座管线）。
    empty = _v2_dimension()
    assert validate_subcategory_dimensions(_single_group_config([empty])) is None


def test_bonus_cap_v2_and_grade_fallback_cannot_mix_within_track() -> None:
    grade = {
        "key": "legacy_grade",
        "weight": 0.5,
        "grade_points": dict(_GRADE_POINTS),
    }
    v2 = _v2_dimension(
        key="rule_v2",
        bonus_rules=[_bonus_rule()],
    )
    v2["weight"] = 0.5
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(_single_group_config([grade, v2]))
    assert excinfo.value.code == "dimension_rule_mode_mixed"


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


# --- group_weights need not sum to 1: non-empty groups renormalize. ---


def test_group_weights_renormalize_among_nonempty_groups() -> None:
    # 0.4 + 0.5 does not sum to 1, but both groups are non-empty so their
    # weights renormalize among themselves (0.4/0.9, 0.5/0.9) and split max.
    result = compose_deductions(
        config=_config(common_weight=0.4, specific_weight=0.5),
        common_grades={"c_a": 1, "c_b": 1},
        specific_grades={"s_a": 1, "s_b": 1},
    )
    # All grade 1 → whole (renormalized) dimension block deducted == dimension_max.
    assert sum(result["deductions"].values()) == pytest.approx(60.0)
    # common slice = 0.4/0.9 * 60 = 26.6667; specific = 0.5/0.9 * 60 = 33.3333.
    assert result["common"]["dimension_max"] == pytest.approx(60 * 0.4 / 0.9)
    assert result["specific"]["dimension_max"] == pytest.approx(60 * 0.5 / 0.9)


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


def test_missing_group_is_treated_as_empty() -> None:
    # An absent group now means "0 dimensions in that group", not an error.
    config = _config()
    del config["specific_group"]
    assert validate_subcategory_dimensions(config) is None
    result = compose_deductions(
        config=config,
        common_grades={"c_a": 1, "c_b": 1},
    )
    # Only the common group scores; it renormalizes to the full dimension_max.
    assert result["dimensions_enabled"] is True
    assert result["specific"] is None
    assert sum(result["deductions"].values()) == pytest.approx(60.0)


# --- Empty groups: 共性/特有 can each be 0; both 0 == prompt-only. ---


def _empty_group(group_weight: float = 0.0) -> dict:
    return {"group_weight": group_weight, "schema_definition": {"dimensions": []}}


def test_only_common_group_renormalizes_to_full_max() -> None:
    config = _config()
    config["specific_group"] = _empty_group()
    result = compose_deductions(
        config=config,
        common_grades={"c_a": 1, "c_b": 1},
    )
    assert result["dimensions_enabled"] is True
    assert result["specific"] is None
    # common renormalizes to the entire dimension_max (60); all grade 1 → 60.
    assert result["common"]["dimension_max"] == pytest.approx(60.0)
    assert sum(result["deductions"].values()) == pytest.approx(60.0)


def test_only_specific_group_renormalizes_to_full_max() -> None:
    config = _config()
    config["common_group"] = _empty_group()
    result = compose_deductions(
        config=config,
        specific_grades={"s_a": 5, "s_b": 5},
    )
    assert result["dimensions_enabled"] is True
    assert result["common"] is None
    assert result["specific"]["dimension_max"] == pytest.approx(60.0)
    # all grade 5 → no deduction.
    assert sum(result["deductions"].values()) == 0.0


def test_both_groups_empty_is_prompt_only() -> None:
    config = _config()
    config["common_group"] = _empty_group()
    config["specific_group"] = _empty_group()
    assert validate_subcategory_dimensions(config) is None
    result = compose_deductions(config=config)
    assert result["dimensions_enabled"] is False
    assert result["deductions"] == {}
    assert result["common"] is None and result["specific"] is None


def test_nonempty_group_zero_weight_fails_closed() -> None:
    # A non-empty group must carry a positive share.
    config = _config()
    config["common_group"]["group_weight"] = 0
    with pytest.raises(DimensionCompositionError) as excinfo:
        validate_subcategory_dimensions(config)
    assert excinfo.value.code == "common_group.group_weight_invalid"


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
