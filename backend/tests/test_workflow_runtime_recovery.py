from __future__ import annotations

import importlib
import importlib.util
import asyncio
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    ProductionRun,
    ProductionStepAttempt,
    QueueSchedulerState,
    ScriptVersion,
)
from app.workflow_runtime import ProductionRunRequest, create_production_run
from tests.test_workflow_runtime import _seed_active_workflow
from app import worker


def _fixture_module():
    assert importlib.util.find_spec("app.workflow_fixture_executor") is not None
    return importlib.import_module("app.workflow_fixture_executor")


def test_process_runtime_step_once_retries_fixture_failure(monkeypatch, tmp_path) -> None:
    fixture = _fixture_module()
    engine = create_engine(f"sqlite:///{tmp_path / 'fixture-runtime.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            version = _seed_active_workflow(db)
            script = db.scalar(select(ScriptVersion))
            assert script is not None
            script.manifest_json = json.dumps({"fixture": "fail_once"})
            db.commit()
            run = create_production_run(
                db,
                ProductionRunRequest(
                    workflow_version_id=version.id,
                    idempotency_key="fixture-run",
                    category_key="model_3d_su",
                    queue_class="validation",
                    runtime_context={"queue_policy_version": "queue-policy-v1"},
                    input_manifest={"content_key": "3d:1:42"},
                    owner="platform",
                    reason="fixture",
                ),
                actor="admin",
            )
            run_id = run.id
            db.commit()

        monkeypatch.setattr(fixture, "SessionLocal", lambda: Session(engine))
        assert fixture.process_runtime_step_once("fixture-worker") is True

        with Session(engine) as db:
            attempts = db.scalars(
                select(ProductionStepAttempt)
                    .where(ProductionStepAttempt.run_id == run_id)
                .order_by(ProductionStepAttempt.attempt_no)
            ).all()
            assert [attempt.status for attempt in attempts] == [
                "failed",
                "retryable",
            ]
            assert db.get(ProductionRun, run_id).status == "retryable"

        assert fixture.process_runtime_step_once("fixture-worker") is True

        with Session(engine) as db:
            attempts = db.scalars(
                select(ProductionStepAttempt)
                    .where(ProductionStepAttempt.run_id == run_id)
                .order_by(ProductionStepAttempt.attempt_no)
            ).all()
            assert attempts[-1].status == "succeeded"
            assert db.get(ProductionRun, run_id).status == "succeeded"
    finally:
        engine.dispose()


def test_worker_process_one_prioritizes_runtime_step(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        worker,
        "recover_runtime_once",
        lambda: calls.append("recover") or 0,
        raising=False,
    )
    monkeypatch.setattr(
        worker,
        "process_workflow_runtime_step_once",
        lambda _worker_id: calls.append("runtime") or True,
        raising=False,
    )
    monkeypatch.setattr(
        worker,
        "claim_next_job",
        lambda: (_ for _ in ()).throw(AssertionError("legacy job claimed")),
    )

    assert asyncio.run(worker.process_one()) is True
    assert calls == ["recover", "runtime"]


def test_runtime_worker_reuses_persisted_global_limit(monkeypatch, tmp_path) -> None:
    fixture = _fixture_module()
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-limit.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(
                QueueSchedulerState(
                    id=1,
                    policy_version="queue-policy-v1",
                    global_limit=4,
                )
            )
            db.commit()
        captured: dict[str, int] = {}
        monkeypatch.setattr(fixture, "SessionLocal", lambda: Session(engine))
        monkeypatch.setattr(
            fixture,
            "claim_next_runtime_step",
            lambda _db, _worker_id, *, global_limit: captured.update(
                global_limit=global_limit
            ) or None,
        )

        assert fixture.process_runtime_step_once("fixture-worker") is False
        assert captured["global_limit"] == 4
    finally:
        engine.dispose()
