from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.label_governance import approve_semantic_facts, build_label_snapshot
from app.models import AssetVersion, ReviewPanel, SemanticTagFact, TagDemandContract
from app.semantic_tag_contracts import canonical_contract_hash, validate_tag_demand_contract

from tests.test_unified_label_platform import _database
from tests.test_tag_demand_contract_api import _definition


def _active_contract(db) -> TagDemandContract:
    definition = validate_tag_demand_contract(_definition())
    contract = TagDemandContract(
        contract_key="semantic-platform",
        version=1,
        status="active",
        definition_json=json.dumps(definition.model_dump(mode="json"), ensure_ascii=False),
        contract_hash=canonical_contract_hash(definition),
        created_by="test",
    )
    db.add(contract)
    db.flush()
    return contract


def _candidate_fact(db, *, contract: TagDemandContract, asset_version: AssetVersion, evaluation_id: int, field_key: str = "style") -> SemanticTagFact:
    fact = SemanticTagFact(
        asset_version_id=asset_version.id,
        field_key=field_key,
        fact_version=1,
        field_status="needs_review",
        values_json=json.dumps([{
            "value": "现代简约",
            "entity_id": "style.modern",
            "locale": "zh",
            "rank": 1,
            "weight": 1.0,
            "evidence_ref": "evaluation:1#semantic.style.0",
        }], ensure_ascii=False),
        evidence_json=json.dumps(["evaluation:1#semantic.style.0"], ensure_ascii=False),
        source_evaluation_id=evaluation_id,
        source_review_id=None,
        contract_id=contract.id,
        normalization_version="semantic-normalization-v1",
        mapping_version="style-map-v1",
        status="candidate",
        payload_hash="a" * 64,
    )
    db.add(fact)
    db.flush()
    return fact


def test_only_completed_human_truth_can_approve_semantic_facts() -> None:
    engine, db, _user, result = _database()
    try:
        contract = _active_contract(db)
        asset_version = AssetVersion(
            asset_id=result.asset_id,
            version=1,
            asset_sha256="a" * 64,
            source_version="fixture-v1",
            created_by="test",
        )
        db.add(asset_version)
        db.flush()
        _candidate_fact(db, contract=contract, asset_version=asset_version, evaluation_id=result.id)
        panel = db.scalar(select(ReviewPanel).where(ReviewPanel.evaluation_id == result.id))
        assert panel is not None
        panel.status = "collecting"
        panel.final_review_id = None
        db.commit()

        with pytest.raises(ValueError, match="人工真值"):
            approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
    finally:
        db.close()
        engine.dispose()


def test_published_label_v2_contains_only_approved_structured_facts() -> None:
    engine, db, _user, result = _database()
    try:
        contract = _active_contract(db)
        asset_version = AssetVersion(
            asset_id=result.asset_id,
            version=1,
            asset_sha256="a" * 64,
            source_version="fixture-v1",
            created_by="test",
        )
        db.add(asset_version)
        db.flush()
        _candidate_fact(db, contract=contract, asset_version=asset_version, evaluation_id=result.id)
        db.commit()

        approved = approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
        assert approved[0].status == "approved"
        content_key, _evaluation_id, _review_id, payload = build_label_snapshot(
            db, evaluation_id=result.id, content_key=None
        )
        assert content_key.startswith("asset:")
        assert payload["schema_version"] == "published-label-v2"
        assert payload["semantic"]["style"]["values"][0]["entity_id"] == "style.modern"
        assert payload["provenance"]["asset_version_id"] == asset_version.id
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "raw_response" not in serialized
        assert "candidate" not in serialized
    finally:
        db.close()
        engine.dispose()
