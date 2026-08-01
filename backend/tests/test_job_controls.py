import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app import worker
from app.main import _category_execution_snapshot, app, current_user
from app.models import (
    Asset,
    EvaluationCategoryProfile,
    EvaluationControl,
    EvaluationJob,
    ModelConfig,
    PromptVersion,
    User,
)
from app.worker import _frozen_category_contract


def test_jobs_pin_prompts_and_support_pause_resume_cancel() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(username="tester", password_hash="unused", display_name="测试员")
    asset = Asset(
        original_name="room.jpg",
        stored_name="room.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=1200,
        height=800,
        sha256="b" * 64,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="分类",
        version="A-2.1",
        system_prompt="system prompt for classification",
        user_prompt="user prompt",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="美感",
        version="B-2.3",
        system_prompt="system prompt for aesthetic scoring",
        user_prompt="user prompt",
        status="draft",
    )
    model = ModelConfig(
        name="自定义主模型",
        provider="custom-compatible",
        base_url="https://model.example.test/v1",
        model_id="vision-model-v1",
        encrypted_api_key="credential-reference-never-snapshot",
    )
    db.add_all(
        [user, asset, prompt_a, prompt_b, model, EvaluationControl(id=1)]
    )
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            "/api/jobs/enqueue",
            json={
                "asset_ids": [asset.id],
                "prompt_a_id": prompt_a.id,
                "prompt_b_id": prompt_b.id,
            },
        )
        assert created.status_code == 200
        job_id = created.json()["job_ids"][0]
        frozen_job = db.get(EvaluationJob, job_id)
        frozen_profile = json.loads(frozen_job.category_profile_snapshot_json)
        assert frozen_profile["schema_version"] == "evaluation-category-profile-v2"
        assert frozen_profile["category_key"] == "space_image"
        assert frozen_profile["prompt_a_id"] == prompt_a.id
        assert frozen_profile["prompt_b_id"] == prompt_b.id
        assert frozen_profile["model_config"]["provider"] == "custom-compatible"
        assert "credential-reference-never-snapshot" not in (
            frozen_job.category_profile_snapshot_json
        )

        original_snapshot = frozen_job.category_profile_snapshot_json
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        profile.status = "retired"
        profile.preprocess_config_json = '{"changed_after_enqueue":true}'
        model.active = False
        model.provider = "changed-after-enqueue"
        db.commit()
        assert frozen_job.category_profile_snapshot_json == original_snapshot
        frozen_contract = _frozen_category_contract(frozen_job, asset)
        assert frozen_contract is not None
        assert frozen_contract["preprocess_config"] == {"preprocess": "image"}
        assert frozen_contract["model_config"]["provider"] == "custom-compatible"
        profile.status = "active"
        db.commit()

        listed = client.get("/api/jobs").json()["items"][0]
        assert listed["prompt_a_version"] == "A-2.1"
        assert listed["prompt_b_version"] == "B-2.3"
        assert listed["updated_at"]

        paused = client.post("/api/jobs/control/pause")
        assert paused.status_code == 200
        assert paused.json()["affected"] == 1
        db.expire_all()
        assert db.get(EvaluationJob, job_id).status == "paused"
        assert db.get(EvaluationJob, job_id).updated_at is not None
        assert client.get("/api/jobs/control").json()["paused"] is True

        resumed = client.post("/api/jobs/control/resume")
        assert resumed.status_code == 200
        db.expire_all()
        assert db.get(EvaluationJob, job_id).status == "queued"

        canceled = client.post("/api/jobs/control/cancel")
        assert canceled.status_code == 200
        db.expire_all()
        assert db.get(EvaluationJob, job_id).status == "canceled"
        assert db.get(Asset, asset.id).status == "uploaded"
        canceled_asset = client.get(
            "/api/assets",
            params={
                "prompt_a_id": prompt_a.id,
                "prompt_b_id": prompt_b.id,
            },
        ).json()["items"][0]
        assert canceled_asset["evaluation_status"] == "failed"

        single = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset.id], "prompt_id": prompt_a.id},
        )
        assert single.status_code == 200
        single_job = client.get("/api/jobs").json()["items"][0]
        assert single_job["prompt_version"] == "A-2.1"
        assert single_job["prompt_a_version"] is None
        assert single_job["prompt_b_version"] is None
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_frozen_category_contract_rejects_mime_and_secret_drift() -> None:
    asset = Asset(
        id=1,
        original_name="room.jpg",
        stored_name="room.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        sha256="c" * 64,
    )
    base_snapshot = {
        "schema_version": "evaluation-category-profile-v1",
        "profile_id": 1,
        "category_key": "space_image",
        "display_name": "空间图片",
        "allowed_mime_types": ["image/png"],
        "preprocess_config": {"preprocess": "image"},
        "prompt_a_id": 11,
        "prompt_b_id": None,
        "model_config_id": 21,
        "model_config": {
            "name": "模型",
            "provider": "custom",
            "base_url": "https://model.example.test/v1",
            "api_path": "/chat/completions",
            "model_id": "vision-v1",
            "temperature": 0.1,
            "max_tokens": 4096,
            "timeout_seconds": 120,
            "max_retries": 1,
            "max_concurrency": 2,
            "structured_output": True,
            "high_risk_review_enabled": True,
        },
        "rubric_version": "rubric-v2.1",
        "dimension_schema_key": None,
        "dimension_schema_version": None,
        "profile_updated_at": None,
    }
    job = EvaluationJob(
        id=1,
        asset_id=1,
        category_key="space_image",
        prompt_a_id=11,
        prompt_b_id=None,
        category_profile_snapshot_json=json.dumps(base_snapshot),
    )
    with pytest.raises(RuntimeError, match="MIME"):
        _frozen_category_contract(job, asset)

    base_snapshot["allowed_mime_types"] = ["image/jpeg"]
    base_snapshot["model_config"]["encrypted_api_key"] = "must-not-survive"
    job.category_profile_snapshot_json = json.dumps(base_snapshot)
    with pytest.raises(RuntimeError, match="模型快照"):
        _frozen_category_contract(job, asset)


def test_worker_uses_frozen_contract_after_live_configuration_is_retired(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="missing.jpg",
        stored_name="missing.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        sha256="d" * 64,
    )
    prompt = PromptVersion(
        stage="A",
        name="单提示词",
        version="single-v1",
        system_prompt="return the complete evaluation contract",
        user_prompt="evaluate {{image_metadata}}",
        rubric_version="rubric-v2.1",
        status="published",
    )
    model = ModelConfig(
        provider="custom-compatible",
        model_id="vision-v1",
        encrypted_api_key="credential-reference",
    )
    profile = EvaluationCategoryProfile(
        category_key="space_image",
        display_name="空间图片",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json='{"preprocess":"image"}',
        prompt_a_id=None,
        prompt_b_id=None,
        model_config_id=None,
        rubric_version="rubric-v2.1",
    )
    db.add_all([asset, prompt, model, profile])
    db.flush()
    snapshot = _category_execution_snapshot(
        profile,
        prompt_a_id=prompt.id,
        prompt_b_id=None,
        model_config=model,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        category_key="space_image",
        category_profile_snapshot_json=snapshot,
        prompt_a_id=prompt.id,
        prompt_b_id=None,
        status="processing",
    )
    db.add(job)
    db.commit()
    profile.status = "retired"
    profile.preprocess_config_json = '{"changed_after_enqueue":true}'
    model.active = False
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(upload_dir=tmp_path))
    try:
        with pytest.raises(RuntimeError, match="原始素材文件不存在"):
            asyncio.run(worker.evaluate_job(job.id))
    finally:
        db.close()
        engine.dispose()
