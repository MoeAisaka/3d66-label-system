from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.dimension_route_registry import (
    CORE_DIMENSION_KEYS,
    CORE_SCHEMA_KEY,
    CORE_SCHEMA_VERSION,
    PRODUCT_FAMILY_DIMENSION_KEYS,
    PRODUCT_SCHEMA_KEY,
    PRODUCT_SCHEMA_VERSION,
    ROUTE_POLICY_KEY,
    ROUTE_POLICY_VERSION,
    common_core_definition,
    materialized_p2_dimension_schema_rows,
    product_candidate_definition,
    route_policy_definition,
)
from app.dimension_router import (
    DimensionRouteContractError,
    resolve_dimension_route,
)
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
    canonical_json,
    materialized_space_schema_rows,
)
from app.main import app, current_user
from app.migrations import run_migrations
from app.migrations.runner import MIGRATIONS
from app.models import (
    DimensionRoutePolicy,
    DimensionRoutePolicyContractError,
    DimensionRoutePolicyImmutableError,
    DimensionSchema,
    User,
    utcnow,
)


def _engine(tmp_path, name: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
def engine(tmp_path):
    result = _engine(tmp_path, "dimension-routing.db")
    Base.metadata.create_all(bind=result)
    with result.begin() as connection:
        run_migrations(connection)
    try:
        yield result
    finally:
        result.dispose()


def _frozen_schemas() -> list[dict]:
    rows = [
        row
        for row in materialized_space_schema_rows()
        if row["version"] == ACTIVE_V13_VERSION
    ]
    rows.extend(materialized_p2_dimension_schema_rows())
    return [
        {
            "schema_key": row["schema_key"],
            "version": row["version"],
            "family_key": row["family_key"],
            "status": row["status"],
            "canonical_hash": row["canonical_hash"],
            "definition": json.loads(row["definition_json"]),
        }
        for row in rows
    ]


def _precheck(
    *,
    scope_status: str = "in_scope",
    primary_category: str = "住宅设计",
    primary_confidence: float = 0.92,
    scene_scope: str = "full_space",
    white_background: str = "no",
    quality_severity: str = "normal",
    needs_review: bool = False,
) -> dict:
    return {
        "classification": {
            "scope_status": scope_status,
            "primary_category": primary_category,
            "primary_confidence": primary_confidence,
        },
        "scene_scope": {"type": scene_scope},
        "media_form": {
            "white_background_product": {
                "status": white_background,
            }
        },
        "image_quality": {
            "quality_severity": quality_severity,
        },
        "needs_review": needs_review,
    }


def _resolve(precheck: dict) -> dict:
    return resolve_dimension_route(
        precheck,
        frozen_policy=route_policy_definition(),
        frozen_schemas=_frozen_schemas(),
        execution_context="calibration",
    )


def test_materialized_core_and_product_candidate_contracts(engine) -> None:
    core_definition = common_core_definition()
    product_definition = product_candidate_definition()
    assert [item["key"] for item in core_definition["dimensions"]] == list(
        CORE_DIMENSION_KEYS
    )
    assert all(
        item["weight"] is None
        for item in core_definition["dimensions"]
    )
    assert product_definition["core_schema_ref"] == {
        "schema_key": CORE_SCHEMA_KEY,
        "version": CORE_SCHEMA_VERSION,
        "canonical_hash": canonical_hash(core_definition),
    }
    assert product_definition["family_dimension_keys"] == list(
        PRODUCT_FAMILY_DIMENSION_KEYS
    )
    assert sum(
        float(item["weight"])
        for item in product_definition["dimensions"]
    ) == pytest.approx(1.0)
    assert product_definition["release_gate"] == {
        "minimum_calibration_samples": 50,
        "target_calibration_samples": 100,
        "required_sample_roles": [
            "target_error",
            "stable_control",
            "blind_holdout",
        ],
        "completed_calibration_samples": 0,
        "status": "not_started",
        "publishing_blocked": True,
        "blocked_reasons": [
            "manual_calibration_incomplete",
            "prompt_contract_missing",
        ],
    }
    assert product_definition["prompt_contract"]["publishing_blocked"] is True

    with Session(engine) as db:
        core = db.scalar(
            select(DimensionSchema).where(
                DimensionSchema.schema_key == CORE_SCHEMA_KEY,
                DimensionSchema.version == CORE_SCHEMA_VERSION,
            )
        )
        product = db.scalar(
            select(DimensionSchema).where(
                DimensionSchema.schema_key == PRODUCT_SCHEMA_KEY,
                DimensionSchema.version == PRODUCT_SCHEMA_VERSION,
            )
        )
        policy = db.scalar(
            select(DimensionRoutePolicy).where(
                DimensionRoutePolicy.policy_key == ROUTE_POLICY_KEY,
                DimensionRoutePolicy.version == ROUTE_POLICY_VERSION,
            )
        )
        assert core is not None
        assert product is not None
        assert policy is not None
        assert core.status == "published"
        assert product.status == "candidate"
        assert product.core_schema_id == core.id
        assert policy.status == "candidate"
        assert policy.canonical_hash == canonical_hash(
            json.loads(policy.definition_json)
        )


@pytest.mark.parametrize(
    ("precheck", "status", "family", "reason", "schema_key"),
    [
        (
            _precheck(),
            "resolved",
            "space",
            "controlled_space_category",
            SPACE_SCHEMA_KEY,
        ),
        (
            _precheck(
                scope_status="boundary",
                primary_category="软装家具",
                scene_scope="object_only",
            ),
            "resolved",
            "product",
            "controlled_product_category",
            PRODUCT_SCHEMA_KEY,
        ),
        (
            _precheck(
                scope_status="out_of_scope",
                primary_category="无法确定",
                scene_scope="object_only",
                white_background="yes",
            ),
            "resolved",
            "product",
            "product_media_signal",
            PRODUCT_SCHEMA_KEY,
        ),
        (
            _precheck(
                scope_status="out_of_scope",
                primary_category="平面设计",
                scene_scope="uncertain",
            ),
            "core_fallback",
            "graphic",
            "graphic_pack_not_ready",
            CORE_SCHEMA_KEY,
        ),
        (
            _precheck(
                scope_status="out_of_scope",
                primary_category="意向图",
                scene_scope="uncertain",
            ),
            "core_fallback",
            "intent",
            "intent_pack_not_ready",
            CORE_SCHEMA_KEY,
        ),
        (
            _precheck(
                scope_status="out_of_scope",
                primary_category="无法确定",
                scene_scope="uncertain",
            ),
            "core_fallback",
            "common",
            "unknown_family_core_fallback",
            CORE_SCHEMA_KEY,
        ),
        (
            _precheck(quality_severity="unusable"),
            "unassessable",
            "common",
            "image_unusable",
            None,
        ),
        (
            _precheck(white_background="yes"),
            "core_fallback",
            "common",
            "space_product_signal_conflict",
            CORE_SCHEMA_KEY,
        ),
    ],
)
def test_deterministic_route_cases(
    precheck: dict,
    status: str,
    family: str,
    reason: str,
    schema_key: str | None,
) -> None:
    decision = _resolve(precheck)
    assert decision["status"] == status
    assert decision["family_key"] == family
    assert decision["route_reason"] == reason
    assert (
        decision["dimension_schema"]["schema_key"]
        if decision["dimension_schema"] is not None
        else None
    ) == schema_key
    if status in {"core_fallback", "unassessable"}:
        assert decision["needs_review"] is True


def test_unknown_input_never_becomes_silent_no_result() -> None:
    decision = _resolve({"classification": {}})
    assert decision["status"] == "core_fallback"
    assert decision["family_key"] == "common"
    assert decision["unassessable_reason"] is None
    assert decision["needs_review"] is True


def test_route_is_repeatable_and_candidate_policy_rejects_production() -> None:
    precheck = _precheck(
        scope_status="boundary",
        primary_category="灯具照明",
        scene_scope="object_only",
    )
    expected = canonical_json(_resolve(precheck))
    assert {
        canonical_json(_resolve(precheck))
        for _ in range(100)
    } == {expected}
    with pytest.raises(
        DimensionRouteContractError,
        match="仅允许用于人工校准",
    ):
        resolve_dimension_route(
            precheck,
            frozen_policy=route_policy_definition(),
            frozen_schemas=_frozen_schemas(),
            execution_context="production",
        )


def test_route_rejects_missing_or_tampered_frozen_schema() -> None:
    frozen = _frozen_schemas()
    with pytest.raises(
        DimensionRouteContractError,
        match="未冻结",
    ):
        resolve_dimension_route(
            _precheck(),
            frozen_policy=route_policy_definition(),
            frozen_schemas=frozen[1:],
            execution_context="calibration",
        )
    tampered = _frozen_schemas()
    tampered[0]["definition"]["compatibility_revision"] = "tampered"
    with pytest.raises(
        DimensionRouteContractError,
        match="规范哈希无效",
    ):
        resolve_dimension_route(
            _precheck(),
            frozen_policy=route_policy_definition(),
            frozen_schemas=tampered,
            execution_context="calibration",
        )


def test_route_policy_hash_guard_and_published_immutability(engine) -> None:
    definition = {
        "format_version": "dimension-route-policy-definition-v1",
        "policy_key": "published-test",
    }
    with Session(engine, expire_on_commit=False) as db:
        invalid = DimensionRoutePolicy(
            policy_key="invalid-hash",
            version="1.0.0",
            display_name="错误哈希",
            status="draft",
            definition_json=canonical_json(definition),
            canonical_hash="0" * 64,
            created_by="test",
        )
        db.add(invalid)
        with pytest.raises(
            DimensionRoutePolicyContractError,
            match="规范哈希",
        ):
            db.commit()
        db.rollback()

        published = DimensionRoutePolicy(
            policy_key="published-test",
            version="1.0.0",
            display_name="已发布测试",
            status="published",
            definition_json=canonical_json(definition),
            canonical_hash=canonical_hash(definition),
            created_by="test",
            published_by="test",
            published_at=utcnow(),
        )
        db.add(published)
        db.commit()
        policy_id = published.id

        published.display_name = "禁止覆盖"
        with pytest.raises(DimensionRoutePolicyImmutableError):
            db.commit()
        db.rollback()

        published = db.get(DimensionRoutePolicy, policy_id)
        assert published is not None
        db.delete(published)
        with pytest.raises(DimensionRoutePolicyImmutableError):
            db.commit()
        db.rollback()

    with pytest.raises(IntegrityError, match="immutable"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE dimension_route_policies "
                "SET display_name='raw-update' WHERE id=?",
                (policy_id,),
            )
    with pytest.raises(IntegrityError, match="cannot be deleted"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM dimension_route_policies WHERE id=?",
                (policy_id,),
            )


def test_migration_28_old_database_smoke(tmp_path) -> None:
    engine = _engine(tmp_path, "dimension-routing-migration.db")
    old_tables = [
        table
        for table in Base.metadata.tables.values()
        if table.name
        not in {
            "dimension_schemas",
            "dimension_route_policies",
        }
    ]
    Base.metadata.create_all(bind=engine, tables=old_tables)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for migration in MIGRATIONS[:25]:
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (migration.version, migration.name),
                )
            run_migrations(connection)
            assert connection.exec_driver_sql(
                "SELECT max(version) FROM schema_migrations"
            ).scalar_one() == len(MIGRATIONS)
            assert connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all() == []
            assert connection.exec_driver_sql(
                "SELECT status FROM dimension_schemas "
                "WHERE schema_key=? AND version=?",
                (CORE_SCHEMA_KEY, CORE_SCHEMA_VERSION),
            ).scalar_one() == "published"
            assert connection.exec_driver_sql(
                "SELECT status FROM dimension_schemas "
                "WHERE schema_key=? AND version=?",
                (PRODUCT_SCHEMA_KEY, PRODUCT_SCHEMA_VERSION),
            ).scalar_one() == "candidate"
            assert connection.exec_driver_sql(
                "SELECT status FROM dimension_route_policies "
                "WHERE policy_key=? AND version=?",
                (ROUTE_POLICY_KEY, ROUTE_POLICY_VERSION),
            ).scalar_one() == "candidate"
    finally:
        engine.dispose()


def test_read_only_dimension_route_policy_api(engine) -> None:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: User(
        id=1,
        username="route-reader",
        password_hash="unused",
    )
    client = TestClient(app)
    try:
        response = client.get(
            "/api/dimension-route-policies",
            params={
                "policy_key": ROUTE_POLICY_KEY,
                "status": "candidate",
            },
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["version"] == ROUTE_POLICY_VERSION
        assert "definition" not in items[0]

        detail = client.get(
            f"/api/dimension-route-policies/{ROUTE_POLICY_KEY}"
            f"/versions/{ROUTE_POLICY_VERSION}"
        )
        assert detail.status_code == 200
        assert (
            detail.json()["definition"]["activation_scope"]
            == "calibration_only"
        )

        missing = client.get(
            f"/api/dimension-route-policies/{ROUTE_POLICY_KEY}"
            "/versions/9.9.9"
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "维度路由策略版本不存在"
    finally:
        app.dependency_overrides.clear()
