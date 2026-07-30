from __future__ import annotations

import json

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
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
    MaterialPackage,
    MaterialPackageItem,
    MigrationItem,
    MigrationRun,
    ModelConfig,
    PromptVersion,
    ReviewPanel,
    ReviewWorkflowPolicy,
    SamplingPolicy,
    User,
)
from app.security import hash_password
from app.strategy_bundle import build_strategy_snapshot, get_or_create_bundle


def _client_with_result():
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
        username="workflow-owner",
        password_hash="unused",
        display_name="工作流管理员",
    )
    asset = Asset(
        original_name="workflow.jpg",
        stored_name="workflow.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="e" * 64,
    )
    policy = db.get(ReviewWorkflowPolicy, 1)
    assert policy is not None
    policy.initial_reviewers = 1
    prompt = PromptVersion(
        stage="A",
        name="初审策略测试提示词",
        version="workflow-A1",
        system_prompt="system prompt",
        user_prompt="user prompt",
        rubric_version="R1",
        status="published",
    )
    model = ModelConfig(
        name="workflow model",
        model_id="workflow-model",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
    )
    sampling_policy = SamplingPolicy(id=1, revision=1)
    db.add_all([user, asset, prompt, model, sampling_policy])
    db.flush()
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt,
        prompt_b=None,
        rubric_version="R1",
        engine_version="E1",
        risk_review_version=None,
        sampling_policy=sampling_policy,
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
            bundle, prompt, None, sampling_policy
        ),
        precheck_json=json.dumps(
            {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "住宅设计",
                },
                "image_quality": {"quality_severity": "normal"},
            },
            ensure_ascii=False,
        ),
        aesthetic_json=json.dumps(
            {
                "dimensions": {
                    key: {"grade": 4}
                    for key in (
                        "composition_viewpoint",
                        "lighting_atmosphere",
                        "color_material",
                        "spatial_design_furnishing",
                        "visual_hierarchy",
                        "detail_completion",
                        "inspiration_reference",
                        "presentation_integrity",
                    )
                }
            },
            ensure_ascii=False,
        ),
        scoring_json=json.dumps({"formal": True, "level": "L3"}),
        raw_response_a="{}",
        score=75,
        level="L3",
        confidence=0.5,
        needs_review=True,
        model_id="workflow-model",
        prompt_a_version=prompt.version,
        rubric_version="R1",
        engine_version="E1",
    )
    db.add(result)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    def override_current_user(
        test_reviewer: str | None = Header(
            default=None, alias="X-Test-Reviewer"
        ),
    ) -> User:
        if test_reviewer is None:
            return user
        return User(
            username=test_reviewer,
            password_hash="unused",
            display_name="测试审核员",
            is_active=True,
            is_admin=True,
        )

    app.dependency_overrides[current_user] = override_current_user
    return engine, db, TestClient(app), result


def _close(engine, db) -> None:
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_review_workflow_policy_defaults_to_one_and_accepts_odd_growth() -> None:
    engine, db, client, _result = _client_with_result()
    try:
        current = client.get("/api/review-workflow-policy")
        assert current.status_code == 200
        assert current.json()["initial_reviewers"] == 1
        assert current.json()["supported_reviewer_counts"] == [1, 3, 5, 7, 9]

        updated = client.put(
            "/api/review-workflow-policy",
            json={"initial_reviewers": 5},
        )
        assert updated.status_code == 200
        assert updated.json()["initial_reviewers"] == 5
        assert updated.json()["revision"] == 2

        rejected = client.put(
            "/api/review-workflow-policy",
            json={"initial_reviewers": 2},
        )
        assert rejected.status_code == 422
    finally:
        _close(engine, db)


def test_single_reviewer_vote_finishes_initial_review_without_second_round() -> None:
    engine, db, client, result = _client_with_result()
    try:
        opened = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["required_reviewers"] == 1

        completed = client.post(
            f"/api/evaluations/{result.id}/review-panel/votes",
            json={
                "reviewer_name": "唯一审核员",
                "decision": "approved",
                "expected_panel_revision": 0,
                "note": "单人初审确认",
                "corrections": [],
            },
        )
        assert completed.status_code == 200, completed.text
        payload = completed.json()
        assert payload["status"] == "completed"
        assert payload["submitted_count"] == 1
        assert payload["final_truth"]["resolution_mode"] == "single_reviewer"

        db.expire_all()
        panel = db.query(ReviewPanel).filter_by(evaluation_id=result.id).one()
        final_review = db.get(HumanReview, panel.final_review_id)
        assert final_review is not None
        assert final_review.stage == "initial"
        assert panel.evaluation.review_stage == "completed"
    finally:
        _close(engine, db)


def test_login_session_identity_overrides_spoofed_vote_and_query_name() -> None:
    engine, db, client, result = _client_with_result()
    try:
        user = db.query(User).filter_by(username="workflow-owner").one()
        user.password_hash = hash_password("fixture-password")
        policy = db.get(ReviewWorkflowPolicy, 1)
        assert policy is not None
        policy.initial_reviewers = 3
        db.commit()
        app.dependency_overrides.pop(current_user)

        login = client.post(
            "/api/auth/login",
            json={
                "username": "workflow-owner",
                "password": "fixture-password",
            },
        )
        assert login.status_code == 200, login.text
        assert login.json()["username"] == "workflow-owner"
        opened = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        )
        assert opened.status_code == 200, opened.text

        voted = client.post(
            f"/api/evaluations/{result.id}/review-panel/votes",
            json={
                "reviewer_name": "伪造审核员",
                "decision": "approved",
                "expected_panel_revision": 0,
                "note": "真实登录会话提交",
                "corrections": [],
            },
        )
        assert voted.status_code == 200, voted.text
        assert voted.json()["my_vote"]["decision"] == "approved"

        db.expire_all()
        panel = db.query(ReviewPanel).filter_by(
            evaluation_id=result.id
        ).one()
        stored_vote = db.query(HumanReview).filter_by(
            panel_id=panel.id
        ).one()
        assert stored_vote.reviewer_name == "workflow-owner"

        fetched = client.get(
            f"/api/evaluations/{result.id}/review-panel",
            params={"reviewer_name": "伪造审核员"},
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["my_vote"]["id"] == stored_vote.id
        assert fetched.json()["votes"] == []
    finally:
        _close(engine, db)


def test_panel_size_is_snapshotted_when_global_policy_changes() -> None:
    engine, db, client, result = _client_with_result()
    try:
        opened = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        )
        assert opened.status_code == 200
        assert opened.json()["required_reviewers"] == 1

        changed = client.put(
            "/api/review-workflow-policy",
            json={"initial_reviewers": 3},
        )
        assert changed.status_code == 200

        same_panel = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        )
        assert same_panel.status_code == 200
        assert same_panel.json()["required_reviewers"] == 1

        conflicting_override = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={"required_reviewers": 3},
        )
        assert conflicting_override.status_code == 409
    finally:
        _close(engine, db)


def test_three_reviewer_panel_waits_for_all_votes_then_uses_majority() -> None:
    engine, db, client, result = _client_with_result()
    try:
        changed = client.put(
            "/api/review-workflow-policy",
            json={"initial_reviewers": 3},
        )
        assert changed.status_code == 200
        opened = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        )
        assert opened.status_code == 200
        assert opened.json()["required_reviewers"] == 3

        for index, reviewer in enumerate(("reviewer-a", "reviewer-b", "reviewer-c")):
            response = client.post(
                f"/api/evaluations/{result.id}/review-panel/votes",
                headers={"X-Test-Reviewer": reviewer},
                json={
                    "reviewer_name": "伪造姓名",
                    "decision": "approved",
                    "expected_panel_revision": index,
                    "note": "多人盲审确认",
                    "corrections": [],
                },
            )
            assert response.status_code == 200, response.text
            expected_status = "completed" if index == 2 else "collecting"
            assert response.json()["status"] == expected_status

        final_truth = response.json()["final_truth"]
        assert final_truth["required_reviewers"] == 3
        assert final_truth["resolution_mode"] == "majority_consensus"
    finally:
        _close(engine, db)


def test_database_rejects_even_review_panel_size() -> None:
    engine, db, _client, result = _client_with_result()
    try:
        db.add(
            ReviewPanel(
                evaluation_id=result.id,
                required_reviewers=2,
                status="collecting",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        _close(engine, db)


def test_unresolved_panel_stays_in_initial_workbench_for_lead_adjudication() -> None:
    engine, db, client, result = _client_with_result()
    try:
        changed = client.put(
            "/api/review-workflow-policy",
            json={"initial_reviewers": 3},
        )
        assert changed.status_code == 200
        assert client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        ).status_code == 200

        for index, (reviewer, decision) in enumerate(
            (
                ("reviewer-a", "approved"),
                ("reviewer-b", "rejected"),
                ("reviewer-c", "corrected"),
            )
        ):
            corrections = (
                [
                    {
                        "target_type": "dimension",
                        "field_key": "color_material",
                        "model_value": 4,
                        "human_value": 3,
                        "reason_codes": ["panel-test"],
                        "note": "测试无多数",
                    }
                ]
                if decision == "corrected"
                else []
            )
            response = client.post(
                f"/api/evaluations/{result.id}/review-panel/votes",
                headers={"X-Test-Reviewer": reviewer},
                json={
                    "reviewer_name": "伪造姓名",
                    "decision": decision,
                    "expected_panel_revision": index,
                    "note": "独立盲审",
                    "corrections": corrections,
                },
            )
            assert response.status_code == 200, response.text

        assert response.json()["status"] == "lead_adjudication"
        db.expire_all()
        assert db.get(EvaluationResult, result.id).review_stage == "initial"

        adjudicated = client.post(
            f"/api/evaluations/{result.id}/review-panel/lead-adjudication",
            headers={"X-Test-Reviewer": "workflow-lead"},
            json={
                "lead_reviewer_name": "伪造主审",
                "decision": "approved",
                "expected_panel_revision": 3,
                "note": "主审在初审工作台裁决",
                "corrections": [],
            },
        )
        assert adjudicated.status_code == 200, adjudicated.text
        assert adjudicated.json()["status"] == "completed"
        db.expire_all()
        panel = db.query(ReviewPanel).filter_by(
            evaluation_id=result.id
        ).one()
        assert db.get(HumanReview, panel.final_review_id).stage == "initial"
        assert panel.evaluation.review_stage == "completed"
        assert db.get(HumanReview, panel.final_review_id).reviewer_name == (
            "workflow-lead"
        )
    finally:
        _close(engine, db)


def test_migration_review_uses_authenticated_username() -> None:
    engine, db, client, result = _client_with_result()
    try:
        run = MigrationRun(
            name="审核身份迁移测试",
            baseline_model_id="baseline-model",
            candidate_model_id="candidate-model",
            sample_size=1,
            created_by="workflow-owner",
        )
        db.add(run)
        db.flush()
        item = MigrationItem(
            run_id=run.id,
            asset_id=result.asset_id,
            baseline_result_id=result.id,
            candidate_result_id=result.id,
            status="review",
            requires_review=True,
        )
        db.add(item)
        db.commit()

        reviewed = client.post(
            f"/api/migrations/{run.id}/items/{item.id}/review",
            json={
                "verdict": "same",
                "reviewer_name": "伪造迁移审核员",
                "note": "身份应取当前登录账号",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        db.refresh(item)
        assert item.reviewer_name == "workflow-owner"
    finally:
        _close(engine, db)


def test_material_package_status_and_frozen_prompt_metrics_are_readable() -> None:
    engine, db, client, result = _client_with_result()
    try:
        package = MaterialPackage(
            package_key="package:test",
            name="测试素材包",
            source="manual_upload",
            created_by="workflow-owner",
        )
        db.add(package)
        db.flush()
        db.add(
            MaterialPackageItem(
                package_id=package.id,
                asset_id=result.asset_id,
                original_name="workflow.jpg",
                duplicate=False,
                position=1,
            )
        )
        db.commit()
        package_response = client.get(
            f"/api/material-packages?prompt_id={result.job.prompt_a_id}"
        )
        assert package_response.status_code == 200
        assert package_response.json()["items"][0]["status_summary"] == {
            "not_evaluated": 0,
            "evaluated_old": 0,
            "evaluated_current": 1,
            "queued": 0,
            "running": 0,
            "failed": 0,
        }

        assert client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        ).status_code == 200
        assert client.post(
            f"/api/evaluations/{result.id}/review-panel/votes",
            json={
                "reviewer_name": "唯一审核员",
                "decision": "approved",
                "expected_panel_revision": 0,
                "note": "形成冻结指标",
                "corrections": [],
            },
        ).status_code == 200
        snapshot = client.post(
            f"/api/prompts/{result.job.prompt_a_id}/metric-snapshots",
            json={
                "task_set_key": "workflow-test-batch",
                "evaluation_ids": [result.id],
            },
        )
        assert snapshot.status_code == 200, snapshot.text
        metrics = snapshot.json()["metrics"]
        assert metrics["N"] == 1
        assert metrics["reviewed_sample_count"] == 1
        assert metrics["sample_accuracy"] == 1
        listed = client.get(
            f"/api/prompts/{result.job.prompt_a_id}/metric-snapshots"
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["task_set_key"] == (
            "workflow-test-batch"
        )
    finally:
        _close(engine, db)
