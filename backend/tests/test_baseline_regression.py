import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.baseline_regression import (
    complete_baseline_item,
    compute_level_metrics,
    fail_baseline_item,
    filename_level_suggestion,
    level_explanation,
)
from app.database import Base, get_db
from app.main import app, current_user
from app.models import (
    Asset,
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationJob,
    EvaluationResult,
    MaterialPackage,
    MaterialPackageItem,
    ModelConfig,
    OptimizationCaseQueue,
    PromptVersion,
    User,
)


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
        assert json.loads(case.case_json)["expected_level"] == "L1"
        historical_snapshot = json.loads(item.result_snapshot_json)
        historical_snapshot.pop("level_explanation")
        item.result_snapshot_json = json.dumps(historical_snapshot)
        db.commit()
        historical = client.get(
            f"/api/baseline-regressions/{run.id}"
        ).json()["items"][0]["level_explanation"]
        assert historical["status"] == "unavailable_historical"
        assert historical["message"] == "历史结果未冻结评测理由"
        db.refresh(asset)
        assert asset.status == "uploaded"
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


def test_baseline_run_can_freeze_manual_prompt_pair_and_reserves_dimension_choice() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
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
        user_prompt="draft user b",
        rubric_version="R2",
        status="draft",
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

        partial = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"prompt_a_id": draft_a.id},
        )
        assert partial.status_code == 422
        assert "必须同时指定 A 与 B" in partial.text

        reserved_dimension = client.post(
            f"/api/baseline-sets/{set_id}/runs",
            json={"dimension_schema_id": 1},
        )
        assert reserved_dimension.status_code == 409
        assert (
            reserved_dimension.json()["detail"]["code"]
            == "DIMENSION_VERSION_SELECTION_NOT_ENABLED"
        )

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
        assert payload["selection"]["dimension"] == {
            "mode": "strategy_snapshot",
            "manual_selection_supported": False,
            "route_policy_id": None,
            "schemas": [],
        }

        run = db.get(BaselineRegressionRun, payload["id"])
        assert run is not None
        job = db.get(EvaluationJob, run.items[0].job_id)
        assert job is not None
        assert job.prompt_a_id == draft_a.id
        assert job.prompt_b_id == draft_b.id

        detail = client.get(
            f"/api/baseline-regressions/{run.id}"
        )
        assert detail.status_code == 200
        assert (
            detail.json()["summary"]["selection"]["prompt_b"]["version"]
            == "B-draft"
        )
        set_detail = client.get(f"/api/baseline-sets/{set_id}")
        assert (
            set_detail.json()["runs"][0]["selection"]["prompt_a"]["version"]
            == "A-draft"
        )
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
