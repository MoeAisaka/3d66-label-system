from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adaptive_correction import (
    correction_lane_for_run,
    recompute_v3_from_correction,
    route_human_evidence,
    wrap_evaluation_item,
)
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.dimension_deduction_bridge import empty_deduction_output
from app.correction_contract import correction_contract_hash
from app.database import Base, get_db
from app.main import app, current_user
from app.models import (
    EvaluationJob,
    EvaluationProductionRun,
    EvaluationResult,
    PromptRegressionItem,
    PromptRegressionRun,
)


@pytest.mark.parametrize(
    ("run", "lane"),
    [
        (SimpleNamespace(__tablename__="baseline_regression_runs"), "baseline"),
        (
            SimpleNamespace(
                __tablename__="evaluation_production_runs",
                workflow_kind="incremental",
            ),
            "incremental",
        ),
        (
            SimpleNamespace(__tablename__="prompt_regression_runs"),
            "candidate",
        ),
    ],
)
def test_correction_lane_for_run_is_deterministic(run: SimpleNamespace, lane: str) -> None:
    assert correction_lane_for_run(run) == lane


def test_human_evidence_routes_a_b_and_v3_without_allowing_browser_rules() -> None:
    assert route_human_evidence({"affected_layers": ["A"]}) == "A"
    assert route_human_evidence({"affected_layers": ["B"]}) == "B"
    assert route_human_evidence({"affected_layers": ["V3"]}) == "A+B"
    assert route_human_evidence({"affected_layers": ["A", "V3"]}) == "A+B"
    assert route_human_evidence({"affected_layers": []}) == "A+B"

    with pytest.raises(ValueError, match="规则代码"):
        route_human_evidence(
            {
                "affected_layers": ["A"],
                "rule_code": "return hidden_threshold",
            }
        )


def test_wrap_evaluation_item_keeps_lane_run_and_immutable_snapshot() -> None:
    result = SimpleNamespace(
        id=91,
        job_id=101,
        asset_id=501,
        precheck_json=json.dumps(
            {"production_fields": {"title": "模型标题"}}, ensure_ascii=False
        ),
        aesthetic_json="{}",
        scoring_json=json.dumps({"level": "L2", "score": 70}),
        correction_history_json="[]",
        level="L2",
        score=70,
        review_revision=2,
    )
    run = SimpleNamespace(id=7, category_key="inspiration_image")
    item = wrap_evaluation_item(
        run,
        result,
        item_id=33,
        result_snapshot_json=json.dumps(
            {"predicted_level": "L2", "stage_a": {"production_fields": {"title": "模型标题"}}},
            ensure_ascii=False,
        ),
    )

    assert item.run_id == run.id
    assert item.id == 33
    assert item.evaluation is result
    assert json.loads(item.result_snapshot_json)["predicted_level"] == "L2"


def test_recompute_v3_from_correction_matches_authoritative_scoring_shape() -> None:
    context = {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 4,
    }
    precheck = {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "建筑设计",
            "primary_confidence": 0.96,
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
    }
    config = context["subcategory_dimensions"]["class_one"]
    output = empty_deduction_output(config)

    result = recompute_v3_from_correction(
        v3_context=context,
        precheck=precheck,
        dimension_output=output,
    )

    assert result["scoring"]["level"] in {"L1", "L2", "L3", "L4", "L5"}
    assert isinstance(result["scoring"]["score"], (int, float))
    assert result["scoring"]["v3_context"]["config_revision"] == 4


def test_incremental_and_candidate_correction_routes_are_exposed() -> None:
    paths = {
        str(route.path)
        for route in app.routes
        if getattr(route, "path", None) is not None
    }

    assert {
        "/api/evaluation-production-runs/{run_id}/evaluations/{evaluation_id}/correction-view",
        "/api/evaluation-production-runs/{run_id}/evaluations/{evaluation_id}/corrections",
        "/api/prompt-regressions/{run_id}/items/{item_id}/correction-view",
        "/api/prompt-regressions/{run_id}/items/{item_id}/corrections",
    } <= paths


def _text_contract() -> dict:
    contract = {
        "contract_version": "three-lane-v1",
        "category_key": "inspiration_image",
        "nodes": [
            {
                "node_key": "call_a.title",
                "layer": "A",
                "path": "call_a.title",
                "order": 1,
                "label": "素材标题",
                "description": "人工纠正模型识别的素材标题",
                "type": "text",
                "semantic_version": "1",
                "compatibility_key": "production-title",
                "required": True,
                "evidence": {
                    "description": "需要提供画面主体证据",
                    "required": True,
                },
                "metadata": {"node_type": "call_a_field"},
            }
        ],
    }
    contract["contract_hash"] = correction_contract_hash(contract)
    return contract


def _evaluation(job: EvaluationJob, title: str) -> EvaluationResult:
    return EvaluationResult(
        asset_id=job.asset_id,
        job_id=job.id,
        precheck_json=json.dumps(
            {"production_fields": {"title": title}}, ensure_ascii=False
        ),
        aesthetic_json="{}",
        scoring_json=json.dumps({"score": 70, "level": "L2"}),
        correction_history_json="[]",
        raw_response_a='{"immutable":true}',
        score=70,
        level="L2",
        confidence=0.8,
        needs_review=False,
        review_revision=0,
        model_id="fake-model",
        prompt_a_version="a-v1",
        prompt_b_version="b-v1",
        rubric_version="r-v1",
        engine_version="e-v1",
    )


def test_incremental_and_candidate_routes_share_submission_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    contract = _text_contract()
    production_job = EvaluationJob(
        asset_id=101,
        category_key="inspiration_image",
        category_profile_snapshot_json="{}",
        queue_class="production_batch",
        origin_queue_class="production_batch",
        batch_key="production-batch-1",
        status="completed",
        stage="completed",
    )
    candidate_job = EvaluationJob(
        asset_id=102,
        category_key="inspiration_image",
        category_profile_snapshot_json="{}",
        queue_class="validation",
        origin_queue_class="validation",
        batch_key="candidate-batch-1",
        status="completed",
        stage="completed",
    )
    db.add_all([production_job, candidate_job])
    db.flush()
    production_result = _evaluation(production_job, "增量模型标题")
    candidate_result = _evaluation(candidate_job, "候选模型标题")
    db.add_all([production_result, candidate_result])
    db.flush()
    production_run = EvaluationProductionRun(
        idempotency_key="production-run-1",
        request_hash="a" * 64,
        material_package_id=501,
        category_key="inspiration_image",
        workflow_kind="incremental",
        category_profile_snapshot_json="{}",
        category_profile_hash="b" * 64,
        correction_contract_json=json.dumps(contract, ensure_ascii=False),
        correction_contract_hash=contract["contract_hash"],
        job_ids_json=json.dumps([production_job.id]),
        batch_key="production-batch-1",
        status="first_review",
        current_stage="first_review",
        blockers_json="[]",
        audit_revision=1,
        created_by="test",
    )
    candidate_run = PromptRegressionRun(
        name="候选纠偏回归",
        sample_set_id=601,
        prompt_a_id=701,
        prompt_b_id=702,
        regression_mode="paired",
        correction_contract_json=json.dumps(contract, ensure_ascii=False),
        correction_contract_hash=contract["contract_hash"],
        status="passed",
        total=1,
        completed=1,
        created_by="test",
    )
    db.add_all([production_run, candidate_run])
    db.flush()
    candidate_item = PromptRegressionItem(
        run_id=candidate_run.id,
        sample_item_id=801,
        evaluation_id=candidate_result.id,
        candidate_evaluation_id=candidate_result.id,
        candidate_result_json="{}",
        status="completed",
        passed=True,
    )
    db.add(candidate_item)
    db.commit()
    user = SimpleNamespace(username="运营乙", is_admin=True, role="admin")
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        lanes = [
            (
                "incremental",
                f"/api/evaluation-production-runs/{production_run.id}/evaluations/{production_result.id}",
                "增量人工标题",
            ),
            (
                "candidate",
                f"/api/prompt-regressions/{candidate_run.id}/items/{candidate_item.id}",
                "候选人工标题",
            ),
        ]
        for index, (lane, prefix, human_value) in enumerate(lanes, start=1):
            view = client.get(f"{prefix}/correction-view")
            assert view.status_code == 200
            assert view.json()["lane"] == lane
            saved = client.post(
                f"{prefix}/corrections",
                json={
                    "contract_hash": contract["contract_hash"],
                    "review_revision": 0,
                    "idempotency_key": f"three-lane-{index:04d}",
                    "nodes": [
                        {
                            "node_key": "call_a.title",
                            "human_value": human_value,
                            "reason": "主体识别错误",
                            "evidence": [{"text": "画面主体清晰可见"}],
                        }
                    ],
                },
            )
            assert saved.status_code == 200
            assert saved.json()["lane"] == lane
            assert saved.json()["review_revision"] == 1
            assert saved.json()["nodes"][0]["human_value"] == human_value
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
