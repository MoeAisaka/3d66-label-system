from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.category_evaluation_contract import canonical_contract_hash
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.main import app
from app.migrations import run_migrations
from app.models import (
    Asset,
    AuditEvent,
    CategoryEvaluationV3Config,
    MaterialPackage,
    MaterialPackageItem,
)


def _fixture(*, active_mechanism: bool = True) -> tuple[object, Session, TestClient, Asset]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="asset-1.jpg",
        stored_name="asset-1.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
        category_key="space_image",
    )
    db.add(asset)
    if active_mechanism:
        contract = build_inspiration_v3_contract()
        contract["category_key"] = "space_image"
        db.add(
            CategoryEvaluationV3Config(
                category_key="space_image",
                display_name="空间图",
                status="active",
                contract_json=json.dumps(contract, ensure_ascii=False),
                classification_map_json=json.dumps(
                    build_inspiration_classification_map(), ensure_ascii=False
                ),
                subcategory_dimensions_json=json.dumps(
                    build_inspiration_subcategory_dimensions(), ensure_ascii=False
                ),
                contract_hash=canonical_contract_hash(contract),
                created_by="test",
            )
        )
    db.commit()
    app.dependency_overrides[get_db] = lambda: (yield db)
    return engine, db, TestClient(app), asset


def _payload(*, event_id: str, asset_id: int | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "content_id": "asset-1",
        "content_version": "v1",
        "category_key": "space_image",
    }
    if asset_id is not None:
        payload["asset_id"] = asset_id
    return {
        "event_id": event_id,
        "schema_version": "content-ingress-v1",
        "event_type": "content.created",
        "source_system": "upstream-sim",
        "occurred_at": datetime(2026, 8, 13, tzinfo=timezone.utc).isoformat(),
        "payload": payload,
    }


def _headers(monkeypatch) -> dict[str, str]:
    token = "test-content-ingress-token"
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


def test_content_created_builds_incremental_package(monkeypatch) -> None:
    engine, db, client, asset = _fixture()
    try:
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_payload(event_id="evt-1", asset_id=asset.id),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["workflow_kind"] == "incremental"
        assert body["routing_status"] == "packaged"
        assert body["package_created"] is True
        assert body["writes_evaluation_job"] is False
        package = db.get(MaterialPackage, body["material_package_id"])
        assert package is not None
        assert package.source == "production_import"
        assert package.category_key == "space_image"
        item = db.scalar(
            select(MaterialPackageItem).where(
                MaterialPackageItem.package_id == package.id
            )
        )
        assert item is not None
        assert item.asset_id == asset.id
    finally:
        _close(engine, db)


def test_duplicate_content_event_reuses_incremental_package(monkeypatch) -> None:
    engine, db, client, asset = _fixture()
    headers = _headers(monkeypatch)
    payload = _payload(event_id="evt-duplicate", asset_id=asset.id)
    try:
        first = client.post("/api/content-ingress/events", headers=headers, json=payload)
        second = client.post("/api/content-ingress/events", headers=headers, json=payload)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["duplicate"] is True
        assert second.json()["package_created"] is False
        assert second.json()["material_package_id"] == first.json()["material_package_id"]
        assert db.scalar(select(func.count(MaterialPackage.id))) == 1
        assert db.scalar(select(func.count(MaterialPackageItem.id))) == 1
        assert db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.category == "content_ingress",
                AuditEvent.action == "duplicate_reused",
            )
        ) == 1
    finally:
        _close(engine, db)


def test_content_without_local_asset_waits_without_package(monkeypatch) -> None:
    engine, db, client, _asset = _fixture()
    try:
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_payload(event_id="evt-awaiting", asset_id=None),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["workflow_kind"] == "incremental"
        assert body["routing_status"] == "awaiting_material"
        assert body["material_package_id"] is None
        assert body["package_created"] is False
        assert db.scalar(select(func.count(MaterialPackage.id))) == 0
    finally:
        _close(engine, db)


def test_missing_active_mechanism_blocks_package_creation(monkeypatch) -> None:
    engine, db, client, asset = _fixture(active_mechanism=False)
    try:
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_payload(event_id="evt-profile-blocked", asset_id=asset.id),
        )

        assert response.status_code == 200, response.text
        assert response.json()["routing_status"] == "blocked_profile"
        assert response.json()["material_package_id"] is None
        assert db.scalar(select(func.count(MaterialPackage.id))) == 0
    finally:
        _close(engine, db)


def test_long_event_id_uses_bounded_deterministic_package_key(monkeypatch) -> None:
    engine, db, client, asset = _fixture()
    event_id = "evt-" + ("x" * 150)
    try:
        response = client.post(
            "/api/content-ingress/events",
            headers=_headers(monkeypatch),
            json=_payload(event_id=event_id, asset_id=asset.id),
        )

        assert response.status_code == 200, response.text
        package = db.get(MaterialPackage, response.json()["material_package_id"])
        assert package is not None
        assert len(package.package_key) <= 80
        assert package.package_key.startswith("ingress:")
    finally:
        _close(engine, db)
