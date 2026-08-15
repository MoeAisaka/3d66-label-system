from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    BaselineRegressionRun,
    BaselineSet,
    SemanticQualityMetricSnapshot,
    StrategyBundle,
    TagDemandContract,
)
from app.migrations import run_migrations
from app.semantic_tag_quality import (
    build_run_semantic_quality,
    compute_semantic_quality_metrics,
    persist_semantic_quality_snapshot,
)


def _review_stats() -> dict[str, int]:
    return {
        "corrected": 0,
        "reviewed": 1,
        "required": 1,
        "null_truth": 0,
        "null_correct": 0,
        "bilingual": 0,
        "bilingual_consistent": 0,
    }


def test_field_precision_and_recall_use_entity_ids() -> None:
    report = compute_semantic_quality_metrics(
        truth_by_asset={"asset-v1": {"style": {"style.modern", "style.minimal"}}},
        predicted_by_asset={"asset-v1": {"style": {"style.modern", "style.luxury"}}},
        mapping_stats={"style": {"candidate": 2, "mapped": 2, "unmapped": 0, "conflicted": 0, "evaluated": 1}},
        review_stats={"style": _review_stats()},
        reconciliation_stats={"expected": 1, "matched": 1},
    )
    assert report.fields["style"].precision == pytest.approx(0.5)
    assert report.fields["style"].recall == pytest.approx(0.5)
    assert report.fields["style"].mapping_coverage == pytest.approx(1.0)


def test_platform_macro_and_micro_metrics_are_both_reported() -> None:
    report = compute_semantic_quality_metrics(
        truth_by_asset={
            "a": {"style": {"style.modern"}, "material": {"material.wood"}},
            "b": {"style": {"style.minimal"}},
        },
        predicted_by_asset={
            "a": {"style": {"style.modern"}, "material": set()},
            "b": {"style": {"style.luxury"}},
        },
        mapping_stats={
            "style": {"candidate": 2, "mapped": 2, "unmapped": 0, "conflicted": 0, "evaluated": 2},
            "material": {"candidate": 1, "mapped": 1, "unmapped": 0, "conflicted": 0, "evaluated": 1},
        },
        review_stats={"style": _review_stats(), "material": _review_stats()},
        reconciliation_stats={"expected": 3, "matched": 2},
    )
    assert 0 <= report.macro_precision <= 1
    assert 0 <= report.micro_recall <= 1
    assert report.reconciliation_rate == pytest.approx(2 / 3)


def test_empty_denominator_serializes_as_none() -> None:
    report = compute_semantic_quality_metrics(
        truth_by_asset={},
        predicted_by_asset={},
        mapping_stats={"material": {"candidate": 0, "mapped": 0, "unmapped": 0, "conflicted": 0, "evaluated": 0}},
        review_stats={"material": _review_stats() | {"reviewed": 0, "required": 0}},
        reconciliation_stats={"expected": 0, "matched": 0},
    )
    assert report.fields["material"].precision is None
    assert report.fields["material"].recall is None
    assert report.fields["material"].mapping_coverage is None
    assert report.reconciliation_rate is None


def test_run_quality_uses_only_frozen_truth_and_result_evidence() -> None:
    run = SimpleNamespace(
        id=17,
        category_key="model_3d_su",
        execution_snapshot_json=json.dumps(
            {
                "semantic_truth_snapshot": {
                    "schema_version": "semantic-truth-snapshot-v1",
                    "assets": {
                        "11": {
                            "sample_set_id": 3,
                            "sample_item_id": 5,
                            "truth_revision": 2,
                            "truth": {
                                "semantic": {
                                    "style": {
                                        "status": "required",
                                        "values": [{"entity_id": "style.modern"}],
                                    }
                                }
                            },
                        }
                    },
                },
                "semantic_quality_context": {
                    "contract_id": 9,
                    "contract_key": "semantic-platform",
                    "contract_version": 4,
                    "contract_hash": "a" * 64,
                    "site_scope": "domestic",
                    "asset_scope": "unknown",
                },
            }
        ),
        items=[
            SimpleNamespace(
                asset_id=11,
                result_snapshot_json=json.dumps(
                    {
                        "stage_a": {
                            "semantic": {
                                "style": {
                                    "status": "required",
                                    "values": [{"entity_id": "style.modern"}],
                                }
                            },
                            "semantic_mapping_stats": {
                                "style": {
                                    "candidate": 1,
                                    "mapped": 1,
                                    "unmapped": 0,
                                    "conflicted": 0,
                                }
                            },
                            "semantic_review_stats": {
                                "style": {
                                    "corrected": 0,
                                    "reviewed": 1,
                                    "required": 1,
                                    "null_truth": 0,
                                    "null_correct": 0,
                                    "bilingual": 0,
                                    "bilingual_consistent": 0,
                                }
                            },
                            "semantic_reconciliation_stats": {
                                "expected": 1,
                                "matched": 1,
                            },
                        }
                    }
                ),
            )
        ],
    )

    report, evidence, context = build_run_semantic_quality(run)

    assert report.fields["style"].precision == pytest.approx(1.0)
    assert report.fields["style"].review_coverage == pytest.approx(1.0)
    assert report.reconciliation_rate == pytest.approx(1.0)
    assert evidence["truth_source"] == "frozen_run_snapshot"
    assert evidence["truth_revision_min"] == 2
    assert context["contract_id"] == 9


def test_quality_snapshot_is_append_only_and_idempotent() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine) as db:
        contract = TagDemandContract(
            contract_key="semantic-platform",
            version=1,
            status="active",
            definition_json="{}",
            contract_hash="a" * 64,
            created_by="test",
        )
        baseline_set = BaselineSet(
            name="semantic-quality-fixture",
            category_key="model_3d_su",
            default_expected_level="L1",
            fingerprint="b" * 64,
            created_by="test",
        )
        bundle = StrategyBundle(
            canonical_hash="c" * 64,
            strategy_schema_version="strategy-bundle-v1",
            model_id="fixture-model",
            model_config_snapshot="{}",
            prompt_a_version="prompt-a",
            prompt_b_version="prompt-b",
            rubric_version="rubric-v1",
            engine_version="engine-v1",
        )
        db.add_all([contract, baseline_set, bundle])
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
            total=1,
            completed=1,
            valid_predictions=1,
            metrics_json="{}",
            created_by="test",
        )
        db.add(run)
        db.flush()
        report = compute_semantic_quality_metrics(
            truth_by_asset={"a": {"style": {"style.modern"}}},
            predicted_by_asset={"a": {"style": {"style.modern"}}},
            mapping_stats={"style": {"candidate": 1, "mapped": 1, "unmapped": 0, "conflicted": 0, "evaluated": 1}},
            review_stats={"style": _review_stats()},
            reconciliation_stats={"expected": 1, "matched": 1},
        )
        rows = persist_semantic_quality_snapshot(
            db,
            baseline_run_id=run.id,
            contract_id=contract.id,
            category_key="model_3d_su",
            site_scope="domestic",
            asset_scope="whole",
            report=report,
        )
        db.commit()
        assert {row.field_key for row in rows} == {"style", "_aggregate"}
        again = persist_semantic_quality_snapshot(
            db,
            baseline_run_id=run.id,
            contract_id=contract.id,
            category_key="model_3d_su",
            site_scope="domestic",
            asset_scope="whole",
            report=report,
        )
        assert [row.id for row in again] == [row.id for row in rows]
        assert db.query(SemanticQualityMetricSnapshot).count() == 2
    engine.dispose()
