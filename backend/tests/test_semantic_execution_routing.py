from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.label_governance import SemanticTagRoutingError, resolve_semantic_execution_route
from app.migrations import run_migrations
from app.models import Asset, AssetVersion, ContentRecord, EvaluationCategoryProfile, TagDemandContract
from app.dimension_schema_registry import canonical_json


def _definition(*, category_key: str = "model_3d_su") -> dict[str, object]:
    field_keys = (
        "space", "object", "style", "material", "structural_features",
        "architectural_element", "soft_decoration", "hard_decoration", "color", "title",
    )
    fields = {
        key: {
            "field_key": key,
            "cardinality": "multi" if key == "object" else "single",
            "localized": True,
            "vocabulary_owner": "semantic-owner",
            "max_values": 10 if key == "object" else 1,
            "default_value": [],
        }
        for key in field_keys
    }
    return {
        "schema_version": "tag-demand-contract-v1",
        "semantic_schema": {"schema_version": "semantic-tag-schema-v1", "fields": fields},
        "category_applicability": {
            category_key: {
                key: "required" if key in {"space", "object", "style"} else "optional"
                for key in field_keys
            }
        },
        "execution_variants": [{
            "site_scope": "domestic",
            "asset_scope": "whole",
            "locale": "zh",
            "category_key": category_key,
            "prompt_variant": "whole",
            "prompt_version": "prompt-v1",
            "model_version": "model-v1",
        }],
        "quality_gates": {"style": {"min_precision": 0.8, "min_recall": 0.7, "min_mapping_coverage": 0.9, "max_conflict_rate": 0.1}},
        "projection_targets": [{"target_key": "domestic_material_tags", "mode": "dry_run", "locale": "zh"}],
    }


def _fixture(category_key: str = "model_3d_su") -> tuple[Session, AssetVersion, ContentRecord]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine)
    asset = Asset(
        original_name="route.jpg",
        stored_name="route.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
        category_key=category_key,
    )
    db.add(asset)
    db.flush()
    version = AssetVersion(
        asset_id=asset.id,
        version=1,
        asset_sha256=asset.sha256,
        source_version="source-v1",
        snapshot_kind="materialized",
        created_by="route-test",
    )
    record = ContentRecord(
        source_system="fixture",
        source_content_id="route-1",
        category_key=category_key,
        source_version="source-v1",
        source_occurred_at=datetime.now(timezone.utc),
        asset_id=asset.id,
        status="ready",
    )
    profile = EvaluationCategoryProfile(
        category_key=category_key,
        display_name="路由类目",
        status="active",
    )
    contract = TagDemandContract(
        contract_key="semantic-platform",
        version=1,
        status="active",
        definition_json=canonical_json(_definition(category_key=category_key)),
        contract_hash="b" * 64,
        approved_by="route-owner",
        created_by="route-owner",
    )
    db.add_all([version, record, profile, contract])
    db.commit()
    return db, version, record


def test_four_batches_share_one_active_contract_and_return_field_statuses() -> None:
    db, version, record = _fixture()
    try:
        platform = db.query(TagDemandContract).filter_by(
            contract_key="semantic-platform"
        ).one()
        db.add(
            TagDemandContract(
                contract_key="unrelated-semantic-contract",
                version=999,
                status="active",
                definition_json=canonical_json(_definition(category_key=record.category_key)),
                contract_hash="c" * 64,
                approved_by="other-owner",
                created_by="other-owner",
            )
        )
        db.commit()
        route = resolve_semantic_execution_route(
            db,
            content_record=record,
            asset_version=version,
            site_scope="domestic",
            asset_scope="whole",
            locale="zh",
            prompt_variant="whole",
            prompt_version="prompt-v1",
            model_version="model-v1",
        )
        assert route.contract_id == platform.id
        assert route.category_key == "model_3d_su"
        assert route.fields["space"] == "required"
        assert route.fields["material"] == "optional"
    finally:
        db.get_bind().dispose()


def test_route_rejects_missing_asset_version_and_variant_mismatch() -> None:
    db, version, record = _fixture()
    try:
        with pytest.raises(SemanticTagRoutingError, match="素材版本"):
            resolve_semantic_execution_route(
                db,
                content_record=record,
                asset_version=None,
                site_scope="domestic",
                asset_scope="whole",
                locale="zh",
                prompt_variant="whole",
                prompt_version="prompt-v1",
                model_version="model-v1",
            )
        with pytest.raises(SemanticTagRoutingError, match="执行变体"):
            resolve_semantic_execution_route(
                db,
                content_record=record,
                asset_version=version,
                site_scope="overseas",
                asset_scope="whole",
                locale="en",
                prompt_variant="whole",
                prompt_version="prompt-v1",
                model_version="model-v1",
            )
    finally:
        db.get_bind().dispose()


def test_route_rejects_inactive_category_profile() -> None:
    db, version, record = _fixture()
    try:
        db.query(EvaluationCategoryProfile).filter_by(category_key=record.category_key).update({"status": "retired"})
        db.commit()
        with pytest.raises(SemanticTagRoutingError, match="类目 profile"):
            resolve_semantic_execution_route(
                db,
                content_record=record,
                asset_version=version,
                site_scope="domestic",
                asset_scope="whole",
                locale="zh",
                prompt_variant="whole",
                prompt_version="prompt-v1",
                model_version="model-v1",
            )
    finally:
        db.get_bind().dispose()
