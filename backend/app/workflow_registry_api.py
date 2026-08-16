from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .authz import has_permission
from .database import get_db
from .models import User, WorkflowDefinition, WorkflowVersion
from .workflow_registry import (
    WorkflowRegistryError,
    create_workflow_definition,
    create_workflow_version,
    persisted_workflow_manifest,
    transition_workflow_version,
    validate_workflow_manifest,
)


class WorkflowDefinitionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    owner: str = Field(min_length=1, max_length=120)
    allowed_categories: list[str] = Field(default_factory=list)


class WorkflowVersionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=80)
    manifest: dict[str, Any]


class WorkflowTransitionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: str


def _http_error(exc: WorkflowRegistryError) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
    if exc.code == "workflow_manifest_invalid":
        try:
            detail["validation_report"] = json.loads(str(exc))
            detail["message"] = "工作流合同不合法"
        except json.JSONDecodeError:
            pass
    return HTTPException(status_code=exc.status_code, detail=detail)


def _require_write(user: User) -> None:
    if not has_permission(user, "workflows:write"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "workflow_write_forbidden",
                "message": "当前账号没有工作流写权限",
            },
        )


def _definition(db: Session, workflow_key: str) -> WorkflowDefinition:
    row = db.scalar(
        select(WorkflowDefinition).where(
            WorkflowDefinition.workflow_key == workflow_key
        )
    )
    if row is None:
        raise WorkflowRegistryError(
            "workflow_definition_not_found",
            "工作流定义不存在",
            status_code=404,
        )
    return row


def _version(
    db: Session,
    definition_id: int,
    version: str,
) -> WorkflowVersion:
    row = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_definition_id == definition_id,
            WorkflowVersion.version == version,
        )
    )
    if row is None:
        raise WorkflowRegistryError(
            "workflow_version_not_found",
            "工作流版本不存在",
            status_code=404,
        )
    return row


def _definition_payload(row: WorkflowDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_key": row.workflow_key,
        "name": row.name,
        "description": row.description,
        "owner": row.owner,
        "allowed_categories": json.loads(row.allowed_categories_json),
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _version_payload(row: WorkflowVersion) -> dict[str, Any]:
    try:
        report = json.loads(row.validation_report_json)
    except json.JSONDecodeError:
        report = {
            "ok": False,
            "errors": [
                {
                    "path": "validation_report_json",
                    "code": "workflow_validation_report_invalid",
                    "message": "工作流校验报告损坏",
                }
            ],
        }
    return {
        "id": row.id,
        "workflow_definition_id": row.workflow_definition_id,
        "version": row.version,
        "status": row.status,
        "workflow_schema_version": row.workflow_schema_version,
        "canonical_hash": row.canonical_hash,
        "manifest": persisted_workflow_manifest(row),
        "validation_report": report,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def build_workflow_registry_router(
    require_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/workflows", tags=["workflow-registry"])

    @router.get("/")
    def list_workflows(
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        rows = db.scalars(
            select(WorkflowDefinition).order_by(
                WorkflowDefinition.updated_at.desc(),
                WorkflowDefinition.id.desc(),
            )
        ).all()
        return {"items": [_definition_payload(row) for row in rows]}

    @router.post("/", status_code=201)
    def create_definition(
        payload: WorkflowDefinitionWrite,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            row = create_workflow_definition(
                db,
                workflow_key=payload.workflow_key,
                name=payload.name,
                description=payload.description,
                owner=payload.owner,
                allowed_categories=payload.allowed_categories,
                created_by=user.username,
            )
            db.commit()
            db.refresh(row)
            return _definition_payload(row)
        except WorkflowRegistryError as exc:
            db.rollback()
            raise _http_error(exc) from exc

    @router.get("/{workflow_key}/versions")
    def list_versions(
        workflow_key: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            definition = _definition(db, workflow_key)
        except WorkflowRegistryError as exc:
            raise _http_error(exc) from exc
        rows = db.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_definition_id == definition.id)
            .order_by(WorkflowVersion.created_at.desc(), WorkflowVersion.id.desc())
        ).all()
        return {"items": [_version_payload(row) for row in rows]}

    @router.post("/{workflow_key}/versions", status_code=201)
    def create_version(
        workflow_key: str,
        payload: WorkflowVersionWrite,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            definition = _definition(db, workflow_key)
            row = create_workflow_version(
                db,
                definition=definition,
                version=payload.version,
                manifest=payload.manifest,
                created_by=user.username,
            )
            db.commit()
            db.refresh(row)
            return _version_payload(row)
        except WorkflowRegistryError as exc:
            db.rollback()
            raise _http_error(exc) from exc

    @router.get("/{workflow_key}/versions/{version}")
    def get_version(
        workflow_key: str,
        version: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            definition = _definition(db, workflow_key)
            return _version_payload(_version(db, definition.id, version))
        except WorkflowRegistryError as exc:
            raise _http_error(exc) from exc

    @router.post("/{workflow_key}/versions/{version}/validate")
    def validate_version(
        workflow_key: str,
        version: str,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            definition = _definition(db, workflow_key)
            row = _version(db, definition.id, version)
        except WorkflowRegistryError as exc:
            raise _http_error(exc) from exc
        if row.status not in {"validating", "blocked"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "workflow_validation_state_invalid",
                    "message": "只有 validating 或 blocked 版本可以校验",
                },
            )
        report = validate_workflow_manifest(db, persisted_workflow_manifest(row))
        row.validation_report_json = json.dumps(
            report.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if not report.ok:
            row.status = "blocked"
        db.commit()
        db.refresh(row)
        return _version_payload(row)

    @router.post("/{workflow_key}/versions/{version}/transition")
    def transition_version(
        workflow_key: str,
        version: str,
        payload: WorkflowTransitionWrite,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        _require_write(user)
        try:
            definition = _definition(db, workflow_key)
            row = _version(db, definition.id, version)
            transition_workflow_version(
                db,
                row.id,
                payload.target_status,
                actor=user.username,
            )
            db.commit()
            db.refresh(row)
            return _version_payload(row)
        except WorkflowRegistryError as exc:
            db.rollback()
            raise _http_error(exc) from exc

    return router

