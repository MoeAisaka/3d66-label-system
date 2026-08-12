from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import (
    Asset,
    BaselineRegressionRun,
    BaselineSet,
    BaselineSetItem,
    EvaluationCategoryProfile,
    EvaluationJob,
    ModelConfig,
    PromptVersion,
    User,
)
from tests.v3_contract_fixtures import add_active_v3_contract


def test_baseline_subset_requires_explicit_category_context_without_asset_mutation() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(username="context-tester", password_hash="unused")
    asset_one = Asset(
        original_name="one.jpg",
        stored_name="one.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="1" * 64,
        category_key="space_image",
        status="uploaded",
    )
    asset_two = Asset(
        original_name="two.jpg",
        stored_name="two.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="2" * 64,
        category_key="space_image",
        status="uploaded",
    )
    prompt_a = PromptVersion(
        stage="A",
        category_key="inspiration_image",
        pipeline_scope="shared",
        name="A rev4 frozen",
        version="inspiration-a-v3-hard-defect-recall-rev4-20260805",
        system_prompt="A",
        user_prompt="",
        rubric_version="inspiration-rubric-v1",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        category_key="inspiration_image",
        pipeline_scope="shared",
        name="B evidence frozen",
        version="inspiration-b-v4-evidence-contract-20260806",
        system_prompt="B",
        user_prompt="",
        rubric_version="inspiration-rubric-v1",
        status="published",
    )
    model = ModelConfig(
        name="context-model",
        provider="doubao",
        base_url="https://example.test",
        api_path="/chat",
        model_id="vision",
        active=True,
    )
    db.add_all([user, asset_one, asset_two, prompt_a, prompt_b, model])
    db.flush()
    profile = EvaluationCategoryProfile(
        category_key="inspiration_image",
        display_name="灵感图",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json="{}",
        rubric_version="inspiration-rubric-v1",
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        model_config_id=model.id,
    )
    baseline_set = BaselineSet(
        category_key="inspiration_image",
        name="frozen-context-canary",
        default_expected_level="L1",
        fingerprint="f" * 64,
        created_by=user.username,
    )
    db.add_all([profile, baseline_set])
    db.flush()
    item_one = BaselineSetItem(
        baseline_set_id=baseline_set.id,
        asset_id=asset_one.id,
        expected_level="L1",
        asset_snapshot_json=json.dumps(
            {"id": asset_one.id, "category_key": "space_image"}
        ),
    )
    item_two = BaselineSetItem(
        baseline_set_id=baseline_set.id,
        asset_id=asset_two.id,
        expected_level="L2",
        asset_snapshot_json=json.dumps(
            {"id": asset_two.id, "category_key": "space_image"}
        ),
    )
    db.add_all([item_one, item_two])
    add_active_v3_contract(db, "inspiration_image")
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        missing_context = client.post(
            f"/api/baseline-sets/{baseline_set.id}/runs",
            json={"baseline_item_ids": [item_two.id]},
        )
        assert missing_context.status_code == 422
        assert missing_context.json()["detail"]["code"] == "category_context_required"

        wrong_context = client.post(
            f"/api/baseline-sets/{baseline_set.id}/runs",
            json={
                "baseline_item_ids": [item_two.id],
                "category_context": {
                    "source": "baseline_set",
                    "category_key": "space_image",
                },
            },
        )
        assert wrong_context.status_code == 422
        assert wrong_context.json()["detail"]["code"] == "category_context_mismatch"

        created = client.post(
            f"/api/baseline-sets/{baseline_set.id}/runs",
            json={
                "baseline_item_ids": [item_two.id],
                "category_context": {
                    "source": "baseline_set",
                    "category_key": "inspiration_image",
                },
            },
        )
        assert created.status_code == 200, created.text
        run = db.get(BaselineRegressionRun, created.json()["id"])
        assert run is not None and run.total == 1
        assert [item.asset_id for item in run.items] == [asset_two.id]
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.id == run.items[0].job_id))
        assert job is not None
        assert job.category_key == "inspiration_image"
        assert job.asset_id == asset_two.id
        snapshot = json.loads(job.category_profile_snapshot_json)
        assert snapshot["category_context"] == {
            "source": "baseline_set",
            "category_key": "inspiration_image",
            "selected_baseline_item_ids": [item_two.id],
            "asset_category_mismatches": [asset_two.id],
        }
        db.refresh(asset_one)
        db.refresh(asset_two)
        assert asset_one.category_key == asset_two.category_key == "space_image"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
