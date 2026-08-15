from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import SemanticQualityMetricSnapshot, TagDemandContract
from app.migrations import run_migrations
from app.semantic_tag_quality import compute_semantic_quality_metrics, persist_semantic_quality_snapshot


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
        db.add(contract)
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
            baseline_run_id=1,
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
            baseline_run_id=1,
            contract_id=contract.id,
            category_key="model_3d_su",
            site_scope="domestic",
            asset_scope="whole",
            report=report,
        )
        assert [row.id for row in again] == [row.id for row in rows]
        assert db.query(SemanticQualityMetricSnapshot).count() == 2
    engine.dispose()
