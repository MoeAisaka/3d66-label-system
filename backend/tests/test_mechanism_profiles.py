from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.mechanism_profiles import (
    MechanismProfileError,
    describe_mechanism_profile,
    extract_profile_rule_mirror,
    profile_media_penalty_enabled,
    validate_mechanism_artifacts,
)


PROPOSAL_CONTRACT = (
    Path(__file__).parents[1]
    / "app"
    / "proposal_text_assets"
    / "v3_contract_proposal_text_v1.json"
)


def proposal_contract() -> dict:
    return json.loads(PROPOSAL_CONTRACT.read_text(encoding="utf-8"))


def proposal_classification_map() -> dict:
    return {
        "profile_type": "text-proposal-additive-v1",
        "source": "precheck.信息提取.项目分类.审核类别",
    }


def proposal_subcategory_dimensions() -> dict:
    return {
        "profile_type": "text-proposal-additive-v1",
        "tracks": ["A", "B", "C", "balanced"],
    }


def test_explicit_proposal_profile_wins() -> None:
    result = describe_mechanism_profile(proposal_contract())

    assert result.profile_type == "text-proposal-additive-v1"
    assert result.source == "explicit"
    assert result.supported is True
    assert result.editable is True
    assert result.reason is None


def test_legacy_image_contract_resolves_without_rewrite() -> None:
    contract = build_inspiration_v3_contract()
    assert "profile_type" not in contract

    result = describe_mechanism_profile(contract)

    assert result.profile_type == "image-rule-deduction-v1"
    assert result.source == "legacy_image_shape"
    assert result.supported is True
    assert result.editable is True
    assert "profile_type" not in contract


def test_unknown_explicit_profile_is_readable_but_not_writable() -> None:
    contract = {"profile_type": "future-3d-v1", "category_key": "3d_model"}

    result = describe_mechanism_profile(contract)

    assert result.profile_type == "future-3d-v1"
    assert result.source == "explicit"
    assert result.supported is False
    assert result.editable is False
    assert result.reason
    with pytest.raises(MechanismProfileError) as excinfo:
        validate_mechanism_artifacts(contract, {}, {})
    assert excinfo.value.code == "profile_type_unsupported"


def test_unresolved_contract_is_readable_but_not_writable() -> None:
    contract = {"category_key": "damaged"}

    result = describe_mechanism_profile(contract)

    assert result.profile_type is None
    assert result.source == "unresolved"
    assert result.supported is False
    assert result.editable is False
    with pytest.raises(MechanismProfileError) as excinfo:
        validate_mechanism_artifacts(contract, {}, {})
    assert excinfo.value.code == "profile_type_unresolved"


def test_image_profile_reuses_existing_validation_and_derivation() -> None:
    contract = build_inspiration_v3_contract()
    classification_map = build_inspiration_classification_map()
    dimensions = build_inspiration_subcategory_dimensions()

    assert (
        validate_mechanism_artifacts(contract, classification_map, dimensions)
        == "image-rule-deduction-v1"
    )
    assert extract_profile_rule_mirror("image-rule-deduction-v1", dimensions)
    assert profile_media_penalty_enabled("image-rule-deduction-v1", contract) is (
        contract["common_modifiers"]["media_type_penalty"].get("enabled", True)
    )


def test_contract_bound_rule_mirror_keeps_bonus_rules_and_dimension_caps() -> None:
    contract = build_inspiration_v3_contract()
    dimensions = build_inspiration_subcategory_dimensions()
    for config in dimensions.values():
        for dimension in config["common_group"]["schema_definition"]["dimensions"]:
            dimension["dimension_score_cap"] = 92
            dimension["dimension_deduction_cap"] = 50
            dimension["bonus_rules"] = [
                {
                    "rule_id": "composition_clear",
                    "description": "表现清晰完整且有充分证据",
                    "bonus": 5,
                    "tags": ["正向"],
                }
            ]

    mirror = extract_profile_rule_mirror(
        "image-rule-deduction-v1", dimensions, contract
    )
    assert mirror["format_version"] == "scoring-rule-mirror-v1"
    first = mirror["dimensions"]["class_one"]["common_group"][
        "visual_structure"
    ]
    assert first["dimension_score_cap"] == 92
    assert first["dimension_deduction_cap"] == 50
    assert first["bonus_rules"][0]["rule_id"] == "composition_clear"
    assert first["deduction_rules"]
    assert mirror["track_adjustments"] == contract.get("track_adjustments", {})
    assert mirror["capabilities"]["execution_mode"] == "bonus_cap_v2"


def test_proposal_profile_validates_markers_without_image_derivation() -> None:
    contract = proposal_contract()
    classification_map = proposal_classification_map()
    dimensions = proposal_subcategory_dimensions()

    assert (
        validate_mechanism_artifacts(contract, classification_map, dimensions)
        == "text-proposal-additive-v1"
    )
    assert extract_profile_rule_mirror("text-proposal-additive-v1", dimensions) == {}
    assert profile_media_penalty_enabled("text-proposal-additive-v1", contract) is False


@pytest.mark.parametrize("artifact_name", ["classification_map", "subcategory_dimensions"])
def test_proposal_profile_requires_matching_artifact_markers(artifact_name: str) -> None:
    contract = proposal_contract()
    classification_map = proposal_classification_map()
    dimensions = proposal_subcategory_dimensions()
    target = classification_map if artifact_name == "classification_map" else dimensions
    target["profile_type"] = "image-rule-deduction-v1"

    with pytest.raises(MechanismProfileError) as excinfo:
        validate_mechanism_artifacts(contract, classification_map, dimensions)

    assert excinfo.value.code == f"{artifact_name}_profile_mismatch"
