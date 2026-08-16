from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import ScriptDefinition, ScriptVersion, User


def _registry_module():
    assert importlib.util.find_spec("app.workflow_registry") is not None, (
        "workflow registry module is missing"
    )
    return importlib.import_module("app.workflow_registry")


def _api_module():
    assert importlib.util.find_spec("app.workflow_registry_api") is not None, (
        "workflow registry API module is missing"
    )
    return importlib.import_module("app.workflow_registry_api")


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-registry.db'}")
    Base.metadata.create_all(engine)
    return engine


def _seed_script(db: Session, *, status: str = "active") -> None:
    definition = ScriptDefinition(
        script_key="fixture.identity",
        name="Identity",
        description="",
        owner="platform",
        allowed_categories_json="[]",
        step_types_json='["identity","transform"]',
        status="active",
        created_by="admin",
    )
    db.add(definition)
    db.flush()
    db.add(
        ScriptVersion(
            script_definition_id=definition.id,
            version="1",
            display_name="Identity v1",
            executor_kind="deterministic_fixture",
            artifact_sha256="a" * 64,
            manifest_json='{"fixture":"identity"}',
            input_schema_json='{"type":"object"}',
            output_schema_json='{"type":"object"}',
            required_permissions_json="[]",
            idempotency_template="{run_key}:{step_key}:{input_hash}",
            timeout_seconds=60,
            max_attempts=2,
            retry_policy_json='{"kind":"fixed","delay_seconds":1}',
            concurrency_limit=1,
            estimated_cost_json='{}',
            status=status,
            validation_report_json='{"ok":true,"errors":[]}',
            blocked_reason="",
            created_by="admin",
        )
    )
    db.commit()


def _manifest(*, edges=None, queue_class="validation") -> dict:
    return {
        "schema_version": "workflow-v1",
        "steps": [
            {
                "key": "identity",
                "type": "identity",
                "script_version": "fixture.identity@1",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
            {
                "key": "finish",
                "type": "transform",
                "script_version": "fixture.identity@1",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        ],
        "edges": edges or [{"from": "identity", "to": "finish"}],
        "queue_class": queue_class,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "resource_policy": {"max_parallel": 1},
    }


def test_workflow_validator_rejects_cycle(tmp_path) -> None:
    registry = _registry_module()
    engine = _engine(tmp_path)
    try:
        with Session(engine) as db:
            _seed_script(db)
            report = registry.validate_workflow_manifest(
                db,
                _manifest(
                    edges=[
                        {"from": "identity", "to": "finish"},
                        {"from": "finish", "to": "identity"},
                    ]
                ),
            )
            assert any(item.code == "workflow_cycle" for item in report.errors)
    finally:
        engine.dispose()


def test_workflow_validator_rejects_unknown_condition_and_sixth_queue(tmp_path) -> None:
    registry = _registry_module()
    engine = _engine(tmp_path)
    try:
        with Session(engine) as db:
            _seed_script(db)
            manifest = _manifest(queue_class="priority")
            manifest["edges"] = [
                {
                    "from": "identity",
                    "to": "finish",
                    "condition": {"op": "eval", "expression": "True"},
                }
            ]
            report = registry.validate_workflow_manifest(db, manifest)
            codes = {item.code for item in report.errors}
            assert "queue_class_unsupported" in codes
            assert "condition_operator_unsupported" in codes
    finally:
        engine.dispose()


def test_workflow_validator_accepts_dag_and_freezes_script_hash(tmp_path) -> None:
    registry = _registry_module()
    engine = _engine(tmp_path)
    try:
        with Session(engine) as db:
            _seed_script(db)
            manifest = _manifest()
            report = registry.validate_workflow_manifest(db, manifest)
            assert report.ok

            definition = registry.create_workflow_definition(
                db,
                workflow_key="label.test",
                name="Test",
                description="",
                owner="platform",
                allowed_categories=[],
                created_by="admin",
            )
            version = registry.create_workflow_version(
                db,
                definition=definition,
                version="1",
                manifest=manifest,
                created_by="admin",
            )
            snapshot, snapshot_hash = registry.canonical_workflow_snapshot(
                db,
                version.id,
                {
                    "environment": "dry_run",
                    "category_key": "model_3d_su",
                    "queue_policy_version": "queue-policy-v1",
                },
            )
            assert snapshot["scripts"][0]["artifact_sha256"] == "a" * 64
            assert len(snapshot_hash) == 64
    finally:
        engine.dispose()


def _client(role: str) -> TestClient:
    api_module = _api_module()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionForTest = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionForTest() as db:
        _seed_script(db)

    def db_override():
        db = SessionForTest()
        try:
            yield db
        finally:
            db.close()

    def user_override() -> User:
        return User(
            id=1,
            username=role,
            password_hash="x",
            display_name=role,
            role=role,
            is_admin=role == "admin",
        )

    app = FastAPI()
    app.include_router(api_module.build_workflow_registry_router(user_override))
    app.dependency_overrides[get_db] = db_override
    return TestClient(app)


def test_workflow_registry_api_validates_and_activates_version() -> None:
    client = _client("admin")
    definition = client.post(
        "/api/workflows/",
        json={
            "workflow_key": "label.test",
            "name": "Test",
            "description": "",
            "owner": "platform",
            "allowed_categories": [],
        },
    )
    assert definition.status_code == 201

    version = client.post(
        "/api/workflows/label.test/versions",
        json={"version": "1", "manifest": _manifest()},
    )
    assert version.status_code == 201
    assert version.json()["status"] == "draft"

    assert client.post(
        "/api/workflows/label.test/versions/1/transition",
        json={"target_status": "validating"},
    ).status_code == 200
    validated = client.post(
        "/api/workflows/label.test/versions/1/validate"
    )
    assert validated.status_code == 200
    assert validated.json()["validation_report"]["ok"] is True
    active = client.post(
        "/api/workflows/label.test/versions/1/transition",
        json={"target_status": "active"},
    )
    assert active.status_code == 200
    assert active.json()["status"] == "active"
