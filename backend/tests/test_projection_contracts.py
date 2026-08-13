from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import (
    LabelRelease,
    LocalProjectionRow,
    ProjectionContract,
    ProjectionManifest,
    PublishedLabel,
    User,
)
from app.projection_contracts import LocalProjectionAdapter


def _contract_payload(*, contract_key: str = "unified-dimension") -> dict[str, object]:
    return {
        "contract_key": contract_key,
        "target_role": "unified_dimension",
        "table_name": "unified_dimension_table",
        "environment": "local",
        "primary_key": ["content_key"],
        "field_mappings": {
            "content_key": "content_key",
            "category_key": "category_key",
            "label_version": "$label.version",
            "level": "level",
            "score": "score",
            "classification": "classification",
            "dimensions": "dimensions",
            "production_fields": "production_fields",
            "image_quality": "image_quality",
            "media_form": "media_form",
            "asset_version": "provenance.asset_sha256",
            "mechanism_version": "provenance.strategy_bundle_id",
            "model_version": "provenance.model_id",
        },
        "input_versions": {"label_schema_version": "published-label-v1"},
        "mode": "snapshot",
        "idempotency_key_template": "{table_name}:{content_key}:{label_version}",
        "checkpoint": {"kind": "published_label_id"},
        "reconciliation": {"checks": ["row_count", "missing", "payload_hash", "version"]},
        "rollback": {"strategy": "rebuild_previous_published_version"},
        "owner": "tpeng-label-platform",
        "status": "draft",
    }


@contextmanager
def _projection_context() -> Iterator[dict[str, object]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        user = User(
            username="projection-owner",
            password_hash="unused",
            display_name="投影合同管理员",
            is_admin=True,
        )
        published_release = LabelRelease(
            release_key="formal-release",
            content_key="content:formal-1",
            category_key="space_image",
            label_schema_version="published-label-v1",
            label_payload_json="{}",
            payload_hash="a" * 64,
            status="published",
            requested_by=user.username,
            approved_by=user.username,
            approved_at=datetime.now(timezone.utc),
            published_at=datetime.now(timezone.utc),
        )
        candidate_release = LabelRelease(
            release_key="candidate-release",
            content_key="content:candidate-1",
            category_key="space_image",
            label_schema_version="published-label-v1",
            label_payload_json=json.dumps({"candidate_mechanism": "must-not-project"}),
            payload_hash="b" * 64,
            status="pending_review",
            requested_by=user.username,
        )
        db.add_all([user, published_release, candidate_release])
        db.flush()
        payload = {
            "schema_version": "published-label-v1",
            "content_key": "content:formal-1",
            "category_key": "space_image",
            "level": "L2",
            "score": 88,
            "classification": {"primary_category": "住宅"},
            "dimensions": {"composition": 4},
            "production_fields": {"title": "现代客厅", "tags": ["客厅", "现代"]},
            "image_quality": {"quality_severity": "normal"},
            "media_form": {"rendering": {"status": "yes"}},
            "provenance": {
                "asset_id": 101,
                "asset_sha256": "c" * 64,
                "strategy_bundle_id": 7,
                "model_id": "quality-model-v2",
                "rubric_version": "R3",
                "engine_version": "E2",
            },
            "raw_response": "provider secret",
            "human_review": {"reviewer": "must-not-project"},
            "candidate_mechanism": {"id": 999},
        }
        db.add(
            PublishedLabel(
                release_id=published_release.id,
                content_key=published_release.content_key,
                category_key=published_release.category_key,
                version=1,
                label_schema_version=published_release.label_schema_version,
                label_payload_json=json.dumps(payload, ensure_ascii=False),
                payload_hash="d" * 64,
                status="published",
                published_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    def override_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    try:
        yield {"client": TestClient(app), "sessions": sessions}
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_projection_registry_is_versioned_and_manifest_reads_only_formal_facts() -> None:
    with _projection_context() as fixture:
        client = fixture["client"]
        created = client.post("/api/projection-contracts", json=_contract_payload())

        assert created.status_code == 200, created.text
        contract = created.json()
        assert contract["version"] == 1
        assert contract["status"] == "draft"

        next_version = client.post("/api/projection-contracts", json=_contract_payload())
        assert next_version.status_code == 200
        assert next_version.json()["version"] == 2

        listed = client.get("/api/projection-contracts")
        assert listed.status_code == 200
        assert [item["version"] for item in listed.json()["items"]] == [2, 1]

        manifest = client.post(
            f"/api/projection-contracts/{contract['id']}/manifest"
        )
        assert manifest.status_code == 200, manifest.text
        body = manifest.json()
        assert body["row_count"] == 1
        assert body["content_keys"] == ["content:formal-1"]
        assert len(body["payload_hash"]) == 64
        assert len(body["manifest_hash"]) == 64
        assert body["input_versions"]["label_release_versions"] == [1]
        assert body["input_versions"]["asset_versions"] == ["c" * 64]
        assert body["input_versions"]["mechanism_versions"] == ["strategy-bundle:7"]
        assert body["input_versions"]["model_versions"] == ["quality-model-v2"]
        serialized = json.dumps(body, ensure_ascii=False).lower()
        assert "candidate-1" not in serialized
        assert "provider secret" not in serialized
        assert "must-not-project" not in serialized

        repeated = client.post(
            f"/api/projection-contracts/{contract['id']}/manifest"
        )
        assert repeated.status_code == 200
        assert repeated.json()["id"] == body["id"]
        assert repeated.json()["manifest_hash"] == body["manifest_hash"]


def test_local_projection_reconcile_reports_counts_hash_and_version_match() -> None:
    with _projection_context() as fixture:
        client = fixture["client"]
        contract = client.post(
            "/api/projection-contracts", json=_contract_payload()
        ).json()
        manifest = client.post(
            f"/api/projection-contracts/{contract['id']}/manifest"
        ).json()

        reconciled = client.post(
            f"/api/projection-contracts/{contract['id']}/reconcile"
        )

        assert reconciled.status_code == 200, reconciled.text
        body = reconciled.json()
        assert body["status"] == "matched"
        assert body["row_count"] == 1
        assert body["missing_count"] == 0
        assert body["unexpected_count"] == 0
        assert body["payload_hash"] == manifest["payload_hash"]
        assert body["version_match"] is True
        assert body["target_table"] == "unified_dimension_table"


def test_projection_contract_rejects_forbidden_source_fields() -> None:
    with _projection_context() as fixture:
        payload = _contract_payload(contract_key="unsafe-contract")
        payload["field_mappings"] = {
            "content_key": "content_key",
            "raw": "raw_response",
        }

        response = fixture["client"].post("/api/projection-contracts", json=payload)

        assert response.status_code == 422
        assert "禁止" in response.text


def test_projection_contract_rejects_nested_process_fields() -> None:
    with _projection_context() as fixture:
        payload = _contract_payload(contract_key="unsafe-nested-contract")
        payload["field_mappings"] = {
            "content_key": "content_key",
            "review_note": "classification.human_review_note",
        }

        response = fixture["client"].post("/api/projection-contracts", json=payload)

        assert response.status_code == 422
        assert "禁止" in response.text

        payload["contract_key"] = "unsafe-secret-contract"
        payload["field_mappings"] = {
            "content_key": "content_key",
            "credential": "classification.api_key",
        }
        secret_response = fixture["client"].post(
            "/api/projection-contracts", json=payload
        )
        assert secret_response.status_code == 422
        assert "禁止" in secret_response.text


def test_projection_drift_detection_never_mutates_canonical_labels() -> None:
    with _projection_context() as fixture:
        client = fixture["client"]
        contract_payload = client.post(
            "/api/projection-contracts", json=_contract_payload()
        ).json()
        client.post(
            f"/api/projection-contracts/{contract_payload['id']}/reconcile"
        )

        with fixture["sessions"]() as db:
            canonical_before = [
                (item.id, item.payload_hash, item.label_payload_json, item.status)
                for item in db.scalars(
                    select(PublishedLabel).order_by(PublishedLabel.id)
                ).all()
            ]
            contract = db.get(ProjectionContract, contract_payload["id"])
            manifest = db.scalar(
                select(ProjectionManifest)
                .where(ProjectionManifest.contract_id == contract.id)
                .order_by(ProjectionManifest.id.desc())
            )
            local_row = db.scalar(
                select(LocalProjectionRow).where(
                    LocalProjectionRow.table_name == contract.table_name
                )
            )
            assert contract is not None and manifest is not None and local_row is not None
            local_row.payload_json = json.dumps(
                {"content_key": "content:formal-1", "level": "tampered"}
            )
            local_row.payload_hash = "0" * 64
            db.commit()

            result = LocalProjectionAdapter().reconcile(
                db,
                contract=contract,
                manifest=manifest,
            )
            canonical_after = [
                (item.id, item.payload_hash, item.label_payload_json, item.status)
                for item in db.scalars(
                    select(PublishedLabel).order_by(PublishedLabel.id)
                ).all()
            ]

        assert result.status == "drift"
        assert result.reason == "payload_hash_mismatch"
        assert canonical_after == canonical_before
