"""ADR-0033 Task 3 tests: isolated v3-config CRUD + validation API.

Exercises the router built by ``build_category_evaluation_v3_config_router``
through a FastAPI ``TestClient`` with ``get_db`` overridden onto an isolated
in-memory SQLite engine (StaticPool) and ``require_user`` overridden to a
placeholder principal — mirroring ``test_material_packages_api`` isolation and
``test_category_evaluation_preview_api`` auth-override patterns.  The real
database is never touched.

Coverage:
- create → get → update (revision +1, hash changes) round trip.
- invalid contract / classification_map / subcategory_dimensions are each
  rejected with a coded 400 and nothing is persisted.
- duplicate category_key → coded 409.
- ``/validate`` reports coded errors and does not persist.
- status endpoint flips draft → retired without bumping revision.
- unauthenticated (missing principal) → 401.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.category_evaluation_v3_config_api import (
    build_category_evaluation_v3_config_router,
)
from app.database import Base, get_db
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.migrations import run_migrations


_BASE = "/api/category-evaluation/v3-config"
_PROPOSAL_CONTRACT = (
    Path(__file__).parents[1]
    / "app"
    / "proposal_text_assets"
    / "v3_contract_proposal_text_v1.json"
)


def _valid_body(category_key: str = "inspiration_image") -> dict[str, Any]:
    return {
        "category_key": category_key,
        "display_name": "灵感图 v3",
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
    }


def _proposal_body() -> dict[str, Any]:
    return {
        "category_key": "proposal_text_pdf",
        "display_name": "PDF方案文本",
        "contract": json.loads(_PROPOSAL_CONTRACT.read_text(encoding="utf-8")),
        "classification_map": {
            "profile_type": "text-proposal-additive-v1",
            "source": "precheck.信息提取.项目分类.审核类别",
        },
        "subcategory_dimensions": {
            "profile_type": "text-proposal-additive-v1",
            "tracks": ["A", "B", "C", "balanced"],
        },
    }


class _Principal:
    username = "v3-config-tester"


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _make_client(
    sessions: sessionmaker[Session], *, authenticated: bool = True
) -> TestClient:
    app = FastAPI()

    def require_user() -> Any:
        if not authenticated:
            raise HTTPException(status_code=401, detail="请先登录")
        return _Principal()

    def test_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.include_router(build_category_evaluation_v3_config_router(require_user))
    app.dependency_overrides[get_db] = test_db
    return TestClient(app)


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    with _make_client(sessions) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 1. create → get → update round trip
# --------------------------------------------------------------------------- #


def test_create_get_update_round_trip(client: TestClient) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["category_key"] == "inspiration_image"
    assert body["status"] == "draft"
    assert body["revision"] == 1
    assert body["created_by"] == "v3-config-tester"
    original_hash = body["contract_hash"]
    assert len(original_hash) == 64

    listed = client.get(f"{_BASE}/")
    assert listed.status_code == 200
    assert [item["category_key"] for item in listed.json()["items"]] == [
        "inspiration_image"
    ]

    fetched = client.get(f"{_BASE}/inspiration_image")
    assert fetched.status_code == 200
    assert fetched.json()["contract"] == build_inspiration_v3_contract()
    assert fetched.json()["mechanism_profile"] == {
        "profile_type": "image-rule-deduction-v1",
        "source": "legacy_image_shape",
        "supported": True,
        "editable": True,
        "reason": None,
    }

    # Mutate the contract (drop AI-image penalty magnitude) → hash must change.
    updated_body = _valid_body()
    updated_body["display_name"] = "灵感图 v3（改）"
    updated_body["contract"]["common_modifiers"]["media_type_penalty"][
        "penalties"
    ]["ai_image"] = -10
    updated = client.put(f"{_BASE}/inspiration_image", json=updated_body)
    assert updated.status_code == 200, updated.text
    updated_json = updated.json()
    assert updated_json["revision"] == 2
    assert updated_json["display_name"] == "灵感图 v3（改）"
    assert updated_json["contract_hash"] != original_hash


def test_proposal_profile_reads_and_validates_without_image_fields(client: TestClient) -> None:
    created = client.post(f"{_BASE}/", json=_proposal_body())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["mechanism_profile"] == {
        "profile_type": "text-proposal-additive-v1",
        "source": "explicit",
        "supported": True,
        "editable": True,
        "reason": None,
    }
    assert body["dimension_deduction_rules"] == {}
    assert body["media_penalty_enabled"] is False

    validated = client.post(
        f"{_BASE}/proposal_text_pdf/validate", json=_proposal_body()
    )
    assert validated.status_code == 200, validated.text
    assert validated.json() == {"ok": True, "errors": []}


def test_unknown_explicit_profile_is_readable_but_validation_is_fail_closed(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    with sessions() as db:
        from app.category_evaluation_contract import canonical_contract_hash
        from app.dimension_schema_registry import canonical_json
        from app.models import CategoryEvaluationV3Config

        contract = {"profile_type": "future-3d-v1", "category_key": "future_3d"}
        db.add(
            CategoryEvaluationV3Config(
                category_key="future_3d",
                display_name="未来 3D 机制",
                status="draft",
                contract_json=canonical_json(contract),
                classification_map_json="{}",
                subcategory_dimensions_json="{}",
                dimension_deduction_rules_json="{}",
                media_penalty_enabled=False,
                revision=1,
                contract_hash=canonical_contract_hash(contract),
                created_by="test",
            )
        )
        db.commit()

    fetched = client.get(f"{_BASE}/future_3d")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["mechanism_profile"] == {
        "profile_type": "future-3d-v1",
        "source": "explicit",
        "supported": False,
        "editable": False,
        "reason": "未注册机制 profile：future-3d-v1",
    }

    unknown_body = {
        "category_key": "future_3d",
        "display_name": "未来 3D 机制",
        "contract": {"profile_type": "future-3d-v1", "category_key": "future_3d"},
        "classification_map": {},
        "subcategory_dimensions": {},
    }
    validated = client.post(f"{_BASE}/future_3d/validate", json=unknown_body)
    assert validated.status_code == 200, validated.text
    assert validated.json() == {
        "ok": False,
        "errors": [
            {
                "target": "mechanism_profile",
                "code": "profile_type_unsupported",
                "message": "未注册机制 profile：future-3d-v1",
            }
        ],
    }


def _five_level_scale() -> dict[str, Any]:
    return {
        "version": "category-level-scale-v1",
        "levels": [
            {"level": "L1", "enabled": True, "min_score": 90, "display_name": "优选"},
            {"level": "L2", "enabled": True, "min_score": 75, "display_name": "良好"},
            {"level": "L3", "enabled": True, "min_score": 60, "display_name": "常规"},
            {"level": "L4", "enabled": True, "min_score": 1, "display_name": "较差"},
            {"level": "L5", "enabled": True, "min_score": 0, "display_name": "红线"},
        ],
    }


def test_level_scale_get_and_put_are_revision_guarded(client: TestClient) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body()).json()

    current = client.get(f"{_BASE}/inspiration_image/level-scale")
    assert current.status_code == 200, current.text
    current_json = current.json()
    assert current_json["revision"] == 1
    assert current_json["contract_hash"] == created["contract_hash"]
    assert current_json["level_scale"] is None
    assert current_json["level_thresholds"][-1] == {"level": "L4", "min_score": 0}

    updated = client.put(
        f"{_BASE}/inspiration_image/level-scale",
        json={
            "expected_revision": 1,
            "expected_contract_hash": created["contract_hash"],
            "level_scale": _five_level_scale(),
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["revision"] == 2
    assert body["contract_hash"] != created["contract_hash"]
    assert body["level_scale"] == _five_level_scale()
    assert body["level_thresholds"] is None
    assert body["resolved_level_scale"]["enabled_levels"] == [
        "L1", "L2", "L3", "L4", "L5"
    ]

    stale = client.put(
        f"{_BASE}/inspiration_image/level-scale",
        json={"expected_revision": 1, "level_scale": _five_level_scale()},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert client.get(f"{_BASE}/inspiration_image").json()["revision"] == 2


def test_level_scale_put_rejects_disabled_redline_without_mutation(
    client: TestClient,
) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body()).json()
    scale = _five_level_scale()
    scale["levels"][3]["min_score"] = 0
    scale["levels"][4] = {
        "level": "L5",
        "enabled": False,
        "display_name": "停用",
    }

    response = client.put(
        f"{_BASE}/inspiration_image/level-scale",
        json={"expected_revision": 1, "level_scale": scale},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "redline_level_disabled"
    unchanged = client.get(f"{_BASE}/inspiration_image").json()
    assert unchanged["revision"] == 1
    assert unchanged["contract_hash"] == created["contract_hash"]


def test_level_scale_put_rejects_hash_conflict(client: TestClient) -> None:
    client.post(f"{_BASE}/", json=_valid_body())
    response = client.put(
        f"{_BASE}/inspiration_image/level-scale",
        json={
            "expected_revision": 1,
            "expected_contract_hash": "0" * 64,
            "level_scale": _five_level_scale(),
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "contract_hash_conflict"


def test_level_scale_put_can_atomically_move_redline_to_enabled_floor(
    client: TestClient,
) -> None:
    client.post(f"{_BASE}/", json=_valid_body())
    scale = _five_level_scale()
    scale["levels"][3]["min_score"] = 0
    scale["levels"][4] = {
        "level": "L5",
        "enabled": False,
        "display_name": "停用",
    }
    response = client.put(
        f"{_BASE}/inspiration_image/level-scale",
        json={
            "expected_revision": 1,
            "level_scale": scale,
            "redline_hit_level": "L4",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["resolved_level_scale"]["disabled_levels"] == ["L5"]
    contract = client.get(f"{_BASE}/inspiration_image").json()["contract"]
    assert contract["redline_policy"]["hit_level"] == "L4"


# --------------------------------------------------------------------------- #
# 2. invalid artifacts are rejected with coded 400 and not persisted
# --------------------------------------------------------------------------- #


def test_invalid_contract_rejected_and_not_persisted(client: TestClient) -> None:
    bad = _valid_body()
    bad["contract"]["schema_version"] = "evaluation-category-profile-v2"
    response = client.post(f"{_BASE}/", json=bad)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "schema_version_unsupported"
    # Nothing landed.
    assert client.get(f"{_BASE}/").json()["items"] == []


def test_invalid_classification_map_rejected(client: TestClient) -> None:
    bad = _valid_body()
    bad["classification_map"]["category_to_subcategory"]["建筑设计"] = "no_such_track"
    response = client.post(f"{_BASE}/", json=bad)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "map_target_unknown"
    assert client.get(f"{_BASE}/").json()["items"] == []


def test_invalid_subcategory_dimensions_rejected(client: TestClient) -> None:
    bad = _valid_body()
    # Break group_weights so they no longer renormalize legally (empty schema
    # while claiming weight) — surfaces a coded composition error.
    bad["subcategory_dimensions"]["class_one"]["common_group"]["group_weight"] = -1
    response = client.post(f"{_BASE}/", json=bad)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "common_group.group_weight_invalid"
    assert client.get(f"{_BASE}/").json()["items"] == []


# --------------------------------------------------------------------------- #
# 3. duplicate key → coded 409
# --------------------------------------------------------------------------- #


def test_duplicate_category_key_rejected(client: TestClient) -> None:
    first = client.post(f"{_BASE}/", json=_valid_body())
    assert first.status_code == 201
    second = client.post(f"{_BASE}/", json=_valid_body())
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "v3_config_duplicate_key"


# --------------------------------------------------------------------------- #
# 4. /validate does not persist
# --------------------------------------------------------------------------- #


def test_validate_endpoint_reports_errors_without_persisting(
    client: TestClient,
) -> None:
    ok = client.post(f"{_BASE}/validate", json=_valid_body())
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"ok": True, "errors": []}

    bad = _valid_body()
    bad["contract"]["schema_version"] = "evaluation-category-profile-v2"
    invalid = client.post(f"{_BASE}/validate", json=bad)
    assert invalid.status_code == 200, invalid.text
    payload = invalid.json()
    assert payload["ok"] is False
    assert any(err["code"] == "schema_version_unsupported" for err in payload["errors"])

    # Validate never writes.
    assert client.get(f"{_BASE}/").json()["items"] == []


def test_missing_config_returns_coded_404(client: TestClient) -> None:
    response = client.get(f"{_BASE}/does_not_exist")
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "v3_config_not_found"


# --------------------------------------------------------------------------- #
# 5. status endpoint (retire without DELETE), no revision bump
# --------------------------------------------------------------------------- #


def test_status_change_retires_without_revision_bump(client: TestClient) -> None:
    client.post(f"{_BASE}/", json=_valid_body())
    retired = client.put(
        f"{_BASE}/inspiration_image/status", json={"status": "retired"}
    )
    assert retired.status_code == 200, retired.text
    body = retired.json()
    assert body["status"] == "retired"
    assert body["revision"] == 1  # status change does not bump the contract revision

    bad = client.put(
        f"{_BASE}/inspiration_image/status", json={"status": "bogus"}
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "invalid_status"


# --------------------------------------------------------------------------- #
# 6. unauthenticated → 401
# --------------------------------------------------------------------------- #


def test_unauthenticated_requests_rejected(
    sessions: sessionmaker[Session],
) -> None:
    with _make_client(sessions, authenticated=False) as client:
        assert client.get(f"{_BASE}/").status_code == 401
        assert client.post(f"{_BASE}/", json=_valid_body()).status_code == 401
