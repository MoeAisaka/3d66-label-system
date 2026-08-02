from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.evaluation_packages import canonical_json, canonical_manifest_hash
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    AuditEvent,
    AutomationOptimizationRun,
    DimensionRoutePolicy,
    DimensionSchema,
    EvaluationCategoryProfile,
    EvaluationJob,
    EvaluationPackage,
    EvaluationResult,
    HumanReview,
    ModelConfig,
    PromptMetricSnapshot,
    PromptRegressionItem,
    PromptRegressionRun,
    PromptVersion,
    SampleSet,
    SampleSetItem,
    SamplingPolicy,
    StrategyBundle,
    User,
)
from app.strategy_bundle import (
    build_evaluation_strategy_snapshot,
    build_strategy_snapshot,
    get_or_create_bundle,
)


def _engine(path: Path | None = None):
    if path is None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    return engine


def _client(db: Session, user: User) -> TestClient:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


def _close(engine: Any, db: Session) -> None:
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _strategy_json(
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    policy: SamplingPolicy,
) -> str:
    return build_strategy_snapshot(bundle, prompt_a, prompt_b, policy)


def _result(
    db: Session,
    *,
    asset: Asset,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    policy: SamplingPolicy,
    level: str,
) -> EvaluationResult:
    job = EvaluationJob(
        asset_id=asset.id,
        category_key=asset.category_key,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id if prompt_b else None,
        strategy_bundle_id=bundle.id,
        status="completed",
        stage="done",
        progress=100,
    )
    db.add(job)
    db.flush()
    aesthetic = {
        "scoring_profile": "space_aesthetic_v1.3",
        "dimensions": {},
    }
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
        scoring_json=json.dumps({"formal": True}, ensure_ascii=False),
        raw_response_a='{"sensitive":"provider raw"}',
        raw_response_b='{"sensitive":"provider raw"}' if prompt_b else None,
        score=80,
        level=level,
        confidence=0.95,
        needs_review=False,
        review_stage="completed",
        model_id=bundle.model_id,
        prompt_a_version=prompt_a.version,
        prompt_b_version=prompt_b.version if prompt_b else None,
        rubric_version=bundle.rubric_version,
        engine_version=bundle.engine_version,
        risk_review_version=bundle.risk_review_version,
    )
    db.add(result)
    db.flush()
    return result


def _fixture(
    *,
    path: Path | None = None,
    single_prompt: bool = False,
    recommendation: str = "pass",
    regression_status: str = "passed",
) -> dict[str, Any]:
    engine = _engine(path)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="package-admin",
        password_hash="unused",
        display_name="评测包管理员",
        is_admin=True,
        role="admin",
    )
    automation = AutomationOptimizationRun(
        category_key="space_image",
        run_key="automation-package-run",
        base_prompt_version="base-B",
        policy_revision=1,
        status="awaiting_release_review",
        dry_run=False,
        trigger_reason="case_threshold",
        case_ids_json="[1,2,3]",
        frozen_input_json='{"internal":"not returned"}',
        result_json="{}",
        candidate_count=1,
        estimated_cost_micros=200,
        actual_cost_micros=150,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        created_by="automation-worker",
        finished_at=datetime.now(timezone.utc),
    )
    model = ModelConfig(
        name="package-model",
        model_id="model-package-v1",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
        encrypted_api_key="fake-encrypted-reference",
    )
    prompt_a = PromptVersion(
        stage="A",
        name="新版完整提示词" if single_prompt else "预检提示词",
        version="candidate-A" if single_prompt else "base-A",
        system_prompt="这是用于评测包冻结的完整 A system prompt 文本。",
        user_prompt="请严格返回完整结构化 A 结果。",
        rubric_version="rubric-package-v1",
        status="draft" if single_prompt else "published",
        source="optimizer" if single_prompt else "manual",
        source_automation_run_id=automation.id if automation.id else None,
        change_note="单提示词候选改动摘要" if single_prompt else "",
    )
    base_b = PromptVersion(
        stage="B",
        name="基线美感提示词",
        version="base-B",
        system_prompt="这是冻结的基线 B system prompt 完整文本。",
        user_prompt="请返回基线 B 维度。",
        rubric_version="rubric-package-v1",
        status="published",
    )
    db.add_all([user, automation, model, prompt_a, base_b])
    db.flush()
    if single_prompt:
        prompt_a.source_automation_run_id = automation.id
        candidate_prompt = prompt_a
    else:
        candidate_prompt = PromptVersion(
            stage="B",
            name="新版候选美感提示词",
            version="candidate-B",
            system_prompt="这是用于二审的完整新版 B system prompt 文本。",
            user_prompt="请按新版维度定义返回候选 B 结果。",
            rubric_version="rubric-package-v1",
            status="draft",
            source="optimizer",
            source_automation_run_id=automation.id,
            change_note="修复目标错例，同时保持稳定对照与盲测样本。",
        )
        db.add(candidate_prompt)
        db.flush()
    policy = db.get(SamplingPolicy, 1)
    if policy is None:
        policy = SamplingPolicy(id=1, revision=1)
        db.add(policy)
        db.flush()
    baseline_bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=None if single_prompt else base_b,
        rubric_version="rubric-package-v1",
        engine_version="engine-package-v1",
        risk_review_version="risk-package-v1",
        sampling_policy=policy,
    )
    candidate_bundle = (
        baseline_bundle
        if single_prompt
        else get_or_create_bundle(
            db=db,
            model_config=model,
            prompt_a=prompt_a,
            prompt_b=candidate_prompt,
            rubric_version="rubric-package-v1",
            engine_version="engine-package-v1",
            risk_review_version="risk-package-v1",
            sampling_policy=policy,
        )
    )
    sample_set = SampleSet(
        category_key="space_image",
        name="锁定黄金集",
        description="供评测包二审使用的黄金样本",
        kind="golden",
        status="locked",
        created_by=user.username,
    )
    db.add(sample_set)
    db.flush()
    roles = ["target_error", "stable_control", "blind_holdout"]
    sample_items: list[SampleSetItem] = []
    truth_entries: list[dict[str, Any]] = []
    for index, role in enumerate(roles, start=1):
        asset = Asset(
            original_name=f"gold-{index}.jpg",
            stored_name=f"gold-{index}.jpg",
            mime_type="image/jpeg",
            size_bytes=100 + index,
            sha256=hashlib.sha256(f"asset-{index}".encode()).hexdigest(),
            category_key="space_image",
        )
        db.add(asset)
        db.flush()
        source_result = _result(
            db,
            asset=asset,
            bundle=baseline_bundle,
            prompt_a=prompt_a,
            prompt_b=None if single_prompt else base_b,
            policy=policy,
            level=f"L{index}",
        )
        source_review = HumanReview(
            evaluation_id=source_result.id,
            reviewer_name=user.username,
            stage="initial",
            decision="corrected" if index == 1 else "approved",
            corrected_level=f"L{index}" if index == 1 else None,
            corrected_score=80 if index == 1 else None,
            corrections_json="[]",
            note="冻结黄金真值",
        )
        db.add(source_review)
        db.flush()
        truth = {
            "level": f"L{index}",
            "scope_status": "in_scope",
            "primary_category": "住宅",
            "dimensions": {"composition_viewpoint": index},
        }
        item = SampleSetItem(
            sample_set_id=sample_set.id,
            asset_id=asset.id,
            source_result_id=source_result.id,
            expected_level=f"L{index}",
            expected_category="住宅",
            truth_json=canonical_json(truth),
            truth_revision=1,
            truth_updated_by=user.username,
            truth_updated_at=datetime.now(timezone.utc),
            added_by=user.username,
        )
        db.add(item)
        db.flush()
        sample_items.append(item)
        truth_snapshot = {
            "schema_version": "paired-truth-v1",
            "truth": truth,
            "source": {
                "evaluation_id": source_result.id,
                "review_id": source_review.id,
            },
            "reviewer_name": user.username,
            "decision": "corrected" if index == 1 else "approved",
        }
        truth_entries.append(
            {
                "sample_item_id": item.id,
                "asset_id": asset.id,
                "role": role,
                "truth_revision": 1,
                "truth_snapshot": truth_snapshot,
            }
        )
    sample_manifest = {
        "schema_version": "paired-sample-set-v1",
        "sample_set_id": sample_set.id,
        "items": truth_entries,
    }
    regression = PromptRegressionRun(
        name="候选完整回归证据",
        sample_set_id=sample_set.id,
        trigger_prompt_id=candidate_prompt.id,
        prompt_a_id=prompt_a.id,
        prompt_b_id=base_b.id if single_prompt else candidate_prompt.id,
        regression_mode="single" if single_prompt else "paired",
        baseline_strategy_bundle_id=(
            None if single_prompt else baseline_bundle.id
        ),
        candidate_strategy_bundle_id=candidate_bundle.id,
        baseline_strategy_snapshot_json=(
            "{}"
            if single_prompt
            else _strategy_json(baseline_bundle, prompt_a, base_b, policy)
        ),
        candidate_strategy_snapshot_json=_strategy_json(
            candidate_bundle,
            prompt_a,
            None if single_prompt else candidate_prompt,
            policy,
        ),
        sample_set_version=hashlib.sha256(
            canonical_json(sample_manifest).encode()
        ).hexdigest(),
        sample_manifest_json=canonical_json(sample_manifest),
        metric_rules_version="package-metrics-v1",
        metric_rules_json=canonical_json(
            {
                "schema_version": "paired-metric-rules-v1",
                "thresholds": {"level_consistency_max_drop": 0},
            }
        ),
        summary_json=canonical_json(
            {
                "schema_version": "paired-regression-summary-v1",
                "gate_checks": [{"gate": "all", "passed": recommendation == "pass"}],
                "candidate": {"level_consistency": 1.0},
            }
        ),
        recommendation=recommendation,
        approval_status="pending",
        status=regression_status,
        threshold=1.0,
        total=3,
        completed=3 if regression_status in {"passed", "regressed"} else 0,
        passed=3 if recommendation == "pass" else 0,
        failed=0 if recommendation == "pass" else 3,
        metrics_json=canonical_json(
            {
                "release_gate_passed": recommendation == "pass",
                "pass_rate": 1.0 if recommendation == "pass" else 0.0,
                "raw_response": "must never escape",
                "api_key": "must never escape",
            }
        ),
        created_by="automation-worker",
        finished_at=(
            datetime.now(timezone.utc)
            if regression_status in {"passed", "regressed"}
            else None
        ),
    )
    db.add(regression)
    db.flush()
    for index, (sample_item, truth_entry, role) in enumerate(
        zip(sample_items, truth_entries, roles, strict=True), start=1
    ):
        comparison = {
            "schema_version": "paired-comparison-v1",
            "passed": recommendation == "pass",
            "diffs": [{"field": "level", "change": "improved"}],
            "raw_response_a": "provider raw must be removed",
            "encrypted_api_key": "secret reference must be removed",
        }
        db.add(
            PromptRegressionItem(
                run_id=regression.id,
                sample_item_id=sample_item.id,
                sample_role=role,
                source_evaluation_id=sample_item.source_result_id,
                source_review_id=int(
                    truth_entry["truth_snapshot"]["source"]["review_id"]
                ),
                truth_snapshot_json=canonical_json(
                    truth_entry["truth_snapshot"]
                ),
                status=(
                    "completed"
                    if regression_status in {"passed", "regressed"}
                    else "waiting_results"
                ),
                passed=(
                    recommendation == "pass"
                    if regression_status in {"passed", "regressed"}
                    else None
                ),
                comparison_json=canonical_json(comparison),
                baseline_result_json=canonical_json(
                    {"evaluation_id": sample_item.source_result_id, "fields": {"level": f"L{index}"}}
                ),
                candidate_result_json=canonical_json(
                    {"evaluation_id": 100 + index, "fields": {"level": f"L{index}"}}
                ),
            )
        )
    metric = PromptMetricSnapshot(
        prompt_id=candidate_prompt.id,
        task_set_key="package-task-set",
        task_set_hash="f" * 64,
        evaluation_ids_json="[1,2,3]",
        metrics_json=canonical_json({"accuracy": 1.0}),
        total_count=3,
        reviewed_count=3,
        created_by=user.username,
    )
    db.add(metric)
    db.flush()
    automation.result_json = canonical_json(
        {
            "prompt_ids": [candidate_prompt.id],
            "regression_ids": [regression.id],
            "candidates": [
                {
                    "system_prompt": candidate_prompt.system_prompt,
                    "user_prompt": candidate_prompt.user_prompt,
                    "change_note": candidate_prompt.change_note,
                    "raw_response": "automation provider raw must never escape",
                }
            ],
            "publishes_automatically": False,
        }
    )
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "space_image"
        )
    )
    assert profile is not None
    pipeline = json.loads(profile.pipeline_config_json)
    pipeline["prompt_mode"] = "single" if single_prompt else "ab"
    profile.pipeline_config_json = canonical_json(pipeline)
    db.commit()
    client = _client(db, user)
    return {
        "engine": engine,
        "db": db,
        "client": client,
        "user": user,
        "automation": automation,
        "model": model,
        "regression": regression,
        "sample_set": sample_set,
        "sample_items": sample_items,
        "prompt_a": prompt_a,
        "prompt_b": None if single_prompt else candidate_prompt,
        "candidate_prompt": candidate_prompt,
        "base_b": base_b,
        "candidate_bundle": candidate_bundle,
        "baseline_bundle": baseline_bundle,
        "metric": metric,
    }


def _create_payload(fixture: dict[str, Any], key: str = "package-1") -> dict[str, Any]:
    return {
        "package_key": key,
        "category_key": "space_image",
        "regression_run_id": fixture["regression"].id,
        "automation_run_id": fixture["automation"].id,
        "candidate_strategy_bundle_id": fixture["candidate_bundle"].id,
        "baseline_strategy_bundle_id": (
            fixture["baseline_bundle"].id
            if fixture["regression"].regression_mode == "paired"
            else None
        ),
        "sample_set_id": fixture["sample_set"].id,
        "metric_snapshot_id": fixture["metric"].id,
    }


def test_manifest_is_stable_and_idempotent() -> None:
    fixture = _fixture()
    client = fixture["client"]
    try:
        payload = _create_payload(fixture)
        first = client.post("/api/evaluation-packages", json=payload)
        assert first.status_code == 200, first.text
        second = client.post("/api/evaluation-packages", json=payload)
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["canonical_manifest_hash"] == second.json()["canonical_manifest_hash"]
        manifest = first.json()["canonical_manifest"]
        assert canonical_manifest_hash(manifest) == first.json()["canonical_manifest_hash"]
        assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
        drift = client.post(
            "/api/evaluation-packages",
            json={**payload, "change_summary": "漂移后的请求"},
        )
        assert drift.status_code == 409
        assert "幂等键" in drift.text
    finally:
        _close(fixture["engine"], fixture["db"])


def test_single_and_dual_prompt_packages_freeze_full_prompt_text() -> None:
    dual = _fixture()
    try:
        response = dual["client"].post(
            "/api/evaluation-packages", json=_create_payload(dual, "dual-package")
        )
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["prompt_mode"] == "dual"
        assert detail["prompts"]["a"]["system_prompt"] == dual["prompt_a"].system_prompt
        assert detail["prompts"]["b"]["user_prompt"] == dual["prompt_b"].user_prompt
    finally:
        _close(dual["engine"], dual["db"])

    single = _fixture(single_prompt=True)
    try:
        payload = _create_payload(single, "single-package")
        payload["baseline_strategy_bundle_id"] = None
        response = single["client"].post("/api/evaluation-packages", json=payload)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["prompt_mode"] == "single"
        assert detail["prompt_b_id"] is None
        assert detail["prompts"]["b"] is None
        assert detail["prompts"]["a"]["system_prompt"] == single["prompt_a"].system_prompt
    finally:
        _close(single["engine"], single["db"])


def test_detail_is_complete_and_sensitive_fields_do_not_escape() -> None:
    fixture = _fixture()
    db = fixture["db"]
    client = fixture["client"]
    try:
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        schema = db.scalar(
            select(DimensionSchema).where(
                DimensionSchema.status == "published",
                DimensionSchema.schema_key == profile.dimension_schema_key,
            )
        )
        route = db.scalar(
            select(DimensionRoutePolicy).where(
                DimensionRoutePolicy.status == "published"
            )
        )
        payload = {
            **_create_payload(fixture),
            "dimension_schema_id": schema.id if schema else None,
            "dimension_route_policy_id": route.id if route else None,
        }
        created = client.post("/api/evaluation-packages", json=payload)
        assert created.status_code == 200, created.text
        detail = client.get(
            f"/api/evaluation-packages/{created.json()['id']}"
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["manifest_hash_valid"] is True
        assert body["golden_sample_set"]["judgable_item_count"] == 3
        assert len(body["golden_sample_set"]["items"]) == 3
        assert body["regression"]["items"][0]["comparison"]["diffs"]
        assert body["automation"]["result_binding"]["regression_ids"] == [
            fixture["regression"].id
        ]
        assert body["automation"]["result_binding"]["candidate_evidence"][0][
            "change_note"
        ] == fixture["candidate_prompt"].change_note
        assert body["identity"] == {
            "agent_plan_version": "controlled-agent-plan-v1",
            "baseline_strategy_hash": fixture["baseline_bundle"].canonical_hash,
            "candidate_strategy_hash": fixture["candidate_bundle"].canonical_hash,
            "engine_version": "engine-package-v1",
            "model_id": "model-package-v1",
            "risk_review_version": "risk-package-v1",
            "rubric_version": "rubric-package-v1",
        }
        serialized = json.dumps(body, ensure_ascii=False).lower()
        for forbidden in (
            "provider raw",
            "automation provider raw",
            "must never escape",
            "fake-encrypted-reference",
            "raw_response",
            "encrypted_api_key",
            "api_key",
        ):
            assert forbidden not in serialized
        if schema is not None:
            assert body["dimensions"]["explicit_schema"]["definition"]
        listed = client.get("/api/evaluation-packages").json()["items"]
        assert listed[0]["canonical_manifest_hash"] == body["canonical_manifest_hash"]
        assert "canonical_manifest" not in listed[0]
    finally:
        _close(fixture["engine"], fixture["db"])


def test_golden_set_is_frozen_after_package_creation() -> None:
    fixture = _fixture()
    client = fixture["client"]
    db = fixture["db"]
    try:
        created = client.post(
            "/api/evaluation-packages", json=_create_payload(fixture)
        ).json()
        before = created["golden_sample_set"]
        item = fixture["sample_items"][0]
        item.truth_json = canonical_json({"level": "L5", "tampered": True})
        item.truth_revision = 99
        item.asset.original_name = "changed-after-freeze.jpg"
        db.commit()
        after = client.get(
            f"/api/evaluation-packages/{created['id']}"
        ).json()["golden_sample_set"]
        assert after == before
        assert after["items"][0]["truth"]["level"] == "L1"
        assert after["items"][0]["asset_name"] == "gold-1.jpg"
    finally:
        _close(fixture["engine"], fixture["db"])


def test_category_isolation_and_missing_resources() -> None:
    fixture = _fixture()
    client = fixture["client"]
    try:
        mismatch = client.post(
            "/api/evaluation-packages",
            json={**_create_payload(fixture), "category_key": "material_image"},
        )
        assert mismatch.status_code == 409
        assert "类目" in mismatch.text
        missing = client.post(
            "/api/evaluation-packages",
            json={
                "package_key": "missing-regression",
                "regression_run_id": 999999,
            },
        )
        assert missing.status_code == 404
        assert client.get("/api/evaluation-packages/999999").status_code == 404
    finally:
        _close(fixture["engine"], fixture["db"])


def test_illegal_states_rejection_and_archive() -> None:
    pending = _fixture(regression_status="waiting_results")
    try:
        created = pending["client"].post(
            "/api/evaluation-packages", json=_create_payload(pending)
        )
        assert created.status_code == 200
        package_id = created.json()["id"]
        assert created.json()["status"] == "validating"
        assert pending["client"].post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "试图提前批准"},
        ).status_code == 409
        assert pending["client"].post(
            f"/api/evaluation-packages/{package_id}/publish", json={}
        ).status_code == 409
    finally:
        _close(pending["engine"], pending["db"])

    failed = _fixture(recommendation="fail", regression_status="regressed")
    try:
        package_id = failed["client"].post(
            "/api/evaluation-packages",
            json={**_create_payload(failed), "ai_recommendation": "pass"},
        ).json()["id"]
        blocked = failed["client"].post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "无视失败建议"},
        )
        assert blocked.status_code == 409
        rejected = failed["client"].post(
            f"/api/evaluation-packages/{package_id}/reject",
            json={"note": "回归失败，退回优化"},
        )
        assert rejected.status_code == 200
        archived = failed["client"].post(
            f"/api/evaluation-packages/{package_id}/archive",
            json={"reason": "失败评测包归档"},
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        assert failed["client"].post(
            f"/api/evaluation-packages/{package_id}/publish", json={}
        ).status_code == 409
    finally:
        _close(failed["engine"], failed["db"])


def test_repeated_approval_and_publish_are_idempotent() -> None:
    fixture = _fixture()
    client = fixture["client"]
    try:
        profile = fixture["db"].scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        profile.prompt_a_id = fixture["prompt_a"].id
        profile.prompt_b_id = fixture["base_b"].id
        profile.model_config_id = fixture["model"].id
        profile.rubric_version = fixture["candidate_bundle"].rubric_version
        profile.dimension_schema_key = "space_aesthetic"
        profile.dimension_schema_version = "1.3.0"
        revision_before_review = profile.automation_revision
        other_profile = fixture["db"].scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "material_image"
            )
        )
        assert other_profile is not None
        other_profile.prompt_b_id = fixture["base_b"].id
        fixture["db"].commit()
        package_id = client.post(
            "/api/evaluation-packages", json=_create_payload(fixture)
        ).json()["id"]
        assert json.loads(profile.automation_config_json).get(
            "baseline_strategy_bundle_id"
        ) != fixture["candidate_bundle"].id
        approved = client.post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "证据完整，同意发布"},
        )
        assert approved.status_code == 200, approved.text
        duplicate_approval = client.post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "证据完整，同意发布"},
        )
        assert duplicate_approval.status_code == 200
        assert duplicate_approval.json()["duplicate"] is True
        changed_approval = client.post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "另一份意见"},
        )
        assert changed_approval.status_code == 409
        published = client.post(
            f"/api/evaluation-packages/{package_id}/publish",
            json={"note": "人工显式发布"},
        )
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"
        repeated = client.post(
            f"/api/evaluation-packages/{package_id}/publish", json={}
        )
        assert repeated.status_code == 200
        assert repeated.json()["duplicate"] is True
        fixture["db"].expire_all()
        assert fixture["db"].get(PromptVersion, fixture["candidate_prompt"].id).status == "published"
        assert fixture["db"].get(EvaluationPackage, package_id).published_by == fixture["user"].username
        profile = fixture["db"].get(EvaluationCategoryProfile, profile.id)
        assert profile is not None
        automation = json.loads(profile.automation_config_json)
        assert automation["baseline_strategy_bundle_id"] == fixture["candidate_bundle"].id
        assert profile.prompt_a_id == fixture["prompt_a"].id
        assert profile.prompt_b_id == fixture["candidate_prompt"].id
        assert profile.model_config_id == fixture["model"].id
        assert profile.automation_revision == revision_before_review + 1
        assert fixture["db"].get(PromptVersion, fixture["base_b"].id).status == "published"
        assert fixture["db"].get(EvaluationCategoryProfile, other_profile.id).prompt_b_id == fixture["base_b"].id
    finally:
        _close(fixture["engine"], fixture["db"])


def test_publish_rejects_category_drift_after_package_creation() -> None:
    fixture = _fixture()
    client = fixture["client"]
    db = fixture["db"]
    try:
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        profile.prompt_a_id = fixture["prompt_a"].id
        profile.prompt_b_id = fixture["base_b"].id
        profile.model_config_id = fixture["model"].id
        profile.rubric_version = fixture["candidate_bundle"].rubric_version
        profile.dimension_schema_key = "space_aesthetic"
        profile.dimension_schema_version = "1.3.0"
        db.commit()
        package_id = client.post(
            "/api/evaluation-packages", json=_create_payload(fixture, "stale-package")
        ).json()["id"]
        assert client.post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "先批准，等待发布"},
        ).status_code == 200
        profile.automation_revision += 1
        db.commit()

        stale = client.post(
            f"/api/evaluation-packages/{package_id}/publish", json={}
        )
        assert stale.status_code == 409
        db.expire_all()
        assert db.get(EvaluationPackage, package_id).status == "approved"
        assert json.loads(profile.automation_config_json).get(
            "baseline_strategy_bundle_id"
        ) != fixture["candidate_bundle"].id
    finally:
        _close(fixture["engine"], fixture["db"])


def test_publish_rejects_pipeline_drift_after_package_creation() -> None:
    fixture = _fixture()
    client = fixture["client"]
    db = fixture["db"]
    try:
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        profile.prompt_a_id = fixture["prompt_a"].id
        profile.prompt_b_id = fixture["base_b"].id
        profile.model_config_id = fixture["model"].id
        profile.rubric_version = fixture["candidate_bundle"].rubric_version
        profile.dimension_schema_key = "space_aesthetic"
        profile.dimension_schema_version = "1.3.0"
        db.commit()
        package_id = client.post(
            "/api/evaluation-packages",
            json=_create_payload(fixture, "pipeline-stale-package"),
        ).json()["id"]
        assert client.post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "流水线变更前批准"},
        ).status_code == 200
        pipeline = json.loads(profile.pipeline_config_json)
        pipeline["prompt_context"]["instruction"] = "管理员更新后的类目说明"
        profile.pipeline_config_json = canonical_json(pipeline)
        profile.pipeline_revision += 1
        db.commit()

        stale = client.post(
            f"/api/evaluation-packages/{package_id}/publish", json={}
        )
        assert stale.status_code == 409
        assert "流水线" in stale.text
        db.expire_all()
        assert db.get(EvaluationPackage, package_id).status == "approved"
    finally:
        _close(fixture["engine"], fixture["db"])


def test_package_prompt_mode_must_match_category_pipeline() -> None:
    fixture = _fixture()
    try:
        profile = fixture["db"].scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        pipeline = json.loads(profile.pipeline_config_json)
        pipeline["prompt_mode"] = "single"
        profile.pipeline_config_json = canonical_json(pipeline)
        fixture["db"].commit()
        response = fixture["client"].post(
            "/api/evaluation-packages",
            json=_create_payload(fixture, "wrong-prompt-mode"),
        )
        assert response.status_code == 409
        assert "提示词模式" in response.text
    finally:
        _close(fixture["engine"], fixture["db"])


def test_publish_rejects_pipeline_drift_even_when_prompt_mode_is_unchanged() -> None:
    fixture = _fixture()
    client = fixture["client"]
    db = fixture["db"]
    try:
        profile = db.scalar(select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "space_image"
        ))
        assert profile is not None
        profile.prompt_a_id = fixture["prompt_a"].id
        profile.prompt_b_id = fixture["base_b"].id
        profile.model_config_id = fixture["model"].id
        profile.rubric_version = fixture["candidate_bundle"].rubric_version
        profile.dimension_schema_key = "space_aesthetic"
        profile.dimension_schema_version = "1.3.0"
        db.commit()
        package_id = client.post("/api/evaluation-packages", json=_create_payload(fixture, "pipeline-stale")).json()["id"]
        assert client.post(f"/api/evaluation-packages/{package_id}/approve", json={"note": "ok"}).status_code == 200
        pipeline = json.loads(profile.pipeline_config_json)
        pipeline["prompt_context"]["instruction"] = "changed after package freeze"
        profile.pipeline_config_json = canonical_json(pipeline)
        # Simulate an out-of-band edit that incorrectly omitted the revision bump.
        db.commit()
        response = client.post(f"/api/evaluation-packages/{package_id}/publish", json={})
        assert response.status_code == 409
        assert "流水线" in response.text
        db.expire_all()
        assert db.get(EvaluationPackage, package_id).status == "approved"
    finally:
        _close(fixture["engine"], db)


def test_two_approved_packages_cannot_concurrently_promote_the_same_category(
    tmp_path: Path,
) -> None:
    fixture = _fixture(path=tmp_path / "competing-package-publish.db")
    client = fixture["client"]
    db = fixture["db"]
    try:
        profile = db.scalar(select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "space_image"
        ))
        assert profile is not None
        profile.prompt_a_id = fixture["prompt_a"].id
        profile.prompt_b_id = fixture["base_b"].id
        profile.model_config_id = fixture["model"].id
        profile.rubric_version = fixture["candidate_bundle"].rubric_version
        profile.dimension_schema_key = "space_aesthetic"
        profile.dimension_schema_version = "1.3.0"
        db.commit()
        ids = [
            client.post("/api/evaluation-packages", json=_create_payload(fixture, key)).json()["id"]
            for key in ("competing-package-a", "competing-package-b")
        ]
        for package_id in ids:
            assert client.post(
                f"/api/evaluation-packages/{package_id}/approve", json={"note": "approved"}
            ).status_code == 200
        expected_revision = profile.automation_revision + 1
        thread_user = User(
            username="package-admin",
            password_hash="unused",
            display_name="评测包管理员",
            is_admin=True,
            role="admin",
        )
        db.close()
        SessionLocal = sessionmaker(bind=fixture["engine"], expire_on_commit=False)

        def override_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[current_user] = lambda: thread_user

        def publish(package_id: int) -> int:
            thread_client = TestClient(app)
            try:
                return thread_client.post(
                    f"/api/evaluation-packages/{package_id}/publish", json={}
                ).status_code
            finally:
                thread_client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(publish, ids))
        assert sorted(statuses) == [200, 409]
        with SessionLocal() as check:
            package_statuses = sorted(
                check.get(EvaluationPackage, package_id).status for package_id in ids
            )
            assert package_statuses == ["approved", "published"]
            current_profile = check.scalar(
                select(EvaluationCategoryProfile).where(
                    EvaluationCategoryProfile.category_key == "space_image"
                )
            )
            assert current_profile is not None
            assert current_profile.automation_revision == expected_revision
            assert check.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action == "published",
                    AuditEvent.subject_type == "evaluation_package",
                    AuditEvent.subject_id.in_([str(package_id) for package_id in ids]),
                )
            ) == 1
    finally:
        app.dependency_overrides.clear()
        fixture["engine"].dispose()


def test_old_paired_approval_cannot_bypass_package_gate() -> None:
    fixture = _fixture()
    client = fixture["client"]
    regression = fixture["regression"]
    try:
        fixture["candidate_prompt"].source = "manual"
        fixture["candidate_prompt"].source_automation_run_id = None
        fixture["db"].commit()
        old_approval = client.post(
            f"/api/paired-regressions/{regression.id}/approval",
            json={
                "status": "approved",
                "reviewer_name": "伪造客户端姓名",
                "note": "旧接口批准只能成为参考证据",
            },
        )
        assert old_approval.status_code == 200, old_approval.text
        assert old_approval.json()["published"] is False
        bypass = client.post(
            f"/api/prompts/{fixture['candidate_prompt'].id}/publish"
        )
        assert bypass.status_code == 409
        assert "评测包" in bypass.text
        assert fixture["candidate_prompt"].status == "draft"
    finally:
        _close(fixture["engine"], fixture["db"])


def test_from_completed_automation_run_is_idempotent() -> None:
    fixture = _fixture()
    client = fixture["client"]
    try:
        path = (
            "/api/evaluation-packages/from-automation/"
            f"{fixture['automation'].id}"
        )
        first = client.post(path, json={})
        assert first.status_code == 200, first.text
        second = client.post(path, json={})
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["id"] == first.json()["id"]
        assert first.json()["automation"]["publishes_automatically"] is False
    finally:
        _close(fixture["engine"], fixture["db"])


def test_concurrent_approve_and_publish_have_one_audit_effect(tmp_path: Path) -> None:
    fixture = _fixture(path=tmp_path / "evaluation-package-concurrency.db")
    db = fixture["db"]
    client = fixture["client"]
    try:
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        assert profile is not None
        profile.prompt_a_id = fixture["prompt_a"].id
        profile.prompt_b_id = fixture["base_b"].id
        profile.model_config_id = fixture["model"].id
        profile.rubric_version = fixture["candidate_bundle"].rubric_version
        profile.dimension_schema_key = "space_aesthetic"
        profile.dimension_schema_version = "1.3.0"
        db.commit()
        package_id = client.post(
            "/api/evaluation-packages", json=_create_payload(fixture)
        ).json()["id"]
        db.close()
        SessionLocal = sessionmaker(bind=fixture["engine"], expire_on_commit=False)

        def override_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[current_user] = lambda: fixture["user"]

        def approve() -> tuple[int, dict[str, Any]]:
            thread_client = TestClient(app)
            try:
                response = thread_client.post(
                    f"/api/evaluation-packages/{package_id}/approve",
                    json={"note": "并发二审同一意见"},
                )
                return response.status_code, response.json()
            finally:
                thread_client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            approvals = list(pool.map(lambda _: approve(), range(2)))
        assert [status for status, _ in approvals] == [200, 200]
        assert sorted(payload["duplicate"] for _, payload in approvals) == [False, True]

        def publish() -> tuple[int, dict[str, Any]]:
            thread_client = TestClient(app)
            try:
                response = thread_client.post(
                    f"/api/evaluation-packages/{package_id}/publish", json={}
                )
                return response.status_code, response.json()
            finally:
                thread_client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            publications = list(pool.map(lambda _: publish(), range(2)))
        assert [status for status, _ in publications] == [200, 200]
        assert sorted(payload["duplicate"] for _, payload in publications) == [False, True]
        with SessionLocal() as check:
            package = check.get(EvaluationPackage, package_id)
            assert package is not None and package.status == "published"
            assert check.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.subject_type == "evaluation_package",
                    AuditEvent.subject_id == str(package_id),
                    AuditEvent.action == "approved",
                )
            ) == 1
            assert check.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.subject_type == "evaluation_package",
                    AuditEvent.subject_id == str(package_id),
                    AuditEvent.action == "published",
                )
            ) == 1
    finally:
        app.dependency_overrides.clear()
        fixture["engine"].dispose()


def test_reviewed_manifest_cannot_be_silently_changed() -> None:
    fixture = _fixture()
    client = fixture["client"]
    db = fixture["db"]
    try:
        package_id = client.post(
            "/api/evaluation-packages", json=_create_payload(fixture)
        ).json()["id"]
        assert client.post(
            f"/api/evaluation-packages/{package_id}/approve",
            json={"note": "冻结后批准"},
        ).status_code == 200
        db.rollback()
        try:
            db.execute(
                text(
                    "UPDATE evaluation_packages "
                    "SET canonical_manifest_json='{}' WHERE id=:id"
                ),
                {"id": package_id},
            )
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError("二审后数据库触发器必须拒绝清单漂移")
        assert client.get(
            f"/api/evaluation-packages/{package_id}"
        ).json()["manifest_hash_valid"] is True
    finally:
        _close(fixture["engine"], fixture["db"])
