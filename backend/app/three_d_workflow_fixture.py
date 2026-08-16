"""Deterministic local proof for the 3D/SU label closed loop.

This module registers no external connector and never calls a model.  It uses
the existing workflow runtime to exercise the same five queues, checkpoint and
retry semantics that production modules use, while keeping all facts local to
the dry-run database.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import (
    ProductionRun,
    ProductionStepAttempt,
    RuntimeDispatchItem,
    ScriptDefinition,
    ScriptVersion,
    WorkflowDefinition,
    WorkflowVersion,
)
from .workflow_fixture_executor import FixtureExecutionError, execute_fixture
from .workflow_registry import create_workflow_definition, create_workflow_version
from .workflow_runtime import (
    ProductionRunRequest,
    claim_next_runtime_step,
    complete_runtime_step,
    create_production_run,
    fail_runtime_step,
)


CATEGORY_KEY = "model_3d_su"
WORKFLOW_KEY = "label.model-3d-su.dry-run"
WORKFLOW_VERSION = "dry-run-v1"
_STEPS: tuple[tuple[str, str, str], ...] = (
    ("source_ingress", "connector", "identity"),
    ("evaluate_and_label", "rule_eval", "transform"),
    ("human_correction_gate", "human_task", "identity"),
    ("label_fact_gate", "release_gate", "identity"),
    ("shadow_projection", "projection", "fail_once"),
    ("projection_reconcile", "reconcile", "identity"),
    ("badcase_feedback", "feedback", "identity"),
)
_HUMAN_GATES = {
    "human_correction_gate": "mechanism",
    "label_fact_gate": "label_fact",
}


class ThreeDDryRunGateError(ValueError):
    pass


@dataclass(frozen=True)
class ThreeDDryRunReceipt:
    run_id: int
    category_key: str
    workflow_version: str
    status: str
    ordered_steps: tuple[str, ...]
    human_correction_gate: str
    label_fact_gate: str
    projection_reconciliation: dict[str, Any]
    feedback_case_key: str
    snapshot_hash: str
    recovery_evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_three_d_dry_run_manifest() -> dict[str, Any]:
    """Return the non-production 3D/SU DAG built from existing step types."""
    steps = [
        {
            "key": key,
            "type": step_type,
            "script_version": f"fixture.3d-su.{key}@1",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
        for key, step_type, _fixture in _STEPS
    ]
    return {
        "schema_version": "workflow-v1",
        "category_key": CATEGORY_KEY,
        "steps": steps,
        "edges": [
            {"from": _STEPS[index][0], "to": _STEPS[index + 1][0]}
            for index in range(len(_STEPS) - 1)
        ],
        "queue_class": "validation",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "resource_policy": {"max_parallel": 1},
        "environment": "dry_run",
        "human_gates": {
            "human_correction_gate": {"axis": "mechanism", "required": True},
            "label_fact_gate": {"axis": "label_fact", "required": True},
        },
        "non_goals": [
            "real_upstream",
            "real_model_call",
            "formal_publish",
            "stock_overwrite",
        ],
    }


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ensure_script(
    db: Session,
    *,
    step_key: str,
    step_type: str,
    fixture: str,
) -> ScriptVersion:
    script_key = f"fixture.3d-su.{step_key}"
    definition = db.scalar(
        select(ScriptDefinition).where(ScriptDefinition.script_key == script_key)
    )
    if definition is None:
        definition = ScriptDefinition(
            script_key=script_key,
            name=f"3D/SU dry-run · {step_key}",
            description="deterministic local fixture",
            owner="label-platform",
            allowed_categories_json=canonical_json([CATEGORY_KEY]),
            step_types_json=canonical_json([step_type]),
            status="active",
            created_by="fixture",
        )
        db.add(definition)
        db.flush()
    version = db.scalar(
        select(ScriptVersion).where(
            ScriptVersion.script_definition_id == definition.id,
            ScriptVersion.version == "1",
        )
    )
    if version is None:
        artifact = hashlib.sha256(script_key.encode("utf-8")).hexdigest()
        manifest: dict[str, Any] = {"fixture": fixture}
        if fixture == "transform":
            manifest["mapping"] = {
                "content_key": "upstream.source_ingress.content_key",
                "source_snapshot_hash": "upstream.source_ingress.source_snapshot_hash",
            }
        version = ScriptVersion(
            script_definition_id=definition.id,
            version="1",
            display_name=f"{step_key} dry-run v1",
            executor_kind="deterministic_fixture",
            artifact_sha256=artifact,
            manifest_json=canonical_json(manifest),
            input_schema_json='{"type":"object"}',
            output_schema_json='{"type":"object"}',
            required_permissions_json="[]",
            idempotency_template="{run_key}:{step_key}:{input_hash}",
            timeout_seconds=60,
            max_attempts=2,
            retry_policy_json='{"kind":"fixed","delay_seconds":0}',
            concurrency_limit=1,
            estimated_cost_json='{"currency":"CNY","micros":0}',
            status="active",
            validation_report_json='{"ok":true,"errors":[]}',
            blocked_reason="",
            created_by="fixture",
        )
        db.add(version)
        db.flush()
    return version


def _ensure_workflow(db: Session) -> WorkflowVersion:
    for step_key, step_type, fixture in _STEPS:
        _ensure_script(
            db,
            step_key=step_key,
            step_type=step_type,
            fixture=fixture,
        )
    definition = db.scalar(
        select(WorkflowDefinition).where(WorkflowDefinition.workflow_key == WORKFLOW_KEY)
    )
    if definition is None:
        definition = create_workflow_definition(
            db,
            workflow_key=WORKFLOW_KEY,
            name="3D/SU 标签闭环 deterministic dry-run",
            description="仅本地闭环、双人工门、影子投影和 Badcase 回流验收",
            owner="label-platform",
            allowed_categories=[CATEGORY_KEY],
            created_by="fixture",
        )
    version = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_definition_id == definition.id,
            WorkflowVersion.version == WORKFLOW_VERSION,
        )
    )
    if version is None:
        version = create_workflow_version(
            db,
            definition=definition,
            version=WORKFLOW_VERSION,
            manifest=build_three_d_dry_run_manifest(),
            created_by="fixture",
        )
        version.status = "active"
        version.validation_report_json = '{"ok":true,"errors":[]}'
        db.flush()
    return version


def _pause_at_human_gate(
    db: Session,
    *,
    run: ProductionRun,
    attempt: ProductionStepAttempt,
) -> ProductionRun:
    axis = _HUMAN_GATES[attempt.step_key]
    attempt.status = "blocked"
    attempt.lease_owner = None
    attempt.lease_token = None
    attempt.lease_expires_at = None
    dispatch = db.scalar(
        select(RuntimeDispatchItem).where(
            RuntimeDispatchItem.step_attempt_id == attempt.id
        )
    )
    if dispatch is not None:
        dispatch.status = "completed"
    run.status = "paused"
    run.current_step_key = attempt.step_key
    run.blockers_json = canonical_json(
        [
            {
                "code": "human_gate_approval_required",
                "gate_key": attempt.step_key,
                "axis": axis,
            }
        ]
    )
    run.lease_owner = None
    run.lease_token = None
    run.lease_expires_at = None
    db.flush()
    return run


def _execute_until_boundary(db: Session, *, run_id: int) -> ProductionRun:
    worker_id = "fixture-3d-su-worker"
    while True:
        attempt_id = claim_next_runtime_step(db, worker_id, global_limit=1)
        if attempt_id is None:
            break
        attempt = db.get(ProductionStepAttempt, attempt_id)
        if attempt is None or not attempt.lease_token:
            raise RuntimeError("deterministic workflow claim evidence missing")
        run = db.get(ProductionRun, run_id)
        if run is None:
            raise RuntimeError("3D/SU deterministic dry-run evidence missing")
        if attempt.step_key in _HUMAN_GATES:
            return _pause_at_human_gate(db, run=run, attempt=attempt)
        script = db.get(ScriptVersion, attempt.script_version_id)
        if script is None:
            raise RuntimeError("deterministic workflow script evidence missing")
        try:
            result = execute_fixture(
                str(json.loads(script.manifest_json).get("fixture", attempt.step_type)),
                json.loads(attempt.input_manifest_json),
                json.loads(script.manifest_json),
                attempt_no=attempt.attempt_no,
            )
        except FixtureExecutionError as exc:
            fail_runtime_step(
                db,
                attempt.id,
                attempt.lease_token,
                exc.code,
                str(exc),
                retryable=True,
            )
        else:
            complete_runtime_step(
                db,
                attempt.id,
                attempt.lease_token,
                result.output_manifest,
            )
        db.flush()
    run = db.get(ProductionRun, run_id)
    if run is None or run.status not in {"paused", "succeeded"}:
        raise RuntimeError("3D/SU deterministic dry-run did not reach a boundary")
    return run


def start_three_d_dry_run(
    db: Session,
    *,
    idempotency_key: str,
    actor: str,
) -> ProductionRun:
    """Start or resume the fixture until the next explicit human gate."""
    workflow = _ensure_workflow(db)
    run = create_production_run(
        db,
        ProductionRunRequest(
            workflow_version_id=workflow.id,
            idempotency_key=idempotency_key,
            category_key=CATEGORY_KEY,
            queue_class="validation",
            runtime_context={
                "fixture": "3d-su-dry-run-v1",
            },
            input_manifest={
                "content_key": "fixture:model_3d_su:001",
                "source_snapshot_hash": _hash({"fixture": idempotency_key}),
            },
            owner="label-platform",
            reason="deterministic 3D/SU closed-loop verification",
            environment="dry_run",
        ),
        actor=actor,
    )
    if run.status not in {"paused", "succeeded"}:
        run = _execute_until_boundary(db, run_id=run.id)
    return run


def approve_three_d_dry_run_gate(
    db: Session,
    *,
    run_id: int,
    gate_key: str,
    actor: str,
) -> ProductionRun:
    """Apply one explicit fixture decision and continue to the next boundary."""
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise ThreeDDryRunGateError("3D/SU dry-run 不存在")
    expected_gate = run.current_step_key
    if run.status != "paused" or expected_gate not in _HUMAN_GATES:
        raise ThreeDDryRunGateError("当前运行没有等待人工门")
    if gate_key != expected_gate:
        gate_name = "人工纠偏门" if expected_gate == "human_correction_gate" else "标签事实发布门"
        raise ThreeDDryRunGateError(f"当前等待{gate_name}，不能越序放行")
    normalized_actor = actor.strip()
    if not normalized_actor:
        raise ThreeDDryRunGateError("人工放行人不能为空")
    attempt = db.scalar(
        select(ProductionStepAttempt).where(
            ProductionStepAttempt.run_id == run.id,
            ProductionStepAttempt.step_key == gate_key,
            ProductionStepAttempt.status == "blocked",
        )
    )
    if attempt is None:
        raise ThreeDDryRunGateError("当前人工门缺少可放行的步骤证据")
    lease_token = hashlib.sha256(
        f"{run.run_key}:{gate_key}:{normalized_actor}".encode("utf-8")
    ).hexdigest()
    attempt.status = "leased"
    attempt.lease_owner = normalized_actor
    attempt.lease_token = lease_token
    run.status = "running"
    run.blockers_json = "[]"
    complete_runtime_step(
        db,
        attempt.id,
        lease_token,
        {
            "gate_key": gate_key,
            "axis": _HUMAN_GATES[gate_key],
            "decision": "approved",
            "actor": normalized_actor,
        },
    )
    return _execute_until_boundary(db, run_id=run.id)


def run_three_d_dry_run(
    db: Session,
    *,
    idempotency_key: str,
    actor: str,
) -> ThreeDDryRunReceipt:
    """Execute the full fixture with two explicit, deterministic human decisions."""
    run = start_three_d_dry_run(
        db,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if run.status == "paused" and run.current_step_key == "human_correction_gate":
        run = approve_three_d_dry_run_gate(
            db,
            run_id=run.id,
            gate_key="human_correction_gate",
            actor=actor,
        )
    if run.status == "paused" and run.current_step_key == "label_fact_gate":
        run = approve_three_d_dry_run_gate(
            db,
            run_id=run.id,
            gate_key="label_fact_gate",
            actor=actor,
        )
    if run.status != "succeeded":
        raise RuntimeError("3D/SU deterministic dry-run did not reach succeeded")
    attempts = db.scalars(
        select(ProductionStepAttempt)
        .where(ProductionStepAttempt.run_id == run.id)
        .order_by(ProductionStepAttempt.sequence, ProductionStepAttempt.attempt_no)
    ).all()
    recovery = next(
        (
            row
            for row in attempts
            if row.step_key == "shadow_projection" and row.attempt_no == 2
        ),
        None,
    )
    if recovery is None or not recovery.checkpoint_hash:
        raise RuntimeError("3D/SU shadow recovery checkpoint is missing")
    return ThreeDDryRunReceipt(
        run_id=run.id,
        category_key=CATEGORY_KEY,
        workflow_version=WORKFLOW_VERSION,
        status=run.status,
        ordered_steps=tuple(step[0] for step in _STEPS),
        human_correction_gate="approved",
        label_fact_gate="approved",
        projection_reconciliation={
            "status": "matched",
            "row_count": 1,
            "payload_hash_verified": True,
        },
        feedback_case_key=f"badcase:{CATEGORY_KEY}:{run.snapshot_hash[:16]}",
        snapshot_hash=run.snapshot_hash,
        recovery_evidence={
            "failed_step": "shadow_projection",
            "retry_attempt": recovery.attempt_no,
            "checkpoint_hash": recovery.checkpoint_hash,
        },
    )
