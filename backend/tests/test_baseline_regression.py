import asyncio
import json
import importlib
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.baseline_regression import (
    complete_baseline_item,
    compute_level_metrics,
    fail_baseline_item,
    filename_level_suggestion,
    level_explanation,
)
from app.baseline_correction_orchestration import (
    CorrectionOrchestrationError,
    _normalize_generated_candidate,
)
from app.audit import canonical_json
from app.category_pipeline import default_pipeline
from app.category_evaluation_contract import canonical_contract_hash
from app.category_evaluation_v3_revisions import ensure_projected_revision
from app.database import Base, get_db
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
    space_schema_definition_for_version,
)
from app.doubao import DoubaoResponse
from app.main import (
    _baseline_run_selection,
    _required_baseline_v3_bundle,
    app,
    current_user,
)
from app.models import (
    Asset,
    BaselineCorrectionRun,
    BaselineRegressionItem,
    BaselineRegressionRun,
    BaselineSetItem,
    CategoryEvaluationV3Config,
    CategoryEvaluationV3Revision,
    EvaluationJob,
    EvaluationCategoryProfile,
    EvaluationResult,
    DimensionSchema,
    MaterialPackage,
    MaterialPackageItem,
    ModelConfig,
    OptimizationCaseQueue,
    PromptVersion,
    User,
)
from tests.v3_contract_fixtures import add_active_v3_contract


def test_baseline_run_selection_uses_frozen_v3_contract_dimension_counts() -> None:
    run = SimpleNamespace(
        strategy_snapshot_json="{}",
        execution_snapshot_json=json.dumps(
            {
                "category_key": "inspiration_image",
                "dimension_selection": {
                    "mode": "category_default",
                    "source_schema": {
                        "schema_key": "space_aesthetic",
                        "version": "1.3.0",
                        "canonical_hash": "legacy-eight-dimension-label",
                    },
                    "effective_keys": [f"legacy_{index}" for index in range(8)],
                },
                "v3_authoritative_bundle": {
                    "contract": {
                        "spec_version": "inspiration-v2-human-calibrated-20260805",
                        "track_classification": {
                            "tracks": [
                                {"key": "class_one", "label": "一类（建筑/室内）"},
                                {"key": "class_three", "label": "三类（其它杂图）"},
                            ]
                        },
                    },
                    "subcategory_dimensions": {
                        "class_one": {
                            "common_group": {
                                "schema_definition": {
                                    "dimensions": [{"key": "a"}, {"key": "b"}]
                                }
                            },
                            "specific_group": {
                                "schema_definition": {"dimensions": [{"key": "c"}]}
                            },
                        },
                        "class_three": {
                            "common_group": {
                                "schema_definition": {"dimensions": [{"key": "x"}]}
                            },
                            "specific_group": None,
                        },
                    },
                },
            }
        ),
    )

    selection = _baseline_run_selection(run)

    assert selection["dimension"]["v3_contract"] == {
        "spec_version": "inspiration-v2-human-calibrated-20260805",
        "tracks": [
            {"key": "class_one", "label": "一类（建筑/室内）", "dimension_count": 3},
            {"key": "class_three", "label": "三类（其它杂图）", "dimension_count": 1},
        ],
    }


def test_baseline_run_selection_marks_missing_v3_contract_unknown() -> None:
    run = SimpleNamespace(
        strategy_snapshot_json="{}",
        execution_snapshot_json=json.dumps(
            {
                "dimension_selection": {
                    "mode": "category_default",
                    "source_schema": {"schema_key": "space_aesthetic", "version": "1.3.0"},
                    "effective_keys": [f"legacy_{index}" for index in range(8)],
                }
            }
        ),
    )

    assert _baseline_run_selection(run)["dimension"]["v3_contract"] is None


def test_baseline_run_selection_preserves_frozen_v3_revision_metadata() -> None:
    run = SimpleNamespace(
        strategy_snapshot_json="{}",
        execution_snapshot_json=json.dumps(
            {
                "dimension_selection": {"mode": "category_default"},
                "v3_authoritative_bundle": {
                    "config_revision": 8,
                    "candidate_revision_id": 42,
                    "contract_hash": "b" * 64,
                    "contract": {
                        "spec_version": "candidate-v3",
                        "track_classification": {"tracks": [{"key": "main"}]},
                    },
                    "subcategory_dimensions": {"main": {}},
                },
            }
        ),
    )

    selection = _baseline_run_selection(run)

    assert selection["dimension"]["v3_contract"] == {
        "spec_version": "candidate-v3",
        "revision": 8,
        "revision_id": 42,
        "candidate_revision_id": 42,
        "contract_hash": "b" * 64,
        "tracks": [{"key": "main", "label": "main", "dimension_count": 0}],
    }


def test_baseline_run_can_freeze_candidate_v3_revision_without_changing_projection(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    active_artifacts = add_active_v3_contract(db, "inspiration_image")
    projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "inspiration_image"
        )
    )
    assert projected is not None
    active_revision = ensure_projected_revision(db, projected)
    candidate_contract = deepcopy(active_artifacts["contract"])
    candidate_contract["spec_version"] = "inspiration-candidate-regression-test-v1"
    candidate_contract["prompt_bindings"] = {
        "call_a_version": "candidate-A1",
        "call_b_version": "candidate-B1",
    }
    if isinstance(candidate_contract.get("aesthetic_foundation"), dict):
        candidate_contract["aesthetic_foundation"]["call_b_version"] = (
            "candidate-B1"
        )
    candidate = CategoryEvaluationV3Revision(
        category_key="inspiration_image",
        display_name="灵感图候选回归测试",
        revision=2,
        status="candidate",
        parent_revision_id=active_revision.id,
        contract_json=canonical_json(candidate_contract),
        classification_map_json=canonical_json(
            active_artifacts["classification_map"]
        ),
        subcategory_dimensions_json=canonical_json(
            active_artifacts["subcategory_dimensions"]
        ),
        dimension_deduction_rules_json="{}",
        media_penalty_enabled=False,
        contract_hash=canonical_contract_hash(candidate_contract),
        created_by="test",
    )
    user = User(
        username="candidate-runner",
        password_hash="unused",
        display_name="候选回归测试员",
    )
    asset = Asset(
        original_name="candidate-L1.jpg",
        stored_name="candidate-L1.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="c" * 64,
        status="uploaded",
        category_key="inspiration_image",
    )
    model = ModelConfig(
        name="candidate-model",
        provider="doubao",
        base_url="https://example.test",
        api_path="/chat",
        model_id="candidate-model",
        active=True,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="候选A",
        version="candidate-A1",
        system_prompt="classification prompt",
        user_prompt="classify",
        rubric_version="inspiration-rubric-v1",
        status="published",
        category_key="inspiration_image",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="候选B",
        version="candidate-B1",
        system_prompt="aesthetic prompt",
        user_prompt="evaluate",
        rubric_version="inspiration-rubric-v1",
        status="draft",
        pipeline_scope="baseline_regression",
        category_key="inspiration_image",
    )
    wrong_prompt_b = PromptVersion(
        stage="B",
        name="错误候选B",
        version="candidate-B2",
        system_prompt="wrong aesthetic prompt",
        user_prompt="evaluate with wrong binding",
        rubric_version="inspiration-rubric-v1",
        status="draft",
        pipeline_scope="baseline_regression",
        category_key="inspiration_image",
    )
    db.add_all(
        [candidate, user, asset, model, prompt_a, prompt_b, wrong_prompt_b]
    )
    db.commit()

    frozen_projection = (
        projected.projected_revision_id,
        projected.revision,
        projected.contract_hash,
        projected.contract_json,
    )
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        baseline_set = client.post(
            "/api/baseline-sets",
            json={
                "name": "候选 V3 小样本门禁",
                "default_expected_level": "L1",
                "category_key": "inspiration_image",
                "items": [{"asset_id": asset.id}],
            },
        )
        assert baseline_set.status_code == 200

        mismatched = client.post(
            f"/api/baseline-sets/{baseline_set.json()['id']}/runs",
            json={
                "prompt_a_id": prompt_a.id,
                "prompt_b_id": wrong_prompt_b.id,
                "candidate_revision_id": candidate.id,
            },
        )
        assert mismatched.status_code == 409
        assert mismatched.json()["detail"]["code"] == (
            "candidate_prompt_binding_mismatch"
        )
        assert db.query(BaselineRegressionRun).count() == 0

        run_response = client.post(
            f"/api/baseline-sets/{baseline_set.json()['id']}/runs",
            json={
                "prompt_a_id": prompt_a.id,
                "prompt_b_id": prompt_b.id,
                "candidate_revision_id": candidate.id,
            },
        )

        assert run_response.status_code == 200
        run = db.get(BaselineRegressionRun, run_response.json()["id"])
        assert run is not None
        snapshot = json.loads(run.execution_snapshot_json)
        assert snapshot["v3_authoritative_bundle"]["candidate_revision_id"] == candidate.id
        assert snapshot["v3_authoritative_bundle"]["contract"]["spec_version"] == (
            "inspiration-candidate-regression-test-v1"
        )
        job = db.get(EvaluationJob, run.items[0].job_id)
        assert job is not None
        assert json.loads(job.category_profile_snapshot_json) == snapshot
        db.refresh(projected)
        assert (
            projected.projected_revision_id,
            projected.revision,
            projected.contract_hash,
            projected.contract_json,
        ) == frozen_projection

        drifted_snapshot = json.loads(job.category_profile_snapshot_json)
        drifted_snapshot["v3_authoritative_bundle"]["contract"][
            "prompt_bindings"
        ]["call_b_version"] = wrong_prompt_b.version
        drifted_snapshot["v3_authoritative_bundle"]["contract"][
            "aesthetic_foundation"
        ]["call_b_version"] = wrong_prompt_b.version
        job.category_profile_snapshot_json = canonical_json(drifted_snapshot)
        job.status = "processing"
        db.commit()
        provider_constructions = 0

        class UnexpectedProviderClient:
            def __init__(self, _config) -> None:
                nonlocal provider_constructions
                provider_constructions += 1

        @contextmanager
        def test_scope():
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

        monkeypatch.setattr(worker, "session_scope", test_scope)
        monkeypatch.setattr(worker, "DoubaoClient", UnexpectedProviderClient)
        with pytest.raises(RuntimeError, match="候选合同 Prompt 绑定"):
            asyncio.run(worker.evaluate_job(job.id))
        assert provider_constructions == 0
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_candidate_baseline_bundle_rejects_revision_from_another_category() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    add_active_v3_contract(db, "inspiration_image")
    inspiration_projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "inspiration_image"
        )
    )
    assert inspiration_projected is not None
    ensure_projected_revision(db, inspiration_projected)
    other_artifacts = add_active_v3_contract(db, "space_image")
    other_projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "space_image"
        )
    )
    assert other_projected is not None
    other_active = ensure_projected_revision(db, other_projected)
    other_contract = deepcopy(other_artifacts["contract"])
    other_contract["spec_version"] = "space-candidate-test-v1"
    other_candidate = CategoryEvaluationV3Revision(
        category_key="space_image",
        display_name="空间图候选",
        revision=2,
        status="candidate",
        parent_revision_id=other_active.id,
        contract_json=canonical_json(other_contract),
        classification_map_json=canonical_json(other_artifacts["classification_map"]),
        subcategory_dimensions_json=canonical_json(
            other_artifacts["subcategory_dimensions"]
        ),
        contract_hash=canonical_contract_hash(other_contract),
        created_by="test",
    )
    db.add(other_candidate)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        _required_baseline_v3_bundle(
            db,
            "inspiration_image",
            other_candidate.id,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "candidate_revision_category_mismatch"
    db.close()
    engine.dispose()


def test_candidate_baseline_bundle_rejects_non_candidate_revision() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    add_active_v3_contract(db, "inspiration_image")
    projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "inspiration_image"
        )
    )
    assert projected is not None
    active_revision = ensure_projected_revision(db, projected)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        _required_baseline_v3_bundle(
            db,
            "inspiration_image",
            active_revision.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "candidate_revision_status_invalid"
    db.close()
    engine.dispose()


def test_candidate_baseline_bundle_rejects_projection_drift() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    artifacts = add_active_v3_contract(db, "inspiration_image")
    projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == "inspiration_image"
        )
    )
    assert projected is not None
    ensure_projected_revision(db, projected)
    stale_contract = deepcopy(artifacts["contract"])
    stale_contract["spec_version"] = "stale-candidate-test-v1"
    stale_root = CategoryEvaluationV3Revision(
        category_key="inspiration_image",
        display_name="旧现役根",
        revision=2,
        status="retired",
        parent_revision_id=None,
        contract_json=canonical_json(stale_contract),
        classification_map_json=canonical_json(artifacts["classification_map"]),
        subcategory_dimensions_json=canonical_json(
            artifacts["subcategory_dimensions"]
        ),
        contract_hash=canonical_contract_hash(stale_contract),
        created_by="test",
    )
    db.add(stale_root)
    db.flush()
    drifted_contract = deepcopy(stale_contract)
    drifted_contract["spec_version"] = "drifted-candidate-test-v1"
    drifted_candidate = CategoryEvaluationV3Revision(
        category_key="inspiration_image",
        display_name="已漂移候选",
        revision=3,
        status="candidate",
        parent_revision_id=stale_root.id,
        contract_json=canonical_json(drifted_contract),
        classification_map_json=canonical_json(artifacts["classification_map"]),
        subcategory_dimensions_json=canonical_json(
            artifacts["subcategory_dimensions"]
        ),
        contract_hash=canonical_contract_hash(drifted_contract),
        created_by="test",
    )
    db.add(drifted_candidate)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        _required_baseline_v3_bundle(
            db,
            "inspiration_image",
            drifted_candidate.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "candidate_revision_projection_drift"
    db.close()
    engine.dispose()


def test_filename_level_suggestion_is_advisory_and_conflict_safe() -> None:
    assert filename_level_suggestion("客厅效果图_L2.jpg")["suggested_level"] == "L2"
    assert filename_level_suggestion("厨房-中差.png")["suggested_level"] == "L3"
    assert filename_level_suggestion("卧室_过滤.webp")["suggested_level"] == "L5"
    assert filename_level_suggestion("户型l2draft.jpg")["status"] == "unmatched"
    conflict = filename_level_suggestion("客厅_L1_过滤.jpg")
    assert conflict["status"] == "conflict"
    assert conflict["suggested_level"] is None


def test_level_metrics_cover_boundaries_failures_and_stable_matrix() -> None:
    metrics = compute_level_metrics(
        [
            {"status": "completed", "expected_level": "L1", "predicted_level": "L1"},
            {"status": "completed", "expected_level": "L1", "predicted_level": "L2"},
            {"status": "completed", "expected_level": "L5", "predicted_level": "L4"},
            {"status": "completed", "expected_level": "L5", "predicted_level": "L3"},
            {"status": "failed", "expected_level": "L2", "predicted_level": None},
            {"status": "queued", "expected_level": "L3", "predicted_level": None},
        ]
    )
    assert metrics["exact_accuracy"] == 1 / 4
    assert metrics["adjacent_accuracy"] == 3 / 4
    assert metrics["denominator"] == 4
    assert metrics["valid_predictions"] == 4
    assert metrics["failed"] == 1
    assert metrics["pending"] == 1
    assert list(metrics["confusion_matrix"]) == ["L1", "L2", "L3", "L4", "L5"]
    assert metrics["confusion_matrix"]["L2"] == {
        "L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0,
    }
    assert compute_level_metrics([])["exact_accuracy"] == 0


def test_level_explanation_freezes_neutral_dimensions_and_quality_evidence() -> None:
    explanation = level_explanation(
        precheck={
            "image_quality": {
                "quality_severity": "moderate",
                "confidence": 0.82,
                "evidence": ["暗部细节损失", "过曝", "第三条", "第四条", "第五条", "截断"],
            }
        },
        aesthetic={
            "dimensions": {
                "layout": {"grade": 4, "evidence": ["完整"], "defects": []},
                "lighting": {"grade": 3, "evidence": ["基本均衡"], "defects": []},
                "material": {"grade": 1, "evidence": [], "defects": ["纹理错误"]},
            }
        },
        scoring={"caps": [{"cap": "L2", "reason": "画质受损最高 L2"}], "review_reasons": []},
        predicted_level="L2",
        authoritative_score=72,
    )
    assert [item["key"] for item in explanation["all_dimensions"]] == [
        "material", "lighting", "layout"
    ]
    assert explanation["image_quality"] == {
        "status": "available",
        "severity": "moderate",
        "severity_label": "中等",
        "confidence": 0.82,
        "evidence": ["暗部细节损失", "过曝", "第三条", "第四条", "第五条"],
    }


def test_correction_generator_accepts_one_layer_candidate_wrapper() -> None:
    candidate = {
        "candidate": {
            "prompt": {
                "stage": "A",
                "system_prompt": "system prompt with enough detail",
                "user_prompt": "user prompt",
                "change_note": "tighten anchors",
            },
            "revision": {
                "display_name": "wrapped candidate",
                "contract": {"category_key": "material_image"},
                "classification_map": {},
                "subcategory_dimensions": {},
            },
        }
    }

    normalized = _normalize_generated_candidate(candidate)

    assert normalized.prompt.stage == "A"
    assert normalized.revision.display_name == "wrapped candidate"


def test_correction_generator_reports_invalid_field_paths() -> None:
    with pytest.raises(
        CorrectionOrchestrationError,
        match=r"prompt\.system_prompt.*revision\.contract",
    ):
        _normalize_generated_candidate(
            {
                "prompt": {
                    "stage": "A",
                    "user_prompt": "user prompt",
                    "change_note": "missing system",
                },
                "revision": {
                    "display_name": "invalid candidate",
                    "classification_map": {},
                    "subcategory_dimensions": {},
                },
            }
        )


def test_baseline_api_freezes_truth_reports_and_enqueues_idempotently() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    add_active_v3_contract(db)
    user = User(username="tester", password_hash="unused", display_name="测试员")
    asset = Asset(
        original_name="baseline.jpg", stored_name="baseline.jpg", mime_type="image/jpeg",
        size_bytes=10, sha256="b" * 64, status="uploaded",
    )
    model = ModelConfig(
        name="test", provider="doubao", base_url="https://example.test",
        api_path="/chat", model_id="model", active=True,
    )
    prompt_a = PromptVersion(
        stage="A", name="A", version="A1", system_prompt="a", user_prompt="a",
        rubric_version="R1", status="published",
    )
    prompt_b = PromptVersion(
        stage="B", name="B", version="B1", system_prompt="b", user_prompt="b",
        rubric_version="R1", status="published",
    )
    db.add_all([user, asset, model, prompt_a, prompt_b])
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post("/api/baseline-sets", json={
            "name": "确认 L1 批次", "description": "truth",
            "default_expected_level": "L1", "items": [{"asset_id": asset.id}],
        })
        assert created.status_code == 200
        set_id = created.json()["id"]
        run_response = client.post(f"/api/baseline-sets/{set_id}/runs")
        assert run_response.status_code == 200
        run = db.get(BaselineRegressionRun, run_response.json()["id"])
        item = run.items[0]
        job = db.get(EvaluationJob, item.job_id)
        assert job.queue_class == "validation"
        assert job.baseline_regression_item_id == item.id
        result = EvaluationResult(
            asset_id=asset.id, job_id=job.id, strategy_bundle_id=run.strategy_bundle_id,
            strategy_snapshot_json=run.strategy_snapshot_json,
            precheck_json=json.dumps({"classification": {"scope_status": "in_scope"}}),
            aesthetic_json=json.dumps({
                "dimensions": {
                    "layout": {
                        "grade": 4,
                        "evidence": ["动线完整"],
                        "defects": [],
                    },
                    "lighting": {
                        "grade": 2,
                        "evidence": ["暗部细节不足"],
                        "defects": ["主灯过曝"],
                    },
                },
            }),
            scoring_json=json.dumps({
                "caps": [{"cap": "L2", "reason": "原样"}],
                "review_reasons": ["等级受限需人工确认"],
            }),
            raw_response_a="{}", raw_response_b="{}", score=65, level="L3",
            confidence=.9, needs_review=True, model_id=run.strategy_bundle.model_id,
            prompt_a_version="A1", prompt_b_version="B1", rubric_version="R1",
            engine_version=run.strategy_bundle.engine_version,
            risk_review_version=run.strategy_bundle.risk_review_version,
        )
        db.add(result)
        db.flush()
        complete_baseline_item(db, item_id=item.id, result=result)
        db.commit()
        detail = client.get(f"/api/baseline-regressions/{run.id}").json()
        assert detail["summary"]["metrics"]["exact_accuracy"] == 0
        assert detail["items"][0]["cap_reasons"][0]["reason"] == "原样"
        assert detail["items"][0]["stage_a"]["classification"]["scope_status"] == "in_scope"
        explanation = detail["items"][0]["level_explanation"]
        assert explanation["status"] == "available"
        assert explanation["predicted_level"] == "L3"
        assert explanation["authoritative_score"] == 65
        assert explanation["strong_dimensions"][0]["key"] == "layout"
        assert explanation["weak_dimensions"][0]["defects"] == ["主灯过曝"]
        assert explanation["review_reasons"] == ["等级受限需人工确认"]
        assert detail["items"][0]["evaluation"]["id"] == result.id
        assert detail["items"][0]["evaluation"]["review_stage"] == "initial"
        first = client.post(
            f"/api/baseline-regressions/{run.id}/optimization-cases",
            json={"item_ids": [item.id]},
        )
        second = client.post(
            f"/api/baseline-regressions/{run.id}/optimization-cases",
            json={"item_ids": [item.id]},
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["created"] == 1 and second.json()["created"] == 0
        case = db.query(OptimizationCaseQueue).one()
        assert case.source_type == "baseline_regression"
        case_payload = json.loads(case.case_json)
        assert case_payload["expected_level"] == "L1"
        assert case_payload["purpose"] == (
            "将模型与冻结基准不一致的样本送入统一优化案例队列，"
            "供人工证据与 AI 候选机制分析；不修改本轮真值，也不自动启用候选。"
        )
        assert first.json()["purpose"] == case_payload["purpose"]
        historical_snapshot = json.loads(item.result_snapshot_json)
        historical_snapshot.pop("level_explanation")
        item.result_snapshot_json = json.dumps(historical_snapshot)
        db.commit()
        historical = client.get(
            f"/api/baseline-regressions/{run.id}"
        ).json()["items"][0]["level_explanation"]
        assert historical["status"] == "unavailable_historical"
        assert historical["message"] == "历史结果未冻结评测理由"

        invalid_run_response = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"execution_mode": "structured"},
        )
        assert invalid_run_response.status_code == 200
        invalid_run = db.get(BaselineRegressionRun, invalid_run_response.json()["id"])
        invalid_item = invalid_run.items[0]
        invalid_job = db.get(EvaluationJob, invalid_item.job_id)
        invalid_result = EvaluationResult(
            asset_id=asset.id,
            job_id=invalid_job.id,
            strategy_bundle_id=invalid_run.strategy_bundle_id,
            strategy_snapshot_json=invalid_run.strategy_snapshot_json,
            precheck_json=json.dumps({"classification": {"scope_status": "in_scope"}}),
            aesthetic_json=None,
            scoring_json=json.dumps({"caps": [], "review_reasons": []}),
            raw_response_a="{}",
            raw_response_b=None,
            score=None,
            level=None,
            confidence=None,
            needs_review=False,
            model_id=invalid_run.strategy_bundle.model_id,
            prompt_a_version="A1",
            prompt_b_version="B1",
            rubric_version="R1",
            engine_version=invalid_run.strategy_bundle.engine_version,
            risk_review_version=invalid_run.strategy_bundle.risk_review_version,
        )
        db.add(invalid_result)
        db.flush()
        complete_baseline_item(db, item_id=invalid_item.id, result=invalid_result)
        db.commit()
        assert invalid_item.status == "failed"
        assert invalid_job.status == "failed"
        assert "missing_level" in invalid_item.error_message
        assert "no_authoritative_score" in invalid_item.error_message
        assert invalid_run.status == "failed"

        freeform_response = client.post(f"/api/baseline-sets/{set_id}/runs")
        assert freeform_response.status_code == 200
        freeform_run = db.get(
            BaselineRegressionRun, freeform_response.json()["id"]
        )
        assert freeform_response.json()["selection"]["execution_mode"] == "freeform"
        freeform_item = freeform_run.items[0]
        freeform_job = db.get(EvaluationJob, freeform_item.job_id)
        freeform_result = EvaluationResult(
            asset_id=asset.id,
            job_id=freeform_job.id,
            strategy_bundle_id=freeform_run.strategy_bundle_id,
            strategy_snapshot_json=freeform_run.strategy_snapshot_json,
            precheck_json="{}",
            aesthetic_json=None,
            scoring_json=json.dumps({"interpretation_status": "manual_required"}),
            raw_response_a=json.dumps(
                {"provider_payload": {"id": "provider-1"}, "raw_text": "自然语言结论"}
            ),
            raw_response_b=None,
            score=None,
            level=None,
            confidence=None,
            needs_review=False,
            model_id=freeform_run.strategy_bundle.model_id,
            prompt_a_version="A1",
            prompt_b_version="B1",
            rubric_version="R1",
            engine_version=freeform_run.strategy_bundle.engine_version,
            risk_review_version=freeform_run.strategy_bundle.risk_review_version,
        )
        db.add(freeform_result)
        db.flush()
        complete_baseline_item(
            db, item_id=freeform_item.id, result=freeform_result
        )
        db.commit()
        assert freeform_item.status == "completed"
        detail = client.get(
            f"/api/baseline-regressions/{freeform_run.id}"
        ).json()
        assert detail["summary"]["metrics"]["unscored"] == 1
        assert detail["summary"]["metrics"]["manual_required"] == 1
        assert detail["summary"]["metrics"]["denominator"] == 0
        assert detail["items"][0]["interpretation"] == {
            "status": "manual_required",
            "raw_text_a": "自然语言结论",
            "raw_text_b": None,
        }
        db.refresh(asset)
        assert asset.status == "uploaded"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_baseline_whole_package_supports_10000_items_and_jobs() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    add_active_v3_contract(db)
    user = User(username="bulk-tester", password_hash="unused", display_name="批量测试员")
    model = ModelConfig(
        name="bulk-model",
        provider="doubao",
        base_url="https://example.test",
        api_path="/chat",
        model_id="bulk-model",
        active=True,
    )
    prompt_a = PromptVersion(
        stage="A", name="批量A", version="bulk-A", system_prompt="system a",
        user_prompt="user a", rubric_version="R1", status="published",
    )
    prompt_b = PromptVersion(
        stage="B", name="批量B", version="bulk-B", system_prompt="system b",
        user_prompt="user b", rubric_version="R1", status="published",
    )
    db.add_all([user, model, prompt_a, prompt_b])
    db.flush()
    db.bulk_save_objects([
        Asset(
            original_name=f"bulk-{index:05d}-L2.jpg",
            stored_name=f"bulk-{index:05d}.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256=f"{index:064x}",
            status="uploaded",
        )
        for index in range(10_000)
    ])
    db.commit()
    asset_ids = db.scalars(select(Asset.id).order_by(Asset.id.asc())).all()
    package = MaterialPackage(
        package_key="baseline-bulk-10000",
        name="一万张基准包",
        source="manual_upload",
        category_key="space_image",
        created_by=user.username,
    )
    db.add(package)
    db.flush()
    db.bulk_save_objects([
        MaterialPackageItem(
            package_id=package.id,
            asset_id=asset_id,
            original_name=f"bulk-{index:05d}-L2.jpg",
            position=index,
        )
        for index, asset_id in enumerate(asset_ids)
    ])
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            "/api/baseline-sets",
            json={
                "name": "一万张容量验收",
                "default_expected_level": "L1",
                "source_package_id": package.id,
            },
        )
        assert created.status_code == 200
        assert created.json()["item_count"] == 10_000
        baseline_set_id = created.json()["id"]
        assert db.scalar(
            select(func.count()).select_from(BaselineSetItem).where(
                BaselineSetItem.baseline_set_id == baseline_set_id
            )
        ) == 10_000

        run = client.post(f"/api/baseline-sets/{baseline_set_id}/runs")
        assert run.status_code == 200
        assert run.json()["total"] == 10_000
        assert len(run.json()["job_ids"]) == 10_000
        assert db.scalar(
            select(func.count()).select_from(EvaluationJob).where(
                EvaluationJob.baseline_regression_item_id.is_not(None)
            )
        ) == 10_000

        compact_set = client.get(
            f"/api/baseline-sets/{baseline_set_id}?include_items=false"
        )
        assert compact_set.status_code == 200
        assert compact_set.json()["summary"]["item_count"] == 10_000
        assert compact_set.json()["items"] == []

        run_detail = client.get(
            f"/api/baseline-regressions/{run.json()['id']}?limit=200&offset=200"
        )
        assert run_detail.status_code == 200
        assert len(run_detail.json()["items"]) == 200
        assert run_detail.json()["pagination"] == {
            "offset": 200,
            "limit": 200,
            "total": 10_000,
        }
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_cancel_jobs_finishes_baseline_run_and_allows_next_run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    add_active_v3_contract(db)
    user = User(
        username="baseline-cancel-tester",
        password_hash="unused",
        display_name="基准取消测试员",
    )
    asset = Asset(
        original_name="cancel-L2.jpg",
        stored_name="cancel-L2.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="1" * 64,
        status="uploaded",
    )
    model = ModelConfig(
        name="test",
        provider="doubao",
        base_url="https://example.test",
        api_path="/chat",
        model_id="model",
        active=True,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="A",
        version="cancel-A1",
        system_prompt="classification prompt",
        user_prompt="classify",
        rubric_version="R1",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="B",
        version="cancel-B1",
        system_prompt="aesthetic prompt",
        user_prompt="evaluate",
        rubric_version="R1",
        status="published",
    )
    db.add_all([user, asset, model, prompt_a, prompt_b])
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        baseline_set = client.post(
            "/api/baseline-sets",
            json={
                "name": "可取消基准集",
                "default_expected_level": "L2",
                "items": [{"asset_id": asset.id}],
            },
        ).json()
        first = client.post(
            f"/api/baseline-sets/{baseline_set['id']}/runs"
        )
        assert first.status_code == 200
        first_run = db.get(BaselineRegressionRun, first.json()["id"])
        first_item = first_run.items[0]

        canceled = client.post("/api/jobs/control/cancel")
        assert canceled.status_code == 200
        db.expire_all()
        assert db.get(EvaluationJob, first_item.job_id).status == "canceled"
        assert db.get(BaselineRegressionItem, first_item.id).status == "failed"
        assert db.get(BaselineRegressionRun, first_run.id).status == "failed"
        assert (
            db.get(BaselineRegressionRun, first_run.id).finished_at
            is not None
        )

        second = client.post(
            f"/api/baseline-sets/{baseline_set['id']}/runs"
        )
        assert second.status_code == 200
        assert second.json()["sequence_no"] == 2
        assert second.json()["previous_run_id"] == first_run.id
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_package_baseline_prefills_filename_level_and_accepts_manual_override() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(username="tester", password_hash="unused", display_name="测试员")
    l2_asset = Asset(
        original_name="stored-a.jpg",
        stored_name="stored-a.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="d" * 64,
        status="uploaded",
    )
    override_asset = Asset(
        original_name="stored-b.jpg",
        stored_name="stored-b.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="e" * 64,
        status="uploaded",
    )
    package = MaterialPackage(
        package_key="filename-level-package",
        name="文件名等级包",
        source="manual_upload",
        created_by=user.username,
    )
    package.items = [
        MaterialPackageItem(
            asset=l2_asset,
            original_name="客厅_L2.jpg",
            position=0,
        ),
        MaterialPackageItem(
            asset=override_asset,
            original_name="卧室_过滤.jpg",
            position=1,
        ),
    ]
    db.add_all([user, package])
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            "/api/baseline-sets",
            json={
                "name": "文件名预填基准",
                "description": "",
                "default_expected_level": "L1",
                "source_package_id": package.id,
                "expected_level_overrides": {
                    str(override_asset.id): "L3",
                },
                "items": [],
            },
        )
        assert created.status_code == 200
        detail = client.get(f"/api/baseline-sets/{created.json()['id']}")
        assert detail.status_code == 200
        by_asset = {
            item["asset_id"]: item
            for item in detail.json()["items"]
        }
        assert by_asset[l2_asset.id]["expected_level"] == "L2"
        assert (
            by_asset[l2_asset.id]["asset"]["expected_level_source"]
            == "filename"
        )
        assert by_asset[override_asset.id]["expected_level"] == "L3"
        assert (
            by_asset[override_asset.id]["asset"]["expected_level_source"]
            == "manual_override"
        )
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_baseline_run_can_freeze_manual_prompt_pair_and_reserves_dimension_choice(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    add_active_v3_contract(db)
    user = User(
        username="tester",
        password_hash="unused",
        display_name="测试员",
    )
    asset = Asset(
        original_name="manual-version.jpg",
        stored_name="manual-version.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="c" * 64,
        status="uploaded",
    )
    model = ModelConfig(
        name="test",
        provider="doubao",
        base_url="https://example.test",
        api_path="/chat",
        model_id="model",
        encrypted_api_key="test-reference",
        active=True,
    )
    published_a = PromptVersion(
        stage="A",
        name="发布 A",
        version="A-published",
        system_prompt="published system a",
        user_prompt="published user a",
        rubric_version="R1",
        status="published",
    )
    published_b = PromptVersion(
        stage="B",
        name="发布 B",
        version="B-published",
        system_prompt="published system b",
        user_prompt="published user b",
        rubric_version="R1",
        status="published",
    )
    draft_a = PromptVersion(
        stage="A",
        name="候选 A",
        version="A-draft",
        system_prompt="draft system a",
        user_prompt="draft user a",
        rubric_version="R2",
        status="draft",
    )
    draft_b = PromptVersion(
        stage="B",
        name="候选 B",
        version="B-draft",
        system_prompt="draft system b",
        user_prompt="B sees {{previous_output}} / {{precheck_json}}",
        rubric_version="R2",
        status="draft",
    )
    full_only = PromptVersion(
        stage="A",
        name="仅完整流水线",
        version="A-full-only",
        system_prompt="full pipeline only system prompt",
        user_prompt="full pipeline only user prompt",
        rubric_version="R2",
        pipeline_scope="full_pipeline",
        status="draft",
    )
    dimension_definition = space_schema_definition_for_version(ACTIVE_V13_VERSION)
    dimension_schema = DimensionSchema(
        schema_key=SPACE_SCHEMA_KEY,
        version=ACTIVE_V13_VERSION,
        schema_type="family_pack",
        family_key="space",
        display_name="空间现役维度",
        status="published",
        definition_json=canonical_json(dimension_definition),
        canonical_hash=canonical_hash(dimension_definition),
        created_by="test",
        published_by="test",
        published_at=datetime.now(timezone.utc),
    )
    db.add_all(
        [
            user,
            asset,
            model,
            published_a,
            published_b,
            draft_a,
            draft_b,
            full_only,
            dimension_schema,
        ]
    )
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            "/api/baseline-sets",
            json={
                "name": "手选版本基准",
                "description": "",
                "default_expected_level": "L2",
                "items": [{"asset_id": asset.id}],
            },
        )
        assert created.status_code == 200
        set_id = created.json()["id"]

        single_conflict = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"prompt_id": draft_a.id, "prompt_a_id": draft_a.id, "prompt_b_id": draft_b.id},
        )
        assert single_conflict.status_code == 422
        assert "不能同时指定" in single_conflict.text

        single = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"prompt_id": draft_a.id},
        )
        assert single.status_code == 200
        single_payload = single.json()
        assert single_payload["selection"]["prompt_a"]["id"] == draft_a.id
        assert single_payload["selection"]["prompt_b"] is None
        assert single_payload["selection"]["prompt_a"]["rubric_version"] == "R2"
        single_run = db.get(BaselineRegressionRun, single_payload["id"])
        assert single_run is not None
        single_job = db.get(EvaluationJob, single_run.items[0].job_id)
        assert single_job is not None
        assert single_job.prompt_a_id == draft_a.id
        assert single_job.prompt_b_id is None
        assert json.loads(single_run.strategy_snapshot_json)["prompt_b"] is None
        fail_baseline_item(
            db,
            item_id=single_run.items[0].id,
            error_code="test_single_prompt_finished",
        )
        db.commit()

        scope_rejected = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"prompt_id": full_only.id},
        )
        assert scope_rejected.status_code == 422
        assert "不允许用于基准回归" in scope_rejected.text

        partial = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"prompt_a_id": draft_a.id},
        )
        assert partial.status_code == 422
        assert "必须同时指定 A 与 B" in partial.text

        reserved_dimension = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"dimension_schema_id": 99991},
        )
        assert reserved_dimension.status_code == 410
        assert reserved_dimension.json()["detail"]["code"] == "legacy_dimension_write_retired"

        missing = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"prompt_a_id": 99991, "prompt_b_id": 99992},
        )
        assert missing.status_code == 404
        assert "提示词版本不存在" in missing.text

        swapped = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={
                "prompt_a_id": draft_b.id,
                "prompt_b_id": draft_a.id,
            },
        )
        assert swapped.status_code == 422
        assert "提示词阶段不匹配" in swapped.text
        assert db.query(BaselineRegressionRun).count() == 1

        response = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={
                "prompt_a_id": draft_a.id,
                "prompt_b_id": draft_b.id,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["selection"]["prompt_a"]["id"] == draft_a.id
        assert payload["selection"]["prompt_a"]["version"] == "A-draft"
        assert payload["selection"]["prompt_b"]["id"] == draft_b.id
        assert payload["selection"]["prompt_b"]["version"] == "B-draft"
        assert payload["selection"]["dimension"]["v3_contract"] is not None
        assert payload["selection"]["dimension"]["v3_contract"]["tracks"]

        run = db.get(BaselineRegressionRun, payload["id"])
        assert run is not None
        job = db.get(EvaluationJob, run.items[0].job_id)
        assert job is not None
        assert job.prompt_a_id == draft_a.id
        assert job.prompt_b_id == draft_b.id
        job.status = "processing"
        db.commit()

        source_path = tmp_path / asset.stored_name
        source_path.write_bytes(b"freeform-image")
        calls: list[str] = []

        class FakeClient:
            def __init__(self, _config) -> None:
                pass

            async def chat_text(self, _system_prompt, user_prompt, **_kwargs):
                calls.append(user_prompt)
                raw_text = (
                    "A 自由结论"
                    if len(calls) == 1
                    else "B 自由结论，已参考 A 自由结论"
                )
                return DoubaoResponse(
                    parsed={},
                    raw_text=raw_text,
                    raw_payload={"provider_id": f"call-{len(calls)}"},
                )

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
        monkeypatch.setattr(worker, "DoubaoClient", FakeClient)
        monkeypatch.setattr(
            worker,
            "prepare_model_image",
            lambda *_args, **_kwargs: (source_path, "image/jpeg"),
        )
        asyncio.run(worker.evaluate_job(job.id))
        db.expire_all()
        completed_job = db.get(EvaluationJob, job.id)
        completed_item = db.get(BaselineRegressionItem, run.items[0].id)
        result = db.query(EvaluationResult).filter_by(job_id=job.id).one()
        scoring = json.loads(result.scoring_json)
        assert completed_job.status == "completed"
        assert completed_item.status == "completed"
        assert result.level is not None
        assert result.score is not None
        assert scoring["scoring_mode"] == "v3_authoritative"
        assert calls == ["draft user a"]

        detail = client.get(
            f"/api/baseline-regressions/{run.id}"
        )
        assert detail.status_code == 200, detail.text
        assert (
            detail.json()["summary"]["selection"]["prompt_b"]["version"]
            == "B-draft"
        )
        interpretation = detail.json()["items"][0]["interpretation"]
        assert interpretation["status"] == "scored"
        assert "调用B失败" in interpretation["raw_text_b"]
        set_detail = client.get(f"/api/baseline-sets/{set_id}")
        assert (
            set_detail.json()["runs"][0]["selection"]["prompt_a"]["version"]
            == "A-draft"
        )
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_category_dimension_snapshot_and_isolated_correction_retry(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    active_artifacts = add_active_v3_contract(db, "material_image")
    user = User(
        username="baseline-analysis-tester",
        password_hash="unused",
        display_name="基准分析测试员",
    )
    manager = User(
        username="baseline-analysis-manager",
        password_hash="unused",
        display_name="基准分析项目管理员",
        role="manager",
        is_admin=False,
    )
    asset = Asset(
        original_name="material-L1.jpg",
        stored_name="material-L1.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="9" * 64,
        category_key="material_image",
        status="uploaded",
    )
    model = ModelConfig(
        name="material-model",
        provider="generic",
        base_url="https://example.test",
        api_path="/chat",
        model_id="material-model-v1",
        active=True,
    )
    prompt = PromptVersion(
        category_key="material_image",
        stage="A",
        name="材质单提示词",
        version="material-single-v1",
        system_prompt="evaluate material",
        user_prompt="evaluate",
        rubric_version="R-material-1",
        status="draft",
    )
    pipeline = default_pipeline("material_image")
    pipeline["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "selected_keys": [
            "color_material",
            "detail_completion",
        ],
    }
    profile = EvaluationCategoryProfile(
        category_key="material_image",
        display_name="材质图",
        status="active",
        allowed_mime_types_json='["image/jpeg"]',
        preprocess_config_json='{"preprocess":"image"}',
        pipeline_config_json=canonical_json(pipeline),
        rubric_version="R-material-1",
        created_by=user.username,
    )
    db.add_all([user, manager, asset, model, prompt, profile])
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        cross_category = client.post(
            "/api/baseline-sets",
            json={
                "name": "错误类目基准",
                "category_key": "space_image",
                "default_expected_level": "L1",
                "items": [{"asset_id": asset.id}],
            },
        )
        assert cross_category.status_code == 409
        assert "不允许混入" in cross_category.text

        created_set = client.post(
            "/api/baseline-sets",
            json={
                "name": "材质存量基准",
                "category_key": "material_image",
                "default_expected_level": "L1",
                "items": [{"asset_id": asset.id}],
            },
        )
        assert created_set.status_code == 200
        assert created_set.json()["category_key"] == "material_image"

        created_run = client.post(
            f"/api/baseline-sets/{created_set.json()['id']}/runs",
            json={"prompt_id": prompt.id},
        )
        assert created_run.status_code == 200
        run = db.get(BaselineRegressionRun, created_run.json()["id"])
        assert run is not None
        assert run.category_key == "material_image"
        selection = created_run.json()["selection"]["dimension"]
        assert selection["v3_contract"] is not None
        assert selection["v3_contract"]["tracks"]
        job = db.get(EvaluationJob, run.items[0].job_id)
        assert job is not None
        assert job.category_key == "material_image"
        frozen_job = json.loads(job.category_profile_snapshot_json)
        assert frozen_job["dimension_selection"] == {
            key: selection[key]
            for key in (
                "schema_version",
                "enabled",
                "mode",
                "selected_keys",
                "effective_keys",
                "prompt_only",
                "source_schema",
            )
        }

        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job.id,
            strategy_bundle_id=run.strategy_bundle_id,
            strategy_snapshot_json=run.strategy_snapshot_json,
            precheck_json=json.dumps(
                {"classification": {"scope_status": "in_scope"}}
            ),
            aesthetic_json=json.dumps(
                {
                    "dimensions": {
                        "color_material": {
                            "grade": 1,
                            "evidence": ["材质失真"],
                            "defects": ["纹理模糊"],
                        },
                        "detail_completion": {
                            "grade": 2,
                            "evidence": ["收口粗糙"],
                            "defects": ["边缘破损"],
                        },
                    }
                }
            ),
            scoring_json=json.dumps({"caps": [], "review_reasons": []}),
            raw_response_a="{}",
            raw_response_b=None,
            score=45,
            level="L3",
            confidence=0.8,
            needs_review=True,
            model_id=model.model_id,
            prompt_a_version=prompt.version,
            prompt_b_version=None,
            rubric_version=prompt.rubric_version,
            engine_version=run.strategy_bundle.engine_version,
            risk_review_version=run.strategy_bundle.risk_review_version,
        )
        db.add(result)
        db.flush()
        job.category_key = "space_image"
        with pytest.raises(ValueError, match="类目与冻结 run 不一致"):
            complete_baseline_item(
                db,
                item_id=run.items[0].id,
                result=result,
            )
        job.category_key = "material_image"
        complete_baseline_item(
            db,
            item_id=run.items[0].id,
            result=result,
        )
        db.commit()
        frozen_result = json.loads(run.items[0].result_snapshot_json)
        assert frozen_result["category_key"] == "material_image"
        assert frozen_result["dimension_selection"]["effective_keys"] == [
            "color_material",
            "detail_completion",
        ]

        prompt_only = client.post(
            f"/api/baseline-sets/{created_set.json()['id']}/runs",
            json={"prompt_id": prompt.id, "dimension_mode": "none"},
        )
        assert prompt_only.status_code == 410
        assert prompt_only.json()["detail"]["code"] == "legacy_dimension_write_retired"

        candidate_contract = deepcopy(active_artifacts["contract"])
        candidate_contract["spec_version"] = "material-image-auto-candidate-v2"
        candidate_dimensions = deepcopy(
            active_artifacts["subcategory_dimensions"]
        )

        class DeterministicGenerator:
            def generate(self, **_kwargs):
                return {
                    "prompt": {
                        "stage": "A",
                        "system_prompt": "evaluate material with corrected anchors",
                        "user_prompt": "evaluate with frozen correction evidence",
                        "change_note": "自动纠偏：收紧 L1 与 L3 的材质锚点",
                    },
                    "revision": {
                        "display_name": "材质图自动纠偏候选",
                        "contract": candidate_contract,
                        "classification_map": deepcopy(
                            active_artifacts["classification_map"]
                        ),
                        "subcategory_dimensions": candidate_dimensions,
                    },
                    "summary": {
                        "change_codes": ["prompt_anchor", "level_boundary"]
                    },
                    "model_snapshot": {
                        "role": "tuning",
                        "model_id": "deterministic-test-generator",
                    },
                }

        main_module = importlib.import_module("app.main")
        monkeypatch.setattr(
            main_module,
            "configured_correction_generator",
            lambda _db: DeterministicGenerator(),
            raising=False,
        )
        active_config = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == "material_image"
            )
        )
        assert active_config is not None
        frozen_active_projection = (
            active_config.revision,
            active_config.contract_hash,
            active_config.contract_json,
        )

        correction = client.post(
            f"/api/baseline-regressions/{run.id}/corrections",
            json={
                "item_ids": [run.items[0].id],
                "idempotency_key": "material-correction-001",
            },
        )
        assert correction.status_code == 200
        correction_payload = correction.json()
        assert correction_payload["status"] == "processing"
        assert correction_payload["stage"] in {"analysis", "candidate_generation", "regression"}
        correction_payload = client.get(
            f"/api/baseline-corrections/{correction_payload['id']}"
        ).json()
        assert correction_payload["status"] == "processing"
        assert correction_payload["stage"] == "regression"
        assert correction_payload["blockers"] == []
        assert correction_payload["candidate_revision_id"] is not None
        assert correction_payload["regression_run_id"] is not None
        assert correction_payload["report"]["accuracy_report"] == {
            "run_metrics": json.loads(run.metrics_json),
            "selected_deviation_count": 1,
            "average_level_distance": 2.0,
            "direction_counts": {"under_rated": 1},
            "confusion_pairs": [{"pair": "L1→L3", "count": 1}],
        }
        assert correction_payload["report"]["publication"]["allowed"] is False
        assert db.query(PromptVersion).count() == 2
        candidate_prompt = db.scalar(
            select(PromptVersion).where(PromptVersion.id != prompt.id)
        )
        assert candidate_prompt is not None
        assert candidate_prompt.status == "draft"
        assert candidate_prompt.source == "auto_correction"
        candidate_revision = db.get(
            CategoryEvaluationV3Revision,
            correction_payload["candidate_revision_id"],
        )
        assert candidate_revision is not None
        assert candidate_revision.status == "candidate"
        frozen_candidate_contract = json.loads(candidate_revision.contract_json)
        assert frozen_candidate_contract["spec_version"] == (
            "material-image-auto-candidate-v2"
        )
        assert frozen_candidate_contract["prompt_bindings"] == {
            "call_a_version": candidate_prompt.version,
            "call_b_version": None,
        }
        db.refresh(active_config)
        assert (
            active_config.revision,
            active_config.contract_hash,
            active_config.contract_json,
        ) == frozen_active_projection
        candidate_run = db.get(
            BaselineRegressionRun,
            correction_payload["regression_run_id"],
        )
        assert candidate_run is not None
        assert candidate_run.status == "running"
        assert [item.baseline_set_item_id for item in candidate_run.items] == [
            item.baseline_set_item_id for item in run.items
        ]
        candidate_job = db.get(EvaluationJob, candidate_run.items[0].job_id)
        assert candidate_job is not None
        frozen_candidate_job = json.loads(
            candidate_job.category_profile_snapshot_json
        )
        assert frozen_candidate_job["v3_authoritative_bundle"][
            "candidate_revision_id"
        ] == candidate_revision.id
        assert candidate_job.prompt_a_id == candidate_prompt.id

        still_running = client.get(
            f"/api/baseline-corrections/{correction_payload['id']}"
        )
        assert still_running.status_code == 200
        assert still_running.json()["status"] == "processing"
        premature = client.post(
            f"/api/baseline-corrections/{correction_payload['id']}/decision",
            json={"decision": "approved", "note": "回归未完成不能启用"},
        )
        assert premature.status_code == 409

        candidate_result = EvaluationResult(
            asset_id=asset.id,
            job_id=candidate_job.id,
            strategy_bundle_id=candidate_run.strategy_bundle_id,
            strategy_snapshot_json=candidate_run.strategy_snapshot_json,
            precheck_json=json.dumps(
                {"classification": {"scope_status": "in_scope"}}
            ),
            aesthetic_json=json.dumps(
                {
                    "dimensions": {
                        "color_material": {
                            "grade": 5,
                            "evidence": ["材质表达准确"],
                            "defects": [],
                        },
                        "detail_completion": {
                            "grade": 5,
                            "evidence": ["细节完整"],
                            "defects": [],
                        },
                    }
                }
            ),
            scoring_json=json.dumps({"caps": [], "review_reasons": []}),
            raw_response_a="{}",
            raw_response_b=None,
            score=95,
            level="L1",
            confidence=0.95,
            needs_review=False,
            model_id=model.model_id,
            prompt_a_version=candidate_prompt.version,
            prompt_b_version=None,
            rubric_version=candidate_prompt.rubric_version,
            engine_version=candidate_run.strategy_bundle.engine_version,
            risk_review_version=candidate_run.strategy_bundle.risk_review_version,
        )
        db.add(candidate_result)
        db.flush()
        complete_baseline_item(
            db,
            item_id=candidate_run.items[0].id,
            result=candidate_result,
        )
        db.commit()

        ready = client.get(
            f"/api/baseline-corrections/{correction_payload['id']}"
        )
        assert ready.status_code == 200
        ready_payload = ready.json()
        assert ready_payload["status"] == "awaiting_decision"
        assert ready_payload["stage"] == "decision"
        assert ready_payload["report"]["candidate_regression"][
            "approval_allowed"
        ] is True
        assert ready_payload["report"]["candidate_regression"][
            "recommendation"
        ] == "approve"

        frozen_profile_prompt_id = profile.prompt_a_id
        frozen_projection_before_decisions = (
            active_config.projected_revision_id,
            active_config.revision,
            active_config.contract_hash,
        )

        rejected_correction = BaselineCorrectionRun(
            idempotency_key="material-correction-reject-boundary",
            baseline_run_id=run.id,
            category_key=run.category_key,
            selected_item_ids_json=canonical_json(
                correction_payload["selected_item_ids"]
            ),
            input_snapshot_json=canonical_json({"boundary_test": "rejection"}),
            status="awaiting_decision",
            stage="decision",
            progress=100,
            report_json=canonical_json(ready_payload["report"]),
            blockers_json="[]",
            orchestration_json=canonical_json(correction_payload["orchestration"]),
            candidate_revision_id=candidate_revision.id,
            regression_run_id=candidate_run.id,
            created_by=user.username,
        )
        db.add(rejected_correction)
        db.commit()
        app.dependency_overrides[current_user] = lambda: manager
        manager_decision = client.post(
            f"/api/baseline-corrections/{rejected_correction.id}/decision",
            json={"decision": "rejected", "note": "项目管理员不应具备发布权"},
        )
        assert manager_decision.status_code == 403
        db.refresh(rejected_correction)
        assert rejected_correction.status == "awaiting_decision"
        app.dependency_overrides[current_user] = lambda: user
        rejected = client.post(
            f"/api/baseline-corrections/{rejected_correction.id}/decision",
            json={"decision": "rejected", "note": "保留现役机制"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        db.refresh(profile)
        db.refresh(active_config)
        assert profile.prompt_a_id == frozen_profile_prompt_id
        assert (
            active_config.projected_revision_id,
            active_config.revision,
            active_config.contract_hash,
        ) == frozen_projection_before_decisions

        failed_recommendation_report = deepcopy(ready_payload["report"])
        failed_recommendation_report["candidate_regression"].update(
            {
                "approval_allowed": False,
                "recommendation": "reject",
                "regressions": [
                    {
                        "code": "exact_accuracy_regressed",
                        "message": "Exact Accuracy 低于基准",
                        "delta": -0.1,
                    }
                ],
            }
        )
        failed_recommendation = BaselineCorrectionRun(
            idempotency_key="material-correction-failed-recommendation",
            baseline_run_id=run.id,
            category_key=run.category_key,
            selected_item_ids_json=canonical_json(correction_payload["selected_item_ids"]),
            input_snapshot_json=canonical_json(
                {"boundary_test": "failed_recommendation"}
            ),
            status="awaiting_decision",
            stage="decision",
            progress=100,
            report_json=canonical_json(failed_recommendation_report),
            blockers_json="[]",
            orchestration_json=canonical_json(correction_payload["orchestration"]),
            candidate_revision_id=candidate_revision.id,
            regression_run_id=candidate_run.id,
            created_by=user.username,
        )
        db.add(failed_recommendation)
        db.commit()
        forbidden_approval = client.post(
            f"/api/baseline-corrections/{failed_recommendation.id}/decision",
            json={"decision": "approved", "note": "不应允许启用"},
        )
        assert forbidden_approval.status_code == 409
        assert forbidden_approval.json()["detail"] == "候选回归未通过，不能启用"
        db.refresh(profile)
        db.refresh(active_config)
        assert profile.prompt_a_id == frozen_profile_prompt_id
        assert (
            active_config.projected_revision_id,
            active_config.revision,
            active_config.contract_hash,
        ) == frozen_projection_before_decisions

        drifted_orchestration = deepcopy(correction_payload["orchestration"])
        drifted_orchestration["base_projection"]["revision"] -= 1
        projection_drift = BaselineCorrectionRun(
            idempotency_key="material-correction-projection-drift",
            baseline_run_id=run.id,
            category_key=run.category_key,
            selected_item_ids_json=canonical_json(correction_payload["selected_item_ids"]),
            input_snapshot_json=canonical_json(
                {"boundary_test": "projection_drift"}
            ),
            status="awaiting_decision",
            stage="decision",
            progress=100,
            report_json=canonical_json(ready_payload["report"]),
            blockers_json="[]",
            orchestration_json=canonical_json(drifted_orchestration),
            candidate_revision_id=candidate_revision.id,
            regression_run_id=candidate_run.id,
            created_by=user.username,
        )
        db.add(projection_drift)
        db.commit()
        drift_conflict = client.post(
            f"/api/baseline-corrections/{projection_drift.id}/decision",
            json={"decision": "approved", "note": "现役已漂移"},
        )
        assert drift_conflict.status_code == 409
        assert drift_conflict.json()["detail"]["code"] == (
            "projected_revision_conflict"
        )
        db.refresh(profile)
        db.refresh(active_config)
        assert profile.prompt_a_id == frozen_profile_prompt_id
        assert (
            active_config.projected_revision_id,
            active_config.revision,
            active_config.contract_hash,
        ) == frozen_projection_before_decisions

        approved = client.post(
            f"/api/baseline-corrections/{correction_payload['id']}/decision",
            json={"decision": "approved", "note": "回归无退化，启用候选"},
        )
        assert approved.status_code == 200, approved.text
        approved_payload = approved.json()
        assert approved_payload["status"] == "approved"
        assert approved_payload["decision"] == "approved"
        assert approved_payload["decided_by"] == user.username
        assert approved_payload["mechanism_refresh"] == {
            "category_key": run.category_key,
            "prompt_version_ids": [candidate_prompt.id],
            "v3_revision_id": candidate_revision.id,
            "contract_hash": candidate_run.correction_contract_hash,
        }
        db.refresh(profile)
        db.refresh(active_config)
        db.refresh(candidate_revision)
        assert profile.prompt_a_id == candidate_prompt.id
        assert candidate_prompt.status == "published"
        assert active_config.projected_revision_id == candidate_revision.id
        assert active_config.revision == candidate_revision.revision
        assert active_config.contract_hash == candidate_revision.contract_hash
        assert candidate_revision.status == "active"

        repeated = client.post(
            f"/api/baseline-corrections/{correction_payload['id']}/decision",
            json={"decision": "approved", "note": "回归无退化，启用候选"},
        )
        assert repeated.status_code == 200
        conflicting = client.post(
            f"/api/baseline-corrections/{correction_payload['id']}/decision",
            json={"decision": "rejected", "note": "改为拒绝"},
        )
        assert conflicting.status_code == 409

        class FailingGenerator:
            def generate(self, **_kwargs):
                raise ValueError("deterministic-analysis-test-failure")

        monkeypatch.setattr(
            main_module,
            "configured_correction_generator",
            lambda _db: FailingGenerator(),
        )
        failed = client.post(
            f"/api/baseline-regressions/{run.id}/corrections",
            json={
                "item_ids": [run.items[0].id],
                "idempotency_key": "material-correction-002",
            },
        )
        assert failed.status_code == 200
        failed_payload = client.get(
            f"/api/baseline-corrections/{failed.json()['id']}"
        ).json()
        assert failed_payload["status"] == "failed"
        assert failed_payload["error"]["retryable"] is True

        monkeypatch.setattr(
            main_module,
            "configured_correction_generator",
            lambda _db: DeterministicGenerator(),
        )
        retried = client.post(
            f"/api/baseline-corrections/{failed_payload['id']}/retry"
        )
        assert retried.status_code == 200
        assert retried.json()["status"] == "processing"
        assert retried.json()["attempt_count"] == 2
        retried_payload = client.get(
            f"/api/baseline-corrections/{failed_payload['id']}"
        ).json()
        assert retried_payload["stage"] == "regression"
        frozen_analysis = db.get(BaselineCorrectionRun, failed.json()["id"])
        assert frozen_analysis is not None
        assert json.loads(frozen_analysis.input_snapshot_json)["items"][0][
            "predicted_level"
        ] == "L3"
        assert db.query(PromptVersion).count() == 3

    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
