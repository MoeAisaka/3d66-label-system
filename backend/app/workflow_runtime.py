from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import (
    ProductionRun,
    ProductionStepAttempt,
    QueueSchedulerState,
    RuntimeDispatchItem,
    ScriptDefinition,
    ScriptVersion,
    WorkflowVersion,
)
from .queue_scheduler import (
    QUEUE_CLASSES,
    DeterministicQueueScheduler,
    QueueJob,
    QueuePolicy,
)
from .workflow_registry import (
    WorkflowRegistryError,
    canonical_workflow_snapshot,
    persisted_workflow_manifest,
)


DEFAULT_LEASE_SECONDS = 300


class WorkflowRuntimeError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ProductionRunRequest:
    workflow_version_id: int
    idempotency_key: str
    category_key: str | None
    queue_class: str
    runtime_context: Mapping[str, Any]
    input_manifest: Mapping[str, Any]
    owner: str
    reason: str = ""
    source_type: str | None = None
    source_id: int | None = None
    source_run_id: int | None = None
    environment: str = "dry_run"


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _script_version_for_reference(
    db: Session,
    reference: str,
) -> ScriptVersion:
    script_key, version_name = reference.rsplit("@", 1)
    definition = db.scalar(
        select(ScriptDefinition).where(ScriptDefinition.script_key == script_key)
    )
    if definition is None:
        raise WorkflowRuntimeError(
            "script_definition_unknown",
            f"工作流引用的脚本不存在：{reference}",
            status_code=409,
        )
    version = db.scalar(
        select(ScriptVersion).where(
            ScriptVersion.script_definition_id == definition.id,
            ScriptVersion.version == version_name,
        )
    )
    if version is None:
        raise WorkflowRuntimeError(
            "script_version_unknown",
            f"工作流引用的脚本版本不存在：{reference}",
            status_code=409,
        )
    return version


def _latest_attempts(
    db: Session,
    run_id: int,
) -> dict[str, ProductionStepAttempt]:
    rows = db.scalars(
        select(ProductionStepAttempt)
        .where(ProductionStepAttempt.run_id == run_id)
        .order_by(
            ProductionStepAttempt.step_key,
            ProductionStepAttempt.attempt_no.desc(),
        )
    ).all()
    latest: dict[str, ProductionStepAttempt] = {}
    for row in rows:
        latest.setdefault(row.step_key, row)
    return latest


def create_production_run(
    db: Session,
    request: ProductionRunRequest,
    *,
    actor: str,
) -> ProductionRun:
    if request.environment != "dry_run":
        raise WorkflowRuntimeError(
            "runtime_environment_forbidden",
            "本阶段运行环境固定为 dry_run",
        )
    if request.queue_class not in QUEUE_CLASSES:
        raise WorkflowRuntimeError(
            "queue_class_unsupported",
            "运行只能使用既有五队列",
        )
    workflow_version = db.get(WorkflowVersion, request.workflow_version_id)
    if workflow_version is None:
        raise WorkflowRuntimeError(
            "workflow_version_not_found",
            "工作流版本不存在",
            status_code=404,
        )
    if workflow_version.status != "active":
        raise WorkflowRuntimeError(
            "workflow_version_unavailable",
            "只有 active 工作流版本可以创建运行",
            status_code=409,
        )

    runtime_context = {
        **dict(request.runtime_context),
        "environment": request.environment,
        "category_key": request.category_key,
        "queue_class": request.queue_class,
        "input_manifest": dict(request.input_manifest),
    }
    try:
        snapshot, snapshot_hash = canonical_workflow_snapshot(
            db,
            workflow_version.id,
            runtime_context,
        )
    except WorkflowRegistryError as exc:
        raise WorkflowRuntimeError(
            exc.code,
            str(exc),
            status_code=exc.status_code,
        ) from exc

    existing = db.scalar(
        select(ProductionRun).where(
            ProductionRun.idempotency_key == request.idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.workflow_version_id != workflow_version.id
            or existing.snapshot_hash != snapshot_hash
        ):
            raise WorkflowRuntimeError(
                "run_idempotency_conflict",
                "相同幂等键对应了不同的冻结输入",
                status_code=409,
            )
        return existing

    run_key = "run-" + hashlib.sha256(
        f"{request.idempotency_key}:{snapshot_hash}".encode("utf-8")
    ).hexdigest()[:32]
    manifest = persisted_workflow_manifest(workflow_version)
    steps = list(manifest["steps"])
    edges = list(manifest["edges"])
    incoming = {step["key"]: 0 for step in steps}
    for edge in edges:
        incoming[edge["to"]] += 1
    roots = [key for key, count in incoming.items() if count == 0]

    run = ProductionRun(
        run_key=run_key,
        idempotency_key=request.idempotency_key,
        source_type=request.source_type,
        source_id=request.source_id,
        source_run_id=request.source_run_id,
        workflow_definition_id=workflow_version.workflow_definition_id,
        workflow_version_id=workflow_version.id,
        snapshot_json=canonical_json(snapshot),
        snapshot_hash=snapshot_hash,
        category_key=request.category_key,
        queue_class=request.queue_class,
        status="queued",
        current_step_key=roots[0] if roots else None,
        blockers_json="[]",
        requested_by=actor,
        owner=request.owner,
        reason=request.reason,
        environment=request.environment,
        total_steps=len(steps),
        completed_steps=0,
        failed_steps=0,
        attempt_count=0,
        error_code="",
        error_message="",
    )
    db.add(run)
    db.flush()

    for sequence, step in enumerate(steps):
        is_root = step["key"] in roots
        input_manifest = (
            dict(request.input_manifest)
            if is_root
            else {"pending_upstream": True, "step_key": step["key"]}
        )
        input_hash = _hash(input_manifest)
        script_version = _script_version_for_reference(
            db,
            step["script_version"],
        )
        attempt = ProductionStepAttempt(
            run_id=run.id,
            step_key=step["key"],
            step_type=step["type"],
            sequence=sequence,
            script_version_id=script_version.id,
            status="pending",
            attempt_no=1,
            idempotency_key=(
                f"{run.run_key}:{step['key']}:{input_hash}:attempt:1"
            ),
            input_manifest_json=canonical_json(input_manifest),
            input_hash=input_hash,
            output_manifest_json="{}",
            checkpoint_json="{}",
            last_error_code="",
            last_error_message="",
        )
        db.add(attempt)
        db.flush()
        if is_root:
            db.add(
                RuntimeDispatchItem(
                    step_attempt_id=attempt.id,
                    queue_class=request.queue_class,
                    priority=50,
                    status="queued",
                    available_at=_now(),
                )
            )
    db.flush()
    return run


def _scheduler_state(
    db: Session,
    policy: QueuePolicy,
) -> QueueSchedulerState:
    state = db.get(QueueSchedulerState, 1)
    if state is None:
        state = QueueSchedulerState(
            id=1,
            policy_version=policy.version,
            global_limit=policy.global_limit,
        )
        db.add(state)
        db.flush()
    if (
        state.policy_version != policy.version
        or state.global_limit != policy.global_limit
    ):
        state.policy_version = policy.version
        state.global_limit = policy.global_limit
        state.validation_deficit = 0
        state.interactive_deficit = 0
        state.production_batch_deficit = 0
        state.canary_deficit = 0
        state.recovery_deficit = 0
        state.dispatch_count = 0
        state.last_recovery_dispatch = None
    return state


def _scheduler_from_state(
    state: QueueSchedulerState,
    policy: QueuePolicy,
) -> DeterministicQueueScheduler:
    return DeterministicQueueScheduler(
        policy,
        deficits={
            "validation": state.validation_deficit,
            "interactive": state.interactive_deficit,
            "production_batch": state.production_batch_deficit,
            "canary": state.canary_deficit,
            "recovery": state.recovery_deficit,
        },
        dispatch_count=state.dispatch_count,
        last_recovery_dispatch=state.last_recovery_dispatch,
    )


def _persist_scheduler_state(
    state: QueueSchedulerState,
    scheduler: DeterministicQueueScheduler,
) -> None:
    persisted = scheduler.export_state()
    deficits = persisted["deficits"]
    state.validation_deficit = deficits["validation"]
    state.interactive_deficit = deficits["interactive"]
    state.production_batch_deficit = deficits["production_batch"]
    state.canary_deficit = deficits["canary"]
    state.recovery_deficit = deficits["recovery"]
    state.dispatch_count = persisted["dispatch_count"]
    state.last_recovery_dispatch = persisted["last_recovery_dispatch"]


def claim_next_runtime_step(
    db: Session,
    worker_id: str,
    *,
    global_limit: int = 1,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> int | None:
    current = _now(now)
    policy = QueuePolicy(global_limit=global_limit)
    state = _scheduler_state(db, policy)

    dispatches = db.scalars(
        select(RuntimeDispatchItem)
        .join(
            ProductionStepAttempt,
            ProductionStepAttempt.id == RuntimeDispatchItem.step_attempt_id,
        )
        .where(
            RuntimeDispatchItem.status == "queued",
            RuntimeDispatchItem.available_at <= current,
            ProductionStepAttempt.status.in_({"pending", "retryable"}),
        )
        .order_by(
            RuntimeDispatchItem.priority.desc(),
            RuntimeDispatchItem.created_at.asc(),
            RuntimeDispatchItem.id.asc(),
        )
    ).all()
    if not dispatches:
        return None

    running = {queue: 0 for queue in QUEUE_CLASSES}
    for queue_class, count in db.execute(
        select(RuntimeDispatchItem.queue_class, func.count(RuntimeDispatchItem.id))
        .join(
            ProductionStepAttempt,
            ProductionStepAttempt.id == RuntimeDispatchItem.step_attempt_id,
        )
        .where(ProductionStepAttempt.status.in_({"leased", "running"}))
        .group_by(RuntimeDispatchItem.queue_class)
    ):
        if queue_class in running:
            running[queue_class] = count

    heads: dict[str, RuntimeDispatchItem] = {}
    for dispatch in dispatches:
        heads.setdefault(dispatch.queue_class, dispatch)
    scheduler = _scheduler_from_state(state, policy)
    selected = scheduler.choose_job(
        [
            QueueJob(
                id=dispatch.id,
                queue_class=dispatch.queue_class,
                created_at=dispatch.created_at,
            )
            for dispatch in heads.values()
        ],
        running=running,
    )
    if selected is None:
        return None
    dispatch = db.get(RuntimeDispatchItem, selected.id)
    if dispatch is None or dispatch.status != "queued":
        return None
    attempt = db.get(ProductionStepAttempt, dispatch.step_attempt_id)
    if attempt is None or attempt.status not in {"pending", "retryable"}:
        return None

    token = secrets.token_hex(20)
    attempt.status = "leased"
    attempt.lease_owner = worker_id
    attempt.lease_token = token
    attempt.lease_expires_at = current + timedelta(seconds=lease_seconds)
    attempt.heartbeat_at = current
    attempt.started_at = attempt.started_at or current
    dispatch.status = "leased"

    run = db.get(ProductionRun, attempt.run_id)
    if run is not None:
        run.status = "running"
        run.current_step_key = attempt.step_key
        run.lease_owner = worker_id
        run.lease_token = token
        run.lease_expires_at = attempt.lease_expires_at
        run.heartbeat_at = current
        run.started_at = run.started_at or current
        run.attempt_count += 1
    _persist_scheduler_state(state, scheduler)
    db.flush()
    return attempt.id


def heartbeat_runtime_step(
    db: Session,
    attempt_id: int,
    lease_token: str,
    worker_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> ProductionStepAttempt:
    attempt = db.get(ProductionStepAttempt, attempt_id)
    if (
        attempt is None
        or attempt.status not in {"leased", "running"}
        or attempt.lease_token != lease_token
        or attempt.lease_owner != worker_id
    ):
        raise WorkflowRuntimeError(
            "step_lease_stale",
            "步骤租约已失效或不属于当前 Worker",
            status_code=409,
        )
    current = _now(now)
    attempt.status = "running"
    attempt.heartbeat_at = current
    attempt.lease_expires_at = current + timedelta(seconds=lease_seconds)
    run = db.get(ProductionRun, attempt.run_id)
    if run is not None:
        run.heartbeat_at = current
        run.lease_expires_at = attempt.lease_expires_at
    db.flush()
    return attempt


def _ready_downstream_steps(
    db: Session,
    run: ProductionRun,
    completed_step_key: str,
) -> list[ProductionStepAttempt]:
    workflow_version = db.get(WorkflowVersion, run.workflow_version_id)
    if workflow_version is None:
        return []
    manifest = persisted_workflow_manifest(workflow_version)
    targets = [
        edge["to"]
        for edge in manifest["edges"]
        if edge["from"] == completed_step_key
    ]
    latest = _latest_attempts(db, run.id)
    ready: list[ProductionStepAttempt] = []
    for target in targets:
        predecessors = [
            edge["from"]
            for edge in manifest["edges"]
            if edge["to"] == target
        ]
        if not all(
            predecessor in latest
            and latest[predecessor].status == "succeeded"
            for predecessor in predecessors
        ):
            continue
        target_attempt = latest.get(target)
        if target_attempt is None or target_attempt.status != "pending":
            continue
        upstream = {
            predecessor: json.loads(latest[predecessor].output_manifest_json)
            for predecessor in predecessors
        }
        input_manifest = {"upstream": upstream}
        input_hash = _hash(input_manifest)
        target_attempt.input_manifest_json = canonical_json(input_manifest)
        target_attempt.input_hash = input_hash
        target_attempt.idempotency_key = (
            f"{run.run_key}:{target}:{input_hash}:attempt:{target_attempt.attempt_no}"
        )
        existing_dispatch = db.scalar(
            select(RuntimeDispatchItem).where(
                RuntimeDispatchItem.step_attempt_id == target_attempt.id
            )
        )
        if existing_dispatch is None:
            db.add(
                RuntimeDispatchItem(
                    step_attempt_id=target_attempt.id,
                    queue_class=run.queue_class,
                    priority=50,
                    status="queued",
                    available_at=_now(),
                )
            )
        ready.append(target_attempt)
    return ready


def complete_runtime_step(
    db: Session,
    attempt_id: int,
    lease_token: str,
    output_manifest: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> ProductionStepAttempt:
    attempt = db.get(ProductionStepAttempt, attempt_id)
    if attempt is None:
        raise WorkflowRuntimeError(
            "step_attempt_not_found",
            "步骤尝试不存在",
            status_code=404,
        )
    output_hash = _hash(output_manifest)
    if attempt.status == "succeeded":
        if attempt.lease_token == lease_token and attempt.output_hash == output_hash:
            return attempt
        raise WorkflowRuntimeError(
            "step_completion_conflict",
            "步骤已经以不同输出完成",
            status_code=409,
        )
    if (
        attempt.status not in {"leased", "running"}
        or attempt.lease_token != lease_token
    ):
        raise WorkflowRuntimeError(
            "step_lease_stale",
            "步骤租约已失效",
            status_code=409,
        )

    current = _now(now)
    checkpoint = {
        "schema_version": "runtime-checkpoint-v1",
        "step_key": attempt.step_key,
        "input_hash": attempt.input_hash,
        "output_hash": output_hash,
    }
    attempt.status = "succeeded"
    attempt.output_manifest_json = canonical_json(output_manifest)
    attempt.output_hash = output_hash
    attempt.checkpoint_json = canonical_json(checkpoint)
    attempt.checkpoint_hash = _hash(checkpoint)
    attempt.finished_at = current
    attempt.heartbeat_at = current
    attempt.last_error_code = ""
    attempt.last_error_message = ""

    dispatch = db.scalar(
        select(RuntimeDispatchItem).where(
            RuntimeDispatchItem.step_attempt_id == attempt.id
        )
    )
    if dispatch is not None:
        dispatch.status = "completed"

    run = db.get(ProductionRun, attempt.run_id)
    if run is None:
        raise WorkflowRuntimeError(
            "production_run_not_found",
            "生产运行不存在",
            status_code=404,
        )
    run.last_checkpoint_id = attempt.id
    ready = _ready_downstream_steps(db, run, attempt.step_key)
    latest = _latest_attempts(db, run.id)
    succeeded = {key for key, item in latest.items() if item.status == "succeeded"}
    failed = {key for key, item in latest.items() if item.status == "failed"}
    run.completed_steps = len(succeeded)
    run.failed_steps = len(failed)
    if run.completed_steps == run.total_steps:
        run.status = "succeeded"
        run.current_step_key = None
        run.finished_at = current
        run.lease_owner = None
        run.lease_token = None
        run.lease_expires_at = None
    else:
        run.status = "queued"
        run.current_step_key = ready[0].step_key if ready else None
        run.lease_owner = None
        run.lease_token = None
        run.lease_expires_at = None
    db.flush()
    return attempt


def recover_expired_runtime_steps(
    db: Session,
    now: datetime | None = None,
) -> int:
    current = _now(now)
    expired = db.scalars(
        select(ProductionStepAttempt).where(
            ProductionStepAttempt.status.in_({"leased", "running"}),
            ProductionStepAttempt.lease_expires_at.is_not(None),
            ProductionStepAttempt.lease_expires_at <= current,
        )
    ).all()
    recovered = 0
    for attempt in expired:
        run = db.get(ProductionRun, attempt.run_id)
        script = db.get(ScriptVersion, attempt.script_version_id)
        dispatch = db.scalar(
            select(RuntimeDispatchItem).where(
                RuntimeDispatchItem.step_attempt_id == attempt.id
            )
        )
        attempt.status = "failed"
        attempt.last_error_code = "STEP_LEASE_EXPIRED"
        attempt.last_error_message = "Worker 租约过期"
        attempt.finished_at = current
        if dispatch is not None:
            dispatch.status = "completed"
        if run is None or script is None:
            continue
        if attempt.attempt_no >= script.max_attempts:
            run.status = "failed"
            run.failed_steps += 1
            run.error_code = "STEP_LEASE_EXPIRED"
            run.error_message = "步骤租约过期且达到重试上限"
            run.finished_at = current
            continue
        next_number = attempt.attempt_no + 1
        next_attempt = ProductionStepAttempt(
            run_id=attempt.run_id,
            step_key=attempt.step_key,
            step_type=attempt.step_type,
            sequence=attempt.sequence,
            script_version_id=attempt.script_version_id,
            status="retryable",
            attempt_no=next_number,
            idempotency_key=(
                f"{run.run_key}:{attempt.step_key}:{attempt.input_hash}:"
                f"attempt:{next_number}"
            ),
            input_manifest_json=attempt.input_manifest_json,
            input_hash=attempt.input_hash,
            output_manifest_json="{}",
            checkpoint_json=attempt.checkpoint_json,
            checkpoint_hash=attempt.checkpoint_hash,
            last_error_code="",
            last_error_message="",
        )
        db.add(next_attempt)
        db.flush()
        db.add(
            RuntimeDispatchItem(
                step_attempt_id=next_attempt.id,
                queue_class="recovery",
                priority=100,
                status="queued",
                available_at=current,
            )
        )
        run.status = "retryable"
        run.current_step_key = attempt.step_key
        run.error_code = "STEP_LEASE_EXPIRED"
        run.error_message = "步骤租约已回收并进入恢复队列"
        run.lease_owner = None
        run.lease_token = None
        run.lease_expires_at = None
        recovered += 1
    db.flush()
    return recovered


def resume_from_checkpoint(
    db: Session,
    run_id: int,
) -> ProductionRun:
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise WorkflowRuntimeError(
            "production_run_not_found",
            "生产运行不存在",
            status_code=404,
        )
    if run.status not in {"paused", "retryable", "blocked"}:
        raise WorkflowRuntimeError(
            "run_resume_state_invalid",
            "当前运行状态不能恢复",
            status_code=409,
        )
    latest = _latest_attempts(db, run.id)
    pending = sorted(
        (
            attempt
            for attempt in latest.values()
            if attempt.status in {"pending", "retryable"}
        ),
        key=lambda item: (item.sequence, item.id),
    )
    if not pending:
        raise WorkflowRuntimeError(
            "run_resume_step_missing",
            "没有可恢复的步骤",
            status_code=409,
        )
    attempt = pending[0]
    dispatch = db.scalar(
        select(RuntimeDispatchItem).where(
            RuntimeDispatchItem.step_attempt_id == attempt.id
        )
    )
    if dispatch is None:
        db.add(
            RuntimeDispatchItem(
                step_attempt_id=attempt.id,
                queue_class="recovery",
                priority=100,
                status="queued",
                available_at=_now(),
            )
        )
    elif dispatch.status != "queued":
        dispatch.status = "queued"
        dispatch.queue_class = "recovery"
        dispatch.available_at = _now()
    run.status = "queued"
    run.current_step_key = attempt.step_key
    db.flush()
    return run
