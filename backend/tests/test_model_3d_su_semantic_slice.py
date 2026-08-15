from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.audit import canonical_json
from app.label_governance import (
    approve_semantic_facts,
    create_release,
    publish_release,
    resolve_semantic_execution_route,
)
from app.models import AssetVersion, ContentRecord, SemanticTagFact, TagDemandContract
from app.model_3d_su_category_seed import seed_model_3d_su
from app.projection_contracts import LocalProjectionAdapter, build_projection_manifest, create_contract_version
from app.schema_adapter import attach_semantic_candidates
from app.semantic_tag_mapping import map_standard_entities

from tests.test_unified_label_platform import _client, _database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((PROJECT_ROOT / "backend/fixtures/semantic/model_3d_su_domestic_v1.json").read_text())


def run_fixture_slice(*, asset_scope: str) -> dict:
    sample = next(item for item in FIXTURE["samples"] if item["asset_scope"] == asset_scope)
    engine, db, user, result = _database()
    client = _client(db, user)
    try:
        result.asset.category_key = "model_3d_su"
        result.asset.sha256 = sample["asset_sha256"]
        result.job.category_key = "model_3d_su"
        seed_model_3d_su(db, SimpleNamespace(project_root=PROJECT_ROOT))
        db.flush()
        draft = db.query(TagDemandContract).filter(TagDemandContract.contract_key == "semantic-platform").one()
        candidate = client.post("/api/tag-demand-contracts", json={
            "contract_key": "semantic-platform",
            "definition": json.loads(draft.definition_json),
            "status": "candidate",
        })
        assert candidate.status_code == 201, candidate.text
        activated = client.post(f"/api/tag-demand-contracts/{candidate.json()['id']}/activate")
        assert activated.status_code == 200, activated.text
        contract = db.get(TagDemandContract, candidate.json()["id"])
        asset_version = AssetVersion(
            asset_id=result.asset_id,
            version=1,
            asset_sha256=sample["asset_sha256"],
            source_version=f"fixture-{sample['key']}",
            created_by="fixture",
        )
        db.add(asset_version)
        db.add(ContentRecord(
            source_system="fixture",
            source_content_id=sample["key"],
            category_key="model_3d_su",
            source_version="v1",
            source_occurred_at=datetime.now(timezone.utc),
            asset_id=result.asset_id,
            status="ready",
        ))
        db.flush()
        route = resolve_semantic_execution_route(
            db,
            content_record=db.query(ContentRecord).filter(ContentRecord.asset_id == result.asset_id).one(),
            asset_version=asset_version,
            site_scope="domestic",
            asset_scope=asset_scope,
            locale="zh",
            prompt_variant=asset_scope,
            prompt_version=f"model-3d-su-semantic-{asset_scope}-v1",
            model_version="fixture-model-v1",
        )
        precheck = attach_semantic_candidates(
            json.loads(result.precheck_json),
            route=route,
            provider_payload={"semantic": sample["semantic"]},
            evidence_prefix=f"evaluation:{result.id}",
        )
        result.precheck_json = canonical_json(precheck)
        for field_key, bundle in precheck["semantic_candidates"].items():
            from app.semantic_tag_mapping import candidate_bundle, candidate
            mapped = map_standard_entities(
                bundle=candidate_bundle(field_key=field_key, values=[candidate(
                    item["value"], locale=item["locale"], rank=item["rank"], weight=item["weight"], evidence_ref=item["evidence_ref"],
                ) for item in bundle]),
                mapping_registry=FIXTURE["mapping_registry"][field_key],
                normalization_version="semantic-normalization-v1",
                mapping_version="kg-entity-map-v1",
            )
            values = [{
                "value": value.localized_names.get("zh", value.entity_id),
                "entity_id": value.entity_id,
                "localized_names": dict(value.localized_names),
                "locale": "zh",
                "rank": value.rank,
                "weight": value.weight,
                "evidence_ref": value.evidence_refs[0],
            } for value in mapped.values]
            db.add(SemanticTagFact(
                asset_version_id=asset_version.id,
                field_key=field_key,
                fact_version=1,
                field_status="needs_review",
                values_json=canonical_json(values),
                evidence_json=canonical_json(list(mapped.evidence_refs)),
                source_evaluation_id=result.id,
                contract_id=contract.id,
                normalization_version="semantic-normalization-v1",
                mapping_version="kg-entity-map-v1",
                status="candidate",
                payload_hash="f" * 64,
            ))
        db.flush()
        approve_semantic_facts(db, evaluation_id=result.id, actor=user.username)
        release, _ = create_release(db, release_key=f"fixture-{sample['key']}", evaluation_id=result.id, content_key=None, requested_by=user.username)
        published, _ = publish_release(db, release=release, actor=user.username)
        projection = create_contract_version(
            db,
            contract_key=f"fixture-{sample['key']}-projection",
            target_role="unified_dimension",
            table_name="unified_dimension_table",
            environment="local",
            primary_key=["content_key"],
            field_mappings={
                "content_key": "content_key",
                "space": "semantic.space.primary_name.zh",
                "object": "semantic.object.weighted_names.zh",
                "is_single": "provenance.is_single",
            },
            input_versions={"label_schema_version": "published-label-v2"},
            mode="snapshot",
            idempotency_key_template="{content_key}:{label_version}",
            checkpoint={"kind": "published_label_id"},
            reconciliation={"checks": ["row_count", "payload_hash", "version"]},
            rollback={"strategy": "rebuild"},
            owner="fixture",
            status="draft",
            created_by="fixture",
        )
        manifest, manifest_body = build_projection_manifest(db, contract=projection)
        adapter = LocalProjectionAdapter()
        adapter.apply(db, contract=projection, manifest=manifest)
        reconciliation = adapter.reconcile(db, contract=projection, manifest=manifest)
        return {
            "route": route.__dict__,
            "published_label": json.loads(published.label_payload_json),
            "projection_row": manifest_body["rows"][0],
            "reconciliation": reconciliation.__dict__,
        }
    finally:
        client.close()
        db.close()
        engine.dispose()


@pytest.mark.parametrize(("asset_scope", "expected_is_single"), [("whole", 0), ("single", 1)])
def test_domestic_3d_su_slice_reaches_dry_run_projection(asset_scope: str, expected_is_single: int) -> None:
    result = run_fixture_slice(asset_scope=asset_scope)
    assert result["route"]["category_key"] == "model_3d_su"
    assert result["published_label"]["schema_version"] == "published-label-v2"
    assert result["projection_row"]["is_single"] == expected_is_single
    assert result["reconciliation"]["status"] == "matched"


def test_3d_su_quality_extension_does_not_replace_platform_semantic_fields() -> None:
    result = run_fixture_slice(asset_scope="whole")
    assert "semantic" in result["published_label"]
    assert "quality" in result["published_label"]
    assert result["published_label"]["quality"]["level"] in {"L1", "L2", "L3", "L4"}
