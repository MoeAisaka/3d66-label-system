from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.category_evaluation_contract import canonical_contract_hash
from app.database import Base
from app.audit import canonical_json
from app.field_demand_contracts import create_field_demand_contract
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.label_governance import create_release, publish_release
from app.migrations import run_migrations
from app.models import (
    Asset,
    CategoryEvaluationV3Config,
    EvaluationCategoryProfile,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    ModelConfig,
    OptimizationCaseQueue,
    PromptVersion,
    ReviewPanel,
    SamplingPolicy,
    SemanticTagFact,
    TagDemandContract,
)
from app.production_feedback import ingest_production_feedback
from app.projection_contracts import create_contract_version
from app.readonly_sources import (
    FixtureReadOnlySourceAdapter,
    SourceRow,
    create_upstream_source_contract,
    poll_upstream_source,
)
from app.shadow_projection import (
    FixtureShadowProjectionAdapter,
    create_shadow_projection_target,
    enqueue_shadow_projection_run,
    shadow_projection_worker_tick,
)
from app.strategy_bundle import build_evaluation_strategy_snapshot, get_or_create_bundle


def test_three_d_fixture_flows_from_readonly_source_to_shadow_and_badcase() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    db = Session(engine, expire_on_commit=False)
    try:
        field_contract = create_field_demand_contract(
            db,
            contract_key="3d-search-consumption",
            category_key="model_3d_su",
            consumer_key="search",
            owner="tpeng-3d",
            fields=[
                {
                    "field_key": "style",
                    "source_path": "semantic.style",
                    "required": True,
                    "data_type": "string",
                    "target_roles": ["unified_dimension", "search_labels"],
                },
                {
                    "field_key": "quality_score",
                    "source_path": "quality.score",
                    "required": True,
                    "data_type": "number",
                    "target_roles": ["unified_dimension"],
                },
            ],
            thresholds={"accuracy": 0.9, "recall": 0.9},
            status="active",
            created_by="admin",
        )
        profile = EvaluationCategoryProfile(
            category_key="model_3d_su",
            display_name="3D 素材",
            status="active",
            allowed_mime_types_json='["image/png"]',
            preprocess_config_json='{"preprocess":"image"}',
            pipeline_config_json="{}",
            created_by="admin",
        )
        contract = build_inspiration_v3_contract()
        contract.update(
            {
                "profile_type": "3d-asset-quality-v1",
                "category_key": "model_3d_su",
                "field_demand_contract_id": field_contract.id,
                "source_schema_fingerprint": "f" * 64,
                "stage_fields": {
                    "a_quality_fields": ["quality.score"],
                    "b_aesthetic_fields": ["semantic.style"],
                },
            }
        )
        mechanism = CategoryEvaluationV3Config(
            category_key="model_3d_su",
            display_name="3D 素材",
            status="active",
            contract_json=json.dumps(contract, ensure_ascii=False),
            classification_map_json=json.dumps(
                build_inspiration_classification_map(), ensure_ascii=False
            ),
            subcategory_dimensions_json=json.dumps(
                build_inspiration_subcategory_dimensions(), ensure_ascii=False
            ),
            contract_hash=canonical_contract_hash(contract),
            created_by="admin",
        )
        asset = Asset(
            original_name="3d-room.png",
            stored_name="3d-room.png",
            mime_type="image/png",
            size_bytes=512,
            width=64,
            height=64,
            sha256="a" * 64,
            category_key="model_3d_su",
        )
        db.add_all([profile, mechanism, asset])
        db.flush()
        source_contract = create_upstream_source_contract(
            db,
            contract_key="3d-readonly-source",
            adapter_key="fixture-readonly",
            source_system="fixture-3d",
            category_key="model_3d_su",
            connection_locator="source-registry:3d-readonly",
            secret_reference="secret-ref:3d-readonly",
            field_mappings={
                "content_id": "content_id",
                "source_version": "source_version",
                "category_key": "category_key",
                "occurred_at": "occurred_at",
                "asset_id": "asset_id",
            },
            cursor_definition={"fields": ["content_id", "source_version"]},
            page_size=100,
            read_only=True,
            schema_fingerprint="f" * 64,
            owner="tpeng-3d",
            status="active",
            created_by="admin",
        )
        source_adapter = FixtureReadOnlySourceAdapter(
            read_only=True,
            schema_fingerprint="f" * 64,
            rows=[
                SourceRow(
                    content_id="1001",
                    source_version="v7",
                    category_key="model_3d_su",
                    occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
                    asset_id=asset.id,
                )
            ],
        )
        read_run = poll_upstream_source(
            db,
            contract=source_contract,
            adapter=source_adapter,
            limit=100,
            actor="admin",
        )
        assert read_run.status == "succeeded"
        assert read_run.package_count == 1

        model = ModelConfig(name="3d-model", model_id="3d-model-v1")
        prompt = PromptVersion(
            stage="A",
            name="3D A",
            version="3d-a-v1",
            system_prompt="system",
            user_prompt="user",
            rubric_version="3d-rubric-v1",
            status="published",
        )
        policy = SamplingPolicy(id=1, revision=1)
        db.add_all([model, prompt, policy])
        db.flush()
        strategy = get_or_create_bundle(
            db=db,
            model_config=model,
            prompt_a=prompt,
            prompt_b=None,
            rubric_version=prompt.rubric_version,
            engine_version="3d-engine-v1",
            risk_review_version=None,
            sampling_policy=policy,
        )
        job = EvaluationJob(
            asset_id=asset.id,
            category_key="model_3d_su",
            prompt_a_id=prompt.id,
            strategy_bundle_id=strategy.id,
            status="completed",
            stage="done",
            progress=100,
        )
        db.add(job)
        db.flush()
        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job.id,
            strategy_bundle_id=strategy.id,
            strategy_snapshot_json=build_evaluation_strategy_snapshot(
                db=db,
                bundle=strategy,
                prompt_a=prompt,
                prompt_b=None,
                sampling_policy=policy,
                aesthetic={"scoring_profile": "3d-asset-quality-v1"},
            ),
            precheck_json=json.dumps(
                {
                    "classification": {"scope_status": "in_scope", "primary_category": "住宅"},
                    "production_fields": {"style": "现代简约", "tags": ["客厅", "现代"]},
                    "image_quality": {"quality_severity": "normal"},
                    "media_form": {"rendering": {"status": "yes"}},
                },
                ensure_ascii=False,
            ),
            aesthetic_json=json.dumps({"dimensions": {"design_aesthetics": {"grade": 4}}}),
            scoring_json="{}",
            raw_response_a='{"candidate":"must-not-project"}',
            raw_response_b='{"raw":"must-not-project"}',
            score=91,
            level="L1",
            confidence=0.94,
            needs_review=False,
            review_stage="completed",
            model_id=model.model_id,
            prompt_a_version=prompt.version,
            rubric_version=prompt.rubric_version,
            engine_version="3d-engine-v1",
        )
        db.add(result)
        db.flush()
        review = HumanReview(
            evaluation_id=result.id,
            reviewer_name="admin",
            stage="initial",
            decision="corrected",
            corrected_level="L1",
            corrected_score=93,
            corrections_json="[]",
        )
        db.add(review)
        db.flush()
        db.add(
            ReviewPanel(
                evaluation_id=result.id,
                required_reviewers=1,
                status="completed",
                final_review_id=review.id,
                final_truth_json=json.dumps(
                    {
                        "decision": "corrected",
                        "corrected_level": "L1",
                        "corrected_score": 93,
                        "key_fields": {"production_fields.style": "现代"},
                    },
                    ensure_ascii=False,
                ),
                completed_at=datetime.now(timezone.utc),
            )
        )
        db.flush()
        tag_contract = TagDemandContract(
            contract_key="platform-semantic",
            version=1,
            status="active",
            definition_json="{}",
            contract_hash="c" * 64,
            created_by="admin",
        )
        db.add(tag_contract)
        db.flush()
        db.add(
            SemanticTagFact(
                asset_version_id=1,
                field_key="style",
                fact_version=1,
                field_status="required",
                values_json=canonical_json(
                    [{
                        "value": "现代",
                        "locale": "zh",
                        "rank": 1,
                        "source": "human",
                        "evidence_ref": "review:3d-1001",
                    }]
                ),
                evidence_json=canonical_json(["review:3d-1001"]),
                source_evaluation_id=result.id,
                source_review_id=review.id,
                contract_id=tag_contract.id,
                normalization_version="semantic-normalization-v1",
                mapping_version="semantic-mapping-v1",
                status="approved",
                payload_hash="d" * 64,
            )
        )
        db.flush()
        release, _ = create_release(
            db,
            release_key="3d-release-1001",
            evaluation_id=result.id,
            content_key="fixture-3d:1001",
            requested_by="admin",
        )
        published, _ = publish_release(db, release=release, actor="admin")
        published_payload = json.loads(published.label_payload_json)
        assert published_payload["provenance"]["asset_version_id"]
        assert published_payload["provenance"]["asset_sha256"] == asset.sha256
        assert published_payload["provenance"]["source_system"] == "fixture-3d"
        assert published_payload["provenance"]["source_content_id"] == "1001"
        assert published_payload["provenance"]["source_version"] == "v7"
        assert published_payload["semantic"]["style"]["values"][0]["value"] == "现代"
        assert published_payload["quality"]["score"] == 93

        shadow_batches: list[str] = []
        for role, table_name in (
            ("unified_dimension", "asset_dimension_shadow"),
            ("search_labels", "search_labels_shadow"),
        ):
            target = create_shadow_projection_target(
                db,
                target_key=f"3d-shadow-{role}",
                adapter_key="fixture-shadow",
                connection_locator=f"target-registry:3d-shadow-{role}",
                secret_reference=f"secret-ref:3d-shadow-{role}",
                schema_name="labellab_shadow",
                table_name=table_name,
                environment="shadow",
                shadow_only=True,
                owner="tpeng-3d",
                schema_fingerprint="e" * 64,
                status="active",
                created_by="admin",
            )
            projection = create_contract_version(
                db,
                contract_key=f"3d-shadow-{role}",
                target_role=role,
                table_name=table_name,
                environment="shadow",
                adapter_key="fixture-shadow",
                target_key=target.target_key,
                write_policy="shadow_only",
                category_key="model_3d_su",
                field_contract_id=field_contract.id,
                max_batch_size=500,
                primary_key=["content_key"],
                field_mappings={
                    "content_key": "content_key",
                    "category_key": "category_key",
                    "style": "semantic.style",
                    "quality_score": "quality.score",
                    "label_version": "$label.version",
                    "asset_version": "provenance.asset_version_id",
                    "mechanism_version": "provenance.strategy_bundle_id",
                    "model_version": "provenance.model_id",
                },
                input_versions={"label_schema_version": "published-label-v1"},
                mode="snapshot",
                idempotency_key_template="{table_name}:{content_key}:{label_version}",
                checkpoint={"kind": "published_label_id"},
                reconciliation={"checks": ["row_count", "payload_hash", "version"]},
                rollback={"strategy": "batch_delete"},
                owner="tpeng-3d",
                status="active",
                created_by="admin",
            )
            run = enqueue_shadow_projection_run(
                db,
                projection_contract=projection,
                field_contract=field_contract,
                target=target,
                max_rows=500,
                actor="admin",
            )
            adapter = FixtureShadowProjectionAdapter(
                shadow_only=True,
                least_privileged=True,
                schema_fingerprint=target.schema_fingerprint,
            )
            shadow_projection_worker_tick(
                db,
                f"worker-{role}",
                adapter_resolver=lambda _target, adapter=adapter: adapter,
            )
            assert run.status == "succeeded"
            assert run.expected_row_count == run.actual_row_count == 1
            assert run.expected_payload_hash == run.actual_payload_hash
            assert json.loads(run.checkpoint_json)["reconciled"] is True
            serialized_rows = json.dumps(adapter.rows, ensure_ascii=False).lower()
            assert "candidate" not in serialized_rows
            assert "raw_response" not in serialized_rows
            shadow_batches.append(run.batch_id)

        event, case, duplicate = ingest_production_feedback(
            db,
            event_id="3d-shadow-badcase-1001",
            schema_version="production-feedback-v1",
            event_type="human_correction_finalized",
            source_system="shadow-search-consumer",
            occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
            payload={
                "production_case_id": "shadow-case-1001",
                "category_key": "model_3d_su",
                "prompt_version": prompt.version,
                "severity": "P2",
                "model_output": {"style": "现代简约"},
                "human_truth": {"style": "现代"},
                "reason_codes": ["style_too_broad"],
                "projected_label_version": published.version,
                "shadow_batches": shadow_batches,
                "production_applied": False,
            },
            received_by="shadow-consumer",
        )
        db.commit()

        assert duplicate is False
        assert event.status == "mapped"
        assert case.source_type == "production_feedback"
        assert db.scalar(select(OptimizationCaseQueue).where(OptimizationCaseQueue.id == case.id)) is case
        case_payload = json.loads(case.case_json)
        assert case_payload["projected_label_version"] == published.version
        assert case_payload["shadow_batches"] == shadow_batches
        assert "query_embedding" not in json.dumps(case_payload).lower()
        assert "ranking_weight" not in json.dumps(case_payload).lower()
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("optional_evidence", "message"),
    [
        ({"projected_label_version": "1"}, "projected_label_version"),
        ({"projected_label_version": 0}, "projected_label_version"),
        ({"shadow_batches": "shadow-1"}, "shadow_batches"),
        ({"shadow_batches": [""]}, "shadow_batches"),
        ({"shadow_batches": [f"shadow-{index}" for index in range(21)]}, "shadow_batches"),
    ],
)
def test_production_feedback_validates_bounded_shadow_evidence(
    optional_evidence: dict[str, object],
    message: str,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        db.add(
            EvaluationCategoryProfile(
                category_key="model_3d_su",
                display_name="3D 素材",
                status="active",
                allowed_mime_types_json='["image/png"]',
                preprocess_config_json='{"preprocess":"image"}',
                pipeline_config_json="{}",
                created_by="admin",
            )
        )
        db.flush()
        with pytest.raises(ValueError, match=message):
            ingest_production_feedback(
                db,
                event_id="invalid-shadow-evidence",
                schema_version="production-feedback-v1",
                event_type="human_correction_finalized",
                source_system="shadow-search-consumer",
                occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
                payload={
                    "production_case_id": "shadow-case-invalid",
                    "category_key": "model_3d_su",
                    "prompt_version": "3d-a-v1",
                    "severity": "P2",
                    "model_output": {"style": "现代简约"},
                    "human_truth": {"style": "现代"},
                    **optional_evidence,
                },
                received_by="shadow-consumer",
            )
    finally:
        db.close()
        engine.dispose()
