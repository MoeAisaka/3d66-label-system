import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.baseline_regression import field_metric_release_regressions
from app.main import app, current_user
from app.models import (
    Asset,
    BaselineRegressionItem,
    BaselineRegressionRun,
    BaselineSet,
    BaselineSetItem,
    SampleSet,
    SampleSetItem,
    StrategyBundle,
    User,
)


def test_quality_metrics_expose_field_accuracy_recall_and_failure_evidence() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="quality-reviewer",
        password_hash="unused",
        display_name="质量审核员",
    )
    assets = [
        Asset(
            original_name=f"quality-{index}.jpg",
            stored_name=f"quality-{index}.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256=str(index) * 64,
            status="uploaded",
            category_key="space_image",
        )
        for index in (1, 2)
    ]
    bundle = StrategyBundle(
        canonical_hash="a" * 64,
        strategy_schema_version="strategy-bundle-v1",
        model_id="quality-model-v1",
        model_config_snapshot="{}",
        prompt_a_version="quality-a-v2",
        prompt_b_version="quality-b-v3",
        rubric_version="rubric-v4",
        engine_version="engine-v5",
    )
    db.add_all([user, bundle, *assets])
    db.flush()
    baseline_set = BaselineSet(
        name="字段质量基准集",
        category_key="space_image",
        default_expected_level="L1",
        fingerprint="b" * 64,
        created_by=user.username,
    )
    db.add(baseline_set)
    db.flush()
    baseline_items = [
        BaselineSetItem(
            baseline_set_id=baseline_set.id,
            asset_id=asset.id,
            expected_level=expected_level,
            asset_snapshot_json=json.dumps(
                {
                    "schema_version": "baseline-asset-v1",
                    "asset_id": asset.id,
                    "sha256": asset.sha256,
                }
            ),
        )
        for asset, expected_level in zip(assets, ("L1", "L2"), strict=True)
    ]
    db.add_all(baseline_items)
    db.flush()
    run = BaselineRegressionRun(
        baseline_set_id=baseline_set.id,
        sequence_no=1,
        strategy_bundle_id=bundle.id,
        category_key="space_image",
        strategy_snapshot_json="{}",
        execution_snapshot_json=json.dumps(
            {
                "category_key": "space_image",
                "v3_authoritative_bundle": {
                    "contract": {"spec_version": "space-image-v3"}
                },
            }
        ),
        baseline_set_fingerprint=baseline_set.fingerprint,
        status="completed",
        total=2,
        completed=2,
        valid_predictions=2,
        metrics_json="{}",
        created_by=user.username,
    )
    db.add(run)
    db.flush()
    result_snapshots = [
        {
            "predicted_level": "L1",
            "stage_a": {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "住宅设计",
                },
                "image_quality": {"quality_severity": "slight"},
            },
            "level_explanation": {
                "all_dimensions": [{"key": "layout", "grade": 4}],
            },
            "versions": {
                "model": "quality-model-v1",
                "prompt_a": "quality-a-v2",
                "prompt_b": "quality-b-v3",
                "rubric": "rubric-v4",
                "engine": "engine-v5",
            },
        },
        {
            "predicted_level": "L3",
            "stage_a": {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "商业空间",
                },
                "image_quality": {"quality_severity": "moderate"},
            },
            "level_explanation": {"all_dimensions": []},
            "versions": {
                "model": "quality-model-v1",
                "prompt_a": "quality-a-v2",
                "prompt_b": "quality-b-v3",
                "rubric": "rubric-v4",
                "engine": "engine-v5",
            },
        },
    ]
    db.add_all(
        [
            BaselineRegressionItem(
                run_id=run.id,
                baseline_set_item_id=baseline_item.id,
                asset_id=asset.id,
                expected_level=baseline_item.expected_level,
                status="completed",
                result_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            )
            for asset, baseline_item, snapshot in zip(
                assets, baseline_items, result_snapshots, strict=True
            )
        ]
    )
    golden = SampleSet(
        name="字段质量黄金集",
        category_key="space_image",
        kind="golden",
        status="locked",
        created_by=user.username,
    )
    db.add(golden)
    db.flush()
    truths = [
        {
            "level": "L1",
            "scope_status": "in_scope",
            "primary_category": "住宅设计",
            "quality_severity": "slight",
            "dimensions": {"layout": 4},
        },
        {
            "level": "L2",
            "scope_status": "in_scope",
            "primary_category": "住宅设计",
            "quality_severity": "moderate",
            "dimensions": {"layout": 3},
        },
    ]
    db.add_all(
        [
            SampleSetItem(
                sample_set_id=golden.id,
                asset_id=asset.id,
                source_result_id=index,
                expected_level=truth["level"],
                expected_category=truth["primary_category"],
                truth_json=json.dumps(truth, ensure_ascii=False),
                truth_revision=index,
                added_by=user.username,
            )
            for index, (asset, truth) in enumerate(
                zip(assets, truths, strict=True), start=1
            )
        ]
    )
    db.commit()

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    try:
        response = TestClient(app).get(
            f"/api/baseline-regressions/{run.id}/metrics"
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["schema_version"] == "baseline-field-metrics-v1"
        metrics = {item["field_key"]: item for item in payload["field_metrics"]}
        assert {"level", "dimensions.layout", "primary_category"} <= metrics.keys()
        assert metrics["level"] | {
            "support": 2,
            "tp": 1,
            "fp": 1,
            "fn": 1,
            "accuracy": 0.5,
            "recall": 0.5,
        } == metrics["level"]
        assert metrics["dimensions.layout"]["confusion_matrix"]["3"]["__missing__"] == 1
        assert metrics["dimensions.layout"]["failure_sample_ids"] == [assets[1].id]
        assert payload["failure_sample_ids"] == [assets[1].id]
        assert payload["golden_failure_sample_ids"] == [assets[1].id]
        assert payload["aggregates"]["macro"]["accuracy"] < 1
        assert payload["aggregates"]["micro"]["recall"] < 1
        assert payload["versions"]["mechanism"]["spec_version"] == "space-image-v3"
        assert payload["versions"]["truth"]["revision_max"] == 2
        assert payload["decision_policy"] == {
            "evidence_only": True,
            "auto_activate_candidate": False,
        }
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_quality_metrics_return_not_found_for_unknown_run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(username="metrics-reader", password_hash="unused")
    db.add(user)
    db.commit()
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    try:
        response = TestClient(app).get("/api/baseline-regressions/999/metrics")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_field_metric_regression_and_golden_failure_block_candidate() -> None:
    baseline = {
        "field_metrics": [
            {
                "field_key": "scope_status",
                "accuracy": 1.0,
                "recall": 1.0,
                "failure_sample_ids": [],
            }
        ],
        "failure_sample_ids": [],
        "versions": {"truth": {"matched_asset_count": 2}},
    }
    candidate = {
        "field_metrics": [
            {
                "field_key": "scope_status",
                "accuracy": 0.5,
                "recall": 0.5,
                "failure_sample_ids": [22],
            }
        ],
        "failure_sample_ids": [22],
        "golden_failure_sample_ids": [22],
        "versions": {"truth": {"matched_asset_count": 2}},
    }

    regressions = field_metric_release_regressions(baseline, candidate)

    assert {item["code"] for item in regressions} == {
        "key_field_regressed",
        "golden_set_failure",
    }
    assert regressions[0]["field_key"] == "scope_status"


def test_non_golden_failure_does_not_trigger_golden_gate() -> None:
    baseline = {"field_metrics": [], "golden_failure_sample_ids": []}
    candidate = {
        "field_metrics": [],
        "failure_sample_ids": [99],
        "golden_failure_sample_ids": [],
        "versions": {"truth": {"matched_asset_count": 1}},
    }

    assert field_metric_release_regressions(baseline, candidate) == []
