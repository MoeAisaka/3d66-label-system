from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.category_pipeline import default_pipeline
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    AutomationOptimizationRun,
    EvaluationJob,
    EvaluationCategoryProfile,
    EvaluationPackage,
    EvaluationProductionRun,
    EvaluationResult,
    HumanReview,
    MaterialPackage,
    MaterialPackageItem,
    ModelConfig,
    OptimizationCaseQueue,
    PromptVersion,
    ReviewPanel,
    SamplingPolicy,
    User,
)
from app.strategy_bundle import (
    build_evaluation_strategy_snapshot,
    get_or_create_bundle,
)


@contextmanager
def _context(
    *,
    asset_count: int = 2,
    include_model: bool = True,
    include_prompts: bool = True,
) -> Iterator[dict[str, Any]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        user = User(
            username="production-manager",
            password_hash="unused",
            display_name="生产管理员",
            is_admin=True,
        )
        package = MaterialPackage(
            package_key="production-source",
            name="待生产素材包",
            category_key="space_image",
            source="manual_upload",
            created_by=user.username,
        )
        db.add_all([user, package])
        db.flush()
        assets: list[Asset] = []
        for index in range(asset_count):
            asset = Asset(
                original_name=f"room-{index}.jpg",
                stored_name=f"room-{index}.jpg",
                mime_type="image/jpeg",
                size_bytes=100,
                sha256=f"{index + 1:064x}",
                category_key="space_image",
            )
            db.add(asset)
            db.flush()
            db.add(
                MaterialPackageItem(
                    package_id=package.id,
                    asset_id=asset.id,
                    original_name=asset.original_name,
                    duplicate=False,
                    position=index + 1,
                )
            )
            assets.append(asset)
        prompt_a = PromptVersion(
            stage="A",
            name="生产 A",
            version="production-A-v1",
            system_prompt="生产 A system",
            user_prompt="生产 A user",
            rubric_version="rubric-v2.1",
            status="published",
        )
        prompt_b = PromptVersion(
            stage="B",
            name="生产 B",
            version="production-B-v1",
            system_prompt="生产 B system",
            user_prompt="生产 B user",
            rubric_version="rubric-v2.1",
            status="published",
        )
        if include_prompts:
            db.add_all([prompt_a, prompt_b])
        model = ModelConfig(
            name="生产模型",
            model_id="production-model",
            base_url="https://model.example.test/v1",
            api_path="/chat/completions",
            encrypted_api_key="encrypted-reference-must-not-escape",
            active=True,
        )
        if include_model:
            db.add(model)
        db.commit()

    def override_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        yield {
            "client": client,
            "sessions": sessions,
            "user": user,
            "package": package,
            "assets": assets,
            "prompt_a": prompt_a,
            "prompt_b": prompt_b,
            "model": model,
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _create(client: TestClient, package_id: int, key: str = "production-request-1"):
    return client.post(
        "/api/evaluation-production-runs",
        json={
            "material_package_id": package_id,
            "category_key": "space_image",
            "idempotency_key": key,
        },
    )


def _complete_job(
    db: Session,
    *,
    job: EvaluationJob,
    model: ModelConfig,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion,
    needs_review: bool,
) -> EvaluationResult:
    policy = db.get(SamplingPolicy, 1)
    if policy is None:
        policy = SamplingPolicy(id=1, revision=1)
        db.add(policy)
        db.flush()
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version="rubric-v2.1",
        engine_version="engine-test-v1",
        risk_review_version=None,
        sampling_policy=policy,
    )
    aesthetic = {"scoring_profile": "space_aesthetic_v1.3", "dimensions": {}}
    result = EvaluationResult(
        asset_id=job.asset_id,
        job_id=job.id,
        strategy_bundle_id=bundle.id,
        strategy_snapshot_json=build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=policy,
            aesthetic=aesthetic,
        ),
        precheck_json=json.dumps(
            {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "住宅",
                },
                "image_quality": {"quality_severity": "normal"},
            },
            ensure_ascii=False,
        ),
        aesthetic_json=json.dumps(aesthetic, ensure_ascii=False),
        scoring_json='{"formal":true}',
        raw_response_a='{"provider":"raw-a"}',
        raw_response_b='{"provider":"raw-b"}',
        score=80,
        level="L2",
        confidence=0.6 if needs_review else 0.95,
        needs_review=needs_review,
        review_stage="initial" if needs_review else "completed",
        model_id=bundle.model_id,
        prompt_a_version=prompt_a.version,
        prompt_b_version=prompt_b.version,
        rubric_version=bundle.rubric_version,
        engine_version=bundle.engine_version,
    )
    job.status = "completed"
    job.stage = "done"
    job.progress = 100
    db.add(result)
    db.flush()
    return result


def test_create_is_idempotent_and_creates_one_job_per_available_asset() -> None:
    with _context(asset_count=3) as fixture:
        first = _create(fixture["client"], fixture["package"].id)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["duplicate"] is False
        assert body["status"] == "queued"
        assert body["job_counts"]["total"] == 3
        assert len(body["job_ids"]) == 3
        assert body["evaluation_package_id"] is None
        assert len(body["category"]["configuration_hash"]) == 64
        with fixture["sessions"]() as db:
            run = db.get(EvaluationProductionRun, body["id"])
            assert run.category_profile_hash == body["category"]["configuration_hash"]
            assert "encrypted-reference-must-not-escape" not in run.category_profile_snapshot_json

        duplicate = _create(fixture["client"], fixture["package"].id)
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["id"] == body["id"]
        assert duplicate.json()["job_ids"] == body["job_ids"]

        changed = fixture["client"].post(
            "/api/evaluation-production-runs",
            json={
                "material_package_id": fixture["package"].id,
                "category_key": "material_image",
                "idempotency_key": "production-request-1",
            },
        )
        assert changed.status_code == 409


def test_rejects_cross_category_empty_materials_and_incomplete_configuration() -> None:
    with _context(asset_count=1) as fixture:
        cross = fixture["client"].post(
            "/api/evaluation-production-runs",
            json={
                "material_package_id": fixture["package"].id,
                "category_key": "material_image",
                "idempotency_key": "cross-category",
            },
        )
        assert cross.status_code == 422

        with fixture["sessions"]() as db:
            empty = MaterialPackage(
                package_key="empty-package",
                name="空包",
                category_key="space_image",
                source="manual_upload",
                created_by=fixture["user"].username,
            )
            db.add(empty)
            db.commit()
            empty_id = empty.id
        rejected = _create(fixture["client"], empty_id, "empty-materials")
        assert rejected.status_code == 409
        assert "可用素材" in rejected.text

    with _context(asset_count=1, include_model=False) as fixture:
        missing_model = _create(
            fixture["client"], fixture["package"].id, "missing-model"
        )
        assert missing_model.status_code == 409
        assert "主评测模型" in missing_model.text

    with _context(asset_count=1, include_prompts=False) as fixture:
        missing_prompt = _create(
            fixture["client"], fixture["package"].id, "missing-prompt"
        )
        assert missing_prompt.status_code in {400, 409}
        assert "提示词" in missing_prompt.text


def test_reconcile_uses_job_review_and_automation_facts_and_recovers_from_blocker() -> None:
    with _context(asset_count=2) as fixture:
        created = _create(fixture["client"], fixture["package"].id)
        run_id = created.json()["id"]
        with fixture["sessions"]() as db:
            run = db.get(EvaluationProductionRun, run_id)
            jobs = db.scalars(
                select(EvaluationJob).where(EvaluationJob.batch_key == run.batch_key)
            ).all()
            jobs[0].status = "processing"
            db.commit()
        evaluating = fixture["client"].post(
            f"/api/evaluation-production-runs/{run_id}/reconcile"
        )
        assert evaluating.status_code == 200
        assert evaluating.json()["status"] == "evaluating"

        with fixture["sessions"]() as db:
            run = db.get(EvaluationProductionRun, run_id)
            jobs = db.scalars(
                select(EvaluationJob)
                .where(EvaluationJob.batch_key == run.batch_key)
                .order_by(EvaluationJob.id.asc())
            ).all()
            results = [
                _complete_job(
                    db,
                    job=job,
                    model=db.get(ModelConfig, fixture["model"].id),
                    prompt_a=db.get(PromptVersion, fixture["prompt_a"].id),
                    prompt_b=db.get(PromptVersion, fixture["prompt_b"].id),
                    needs_review=index == 0,
                )
                for index, job in enumerate(jobs)
            ]
            panel = ReviewPanel(
                evaluation_id=results[0].id,
                required_reviewers=1,
                status="collecting",
            )
            db.add(panel)
            db.commit()
            panel_id = panel.id
            reviewed_result_id = results[0].id
        first_review = fixture["client"].get(
            f"/api/evaluation-production-runs/{run_id}"
        )
        assert first_review.status_code == 200
        assert first_review.json()["status"] == "first_review"
        assert first_review.json()["pending_first_review_count"] == 1

        with fixture["sessions"]() as db:
            panel = db.get(ReviewPanel, panel_id)
            result = db.get(EvaluationResult, reviewed_result_id)
            final_review = HumanReview(
                evaluation_id=result.id,
                reviewer_name="一审定案",
                stage="initial",
                decision="corrected",
                corrected_level="L2",
                corrected_score=80,
                note="纠偏完成",
                corrections_json="[]",
            )
            db.add(final_review)
            db.flush()
            panel.status = "completed"
            panel.final_review_id = final_review.id
            panel.final_truth_json = '{"decision":"corrected"}'
            result.review_stage = "completed"
            result.needs_review = False
            case = OptimizationCaseQueue(
                category_key="space_image",
                idempotency_key=f"production-case:{run_id}",
                evaluation_id=result.id,
                final_review_id=final_review.id,
                source_type="human_review",
                prompt_version=result.prompt_b_version,
                severity="P2",
                case_json='{"schema_version":"optimization-case-v1"}',
                status="pending",
            )
            db.add(case)
            db.commit()
            case_id = case.id
        blocked = fixture["client"].post(
            f"/api/evaluation-production-runs/{run_id}/reconcile"
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"
        assert blocked.json()["blockers"][0]["code"] == "automation_disabled"

        with fixture["sessions"]() as db:
            automation = AutomationOptimizationRun(
                category_key="material_image",
                run_key=f"production-automation:{run_id}",
                base_prompt_version="production-B-v1",
                policy_revision=1,
                status="processing",
                dry_run=False,
                trigger_reason="case_threshold",
                case_ids_json=json.dumps([case_id]),
                frozen_input_json="{}",
                result_json="{}",
                created_by="worker",
            )
            db.add(automation)
            db.flush()
            case = db.get(OptimizationCaseQueue, case_id)
            case.automation_run_id = automation.id
            case.status = "processing"
            db.commit()
        mismatched = fixture["client"].post(
            f"/api/evaluation-production-runs/{run_id}/reconcile"
        )
        assert mismatched.status_code == 200
        assert mismatched.json()["status"] == "failed"
        assert mismatched.json()["error"]["code"] == "automation_category_mismatch"

        with fixture["sessions"]() as db:
            db.get(AutomationOptimizationRun, automation.id).category_key = "space_image"
            db.commit()
        recovered = fixture["client"].post(
            f"/api/evaluation-production-runs/{run_id}/reconcile"
        )
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "optimizing"
        assert recovered.json()["blockers"] == []
        assert recovered.json()["automation_run_id"] == automation.id
        assert recovered.json()["automation"]["lifecycle_status"] == "processing"


def test_reconcile_links_real_final_package_without_approving_or_publishing() -> None:
    from test_evaluation_packages import _close, _fixture

    fixture = _fixture()
    client = fixture["client"]
    db = fixture["db"]
    try:
        source_item = fixture["sample_items"][0]
        material = MaterialPackage(
            package_key="production-final-source",
            name="最终包生产来源",
            category_key="space_image",
            source="manual_upload",
            created_by=fixture["user"].username,
        )
        db.add(material)
        db.flush()
        db.add(
            MaterialPackageItem(
                package_id=material.id,
                asset_id=source_item.asset_id,
                original_name=source_item.asset.original_name,
                duplicate=False,
                position=1,
            )
        )
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        if profile is None:
            profile = EvaluationCategoryProfile(
                category_key="space_image",
                display_name="空间图片",
                status="active",
                allowed_mime_types_json='["image/jpeg"]',
                preprocess_config_json='{"preprocess":"image"}',
                pipeline_config_json=json.dumps(
                    default_pipeline("space_image"), ensure_ascii=False
                ),
                created_by=fixture["user"].username,
            )
            db.add(profile)
        profile.rubric_version = "rubric-package-v1"
        # This case exercises production-run reconciliation, not the separate
        # dimension-candidate calibration gate used by the shared fixture.
        profile.dimension_schema_key = None
        profile.dimension_schema_version = None
        db.commit()
        created = _create(client, material.id, "production-final-package")
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]
        run = db.get(EvaluationProductionRun, run_id)
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.batch_key == run.batch_key))
        source = db.get(EvaluationResult, source_item.source_result_id)
        production_result = EvaluationResult(
            asset_id=source.asset_id,
            job_id=job.id,
            strategy_bundle_id=source.strategy_bundle_id,
            strategy_snapshot_json=source.strategy_snapshot_json,
            preprocess_json=source.preprocess_json,
            precheck_json=source.precheck_json,
            aesthetic_json=source.aesthetic_json,
            scoring_json=source.scoring_json,
            raw_response_a='{"production":"raw response"}',
            raw_response_b='{"production":"raw response"}',
            score=source.score,
            level=source.level,
            confidence=source.confidence,
            needs_review=False,
            review_stage="completed",
            model_id=source.model_id,
            prompt_a_version=source.prompt_a_version,
            prompt_b_version=source.prompt_b_version,
            rubric_version=source.rubric_version,
            engine_version=source.engine_version,
            risk_review_version=source.risk_review_version,
        )
        job.status = "completed"
        job.stage = "done"
        job.progress = 100
        db.add(production_result)
        db.flush()
        final_review = HumanReview(
            evaluation_id=production_result.id,
            reviewer_name="一审定案",
            stage="initial",
            decision="corrected",
            corrected_level=production_result.level,
            corrected_score=production_result.score,
            corrections_json="[]",
            note="进入自动优化",
        )
        db.add(final_review)
        db.flush()
        db.add(
            OptimizationCaseQueue(
                category_key="space_image",
                idempotency_key=f"production-final-case:{run_id}",
                evaluation_id=production_result.id,
                final_review_id=final_review.id,
                source_type="human_review",
                prompt_version=fixture["automation"].base_prompt_version,
                severity="P2",
                case_json='{"schema_version":"optimization-case-v1"}',
                status="completed",
                automation_run_id=fixture["automation"].id,
            )
        )
        db.commit()

        reconciled = client.post(
            f"/api/evaluation-production-runs/{run_id}/reconcile"
        )
        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["status"] == "awaiting_review"
        assert body["evaluation_package_id"] is not None
        package = db.get(EvaluationPackage, body["evaluation_package_id"])
        assert package.status == "awaiting_review"
        assert package.reviewed_by is None
        assert package.published_at is None
        assert package.automation_run_id == fixture["automation"].id
        assert package.regression_run_id == fixture["regression"].id

        serialized = json.dumps(body, ensure_ascii=False).lower()
        for forbidden in (
            "raw response",
            "encrypted-reference",
            "api_key",
            "authorization",
        ):
            assert forbidden not in serialized
    finally:
        _close(fixture["engine"], fixture["db"])
