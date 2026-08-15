from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    ProductionRun,
    ProductionStepAttempt,
    QueueSchedulerState,
    RuntimeDispatchItem,
)
from tests.test_workflow_runtime import _seed_active_workflow


def _runtime_module():
    assert importlib.util.find_spec("app.workflow_runtime") is not None
    return importlib.import_module("app.workflow_runtime")


def test_runtime_dispatch_persists_existing_scheduler_state(tmp_path) -> None:
    runtime = _runtime_module()
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            first_version = _seed_active_workflow(
                db,
                workflow_key="label.runtime.validation",
                queue_class="validation",
            )
            second_version = _seed_active_workflow(
                db,
                workflow_key="label.runtime.interactive",
                queue_class="interactive",
            )
            for version, key, queue in (
                (first_version, "idem-validation", "validation"),
                (second_version, "idem-interactive", "interactive"),
            ):
                runtime.create_production_run(
                    db,
                    runtime.ProductionRunRequest(
                        workflow_version_id=version.id,
                        idempotency_key=key,
                        category_key="model_3d_su",
                        queue_class=queue,
                        runtime_context={
                            "queue_policy_version": "queue-policy-v1"
                        },
                        input_manifest={"content_key": key},
                        owner="platform",
                        reason="scheduler",
                    ),
                    actor="admin",
                )
            db.commit()

            assert runtime.claim_next_runtime_step(
                db, "worker-1", global_limit=2
            ) is not None
            db.commit()
            assert runtime.claim_next_runtime_step(
                db, "worker-2", global_limit=2
            ) is not None
            db.commit()
            state = db.get(QueueSchedulerState, 1)
            assert state is not None
            assert state.dispatch_count == 2
            assert state.policy_version == "queue-policy-v1"
    finally:
        engine.dispose()


def test_expired_runtime_lease_moves_to_recovery_attempt(tmp_path) -> None:
    runtime = _runtime_module()
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            version = _seed_active_workflow(db)
            run = runtime.create_production_run(
                db,
                runtime.ProductionRunRequest(
                    workflow_version_id=version.id,
                    idempotency_key="idem-recovery",
                    category_key="model_3d_su",
                    queue_class="validation",
                    runtime_context={"queue_policy_version": "queue-policy-v1"},
                    input_manifest={"content_key": "recovery"},
                    owner="platform",
                    reason="recovery",
                ),
                actor="admin",
            )
            db.commit()
            first_dispatch = db.scalar(select(RuntimeDispatchItem))
            assert first_dispatch is not None
            claim_time = first_dispatch.available_at
            attempt_id = runtime.claim_next_runtime_step(
                db,
                "worker-dead",
                global_limit=1,
                lease_seconds=30,
                now=claim_time,
            )
            db.commit()
            assert attempt_id is not None

            recovered = runtime.recover_expired_runtime_steps(
                db,
                claim_time + timedelta(seconds=60),
            )
            db.commit()
            assert recovered == 1
            attempts = db.scalars(
                select(ProductionStepAttempt)
                .where(ProductionStepAttempt.run_id == run.id)
                .order_by(ProductionStepAttempt.attempt_no)
            ).all()
            assert [item.status for item in attempts] == ["failed", "retryable"]
            assert attempts[1].attempt_no == 2
            dispatch = db.scalar(
                select(RuntimeDispatchItem).where(
                    RuntimeDispatchItem.step_attempt_id == attempts[1].id
                )
            )
            assert dispatch is not None
            assert dispatch.queue_class == "recovery"
            assert db.get(ProductionRun, run.id).status == "retryable"
    finally:
        engine.dispose()
