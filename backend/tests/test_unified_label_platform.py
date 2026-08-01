from __future__ import annotations

import json
import csv
import io
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.main import app, current_user
from app.label_export import build_export
from app.migrations import run_migrations
from app.models import (
    Asset,
    AuditEvent,
    ContentIngressEvent,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    LabelOutboxEvent,
    ModelConfig,
    PromptVersion,
    PublishedLabel,
    ReviewPanel,
    SamplingPolicy,
    User,
)
from app.strategy_bundle import build_evaluation_strategy_snapshot, get_or_create_bundle


def _database() -> tuple[object, Session, User, EvaluationResult]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine, expire_on_commit=False)
    user = User(username="release-admin", password_hash="unused", display_name="发布管理员")
    asset = Asset(
        original_name="content.jpg", stored_name="content.jpg", mime_type="image/jpeg",
        size_bytes=10, sha256="a" * 64, category_key="space_image",
    )
    model = ModelConfig(
        name="release-model", model_id="release-model", base_url="https://example.test/v1",
        api_path="/chat/completions",
    )
    prompt = PromptVersion(
        stage="A", name="release-prompt", version="release-A1",
        system_prompt="system", user_prompt="user", rubric_version="R1", status="published",
    )
    policy = SamplingPolicy(id=1, revision=1)
    db.add_all([user, asset, model, prompt, policy])
    db.flush()
    bundle = get_or_create_bundle(
        db=db, model_config=model, prompt_a=prompt, prompt_b=None,
        rubric_version="R1", engine_version="E1", risk_review_version=None,
        sampling_policy=policy,
    )
    job = EvaluationJob(
        asset_id=asset.id, category_key="space_image", prompt_a_id=prompt.id,
        strategy_bundle_id=bundle.id, status="completed", stage="done", progress=100,
    )
    db.add(job)
    db.flush()
    result = EvaluationResult(
        asset_id=asset.id, job_id=job.id, strategy_bundle_id=bundle.id,
        strategy_snapshot_json=build_evaluation_strategy_snapshot(
            db=db, bundle=bundle, prompt_a=prompt, prompt_b=None,
            sampling_policy=policy, aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
        ),
        precheck_json=json.dumps({"classification": {"scope_status": "in_scope", "primary_category": "住宅"}}),
        aesthetic_json=json.dumps({"dimensions": {"lighting": {"grade": 4}}}),
        scoring_json="{}", raw_response_a="{}", score=78, level="L3", confidence=0.92,
        needs_review=False, review_stage="completed", model_id=model.model_id,
        prompt_a_version=prompt.version, rubric_version="R1", engine_version="E1",
    )
    db.add(result)
    db.flush()
    review = HumanReview(
        evaluation_id=result.id, reviewer_name=user.username, stage="initial",
        decision="corrected", corrected_level="L2", corrected_score=63,
        corrections_json="[]",
    )
    db.add(review)
    db.flush()
    panel = ReviewPanel(
        evaluation_id=result.id, required_reviewers=1, status="completed", final_review_id=review.id,
        final_truth_json=json.dumps({
            "decision": "corrected", "corrected_level": "L2", "corrected_score": 63,
            "dimensions": {"lighting": 3}, "key_fields": {"style": "现代"},
        }),
        completed_at=datetime.now(timezone.utc),
    )
    db.add(panel)
    db.commit()
    return engine, db, user, result


def _client(db: Session, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


def _close(engine: object, db: Session) -> None:
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _ingress_payload(*, event_id: str, occurred_at: datetime, asset_id: int, version: str = "1") -> dict:
    return {
        "event_id": event_id,
        "schema_version": "content-ingress-v1",
        "event_type": "content.created",
        "source_system": "content-hub",
        "occurred_at": occurred_at.isoformat(),
        "payload": {
            "content_id": "content-001", "content_version": version,
            "category_key": "space_image", "asset_id": asset_id,
        },
    }


def test_ingress_publish_consumer_cursor_and_rollback_are_versioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, db, user, result = _database()
    client = _client(db, user)
    token = "test-label-integration-token"
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, content_ingress_token=token, label_consumer_token=token),
    )
    headers = {"Authorization": f"Bearer {token}"}
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    try:
        created = client.post("/api/content-ingress/events", headers=headers, json=_ingress_payload(event_id="ing-1", occurred_at=now, asset_id=result.asset_id))
        assert created.status_code == 200
        assert created.json()["event_status"] == "applied"
        assert created.json()["writes_evaluation_job"] is False
        assert client.post("/api/content-ingress/events", headers=headers, json=_ingress_payload(event_id="ing-1", occurred_at=now, asset_id=result.asset_id)).json()["duplicate"] is True
        changed = _ingress_payload(event_id="ing-1", occurred_at=now, asset_id=result.asset_id, version="2")
        assert client.post("/api/content-ingress/events", headers=headers, json=changed).status_code == 409
        stale = _ingress_payload(event_id="ing-older", occurred_at=now - timedelta(seconds=1), asset_id=result.asset_id, version="0")
        assert client.post("/api/content-ingress/events", headers=headers, json=stale).json()["event_status"] == "stale"

        release = client.post("/api/label-releases", json={
            "release_key": "release-1", "evaluation_id": result.id,
            "content_key": "content-hub:content-001",
        })
        assert release.status_code == 200
        release_id = release.json()["release"]["id"]
        assert release.json()["release"]["status"] == "pending_review"
        release_drift = client.post("/api/label-releases", json={
            "release_key": "release-1", "evaluation_id": result.id + 999,
            "content_key": "content-hub:content-001",
        })
        assert release_drift.status_code == 409
        published = client.post(f"/api/label-releases/{release_id}/approve-and-publish")
        assert published.status_code == 200
        assert published.json()["release"]["status"] == "published"
        assert published.json()["release"]["published_version"] == 1

        read = client.get("/api/consumer/v1/labels/content-hub:content-001", headers=headers)
        assert read.status_code == 200
        assert read.headers["etag"]
        assert read.json()["version"] == 1
        assert read.json()["label"]["level"] == "L2"
        assert "raw_response" not in json.dumps(read.json(), ensure_ascii=False)
        changes = client.get("/api/consumer/v1/changes", headers=headers).json()
        assert changes["next_cursor"] == 1
        assert changes["items"][0]["operation"] == "published"
        checkpoint = client.post("/api/consumer/v1/checkpoints", headers=headers, json={"consumer_name": "frontend-search", "cursor": 1})
        assert checkpoint.status_code == 200
        assert client.post("/api/consumer/v1/checkpoints", headers=headers, json={"consumer_name": "frontend-search", "cursor": 0}).status_code == 409

        first = db.scalar(select(PublishedLabel).where(PublishedLabel.content_key == "content-hub:content-001"))
        assert first is not None
        rollback = client.post(f"/api/published-labels/{first.id}/rollback", json={"rollback_key": "rollback-1"})
        assert rollback.status_code == 200
        assert rollback.json()["release"]["published_version"] == 2
        second = db.scalar(select(PublishedLabel).where(
            PublishedLabel.content_key == "content-hub:content-001",
            PublishedLabel.status == "published",
        ))
        assert second is not None
        rollback_drift = client.post(f"/api/published-labels/{second.id}/rollback", json={"rollback_key": "rollback-1"})
        assert rollback_drift.status_code == 409
        assert db.scalar(select(LabelOutboxEvent).where(LabelOutboxEvent.operation == "rolled_back")) is not None
        assert client.get("/api/consumer/v1/reconciliation", headers=headers).json()["outbox_high_watermark"] == 2
    finally:
        _close(engine, db)


def test_ingress_awaits_material_and_outbox_is_append_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, db, user, result = _database()
    client = _client(db, user)
    token = "test-label-integration-token"
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, content_ingress_token=token, label_consumer_token=token),
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        payload = _ingress_payload(event_id="ing-awaiting", occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc), asset_id=result.asset_id)
        del payload["payload"]["asset_id"]
        received = client.post("/api/content-ingress/events", headers=headers, json=payload)
        assert received.status_code == 200
        assert received.json()["material_required"] is True
        assert received.json()["writes_evaluation_job"] is False
        event = db.scalar(select(ContentIngressEvent).where(ContentIngressEvent.event_id == "ing-awaiting"))
        assert event is not None
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text("UPDATE content_ingress_events SET status='applied' WHERE id=:id"), {"id": event.id})
            db.commit()
        db.rollback()
    finally:
        _close(engine, db)


def test_operator_can_export_current_labels_and_version_history() -> None:
    engine, db, user, result = _database()
    client = _client(db, user)
    try:
        release = client.post(
            "/api/label-releases",
            json={
                "release_key": "export-release-1",
                "evaluation_id": result.id,
                "content_key": None,
            },
        ).json()["release"]
        client.post(f"/api/label-releases/{release['id']}/approve-and-publish")
        first = db.scalar(
            select(PublishedLabel).where(PublishedLabel.release_id == release["id"])
        )
        assert first is not None
        client.post(
            f"/api/published-labels/{first.id}/rollback",
            json={"rollback_key": "export-rollback-1"},
        )

        current_csv = client.post(
            "/api/published-labels/export",
            json={"format": "csv", "scope": "current", "category_key": "space_image"},
        )
        assert current_csv.status_code == 200
        assert current_csv.headers["content-disposition"].endswith('.csv"')
        assert current_csv.headers["x-export-row-count"] == "1"
        csv_rows = list(csv.DictReader(io.StringIO(current_csv.content.decode("utf-8-sig"))))
        assert len(csv_rows) == 1
        assert csv_rows[0]["content_key"] == first.content_key
        assert csv_rows[0]["status"] == "published"
        assert csv_rows[0]["level"] == "L2"
        assert "raw_response" not in current_csv.text

        history_json = client.post(
            "/api/published-labels/export",
            json={"format": "json", "scope": "history"},
        )
        assert history_json.status_code == 200
        assert history_json.json()["schema_version"] == "published-label-export-v1"
        assert history_json.json()["count"] == 2
        assert {item["status"] for item in history_json.json()["items"]} == {
            "published",
            "superseded",
        }

        workbook = client.post(
            "/api/published-labels/export",
            json={"format": "xlsx", "scope": "current"},
        )
        assert workbook.status_code == 200
        assert workbook.content.startswith(b"PK")
        with zipfile.ZipFile(io.BytesIO(workbook.content)) as archive:
            assert archive.testzip() is None
            assert "xl/worksheets/sheet1.xml" in archive.namelist()
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            assert "正式标签" not in sheet
            assert "L2" in sheet
            assert "raw_response" not in sheet
        export_audits = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.category == "label_export",
                AuditEvent.action == "downloaded",
            )
            .order_by(AuditEvent.id)
        ).all()
        assert len(export_audits) == 3
        assert [json.loads(item.payload_json)["row_count"] for item in export_audits] == [
            1,
            2,
            1,
        ]
    finally:
        _close(engine, db)


def test_label_export_requires_release_read_permission_and_valid_filters() -> None:
    engine, db, user, _result = _database()
    client = _client(db, user)
    try:
        app.dependency_overrides.pop(current_user)
        assert client.post("/api/published-labels/export", json={}).status_code == 401
        app.dependency_overrides[current_user] = lambda: user
        assert client.post(
            "/api/published-labels/export", json={"category_key": "INVALID"}
        ).status_code == 422
        assert client.post(
            "/api/published-labels/export",
            json={
                "published_from": "2026-08-02T00:00:00Z",
                "published_to": "2026-08-01T00:00:00Z",
            },
        ).status_code == 422
    finally:
        _close(engine, db)


def test_csv_export_neutralizes_spreadsheet_formula_prefixes() -> None:
    label = PublishedLabel(
        release_id=1,
        content_key="=2+3",
        category_key="space_image",
        version=1,
        label_schema_version="published-label-v1",
        label_payload_json=json.dumps({"level": "L1", "score": 99}),
        payload_hash="a" * 64,
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    export = build_export([label], format="csv", scope="current")
    row = next(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
    assert row["content_key"] == "'=2+3"
