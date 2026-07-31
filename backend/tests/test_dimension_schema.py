from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base, get_db
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
    canonical_json,
)
from app.main import app, current_user
from app.migrations import run_migrations
from app.migrations.runner import MIGRATIONS
from app.models import (
    DimensionSchema,
    DimensionSchemaImmutableError,
    User,
)
from app.risk_review import (
    CAP_RANK,
    DIMENSION_KEYS,
    QUALITY_RANK,
    RISK_REVIEW_VERSION,
)
from app.scoring import (
    COMBINED_WEIGHTS,
    ENGINE_VERSION,
    GRADE_POINTS,
    WEIGHTS,
    _level_for_score,
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
    result = _engine(tmp_path, "dimension-schema.db")
    Base.metadata.create_all(bind=result)
    with result.begin() as connection:
        run_migrations(connection)
    try:
        yield result
    finally:
        result.dispose()


def _definition_by_version(db: Session, version: str) -> dict:
    schema = db.scalar(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == SPACE_SCHEMA_KEY,
            DimensionSchema.version == version,
        )
    )
    assert schema is not None
    return json.loads(schema.definition_json)


def _weights(definition: dict) -> dict[str, float]:
    return {
        dimension["key"]: dimension["weight"]
        for dimension in definition["dimensions"]
    }


def _grade_points(definition: dict) -> dict[int, float]:
    return {
        int(grade): points
        for grade, points in definition["aggregation"]["grade_points"].items()
    }


def test_materialized_space_revisions_equal_live_scoring_constants(
    engine,
) -> None:
    with Session(engine) as db:
        schemas = db.scalars(
            select(DimensionSchema)
            .where(DimensionSchema.schema_key == SPACE_SCHEMA_KEY)
            .order_by(DimensionSchema.version)
        ).all()
        assert len(schemas) == 2
        assert {schema.version for schema in schemas} == {
            HISTORICAL_DEFAULT_VERSION,
            ACTIVE_V13_VERSION,
        }
        assert all(schema.status == "published" for schema in schemas)
        assert all(
            schema.canonical_hash
            == canonical_hash(json.loads(schema.definition_json))
            for schema in schemas
        )

        historical = _definition_by_version(db, HISTORICAL_DEFAULT_VERSION)
        active = _definition_by_version(db, ACTIVE_V13_VERSION)

    assert _weights(historical) == WEIGHTS
    assert _weights(active) == COMBINED_WEIGHTS
    assert _grade_points(historical) == GRADE_POINTS
    assert _grade_points(active) == GRADE_POINTS
    assert historical["aggregation"]["engine_version"] == ENGINE_VERSION
    assert active["aggregation"]["engine_version"] == ENGINE_VERSION

    thresholds = active["aggregation"]["level_thresholds"]
    assert thresholds == historical["aggregation"]["level_thresholds"]
    for threshold_key, lower_level, level_at_threshold in (
        ("L2", "L1", "L2"),
        ("L3", "L2", "L3"),
        ("L4", "L3", "L4"),
        ("L5", "L4", "L5"),
    ):
        threshold = thresholds[threshold_key]
        assert _level_for_score(threshold - 0.01) == lower_level
        assert _level_for_score(threshold) == level_at_threshold

    for definition in (historical, active):
        risk_review = definition["risk_review"]
        assert risk_review["version"] == RISK_REVIEW_VERSION
        assert risk_review["dimension_keys"] == list(DIMENSION_KEYS)
        assert risk_review["quality_rank"] == QUALITY_RANK
        assert risk_review["cap_rank"] == CAP_RANK
        assert definition["output_contract"]["dimension_output_keys"] == list(
            DIMENSION_KEYS
        )


def test_dimension_schema_draft_crud_and_constraints(engine) -> None:
    definition = {"format_version": "test-v1", "dimensions": []}
    with Session(engine, expire_on_commit=False) as db:
        schema = DimensionSchema(
            schema_key="test_schema",
            version="0.1.0",
            schema_type="extension",
            family_key="space",
            display_name="测试草稿",
            status="draft",
            definition_json=canonical_json(definition),
            canonical_hash=canonical_hash(definition),
            created_by="test",
        )
        db.add(schema)
        db.commit()
        schema_id = schema.id

        schema.display_name = "测试候选"
        schema.status = "candidate"
        db.commit()
        assert db.get(DimensionSchema, schema_id).display_name == "测试候选"

        db.delete(schema)
        db.commit()
        assert db.get(DimensionSchema, schema_id) is None

        duplicate_definition = {"format_version": "test-v2", "dimensions": []}
        db.add_all(
            [
                DimensionSchema(
                    schema_key="duplicate",
                    version="1.0.0",
                    schema_type="extension",
                    family_key="space",
                    display_name="重复一",
                    definition_json=canonical_json(duplicate_definition),
                    canonical_hash=canonical_hash(duplicate_definition),
                    created_by="test",
                ),
                DimensionSchema(
                    schema_key="duplicate",
                    version="1.0.0",
                    schema_type="extension",
                    family_key="space",
                    display_name="重复二",
                    definition_json=canonical_json(
                        {"format_version": "test-v3", "dimensions": []}
                    ),
                    canonical_hash=canonical_hash(
                        {"format_version": "test-v3", "dimensions": []}
                    ),
                    created_by="test",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_published_schema_rejects_orm_and_database_update_delete(engine) -> None:
    with Session(engine, expire_on_commit=False) as db:
        schema = db.scalar(
            select(DimensionSchema).where(
                DimensionSchema.schema_key == SPACE_SCHEMA_KEY,
                DimensionSchema.version == ACTIVE_V13_VERSION,
            )
        )
        assert schema is not None
        schema_id = schema.id

        schema.display_name = "禁止覆盖"
        with pytest.raises(DimensionSchemaImmutableError):
            db.commit()
        db.rollback()

        schema = db.get(DimensionSchema, schema_id)
        assert schema is not None
        db.delete(schema)
        with pytest.raises(DimensionSchemaImmutableError):
            db.commit()
        db.rollback()

    with pytest.raises(IntegrityError, match="immutable"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE dimension_schemas SET display_name='raw-update' "
                "WHERE id=?",
                (schema_id,),
            )
    with pytest.raises(IntegrityError, match="cannot be deleted"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM dimension_schemas WHERE id=?",
                (schema_id,),
            )


def test_migration_26_foreign_keys_and_true_insert_smoke(tmp_path) -> None:
    engine = _engine(tmp_path, "dimension-schema-migration.db")
    old_tables = [
        table
        for table in Base.metadata.tables.values()
        if table.name != "dimension_schemas"
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
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
            run_migrations(connection)

            assert connection.exec_driver_sql(
                "SELECT max(version) FROM schema_migrations"
            ).scalar_one() == len(MIGRATIONS)
            assert connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all() == []
            foreign_keys = {
                (row[3], row[2])
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_list(dimension_schemas)"
                )
            }
            assert foreign_keys >= {
                ("parent_schema_id", "dimension_schemas"),
                ("core_schema_id", "dimension_schemas"),
                ("source_optimization_run_id", "prompt_optimization_runs"),
            }

            parent_definition = {"format_version": "smoke-parent-v1"}
            child_definition = {"format_version": "smoke-child-v1"}
            connection.exec_driver_sql(
                """
                INSERT INTO dimension_schemas (
                    id, schema_key, version, schema_type, family_key,
                    display_name, status, definition_json, canonical_hash,
                    created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    9001,
                    "smoke_core",
                    "1.0.0",
                    "core",
                    "common",
                    "冒烟核心",
                    "draft",
                    canonical_json(parent_definition),
                    canonical_hash(parent_definition),
                    "test",
                ),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO dimension_schemas (
                    id, schema_key, version, schema_type, family_key,
                    display_name, status, parent_schema_id, core_schema_id,
                    definition_json, canonical_hash, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    9002,
                    "smoke_family",
                    "1.0.0",
                    "family_pack",
                    "space",
                    "冒烟族包",
                    "draft",
                    9001,
                    9001,
                    canonical_json(child_definition),
                    canonical_hash(child_definition),
                    "test",
                ),
            )
            assert connection.exec_driver_sql(
                "SELECT parent_schema_id, core_schema_id "
                "FROM dimension_schemas WHERE id=9002"
            ).one() == (9001, 9001)
            assert connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all() == []
    finally:
        engine.dispose()


def test_read_only_dimension_schema_registry_api(engine) -> None:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: User(
        id=1,
        username="schema-reader",
        password_hash="unused",
    )
    client = TestClient(app)
    try:
        response = client.get(
            "/api/dimension-schemas",
            params={"schema_key": SPACE_SCHEMA_KEY, "status": "published"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 2
        assert {item["version"] for item in items} == {
            HISTORICAL_DEFAULT_VERSION,
            ACTIVE_V13_VERSION,
        }
        assert all("definition" not in item for item in items)

        detail = client.get(
            f"/api/dimension-schemas/{SPACE_SCHEMA_KEY}"
            f"/versions/{ACTIVE_V13_VERSION}"
        )
        assert detail.status_code == 200
        assert detail.json()["definition"]["compatibility_revision"] == (
            "active_v1_3"
        )

        missing = client.get(
            f"/api/dimension-schemas/{SPACE_SCHEMA_KEY}/versions/9.9.9"
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "维度 Schema 版本不存在"
    finally:
        app.dependency_overrides.clear()
