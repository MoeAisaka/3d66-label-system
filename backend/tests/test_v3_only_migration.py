import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dimension_schema_registry import space_schema_definition_for_version
from app.main import app, current_user
from app.models import (
    Asset,
    BaselineSet,
    BaselineSetItem,
    DimensionSchema,
    User,
)


@pytest.fixture()
def api_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="v3-only-admin",
        password_hash="unused",
        display_name="v3-only 管理员",
        role="admin",
        is_admin=True,
    )
    db.add(user)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    try:
        yield TestClient(app), db, user
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_full_enqueue_fails_closed_without_active_v3_config(api_db) -> None:
    client, _db, _user = api_db

    response = client.post(
        "/api/jobs/enqueue",
        json={"asset_ids": [999], "category_key": "space_image"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "v3_active_config_missing",
        "message": "类目 space_image 缺少 active v3 合同，已拒绝回退 v1",
    }


def test_baseline_run_fails_closed_without_active_v3_config(api_db) -> None:
    client, db, user = api_db
    asset = Asset(
        original_name="baseline.jpg",
        stored_name="baseline.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
        category_key="space_image",
    )
    db.add(asset)
    db.flush()
    baseline_set = BaselineSet(
        name="v3 only",
        description="",
        category_key="space_image",
        default_expected_level="L1",
        fingerprint="b" * 64,
        created_by=user.username,
    )
    db.add(baseline_set)
    db.flush()
    db.add(
        BaselineSetItem(
            baseline_set_id=baseline_set.id,
            asset_id=asset.id,
            expected_level="L1",
            asset_snapshot_json=json.dumps({"id": asset.id}),
        )
    )
    db.commit()

    response = client.post(f"/api/baseline-sets/{baseline_set.id}/runs")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "v3_active_config_missing",
        "message": "类目 space_image 缺少 active v3 合同，已拒绝回退 v1",
    }


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        (
            "post",
            "/api/dimension-schemas",
            {
                "schema_key": "legacy.schema",
                "version": "1",
                "schema_type": "family_pack",
                "family_key": "space",
                "display_name": "旧维度",
                "definition": space_schema_definition_for_version("1.3.0"),
            },
        ),
        (
            "put",
            "/api/dimension-schemas/999",
            {
                "display_name": "旧维度",
                "definition": space_schema_definition_for_version("1.3.0"),
            },
        ),
        ("delete", "/api/dimension-schemas/999", None),
        ("post", "/api/dimension-schemas/999/publish", None),
    ],
)
def test_dimension_schema_writes_are_gone(
    api_db, method: str, path: str, json_body: dict | None
) -> None:
    client, _db, _user = api_db

    response = client.request(method, path, json=json_body)

    assert response.status_code == 410
    assert response.json()["detail"] == {
        "code": "legacy_dimension_write_retired",
        "message": "旧类目维度写入口已下线，请使用“类目评测 v3 合同配置”。",
    }


def test_category_profile_dimension_changes_are_gone(api_db) -> None:
    client, _db, _user = api_db
    profile = next(
        item
        for item in client.get("/api/evaluation-categories").json()["items"]
        if item["category_key"] == "space_image"
    )
    pipeline = dict(profile["pipeline_config"])
    pipeline["dimensions"] = {
        "enabled": False,
        "mode": "none",
        "selected_keys": [],
        "enabled_keys": [],
    }

    response = client.put(
        "/api/evaluation-categories/space_image",
        json={
            **{key: profile[key] for key in (
                "display_name", "description", "status", "allowed_mime_types",
                "preprocess_config", "prompt_a_id", "prompt_b_id",
                "model_config_id", "rubric_version", "dimension_schema_key",
                "dimension_schema_version", "automation_config",
            )},
            "pipeline_config": pipeline,
        },
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_dimension_write_retired"


def test_dimension_schema_reads_remain_available_for_history(api_db) -> None:
    client, db, _user = api_db
    schema = DimensionSchema(
        schema_key="history.schema",
        version="1",
        schema_type="family_pack",
        family_key="space",
        display_name="历史维度",
        status="published",
        definition_json="{}",
        canonical_hash="c" * 64,
        published_by="v3-only-admin",
        published_at=datetime.now(timezone.utc),
    )
    db.add(schema)
    db.commit()

    listed = client.get("/api/dimension-schemas")
    detailed = client.get("/api/dimension-schemas/history.schema/versions/1")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["schema_key"] == "history.schema"
    assert detailed.status_code == 200
    assert detailed.json()["definition"] == {}
