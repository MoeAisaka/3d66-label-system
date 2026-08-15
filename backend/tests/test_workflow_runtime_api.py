from __future__ import annotations

import importlib
import importlib.util

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import RuntimeAuditEvent, User
from tests.test_workflow_runtime import _seed_active_workflow


def _api_module():
    assert importlib.util.find_spec("app.workflow_runtime_api") is not None, (
        "workflow runtime API module is missing"
    )
    return importlib.import_module("app.workflow_runtime_api")


def _client(role: str) -> tuple[TestClient, sessionmaker]:
    api_module = _api_module()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionForTest = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionForTest() as db:
        version = _seed_active_workflow(db)
        workflow_version_id = version.id

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
    app.include_router(api_module.build_workflow_runtime_router(user_override))
    app.dependency_overrides[get_db] = db_override
    client = TestClient(app)
    client.workflow_version_id = workflow_version_id  # type: ignore[attr-defined]
    return client, SessionForTest


def _create_payload(client: TestClient, *, idempotency_key: str = "runtime-api-1") -> dict:
    return {
        "workflow_version_id": client.workflow_version_id,  # type: ignore[attr-defined]
        "idempotency_key": idempotency_key,
        "category_key": "model_3d_su",
        "queue_class": "validation",
        "runtime_context": {"queue_policy_version": "queue-policy-v1"},
        "input_manifest": {"content_key": "3d:1:42"},
        "owner": "platform",
        "reason": "api test",
    }


def test_runtime_api_creates_idempotently_and_returns_evidence() -> None:
    client, SessionForTest = _client("admin")
    first = client.post("/api/runtime/runs", json=_create_payload(client))
    assert first.status_code == 201
    run_key = first.json()["run_key"]
    assert first.json()["environment"] == "dry_run"
    assert first.json()["allowed_actions"] == ["cancel", "pause"]

    duplicate = client.post("/api/runtime/runs", json=_create_payload(client))
    assert duplicate.status_code == 200
    assert duplicate.json()["run_key"] == run_key
    assert duplicate.json()["duplicate"] is True

    listing = client.get("/api/runtime/runs?queue_class=validation")
    assert listing.status_code == 200
    assert [item["run_key"] for item in listing.json()["items"]] == [run_key]

    detail = client.get(f"/api/runtime/runs/{run_key}")
    assert detail.status_code == 200
    assert detail.json()["workflow_version"] == "1"
    assert detail.json()["current_step_key"] == "identity"

    timeline = client.get(f"/api/runtime/runs/{run_key}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["items"][0]["step_key"] == "identity"
    assert timeline.json()["items"][0]["script_version"] == "1"

    snapshot = client.get(f"/api/runtime/runs/{run_key}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["snapshot"]["schema_version"] == "production-run-snapshot-v1"

    with SessionForTest() as db:
        events = db.scalars(select(RuntimeAuditEvent)).all()
        assert [(item.action, item.entity_key) for item in events] == [
            ("create", run_key)
        ]


def test_runtime_api_enforces_rbac_and_rejects_executable_fields() -> None:
    viewer, _ = _client("viewer")
    forbidden = viewer.post("/api/runtime/runs", json=_create_payload(viewer))
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "runtime_write_forbidden"

    admin, _ = _client("admin")
    payload = _create_payload(admin, idempotency_key="unsafe-runtime")
    payload["input_manifest"] = {"command": "echo unsafe"}
    unsafe = admin.post("/api/runtime/runs", json=payload)
    assert unsafe.status_code == 400
    assert unsafe.json()["detail"]["code"] == "arbitrary_code_field_forbidden"


def test_runtime_api_pause_resume_and_cancel_are_state_aware() -> None:
    client, SessionForTest = _client("admin")
    created = client.post("/api/runtime/runs", json=_create_payload(client))
    run_key = created.json()["run_key"]

    paused = client.post(f"/api/runtime/runs/{run_key}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["allowed_actions"] == ["cancel", "resume"]

    resumed = client.post(f"/api/runtime/runs/{run_key}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    canceled = client.post(f"/api/runtime/runs/{run_key}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["allowed_actions"] == []

    invalid = client.post(f"/api/runtime/runs/{run_key}/resume")
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "run_resume_state_invalid"

    with SessionForTest() as db:
        actions = [
            item.action
            for item in db.scalars(
                select(RuntimeAuditEvent).order_by(RuntimeAuditEvent.id)
            ).all()
        ]
        assert actions == ["create", "pause", "resume", "cancel"]

