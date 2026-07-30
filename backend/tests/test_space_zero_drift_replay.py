from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
    space_schema_definition_for_version,
)
from app.scoring import calculate_corrected_score, calculate_score


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "space_zero_drift_v1.json"
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
def _deep_merge(
    target: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(
            target.get(key), dict
        ):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def _precheck(case: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    return _deep_merge(
        payload,
        case.get("precheck_overrides") or {},
    )


def _aesthetic(
    case: dict[str, Any],
) -> dict[str, Any] | None:
    if "aesthetic" in case and case["aesthetic"] is None:
        return None
    grades = case["grades"]
    evidence_count = int(case.get("evidence_count", 3))
    payload: dict[str, Any] = {
        "dimensions": {
            key: {
                "grade": grade,
                "evidence": [
                    f"证据{index + 1}"
                    for index in range(evidence_count)
                ],
            }
            for key, grade in zip(
                SPACE_KEYS,
                grades,
                strict=True,
            )
        },
        "special_checks": {},
        "assessment_confidence": float(
            case.get("assessment_confidence", 0.95)
        ),
        "needs_review": False,
        "review_reasons": [],
    }
    profile = case.get("profile")
    if profile is not None:
        payload["scoring_profile"] = profile
    return _deep_merge(
        payload,
        case.get("aesthetic_overrides") or {},
    )


def _schema(case: dict[str, Any]) -> dict[str, Any]:
    version = (
        ACTIVE_V13_VERSION
        if case.get("profile") == "space_aesthetic_v1.3"
        else HISTORICAL_DEFAULT_VERSION
    )
    return space_schema_definition_for_version(version)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
    FIXTURE = json.load(fixture_file)


@pytest.mark.parametrize(
    "case",
    FIXTURE["cases"],
    ids=lambda case: case["name"],
)
def test_space_schema_matches_frozen_legacy_output(
    case: dict[str, Any],
) -> None:
    precheck = _precheck(case)
    aesthetic = _aesthetic(case)
    definition = _schema(case)
    corrections = case.get("corrections")

    if corrections is None:
        result = calculate_score(
            precheck,
            aesthetic,
            dimension_schema=definition,
        )
    else:
        result = calculate_corrected_score(
            precheck,
            aesthetic,
            corrections,
            dimension_schema=definition,
        )

    current_summary = {
        key: result.get(key)
        for key in case["expected_summary"]
    }
    assert current_summary == case["expected_summary"]
    assert _canonical_sha256(result) == case["expected_sha256"]


def test_zero_drift_fixture_is_bound_to_known_legacy_engine() -> None:
    assert FIXTURE["fixture_version"] == "space-zero-drift-v1"
    assert FIXTURE["legacy_source_commit"] == (
        "2cbb5947e9c76cecb4a8f38a5d50f18952f8390f"
    )
    assert FIXTURE["canonical_json"] == {
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": [",", ":"],
    }
    assert len(FIXTURE["cases"]) == 15
