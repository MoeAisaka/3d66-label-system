from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import run_migrations
from app.models import (
    Asset,
    AssetVersion,
    BaselineRegressionRun,
    BaselineSet,
    ContentIngressEvent,
    ContentRecord,
    LabelRelease,
    PublishedLabel,
    SemanticQualityMetricSnapshot,
    SemanticTagFact,
    StrategyBundle,
    SourceIdentityVerification,
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


def test_migration_70_preserves_legacy_content_records_as_unverified(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        record = ContentRecord(
            source_system="legacy",
            source_content_id="1",
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=datetime.now(timezone.utc),
            status="awaiting_material",
        )
        db.add(record)
        db.commit()
        assert record.identity_status == "legacy_unverified"
        assert record.content_key is None
    engine.dispose()


def test_verified_content_key_is_unique_when_present(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        first = ContentRecord(
            source_system="aliyun_3d66_dw",
            source_content_id="source-1",
            content_key="aliyun_3d66_dw:1:12345",
            source_res_type=1,
            source_ll_id="12345",
            identity_status="verified",
            identity_hash="a" * 64,
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=now,
            status="awaiting_material",
        )
        second = ContentRecord(
            source_system="aliyun_3d66_dw",
            source_content_id="source-2",
            content_key="aliyun_3d66_dw:1:12345",
            source_res_type=1,
            source_ll_id="12345",
            identity_status="verified",
            identity_hash="b" * 64,
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=now,
            status="awaiting_material",
        )
        db.add(first)
        db.commit()
        db.add(second)
        with pytest.raises(IntegrityError, match="content_key"):
            db.commit()
    engine.dispose()


def test_only_one_approved_identity_verification_exists_per_source_contract(
    tmp_path,
) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        def row(probe_hash: str) -> SourceIdentityVerification:
            return SourceIdentityVerification(
                contract_key="semantic-platform",
                source_system="aliyun_3d66_dw",
                key_fields_json='["res_type","ll_id"]',
                result="verified",
                probe_hash=probe_hash,
                data_window="2026-08-01/2026-08-15",
                scoped_row_count=100,
                duplicate_key_count=0,
                res_id_conflict_count=0,
                status="approved",
                created_by="test",
                approved_by="test",
                approved_at=datetime.now(timezone.utc),
            )

        db.add(row("c" * 64))
        db.commit()
        db.add(row("d" * 64))
        with pytest.raises(IntegrityError, match="source_identity_verifications"):
            db.commit()
    engine.dispose()


def test_verified_content_identity_is_immutable(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        record = ContentRecord(
            source_system="aliyun_3d66_dw",
            source_content_id="1:12345",
            content_key="aliyun_3d66_dw:1:12345",
            source_res_type=1,
            source_ll_id="12345",
            identity_status="verified",
            identity_hash="a" * 64,
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=datetime.now(timezone.utc),
            status="awaiting_material",
        )
        db.add(record)
        db.commit()
        record.identity_hash = "b" * 64
        with pytest.raises(IntegrityError, match="ContentRecord identity is immutable"):
            db.commit()
    engine.dispose()


def test_content_ingress_identity_snapshot_is_immutable(tmp_path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        event = ContentIngressEvent(
            event_id="identity-event-1",
            schema_version="content-ingress-v2",
            event_type="content.created",
            source_system="aliyun_3d66_dw",
            occurred_at=datetime.now(timezone.utc),
            payload_hash="a" * 64,
            payload_json="{}",
            identity_snapshot_json='{"content_key":"aliyun_3d66_dw:1:12345"}',
            identity_hash="b" * 64,
            status="awaiting_material",
            received_by="test",
        )
        db.add(event)
        db.commit()
        event.identity_snapshot_json = "{}"
        with pytest.raises(IntegrityError, match="ContentIngressEvent identity is immutable"):
            db.commit()
    engine.dispose()


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
        baseline_set = BaselineSet(
            name="semantic-model-fixture",
            category_key="model_3d_su",
            default_expected_level="L1",
            fingerprint="e" * 64,
            created_by="test-owner",
        )
        bundle = StrategyBundle(
            canonical_hash="f" * 64,
            strategy_schema_version="strategy-bundle-v1",
            model_id="fixture-model",
            model_config_snapshot="{}",
            prompt_a_version="prompt-a",
            prompt_b_version="prompt-b",
            rubric_version="rubric-v1",
            engine_version="engine-v1",
        )
        db.add_all([version, contract, baseline_set, bundle])
        db.flush()
        run = BaselineRegressionRun(
            baseline_set_id=baseline_set.id,
            sequence_no=1,
            strategy_bundle_id=bundle.id,
            category_key="model_3d_su",
            strategy_snapshot_json="{}",
            execution_snapshot_json="{}",
            baseline_set_fingerprint=baseline_set.fingerprint,
            status="completed",
            total=0,
            metrics_json="{}",
            created_by="test-owner",
        )
        db.add(run)
        db.flush()
        fact = SemanticTagFact(
            asset_version_id=version.id,
            field_key="style",
            fact_version=1,
            field_status="optional",
            supersedes_fact_id=None,
            values_json='[{"entity_id":"style.modern"}]',
            evidence_json='[{"ref":"evaluation:1"}]',
            source_evaluation_id=None,
            source_review_id=None,
            contract_id=contract.id,
            normalization_version="semantic-normalization-v1",
            mapping_version="style-map-v1",
            status="approved",
            payload_hash="c" * 64,
        )
        metrics = SemanticQualityMetricSnapshot(
            baseline_run_id=run.id,
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
