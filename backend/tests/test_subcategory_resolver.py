from __future__ import annotations

import json

import pytest

from app.subcategory_resolver import (
    CLASSIFICATION_MAP_FORMAT_VERSION,
    SubcategoryResolverError,
    resolve_subcategory,
    validate_classification_map,
)
from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.category_evaluation_contract import CATEGORY_EVALUATION_CONTRACT_VERSION
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


# --- Inspiration-image fixtures: three tracks 一类40+60=100 / 二类20+60=80 /
# --- 三类40+30=70; default_track = class_three. Mirrors the aggregator suite. ---


def _track_classification() -> dict:
    return {
        "format_version": "track-classification-v1",
        "tracks": [
            {
                "key": "class_one",
                "label": "一类",
                "base_score": 40,
                "dimension_max": 60,
                "track_cap": 100,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
            {
                "key": "class_two",
                "label": "二类",
                "base_score": 20,
                "dimension_max": 60,
                "track_cap": 80,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
            {
                "key": "class_three",
                "label": "三类",
                "base_score": 40,
                "dimension_max": 30,
                "track_cap": 70,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
        ],
        "default_track": "class_three",
    }


def _classification_map() -> dict:
    return {
        "format_version": CLASSIFICATION_MAP_FORMAT_VERSION,
        "min_confidence": 0.6,
        "category_to_subcategory": {
            "建筑设计": "class_one",
            "产品设计": "class_two",
            "其它": "class_three",
        },
        "out_of_scope_subcategory": "class_three",
    }


def _precheck(*, scope_status="in_scope", primary_category="建筑设计", primary_confidence=0.9) -> dict:
    classification: dict = {}
    if scope_status is not None:
        classification["scope_status"] = scope_status
    if primary_category is not None:
        classification["primary_category"] = primary_category
    if primary_confidence is not None:
        classification["primary_confidence"] = primary_confidence
    return {"classification": classification}


def _resolve(precheck: dict) -> dict:
    return resolve_subcategory(
        precheck,
        classification_map=_classification_map(),
        track_classification=_track_classification(),
    )


# --- Aggregator round-trip fixtures (contract shape from the aggregator suite). ---


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


def _common_modifiers() -> dict:
    return {
        "format_version": "common-modifiers-v1",
        "media_type_penalty": {
            "baseline": "real_photo",
            "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -15, "other": 0},
        },
        "high_score_veto": {"threshold": 80, "cap_to": 79},
    }


def _contract() -> dict:
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": _common_modifiers(),
    }


def _aggregator_precheck() -> dict:
    return {"production_fields": {"trait": "实景照片", "reason": []}}


# --- Step 5: mapped hit with high confidence → class_one, mapped, no review. ---


def test_mapped_high_confidence_resolves_to_track() -> None:
    result = _resolve(_precheck(primary_category="建筑设计", primary_confidence=0.9))
    assert result["track_key"] == "class_one"
    assert result["resolved_by"] == "mapped"
    assert result["needs_review"] is False
    assert result["primary_category"] == "建筑设计"
    assert result["confidence"] == 0.9


def test_mapped_second_category_resolves_to_class_two() -> None:
    result = _resolve(_precheck(primary_category="产品设计", primary_confidence=0.75))
    assert result["track_key"] == "class_two"
    assert result["resolved_by"] == "mapped"
    assert result["needs_review"] is False


# --- Step 3: out_of_scope → out_of_scope_subcategory, no review. ---


def test_out_of_scope_routes_to_out_of_scope_subcategory() -> None:
    result = _resolve(_precheck(scope_status="out_of_scope", primary_category="建筑设计"))
    assert result["track_key"] == "class_three"
    assert result["resolved_by"] == "out_of_scope"
    assert result["needs_review"] is False


def test_out_of_scope_takes_priority_over_low_confidence() -> None:
    # out_of_scope is resolved (step 3) before the low-confidence check (step 4).
    result = _resolve(_precheck(scope_status="out_of_scope", primary_confidence=0.1))
    assert result["track_key"] == "class_three"
    assert result["resolved_by"] == "out_of_scope"


# --- Step 4: low confidence → default_track + low_confidence + needs_review. ---


def test_low_confidence_falls_back_to_default_track() -> None:
    result = _resolve(_precheck(primary_category="建筑设计", primary_confidence=0.4))
    assert result["track_key"] == "class_three"  # default_track
    assert result["resolved_by"] == "low_confidence"
    assert result["needs_review"] is True
    # The original primary_category is preserved in the result.
    assert result["primary_category"] == "建筑设计"
    assert result["confidence"] == 0.4


def test_confidence_exactly_at_threshold_is_not_low() -> None:
    # min_confidence is 0.6; a confidence of exactly 0.6 is NOT below it.
    result = _resolve(_precheck(primary_category="建筑设计", primary_confidence=0.6))
    assert result["resolved_by"] == "mapped"
    assert result["track_key"] == "class_one"


# --- Step 6: unmapped category → default_track + unmapped_category + review. ---


def test_unmapped_category_falls_back_to_default_track() -> None:
    result = _resolve(_precheck(primary_category="平面设计", primary_confidence=0.95))
    assert result["track_key"] == "class_three"  # default_track
    assert result["resolved_by"] == "unmapped_category"
    assert result["needs_review"] is True
    assert result["primary_category"] == "平面设计"


# --- Step 2: missing / illegal classification → invalid_classification. ---


def test_missing_classification_block_is_invalid() -> None:
    result = _resolve({})
    assert result["track_key"] == "class_three"  # default_track
    assert result["resolved_by"] == "invalid_classification"
    assert result["needs_review"] is True


def test_missing_scope_status_is_invalid() -> None:
    result = _resolve(_precheck(scope_status=None))
    assert result["resolved_by"] == "invalid_classification"
    assert result["needs_review"] is True


def test_illegal_scope_status_is_invalid() -> None:
    result = _resolve(_precheck(scope_status="maybe"))
    assert result["resolved_by"] == "invalid_classification"
    assert result["track_key"] == "class_three"


def test_empty_primary_category_is_invalid() -> None:
    result = _resolve(_precheck(primary_category=""))
    assert result["resolved_by"] == "invalid_classification"


def test_confidence_out_of_range_is_invalid() -> None:
    result = _resolve(_precheck(primary_confidence=1.5))
    assert result["resolved_by"] == "invalid_classification"


def test_confidence_non_numeric_is_invalid() -> None:
    result = _resolve(_precheck(primary_confidence="high"))
    assert result["resolved_by"] == "invalid_classification"


def test_confidence_boolean_is_invalid() -> None:
    # bool is a subclass of int but must not be accepted as a confidence value.
    result = _resolve(_precheck(primary_confidence=True))
    assert result["resolved_by"] == "invalid_classification"


# --- boundary scope_status is treated as in-scope and still maps. ---


def test_boundary_scope_status_still_maps() -> None:
    result = _resolve(_precheck(scope_status="boundary", primary_category="建筑设计", primary_confidence=0.9))
    assert result["track_key"] == "class_one"
    assert result["resolved_by"] == "mapped"
    assert result["needs_review"] is False
    assert any("boundary" in note for note in result["notes"])


def test_boundary_scope_status_low_confidence_falls_back() -> None:
    result = _resolve(_precheck(scope_status="boundary", primary_confidence=0.3))
    assert result["resolved_by"] == "low_confidence"
    assert result["track_key"] == "class_three"


# --- Resolver → aggregator round-trip: resolved track_key drives scoring. ---


def test_round_trip_mapped_class_two_is_capped_at_80() -> None:
    resolved = _resolve(_precheck(primary_category="产品设计", primary_confidence=0.8))
    assert resolved["track_key"] == "class_two"
    aggregated = aggregate_category_evaluation(
        _contract(),
        _aggregator_precheck(),
        {"deductions": {}},
        track_key=resolved["track_key"],
    )
    # class_two: base20 + dim60 = 80, cap 80 → full score capped at 80.
    assert aggregated["track_key"] == "class_two"
    assert aggregated["score"] <= 80
    assert aggregated["score"] == 80
    assert aggregated["level"] == "L1"


def test_round_trip_mapped_class_one_reaches_100() -> None:
    resolved = _resolve(_precheck(primary_category="建筑设计", primary_confidence=0.9))
    aggregated = aggregate_category_evaluation(
        _contract(),
        _aggregator_precheck(),
        {"deductions": {}},
        track_key=resolved["track_key"],
    )
    assert aggregated["track_key"] == "class_one"
    assert aggregated["score"] == 100
    assert aggregated["level"] == "L1"


def test_round_trip_default_fallback_track_is_valid_in_contract() -> None:
    # A low-confidence fallback yields default_track (class_three); feed it back
    # to the aggregator to prove the fallback key is a real contract track.
    resolved = _resolve(_precheck(primary_confidence=0.2))
    assert resolved["track_key"] == "class_three"
    aggregated = aggregate_category_evaluation(
        _contract(),
        _aggregator_precheck(),
        {"deductions": {}},
        track_key=resolved["track_key"],
    )
    # class_three: base40 + dim30 = 70, cap 70.
    assert aggregated["track_key"] == "class_three"
    assert aggregated["score"] == 70


# --- Fail-closed: bad map targets / version / default_track. ---


def test_map_target_not_in_contract_fails_closed() -> None:
    bad_map = _classification_map()
    bad_map["category_to_subcategory"]["建筑设计"] = "class_nine"
    with pytest.raises(SubcategoryResolverError) as excinfo:
        resolve_subcategory(
            _precheck(),
            classification_map=bad_map,
            track_classification=_track_classification(),
        )
    assert excinfo.value.code == "map_target_unknown"


def test_out_of_scope_target_not_in_contract_fails_closed() -> None:
    bad_map = _classification_map()
    bad_map["out_of_scope_subcategory"] = "class_nine"
    with pytest.raises(SubcategoryResolverError) as excinfo:
        resolve_subcategory(
            _precheck(),
            classification_map=bad_map,
            track_classification=_track_classification(),
        )
    assert excinfo.value.code == "out_of_scope_target_unknown"


def test_wrong_format_version_fails_closed() -> None:
    bad_map = _classification_map()
    bad_map["format_version"] = "subcategory-classification-map-v0"
    with pytest.raises(SubcategoryResolverError) as excinfo:
        resolve_subcategory(
            _precheck(),
            classification_map=bad_map,
            track_classification=_track_classification(),
        )
    assert excinfo.value.code == "classification_map_version"


def test_default_track_not_in_keys_fails_closed() -> None:
    bad_tracks = _track_classification()
    bad_tracks["default_track"] = "class_nine"
    with pytest.raises(SubcategoryResolverError) as excinfo:
        resolve_subcategory(
            _precheck(),
            classification_map=_classification_map(),
            track_classification=bad_tracks,
        )
    assert excinfo.value.code == "default_track_unknown"


def test_invalid_min_confidence_fails_closed() -> None:
    bad_map = _classification_map()
    bad_map["min_confidence"] = 1.5
    with pytest.raises(SubcategoryResolverError) as excinfo:
        validate_classification_map(bad_map, valid_track_keys={"class_one", "class_three"})
    assert excinfo.value.code == "min_confidence_invalid"


def test_empty_category_map_fails_closed() -> None:
    bad_map = _classification_map()
    bad_map["category_to_subcategory"] = {}
    with pytest.raises(SubcategoryResolverError) as excinfo:
        validate_classification_map(bad_map, valid_track_keys={"class_three"})
    assert excinfo.value.code == "category_map_invalid"


def test_non_object_classification_map_fails_closed() -> None:
    with pytest.raises(SubcategoryResolverError) as excinfo:
        validate_classification_map("nope", valid_track_keys={"class_three"})
    assert excinfo.value.code == "classification_map_not_object"


def test_malformed_track_classification_fails_closed() -> None:
    with pytest.raises(SubcategoryResolverError) as excinfo:
        resolve_subcategory(
            _precheck(),
            classification_map=_classification_map(),
            track_classification={"tracks": []},
        )
    assert excinfo.value.code == "track_classification_invalid"


# --- Determinism + JSON-serializability. ---


def test_result_is_json_serializable() -> None:
    result = _resolve(_precheck(primary_category="建筑设计", primary_confidence=0.9))
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_determinism_same_input_same_output() -> None:
    first = _resolve(_precheck(primary_category="产品设计", primary_confidence=0.7))
    second = _resolve(_precheck(primary_category="产品设计", primary_confidence=0.7))
    assert first == second


def test_notes_records_resolution_path() -> None:
    result = _resolve(_precheck(primary_category="建筑设计", primary_confidence=0.9))
    assert isinstance(result["notes"], list)
    assert any("命中映射" in note for note in result["notes"])
