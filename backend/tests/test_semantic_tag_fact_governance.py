from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.audit import canonical_json
from app.label_governance import (
    approve_semantic_facts,
    build_label_snapshot,
    create_release,
)
from app.models import AssetVersion, ReviewPanel, SemanticTagFact, TagDemandContract
from app.semantic_tag_contracts import canonical_contract_hash, validate_tag_demand_contract

from tests.test_unified_label_platform import _database
from tests.test_tag_demand_contract_api import _definition


def _definition_for_test_categories() -> dict[str, object]:
    payload = _definition()
    payload["category_applicability"]["space_image"] = dict(
        payload["category_applicability"]["model_3d_su"]
    )
    variant = dict(payload["execution_variants"][0])
    variant["category_key"] = "space_image"
    payload["execution_variants"].append(variant)
    return payload


def _active_contract(db) -> TagDemandContract:
    definition = validate_tag_demand_contract(_definition_for_test_categories())
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


def _attach_semantic_route(result, *, contract: TagDemandContract, asset_version: AssetVersion) -> None:
    precheck = json.loads(result.precheck_json or "{}")
    precheck["semantic_route"] = {
        "contract_id": contract.id,
        "contract_version": contract.version,
        "contract_hash": contract.contract_hash,
        "asset_version_id": asset_version.id,
        "site_scope": "domestic",
        "asset_scope": "whole",
        "locale": "zh",
        "category_key": result.job.category_key,
        "prompt_variant": "whole",
        "prompt_version": "prompt-v1",
        "model_version": "model-v1",
    }
    result.precheck_json = canonical_json(precheck)


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
        _attach_semantic_route(result, contract=contract, asset_version=asset_version)
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
        _attach_semantic_route(result, contract=contract, asset_version=asset_version)
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


def test_semantic_approval_is_idempotent_for_same_final_review() -> None:
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
        _attach_semantic_route(result, contract=contract, asset_version=asset_version)
        _candidate_fact(
            db,
            contract=contract,
            asset_version=asset_version,
            evaluation_id=result.id,
        )
        db.commit()

        first = approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
        db.commit()
        second = approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
        db.commit()

        assert [row.id for row in second] == [row.id for row in first]
        approved = db.scalars(
            select(SemanticTagFact).where(SemanticTagFact.status == "approved")
        ).all()
        assert len(approved) == 1
    finally:
        db.close()
        engine.dispose()


def test_semantic_approval_rejects_contract_drift() -> None:
    engine, db, _user, result = _database()
    try:
        old_contract = _active_contract(db)
        asset_version = AssetVersion(
            asset_id=result.asset_id,
            version=1,
            asset_sha256="a" * 64,
            source_version="fixture-v1",
            created_by="test",
        )
        db.add(asset_version)
        db.flush()
        _attach_semantic_route(
            result,
            contract=old_contract,
            asset_version=asset_version,
        )
        _candidate_fact(
            db,
            contract=old_contract,
            asset_version=asset_version,
            evaluation_id=result.id,
        )
        old_contract.status = "retired"
        definition = validate_tag_demand_contract(_definition_for_test_categories())
        current = TagDemandContract(
            contract_key="semantic-platform",
            version=2,
            status="active",
            definition_json=json.dumps(
                definition.model_dump(mode="json"), ensure_ascii=False
            ),
            contract_hash=canonical_contract_hash(definition),
            created_by="test",
        )
        db.add(current)
        db.commit()

        with pytest.raises(ValueError, match="合同.*漂移"):
            approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
    finally:
        db.close()
        engine.dispose()


def test_semantic_approval_rejects_not_applicable_values() -> None:
    engine, db, _user, result = _database()
    try:
        definition_payload = _definition_for_test_categories()
        for category_key in ("model_3d_su", "space_image"):
            definition_payload["category_applicability"][category_key]["style"] = (
                "not_applicable"
            )
        definition = validate_tag_demand_contract(definition_payload)
        contract = TagDemandContract(
            contract_key="semantic-platform",
            version=1,
            status="active",
            definition_json=json.dumps(
                definition.model_dump(mode="json"), ensure_ascii=False
            ),
            contract_hash=canonical_contract_hash(definition),
            created_by="test",
        )
        asset_version = AssetVersion(
            asset_id=result.asset_id,
            version=1,
            asset_sha256="a" * 64,
            source_version="fixture-v1",
            created_by="test",
        )
        db.add_all([contract, asset_version])
        db.flush()
        _attach_semantic_route(result, contract=contract, asset_version=asset_version)
        _candidate_fact(
            db,
            contract=contract,
            asset_version=asset_version,
            evaluation_id=result.id,
        )
        db.commit()

        with pytest.raises(ValueError, match="values.*必须为空"):
            approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
    finally:
        db.close()
        engine.dispose()


def test_semantic_release_metadata_matches_v2_payload() -> None:
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
        _attach_semantic_route(result, contract=contract, asset_version=asset_version)
        _candidate_fact(
            db,
            contract=contract,
            asset_version=asset_version,
            evaluation_id=result.id,
        )
        approve_semantic_facts(db, evaluation_id=result.id, actor="reviewer")
        release, _duplicate = create_release(
            db,
            release_key="semantic-v2-release",
            evaluation_id=result.id,
            content_key=None,
            requested_by="reviewer",
        )

        assert json.loads(release.label_payload_json)["schema_version"] == (
            "published-label-v2"
        )
        assert release.label_schema_version == "published-label-v2"
    finally:
        db.close()
        engine.dispose()
