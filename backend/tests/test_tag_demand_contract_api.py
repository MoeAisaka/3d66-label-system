from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import AuditEvent, TagDemandContract, User
from app.migrations import run_migrations


def _definition() -> dict[str, object]:
    fields = {
        key: {
            "field_key": key,
            "cardinality": "multi" if key == "object" else "single",
            "localized": True,
            "vocabulary_owner": "semantic-owner",
            "max_values": 10 if key == "object" else 1,
            "default_value": [],
        }
        for key in (
            "space", "object", "style", "material", "structural_features",
            "architectural_element", "soft_decoration", "hard_decoration", "color", "title",
        )
    }
    return {
        "schema_version": "tag-demand-contract-v1",
        "semantic_schema": {"schema_version": "semantic-tag-schema-v1", "fields": fields},
        "category_applicability": {
            "model_3d_su": {key: "required" if key in {"space", "object", "style"} else "optional" for key in fields}
        },
        "execution_variants": [{
            "site_scope": "domestic",
            "asset_scope": "whole",
            "locale": "zh",
            "category_key": "model_3d_su",
            "prompt_variant": "whole",
            "prompt_version": "prompt-v1",
            "model_version": "model-v1",
        }],
        "quality_gates": {"style": {"min_precision": 0.8, "min_recall": 0.7, "min_mapping_coverage": 0.9, "max_conflict_rate": 0.1}},
        "projection_targets": [{"target_key": "domestic_material_tags", "mode": "dry_run", "locale": "zh"}],
    }


def _request(*, status: str = "draft") -> dict[str, object]:
    return {"contract_key": "semantic-platform", "definition": _definition(), "status": status}


@contextmanager
def _context() -> Iterator[dict[str, object]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        admin = User(username="contract-admin", password_hash="unused", display_name="合同管理员", is_admin=True, role="admin")
        manager = User(username="contract-manager", password_hash="unused", display_name="合同经理", is_admin=False, role="manager")
        db.add_all([admin, manager])
        db.commit()

    def override_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield {"client": TestClient(app), "sessions": sessions, "admin": admin, "manager": manager}
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _as_user(user: User):
    return lambda: user


def test_create_contract_appends_version_without_overwriting_active() -> None:
    with _context() as fixture:
        client = fixture["client"]
        app.dependency_overrides[current_user] = _as_user(fixture["admin"])
        first = client.post("/api/tag-demand-contracts", json=_request()).json()
        second = client.post("/api/tag-demand-contracts", json=_request()).json()
        assert (first["version"], second["version"]) == (1, 2)
        assert first["contract_hash"]
        assert first["status"] == "draft"


def test_activation_requires_admin() -> None:
    with _context() as fixture:
        client = fixture["client"]
        app.dependency_overrides[current_user] = _as_user(fixture["manager"])
        response = client.post("/api/tag-demand-contracts", json=_request())
        assert response.status_code == 403


def test_activation_requires_candidate_and_signoff_fields() -> None:
    with _context() as fixture:
        client = fixture["client"]
        app.dependency_overrides[current_user] = _as_user(fixture["admin"])
        draft = client.post("/api/tag-demand-contracts", json=_request()).json()
        response = client.post(f"/api/tag-demand-contracts/{draft['id']}/activate")
        assert response.status_code == 409

        candidate = client.post("/api/tag-demand-contracts", json=_request(status="candidate")).json()
        activated = client.post(f"/api/tag-demand-contracts/{candidate['id']}/activate")
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "active"
        assert activated.json()["approved_by"] == "contract-admin"

        with fixture["sessions"]() as db:
            audit = db.scalar(select(AuditEvent).where(AuditEvent.subject_type == "tag_demand_contract"))
            assert audit is not None
            assert db.scalar(select(TagDemandContract).where(TagDemandContract.id == candidate["id"])).status == "active"


def test_activation_retires_previous_active_without_side_effects() -> None:
    with _context() as fixture:
        client = fixture["client"]
        app.dependency_overrides[current_user] = _as_user(fixture["admin"])
        first = client.post("/api/tag-demand-contracts", json=_request(status="candidate")).json()
        assert client.post(f"/api/tag-demand-contracts/{first['id']}/activate").status_code == 200
        second = client.post("/api/tag-demand-contracts", json=_request(status="candidate")).json()
        activated = client.post(f"/api/tag-demand-contracts/{second['id']}/activate")
        assert activated.status_code == 200
        with fixture["sessions"]() as db:
            statuses = db.scalars(select(TagDemandContract.status).order_by(TagDemandContract.version)).all()
            assert statuses == ["retired", "active"]
        assert client.get("/api/label-releases").json()["items"] == []


def test_contract_detail_never_returns_secrets_or_raw_payloads() -> None:
    with _context() as fixture:
        client = fixture["client"]
        app.dependency_overrides[current_user] = _as_user(fixture["admin"])
        created = client.post("/api/tag-demand-contracts", json=_request()).json()
        detail = client.get(f"/api/tag-demand-contracts/{created['id']}")
        assert detail.status_code == 200
        serialized = json.dumps(detail.json(), ensure_ascii=False)
        assert "api_key" not in serialized
        assert "raw_response" not in serialized
