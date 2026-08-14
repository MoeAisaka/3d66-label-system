from __future__ import annotations

import json
import csv
import io
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.quality_assets import build_quality_asset_export
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    SampleSet,
    SampleSetItem,
    SampleTruthRevision,
    User,
)


@contextmanager
def _quality_context(*, locked: bool = True) -> Iterator[dict[str, object]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        user = User(
            username="quality-owner",
            password_hash="unused",
            display_name="质量资产管理员",
            is_admin=True,
        )
        asset = Asset(
            original_name="golden.jpg",
            stored_name="golden.jpg",
            mime_type="image/jpeg",
            size_bytes=128,
            sha256="a" * 64,
            category_key="space_image",
        )
        db.add_all([user, asset])
        db.flush()
        job = EvaluationJob(
            asset_id=asset.id,
            category_key="space_image",
            status="completed",
            stage="done",
            progress=100,
        )
        db.add(job)
        db.flush()
        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job.id,
            precheck_json='{"classification":{"primary_category":"住宅"}}',
            aesthetic_json='{"dimensions":{"composition":4}}',
            scoring_json="{}",
            raw_response_a='{"must_not_export":"provider raw"}',
            score=80,
            level="L2",
            confidence=0.95,
            needs_review=False,
            model_id="quality-model",
            prompt_a_version="quality-a-v1",
            prompt_b_version="quality-b-v1",
            rubric_version="quality-r1",
            engine_version="quality-e1",
        )
        db.add(result)
        db.flush()
        sample_set = SampleSet(
            name="空间黄金集 V1",
            description="锁定质量基准",
            kind="golden",
            status="locked" if locked else "draft",
            category_key="space_image",
            created_by=user.username,
        )
        db.add(sample_set)
        db.flush()
        truth = {
            "level": "L2",
            "category": "住宅",
            "quality": {"severity": "good"},
            "dimensions": {"composition": 4},
            "evidence": ["主体完整"],
        }
        item = SampleSetItem(
            sample_set_id=sample_set.id,
            asset_id=asset.id,
            source_result_id=result.id,
            expected_level="L2",
            expected_category="住宅",
            truth_json=json.dumps(truth, ensure_ascii=False),
            truth_revision=1,
            truth_updated_by=user.username,
            added_by=user.username,
        )
        db.add(item)
        db.flush()
        db.add(
            SampleTruthRevision(
                sample_item_id=item.id,
                revision=1,
                truth_json=item.truth_json,
                reason="建立首版真值",
                reviewer_name=user.username,
            )
        )
        db.commit()

    def override_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    try:
        yield {
            "client": TestClient(app),
            "sample_set_id": sample_set.id,
            "item_id": item.id,
            "sessions": sessions,
        }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_locked_golden_set_rejects_direct_mutation() -> None:
    with _quality_context() as fixture:
        client = fixture["client"]
        sample_set_id = fixture["sample_set_id"]
        item_id = fixture["item_id"]

        updated = client.patch(
            f"/api/sample-sets/{sample_set_id}/items/{item_id}",
            json={"expected_level": "L1", "note": "覆盖锁定真值"},
        )
        removed = client.delete(
            f"/api/sample-sets/{sample_set_id}/items/{item_id}"
        )
        added = client.post(
            f"/api/sample-sets/{sample_set_id}/items",
            json={"asset_ids": [1]},
        )
        unlocked = client.patch(
            f"/api/sample-sets/{sample_set_id}/status",
            json={"status": "draft"},
        )
        history = client.get(
            f"/api/sample-sets/{sample_set_id}/items/{item_id}/history"
        )

        assert updated.status_code == 409
        assert removed.status_code == 409
        assert added.status_code == 409
        assert unlocked.status_code == 409
        assert history.status_code == 200
        assert "复制" in updated.json()["detail"]


def test_locked_golden_manifest_export_is_versioned_and_redacted() -> None:
    with _quality_context() as fixture:
        response = fixture["client"].post(
            f"/api/sample-sets/{fixture['sample_set_id']}/export",
            json={"format": "manifest"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["X-Export-Row-Count"] == "1"
        body = response.json()
        assert body["schema_version"] == "quality-asset-manifest-v1"
        assert body["sample_set_status"] == "locked"
        assert body["truth_revision_min"] == 1
        assert body["truth_revision_max"] == 1
        assert len(body["dataset_hash"]) == 64
        assert len(body["manifest_hash"]) == 64
        serialized = json.dumps(body, ensure_ascii=False).lower()
        assert "must_not_export" not in serialized
        assert "provider raw" not in serialized
        assert "api_key" not in serialized


def test_quality_asset_json_and_csv_exports_contain_formal_truth() -> None:
    with _quality_context() as fixture:
        client = fixture["client"]
        sample_set_id = fixture["sample_set_id"]
        json_response = client.post(
            f"/api/sample-sets/{sample_set_id}/export",
            json={"format": "json"},
        )
        csv_response = client.post(
            f"/api/sample-sets/{sample_set_id}/export",
            json={"format": "csv"},
        )

        assert json_response.status_code == 200
        assert json_response.json()["items"][0]["truth"]["level"] == "L2"
        assert csv_response.status_code == 200
        csv_text = csv_response.content.decode("utf-8-sig")
        assert "asset_id" in csv_text
        assert "L2" in csv_text
        assert "provider raw" not in csv_text


def test_quality_assets_summary_groups_kind_category_status_and_truth() -> None:
    with _quality_context() as fixture:
        response = fixture["client"].get("/api/quality-assets/summary")
        sample_sets = fixture["client"].get("/api/sample-sets")

        assert response.status_code == 200
        assert sample_sets.status_code == 200
        body = response.json()
        assert body["sample_set_count"] == 1
        assert body["item_count"] == 1
        assert body["truth_complete_count"] == 1
        assert body["by_kind"]["golden"]["sample_sets"] == 1
        assert body["by_category"]["space_image"]["items"] == 1
        assert body["by_status"]["locked"]["truth_complete"] == 1
        assert body["by_truth_complete"]["true"] == 1
        assert body["by_truth_complete"]["false"] == 0
        assert sample_sets.json()["items"][0]["latest_truth_revision"] == 1


def test_quality_asset_csv_neutralizes_spreadsheet_formula_prefixes() -> None:
    with _quality_context() as fixture:
        client = fixture["client"]
        detail = client.get(f"/api/sample-sets/{fixture['sample_set_id']}").json()
        assert detail["items"][0]["asset_name"] == "golden.jpg"

        with fixture["sessions"]() as db:
            sample_set = db.get(SampleSet, fixture["sample_set_id"])
            assert sample_set is not None
            sample_set.items[0].asset.original_name = "=2+3"
            db.commit()
            export = build_quality_asset_export(sample_set, format="csv")

        row = next(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
        assert row["asset_name"] == "'=2+3"
