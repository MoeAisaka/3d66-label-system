from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ProductionRun, ProductionStepAttempt
from app.three_d_workflow_fixture import (
    ThreeDDryRunGateError,
    approve_three_d_dry_run_gate,
    build_three_d_dry_run_manifest,
    run_three_d_dry_run,
    start_three_d_dry_run,
)


def test_three_d_manifest_keeps_two_human_gates_and_shadow_boundaries() -> None:
    manifest = build_three_d_dry_run_manifest()
    assert manifest["schema_version"] == "workflow-v1"
    assert manifest["category_key"] == "model_3d_su"
    assert [step["key"] for step in manifest["steps"]] == [
        "source_ingress",
        "evaluate_and_label",
        "human_correction_gate",
        "label_fact_gate",
        "shadow_projection",
        "projection_reconcile",
        "badcase_feedback",
    ]
    assert manifest["human_gates"] == {
        "human_correction_gate": {"axis": "mechanism", "required": True},
        "label_fact_gate": {"axis": "label_fact", "required": True},
    }
    assert manifest["environment"] == "dry_run"
    assert manifest["non_goals"] == [
        "real_upstream",
        "real_model_call",
        "formal_publish",
        "stock_overwrite",
    ]


def test_three_d_dry_run_pauses_at_each_human_gate_and_requires_ordered_approval() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            run = start_three_d_dry_run(
                db,
                idempotency_key="3d-su-fixture-human-gates",
                actor="fixture-starter",
            )

            assert run.status == "paused"
            assert run.current_step_key == "human_correction_gate"
            assert json.loads(run.blockers_json) == [
                {
                    "code": "human_gate_approval_required",
                    "gate_key": "human_correction_gate",
                    "axis": "mechanism",
                }
            ]
            with pytest.raises(ThreeDDryRunGateError, match="当前等待人工纠偏门"):
                approve_three_d_dry_run_gate(
                    db,
                    run_id=run.id,
                    gate_key="label_fact_gate",
                    actor="fixture-publisher",
                )

            run = approve_three_d_dry_run_gate(
                db,
                run_id=run.id,
                gate_key="human_correction_gate",
                actor="fixture-reviewer",
            )
            assert run.status == "paused"
            assert run.current_step_key == "label_fact_gate"
            assert json.loads(run.blockers_json) == [
                {
                    "code": "human_gate_approval_required",
                    "gate_key": "label_fact_gate",
                    "axis": "label_fact",
                }
            ]

            run = approve_three_d_dry_run_gate(
                db,
                run_id=run.id,
                gate_key="label_fact_gate",
                actor="fixture-publisher",
            )
            assert run.status == "succeeded"

            attempts = db.scalars(
                select(ProductionStepAttempt)
                .where(ProductionStepAttempt.run_id == run.id)
                .order_by(ProductionStepAttempt.sequence, ProductionStepAttempt.attempt_no)
            ).all()
            gate_outputs = {
                row.step_key: json.loads(row.output_manifest_json)
                for row in attempts
                if row.step_key in {"human_correction_gate", "label_fact_gate"}
            }
            assert gate_outputs == {
                "human_correction_gate": {
                    "gate_key": "human_correction_gate",
                    "axis": "mechanism",
                    "decision": "approved",
                    "actor": "fixture-reviewer",
                },
                "label_fact_gate": {
                    "gate_key": "label_fact_gate",
                    "axis": "label_fact",
                    "decision": "approved",
                    "actor": "fixture-publisher",
                },
            }
    finally:
        engine.dispose()


def test_three_d_dry_run_is_idempotent_and_recovers_projection_failure() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            first = run_three_d_dry_run(
                db,
                idempotency_key="3d-su-fixture-001",
                actor="fixture",
            )
            db.commit()
            duplicate = run_three_d_dry_run(
                db,
                idempotency_key="3d-su-fixture-001",
                actor="fixture",
            )
            db.commit()

            assert duplicate.run_id == first.run_id
            assert first.status == "succeeded"
            assert first.category_key == "model_3d_su"
            assert first.human_correction_gate == "approved"
            assert first.label_fact_gate == "approved"
            assert first.projection_reconciliation == {
                "status": "matched",
                "row_count": 1,
                "payload_hash_verified": True,
            }
            assert first.feedback_case_key.startswith("badcase:model_3d_su:")
            assert first.recovery_evidence["failed_step"] == "shadow_projection"
            assert first.recovery_evidence["retry_attempt"] == 2
            assert first.recovery_evidence["checkpoint_hash"]
            assert len(first.ordered_steps) == 7

            assert db.scalar(select(func.count(ProductionRun.id))) == 1
            attempts = db.scalars(
                select(ProductionStepAttempt)
                .where(ProductionStepAttempt.run_id == first.run_id)
                .order_by(ProductionStepAttempt.sequence, ProductionStepAttempt.attempt_no)
            ).all()
            assert [(row.step_key, row.attempt_no, row.status) for row in attempts] == [
                ("source_ingress", 1, "succeeded"),
                ("evaluate_and_label", 1, "succeeded"),
                ("human_correction_gate", 1, "succeeded"),
                ("label_fact_gate", 1, "succeeded"),
                ("shadow_projection", 1, "failed"),
                ("shadow_projection", 2, "succeeded"),
                ("projection_reconcile", 1, "succeeded"),
                ("badcase_feedback", 1, "succeeded"),
            ]
            assert all(row.checkpoint_hash for row in attempts if row.status == "succeeded")
            assert "candidate" not in json.dumps(first.as_dict(), ensure_ascii=False).lower()
    finally:
        engine.dispose()
