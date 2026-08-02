from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    EvaluationCategoryProfile,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    ModelConfig,
    PromptOptimizationRun,
    PromptRegressionRun,
    PromptRegressionItem,
    PromptVersion,
    SampleSet,
    SampleSetItem,
    SamplingPolicy,
    StrategyBundle,
    User,
)
from app.strategy_bundle import (
    build_evaluation_strategy_snapshot,
    get_or_create_bundle,
)
from app.regression import compare_paired_results


DIMENSION_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)

RAW_PROVIDER_SENTINEL = "P0C_RAW_PROVIDER_SENTINEL"
MODEL_CREDENTIAL_SENTINEL = "P0C_MODEL_CREDENTIAL_SENTINEL"
PROMPT_CREDENTIAL_SENTINEL = "P0C_PROMPT_CREDENTIAL_SENTINEL"


@dataclass
class SeededPair:
    db: Session
    engine: object
    client: TestClient
    user: User
    sample_set: SampleSet
    sample_items: dict[str, SampleSetItem]
    baseline_bundle: StrategyBundle
    candidate_bundle: StrategyBundle
    baseline_results: dict[str, EvaluationResult]
    candidate_results: dict[str, EvaluationResult]
    prompts: dict[str, PromptVersion]
    policy: SamplingPolicy
    model: ModelConfig


def _precheck(
    *,
    scope: str = "in_scope",
    category: str = "住宅设计",
    quality: str = "normal",
) -> dict:
    return {
        "classification": {
            "scope_status": scope,
            "primary_category": category,
            "primary_confidence": 0.95,
        },
        "image_quality": {
            "quality_severity": quality,
            "confidence": 0.95,
            "evidence": ["清晰"],
        },
        "media_form": {
            "real_photo": {"status": "yes", "confidence": 0.95},
            "rendering": {"status": "no", "confidence": 0.95},
            "ai_generated": {"status": "no", "confidence": 0.95},
            "professional_photography": {
                "status": "yes",
                "confidence": 0.95,
            },
            "casual_snapshot": {"status": "no", "confidence": 0.95},
            "documentary_record": {"status": "no", "confidence": 0.95},
        },
    }


def _aesthetic(*, color_grade: int = 3, hard_gate: bool = False) -> dict:
    return {
        "dimensions": {
            key: {"grade": color_grade if key == "color_material" else 3}
            for key in DIMENSION_KEYS
        },
        "decision_rules": {
            "hard_gate_triggered": hard_gate,
            "hard_gate_target": "L1" if hard_gate else "none",
            "hard_gate_reasons": ["严重损坏"] if hard_gate else [],
            "level_cap": "L1" if hard_gate else "none",
            "level_cap_reasons": ["严重损坏"] if hard_gate else [],
        },
    }


def _scoring(level: str, *, formal: bool = True) -> dict:
    return {
        "formal": formal,
        "level": level,
        "caps": [],
    }


def _contract_result(
    result_id: int,
    *,
    scope: str = "in_scope",
    quality: str = "normal",
    level: str | None = "L2",
    hard_gate: bool = False,
    level_cap: str = "none",
    needs_review: bool = True,
) -> EvaluationResult:
    result = EvaluationResult(
        asset_id=1,
        job_id=result_id,
        precheck_json=json.dumps(
            _precheck(scope=scope, quality=quality), ensure_ascii=False
        ),
        aesthetic_json=json.dumps(
            {
                **_aesthetic(hard_gate=hard_gate),
                "dimensions": {},
                "decision_rules": {
                    "hard_gate_triggered": hard_gate,
                    "hard_gate_target": "L1" if hard_gate else "none",
                    "level_cap": level_cap,
                },
            },
            ensure_ascii=False,
        ),
        scoring_json=json.dumps(
            _scoring(level or "L1", formal=scope != "out_of_scope")
            | {"level": level},
            ensure_ascii=False,
        ),
        raw_response_a="{}",
        level=level,
        needs_review=needs_review,
        model_id="model",
        prompt_a_version="A",
        prompt_b_version="B",
        rubric_version="R",
        engine_version="E",
    )
    result.id = result_id
    return result


def _add_result(
    db: Session,
    *,
    asset: Asset,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion,
    policy: SamplingPolicy,
    level: str,
    color_grade: int = 3,
    scope: str = "in_scope",
    quality: str = "normal",
    needs_review: bool = False,
) -> EvaluationResult:
    job = EvaluationJob(
        asset_id=asset.id,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
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
        strategy_snapshot_json=build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=policy,
            aesthetic=_aesthetic(color_grade=color_grade),
        ),
        precheck_json=json.dumps(
            _precheck(scope=scope, quality=quality), ensure_ascii=False
        ),
        aesthetic_json=json.dumps(
            _aesthetic(color_grade=color_grade), ensure_ascii=False
        ),
        scoring_json=json.dumps(
            _scoring(level, formal=scope != "out_of_scope"),
            ensure_ascii=False,
        ),
        raw_response_a=json.dumps(
            {"provider_token": RAW_PROVIDER_SENTINEL}
        ),
        raw_response_b=json.dumps(
            {"provider_secret": RAW_PROVIDER_SENTINEL}
        ),
        raw_response_risk_review=json.dumps(
            {"authorization": RAW_PROVIDER_SENTINEL}
        ),
        score=65.0,
        level=level,
        confidence=0.95,
        needs_review=needs_review,
        model_id=bundle.model_id,
        prompt_a_version=bundle.prompt_a_version,
        prompt_b_version=bundle.prompt_b_version,
        risk_review_version=bundle.risk_review_version,
        rubric_version=bundle.rubric_version,
        engine_version=bundle.engine_version,
    )
    db.add(result)
    db.flush()
    return result


def _seed_pair(
    *,
    candidate_regression: bool = False,
    omit_candidate_role: str | None = None,
) -> SeededPair:
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
        username="paired-tester",
        password_hash="unused",
        display_name="配对测试员",
    )
    model = ModelConfig(
        name="测试模型",
        base_url=(
            "https://snapshot-user:snapshot-pass@example.test/v1"
            f"?api_key={MODEL_CREDENTIAL_SENTINEL}&region=cn"
        ),
        model_id="paired-model",
        encrypted_api_key=MODEL_CREDENTIAL_SENTINEL,
    )
    policy = SamplingPolicy(id=1, revision=7)
    prompts = {
        "baseline_a": PromptVersion(
            stage="A",
            name="基线A",
            version="paired-A-v1",
            system_prompt="baseline system A",
            user_prompt="baseline user A",
            rubric_version="paired-rubric-v1",
            status="published",
        ),
        "baseline_b": PromptVersion(
            stage="B",
            name="基线B",
            version="paired-B-v1",
            system_prompt="baseline system B",
            user_prompt="baseline user B",
            rubric_version="paired-rubric-v1",
            status="published",
        ),
        "candidate_a": PromptVersion(
            stage="A",
            name="候选A",
            version="paired-A-v2",
            system_prompt=(
                "candidate system A 保留正文\n"
                f"Authorization: Bearer {PROMPT_CREDENTIAL_SENTINEL}"
            ),
            user_prompt="candidate user A 保留全文",
            rubric_version="paired-rubric-v1",
            status="draft",
        ),
        "candidate_b": PromptVersion(
            stage="B",
            name="候选B",
            version="paired-B-v2",
            system_prompt="candidate system B",
            user_prompt="candidate user B",
            rubric_version="paired-rubric-v1",
            status="draft",
        ),
    }
    db.add_all([user, model, policy, *prompts.values()])
    db.flush()
    baseline_bundle = get_or_create_bundle(
        db,
        model,
        prompts["baseline_a"],
        prompts["baseline_b"],
        "paired-rubric-v1",
        "paired-engine-v1",
        None,
        policy,
    )
    candidate_bundle = get_or_create_bundle(
        db,
        model,
        prompts["candidate_a"],
        prompts["candidate_b"],
        "paired-rubric-v1",
        "paired-engine-v1",
        None,
        policy,
    )
    db.flush()
    category_profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "space_image"
        )
    )
    assert category_profile is not None
    category_profile.rubric_version = "paired-rubric-v1"

    sample_set = SampleSet(
        name="P0-C 冻结集",
        description="人工纠偏驱动配对回归",
        kind="golden",
        status="locked",
        created_by=user.username,
    )
    db.add(sample_set)
    db.flush()
    sample_items: dict[str, SampleSetItem] = {}
    baseline_results: dict[str, EvaluationResult] = {}
    candidate_results: dict[str, EvaluationResult] = {}
    for index, role in enumerate(
        ("target_error", "stable_control", "blind_holdout"), start=1
    ):
        asset = Asset(
            original_name=f"{role}.jpg",
            stored_name=f"{role}.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            sha256=(str(index) * 64)[:64],
            status="evaluated",
        )
        db.add(asset)
        db.flush()
        baseline = _add_result(
            db,
            asset=asset,
            bundle=baseline_bundle,
            prompt_a=prompts["baseline_a"],
            prompt_b=prompts["baseline_b"],
            policy=policy,
            level="L4" if role == "target_error" else "L3",
            color_grade=5 if role == "target_error" else 3,
        )
        review = HumanReview(
            evaluation_id=baseline.id,
            reviewer_name="人工审核员",
            decision="corrected" if role == "target_error" else "approved",
            corrected_level="L3" if role == "target_error" else None,
            corrected_score=65.0 if role == "target_error" else None,
            note="纠正目标错例" if role == "target_error" else "稳定对照已确认",
            corrections_json=(
                json.dumps(
                    [
                        {
                            "target_type": "dimension",
                            "field_key": "color_material",
                            "model_value": 5,
                            "human_value": 3,
                            "reason_codes": ["overrated"],
                            "note": "色彩不应升档",
                        }
                    ],
                    ensure_ascii=False,
                )
                if role == "target_error"
                else "[]"
            ),
        )
        db.add(review)
        db.flush()
        item = SampleSetItem(
            sample_set_id=sample_set.id,
            asset_id=asset.id,
            source_result_id=baseline.id,
            expected_level="L3",
            expected_category="住宅设计",
            truth_json=json.dumps({"level": "L3"}, ensure_ascii=False),
            truth_revision=1,
            truth_updated_by="人工审核员",
            added_by=user.username,
        )
        db.add(item)
        db.flush()
        sample_items[role] = item
        baseline_results[role] = baseline
        if role != omit_candidate_role:
            candidate_results[role] = _add_result(
                db,
                asset=asset,
                bundle=candidate_bundle,
                prompt_a=prompts["candidate_a"],
                prompt_b=prompts["candidate_b"],
                policy=policy,
                level=(
                    "L5"
                    if candidate_regression and role == "blind_holdout"
                    else "L3"
                ),
                color_grade=3,
            )
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    return SeededPair(
        db=db,
        engine=engine,
        client=TestClient(app),
        user=user,
        sample_set=sample_set,
        sample_items=sample_items,
        baseline_bundle=baseline_bundle,
        candidate_bundle=candidate_bundle,
        baseline_results=baseline_results,
        candidate_results=candidate_results,
        prompts=prompts,
        policy=policy,
        model=model,
    )


def _create_payload(seed: SeededPair) -> dict:
    return {
        "name": "P0-C 小策略配对回归",
        "sample_set_id": seed.sample_set.id,
        "baseline_strategy_bundle_id": seed.baseline_bundle.id,
        "candidate_strategy_bundle_id": seed.candidate_bundle.id,
        "samples": [
            {
                "sample_item_id": seed.sample_items[role].id,
                "role": role,
            }
            for role in ("target_error", "stable_control", "blind_holdout")
        ],
        "metric_rules_version": "paired-gate-v1.0.0",
        "aesthetic_accuracy_max_drop": 0.0,
        "whole_image_accuracy_max_drop": 0.0,
        "level_consistency_max_drop": 0.0,
    }


def _close(seed: SeededPair) -> None:
    app.dependency_overrides.clear()
    seed.db.close()
    seed.engine.dispose()


def test_paired_regression_passes_then_requires_separate_human_approval() -> None:
    seed = _seed_pair()
    try:
        response = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["id"]
        assert response.json()["recommendation"] == "pass"
        assert response.json()["approval_status"] == "pending"
        assert response.json()["pending_item_ids"] == []
        assert len(response.json()["sample_set_version"]) == 64

        detail = seed.client.get(f"/api/prompt-regressions/{run_id}").json()
        summary = detail["summary"]
        assert summary["regression_mode"] == "paired"
        assert summary["baseline_strategy_bundle_id"] == seed.baseline_bundle.id
        assert summary["candidate_strategy_bundle_id"] == seed.candidate_bundle.id
        assert summary["metric_rules"]["thresholds"] == {
            "aesthetic_accuracy_max_drop": 0.0,
            "level_consistency_max_drop": 0.0,
            "whole_image_accuracy_max_drop": 0.0,
        }
        assert summary["recommendation"] == "pass"
        assert all(
            check["passed"] for check in summary["summary"]["gate_checks"]
        )
        candidate_strategy = detail["candidate_strategy"]
        assert detail["baseline_strategy"]["bundle_id"] == (
            seed.baseline_bundle.id
        )
        assert candidate_strategy["bundle_id"] == seed.candidate_bundle.id
        assert candidate_strategy["canonical_hash"] == (
            seed.candidate_bundle.canonical_hash
        )
        assert candidate_strategy["model_id"] == "paired-model"
        assert candidate_strategy["model_config"]["model_id"] == (
            "paired-model"
        )
        assert candidate_strategy["model_config"]["temperature"] == 0.1
        assert candidate_strategy["prompt_a"] == {
            "id": seed.prompts["candidate_a"].id,
            "stage": "A",
            "version": "paired-A-v2",
            "name": "候选A",
            "rubric_version": "paired-rubric-v1",
            "system_prompt": (
                "candidate system A 保留正文\n"
                "Authorization: [REDACTED]"
            ),
            "user_prompt": "candidate user A 保留全文",
        }
        assert candidate_strategy["prompt_b"]["id"] == (
            seed.prompts["candidate_b"].id
        )
        assert candidate_strategy["prompt_b"]["version"] == "paired-B-v2"
        assert candidate_strategy["rubric_version"] == "paired-rubric-v1"
        assert candidate_strategy["engine_version"] == "paired-engine-v1"
        assert candidate_strategy["risk_review_version"] is None
        assert candidate_strategy["sampling_policy"]["revision"] == 7
        serialized_detail = json.dumps(detail, ensure_ascii=False)
        assert MODEL_CREDENTIAL_SENTINEL not in serialized_detail
        assert PROMPT_CREDENTIAL_SENTINEL not in serialized_detail
        assert RAW_PROVIDER_SENTINEL not in serialized_detail
        assert "encrypted_api_key" not in serialized_detail
        assert "raw_response_a" not in serialized_detail
        assert "raw_response_b" not in serialized_detail

        target = next(
            item for item in detail["items"] if item["sample_role"] == "target_error"
        )
        assert target["source_evaluation_id"] == seed.baseline_results["target_error"].id
        assert target["source_review_id"]
        assert target["truth_snapshot"]["source"]["decision"] == "corrected"
        assert target["baseline_evaluation_id"] == (
            seed.baseline_results["target_error"].id
        )
        assert target["candidate_evaluation_id"] == (
            seed.candidate_results["target_error"].id
        )
        assert target["baseline_result"]["fields"]["level"] == "L4"
        assert target["candidate_result"]["fields"]["level"] == "L3"
        assert target["outcome"] == "passed"
        assert target["failed"] is False
        assert target["comparison"]["target_error_improved"] is True
        assert {
            "scope_status",
            "primary_category",
            "quality_severity",
            "hard_gate.triggered",
            "level_cap",
            "level",
        } <= {
            diff["field"] for diff in target["comparison"]["diffs"]
        }
        assert any(
            diff["field"].startswith("media_type.")
            for diff in target["comparison"]["diffs"]
        )
        assert any(
            diff["field"].startswith("shooting_method.")
            for diff in target["comparison"]["diffs"]
        )

        seed.prompts["candidate_a"].name = "后续草稿改名"
        seed.prompts["candidate_a"].version = "paired-A-v3-unpublished"
        seed.prompts["candidate_a"].system_prompt = "后续草稿正文"
        seed.model.temperature = 1.7
        seed.model.base_url = "https://later-config.example/v2"
        seed.db.commit()
        detail_after_changes = seed.client.get(
            f"/api/prompt-regressions/{run_id}"
        ).json()
        assert detail_after_changes == detail

        approved = seed.client.post(
            f"/api/paired-regressions/{run_id}/approval",
            json={
                "status": "approved",
                "reviewer_name": "发布审批人",
                "note": "人工确认报告，只批准结论，不自动发布",
            },
        )
        assert approved.status_code == 200
        assert approved.json() == {
            "ok": True,
            "approval_status": "approved",
            "published": False,
        }
        seed.db.expire_all()
        approved_run = seed.db.get(PromptRegressionRun, run_id)
        assert approved_run.approval_status == "approved"
        assert approved_run.approved_by == seed.user.username
        assert seed.db.get(PromptVersion, seed.prompts["candidate_a"].id).status == "draft"
        assert seed.db.get(PromptVersion, seed.prompts["candidate_b"].id).status == "draft"
    finally:
        _close(seed)


def test_optimizer_candidate_materialization_is_idempotent_and_publish_is_gated() -> None:
    seed = _seed_pair()
    try:
        optimization = PromptOptimizationRun(
            base_prompt_id=seed.prompts["baseline_b"].id,
            sample_set_id=seed.sample_set.id,
            optimizer_model_id="gpt-test",
            status="completed",
            progress=100,
            candidate_system_prompt="optimized system prompt with complete contract",
            candidate_user_prompt="optimized user prompt",
            change_note="materialized only after explicit request",
            created_by=seed.user.username,
        )
        seed.db.add(optimization)
        seed.db.commit()
        payload = {
            "version": "paired-B-optimizer-v1",
            "name": "优化候选B",
            "baseline_strategy_bundle_id": seed.baseline_bundle.id,
            "samples": [
                {
                    "sample_item_id": seed.sample_items[role].id,
                    "role": role,
                }
                for role in (
                    "target_error",
                    "stable_control",
                    "blind_holdout",
                )
            ],
            "metric_rules_version": "optimizer-paired-gate-v1",
        }
        created = seed.client.post(
            f"/api/prompt-optimizations/{optimization.id}/materialize-and-validate",
            json=payload,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["prompt_id"]
        assert len(body["paired_regression_ids"]) == 1
        prompt = seed.db.get(PromptVersion, body["prompt_id"])
        assert prompt is not None
        assert prompt.status == "draft"
        assert prompt.source_optimization_run_id == optimization.id
        regression = seed.db.get(
            PromptRegressionRun, body["paired_regression_ids"][0]
        )
        assert regression is not None
        assert regression.trigger_prompt_id == prompt.id
        assert regression.regression_mode == "paired"

        replayed = seed.client.post(
            f"/api/prompt-optimizations/{optimization.id}/materialize-and-validate",
            json=payload,
        )
        assert replayed.status_code == 200
        assert replayed.json() == body

        blocked = seed.client.post(f"/api/prompts/{prompt.id}/publish")
        assert blocked.status_code == 409
        assert seed.db.get(PromptVersion, prompt.id).status == "draft"

        candidate_bundle = seed.db.get(
            StrategyBundle, regression.candidate_strategy_bundle_id
        )
        candidate_results = {}
        for role in ("target_error", "stable_control", "blind_holdout"):
            candidate_results[role] = _add_result(
                seed.db,
                asset=seed.sample_items[role].asset,
                bundle=candidate_bundle,
                prompt_a=seed.prompts["baseline_a"],
                prompt_b=prompt,
                policy=seed.policy,
                level="L3",
                color_grade=3,
            )
        seed.db.commit()
        for item in regression.items:
            role = str(item.sample_role)
            attached = seed.client.post(
                f"/api/paired-regressions/{regression.id}/items/{item.id}/results",
                json={
                    "baseline_evaluation_id": seed.baseline_results[role].id,
                    "candidate_evaluation_id": candidate_results[role].id,
                },
            )
            assert attached.status_code == 200, attached.text
        approved = seed.client.post(
            f"/api/paired-regressions/{regression.id}/approval",
            json={
                "status": "approved",
                "reviewer_name": "发布审批人",
                "note": "配对回归通过，批准候选进入人工发布",
            },
        )
        assert approved.status_code == 200, approved.text
        published = seed.client.post(f"/api/prompts/{prompt.id}/publish")
        assert published.status_code == 409
        assert "评测包" in published.text
        assert seed.db.get(PromptVersion, prompt.id).status == "draft"

        discover = seed.client.get(
            f"/api/strategy-bundles?prompt_b_id={seed.prompts['baseline_b'].id}"
        )
        assert discover.status_code == 200
        assert seed.baseline_bundle.id in {
            item["id"] for item in discover.json()["items"]
        }
    finally:
        _close(seed)


def test_waiting_item_can_attach_matching_results_once_and_keeps_frozen_truth() -> None:
    seed = _seed_pair(omit_candidate_role="blind_holdout")
    try:
        created = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )
        assert created.status_code == 200, created.text
        assert created.json()["recommendation"] == "pending"
        assert len(created.json()["pending_item_ids"]) == 1
        assert len(created.json()["pending_job_ids"]) == 1
        run_id = created.json()["id"]
        item_id = created.json()["pending_item_ids"][0]
        queued_job = seed.db.get(
            EvaluationJob, created.json()["pending_job_ids"][0]
        )
        assert queued_job.queue_class == "validation"
        assert queued_job.origin_queue_class == "validation"
        assert queued_job.regression_item_id == item_id
        assert (
            queued_job.strategy_bundle_id
            == seed.candidate_bundle.id
        )
        before = seed.client.get(f"/api/prompt-regressions/{run_id}").json()
        blind_before = next(
            item
            for item in before["items"]
            if item["sample_role"] == "blind_holdout"
        )
        assert before["summary"]["summary"]["blind_holdout_revealed"] is False
        assert blind_before["truth_snapshot"] is None
        assert blind_before["expected"] is None
        assert blind_before["source_evaluation_id"] is None
        assert blind_before["source_review_id"] is None
        assert blind_before["answer_withheld"] is True
        assert blind_before["truth_revealed"] is False
        assert blind_before["outcome"] == "sealed"
        assert blind_before["passed"] is None
        assert blind_before["diffs"] == []
        assert blind_before["comparison"]["withheld"] is True

        sealed_only = seed.client.get(
            f"/api/prompt-regressions/{run_id}?outcome=sealed"
        ).json()
        assert [item["id"] for item in sealed_only["items"]] == [item_id]

        sample_item = seed.sample_items["blind_holdout"]
        sample_item.truth_json = json.dumps({"level": "L5"})
        sample_item.truth_revision = 99
        seed.db.commit()
        candidate = _add_result(
            seed.db,
            asset=sample_item.asset,
            bundle=seed.candidate_bundle,
            prompt_a=seed.prompts["candidate_a"],
            prompt_b=seed.prompts["candidate_b"],
            policy=seed.policy,
            level="L3",
        )
        seed.db.commit()

        attached = seed.client.post(
            f"/api/paired-regressions/{run_id}/items/{item_id}/results",
            json={
                "baseline_evaluation_id": seed.baseline_results[
                    "blind_holdout"
                ].id,
                "candidate_evaluation_id": candidate.id,
            },
        )
        assert attached.status_code == 200, attached.text
        assert attached.json()["recommendation"] == "pass"
        after = seed.client.get(f"/api/prompt-regressions/{run_id}").json()
        blind_after = next(
            item
            for item in after["items"]
            if item["sample_role"] == "blind_holdout"
        )
        assert after["summary"]["summary"]["blind_holdout_revealed"] is True
        assert blind_after["truth_revealed"] is True
        assert blind_after["answer_withheld"] is False
        assert blind_after["expected"]["level"] == "L3"
        assert blind_after["truth_snapshot"]["truth"]["level"] == "L3"
        assert blind_after["source_evaluation_id"] == (
            seed.baseline_results["blind_holdout"].id
        )
        assert blind_after["source_review_id"]
        assert blind_after["outcome"] == "passed"

        replacement = seed.client.post(
            f"/api/paired-regressions/{run_id}/items/{item_id}/results",
            json={
                "baseline_evaluation_id": seed.baseline_results[
                    "blind_holdout"
                ].id,
                "candidate_evaluation_id": seed.baseline_results[
                    "blind_holdout"
                ].id,
            },
        )
        assert replacement.status_code == 409
    finally:
        _close(seed)


def test_missing_baseline_result_enqueues_idempotent_validation_job() -> None:
    seed = _seed_pair()
    try:
        baseline_a = PromptVersion(
            stage="A",
            name="新基线A",
            version="paired-A-no-result",
            system_prompt="baseline no result A",
            user_prompt="baseline no result A user",
            rubric_version="paired-rubric-v1",
            status="published",
        )
        baseline_b = PromptVersion(
            stage="B",
            name="新基线B",
            version="paired-B-no-result",
            system_prompt="baseline no result B",
            user_prompt="baseline no result B user",
            rubric_version="paired-rubric-v1",
            status="published",
        )
        seed.db.add_all([baseline_a, baseline_b])
        seed.db.flush()
        no_result_bundle = get_or_create_bundle(
            seed.db,
            seed.model,
            baseline_a,
            baseline_b,
            "paired-rubric-v1",
            "paired-engine-v1",
            None,
            seed.policy,
        )
        seed.db.commit()
        payload = _create_payload(seed)
        payload["baseline_strategy_bundle_id"] = no_result_bundle.id
        created = seed.client.post("/api/paired-regressions", json=payload)
        assert created.status_code == 200, created.text
        assert len(created.json()["pending_item_ids"]) == 3
        assert len(created.json()["pending_job_ids"]) == 3
        jobs = seed.db.scalars(
            select(EvaluationJob).where(
                EvaluationJob.id.in_(created.json()["pending_job_ids"])
            )
        ).all()
        assert len(jobs) == 3
        assert {job.queue_class for job in jobs} == {"validation"}
        assert {
            job.strategy_bundle_id for job in jobs
        } == {no_result_bundle.id}
        assert len(
            {
                (job.regression_item_id, job.strategy_bundle_id)
                for job in jobs
            }
        ) == 3
        first_job = jobs[0]
        first_item = seed.db.get(
            PromptRegressionItem,
            first_job.regression_item_id,
        )
        assert first_item is not None
        replayed = main_module._ensure_paired_validation_job(
            seed.db,
            item=first_item,
            bundle=no_result_bundle,
            prompt_a=baseline_a,
            prompt_b=baseline_b,
        )
        replayed_again = main_module._ensure_paired_validation_job(
            seed.db,
            item=first_item,
            bundle=no_result_bundle,
            prompt_a=baseline_a,
            prompt_b=baseline_b,
        )
        assert replayed.id == first_job.id
        assert replayed_again.id == first_job.id
        assert len(
            seed.db.scalars(
                select(EvaluationJob).where(
                    EvaluationJob.regression_item_id
                    == first_item.id,
                    EvaluationJob.strategy_bundle_id
                    == no_result_bundle.id,
                    EvaluationJob.technical_attempt == 0,
                )
            ).all()
        ) == 1
    finally:
        _close(seed)


def test_completed_blind_holdout_stays_sealed_until_every_item_finishes() -> None:
    seed = _seed_pair(
        candidate_regression=True,
        omit_candidate_role="stable_control",
    )
    try:
        created = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )
        assert created.status_code == 200, created.text
        assert created.json()["recommendation"] == "pending"
        run_id = created.json()["id"]

        detail = seed.client.get(
            f"/api/prompt-regressions/{run_id}"
        ).json()
        blind = next(
            item
            for item in detail["items"]
            if item["sample_role"] == "blind_holdout"
        )
        assert blind["status"] == "completed"
        assert blind["answer_withheld"] is True
        assert blind["truth_snapshot"] is None
        assert blind["expected"] is None
        assert blind["source_evaluation_id"] is None
        assert blind["source_review_id"] is None
        assert blind["passed"] is None
        assert blind["failed"] is None
        assert blind["outcome"] == "sealed"
        assert blind["comparison"] == {
            "withheld": True,
            "reason": "blind_holdout_pending",
        }
        assert blind["diffs"] == []
        assert blind["failure_reasons"] == []
        assert blind["baseline_result"]["fields"]["level"] == "L3"
        assert blind["candidate_result"]["fields"]["level"] == "L5"
        assert detail["error_items"] == []
        assert detail["summary"]["failed"] == 0
        assert detail["summary"]["summary"]["error_items"] == []
        assert detail["summary"]["summary"]["failed_item_ids"] == []
        assert detail["summary"]["summary"][
            "blind_holdout_revealed"
        ] is False
    finally:
        _close(seed)


def test_new_p1_regression_fails_recommendation_and_cannot_be_approved() -> None:
    seed = _seed_pair(candidate_regression=True)
    try:
        created = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]
        assert created.json()["recommendation"] == "fail"
        detail = seed.client.get(f"/api/prompt-regressions/{run_id}").json()
        blind = next(
            item
            for item in detail["items"]
            if item["sample_role"] == "blind_holdout"
        )
        stable = next(
            item
            for item in detail["items"]
            if item["sample_role"] == "stable_control"
        )
        assert blind["asset_name"] == "blind_holdout.jpg"
        assert blind["image_url"].endswith(
            f"/api/assets/{blind['asset_id']}/file"
        )
        assert blind["baseline_evaluation_id"] == (
            seed.baseline_results["blind_holdout"].id
        )
        assert blind["candidate_evaluation_id"] == (
            seed.candidate_results["blind_holdout"].id
        )
        assert blind["baseline_result"]["fields"]["level"] == "L3"
        assert blind["candidate_result"]["fields"]["level"] == "L5"
        assert blind["outcome"] == "failed"
        assert blind["passed"] is False
        assert blind["failed"] is True
        assert blind["comparison"]["critical_regressions"] == ["level"]
        assert blind["comparison"]["new_severe_errors"] == [
            {"code": "low_grade_raised_l4_l5", "severity": "P1"}
        ]
        level_diff = next(
            diff for diff in blind["diffs"] if diff["field"] == "level"
        )
        assert level_diff == {
            "field": "level",
            "expected": "L3",
            "baseline": "L3",
            "candidate": "L5",
            "baseline_passed": True,
            "candidate_passed": False,
            "improved": False,
            "regressed": True,
            "change": "regressed",
            "severe_error_codes": ["low_grade_raised_l4_l5"],
        }
        assert {
            reason["code"] for reason in blind["failure_reasons"]
        } == {
            "critical_field_regression",
            "low_grade_raised_l4_l5",
        }
        assert stable["outcome"] == "passed"
        assert stable["failed"] is False

        assert detail["summary"]["summary"]["failed_item_ids"] == [
            blind["id"]
        ]
        assert detail["summary"]["summary"]["error_items"][0][
            "image_url"
        ] == blind["image_url"]
        assert detail["error_items"][0]["item_id"] == blind["id"]
        failed_only = seed.client.get(
            f"/api/prompt-regressions/{run_id}?outcome=failed"
        ).json()
        assert [item["id"] for item in failed_only["items"]] == [
            blind["id"]
        ]

        approval = seed.client.post(
            f"/api/paired-regressions/{run_id}/approval",
            json={
                "status": "approved",
                "reviewer_name": "发布审批人",
                "note": "不应允许",
            },
        )
        assert approval.status_code == 409
        rejected = seed.client.post(
            f"/api/paired-regressions/{run_id}/approval",
            json={
                "status": "rejected",
                "reviewer_name": "发布审批人",
                "note": "发现低等级错误升到 L5",
            },
        )
        assert rejected.status_code == 200
        assert rejected.json()["published"] is False
    finally:
        _close(seed)


def test_unreviewed_history_and_same_bundle_are_rejected() -> None:
    seed = _seed_pair()
    try:
        blind_review = seed.baseline_results["blind_holdout"].reviews[-1]
        seed.db.delete(blind_review)
        seed.db.commit()
        seed.db.expire(seed.baseline_results["blind_holdout"], ["reviews"])
        unreviewed = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )
        assert unreviewed.status_code == 400
        assert "未经人工确认" in unreviewed.json()["detail"]

        same_bundle_payload = _create_payload(seed)
        same_bundle_payload["candidate_strategy_bundle_id"] = (
            same_bundle_payload["baseline_strategy_bundle_id"]
        )
        same_bundle = seed.client.post(
            "/api/paired-regressions", json=same_bundle_payload
        )
        assert same_bundle.status_code == 422
    finally:
        _close(seed)


def test_metric_rule_version_cannot_silently_change_thresholds() -> None:
    seed = _seed_pair()
    try:
        first = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )
        assert first.status_code == 200, first.text
        changed = _create_payload(seed)
        changed["whole_image_accuracy_max_drop"] = 0.2
        second = seed.client.post("/api/paired-regressions", json=changed)
        assert second.status_code == 400
        assert "同一指标规则版本" in second.json()["detail"]
    finally:
        _close(seed)


def test_paired_regression_rejects_bundle_outside_configured_category_contract() -> None:
    seed = _seed_pair()
    try:
        profile = seed.db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        dimension = json.loads(
            seed.baseline_bundle.dimension_schema_set_snapshot
        )["schemas"][0]
        profile.prompt_a_id = seed.prompts["baseline_a"].id
        profile.prompt_b_id = seed.prompts["baseline_b"].id
        profile.model_config_id = seed.model.id
        profile.rubric_version = seed.baseline_bundle.rubric_version
        profile.dimension_schema_key = dimension["schema_key"]
        profile.dimension_schema_version = dimension["version"]
        profile.automation_config_json = json.dumps(
            {
                "enabled": True,
                "baseline_strategy_bundle_id": seed.baseline_bundle.id,
            }
        )
        seed.db.commit()

        response = seed.client.post(
            "/api/paired-regressions", json=_create_payload(seed)
        )

        assert response.status_code == 409
        assert "类目合同不一致" in response.json()["detail"]
        assert seed.db.scalar(select(PromptRegressionRun.id)) is None
    finally:
        _close(seed)


@pytest.mark.parametrize(
    (
        "truth_overrides",
        "baseline_kwargs",
        "candidate_kwargs",
        "expected_code",
    ),
    [
        (
            {"scope_status": "out_of_scope", "level": None},
            {"scope": "out_of_scope", "level": None},
            {"scope": "in_scope", "level": "L2"},
            "out_of_scope_misrelease",
        ),
        (
            {"quality_severity": "severe", "level": "L1"},
            {"quality": "severe", "level": "L1", "needs_review": True},
            {"quality": "severe", "level": "L2", "needs_review": False},
            "severe_damage_auto_pass",
        ),
        (
            {"level": "L2"},
            {"level": "L2"},
            {"level": "L4"},
            "low_grade_raised_l4_l5",
        ),
        (
            {
                "hard_gate": {"triggered": True, "target": "L1"},
                "level": "L1",
            },
            {"hard_gate": True, "level": "L1"},
            {"hard_gate": False, "level": "L1"},
            "hard_gate_missed",
        ),
        (
            {"level_cap": "L2", "level": "L2"},
            {"level_cap": "L2", "level": "L2"},
            {"level_cap": "none", "level": "L2"},
            "level_cap_missed",
        ),
    ],
)
def test_each_new_p0_p1_error_is_explicitly_blocked(
    truth_overrides: dict,
    baseline_kwargs: dict,
    candidate_kwargs: dict,
    expected_code: str,
) -> None:
    truth = {
        "scope_status": "in_scope",
        "primary_category": "住宅设计",
        "media_type": {},
        "shooting_method": {},
        "quality_severity": "normal",
        "hard_gate": {"triggered": False, "target": "none"},
        "level_cap": "none",
        "dimensions": {},
        "level": "L2",
        **truth_overrides,
    }
    comparison = compare_paired_results(
        truth_snapshot={
            "truth": truth,
            "source": {"evaluation_id": 1, "review_id": 1},
        },
        role="blind_holdout",
        baseline=_contract_result(1, **baseline_kwargs),
        candidate=_contract_result(2, **candidate_kwargs),
    )
    codes = {
        error["code"] for error in comparison["new_severe_errors"]
    }
    assert expected_code in codes
    assert comparison["passed"] is False
