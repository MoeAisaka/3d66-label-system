from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import (
    EvaluationJob,
    LabelRelease,
    ProductionRun,
    ProductionStepAttempt,
    QueueSchedulerState,
    RuntimeDispatchItem,
    ScriptDefinition,
    ScriptVersion,
)
from app.workflow_fixture_executor import process_runtime_step_once
from app.workflow_registry import create_workflow_definition, create_workflow_version
from app.workflow_runtime import ProductionRunRequest, create_production_run


def _script(
    db: Session,
    *,
    key: str,
    step_type: str,
    fixture: str,
    artifact: str,
) -> ScriptVersion:
    definition = ScriptDefinition(
        script_key=key,
        name=key,
        description="e2e fixture",
        owner="platform",
        allowed_categories_json='["model_3d_su"]',
        step_types_json=f'["{step_type}"]',
        status="active",
        created_by="e2e",
    )
    db.add(definition)
    db.flush()
    version = ScriptVersion(
        script_definition_id=definition.id,
        version="1",
        display_name=key + " v1",
        executor_kind="deterministic_fixture",
        artifact_sha256=artifact * 64,
        manifest_json='{"fixture":"' + fixture + '"}',
        input_schema_json='{"type":"object"}',
        output_schema_json='{"type":"object"}',
        required_permissions_json="[]",
        idempotency_template="{run_key}:{step_key}:{input_hash}",
        timeout_seconds=60,
        max_attempts=3,
        retry_policy_json='{"kind":"fixed","delay_seconds":0}',
        concurrency_limit=1,
        estimated_cost_json='{"currency":"CNY","micros":0}',
        status="active",
        validation_report_json='{"ok":true,"errors":[]}',
        blocked_reason="",
        created_by="e2e",
    )
    db.add(version)
    db.flush()
    return version


def test_workflow_runtime_two_step_retry_dry_run(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-runtime-e2e.db'}")
    Base.metadata.create_all(engine)
    SessionForTest = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with SessionForTest() as db:
            identity = _script(
                db,
                key="fixture.e2e.identity",
                step_type="identity",
                fixture="identity",
                artifact="a",
            )
            fail_once = _script(
                db,
                key="fixture.e2e.fail-once",
                step_type="transform",
                fixture="fail_once",
                artifact="b",
            )
            definition = create_workflow_definition(
                db,
                workflow_key="label.e2e.retry",
                name="E2E retry",
                description="deterministic dry-run",
                owner="platform",
                allowed_categories=["model_3d_su"],
                created_by="e2e",
            )
            workflow = create_workflow_version(
                db,
                definition=definition,
                version="1",
                manifest={
                    "schema_version": "workflow-v1",
                    "steps": [
                        {
                            "key": "identity",
                            "type": "identity",
                            "script_version": "fixture.e2e.identity@1",
                            "input_schema": {"type": "object"},
                            "output_schema": {"type": "object"},
                        },
                        {
                            "key": "retry-proof",
                            "type": "transform",
                            "script_version": "fixture.e2e.fail-once@1",
                            "input_schema": {"type": "object"},
                            "output_schema": {"type": "object"},
                        },
                    ],
                    "edges": [{"from": "identity", "to": "retry-proof"}],
                    "queue_class": "validation",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "resource_policy": {"max_parallel": 1},
                },
                created_by="e2e",
            )
            workflow.status = "active"
            workflow.validation_report_json = '{"ok":true,"errors":[]}'
            db.commit()

            request = ProductionRunRequest(
                workflow_version_id=workflow.id,
                idempotency_key="runtime-e2e-1",
                category_key="model_3d_su",
                queue_class="validation",
                runtime_context={"queue_policy_version": "queue-policy-v1"},
                input_manifest={"content_key": "3d:1:42"},
                owner="platform",
                reason="e2e",
            )
            run = create_production_run(db, request, actor="e2e")
            db.commit()
            duplicate = create_production_run(db, request, actor="e2e")
            assert duplicate.id == run.id
            assert db.scalar(select(func.count(ProductionRun.id))) == 1
            run_id = run.id

        import app.workflow_fixture_executor as executor

        monkeypatch.setattr(executor, "SessionLocal", SessionForTest)
        assert process_runtime_step_once("e2e-worker") is True
        assert process_runtime_step_once("e2e-worker") is True
        assert process_runtime_step_once("e2e-worker") is True
        assert process_runtime_step_once("e2e-worker") is False

        with SessionForTest() as db:
            persisted = db.get(ProductionRun, run_id)
            assert persisted is not None
            assert persisted.status == "succeeded"
            assert persisted.completed_steps == 2
            attempts = db.scalars(
                select(ProductionStepAttempt)
                .where(ProductionStepAttempt.run_id == run_id)
                .order_by(
                    ProductionStepAttempt.sequence,
                    ProductionStepAttempt.attempt_no,
                )
            ).all()
            assert [
                (item.step_key, item.attempt_no, item.status)
                for item in attempts
            ] == [
                ("identity", 1, "succeeded"),
                ("retry-proof", 1, "failed"),
                ("retry-proof", 2, "succeeded"),
            ]
            assert sum(item.checkpoint_hash is not None for item in attempts) == 2
            assert db.scalar(select(func.count(EvaluationJob.id))) == 0
            assert db.scalar(select(func.count(LabelRelease.id))) == 0
            queue_classes = set(
                db.scalars(select(RuntimeDispatchItem.queue_class)).all()
            )
            assert queue_classes == {"validation", "recovery"}
            scheduler = db.get(QueueSchedulerState, 1)
            assert scheduler is not None
            assert scheduler.dispatch_count == 3
            assert identity.id != fail_once.id
    finally:
        engine.dispose()

