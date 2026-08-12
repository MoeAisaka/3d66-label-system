"""Mechanism-profile resolution and validation for v3 category contracts.

The stored contract remains the source artifact. This registry only describes
and routes it; legacy image contracts are never rewritten merely to add a
``profile_type`` marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .category_evaluation_contract import validate_category_evaluation_contract
from .dimension_composition import validate_subcategory_dimensions
from .dimension_deduction_bridge import extract_dimension_deduction_rules
from .proposal_text_contract import validate_proposal_text_contract
from .redline_policy import evaluate_redlines
from .subcategory_resolver import validate_classification_map


IMAGE_PROFILE = "image-rule-deduction-v1"
PROPOSAL_PROFILE = "text-proposal-additive-v1"
SUPPORTED_PROFILES = frozenset({IMAGE_PROFILE, PROPOSAL_PROFILE})


@dataclass(frozen=True)
class MechanismProfileResolution:
    profile_type: str | None
    source: Literal["explicit", "legacy_image_shape", "unresolved"]
    supported: bool
    editable: bool
    reason: str | None = None


class MechanismProfileError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        target: str = "mechanism_profile",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.target = target


def _legacy_image_shape(contract: dict[str, Any]) -> bool:
    if contract.get("schema_version") != "evaluation-category-profile-v3":
        return False
    track_classification = contract.get("track_classification")
    if not isinstance(track_classification, dict):
        return False
    tracks = track_classification.get("tracks")
    return isinstance(tracks, list) and bool(tracks)


def _image_candidate_shape(contract: dict[str, Any]) -> bool:
    """Recognize an image candidate for write-time precise validation.

    Unlike the read descriptor, this permits a malformed schema_version so the
    existing image validator can preserve its stable, specific error code.
    """
    track_classification = contract.get("track_classification")
    common_modifiers = contract.get("common_modifiers")
    redline_policy = contract.get("redline_policy")
    tracks = (
        track_classification.get("tracks")
        if isinstance(track_classification, dict)
        else None
    )
    return (
        isinstance(tracks, list)
        and bool(tracks)
        and isinstance(common_modifiers, dict)
        and isinstance(redline_policy, dict)
    )


def describe_mechanism_profile(contract: Any) -> MechanismProfileResolution:
    """Describe a stored contract without raising or mutating it."""
    if not isinstance(contract, dict):
        return MechanismProfileResolution(
            profile_type=None,
            source="unresolved",
            supported=False,
            editable=False,
            reason="评测合同必须是对象",
        )

    if "profile_type" in contract:
        profile_type = contract.get("profile_type")
        if not isinstance(profile_type, str) or not profile_type.strip():
            return MechanismProfileResolution(
                profile_type=None,
                source="unresolved",
                supported=False,
                editable=False,
                reason="profile_type 必须是非空字符串",
            )
        supported = profile_type in SUPPORTED_PROFILES
        return MechanismProfileResolution(
            profile_type=profile_type,
            source="explicit",
            supported=supported,
            editable=supported,
            reason=None if supported else f"未注册机制 profile：{profile_type}",
        )

    if _legacy_image_shape(contract):
        return MechanismProfileResolution(
            profile_type=IMAGE_PROFILE,
            source="legacy_image_shape",
            supported=True,
            editable=True,
        )

    return MechanismProfileResolution(
        profile_type=None,
        source="unresolved",
        supported=False,
        editable=False,
        reason="无法识别机制 profile",
    )


def _raise_wrapped(exc: ValueError, *, target: str, fallback_code: str) -> None:
    raise MechanismProfileError(
        getattr(exc, "code", fallback_code),
        str(exc),
        target=target,
    ) from exc


def _image_track_keys(contract: dict[str, Any]) -> set[str]:
    block = contract.get("track_classification")
    tracks = block.get("tracks") if isinstance(block, dict) else None
    if not isinstance(tracks, list):
        return set()
    return {
        track["key"]
        for track in tracks
        if isinstance(track, dict) and isinstance(track.get("key"), str)
    }


def _validate_image_artifacts(
    contract: dict[str, Any],
    classification_map: Any,
    subcategory_dimensions: Any,
) -> None:
    try:
        validate_category_evaluation_contract(contract)
    except ValueError as exc:
        _raise_wrapped(exc, target="contract", fallback_code="invalid_contract")

    try:
        evaluate_redlines(
            {"production_fields": {"reason": []}},
            policy=contract["redline_policy"],
        )
    except ValueError as exc:
        _raise_wrapped(
            exc,
            target="redline_policy",
            fallback_code="invalid_redline_policy",
        )

    try:
        validate_classification_map(
            classification_map,
            valid_track_keys=_image_track_keys(contract),
        )
    except ValueError as exc:
        _raise_wrapped(
            exc,
            target="classification_map",
            fallback_code="invalid_classification_map",
        )

    if not isinstance(subcategory_dimensions, dict):
        raise MechanismProfileError(
            "subcategory_dimensions_not_object",
            "subcategory_dimensions 必须是 {track_key: config} 对象",
            target="subcategory_dimensions",
        )
    for track_key, config in subcategory_dimensions.items():
        try:
            validate_subcategory_dimensions(config)
        except ValueError as exc:
            _raise_wrapped(
                exc,
                target=f"subcategory_dimensions.{track_key}",
                fallback_code="invalid_subcategory_dimensions",
            )


def _validate_proposal_artifacts(
    contract: dict[str, Any],
    classification_map: Any,
    subcategory_dimensions: Any,
) -> None:
    try:
        validate_proposal_text_contract(contract)
    except ValueError as exc:
        _raise_wrapped(
            exc,
            target="contract",
            fallback_code="proposal_contract_invalid",
        )

    if (
        not isinstance(classification_map, dict)
        or classification_map.get("profile_type") != PROPOSAL_PROFILE
    ):
        raise MechanismProfileError(
            "classification_map_profile_mismatch",
            f"classification_map.profile_type 必须是 {PROPOSAL_PROFILE}",
            target="classification_map",
        )
    if (
        not isinstance(subcategory_dimensions, dict)
        or subcategory_dimensions.get("profile_type") != PROPOSAL_PROFILE
    ):
        raise MechanismProfileError(
            "subcategory_dimensions_profile_mismatch",
            f"subcategory_dimensions.profile_type 必须是 {PROPOSAL_PROFILE}",
            target="subcategory_dimensions",
        )


def validate_mechanism_artifacts(
    contract: Any,
    classification_map: Any,
    subcategory_dimensions: Any,
) -> str:
    """Validate one supported mechanism bundle and return its profile type."""
    resolution = describe_mechanism_profile(contract)
    if not resolution.supported or resolution.profile_type is None:
        if (
            isinstance(contract, dict)
            and "profile_type" not in contract
            and _image_candidate_shape(contract)
        ):
            _validate_image_artifacts(
                contract,
                classification_map,
                subcategory_dimensions,
            )
            return IMAGE_PROFILE
        code = (
            "profile_type_unsupported"
            if resolution.source == "explicit" and resolution.profile_type
            else "profile_type_unresolved"
        )
        raise MechanismProfileError(
            code,
            resolution.reason or "无法识别机制 profile",
        )

    if resolution.profile_type == IMAGE_PROFILE:
        _validate_image_artifacts(
            contract,
            classification_map,
            subcategory_dimensions,
        )
    elif resolution.profile_type == PROPOSAL_PROFILE:
        _validate_proposal_artifacts(
            contract,
            classification_map,
            subcategory_dimensions,
        )
    else:  # Defensive guard for future registry edits.
        raise MechanismProfileError(
            "profile_type_unsupported",
            f"未注册机制 profile：{resolution.profile_type}",
        )
    return resolution.profile_type


def extract_profile_rule_mirror(
    profile_type: str,
    subcategory_dimensions: dict[str, Any],
) -> dict[str, Any]:
    if profile_type == IMAGE_PROFILE:
        return extract_dimension_deduction_rules(subcategory_dimensions)
    if profile_type == PROPOSAL_PROFILE:
        return {}
    raise MechanismProfileError(
        "profile_type_unsupported",
        f"未注册机制 profile：{profile_type}",
    )


def profile_media_penalty_enabled(profile_type: str, contract: dict[str, Any]) -> bool:
    if profile_type == PROPOSAL_PROFILE:
        return False
    if profile_type == IMAGE_PROFILE:
        common_modifiers = contract.get("common_modifiers")
        media_penalty = (
            common_modifiers.get("media_type_penalty")
            if isinstance(common_modifiers, dict)
            else None
        )
        return (
            bool(media_penalty.get("enabled", True))
            if isinstance(media_penalty, dict)
            else True
        )
    raise MechanismProfileError(
        "profile_type_unsupported",
        f"未注册机制 profile：{profile_type}",
    )
