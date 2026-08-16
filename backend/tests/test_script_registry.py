from __future__ import annotations

import importlib
import importlib.util

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import User


def _registry_module():
    assert importlib.util.find_spec("app.script_registry") is not None, (
        "script registry module is missing"
    )
    return importlib.import_module("app.script_registry")


def _api_module():
    assert importlib.util.find_spec("app.script_registry_api") is not None, (
        "script registry API module is missing"
    )
    return importlib.import_module("app.script_registry_api")


def _valid_payload() -> dict:
    return {
        "display_name": "Identity v1",
        "executor_kind": "deterministic_fixture",
        "artifact_sha256": "a" * 64,
        "manifest": {"fixture": "identity"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_permissions": [],
        "idempotency_template": "{run_key}:{step_key}:{input_hash}",
        "timeout_seconds": 60,
        "max_attempts": 2,
        "retry_policy": {"kind": "fixed", "delay_seconds": 1},
        "concurrency_limit": 1,
        "rate_limit_key": None,
        "estimated_cost": {"currency": "CNY", "micros": 0},
    }


def test_script_validation_rejects_arbitrary_executor_and_source() -> None:
    registry = _registry_module()
    payload = _valid_payload()
    payload["executor_kind"] = "python"
    payload["source"] = "print('unsafe')"

    report = registry.validate_script_version_payload(payload)

    assert not report.ok
    assert {item.code for item in report.errors} == {
        "executor_kind_unsupported",
        "arbitrary_code_field_forbidden",
    }


def test_script_validation_accepts_deterministic_fixture_contract() -> None:
    registry = _registry_module()

    report = registry.validate_script_version_payload(_valid_payload())

    assert report.ok
    assert report.errors == ()


def test_script_lifecycle_rejects_direct_draft_activation(tmp_path) -> None:
    registry = _registry_module()
    models = importlib.import_module("app.models")
    engine = create_engine(f"sqlite:///{tmp_path / 'script-lifecycle.db'}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            definition = models.ScriptDefinition(
                script_key="fixture.identity",
                name="Identity",
                description="",
                owner="platform",
                allowed_categories_json="[]",
                step_types_json='["identity"]',
                status="active",
                created_by="admin",
            )
            db.add(definition)
            db.flush()
            version = models.ScriptVersion(
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
                estimated_cost_json='{"currency":"CNY","micros":0}',
                status="draft",
                validation_report_json="{}",
                blocked_reason="",
                created_by="admin",
            )
            db.add(version)
            db.flush()

            try:
                registry.transition_script_version(
                    db, version.id, "active", actor="admin"
                )
            except registry.ScriptRegistryError as exc:
                assert exc.code == "script_transition_invalid"
            else:
                raise AssertionError("draft version activated without validation")
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
    app.include_router(api_module.build_script_registry_router(user_override))
    app.dependency_overrides[get_db] = db_override
    return TestClient(app)


def test_script_registry_api_enforces_write_permission() -> None:
    response = _client("viewer").post(
        "/api/scripts/",
        json={
            "script_key": "fixture.identity",
            "name": "Identity",
            "description": "",
            "owner": "platform",
            "allowed_categories": [],
            "step_types": ["identity"],
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "script_write_forbidden"


def test_script_registry_api_creates_validates_and_activates_version() -> None:
    client = _client("admin")
    create_definition = client.post(
        "/api/scripts/",
        json={
            "script_key": "fixture.identity",
            "name": "Identity",
            "description": "",
            "owner": "platform",
            "allowed_categories": [],
            "step_types": ["identity"],
        },
    )
    assert create_definition.status_code == 201

    create_version = client.post(
        "/api/scripts/fixture.identity/versions",
        json={"version": "1", **_valid_payload()},
    )
    assert create_version.status_code == 201
    assert create_version.json()["status"] == "draft"

    validating = client.post(
        "/api/scripts/fixture.identity/versions/1/transition",
        json={"target_status": "validating"},
    )
    assert validating.status_code == 200

    validation = client.post(
        "/api/scripts/fixture.identity/versions/1/validate"
    )
    assert validation.status_code == 200
    assert validation.json()["validation_report"]["ok"] is True

    active = client.post(
        "/api/scripts/fixture.identity/versions/1/transition",
        json={"target_status": "active"},
    )
    assert active.status_code == 200
    assert active.json()["status"] == "active"

    listing = client.get("/api/scripts/")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["script_key"] == "fixture.identity"

