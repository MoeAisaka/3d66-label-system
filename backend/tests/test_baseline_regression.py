import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.baseline_regression import complete_baseline_item, compute_level_metrics
from app.database import Base, get_db
from app.main import app, current_user
from app.models import (
    Asset,
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    OptimizationCaseQueue,
    PromptVersion,
    User,
)


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
    assert metrics["exact_accuracy"] == 1 / 5
    assert metrics["adjacent_accuracy"] == 3 / 5
    assert metrics["valid_predictions"] == 4
    assert metrics["failed"] == 1
    assert metrics["pending"] == 1
    assert list(metrics["confusion_matrix"]) == ["L1", "L2", "L3", "L4", "L5"]
    assert metrics["confusion_matrix"]["L2"] == {
        "L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0,
    }
    assert compute_level_metrics([])["exact_accuracy"] == 0


def test_baseline_api_freezes_truth_reports_and_enqueues_idempotently() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
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
            aesthetic_json="{}", scoring_json=json.dumps({"caps": [{"cap": "L2", "reason": "原样"}]}),
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
        assert json.loads(case.case_json)["expected_level"] == "L1"
        db.refresh(asset)
        assert asset.status == "uploaded"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
