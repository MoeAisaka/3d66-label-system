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
from app.category_pipeline import default_pipeline
from app.main import _category_execution_snapshot, app, current_user
from app.models import (
    Asset,
    DimensionSchema,
    EvaluationCategoryProfile,
    EvaluationControl,
    EvaluationJob,
    ModelConfig,
    PromptVersion,
    User,
)
from app.dimension_schema_registry import canonical_hash, canonical_json
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
    SPACE_SCHEMA_KEY,
    materialized_space_schema_rows,
    space_schema_definition_for_version,
)
from app.production_dimension_contract import (
    ProductionDimensionContractError,
    resolve_frozen_dimension_contract,
    resolve_published_dimension_contract,
)
from app.strategy_bundle import resolve_frozen_dimension_entry
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
    schema = _supported_dimension_schema()
    db.add_all(
        [
            user,
            asset,
            prompt_a,
            prompt_b,
            model,
            schema,
            EvaluationControl(id=1),
        ]
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
        assert frozen_profile["dimension_contract"] == {
            "schema_id": schema.id,
            "schema_key": SPACE_SCHEMA_KEY,
            "version": ACTIVE_V13_VERSION,
            "canonical_hash": schema.canonical_hash,
            "definition": json.loads(schema.definition_json),
        }
        frozen_dimension = resolve_frozen_dimension_contract(frozen_profile)
        assert frozen_dimension is not None
        assert frozen_dimension.definition == json.loads(schema.definition_json)
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

        profile.pipeline_config_json = json.dumps(
            {**default_pipeline("space_image"), "prompt_mode": "single"},
            ensure_ascii=False,
        )
        profile.prompt_a_id = prompt_a.id
        profile.prompt_b_id = None
        db.commit()
        wrong_single = client.post(
            "/api/jobs/enqueue",
            json={
                "asset_ids": [asset.id],
                "prompt_a_id": prompt_a.id,
                "prompt_b_id": prompt_b.id,
            },
        )
        assert wrong_single.status_code == 409
        assert wrong_single.json()["detail"]["code"] == "prompt_mode_mismatch"

        profile.pipeline_config_json = json.dumps(
            {**default_pipeline("space_image"), "prompt_mode": "ab"},
            ensure_ascii=False,
        )
        profile.prompt_a_id = prompt_a.id
        profile.prompt_b_id = prompt_b.id
        db.commit()
        wrong_ab = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset.id], "prompt_id": prompt_a.id},
        )
        assert wrong_ab.status_code == 409
        assert wrong_ab.json()["detail"]["code"] == "prompt_mode_mismatch"

        incomplete_pair = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset.id], "prompt_a_id": prompt_a.id},
        )
        assert incomplete_pair.status_code == 422
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


def test_frozen_category_contract_rejects_dimension_selection_drift() -> None:
    asset = Asset(
        id=1,
        original_name="room.jpg",
        stored_name="room.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        sha256="f" * 64,
    )
    pipeline = default_pipeline("space_image")
    pipeline["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "selected_keys": ["composition_viewpoint"],
    }
    profile = EvaluationCategoryProfile(
        id=1,
        category_key="space_image",
        display_name="空间图片",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json="{}",
        pipeline_config_json=canonical_json(pipeline),
        prompt_a_id=11,
        prompt_b_id=None,
        rubric_version="rubric-v2.1",
    )
    model = ModelConfig(
        id=21,
        name="模型",
        provider="custom-compatible",
        base_url="https://model.example.test/v1",
        api_path="/chat/completions",
        model_id="vision-v1",
    )
    snapshot = json.loads(
        _category_execution_snapshot(
            profile,
            prompt_a_id=11,
            prompt_b_id=None,
            model_config=model,
        )
    )
    job = EvaluationJob(
        id=1,
        asset_id=1,
        category_key="space_image",
        prompt_a_id=11,
        prompt_b_id=None,
        category_profile_snapshot_json=json.dumps(snapshot),
    )
    assert _frozen_category_contract(job, asset)["dimension_selection"][
        "effective_keys"
    ] == ["composition_viewpoint"]

    snapshot["dimension_selection"]["effective_keys"] = [
        "presentation_integrity"
    ]
    job.category_profile_snapshot_json = json.dumps(snapshot)
    with pytest.raises(RuntimeError, match="维度选择与流水线不一致"):
        _frozen_category_contract(job, asset)


def test_frozen_category_contract_accepts_selected_key_from_custom_schema() -> None:
    definition = json.loads(
        json.dumps(space_schema_definition_for_version(ACTIVE_V13_VERSION))
    )
    old_key = definition["dimensions"][-1]["key"]
    custom_key = "material_authenticity"
    definition["dimensions"][-1]["key"] = custom_key
    definition["dimensions"][-1]["label"] = "材质真实性"
    definition["output_contract"]["dimension_output_keys"][-1] = custom_key
    definition["risk_review"]["dimension_keys"][-1] = custom_key
    definition["core_dimension_keys"] = [
        custom_key if key == old_key else key
        for key in definition["core_dimension_keys"]
    ]
    definition_hash = canonical_hash(definition)
    dimension_contract = SimpleNamespace(
        schema_id=99,
        schema_key="material.managed",
        version="1.0.0",
        canonical_hash=definition_hash,
        definition=definition,
    )
    pipeline = default_pipeline("material_image")
    pipeline["prompt_mode"] = "single"
    pipeline["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "selected_keys": [custom_key],
    }
    profile = EvaluationCategoryProfile(
        id=1,
        category_key="material_image",
        display_name="材质图",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json="{}",
        pipeline_config_json=canonical_json(pipeline),
        prompt_a_id=11,
        prompt_b_id=None,
        rubric_version="material-v1",
        dimension_schema_key=dimension_contract.schema_key,
        dimension_schema_version=dimension_contract.version,
    )
    model = ModelConfig(
        id=21,
        provider="custom-compatible",
        base_url="https://model.example.test/v1",
        api_path="/chat/completions",
        model_id="vision-v1",
    )
    snapshot = json.loads(
        _category_execution_snapshot(
            profile,
            prompt_a_id=11,
            prompt_b_id=None,
            model_config=model,
            dimension_contract=dimension_contract,
        )
    )
    assert snapshot["dimension_selection"]["effective_keys"] == [custom_key]
    asset = Asset(
        id=1,
        original_name="material.jpg",
        stored_name="material.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        sha256="a" * 64,
    )
    job = EvaluationJob(
        id=1,
        asset_id=1,
        category_key="material_image",
        prompt_a_id=11,
        prompt_b_id=None,
        category_profile_snapshot_json=canonical_json(snapshot),
    )
    frozen = _frozen_category_contract(job, asset)
    assert frozen is not None
    assert frozen["dimension_selection"]["effective_keys"] == [custom_key]
    assert frozen["dimension_contract"]["canonical_hash"] == definition_hash


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
    schema = _supported_dimension_schema()
    profile.dimension_schema_key = schema.schema_key
    profile.dimension_schema_version = schema.version
    db.add_all([asset, prompt, model, profile, schema])
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


def test_worker_rejects_frozen_non_published_dimension_before_asset_access(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    schema = _dimension_schema(status="candidate", version="worker-candidate-v1")
    asset = Asset(
        original_name="missing.jpg", stored_name="missing.jpg", mime_type="image/jpeg",
        size_bytes=100, sha256="e" * 64, category_key="space_image",
    )
    prompt = PromptVersion(
        stage="A", name="单提示词", version="worker-candidate-prompt-v1",
        system_prompt="return complete result", user_prompt="evaluate",
        rubric_version="rubric-v2.1", status="published",
    )
    model = ModelConfig(
        provider="custom-compatible", model_id="worker-candidate-model-v1",
        encrypted_api_key="credential-reference",
    )
    profile = EvaluationCategoryProfile(
        category_key="space_image", display_name="空间图片", status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json='{"preprocess":"image"}',
        rubric_version="rubric-v2.1", dimension_schema_key=schema.schema_key,
        dimension_schema_version=schema.version,
    )
    db.add_all([schema, asset, prompt, model, profile])
    db.flush()
    job = EvaluationJob(
        asset_id=asset.id, category_key="space_image", prompt_a_id=prompt.id,
        status="processing",
        category_profile_snapshot_json=_category_execution_snapshot(
            profile, prompt_a_id=prompt.id, prompt_b_id=None, model_config=model
        ),
    )
    db.add(job)
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
        with pytest.raises(ProductionDimensionContractError) as blocked:
            asyncio.run(worker.evaluate_job(job.id))
        assert blocked.value.code == "dimension_contract_not_published"
    finally:
        db.close()
        engine.dispose()


def _dimension_schema(*, status: str, version: str) -> DimensionSchema:
    definition = {
        "format_version": "dimension-schema-definition-v1",
        "dimensions": [{"key": "quality", "required": True}],
    }
    published = status in {"published", "retired"}
    return DimensionSchema(
        schema_key="test.production",
        version=version,
        schema_type="family_pack",
        family_key="space",
        display_name=f"测试维度 {version}",
        status=status,
        definition_json=canonical_json(definition),
        canonical_hash=canonical_hash(definition),
        created_by="test",
        published_by="test" if published else None,
        published_at=worker.datetime.now(worker.timezone.utc) if published else None,
        retired_at=worker.datetime.now(worker.timezone.utc) if status == "retired" else None,
    )


def _supported_dimension_schema() -> DimensionSchema:
    definition = space_schema_definition_for_version(ACTIVE_V13_VERSION)
    return DimensionSchema(
        schema_key=SPACE_SCHEMA_KEY,
        version=ACTIVE_V13_VERSION,
        schema_type="family_pack",
        family_key="space",
        display_name="空间现役维度",
        status="published",
        definition_json=canonical_json(definition),
        canonical_hash=canonical_hash(definition),
        created_by="test",
        published_by="test",
        published_at=worker.datetime.now(worker.timezone.utc),
    )


def _dimension_enqueue_dependencies(db: Session, *, suffix: str):
    asset = Asset(
        original_name=f"dimension-{suffix}.jpg",
        stored_name=f"dimension-{suffix}.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        sha256=(suffix[0] if suffix else "e") * 64,
        category_key="space_image",
    )
    prompt_a = PromptVersion(
        stage="A", name="维度 A", version=f"dimension-a-{suffix}",
        system_prompt="classify image completely", user_prompt="evaluate",
        rubric_version="rubric-v2.1", status="published",
    )
    prompt_b = PromptVersion(
        stage="B", name="维度 B", version=f"dimension-b-{suffix}",
        system_prompt="score image completely", user_prompt="evaluate",
        rubric_version="rubric-v2.1", status="published",
    )
    model = ModelConfig(
        name="维度测试模型", provider="custom-compatible",
        model_id=f"dimension-model-{suffix}", active=True,
    )
    db.add_all([asset, prompt_a, prompt_b, model])
    db.flush()
    return asset, prompt_a, prompt_b, model


@pytest.mark.parametrize("status", ["draft", "candidate", "retired"])
def test_enqueue_rejects_non_published_dimension_contract(status: str) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    schema = _dimension_schema(status=status, version=f"{status}-v1")
    user = User(
        username="dimension-admin",
        password_hash="unused",
        display_name="维度管理员",
        role="admin",
        is_admin=True,
    )
    db.add_all([schema, user])
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    try:
        client = TestClient(app)
        client.get("/api/evaluation-categories")
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        asset, prompt_a, prompt_b, model = _dimension_enqueue_dependencies(
            db, suffix=status
        )
        profile.prompt_a_id = prompt_a.id
        profile.prompt_b_id = prompt_b.id
        profile.model_config_id = model.id
        profile.rubric_version = "rubric-v2.1"
        profile.dimension_schema_key = schema.schema_key
        profile.dimension_schema_version = schema.version
        db.commit()
        response = client.post(
            "/api/jobs/enqueue", json={"asset_ids": [asset.id]}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "dimension_contract_not_published"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_published_dimension_contract_allows_enqueue() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    schema = _supported_dimension_schema()
    user = User(
        username="dimension-admin",
        password_hash="unused",
        display_name="维度管理员",
        role="admin",
        is_admin=True,
    )
    db.add_all([schema, user])
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    try:
        client = TestClient(app)
        client.get("/api/evaluation-categories")
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        asset, prompt_a, prompt_b, model = _dimension_enqueue_dependencies(
            db, suffix="published"
        )
        profile.prompt_a_id = prompt_a.id
        profile.prompt_b_id = prompt_b.id
        profile.model_config_id = model.id
        profile.rubric_version = "rubric-v2.1"
        profile.dimension_schema_key = schema.schema_key
        profile.dimension_schema_version = schema.version
        db.commit()
        response = client.post(
            "/api/jobs/enqueue", json={"asset_ids": [asset.id]}
        )
        assert response.status_code == 200
        assert len(response.json()["job_ids"]) == 1
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_enqueue_requires_explicit_dimension_contract_for_new_jobs() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="dimension-admin",
        password_hash="unused",
        display_name="维度管理员",
        role="admin",
        is_admin=True,
    )
    db.add(user)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    try:
        client = TestClient(app)
        client.get("/api/evaluation-categories")
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        profile.dimension_schema_key = None
        profile.dimension_schema_version = None
        db.commit()

        response = client.post("/api/jobs/enqueue", json={"asset_ids": [999]})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "dimension_contract_incomplete"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_active_category_update_rejects_unexecutable_dimension_contract() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="dimension-admin",
        password_hash="unused",
        display_name="维度管理员",
        role="admin",
        is_admin=True,
    )
    db.add(user)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    try:
        client = TestClient(app)
        profile = next(
            item
            for item in client.get("/api/evaluation-categories").json()["items"]
            if item["category_key"] == "space_image"
        )
        payload = {
            key: value
            for key, value in profile.items()
            if key
            not in {
                "id",
                "category_key",
                "pipeline_revision",
                "automation_revision",
                "created_by",
                "created_at",
                "updated_at",
            }
        }
        payload["dimension_schema_key"] = "missing.dimension"
        payload["dimension_schema_version"] = "v1"

        response = client.put(
            "/api/evaluation-categories/space_image", json=payload
        )

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "dimension_contract_missing"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_production_dimension_contract_rejects_missing_and_invalid_definition() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    try:
        with pytest.raises(ProductionDimensionContractError) as missing:
            resolve_published_dimension_contract(
                db, schema_key="missing", version="v1"
            )
        assert missing.value.code == "dimension_contract_missing"
        assert (
            resolve_published_dimension_contract(
                db, schema_key=None, version=None
            )
            is None
        )

        schema = _dimension_schema(status="published", version="tampered-v1")
        schema.canonical_hash = "f" * 64
        db.add(schema)
        db.commit()
        with pytest.raises(ProductionDimensionContractError) as invalid:
            resolve_published_dimension_contract(
                db, schema_key=schema.schema_key, version=schema.version
            )
        assert invalid.value.code == "dimension_contract_invalid"

        invalid_definition = _dimension_schema(
            status="published", version=ACTIVE_V13_VERSION
        )
        invalid_definition.schema_key = SPACE_SCHEMA_KEY
        db.add(invalid_definition)
        db.commit()
        with pytest.raises(ProductionDimensionContractError) as not_executable:
            resolve_published_dimension_contract(
                db,
                schema_key=invalid_definition.schema_key,
                version=invalid_definition.version,
            )
        assert not_executable.value.code == "dimension_contract_not_executable"

        custom_definition = space_schema_definition_for_version(
            ACTIVE_V13_VERSION
        )
        custom_definition["package_version"] = "custom-v1"
        custom_schema = DimensionSchema(
            schema_key="test.custom-executable",
            version="v1",
            schema_type="family_pack",
            family_key="space",
            display_name="尚未接入 Bundle 的可执行维度",
            status="published",
            definition_json=canonical_json(custom_definition),
            canonical_hash=canonical_hash(custom_definition),
            created_by="test",
            published_by="test",
            published_at=worker.datetime.now(worker.timezone.utc),
        )
        db.add(custom_schema)
        db.commit()
        resolved_custom = resolve_published_dimension_contract(
            db,
            schema_key=custom_schema.schema_key,
            version=custom_schema.version,
        )
        assert resolved_custom is not None
        assert resolved_custom.canonical_hash == custom_schema.canonical_hash

        custom_bundle = SimpleNamespace(
            dimension_schema_set_snapshot=json.dumps(
                {
                    "schemas": [
                        {
                            "schema_key": custom_schema.schema_key,
                            "version": custom_schema.version,
                            "canonical_hash": custom_schema.canonical_hash,
                            "definition": custom_definition,
                        }
                    ]
                }
            )
        )
        resolved_from_bundle = resolve_published_dimension_contract(
            db,
            schema_key=custom_schema.schema_key,
            version=custom_schema.version,
            bundle=custom_bundle,
        )
        assert resolved_from_bundle is not None
        assert resolved_from_bundle.definition == custom_definition
    finally:
        db.close()
        engine.dispose()


def test_explicit_category_dimension_overrides_model_scoring_profile_route() -> None:
    rows = materialized_space_schema_rows()
    bundle = SimpleNamespace(
        strategy_schema_version="strategy-bundle-v2",
        dimension_schema_set_snapshot=json.dumps(
            {
                "schemas": [
                    {
                        "schema_key": row["schema_key"],
                        "version": row["version"],
                        "canonical_hash": row["canonical_hash"],
                        "definition": json.loads(row["definition_json"]),
                    }
                    for row in rows
                ]
            }
        ),
    )
    routed = resolve_frozen_dimension_entry(
        bundle=bundle,
        aesthetic={"scoring_profile": "not-v1.3"},
        schema_key=SPACE_SCHEMA_KEY,
        version=ACTIVE_V13_VERSION,
    )
    assert routed["version"] == ACTIVE_V13_VERSION
    assert routed["version"] != HISTORICAL_DEFAULT_VERSION


def test_dimension_contract_error_preserves_worker_error_code() -> None:
    error = ProductionDimensionContractError(
        "dimension_contract_not_executable",
        "维度合同不可执行",
    )

    failure = worker._technical_failure_from_exception(error)

    assert failure.error_type == "dimension_contract_not_executable"
    assert failure.retryable is False
