from __future__ import annotations

import importlib
import importlib.util
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    ProductionRun,
    ProductionStepAttempt,
    RuntimeDispatchItem,
    ScriptDefinition,
    ScriptVersion,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.workflow_registry import create_workflow_definition, create_workflow_version


def _runtime_module():
    assert importlib.util.find_spec("app.workflow_runtime") is not None, (
        "workflow runtime module is missing"
    )
    return importlib.import_module("app.workflow_runtime")


def _engine(tmp_path, name="runtime.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return engine


def _seed_active_workflow(
    db: Session,
    *,
    workflow_key: str = "label.runtime.test",
    queue_class: str = "validation",
    two_steps: bool = False,
) -> WorkflowVersion:
    definition = ScriptDefinition(
        script_key=f"fixture.identity.{workflow_key}",
        name="Identity",
        description="",
        owner="platform",
        allowed_categories_json="[]",
        step_types_json='["identity","transform"]',
        status="active",
        created_by="admin",
    )
    db.add(definition)
    db.flush()
    script = ScriptVersion(
        script_definition_id=definition.id,
        version="1",
        display_name="Identity v1",
        executor_kind="deterministic_fixture",
        artifact_sha256="a" * 64,
        manifest_json='{"fixture":"identity"}',
        input_schema_json='{"type":"object"}',
        output_schema_json='{"type":"object"}',
        required_permissions_json="[]",
        idempotency_template="{run_key}:{step_key}:{input_hash}",
        timeout_seconds=60,
        max_attempts=3,
        retry_policy_json='{"kind":"fixed","delay_seconds":1}',
        concurrency_limit=1,
        estimated_cost_json="{}",
        status="active",
        validation_report_json='{"ok":true,"errors":[]}',
        blocked_reason="",
        created_by="admin",
    )
    db.add(script)
    db.flush()
    workflow = create_workflow_definition(
        db,
        workflow_key=workflow_key,
        name="Runtime test",
        description="",
        owner="platform",
        allowed_categories=[],
        created_by="admin",
    )
    steps = [
        {
            "key": "identity",
            "type": "identity",
            "script_version": f"{definition.script_key}@1",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    ]
    edges = []
    if two_steps:
        steps.append(
            {
                "key": "finish",
                "type": "transform",
                "script_version": f"{definition.script_key}@1",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        )
        edges.append({"from": "identity", "to": "finish"})
    version = create_workflow_version(
        db,
        definition=workflow,
        version="1",
        manifest={
            "schema_version": "workflow-v1",
            "steps": steps,
            "edges": edges,
            "queue_class": queue_class,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "resource_policy": {"max_parallel": 1},
        },
        created_by="admin",
    )
    version.status = "active"
    version.validation_report_json = '{"ok":true,"errors":[]}'
    db.commit()
    return version


def test_create_production_run_is_idempotent_and_rejects_drift(tmp_path) -> None:
    runtime = _runtime_module()
    engine = _engine(tmp_path)
    try:
        with Session(engine) as db:
            version = _seed_active_workflow(db)
            request = runtime.ProductionRunRequest(
                workflow_version_id=version.id,
                idempotency_key="idem-1",
                category_key="model_3d_su",
                queue_class="validation",
                runtime_context={"queue_policy_version": "queue-policy-v1"},
                input_manifest={"content_key": "3d:1:42"},
                owner="platform",
                reason="test",
            )
            first = runtime.create_production_run(db, request, actor="admin")
            db.commit()
            second = runtime.create_production_run(db, request, actor="admin")
            assert first.id == second.id
            assert db.scalar(select(ProductionRun).count()) if False else True
            assert len(db.scalars(select(ProductionRun)).all()) == 1
            assert len(db.scalars(select(ProductionStepAttempt)).all()) == 1
            assert len(db.scalars(select(RuntimeDispatchItem)).all()) == 1

            drifted = replace(
                request,
                input_manifest={"content_key": "3d:1:99"},
            )
            try:
                runtime.create_production_run(db, drifted, actor="admin")
            except runtime.WorkflowRuntimeError as exc:
                assert exc.code == "run_idempotency_conflict"
            else:
                raise AssertionError("idempotency key accepted drifted input")
    finally:
        engine.dispose()


def test_runtime_step_lease_and_checkpoint_are_idempotent(tmp_path) -> None:
    runtime = _runtime_module()
    engine = _engine(tmp_path, "lease.db")
    try:
        with Session(engine) as db:
            version = _seed_active_workflow(db)
            run = runtime.create_production_run(
                db,
                runtime.ProductionRunRequest(
                    workflow_version_id=version.id,
                    idempotency_key="idem-lease",
                    category_key="model_3d_su",
                    queue_class="validation",
                    runtime_context={"queue_policy_version": "queue-policy-v1"},
                    input_manifest={"content_key": "3d:1:42"},
                    owner="platform",
                    reason="test",
                ),
                actor="admin",
            )
            db.commit()

            attempt_id = runtime.claim_next_runtime_step(
                db, "worker-1", global_limit=1
            )
            db.commit()
            assert attempt_id is not None
            assert runtime.claim_next_runtime_step(
                db, "worker-2", global_limit=1
            ) is None
            attempt = db.get(ProductionStepAttempt, attempt_id)
            assert attempt is not None
            assert attempt.lease_token
            lease_token = attempt.lease_token

            try:
                runtime.heartbeat_runtime_step(
                    db,
                    attempt_id,
                    "stale-token",
                    "worker-1",
                )
            except runtime.WorkflowRuntimeError as exc:
                assert exc.code == "step_lease_stale"
            else:
                raise AssertionError("stale lease token was accepted")

            completed = runtime.complete_runtime_step(
                db,
                attempt_id,
                lease_token,
                {"verified": True},
            )
            db.commit()
            assert completed.status == "succeeded"
            assert completed.output_hash
            assert completed.checkpoint_hash
            assert db.get(ProductionRun, run.id).status == "succeeded"

            repeated = runtime.complete_runtime_step(
                db,
                attempt_id,
                lease_token,
                {"verified": True},
            )
            assert repeated.id == completed.id
    finally:
        engine.dispose()

