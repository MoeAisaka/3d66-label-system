"""ADR-0033 Task 3 tests: isolated v3-config CRUD + validation API.

Exercises the router built by ``build_category_evaluation_v3_config_router``
through a FastAPI ``TestClient`` with ``get_db`` overridden onto an isolated
in-memory SQLite engine (StaticPool) and ``require_user`` overridden to a
placeholder principal — mirroring ``test_material_packages_api`` isolation and
``test_category_evaluation_preview_api`` auth-override patterns.  The real
database is never touched.

Coverage:
- create → get round trip with a matching projected revision.
- invalid contract / classification_map / subcategory_dimensions are each
  rejected with a coded 400 and nothing is persisted.
- duplicate category_key → coded 409.
- ``/validate`` reports coded errors and does not persist.
- legacy full, level-scale and status mutations are rejected because the
  runtime projection is immutable.
- unauthenticated (missing principal) → 401.
"""

from __future__ import annotations

from copy import deepcopy
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
from app.dimension_schema_registry import canonical_json
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.migrations import run_migrations
from app.models import TagDemandContract


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


def _make_activation_client(
    sessions: sessionmaker[Session], *, admin: bool = True
) -> TestClient:
    app = FastAPI()

    def require_user() -> Any:
        return _Principal()

    def require_admin() -> Any:
        if not admin:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        principal = _Principal()
        principal.username = "v3-config-admin"
        return principal

    def test_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.include_router(
        build_category_evaluation_v3_config_router(require_user, require_admin)
    )
    app.dependency_overrides[get_db] = test_db
    return TestClient(app)


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    with _make_client(sessions) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 1. create → get round trip
# --------------------------------------------------------------------------- #


def test_create_get_round_trip_has_matching_projected_revision(
    client: TestClient,
) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["category_key"] == "inspiration_image"
    assert body["status"] == "draft"
    assert body["revision"] == 1
    assert body["created_by"] == "v3-config-tester"
    assert isinstance(body["projected_revision_id"], int)
    assert body["candidate_count"] == 0
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
        "version": "v1",
        "capabilities": [
            "structured_editor",
            "candidate_validation",
            "candidate_execution",
            "workflow_incremental",
            "workflow_stock",
        ],
        "editor_route": "image-rule",
        "read_only_fallback": False,
        "can_execute": True,
    }
    assert fetched.json()["projected_revision_id"] == body["projected_revision_id"]
    assert fetched.json()["contract_hash"] == original_hash


def test_detail_exposes_platform_semantic_tag_applicability_summary(
    client: TestClient,
    sessions: sessionmaker[Session],
) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body()).json()
    field_keys = (
        "space", "object", "style", "material", "structural_features",
        "architectural_element", "soft_decoration", "hard_decoration", "color", "title",
    )
    definition = {
        "schema_version": "tag-demand-contract-v1",
        "semantic_schema": {
            "schema_version": "semantic-tag-schema-v1",
            "fields": {
                key: {
                    "field_key": key,
                    "cardinality": "multi" if key == "object" else "single",
                    "localized": True,
                    "vocabulary_owner": "semantic-owner",
                    "max_values": 10 if key == "object" else 1,
                    "default_value": [],
                }
                for key in field_keys
            },
        },
        "category_applicability": {
            "inspiration_image": {
                key: "required" if key == "style" else "optional"
                for key in field_keys
            }
        },
        "execution_variants": [{
            "site_scope": "domestic",
            "asset_scope": "whole",
            "locale": "zh",
            "category_key": "inspiration_image",
            "prompt_variant": "whole",
            "prompt_version": "prompt-v1",
            "model_version": "model-v1",
        }],
        "quality_gates": {
            "style": {
                "min_precision": 0.8,
                "min_recall": 0.7,
                "min_mapping_coverage": 0.9,
                "max_conflict_rate": 0.1,
            }
        },
        "projection_targets": [{
            "target_key": "domestic_material_tags",
            "mode": "dry_run",
            "locale": "zh",
        }],
    }
    with sessions() as db:
        unrelated = json.loads(json.dumps(definition))
        unrelated["category_applicability"]["inspiration_image"]["style"] = "optional"
        db.add_all([
            TagDemandContract(
                contract_key="semantic-platform",
                version=1,
                status="active",
                definition_json=canonical_json(definition),
                contract_hash="f" * 64,
                approved_by="test-owner",
                created_by="test-owner",
            ),
            TagDemandContract(
                contract_key="unrelated-semantic-contract",
                version=999,
                status="active",
                definition_json=canonical_json(unrelated),
                contract_hash="e" * 64,
                approved_by="other-owner",
                created_by="other-owner",
            ),
        ])
        db.commit()
    detail = client.get(f"{_BASE}/inspiration_image")
    assert detail.status_code == 200, detail.text
    summary = detail.json()["semantic_tag_applicability"]
    assert summary["contract_version"] == 1
    assert summary["field_counts"]["required"] == 1
    assert summary["fields"]["style"] == "required"


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
        "version": "v1",
        "capabilities": [
            "structured_editor",
            "candidate_validation",
            "candidate_execution",
            "workflow_incremental",
            "workflow_stock",
        ],
        "editor_route": "proposal-text",
        "read_only_fallback": False,
        "can_execute": True,
    }
    assert body["dimension_deduction_rules"] == {}
    assert body["media_penalty_enabled"] is False

    validated = client.post(
        f"{_BASE}/proposal_text_pdf/validate", json=_proposal_body()
    )
    assert validated.status_code == 200, validated.text
    assert validated.json() == {"ok": True, "errors": []}


def test_proposal_candidate_round_trip_preserves_unknown_json_and_runtime(
    client: TestClient,
) -> None:
    created = client.post(f"{_BASE}/", json=_proposal_body())
    assert created.status_code == 201, created.text
    runtime = created.json()
    candidate_body = deepcopy(_proposal_body())
    candidate_body["contract"]["spec_version"] = (
        "proposal-text-v3-owner-edit-20260812"
    )
    candidate_body["contract"]["call_a_version"] = (
        "proposal-text-a-v3-owner-edit-20260812"
    )
    candidate_body["contract"]["call_b_version"] = (
        "proposal-text-b-v3-owner-edit-20260812"
    )
    candidate_body["contract"]["extension"] = {
        "keep": ["x", {"nested": True}],
        "owner_note": "未知扩展字段必须无损保留",
    }
    candidate_body.update(
        {
            "parent_revision_id": runtime["projected_revision_id"],
            "expected_projected_revision": runtime["revision"],
            "expected_projected_contract_hash": runtime["contract_hash"],
        }
    )

    candidate_response = client.post(
        f"{_BASE}/proposal_text_pdf/revisions",
        json=candidate_body,
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    reopened = client.get(
        f"{_BASE}/proposal_text_pdf/revisions/{candidate['revision']}"
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["contract"]["extension"] == candidate_body["contract"][
        "extension"
    ]

    unchanged = client.get(f"{_BASE}/proposal_text_pdf")
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["revision"] == runtime["revision"]
    assert unchanged.json()["contract_hash"] == runtime["contract_hash"]
    assert "extension" not in unchanged.json()["contract"]


def test_unknown_explicit_profile_is_readable_but_validation_is_fail_closed(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    with sessions() as db:
        from app.category_evaluation_contract import canonical_contract_hash
        from app.category_evaluation_v3_revisions import ensure_projected_revision
        from app.dimension_schema_registry import canonical_json
        from app.models import CategoryEvaluationV3Config

        contract = {"profile_type": "future-3d-v1", "category_key": "future_3d"}
        row = CategoryEvaluationV3Config(
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
        db.add(row)
        db.flush()
        ensure_projected_revision(db, row)
        db.commit()

    fetched = client.get(f"{_BASE}/future_3d")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["mechanism_profile"] == {
        "profile_type": "future-3d-v1",
        "source": "explicit",
        "supported": False,
        "editable": False,
        "reason": "未注册机制 profile：future-3d-v1",
        "version": "v1",
        "capabilities": [],
        "editor_route": None,
        "read_only_fallback": True,
        "can_execute": False,
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


def test_level_scale_get_remains_available_but_put_is_immutable(
    client: TestClient,
) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body()).json()

    current = client.get(f"{_BASE}/inspiration_image/level-scale")
    assert current.status_code == 200, current.text
    current_json = current.json()
    assert current_json["revision"] == 1
    assert current_json["contract_hash"] == created["contract_hash"]
    assert current_json["level_scale"] is None
    assert current_json["level_thresholds"][-1] == {"level": "L4", "min_score": 0}

    rejected = client.put(
        f"{_BASE}/inspiration_image/level-scale",
        json={
            "expected_revision": 1,
            "expected_contract_hash": created["contract_hash"],
            "level_scale": _five_level_scale(),
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == {
        "code": "active_projection_immutable",
        "message": "现役合同只能通过已批准机制发布原子切换，请先创建候选版本",
    }
    unchanged = client.get(f"{_BASE}/inspiration_image").json()
    assert unchanged["revision"] == 1
    assert unchanged["contract_hash"] == created["contract_hash"]


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


def test_candidate_activation_requires_admin_and_uses_stable_route(
    sessions: sessionmaker[Session],
) -> None:
    with _make_activation_client(sessions, admin=False) as non_admin:
        denied = non_admin.post(
            f"{_BASE}/inspiration_image/revisions/2/activate",
            json={
                "regression_run_id": 1,
                "expected_projected_revision": 1,
                "expected_projected_contract_hash": "a" * 64,
                "note": "人工确认",
            },
        )
        assert denied.status_code == 403

    with _make_activation_client(sessions) as admin:
        missing = admin.post(
            f"{_BASE}/inspiration_image/revisions/2/activate",
            json={
                "regression_run_id": 1,
                "expected_projected_revision": 1,
                "expected_projected_contract_hash": "a" * 64,
                "note": "人工确认",
            },
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "v3_config_not_found"


# --------------------------------------------------------------------------- #
# 5. legacy active mutations are closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("inspiration_image", _valid_body()),
        ("inspiration_image/status", {"status": "retired"}),
    ],
)
def test_legacy_active_mutations_return_immutable_conflict(
    client: TestClient,
    path: str,
    payload: dict[str, Any],
) -> None:
    created = client.post(f"{_BASE}/", json=_valid_body()).json()
    response = client.put(f"{_BASE}/{path}", json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "active_projection_immutable",
        "message": "现役合同只能通过已批准机制发布原子切换，请先创建候选版本",
    }
    unchanged = client.get(f"{_BASE}/inspiration_image").json()
    assert unchanged["status"] == "draft"
    assert unchanged["revision"] == 1
    assert unchanged["contract_hash"] == created["contract_hash"]


# --------------------------------------------------------------------------- #
# 6. unauthenticated → 401
# --------------------------------------------------------------------------- #


def test_unauthenticated_requests_rejected(
    sessions: sessionmaker[Session],
) -> None:
    with _make_client(sessions, authenticated=False) as client:
        assert client.get(f"{_BASE}/").status_code == 401
        assert client.post(f"{_BASE}/", json=_valid_body()).status_code == 401
