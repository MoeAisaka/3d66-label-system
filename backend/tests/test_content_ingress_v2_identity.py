from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.model_3d_su_category_seed import seed_model_3d_su
from app.models import Asset, ModelConfig, User


def _v2_payload(
    *,
    event_id: str,
    asset_id: int,
    res_type: int = 1,
    ll_id: str = "12345",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "schema_version": "content-ingress-v2",
        "event_type": "content.created",
        "source_system": "aliyun_3d66_dw",
        "occurred_at": datetime(2026, 8, 18, tzinfo=timezone.utc).isoformat(),
        "payload": {
            "content_id": f"{res_type}:{ll_id}",
            "content_version": "2026-08-18",
            "category_key": "model_3d_su",
            "asset_id": asset_id,
            "res_type": res_type,
            "ll_id": ll_id,
            "res_id": f"res-{ll_id}",
        },
    }


def _verification_payload(*, result: str = "verified") -> dict[str, object]:
    return {
        "contract_key": "semantic-platform",
        "source_system": "aliyun_3d66_dw",
        "key_fields": ["res_type", "ll_id"],
        "result": result,
        "probe_hash": "a" * 64,
        "data_window": "2026-08-01/2026-08-15",
        "scoped_row_count": 100,
        "duplicate_key_count": 0 if result == "verified" else 1,
        "res_id_conflict_count": 0,
    }


def _create_v2_contract(client: TestClient) -> dict[str, object]:
    from tests.test_semantic_tag_contracts import valid_contract_v2

    response = client.post(
        "/api/tag-demand-contracts",
        json={
            "contract_key": "semantic-platform",
            "definition": valid_contract_v2(),
            "status": "draft",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve_identity(client: TestClient) -> dict[str, object]:
    _create_v2_contract(client)
    created = client.post(
        "/api/source-identity-verifications",
        json=_verification_payload(),
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/api/source-identity-verifications/{created.json()['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def _fixture_model_3d_su() -> tuple[object, Session, TestClient, Asset]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine, expire_on_commit=False)
    admin = User(
        username="identity-admin",
        password_hash="unused",
        display_name="身份管理员",
        is_admin=True,
        role="admin",
    )
    db.add_all([admin, ModelConfig(active=True)])
    db.commit()
    seed_model_3d_su(db, main.settings)
    asset = Asset(
        original_name="model-3d-su.jpg",
        stored_name="model-3d-su.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="b" * 64,
        category_key="model_3d_su",
    )
    db.add(asset)
    db.commit()
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: admin
    return engine, db, TestClient(app), asset


def _headers(monkeypatch) -> dict[str, str]:
    token = "test-content-ingress-v2-token"
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, content_ingress_token=token),
    )
    return {"Authorization": f"Bearer {token}"}


def _close(engine: object, db: Session) -> None:
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_v2_event_without_approved_identity_is_stored_but_not_packaged(
    monkeypatch,
) -> None:
    engine, db, client, asset = _fixture_model_3d_su()
    try:
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_v2_payload(event_id="evt-pending", asset_id=asset.id),
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"]["identity_status"] == "pending_verification"
        assert response.json()["content"]["content_key"] is None
        assert response.json()["routing_status"] == "blocked_identity"
        assert response.json()["material_package_id"] is None
    finally:
        _close(engine, db)


def test_admin_approval_allows_new_v2_event_to_package(monkeypatch) -> None:
    engine, db, client, asset = _fixture_model_3d_su()
    try:
        _approve_identity(client)
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_v2_payload(event_id="evt-verified", asset_id=asset.id),
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"]["content_key"] == (
            "aliyun_3d66_dw:1:12345"
        )
        assert response.json()["routing_status"] == "packaged"
    finally:
        _close(engine, db)


def test_conflict_probe_cannot_be_approved_as_verified() -> None:
    engine, db, client, _asset = _fixture_model_3d_su()
    try:
        _create_v2_contract(client)
        created = client.post(
            "/api/source-identity-verifications",
            json=_verification_payload(result="conflict"),
        )
        assert created.status_code == 201, created.text
        response = client.post(
            f"/api/source-identity-verifications/{created.json()['id']}/approve"
        )
        assert response.status_code == 409
    finally:
        _close(engine, db)


def test_same_v2_event_id_with_changed_identity_returns_409(monkeypatch) -> None:
    engine, db, client, asset = _fixture_model_3d_su()
    headers = _headers(monkeypatch)
    try:
        _approve_identity(client)
        first_payload = _v2_payload(
            event_id="evt-identity-drift",
            asset_id=asset.id,
            res_type=1,
            ll_id="12345",
        )
        second_payload = _v2_payload(
            event_id="evt-identity-drift",
            asset_id=asset.id,
            res_type=1,
            ll_id="99999",
        )
        first = client.post(
            "/api/content-ingress/events", headers=headers, json=first_payload
        )
        second = client.post(
            "/api/content-ingress/events", headers=headers, json=second_payload
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "INGRESS_EVENT_CONFLICT"
    finally:
        _close(engine, db)
