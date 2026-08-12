from __future__ import annotations

from typing import Iterator

import app.main as main_module
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import ModelConfig, ModelRegistryEntry, OptimizerConfig, User


def _isolated_client() -> tuple[TestClient, Session, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="registry-owner",
        password_hash="unused",
        display_name="Registry Owner",
        role="admin",
    )
    db.add_all(
        [
            user,
            ModelConfig(
                name="主评测模型",
                provider="doubao",
                model_id="doubao-test",
                encrypted_api_key="keychain:v1:main",
            ),
            OptimizerConfig(
                name="调优模型",
                provider="openai",
                model_id="optimizer-test",
                encrypted_api_key="keychain:v1:tuning",
            ),
        ]
    )
    db.commit()

    def override_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app), db, engine


def _close(client: TestClient, db: Session, engine: object) -> None:
    client.close()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()  # type: ignore[attr-defined]


def test_model_registry_migrates_existing_configs_and_defaults_role() -> None:
    client, db, engine = _isolated_client()
    try:
        response = client.get("/api/model-registry")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert {item["role"] for item in items} == {"main", "tuning"}
        assert {item["model_id"] for item in items} == {"doubao-test", "optimizer-test"}
        assert all(item["has_api_key"] is True for item in items)
        assert all("encrypted_api_key" not in item for item in items)
    finally:
        _close(client, db, engine)


def test_model_registry_filters_and_rejects_unknown_protocol() -> None:
    client, db, engine = _isolated_client()
    try:
        response = client.get("/api/model-registry?role=tuning&active=true")
        assert response.status_code == 200, response.text
        assert [item["role"] for item in response.json()["items"]] == ["tuning"]

        payload = {
            "role": "main",
            "name": "非法协议模型",
            "provider": "example",
            "protocol": "arbitrary_headers",
            "capabilities": ["text"],
            "description": "",
            "base_url": "https://example.test/v1",
            "api_path": "/chat/completions",
            "model_id": "example-model",
            "api_key": "secret-that-must-not-leak",
            "temperature": 0.1,
            "max_tokens": 4096,
            "timeout_seconds": 120,
            "max_retries": 1,
            "max_concurrency": 2,
            "max_input_tokens": 0,
            "input_micros_per_million_tokens": 0,
            "output_micros_per_million_tokens": 0,
            "monthly_budget_micros": 0,
            "thinking_mode": "auto",
            "level": "standard",
            "active": True,
        }
        rejected = client.post("/api/model-registry", json=payload)
        assert rejected.status_code == 422, rejected.text
        assert "secret-that-must-not-leak" not in rejected.text

        missing_text = client.post(
            "/api/model-registry",
            json=_valid_payload(capabilities=["vision"]),
        )
        assert missing_text.status_code == 422

        missing_structured_capability = client.post(
            "/api/model-registry",
            json=_valid_payload(capabilities=["text"], structured_output=True),
        )
        assert missing_structured_capability.status_code == 422
    finally:
        _close(client, db, engine)


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "role": "main",
        "name": "新增主模型",
        "provider": "openai",
        "protocol": "openai_chat",
        "capabilities": ["text", "vision", "structured_output"],
        "description": "用于类目评测",
        "base_url": "https://example.test/v1",
        "api_path": "/chat/completions",
        "model_id": "gpt-test",
        "temperature": 0.2,
        "max_tokens": 4096,
        "timeout_seconds": 120,
        "max_retries": 1,
        "max_concurrency": 4,
        "max_requests_per_minute": 60,
        "max_input_tokens": 12000,
        "input_micros_per_million_tokens": 100,
        "output_micros_per_million_tokens": 200,
        "monthly_budget_micros": 10000,
        "thinking_mode": "auto",
        "level": "standard",
        "structured_output": True,
        "active": True,
    }
    payload.update(overrides)
    return payload


def test_model_registry_can_create_tuning_entry_without_exposing_key() -> None:
    client, db, engine = _isolated_client()
    try:
        created = client.post(
            "/api/model-registry",
            json=_valid_payload(
                role="tuning",
                name="调优候选 A",
                provider="anthropic",
                protocol="anthropic_messages",
                capabilities=["text", "structured_output"],
            ),
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["role"] == "tuning"
        assert body["source_optimizer_config_id"] is not None
        assert body["source_model_config_id"] is None
        assert body["has_api_key"] is False
        assert "encrypted_api_key" not in created.text

        tuning = client.get("/api/model-registry?role=tuning")
        assert tuning.status_code == 200
        assert {item["name"] for item in tuning.json()["items"]} >= {"调优模型", "调优候选 A"}
    finally:
        _close(client, db, engine)


def test_model_registry_edit_and_disable_keeps_registry_history() -> None:
    client, db, engine = _isolated_client()
    try:
        created = client.post("/api/model-registry", json=_valid_payload())
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]

        updated = client.put(
            f"/api/model-registry/{entry_id}",
            json=_valid_payload(name="主模型 v2", level="critical", active=True),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "主模型 v2"
        assert updated.json()["level"] == "critical"

        disabled = client.post(f"/api/model-registry/{entry_id}/deactivate")
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["active"] is False

        listed = client.get("/api/model-registry?active=false")
        assert listed.status_code == 200
        listed_item = next(item for item in listed.json()["items"] if item["id"] == entry_id)
        assert listed_item["level"] == "critical"
        assert listed_item["monthly_budget_micros"] == 10000
        assert listed_item["max_requests_per_minute"] == 60
        assert db.get(ModelConfig, created.json()["source_model_config_id"]).active is False
    finally:
        _close(client, db, engine)


def test_tuning_limits_and_thinking_mode_survive_registry_refresh() -> None:
    client, db, engine = _isolated_client()
    try:
        created = client.post(
            "/api/model-registry",
            json=_valid_payload(
                role="tuning",
                name="调优持久化",
                max_concurrency=17,
                max_requests_per_minute=240,
                monthly_budget_micros=9000,
                thinking_mode="enabled",
                level="advanced",
            ),
        )
        assert created.status_code == 200, created.text
        entry_id = created.json()["id"]
        refreshed = client.get("/api/model-registry?role=tuning")
        assert refreshed.status_code == 200
        item = next(item for item in refreshed.json()["items"] if item["id"] == entry_id)
        assert item["max_concurrency"] == 17
        assert item["max_requests_per_minute"] == 240
        assert item["monthly_budget_micros"] == 9000
        assert item["thinking_mode"] == "enabled"
        assert item["level"] == "advanced"
    finally:
        _close(client, db, engine)


def test_registry_credentials_use_unique_registry_accounts(monkeypatch) -> None:
    client, db, engine = _isolated_client()
    accounts: list[str] = []

    def protect_for_test(secret, *, account: str):
        if secret is None:
            return None
        accounts.append(account)
        return f"test-reference:{account}"

    monkeypatch.setattr(main_module, "_protected_api_key", protect_for_test)
    try:
        main_entry = client.post(
            "/api/model-registry",
            json=_valid_payload(name="带密钥主模型", api_key="test-main-key"),
        )
        tuning_entry = client.post(
            "/api/model-registry",
            json=_valid_payload(
                role="tuning",
                name="带密钥调优模型",
                api_key="test-tuning-key",
            ),
        )
        assert main_entry.status_code == 200, main_entry.text
        assert tuning_entry.status_code == 200, tuning_entry.text
        expected = {
            f"model-registry-{main_entry.json()['id']}",
            f"model-registry-{tuning_entry.json()['id']}",
        }
        assert set(accounts) == expected
        assert len(accounts) == len(set(accounts)) == 2
    finally:
        _close(client, db, engine)


def test_registry_migration_projects_existing_legacy_rows() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine)
    try:
        db.add_all(
            [
                ModelConfig(name="迁移主模型", model_id="legacy-main"),
                OptimizerConfig(name="迁移调优模型", model_id="legacy-tuning"),
            ]
        )
        db.commit()
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM model_registry_entries")
            connection.exec_driver_sql(
                "DELETE FROM schema_migrations WHERE version IN (61,62)"
            )
            run_migrations(connection)
        rows = db.execute(
            select(ModelRegistryEntry).where(
                ModelRegistryEntry.model_id.in_(["legacy-main", "legacy-tuning"])
            )
        ).scalars().all()
        assert {(row.role, row.model_id) for row in rows} == {
            ("main", "legacy-main"),
            ("tuning", "legacy-tuning"),
        }
    finally:
        db.close()
        engine.dispose()
