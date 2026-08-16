from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.field_demand_contracts import (
    FieldDemandContractError,
    create_field_demand_contract,
    record_asset_version,
)
from app.main import app, current_user
from app.models import Asset, AssetVersion, FieldDemandContract, User


def _fields(*, source_path: str = "semantic.style") -> list[dict[str, object]]:
    return [
        {
            "field_key": "style",
            "source_path": source_path,
            "required": True,
            "data_type": "string",
            "target_roles": ["unified_dimension", "search_labels"],
        }
    ]


def _contract_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "contract_key": "3d-search",
        "category_key": "model_3d_su",
        "consumer_key": "search",
        "owner": "tpeng-search",
        "fields": _fields(),
        "thresholds": {"accuracy": 0.9, "recall": 0.9},
        "status": "draft",
        "created_by": "admin",
    }
    args.update(overrides)
    return args


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def db(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as session:
        yield session


@pytest.fixture
def asset(db: Session) -> Asset:
    row = Asset(
        original_name="model.png",
        stored_name="model.png",
        mime_type="image/png",
        size_bytes=128,
        width=32,
        height=32,
        sha256="a" * 64,
        category_key="model_3d_su",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_field_contract_rejects_candidate_path(db: Session) -> None:
    with pytest.raises(FieldDemandContractError, match="Canonical"):
        create_field_demand_contract(
            db,
            **_contract_args(fields=_fields(source_path="candidate.style")),
        )


def test_field_contract_is_idempotent_and_changed_definition_increments_version(
    db: Session,
) -> None:
    first = create_field_demand_contract(db, **_contract_args())
    duplicate = create_field_demand_contract(db, **_contract_args())
    changed = create_field_demand_contract(
        db,
        **_contract_args(thresholds={"accuracy": 0.95, "recall": 0.9}),
    )
    db.commit()

    assert duplicate.id == first.id
    assert duplicate.contract_hash == first.contract_hash
    assert first.version == 1
    assert changed.version == 2
    assert changed.contract_hash != first.contract_hash
    assert [item.version for item in db.scalars(
        select(FieldDemandContract).order_by(FieldDemandContract.version.desc())
    ).all()] == [2, 1]


def test_field_contract_rejects_duplicate_keys_and_invalid_thresholds(db: Session) -> None:
    duplicate_fields = _fields() + [
        {
            "field_key": "style",
            "source_path": "quality.style_confidence",
            "required": False,
            "data_type": "number",
        }
    ]
    with pytest.raises(FieldDemandContractError, match="重复"):
        create_field_demand_contract(db, **_contract_args(fields=duplicate_fields))
    with pytest.raises(FieldDemandContractError, match="0 到 1"):
        create_field_demand_contract(
            db,
            **_contract_args(thresholds={"accuracy": 1.1, "recall": 0.9}),
        )


def test_asset_version_reuses_identical_source_version(
    db: Session,
    asset: Asset,
) -> None:
    args = {
        "source_system": "fixture-3d",
        "source_content_id": "1001",
        "source_version": "v7",
        "asset": asset,
        "occurred_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
    }
    first, first_duplicate = record_asset_version(db, **args)
    second, second_duplicate = record_asset_version(db, **args)
    db.commit()

    assert first_duplicate is False
    assert second_duplicate is True
    assert second.id == first.id
    assert second.payload_hash == first.payload_hash


def test_asset_version_rejects_conflicting_payload_for_same_source_version(
    db: Session,
    asset: Asset,
) -> None:
    occurred_at = datetime(2026, 8, 14, tzinfo=timezone.utc)
    record_asset_version(
        db,
        source_system="fixture-3d",
        source_content_id="1001",
        source_version="v7",
        asset=asset,
        occurred_at=occurred_at,
    )
    with pytest.raises(FieldDemandContractError, match="ASSET_VERSION_CONFLICT"):
        record_asset_version(
            db,
            source_system="fixture-3d",
            source_content_id="1001",
            source_version="v7",
            asset=asset,
            occurred_at=occurred_at + timedelta(seconds=1),
        )


@contextmanager
def _client_context(
    sessions: sessionmaker[Session],
    *,
    role: str,
    is_admin: bool = False,
) -> Iterator[TestClient]:
    user = User(
        username=f"{role}-user",
        password_hash="unused",
        display_name=role,
        is_admin=is_admin,
        role=role,
    )

    def override_db() -> Iterator[Session]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_field_contract_api_enforces_admin_write_and_redacted_read(
    sessions: sessionmaker[Session],
) -> None:
    with _client_context(sessions, role="admin", is_admin=True) as client:
        first = client.post("/api/field-demand-contracts", json=_contract_args())
        second_payload = _contract_args(
            thresholds={"accuracy": 0.95, "recall": 0.9}
        )
        second = client.post("/api/field-demand-contracts", json=second_payload)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert [first.json()["version"], second.json()["version"]] == [1, 2]

    with _client_context(sessions, role="viewer") as client:
        denied = client.post("/api/field-demand-contracts", json=_contract_args())
        listed = client.get("/api/field-demand-contracts")
        assert denied.status_code == 403
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert [item["version"] for item in body["items"]] == [2, 1]
        assert body["items"][0]["fields"] == _fields()
        serialized = listed.text.lower()
        assert "password" not in serialized
        assert "secret" not in serialized
        assert "token" not in serialized


def test_asset_version_api_lists_newest_first(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        asset = Asset(
            original_name="api-model.png",
            stored_name="api-model.png",
            mime_type="image/png",
            size_bytes=256,
            sha256="b" * 64,
            category_key="model_3d_su",
        )
        db.add(asset)
        db.flush()
        for source_version, offset in (("v1", 0), ("v2", 1)):
            record_asset_version(
                db,
                source_system="fixture-3d",
                source_content_id="1002",
                source_version=source_version,
                asset=asset,
                occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc)
                + timedelta(minutes=offset),
            )
        db.commit()
        asset_id = asset.id

    with _client_context(sessions, role="viewer") as client:
        response = client.get(f"/api/assets/{asset_id}/versions")
        assert response.status_code == 200, response.text
        assert [item["source_version"] for item in response.json()["items"]] == [
            "v2",
            "v1",
        ]
        assert set(response.json()["items"][0]) == {
            "id",
            "source_system",
            "source_content_id",
            "source_version",
            "asset_id",
            "sha256",
            "mime_type",
            "size_bytes",
            "occurred_at",
            "payload_hash",
            "created_at",
        }
