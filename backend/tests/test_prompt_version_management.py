from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import admin_user, app, current_user
from app.migrations.runner import MIGRATIONS, run_migrations
from app.models import EvaluationCategoryProfile, PromptVersion, User


def test_database_receives_prompt_pipeline_scope_without_ledger_drift() -> None:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE prompt_versions (
                    id INTEGER PRIMARY KEY,
                    version VARCHAR(40) NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for migration in MIGRATIONS:
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
            run_migrations(connection)
            columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(prompt_versions)")
            }
            assert "pipeline_scope" in columns
            assert connection.exec_driver_sql(
                "SELECT max(version) FROM schema_migrations"
            ).scalar_one() == 50
    finally:
        engine.dispose()


def test_prompt_draft_update_clone_publish_scope_and_archive() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="prompt-admin",
        password_hash="unused",
        display_name="提示词管理员",
        is_admin=True,
        role="admin",
    )
    profile = EvaluationCategoryProfile(
        category_key="space_image",
        display_name="空间图",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json="{}",
        pipeline_config_json="{}",
        created_by=user.username,
    )
    prompt = PromptVersion(
        category_key="space_image",
        pipeline_scope="baseline_regression",
        stage="A",
        name="基准草稿",
        version="baseline-draft-v1",
        system_prompt="这是用于基准回归的完整系统提示词。",
        user_prompt="请执行基准回归评测。",
        rubric_version="rubric-v2.1",
        status="draft",
        created_by=user.username,
    )
    db.add_all([user, profile, prompt])
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[admin_user] = lambda: user
    client = TestClient(app)
    try:
        update_payload = {
            "category_key": "space_image",
            "pipeline_scope": "baseline_regression",
            "stage": "A",
            "name": "基准草稿已修改",
            "version": "baseline-draft-v1",
            "system_prompt": "这是修改后且仍用于基准回归的完整系统提示词。",
            "user_prompt": "请执行修改后的基准回归评测。",
            "rubric_version": "rubric-v2.1",
            "change_note": "原地保存",
        }
        updated = client.put(f"/api/prompts/{prompt.id}", json=update_payload)
        assert updated.status_code == 200, updated.text
        assert updated.json() == {"id": prompt.id, "status": "draft"}
        assert db.query(PromptVersion).count() == 1
        assert db.get(PromptVersion, prompt.id).name == "基准草稿已修改"

        clone_payload = {
            **update_payload,
            "version": "baseline-draft-v2",
            "pipeline_scope": "shared",
            "change_note": "另存为",
        }
        cloned = client.post(f"/api/prompts/{prompt.id}/clone", json=clone_payload)
        assert cloned.status_code == 200, cloned.text
        clone_id = cloned.json()["id"]
        assert clone_id != prompt.id
        assert db.query(PromptVersion).count() == 2
        assert db.get(PromptVersion, clone_id).status == "draft"

        published = client.post(
            f"/api/prompts/{clone_id}/publish",
            json={"pipeline_scope": "shared"},
        )
        assert published.status_code == 200, published.text
        assert db.get(PromptVersion, clone_id).status == "published"

        full_only = client.post(
            "/api/prompts",
            json={
                "category_key": "space_image",
                "pipeline_scope": "full_pipeline",
                "stage": "A",
                "name": "完整流水线专用",
                "version": "full-only-v1",
                "system_prompt": "这是完整流水线专用的系统提示词内容，请按完整流程执行。",
                "user_prompt": "请执行完整流水线评测。",
                "rubric_version": "rubric-v2.1",
            },
        )
        assert full_only.status_code == 200, full_only.text

        baseline_items = client.get(
            "/api/prompts?category_key=space_image&pipeline_scope=baseline_regression"
        ).json()["items"]
        assert {item["id"] for item in baseline_items} == {prompt.id, clone_id}
        assert all(item["pipeline_scope"] in {"baseline_regression", "shared"} for item in baseline_items)

        archived = client.delete(f"/api/prompts/{prompt.id}")
        assert archived.status_code == 200, archived.text
        assert db.get(PromptVersion, prompt.id).status == "archived"
        visible_ids = {
            item["id"]
            for item in client.get("/api/prompts?category_key=space_image").json()["items"]
        }
        assert prompt.id not in visible_ids

        immutable = client.put(f"/api/prompts/{clone_id}", json=clone_payload)
        assert immutable.status_code == 409
        assert "不可原地修改" in immutable.text
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
