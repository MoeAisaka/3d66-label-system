from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    User,
)
from app.regression import reviewed_truth_snapshot
from app.strategy_bundle import build_strategy_snapshot, get_or_create_bundle


def _client_with_result(*, level: str = "L3", needs_review: bool = False):
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
        username="review-owner",
        password_hash="unused",
        display_name="审核员",
    )
    asset = Asset(
        original_name="review.jpg",
        stored_name="review.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="b" * 64,
    )
    prompt = PromptVersion(
        stage="A",
        name="单提示词",
        version="review-A1",
        system_prompt="system prompt for staged review",
        user_prompt="user prompt",
        rubric_version="R1",
        status="published",
    )
    model = ModelConfig(
        name="review model",
        model_id="review-model",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
    )
    policy = SamplingPolicy(id=1, revision=1)
    db.add_all([user, asset, prompt, model, policy])
    db.flush()
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt,
        prompt_b=None,
        rubric_version="R1",
        engine_version="E1",
        risk_review_version=None,
        sampling_policy=policy,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        prompt_a_id=prompt.id,
        strategy_bundle_id=bundle.id,
        status="completed",
        stage="done",
        progress=100,
    )
    db.add(job)
    db.flush()
    result = EvaluationResult(
        asset_id=asset.id,
        job_id=job.id,
        strategy_bundle_id=bundle.id,
        strategy_snapshot_json=build_strategy_snapshot(
            bundle, prompt, None, policy
        ),
        precheck_json=json.dumps(
            {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "住宅设计",
                },
                "image_quality": {"quality_severity": "normal"},
            }
        ),
        aesthetic_json=json.dumps(
            {
                "dimensions": {
                    "color_material": {"grade": 4},
                },
                "decision_rules": {},
            }
        ),
        scoring_json=json.dumps({"formal": True, "level": level}),
        raw_response_a="{}",
        score=75,
        level=level,
        confidence=0.95,
        needs_review=needs_review,
        model_id="review-model",
        prompt_a_version=prompt.version,
        prompt_b_version=None,
        rubric_version="R1",
        engine_version="E1",
    )
    db.add(result)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    return engine, db, TestClient(app), result


def _close(engine, db) -> None:
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _review_payload(
    *,
    stage: str,
    revision: int,
    decision: str = "approved",
) -> dict:
    return {
        "reviewer_name": f"{stage}-reviewer",
        "decision": decision,
        "expected_stage": stage,
        "expected_review_revision": revision,
        "note": f"{stage} conclusion",
        "corrections": [],
    }


def test_ordinary_approval_completes_and_exposes_append_only_history() -> None:
    engine, db, client, result = _client_with_result()
    try:
        response = client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(stage="initial", revision=0),
        )
        assert response.status_code == 200, response.text
        assert response.json()["review_stage"] == "completed"
        assert response.json()["review_revision"] == 1
        detail = client.get(f"/api/evaluations/{result.id}").json()["evaluation"]
        assert detail["review_stage"] == "completed"
        assert detail["review_truth_status"] == "completed"
        assert [item["stage"] for item in detail["review_history"]] == ["initial"]
    finally:
        _close(engine, db)


def test_high_risk_agreement_needs_secondary_then_completes_and_stale_is_409() -> None:
    engine, db, client, result = _client_with_result(
        level="L4", needs_review=True
    )
    try:
        initial = client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(stage="initial", revision=0),
        )
        assert initial.status_code == 200
        assert initial.json()["review_stage"] == "secondary"
        provisional = client.get(
            f"/api/evaluations/{result.id}"
        ).json()["evaluation"]
        assert provisional["review_truth_status"] == "provisional"
        db.refresh(result)
        with pytest.raises(ValueError, match="未经人工确认"):
            reviewed_truth_snapshot(result, "stable_control")

        stale = client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(stage="initial", revision=0),
        )
        assert stale.status_code == 409
        secondary = client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(stage="secondary", revision=1),
        )
        assert secondary.status_code == 200, secondary.text
        assert secondary.json()["review_stage"] == "completed"
        assert db.query(HumanReview).filter_by(evaluation_id=result.id).count() == 2
    finally:
        _close(engine, db)


def test_disagreement_routes_to_arbitration_and_arbitration_completes() -> None:
    engine, db, client, result = _client_with_result(
        level="L5", needs_review=True
    )
    try:
        client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(stage="initial", revision=0),
        )
        second = client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(
                stage="secondary", revision=1, decision="rejected"
            ),
        )
        assert second.status_code == 200, second.text
        assert second.json()["review_stage"] == "arbitration"
        arbitration = client.post(
            f"/api/evaluations/{result.id}/review",
            json=_review_payload(stage="arbitration", revision=2),
        )
        assert arbitration.status_code == 200, arbitration.text
        assert arbitration.json()["review_stage"] == "completed"
        assert arbitration.json()["review_revision"] == 3
        history = client.get(
            f"/api/evaluations/{result.id}"
        ).json()["evaluation"]["review_history"]
        assert [item["stage"] for item in history] == [
            "initial",
            "secondary",
            "arbitration",
        ]
    finally:
        _close(engine, db)
