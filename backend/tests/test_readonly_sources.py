from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.category_evaluation_contract import canonical_contract_hash
from app.database import Base, get_db
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    AssetVersion,
    CategoryEvaluationV3Config,
    ContentIngressEvent,
    MaterialPackage,
    UpstreamReadRun,
    UpstreamSourceContract,
    User,
)
from app.readonly_sources import (
    FixtureReadOnlySourceAdapter,
    ReadOnlySourceError,
    SqlReadOnlySourceAdapter,
    SourceRow,
    create_upstream_source_contract,
    poll_upstream_source,
)


_SCHEMA_FINGERPRINT = "f" * 64


def _contract_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "contract_key": "3d-fixture-source",
        "adapter_key": "fixture-readonly",
        "source_system": "fixture-3d",
        "category_key": "space_image",
        "connection_locator": "source-registry:fixture-3d",
        "secret_reference": "secret-ref:fixture-3d-readonly",
        "field_mappings": {
            "content_id": "content_id",
            "source_version": "source_version",
            "category_key": "category_key",
            "occurred_at": "occurred_at",
            "asset_id": "asset_id",
        },
        "cursor_definition": {"fields": ["content_id", "source_version"]},
        "page_size": 100,
        "read_only": True,
        "schema_fingerprint": _SCHEMA_FINGERPRINT,
        "owner": "tpeng-3d",
        "status": "active",
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
    with engine.begin() as connection:
        run_migrations(connection)
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
def source_contract(db: Session) -> UpstreamSourceContract:
    contract = create_upstream_source_contract(db, **_contract_args())
    db.commit()
    db.refresh(contract)
    return contract


def test_poll_blocks_adapter_without_read_only_evidence(
    db: Session,
    source_contract: UpstreamSourceContract,
) -> None:
    adapter = FixtureReadOnlySourceAdapter(read_only=False, rows=[])
    run = poll_upstream_source(
        db,
        contract=source_contract,
        adapter=adapter,
        limit=10,
        actor="admin",
    )
    db.commit()

    assert run.status == "blocked"
    assert run.error_code == "SOURCE_NOT_READ_ONLY"
    assert json.loads(run.next_cursor_json) == {}
    assert adapter.fetch_count == 0


def test_source_page_limit_is_capped_before_adapter_call(
    db: Session,
    source_contract: UpstreamSourceContract,
) -> None:
    adapter = FixtureReadOnlySourceAdapter(read_only=True, rows=[])
    with pytest.raises(ReadOnlySourceError, match="500"):
        poll_upstream_source(
            db,
            contract=source_contract,
            adapter=adapter,
            limit=501,
            actor="admin",
        )
    assert adapter.verify_count == 0
    assert adapter.fetch_count == 0


def test_schema_drift_blocks_before_ingestion(
    db: Session,
    source_contract: UpstreamSourceContract,
) -> None:
    adapter = FixtureReadOnlySourceAdapter(
        read_only=True,
        rows=[],
        schema_fingerprint="0" * 64,
    )
    run = poll_upstream_source(
        db,
        contract=source_contract,
        adapter=adapter,
        limit=10,
        actor="admin",
    )
    db.commit()

    assert run.status == "blocked"
    assert run.error_code == "SOURCE_SCHEMA_DRIFT"
    assert db.scalar(select(func.count(ContentIngressEvent.id))) == 0


def test_sql_adapter_rechecks_read_only_on_fetch_connection() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE source_assets (
                content_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                category_key TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO source_assets (
                content_id, source_version, category_key, occurred_at
            ) VALUES ('1001', 'v1', 'space_image', '2026-08-14T00:00:00+00:00')
            """
        )

    connection_count = 0

    @contextmanager
    def connection_factory() -> Iterator[object]:
        nonlocal connection_count
        connection_count += 1
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "PRAGMA query_only = ON"
                if connection_count == 1
                else "PRAGMA query_only = OFF"
            )
            yield connection

    adapter = SqlReadOnlySourceAdapter(
        connection_factory=connection_factory,
        table_name="source_assets",
        field_mappings={
            "content_id": "content_id",
            "source_version": "source_version",
            "category_key": "category_key",
            "occurred_at": "occurred_at",
        },
        schema_fingerprint=_SCHEMA_FINGERPRINT,
    )
    try:
        assert adapter.verify_read_only().read_only is True
        with pytest.raises(ReadOnlySourceError, match="只读"):
            adapter.fetch_page(cursor=None, limit=10)
    finally:
        engine.dispose()


def _activate_incremental_route(db: Session) -> Asset:
    asset = Asset(
        original_name="source-3d.png",
        stored_name="source-3d.png",
        mime_type="image/png",
        size_bytes=256,
        sha256="a" * 64,
        category_key="space_image",
    )
    contract = build_inspiration_v3_contract()
    contract["category_key"] = "space_image"
    db.add_all(
        [
            asset,
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
            ),
        ]
    )
    db.commit()
    db.refresh(asset)
    return asset


def test_replaying_source_page_reuses_event_asset_version_and_package(
    db: Session,
    source_contract: UpstreamSourceContract,
) -> None:
    asset = _activate_incremental_route(db)
    row = SourceRow(
        content_id="1001",
        source_version="v7",
        category_key="space_image",
        occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        asset_id=asset.id,
    )
    adapter = FixtureReadOnlySourceAdapter(
        read_only=True,
        rows=[row],
        schema_fingerprint=_SCHEMA_FINGERPRINT,
    )

    first = poll_upstream_source(
        db,
        contract=source_contract,
        adapter=adapter,
        limit=10,
        actor="admin",
    )
    second = poll_upstream_source(
        db,
        contract=source_contract,
        adapter=adapter,
        limit=10,
        actor="admin",
    )
    db.commit()

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert first.package_count == 1
    assert second.duplicate_count == 1
    assert db.scalar(select(func.count(ContentIngressEvent.id))) == 1
    assert db.scalar(select(func.count(AssetVersion.id))) == 1
    assert db.scalar(select(func.count(MaterialPackage.id))) == 1
    assert db.scalar(select(func.count(UpstreamReadRun.id))) == 2


def test_invalid_row_rolls_back_the_entire_source_page(
    db: Session,
    source_contract: UpstreamSourceContract,
) -> None:
    asset = _activate_incremental_route(db)
    occurred_at = datetime(2026, 8, 14, tzinfo=timezone.utc)
    adapter = FixtureReadOnlySourceAdapter(
        read_only=True,
        schema_fingerprint=_SCHEMA_FINGERPRINT,
        rows=[
            SourceRow(
                content_id="1001",
                source_version="v1",
                category_key="space_image",
                occurred_at=occurred_at,
                asset_id=asset.id,
            ),
            SourceRow(
                content_id="1002",
                source_version="v1",
                category_key="three_d",
                occurred_at=occurred_at,
                asset_id=asset.id,
            ),
        ],
    )

    run = poll_upstream_source(
        db,
        contract=source_contract,
        adapter=adapter,
        limit=10,
        actor="admin",
    )
    db.commit()

    assert run.status == "blocked"
    assert run.error_code == "SOURCE_CATEGORY_MISMATCH"
    assert db.scalar(select(func.count(ContentIngressEvent.id))) == 0
    assert db.scalar(select(func.count(AssetVersion.id))) == 0
    assert db.scalar(select(func.count(MaterialPackage.id))) == 0


@contextmanager
def _client_context(
    sessions: sessionmaker[Session],
    *,
    role: str,
    is_admin: bool = False,
) -> Iterator[TestClient]:
    user = User(
        username=f"{role}-source-user",
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


def test_source_contract_api_is_admin_written_and_never_exposes_secret_value(
    sessions: sessionmaker[Session],
) -> None:
    with _client_context(sessions, role="admin", is_admin=True) as client:
        created = client.post("/api/upstream-source-contracts", json=_contract_args())
        assert created.status_code == 200, created.text
        assert created.json()["version"] == 1
        assert created.json()["secret_reference"] == "secret-ref:fixture-3d-readonly"
        assert created.json()["secret_status"] == "unresolved"

    with _client_context(sessions, role="viewer") as client:
        denied = client.post("/api/upstream-source-contracts", json=_contract_args())
        listed = client.get("/api/upstream-source-contracts")
        runs = client.get("/api/upstream-read-runs")
        assert denied.status_code == 403
        assert listed.status_code == 200
        assert runs.status_code == 200
        serialized = listed.text.lower()
        assert "postgres://" not in serialized
        assert "password=" not in serialized
        assert "secret_value" not in serialized
