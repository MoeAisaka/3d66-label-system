from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .authz import has_permission
from .audit import canonical_json
from .database import get_db
from .models import ScriptDefinition, ScriptVersion, User
from .script_registry import (
    ScriptRegistryError,
    persisted_script_payload,
    transition_script_version,
    validate_script_version_payload,
)


class ScriptDefinitionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    owner: str = Field(min_length=1, max_length=120)
    allowed_categories: list[str] = Field(default_factory=list)
    step_types: list[str] = Field(default_factory=list)


class ScriptVersionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    executor_kind: str
    artifact_sha256: str
    manifest: dict[str, Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_permissions: list[str] = Field(default_factory=list)
    idempotency_template: str
    timeout_seconds: int
    max_attempts: int
    retry_policy: dict[str, Any]
    concurrency_limit: int
    rate_limit_key: str | None = None
    estimated_cost: dict[str, Any]


class ScriptTransitionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: str


def _error(exc: ScriptRegistryError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _require_write(user: User) -> None:
    if not has_permission(user, "scripts:write"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "script_write_forbidden",
                "message": "当前账号没有脚本注册写权限",
            },
        )


def _definition_payload(row: ScriptDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "script_key": row.script_key,
        "name": row.name,
        "description": row.description,
        "owner": row.owner,
        "allowed_categories": json.loads(row.allowed_categories_json),
        "step_types": json.loads(row.step_types_json),
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _version_payload(row: ScriptVersion) -> dict[str, Any]:
    try:
        validation_report = json.loads(row.validation_report_json)
    except json.JSONDecodeError:
        validation_report = {
            "ok": False,
            "errors": [
                {
                    "path": "validation_report_json",
                    "code": "script_validation_report_invalid",
                    "message": "脚本版本校验报告损坏",
                }
            ],
        }
    return {
        "id": row.id,
        "script_definition_id": row.script_definition_id,
        "version": row.version,
        "display_name": row.display_name,
        "executor_kind": row.executor_kind,
        "artifact_sha256": row.artifact_sha256,
        "manifest": json.loads(row.manifest_json),
        "input_schema": json.loads(row.input_schema_json),
        "output_schema": json.loads(row.output_schema_json),
        "required_permissions": json.loads(row.required_permissions_json),
        "idempotency_template": row.idempotency_template,
        "timeout_seconds": row.timeout_seconds,
        "max_attempts": row.max_attempts,
        "retry_policy": json.loads(row.retry_policy_json),
        "concurrency_limit": row.concurrency_limit,
        "rate_limit_key": row.rate_limit_key,
        "estimated_cost": json.loads(row.estimated_cost_json),
        "status": row.status,
        "validation_report": validation_report,
        "blocked_reason": row.blocked_reason,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _definition(db: Session, script_key: str) -> ScriptDefinition:
    row = db.scalar(
        select(ScriptDefinition).where(ScriptDefinition.script_key == script_key)
    )
    if row is None:
        raise ScriptRegistryError(
            "script_definition_not_found",
            "脚本定义不存在",
            status_code=404,
        )
    return row


def _version(
    db: Session,
    definition_id: int,
    version: str,
) -> ScriptVersion:
    row = db.scalar(
        select(ScriptVersion).where(
            ScriptVersion.script_definition_id == definition_id,
            ScriptVersion.version == version,
        )
    )
    if row is None:
        raise ScriptRegistryError(
            "script_version_not_found",
            "脚本版本不存在",
            status_code=404,
        )
    return row


def build_script_registry_router(
    require_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/scripts", tags=["script-registry"])

    @router.get("/")
    def list_scripts(
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        definitions = db.scalars(
            select(ScriptDefinition).order_by(
                ScriptDefinition.updated_at.desc(),
                ScriptDefinition.id.desc(),
            )
        ).all()
        return {"items": [_definition_payload(row) for row in definitions]}

    @router.post("/", status_code=201)
    def create_script(
        payload: ScriptDefinitionWrite,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        existing = db.scalar(
            select(ScriptDefinition).where(
                ScriptDefinition.script_key == payload.script_key
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "script_definition_duplicate",
                    "message": "脚本定义键已存在",
                },
            )
        row = ScriptDefinition(
            script_key=payload.script_key,
            name=payload.name,
            description=payload.description,
            owner=payload.owner,
            allowed_categories_json=canonical_json(payload.allowed_categories),
            step_types_json=canonical_json(payload.step_types),
            status="active",
            created_by=user.username,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _definition_payload(row)

    @router.get("/{script_key}/versions")
    def list_versions(
        script_key: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            definition = _definition(db, script_key)
        except ScriptRegistryError as exc:
            raise _error(exc) from exc
        rows = db.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.script_definition_id == definition.id)
            .order_by(ScriptVersion.created_at.desc(), ScriptVersion.id.desc())
        ).all()
        return {"items": [_version_payload(row) for row in rows]}

    @router.post("/{script_key}/versions", status_code=201)
    def create_version(
        script_key: str,
        payload: ScriptVersionWrite,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            definition = _definition(db, script_key)
        except ScriptRegistryError as exc:
            raise _error(exc) from exc
        if definition.status != "active":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "script_definition_retired",
                    "message": "已退休脚本定义不能创建新版本",
                },
            )
        existing = db.scalar(
            select(ScriptVersion).where(
                ScriptVersion.script_definition_id == definition.id,
                ScriptVersion.version == payload.version,
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "script_version_duplicate",
                    "message": "脚本版本已存在",
                },
            )
        raw = payload.model_dump(exclude={"version"})
        report = validate_script_version_payload(raw)
        if not report.ok:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "script_version_invalid",
                    "message": "脚本版本合同不合法",
                    "errors": report.as_dict()["errors"],
                },
            )
        row = ScriptVersion(
            script_definition_id=definition.id,
            version=payload.version,
            display_name=payload.display_name,
            executor_kind=payload.executor_kind,
            artifact_sha256=payload.artifact_sha256,
            manifest_json=canonical_json(payload.manifest),
            input_schema_json=canonical_json(payload.input_schema),
            output_schema_json=canonical_json(payload.output_schema),
            required_permissions_json=canonical_json(payload.required_permissions),
            idempotency_template=payload.idempotency_template,
            timeout_seconds=payload.timeout_seconds,
            max_attempts=payload.max_attempts,
            retry_policy_json=canonical_json(payload.retry_policy),
            concurrency_limit=payload.concurrency_limit,
            rate_limit_key=payload.rate_limit_key,
            estimated_cost_json=canonical_json(payload.estimated_cost),
            status="draft",
            validation_report_json="{}",
            blocked_reason="",
            created_by=user.username,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _version_payload(row)

    @router.post("/{script_key}/versions/{version}/validate")
    def validate_version(
        script_key: str,
        version: str,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            definition = _definition(db, script_key)
            row = _version(db, definition.id, version)
        except ScriptRegistryError as exc:
            raise _error(exc) from exc
        if row.status not in {"validating", "blocked"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "script_validation_state_invalid",
                    "message": "只有 validating 或 blocked 版本可以校验",
                },
            )
        report = validate_script_version_payload(persisted_script_payload(row))
        row.validation_report_json = canonical_json(report.as_dict())
        if not report.ok:
            row.status = "blocked"
            row.blocked_reason = "脚本版本合同校验失败"
        db.commit()
        db.refresh(row)
        return _version_payload(row)

    @router.post("/{script_key}/versions/{version}/transition")
    def transition_version(
        script_key: str,
        version: str,
        payload: ScriptTransitionWrite,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            definition = _definition(db, script_key)
            row = _version(db, definition.id, version)
            transition_script_version(
                db,
                row.id,
                payload.target_status,
                actor=user.username,
            )
            db.commit()
            db.refresh(row)
            return _version_payload(row)
        except ScriptRegistryError as exc:
            db.rollback()
            raise _error(exc) from exc

    return router

