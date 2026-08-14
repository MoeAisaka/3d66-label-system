from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import run_migrations
from app.models import (
    Asset,
    AssetVersion,
    LabelRelease,
    PublishedLabel,
    SemanticQualityMetricSnapshot,
    SemanticTagFact,
    TagDemandContract,
)


def _engine(tmp_path):
    return create_engine(
        f"sqlite:///{tmp_path / 'semantic-contract.db'}",
        connect_args={"check_same_thread": False},
    )


def _contract() -> TagDemandContract:
    definition = {
        "schema_version": "tag-demand-contract-v1",
        "semantic_schema": {"schema_version": "semantic-tag-schema-v1", "fields": {}},
    }
    return TagDemandContract(
        contract_key="semantic-platform-v1",
        version=1,
        status="candidate",
        definition_json=json.dumps(definition, sort_keys=True),
        contract_hash="a" * 64,
        created_by="test-owner",
    )


def test_asset_version_and_semantic_rows_persist_after_migration(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        asset = Asset(
            original_name="semantic.jpg",
            stored_name="semantic.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256="b" * 64,
            category_key="model_3d_su",
        )
        db.add(asset)
        db.flush()
        version = AssetVersion(
            asset_id=asset.id,
            version=1,
            asset_sha256=asset.sha256,
            source_version="source-v1",
            snapshot_kind="materialized",
            created_by="test-owner",
        )
        contract = _contract()
        db.add_all([version, contract])
        db.flush()
        fact = SemanticTagFact(
            asset_version_id=version.id,
            field_key="style",
            fact_version=1,
            field_status="optional",
            supersedes_fact_id=None,
            values_json='[{"entity_id":"style.modern"}]',
            evidence_json='[{"ref":"evaluation:1"}]',
            source_evaluation_id=1,
            source_review_id=1,
            contract_id=contract.id,
            normalization_version="semantic-normalization-v1",
            mapping_version="style-map-v1",
            status="approved",
            payload_hash="c" * 64,
        )
        metrics = SemanticQualityMetricSnapshot(
            baseline_run_id=1,
            contract_id=contract.id,
            category_key="model_3d_su",
            site_scope="domestic",
            asset_scope="whole",
            field_key="style",
            truth_count=1,
            predicted_count=1,
            true_positive_count=1,
            precision=1.0,
            recall=1.0,
            mapping_coverage=1.0,
            unmapped_rate=0.0,
            conflict_rate=0.0,
            null_semantics_accuracy=1.0,
            correction_rate=0.0,
            review_coverage=1.0,
            bilingual_consistency=1.0,
            reconciliation_rate=1.0,
            metrics_hash="d" * 64,
        )
        db.add_all([fact, metrics])
        db.commit()
        assert db.scalar(select(AssetVersion).where(AssetVersion.id == version.id)) is not None
        assert db.scalar(select(SemanticTagFact).where(SemanticTagFact.id == fact.id)) is not None
        assert db.scalar(select(SemanticQualityMetricSnapshot).where(SemanticQualityMetricSnapshot.id == metrics.id)) is not None
    engine.dispose()


def test_asset_version_is_immutable_per_asset_revision(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        asset = Asset(
            original_name="immutable.jpg",
            stored_name="immutable.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256="e" * 64,
            category_key="model_3d_su",
        )
        db.add(asset)
        db.flush()
        version = AssetVersion(
            asset_id=asset.id,
            version=1,
            asset_sha256=asset.sha256,
            source_version="source-v1",
            snapshot_kind="materialized",
            created_by="test-owner",
        )
        db.add(version)
        db.commit()
        version.asset_sha256 = "f" * 64
        with pytest.raises(IntegrityError, match="AssetVersion is immutable"):
            db.commit()
        db.rollback()
        duplicate = AssetVersion(
            asset_id=asset.id,
            version=1,
            asset_sha256=asset.sha256,
            source_version="source-v1-duplicate",
            snapshot_kind="materialized",
            created_by="test-owner",
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            db.commit()
    engine.dispose()


def test_contract_definition_is_immutable_but_status_transition_is_allowed(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        contract = _contract()
        db.add(contract)
        db.commit()
        contract.status = "active"
        db.commit()
        contract.definition_json = "{}"
        with pytest.raises(IntegrityError, match="TagDemandContract definition is immutable"):
            db.commit()
    engine.dispose()


def test_published_label_survives_semantic_contract_migration(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        release = LabelRelease(
            release_key="legacy-release",
            content_key="content:legacy",
            category_key="model_3d_su",
            label_schema_version="published-label-v1",
            label_payload_json='{"level":"L2"}',
            payload_hash="1" * 64,
            status="published",
            requested_by="test-owner",
        )
        db.add(release)
        db.flush()
        label = PublishedLabel(
            release_id=release.id,
            content_key=release.content_key,
            category_key=release.category_key,
            version=1,
            label_schema_version=release.label_schema_version,
            label_payload_json=release.label_payload_json,
            payload_hash=release.payload_hash,
            status="published",
        )
        db.add(label)
        db.commit()
        label_id = label.id
        payload_before = label.label_payload_json
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for version in range(1, 68):
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (version, f"legacy-{version}"),
            )
        run_migrations(connection)
    with Session(engine) as db:
        restored = db.get(PublishedLabel, label_id)
        assert restored is not None
        assert restored.label_payload_json == payload_before
        assert db.scalar(select(TagDemandContract).where(TagDemandContract.id.is_not(None))) is None
    engine.dispose()
