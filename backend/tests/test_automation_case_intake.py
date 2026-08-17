from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def _db():
    from app.database import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _lane(db: Session):
    from app.models import AutomationLanePolicy

    lane = AutomationLanePolicy(
        category_key="space_image",
        pipeline_kind="baseline",
        generation=3,
        mechanism_fingerprint="a" * 64,
        mechanism_snapshot_json='{"model":"m1"}',
    )
    db.add(lane)
    db.flush()
    return lane


def _evidence(*, reason: str, evidence: list[dict[str, object]]):
    from app.automation_case_intake import FinalCorrectionEvidence

    node = {
        "source": "human",
        "node_type": "dimension_rule",
        "node_path": "dimensions.balance",
        "old_value": 4,
        "new_value": 3,
        "reason": reason,
        "evidence": evidence,
    }
    return FinalCorrectionEvidence(
        category_key="space_image",
        pipeline_kind="baseline",
        evaluation_id=10,
        final_review_id=20,
        correction_revision=3,
        node_corrections=(node,),
        human_reviews=(
            {
                "review_id": 20,
                "is_final": True,
                "decision": "corrected",
                "reviewer_name": "ops",
                "corrections": [node],
            },
        ),
        mechanism_snapshot={"model": "m1"},
        mechanism_fingerprint="a" * 64,
    )


def test_final_correction_requires_reason_and_evidence():
    from app.automation_case_intake import qualify_correction

    qualified, blockers = qualify_correction(_evidence(reason="", evidence=[]))

    assert qualified is False
    assert set(blockers) >= {"reason_missing", "evidence_missing"}


def test_same_final_review_revision_is_idempotent(db=None):
    from app.automation_case_intake import admit_final_correction
    from app.models import OptimizationCaseEligibilitySnapshot

    engine, session = _db()
    try:
        lane = _lane(session)
        evidence = _evidence(
            reason="构图平衡被低估",
            evidence=[{"path": "dimensions.balance", "value": "主体偏移"}],
        )
        first = admit_final_correction(
            session,
            evidence=evidence,
            lane=lane,
        )
        second = admit_final_correction(
            session,
            evidence=evidence,
            lane=lane,
        )
        session.commit()

        assert first.id == second.id
        assert session.scalar(select(OptimizationCaseEligibilitySnapshot.id)) == first.id
    finally:
        session.close()
        engine.dispose()


def _evaluation_and_review(*, with_node_evidence: bool):
    node = {
        "source": "human",
        "node_type": "call_a_field",
        "node_path": "call_a.production_fields.title",
        "old_value": "旧标题",
        "new_value": "人工确认标题",
        "reason": "调用A字段判断错误",
        "evidence": ([{"path": "asset.ocr", "value": "现代住宅"}]
                     if with_node_evidence else []),
    }
    evaluation = SimpleNamespace(
        id=10,
        review_revision=4,
        prompt_a_version="A-v1",
        prompt_b_version="B-v1",
        strategy_snapshot_json=json.dumps({"strategy": "frozen-v1"}),
        correction_history_json=json.dumps([node], ensure_ascii=False),
        job=SimpleNamespace(
            category_key="space_image",
            baseline_regression_item_id=None,
        ),
    )
    review = SimpleNamespace(
        id=20,
        reviewer_name="运营",
        stage="initial",
        decision="corrected",
        corrected_level="L2",
        corrected_score=82,
        note="已完成人工最终审核",
        corrections_json=json.dumps([], ensure_ascii=False),
        created_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    return evaluation, review


def test_build_final_correction_evidence_freezes_context_and_mechanism():
    from app.automation_case_intake import build_final_correction_evidence

    evaluation, review = _evaluation_and_review(with_node_evidence=True)
    evidence = build_final_correction_evidence(
        evaluation=evaluation,
        final_review=review,
        mechanism_snapshot={"route_key": "A", "prompt_b_version": "B-v1"},
        mechanism_fingerprint="a" * 64,
    )

    assert evidence.category_key == "space_image"
    assert evidence.pipeline_kind == "incremental"
    assert evidence.evaluation_id == 10
    assert evidence.final_review_id == 20
    assert evidence.correction_revision == 4
    assert evidence.node_corrections[0]["evidence"]
    assert evidence.human_reviews[0]["is_final"] is True
    assert evidence.mechanism_snapshot["route_key"] == "A"


def test_final_review_without_evidence_waits_and_is_not_dispatchable():
    from app.automation_case_intake import on_final_review_completed
    from app.automation_lanes import case_is_dispatchable
    from app.models import OptimizationCaseQueue

    engine, session = _db()
    try:
        lane = _lane(session)
        lane.pipeline_kind = "incremental"
        lane.status = "enabled"
        evaluation, review = _evaluation_and_review(with_node_evidence=False)
        result = on_final_review_completed(
            session,
            evaluation=evaluation,
            final_review=review,
            mechanism_snapshot={"route_key": "A", "prompt_b_version": "B-v1"},
            mechanism_fingerprint="a" * 64,
        )
        session.commit()

        case = session.get(OptimizationCaseQueue, result["case_id"])
        assert result["status"] == "awaiting_evidence"
        assert "evidence_missing" in result["blockers"]
        assert case is not None
        assert case.admission_state == "awaiting_evidence"
        assert case_is_dispatchable(case) is False
    finally:
        session.close()
        engine.dispose()


def test_final_review_with_human_evidence_is_admitted_idempotently():
    from app.automation_case_intake import on_final_review_completed
    from app.models import OptimizationCaseEligibilitySnapshot, OptimizationCaseQueue

    engine, session = _db()
    try:
        lane = _lane(session)
        lane.pipeline_kind = "incremental"
        lane.status = "enabled"
        evaluation, review = _evaluation_and_review(with_node_evidence=True)
        first = on_final_review_completed(
            session,
            evaluation=evaluation,
            final_review=review,
            mechanism_snapshot={"route_key": "A", "prompt_b_version": "B-v1"},
            mechanism_fingerprint="a" * 64,
        )
        second = on_final_review_completed(
            session,
            evaluation=evaluation,
            final_review=review,
            mechanism_snapshot={"route_key": "A", "prompt_b_version": "B-v1"},
            mechanism_fingerprint="a" * 64,
        )
        session.commit()

        assert first["status"] == second["status"] == "eligible"
        assert first["snapshot_id"] == second["snapshot_id"]
        assert session.query(OptimizationCaseQueue).count() == 1
        assert session.query(OptimizationCaseEligibilitySnapshot).count() == 1
    finally:
        session.close()
        engine.dispose()


def test_waiting_case_is_promoted_when_evidence_arrives_later():
    from app.automation_case_intake import on_final_review_completed
    from app.models import OptimizationCaseQueue

    engine, session = _db()
    try:
        lane = _lane(session)
        lane.pipeline_kind = "incremental"
        lane.status = "enabled"
        evaluation, review = _evaluation_and_review(with_node_evidence=False)
        first = on_final_review_completed(
            session,
            evaluation=evaluation,
            final_review=review,
            mechanism_snapshot={"route_key": "A", "prompt_b_version": "B-v1"},
            mechanism_fingerprint="a" * 64,
        )
        evaluation.correction_history_json = json.dumps(
            [
                {
                    "source": "human",
                    "node_type": "call_a_field",
                    "reason": "补充人工依据",
                    "evidence": [{"path": "asset.ocr", "value": "住宅"}],
                }
            ],
            ensure_ascii=False,
        )
        second = on_final_review_completed(
            session,
            evaluation=evaluation,
            final_review=review,
            mechanism_snapshot={"route_key": "A", "prompt_b_version": "B-v1"},
            mechanism_fingerprint="a" * 64,
        )
        session.commit()

        case = session.get(OptimizationCaseQueue, first["case_id"])
        assert first["status"] == "awaiting_evidence"
        assert second["status"] == "eligible"
        assert second["case_id"] == first["case_id"]
        assert case is not None
        assert case.admission_state == "eligible"
    finally:
        session.close()
        engine.dispose()
