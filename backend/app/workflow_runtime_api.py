from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .authz import has_permission
from .database import get_db
from .models import (
    ProductionRun,
    ProductionStepAttempt,
    RuntimeAuditEvent,
    RuntimeDispatchItem,
    ScriptVersion,
    User,
    WorkflowVersion,
)
from .workflow_runtime import (
    ProductionRunRequest,
    WorkflowRuntimeError,
    create_production_run,
    resume_from_checkpoint,
)


FORBIDDEN_EXECUTABLE_FIELDS = frozenset(
    {"source", "code", "command", "shell", "sql", "script"}
)


class RuntimeRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_version_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    category_key: str | None = Field(default=None, max_length=40)
    queue_class: str
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    input_manifest: dict[str, Any]
    owner: str = Field(min_length=1, max_length=120)
    reason: str = ""
    source_type: str | None = Field(default=None, max_length=60)
    source_id: int | None = None
    source_run_id: int | None = None
    environment: str = "dry_run"


def _runtime_error(exc: WorkflowRuntimeError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _require_write(user: User) -> None:
    if not has_permission(user, "runtime:write"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "runtime_write_forbidden",
                "message": "当前账号没有运行时写权限",
            },
        )


def _scan_executable_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_EXECUTABLE_FIELDS:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "arbitrary_code_field_forbidden",
                        "message": f"运行输入包含禁止字段：{child_path}",
                    },
                )
            _scan_executable_fields(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_executable_fields(child, path=f"{path}[{index}]")


def _run(db: Session, run_key: str) -> ProductionRun:
    row = db.scalar(
        select(ProductionRun).where(ProductionRun.run_key == run_key)
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "production_run_not_found",
                "message": "生产运行不存在",
            },
        )
    return row


def _allowed_actions(status: str) -> list[str]:
    return {
        "planned": ["cancel"],
        "queued": ["cancel", "pause"],
        "running": ["cancel", "pause"],
        "paused": ["cancel", "resume"],
        "retryable": ["cancel", "resume"],
        "blocked": ["cancel", "resume"],
        "failed": ["retry"],
    }.get(status, [])


def _run_payload(db: Session, row: ProductionRun) -> dict[str, Any]:
    workflow = db.get(WorkflowVersion, row.workflow_version_id)
    return {
        "id": row.id,
        "run_key": row.run_key,
        "idempotency_key": row.idempotency_key,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "source_run_id": row.source_run_id,
        "workflow_definition_id": row.workflow_definition_id,
        "workflow_version_id": row.workflow_version_id,
        "workflow_version": workflow.version if workflow is not None else "",
        "snapshot_hash": row.snapshot_hash,
        "category_key": row.category_key,
        "queue_class": row.queue_class,
        "status": row.status,
        "current_step_key": row.current_step_key,
        "blockers": json.loads(row.blockers_json),
        "requested_by": row.requested_by,
        "owner": row.owner,
        "reason": row.reason,
        "environment": row.environment,
        "total_steps": row.total_steps,
        "completed_steps": row.completed_steps,
        "failed_steps": row.failed_steps,
        "last_checkpoint_id": row.last_checkpoint_id,
        "attempt_count": row.attempt_count,
        "next_retry_at": row.next_retry_at,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "allowed_actions": _allowed_actions(row.status),
    }


def _append_runtime_audit(
    db: Session,
    row: ProductionRun,
    *,
    action: str,
    actor: str,
    details: Mapping[str, Any],
) -> RuntimeAuditEvent:
    existing_count = db.scalar(
        select(RuntimeAuditEvent.id)
        .where(
            RuntimeAuditEvent.entity_type == "production_run",
            RuntimeAuditEvent.entity_key == row.run_key,
        )
        .order_by(RuntimeAuditEvent.id.desc())
        .limit(1)
    )
    ordinal = int(existing_count or 0) + 1
    event_key = hashlib.sha256(
        f"{row.run_key}:{action}:{actor}:{ordinal}:{canonical_json(details)}".encode(
            "utf-8"
        )
    ).hexdigest()
    event = RuntimeAuditEvent(
        event_key=event_key,
        entity_type="production_run",
        entity_key=row.run_key,
        action=action,
        actor=actor,
        details_json=canonical_json(dict(details)),
    )
    db.add(event)
    db.flush()
    return event


def _pause_run(db: Session, row: ProductionRun) -> ProductionRun:
    if row.status not in {"queued", "running", "retryable"}:
        raise WorkflowRuntimeError(
            "run_pause_state_invalid",
            "当前运行状态不能暂停",
            status_code=409,
        )
    attempts = db.scalars(
        select(ProductionStepAttempt).where(
            ProductionStepAttempt.run_id == row.id,
            ProductionStepAttempt.status.in_({"leased", "running"}),
        )
    ).all()
    for attempt in attempts:
        attempt.status = "retryable"
        attempt.lease_owner = None
        attempt.lease_token = None
        attempt.lease_expires_at = None
        dispatch = db.scalar(
            select(RuntimeDispatchItem).where(
                RuntimeDispatchItem.step_attempt_id == attempt.id
            )
        )
        if dispatch is not None:
            dispatch.status = "canceled"
    row.status = "paused"
    row.lease_owner = None
    row.lease_token = None
    row.lease_expires_at = None
    db.flush()
    return row


def _cancel_run(db: Session, row: ProductionRun) -> ProductionRun:
    if row.status in {"succeeded", "canceled"}:
        raise WorkflowRuntimeError(
            "run_cancel_state_invalid",
            "当前运行已经结束，不能取消",
            status_code=409,
        )
    dispatches = db.scalars(
        select(RuntimeDispatchItem)
        .join(
            ProductionStepAttempt,
            ProductionStepAttempt.id == RuntimeDispatchItem.step_attempt_id,
        )
        .where(
            ProductionStepAttempt.run_id == row.id,
            RuntimeDispatchItem.status.in_({"queued", "leased"}),
        )
    ).all()
    for dispatch in dispatches:
        dispatch.status = "canceled"
    row.status = "canceled"
    row.current_step_key = None
    row.lease_owner = None
    row.lease_token = None
    row.lease_expires_at = None
    db.flush()
    return row


def build_workflow_runtime_router(
    require_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/runtime/runs", tags=["workflow-runtime"])

    @router.get("")
    def list_runs(
        status: str | None = None,
        queue_class: str | None = None,
        category_key: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        statement = select(ProductionRun)
        if status:
            statement = statement.where(ProductionRun.status == status)
        if queue_class:
            statement = statement.where(ProductionRun.queue_class == queue_class)
        if category_key:
            statement = statement.where(ProductionRun.category_key == category_key)
        rows = db.scalars(
            statement.order_by(
                ProductionRun.created_at.desc(),
                ProductionRun.id.desc(),
            ).limit(limit)
        ).all()
        return {"items": [_run_payload(db, row) for row in rows]}

    @router.post("")
    def create_run(
        payload: RuntimeRunCreate,
        response: Response,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        _scan_executable_fields(payload.runtime_context)
        _scan_executable_fields(payload.input_manifest)
        existing = db.scalar(
            select(ProductionRun).where(
                ProductionRun.idempotency_key == payload.idempotency_key
            )
        )
        try:
            row = create_production_run(
                db,
                ProductionRunRequest(
                    workflow_version_id=payload.workflow_version_id,
                    idempotency_key=payload.idempotency_key,
                    category_key=payload.category_key,
                    queue_class=payload.queue_class,
                    runtime_context=payload.runtime_context,
                    input_manifest=payload.input_manifest,
                    owner=payload.owner,
                    reason=payload.reason,
                    source_type=payload.source_type,
                    source_id=payload.source_id,
                    source_run_id=payload.source_run_id,
                    environment=payload.environment,
                ),
                actor=user.username,
            )
        except WorkflowRuntimeError as exc:
            db.rollback()
            raise _runtime_error(exc) from exc
        duplicate = existing is not None
        if not duplicate:
            _append_runtime_audit(
                db,
                row,
                action="create",
                actor=user.username,
                details={
                    "status": row.status,
                    "workflow_version_id": row.workflow_version_id,
                    "snapshot_hash": row.snapshot_hash,
                    "environment": row.environment,
                },
            )
        db.commit()
        db.refresh(row)
        response.status_code = 200 if duplicate else 201
        return {**_run_payload(db, row), "duplicate": duplicate}

    @router.get("/{run_key}")
    def get_run(
        run_key: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        return _run_payload(db, _run(db, run_key))

    @router.get("/{run_key}/timeline")
    def get_timeline(
        run_key: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        row = _run(db, run_key)
        attempts = db.scalars(
            select(ProductionStepAttempt)
            .where(ProductionStepAttempt.run_id == row.id)
            .order_by(
                ProductionStepAttempt.sequence,
                ProductionStepAttempt.attempt_no,
                ProductionStepAttempt.id,
            )
        ).all()
        items: list[dict[str, Any]] = []
        for attempt in attempts:
            script = db.get(ScriptVersion, attempt.script_version_id)
            dispatch = db.scalar(
                select(RuntimeDispatchItem).where(
                    RuntimeDispatchItem.step_attempt_id == attempt.id
                )
            )
            items.append(
                {
                    "id": attempt.id,
                    "step_key": attempt.step_key,
                    "step_type": attempt.step_type,
                    "sequence": attempt.sequence,
                    "attempt_no": attempt.attempt_no,
                    "status": attempt.status,
                    "script_version_id": attempt.script_version_id,
                    "script_version": script.version if script is not None else "",
                    "queue_class": dispatch.queue_class if dispatch is not None else None,
                    "input_hash": attempt.input_hash,
                    "output_hash": attempt.output_hash,
                    "checkpoint_hash": attempt.checkpoint_hash,
                    "lease_owner": attempt.lease_owner,
                    "lease_expires_at": attempt.lease_expires_at,
                    "last_error_code": attempt.last_error_code,
                    "last_error_message": attempt.last_error_message,
                    "started_at": attempt.started_at,
                    "finished_at": attempt.finished_at,
                }
            )
        return {"run_key": run_key, "items": items}

    @router.get("/{run_key}/snapshot")
    def get_snapshot(
        run_key: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        row = _run(db, run_key)
        return {
            "run_key": run_key,
            "snapshot_hash": row.snapshot_hash,
            "snapshot": json.loads(row.snapshot_json),
        }

    @router.post("/{run_key}/pause")
    def pause_run(
        run_key: str,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        row = _run(db, run_key)
        try:
            _pause_run(db, row)
        except WorkflowRuntimeError as exc:
            db.rollback()
            raise _runtime_error(exc) from exc
        _append_runtime_audit(
            db,
            row,
            action="pause",
            actor=user.username,
            details={"status": row.status},
        )
        db.commit()
        db.refresh(row)
        return _run_payload(db, row)

    @router.post("/{run_key}/resume")
    def resume_run(
        run_key: str,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        row = _run(db, run_key)
        try:
            resume_from_checkpoint(db, row.id)
        except WorkflowRuntimeError as exc:
            db.rollback()
            raise _runtime_error(exc) from exc
        _append_runtime_audit(
            db,
            row,
            action="resume",
            actor=user.username,
            details={"status": row.status},
        )
        db.commit()
        db.refresh(row)
        return _run_payload(db, row)

    @router.post("/{run_key}/retry")
    def retry_run(
        run_key: str,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        row = _run(db, run_key)
        if row.status != "failed":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_retry_state_invalid",
                    "message": "只有 failed 运行可以人工重试",
                },
            )
        row.status = "retryable"
        try:
            resume_from_checkpoint(db, row.id)
        except WorkflowRuntimeError as exc:
            db.rollback()
            raise _runtime_error(exc) from exc
        _append_runtime_audit(
            db,
            row,
            action="retry",
            actor=user.username,
            details={"status": row.status},
        )
        db.commit()
        db.refresh(row)
        return _run_payload(db, row)

    @router.post("/{run_key}/cancel")
    def cancel_run(
        run_key: str,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        row = _run(db, run_key)
        try:
            _cancel_run(db, row)
        except WorkflowRuntimeError as exc:
            db.rollback()
            raise _runtime_error(exc) from exc
        _append_runtime_audit(
            db,
            row,
            action="cancel",
            actor=user.username,
            details={"status": row.status},
        )
        db.commit()
        db.refresh(row)
        return _run_payload(db, row)

    return router

