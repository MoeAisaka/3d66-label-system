from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.evaluation_packages import canonical_json, canonical_manifest_hash
from app.models import EvaluationPackage, LabelRelease, MechanismRelease, PublishedLabel, StockRerun

from test_evaluation_packages import _fixture


def _approve_and_publish(fixture: dict):
    package_id = fixture["client"].post(
        f"/api/evaluation-packages/{fixture['package'].id}/approve",
        json={"note": "人工批准"},
    ).json()["package"]["id"]
    response = fixture["client"].post(
        f"/api/evaluation-packages/{package_id}/publish",
        json={"note": "启用机制"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_package_fixture() -> dict:
    fixture = _fixture()
    created = fixture["client"].post(
        "/api/evaluation-packages",
        json={
            "package_key": "mechanism-axis-v1",
            "regression_run_id": fixture["regression"].id,
            "automation_run_id": fixture["automation"].id,
            "sample_set_id": fixture["sample_set"].id,
            "candidate_strategy_bundle_id": fixture["candidate_bundle"].id,
        },
    )
    assert created.status_code == 200, created.text
    fixture["package"] = fixture["db"].get(EvaluationPackage, created.json()["package"]["id"])
    return fixture


def _force_release_snapshot(fixture: dict, mechanism: MechanismRelease, manifest: dict, manifest_hash: str) -> None:
    fixture["db"].execute(text("DROP TRIGGER IF EXISTS trg_mechanism_releases_frozen"))
    fixture["db"].execute(
        text(
            "UPDATE mechanism_releases "
            "SET manifest_json=:manifest_json, manifest_hash=:manifest_hash "
            "WHERE id=:release_id"
        ),
        {
            "manifest_json": canonical_json(manifest),
            "manifest_hash": manifest_hash,
            "release_id": mechanism.id,
        },
    )
    fixture["db"].commit()
    fixture["db"].expire_all()


def test_package_publish_creates_mechanism_release_without_stock_or_label_release():
    fixture = _create_package_fixture()
    _approve_and_publish(fixture)

    releases = fixture["db"].query(MechanismRelease).all()
    assert len(releases) == 1
    assert releases[0].evaluation_package_id == fixture["package"].id
    assert fixture["db"].query(StockRerun).count() == 0
    assert fixture["db"].query(LabelRelease).count() == 0
    assert fixture["db"].query(PublishedLabel).count() == 0


def test_stock_rerun_requires_explicit_creation_and_freezes_scope_and_snapshots():
    fixture = _create_package_fixture()
    _approve_and_publish(fixture)
    mechanism = fixture["db"].query(MechanismRelease).one()
    asset_ids = [item.asset_id for item in fixture["sample_items"][:2]]

    response = fixture["client"].post(
        "/api/stock-reruns",
        json={
            "idempotency_key": "rerun-axis-v1",
            "category_key": "space_image",
            "target_mechanism_release_id": mechanism.id,
            "asset_ids": asset_ids,
            "reason": "新版机制覆盖存量美感分",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()["rerun"]
    assert payload["status"] == "planned"
    assert payload["asset_ids"] == asset_ids
    assert payload["target_mechanism_release_id"] == mechanism.id
    rerun = fixture["db"].get(StockRerun, payload["id"])
    assert rerun is not None
    assert json.loads(rerun.material_scope_json)["asset_ids"] == asset_ids
    assert json.loads(rerun.mechanism_snapshot_json)["manifest_hash"] == mechanism.manifest_hash
    assert json.loads(rerun.execution_snapshot_json)["mode"] == "dry_run_only"

    duplicate = fixture["client"].post(
        "/api/stock-reruns",
        json={
            "idempotency_key": "rerun-axis-v1",
            "category_key": "space_image",
            "target_mechanism_release_id": mechanism.id,
            "asset_ids": asset_ids,
            "reason": "重复提交",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True


def test_stock_rerun_rejects_category_mismatch_and_missing_regression_gate():
    fixture = _create_package_fixture()
    _approve_and_publish(fixture)
    mechanism = fixture["db"].query(MechanismRelease).one()
    asset_id = fixture["sample_items"][0].asset_id

    mismatch = fixture["client"].post(
        "/api/stock-reruns",
        json={
            "idempotency_key": "rerun-category-mismatch",
            "category_key": "pdf_text",
            "target_mechanism_release_id": mechanism.id,
            "asset_ids": [asset_id],
            "reason": "错误类目",
        },
    )
    assert mismatch.status_code == 409
    assert "类目" in mismatch.json()["detail"]

    manifest = json.loads(mechanism.manifest_json)
    manifest["regression"]["status"] = "waiting_results"
    _force_release_snapshot(
        fixture, mechanism, manifest, canonical_manifest_hash(manifest)
    )
    blocked = fixture["client"].post(
        "/api/stock-reruns",
        json={
            "idempotency_key": "rerun-no-regression-gate",
            "category_key": "space_image",
            "target_mechanism_release_id": mechanism.id,
            "asset_ids": [asset_id],
            "reason": "回归证据未完成",
        },
    )
    assert blocked.status_code == 409
    assert "回归" in blocked.json()["detail"]


def test_stock_rerun_rejects_damaged_mechanism_snapshot():
    fixture = _create_package_fixture()
    _approve_and_publish(fixture)
    mechanism = fixture["db"].query(MechanismRelease).one()
    manifest = json.loads(mechanism.manifest_json)
    _force_release_snapshot(fixture, mechanism, manifest, "0" * 64)
    response = fixture["client"].post(
        "/api/stock-reruns",
        json={
            "idempotency_key": "rerun-damaged-snapshot",
            "category_key": "space_image",
            "target_mechanism_release_id": mechanism.id,
            "asset_ids": [fixture["sample_items"][0].asset_id],
            "reason": "损坏快照",
        },
    )
    assert response.status_code == 409
    assert "快照" in response.json()["detail"] or "清单" in response.json()["detail"]


def test_mechanism_release_snapshot_is_immutable():
    fixture = _create_package_fixture()
    _approve_and_publish(fixture)
    mechanism = fixture["db"].query(MechanismRelease).one()
    mechanism.manifest_hash = "0" * 64
    with pytest.raises(IntegrityError, match="MechanismRelease snapshot is immutable"):
        fixture["db"].commit()
    fixture["db"].rollback()


def test_stock_rerun_idempotency_allows_implicit_previous_release():
    fixture = _create_package_fixture()
    _approve_and_publish(fixture)
    first = fixture["db"].query(MechanismRelease).one()
    first.status = "superseded"
    source_package = fixture["package"]
    second_package = EvaluationPackage(
        package_key="mechanism-axis-v2",
        request_hash="a" * 64,
        category_key=source_package.category_key,
        prompt_mode=source_package.prompt_mode,
        prompt_a_id=source_package.prompt_a_id,
        prompt_b_id=source_package.prompt_b_id,
        dimension_schema_id=source_package.dimension_schema_id,
        dimension_route_policy_id=source_package.dimension_route_policy_id,
        sample_set_id=source_package.sample_set_id,
        baseline_strategy_bundle_id=source_package.baseline_strategy_bundle_id,
        candidate_strategy_bundle_id=source_package.candidate_strategy_bundle_id,
        regression_run_id=source_package.regression_run_id,
        automation_run_id=source_package.automation_run_id,
        metric_snapshot_id=source_package.metric_snapshot_id,
        canonical_manifest_json=source_package.canonical_manifest_json,
        canonical_manifest_hash=source_package.canonical_manifest_hash,
        status="published",
        created_by=fixture["user"].username,
    )
    fixture["db"].add(second_package)
    fixture["db"].flush()
    second = MechanismRelease(
        release_key="mechanism:space_image:r2:test",
        category_key="space_image",
        evaluation_package_id=second_package.id,
        previous_release_id=first.id,
        revision=2,
        manifest_json=first.manifest_json,
        manifest_hash=first.manifest_hash,
        status="active",
        activated_by=fixture["user"].username,
    )
    fixture["db"].add(second)
    fixture["db"].commit()
    request = {
        "idempotency_key": "rerun-implicit-previous",
        "category_key": "space_image",
        "target_mechanism_release_id": second.id,
        "asset_ids": [fixture["sample_items"][0].asset_id],
        "reason": "重复请求应解析为同一来源版本",
    }
    first_response = fixture["client"].post("/api/stock-reruns", json=request)
    duplicate = fixture["client"].post("/api/stock-reruns", json=request)
    assert first_response.status_code == 200, first_response.text
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["source_mechanism_release_id"] == first.id
