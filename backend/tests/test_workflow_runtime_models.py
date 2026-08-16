from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app import models
from app.database import Base


RUNTIME_MODELS = (
    "ScriptDefinition",
    "ScriptVersion",
    "WorkflowDefinition",
    "WorkflowVersion",
    "ProductionRun",
    "ProductionStepAttempt",
    "RuntimeDispatchItem",
    "RuntimeAuditEvent",
)


def test_runtime_models_are_registered_with_expected_tables(tmp_path) -> None:
    for name in RUNTIME_MODELS:
        assert hasattr(models, name), f"missing ORM model {name}"

    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-models.db'}")
    try:
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        assert {
            "script_definitions",
            "script_versions",
            "workflow_definitions",
            "workflow_versions",
            "production_runs",
            "production_step_attempts",
            "runtime_dispatch_items",
            "runtime_audit_events",
        } <= tables
    finally:
        engine.dispose()


def test_runtime_model_constraints_reject_invalid_hash_and_queue(tmp_path) -> None:
    assert hasattr(models, "ScriptDefinition")
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-constraints.db'}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO script_definitions "
                "(id, script_key, name, description, owner, allowed_categories_json, "
                "step_types_json, status, created_by, created_at, updated_at) "
                "VALUES (1, 'fixture.identity', 'Identity', '', 'platform', '[]', "
                "'[\"identity\"]', 'active', 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO script_versions "
                    "(script_definition_id, version, display_name, executor_kind, "
                    "artifact_sha256, manifest_json, input_schema_json, output_schema_json, "
                    "required_permissions_json, idempotency_template, timeout_seconds, "
                    "max_attempts, retry_policy_json, concurrency_limit, estimated_cost_json, "
                    "status, validation_report_json, blocked_reason, created_by, created_at, updated_at) "
                    "VALUES (1, '1', 'Identity v1', 'deterministic_fixture', 'bad', '{}', "
                    "'{}', '{}', '[]', '{run_key}:{step_key}', 60, 1, '{}', 1, '{}', "
                    "'draft', '{}', '', 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )

            with pytest.raises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO production_runs "
                    "(run_key, idempotency_key, workflow_definition_id, workflow_version_id, "
                    "snapshot_json, snapshot_hash, queue_class, status, blockers_json, "
                    "requested_by, owner, reason, environment, total_steps, completed_steps, "
                    "failed_steps, attempt_count, error_code, error_message, created_at, updated_at) "
                    "VALUES ('run-1', 'idem-1', 1, 1, '{}', '" + "a" * 64 + "', "
                    "'sixth_queue', 'planned', '[]', 'test', 'platform', '', 'dry_run', "
                    "0, 0, 0, 0, '', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
    finally:
        engine.dispose()
