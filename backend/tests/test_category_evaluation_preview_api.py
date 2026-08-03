"""ADR-0033 Phase 4-preview tests: read-only + dry-run preview API.

Exercises the isolated router built by
``build_category_evaluation_preview_router`` through a FastAPI ``TestClient``
with ``require_user`` overridden to a placeholder (bypassing real auth), mirror-
ing the ``test_p0e_canary_api`` dependency-override pattern.

Coverage:
- GET contract → 200 with contract / classification_map / subcategory_dimensions
  / seed_version and the correct ``schema_version``.
- POST evaluate redline hit (``reason=["是截图"]``) → 200, hard_reject, L5, 49.
- POST evaluate 建筑设计 grade5 实拍 → 200, class_one, 100, L1.
- POST evaluate invalid input (grade out of range) → 400 with a ``code`` (not 500).
- POST validate three valid artifacts → all ``*_valid`` true, empty ``errors``.
- POST validate a broken contract → ``contract_valid`` false with a coded error.
- Determinism: the same evaluate request yields the same response twice.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.category_evaluation_preview_api import (
    build_category_evaluation_preview_router,
)
from app.inspiration_category_seed import (
    TRACK_CLASS_ONE,
    TRACK_CLASS_THREE,
    TRACK_CLASS_TWO,
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)


# --- shared grade fixtures (simulate 调用B): all-5 = full marks, zero deductions ---

_COMMON_GRADE5 = {
    "presentation_integrity": 5,
    "visual_hierarchy": 5,
    "inspiration_reference": 5,
}
_SPECIFIC_GRADE5 = {
    TRACK_CLASS_ONE: {"spatial_originality": 5, "design_trendiness": 5},
    TRACK_CLASS_TWO: {"product_form_language": 5, "artistic_expression": 5},
    TRACK_CLASS_THREE: {"visual_impact": 5},
}


def _common_grades_all5() -> dict[str, Any]:
    return {track: dict(_COMMON_GRADE5) for track in _SPECIFIC_GRADE5}


def _specific_grades_all5() -> dict[str, Any]:
    return {track: dict(grades) for track, grades in _SPECIFIC_GRADE5.items()}


def _precheck(
    *,
    reason: list[str] | None = None,
    trait: str = "实景照片",
    category: str = "建筑设计",
    confidence: float = 0.95,
    scope_status: str = "in_scope",
) -> dict[str, Any]:
    production_fields: dict[str, Any] = {"trait": trait}
    if reason is not None:
        production_fields["reason"] = reason
    return {
        "production_fields": production_fields,
        "classification": {
            "scope_status": scope_status,
            "primary_category": category,
            "primary_confidence": confidence,
        },
    }


def _evaluate_body(precheck: dict[str, Any]) -> dict[str, Any]:
    return {
        "precheck": precheck,
        "common_grades_by_track": _common_grades_all5(),
        "specific_grades_by_track": _specific_grades_all5(),
    }


def _fake_require_user() -> dict[str, str]:
    """Placeholder authenticated principal — bypasses real auth for the tests."""
    return {"username": "preview-tester"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(
        build_category_evaluation_preview_router(_fake_require_user)
    )
    with TestClient(app) as test_client:
        yield test_client


_BASE = "/api/category-evaluation/preview"


# --------------------------------------------------------------------------- #
# 1. GET contract (read-only)
# --------------------------------------------------------------------------- #


def test_get_contract_returns_assembled_config(client: TestClient) -> None:
    response = client.get(f"{_BASE}/inspiration/contract")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "contract",
        "classification_map",
        "subcategory_dimensions",
        "seed_version",
    }
    assert body["contract"]["schema_version"] == "evaluation-category-profile-v3"
    assert body["seed_version"] == "inspiration-category-seed-v1"
    # The read-only endpoint returns exactly what the seed builders assemble.
    assert body["contract"] == build_inspiration_v3_contract()
    assert body["classification_map"] == build_inspiration_classification_map()
    assert body["subcategory_dimensions"] == (
        build_inspiration_subcategory_dimensions()
    )


# --------------------------------------------------------------------------- #
# 2. POST evaluate (dry-run)
# --------------------------------------------------------------------------- #


def test_evaluate_redline_hit_short_circuits(client: TestClient) -> None:
    response = client.post(
        f"{_BASE}/inspiration/evaluate",
        json=_evaluate_body(_precheck(reason=["是截图"])),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["redline"]["hit"] is True
    assert body["redline"]["hit_rules"] == ["screenshot"]
    assert body["resolved"] is None
    result = body["result"]
    assert result["hard_reject"] is True
    assert result["terminated_at"] == "redline"
    assert result["level"] == "L5"
    assert result["score"] == 49


def test_evaluate_architecture_grade5_real_photo_class_one(
    client: TestClient,
) -> None:
    response = client.post(
        f"{_BASE}/inspiration/evaluate",
        json=_evaluate_body(
            _precheck(category="建筑设计", trait="实景照片", confidence=0.95)
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["redline"]["hit"] is False
    assert body["resolved"]["track_key"] == TRACK_CLASS_ONE
    assert body["resolved"]["resolved_by"] == "mapped"
    result = body["result"]
    assert result["track_key"] == TRACK_CLASS_ONE
    assert result["base_score"] == 40
    assert result["dimension_max"] == 60
    assert result["score"] == 100
    assert result["level"] == "L1"
    assert result["hard_reject"] is False


def test_evaluate_invalid_grade_returns_coded_400_not_500(
    client: TestClient,
) -> None:
    # A grade of 9 is out of the 1-5 range; the reused grade bridge fails closed
    # and the router must surface it as a coded HTTP 400 (never a 500).
    specific = _specific_grades_all5()
    specific[TRACK_CLASS_ONE]["spatial_originality"] = 9
    response = client.post(
        f"{_BASE}/inspiration/evaluate",
        json={
            "precheck": _precheck(category="建筑设计", confidence=0.95),
            "common_grades_by_track": _common_grades_all5(),
            "specific_grades_by_track": specific,
        },
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"]
    assert "grade_out_of_range" in detail["code"]


def test_evaluate_is_deterministic(client: TestClient) -> None:
    body = _evaluate_body(
        _precheck(category="建筑设计", trait="实景照片", confidence=0.95)
    )
    first = client.post(f"{_BASE}/inspiration/evaluate", json=body)
    second = client.post(f"{_BASE}/inspiration/evaluate", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


# --------------------------------------------------------------------------- #
# 3. POST validate
# --------------------------------------------------------------------------- #


def test_validate_all_valid_inspiration_artifacts(client: TestClient) -> None:
    response = client.post(
        f"{_BASE}/validate",
        json={
            "contract": build_inspiration_v3_contract(),
            "classification_map": build_inspiration_classification_map(),
            "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract_valid"] is True
    assert body["classification_map_valid"] is True
    assert body["subcategory_dimensions_valid"] is True
    assert body["errors"] == []


def test_validate_broken_contract_reports_coded_error(
    client: TestClient,
) -> None:
    broken = build_inspiration_v3_contract()
    broken["schema_version"] = "evaluation-category-profile-v2"
    response = client.post(
        f"{_BASE}/validate",
        json={"contract": broken},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract_valid"] is False
    # Untouched artifacts stay valid; only the broken one is flagged.
    assert body["classification_map_valid"] is True
    assert body["subcategory_dimensions_valid"] is True
    assert len(body["errors"]) == 1
    error = body["errors"][0]
    assert error["target"] == "contract"
    assert error["code"] == "schema_version_unsupported"
    assert error["message"]
