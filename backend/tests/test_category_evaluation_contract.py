from __future__ import annotations

import pytest

from app.category_evaluation_contract import (
    CATEGORY_EVALUATION_CONTRACT_VERSION,
    CategoryEvaluationContractError,
    canonical_contract_hash,
    validate_category_evaluation_contract,
)
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


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


# Inspiration-image three tracks: 一类 40+60=100, 二类 20+60=80, 三类 40+30=70.
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
                "dimension_schema_ref": {
                    "schema_key": "space_aesthetic",
                    "version": "1.3.0",
                },
            },
            {
                "key": "class_two",
                "label": "二类",
                "base_score": 20,
                "dimension_max": 60,
                "track_cap": 80,
                "dimension_schema_ref": {
                    "schema_key": "space_aesthetic",
                    "version": "1.3.0",
                },
            },
            {
                "key": "class_three",
                "label": "三类",
                "base_score": 40,
                "dimension_max": 30,
                "track_cap": 70,
                "dimension_schema_ref": {
                    "schema_key": "space_aesthetic",
                    "version": "1.3.0",
                },
            },
        ],
        "default_track": "class_three",
    }


def _common_modifiers() -> dict:
    return {
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
    }


def _contract() -> dict:
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": _common_modifiers(),
    }


def test_valid_v3_contract_passes() -> None:
    assert validate_category_evaluation_contract(_contract()) is None


def test_inspiration_track_numbers_are_the_expected_boundaries() -> None:
    tracks = {t["key"]: t for t in _track_classification()["tracks"]}
    assert (tracks["class_one"]["base_score"], tracks["class_one"]["dimension_max"], tracks["class_one"]["track_cap"]) == (40, 60, 100)
    assert (tracks["class_two"]["base_score"], tracks["class_two"]["dimension_max"], tracks["class_two"]["track_cap"]) == (20, 60, 80)
    assert (tracks["class_three"]["base_score"], tracks["class_three"]["dimension_max"], tracks["class_three"]["track_cap"]) == (40, 30, 70)


def test_bad_schema_version_fails_closed() -> None:
    contract = _contract()
    contract["schema_version"] = "evaluation-category-profile-v2"
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "schema_version_unsupported"


def test_missing_block_fails_closed() -> None:
    contract = _contract()
    del contract["common_modifiers"]
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "block_missing"


def test_redline_block_errors_are_wrapped_with_prefixed_code() -> None:
    contract = _contract()
    contract["redline_policy"]["hit_level"] = "L9"
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "redline_policy.hit_level_invalid"


def test_track_cap_boundary_base_plus_dim_equals_cap_is_valid() -> None:
    # 一类 40+60=100 sits exactly on base+dim<=cap<=100; must pass.
    validate_category_evaluation_contract(_contract())


def test_track_cap_violation_fails_closed() -> None:
    contract = _contract()
    # base_score+dimension_max (40+60=100) now exceeds track_cap 70.
    contract["track_classification"]["tracks"][0]["track_cap"] = 70
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "track_cap_inconsistent"


def test_track_cap_over_100_fails_closed() -> None:
    contract = _contract()
    contract["track_classification"]["tracks"][0]["track_cap"] = 120
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "track_score_out_of_range"


def test_duplicate_track_key_fails_closed() -> None:
    contract = _contract()
    contract["track_classification"]["tracks"][1]["key"] = "class_one"
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "track_key_duplicate"


def test_unknown_default_track_fails_closed() -> None:
    contract = _contract()
    contract["track_classification"]["default_track"] = "class_nine"
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "default_track_unknown"


def test_empty_tracks_fails_closed() -> None:
    contract = _contract()
    contract["track_classification"]["tracks"] = []
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "tracks_empty"


def test_media_penalty_missing_key_fails_closed() -> None:
    contract = _contract()
    del contract["common_modifiers"]["media_type_penalty"]["penalties"]["other"]
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "media_penalty_keys"


def test_positive_media_penalty_fails_closed() -> None:
    contract = _contract()
    contract["common_modifiers"]["media_type_penalty"]["penalties"]["render_3d"] = 5
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "media_penalty_value"


def test_nonzero_baseline_penalty_fails_closed() -> None:
    contract = _contract()
    contract["common_modifiers"]["media_type_penalty"]["penalties"]["real_photo"] = -3
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "media_penalty_baseline_nonzero"


def test_veto_cap_not_below_threshold_fails_closed() -> None:
    contract = _contract()
    contract["common_modifiers"]["high_score_veto"] = {"threshold": 80, "cap_to": 80}
    with pytest.raises(CategoryEvaluationContractError) as excinfo:
        validate_category_evaluation_contract(contract)
    assert excinfo.value.code == "veto_inconsistent"


def test_canonical_hash_is_key_order_independent() -> None:
    contract = _contract()
    reordered = {
        "common_modifiers": _common_modifiers(),
        "track_classification": _track_classification(),
        "redline_policy": _redline_policy(),
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
    }
    assert canonical_contract_hash(contract) == canonical_contract_hash(reordered)


def test_canonical_hash_differs_for_different_structure() -> None:
    contract = _contract()
    changed = _contract()
    changed["common_modifiers"]["high_score_veto"]["cap_to"] = 78
    assert canonical_contract_hash(contract) != canonical_contract_hash(changed)


def test_canonical_hash_is_stable_across_calls() -> None:
    contract = _contract()
    assert canonical_contract_hash(contract) == canonical_contract_hash(contract)
