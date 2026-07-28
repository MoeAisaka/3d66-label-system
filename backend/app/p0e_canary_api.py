"""Authenticated persistence and API wiring for P0-E E3 canary runs.

This module persists only the E2 orchestration snapshot. It never downloads
files, calls a model, writes Asset/EvaluationResult rows, forms Gold, or
publishes a release.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import CanaryRun as CanaryRunRecord
from .models import User, utcnow
from .p0e_canary_run import (
    APPROVALS_READY,
    CANCELLED,
    CANDIDATE_READY,
    DRAFT,
    FAILED,
    FREEZE_READY,
    HUMAN_REVIEW_READY,
    PREFLIGHT_READY,
    CanaryRunError,
    CanaryRunIssue,
    RunSnapshot,
    advance_to_approvals_ready,
    advance_to_candidate_ready,
    advance_to_freeze_ready,
    advance_to_human_review_ready,
    advance_to_preflight_ready,
    cancel_run,
    create_run,
    fail_run,
)


CanaryTransition = Literal[
    "preflight_ready",
    "approvals_ready",
    "freeze_ready",
    "candidate_ready",
    "human_review_ready",
]

_TRANSITION_PREVIOUS_STATE: dict[str, str] = {
    PREFLIGHT_READY: DRAFT,
    APPROVALS_READY: PREFLIGHT_READY,
    FREEZE_READY: APPROVALS_READY,
    CANDIDATE_READY: FREEZE_READY,
    HUMAN_REVIEW_READY: CANDIDATE_READY,
}
_TRANSITION_EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    PREFLIGHT_READY: ("xlsx_preflight",),
    APPROVALS_READY: ("approval",),
    FREEZE_READY: ("fetch_config",),
    CANDIDATE_READY: ("manifest",),
    HUMAN_REVIEW_READY: (
        "candidate_preview",
        "human_review_handoff",
    ),
}
_TERMINAL_EVIDENCE_KEYS: dict[str, str] = {
    CANCELLED: "cancellation",
    FAILED: "failure",
}


class CanaryRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["3D"]
    target_size: int = Field(ge=30, le=50, strict=True)
    seed: str = Field(min_length=1, max_length=200, strict=True)
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        strict=True,
    )

    @field_validator("seed", "display_name")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不得为空白")
        return normalized


class CanaryTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        strict=True,
    )
    evidence: dict[str, Any]


class CanaryTerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        strict=True,
    )
    reason: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("原因不得为空白")
        return normalized


class CanaryRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    display_name: str | None
    state: str
    plan: dict[str, Any]
    evidence: dict[str, Any]
    snapshot_fingerprint: str
    writes_business_database: bool
    downloads_performed: bool
    model_runs_performed: bool
    forms_gold: bool
    publishes_release: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class CanaryRunListResponse(BaseModel):
    items: list[CanaryRunResponse]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        issue = CanaryRunIssue(
            code="EVIDENCE_JSON_INVALID",
            message="请求证据无法保存为规范 JSON。",
            current_state=DRAFT,
            attempted_transition="persist",
            retryable=False,
        )
        raise CanaryRunError(issue) from exc


def _snapshot_fingerprint(
    plan: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {"plan": plan, "evidence": evidence}
        ).encode("utf-8")
    ).hexdigest()


def _raise_issue(
    *,
    status_code: int,
    code: str,
    message: str,
    current_state: str,
    attempted_transition: str,
    retryable: bool = False,
) -> None:
    issue = CanaryRunIssue(
        code=code,
        message=message,
        current_state=current_state,
        attempted_transition=attempted_transition,
        retryable=retryable,
    )
    raise HTTPException(status_code=status_code, detail=issue.as_dict())


def _raise_domain_error(error: CanaryRunError) -> None:
    raise HTTPException(
        status_code=422,
        detail=error.issue.as_dict(),
    ) from error


def _scan_for_unsafe_urls(
    node: Any,
    *,
    current_state: str,
    attempted_transition: str,
) -> None:
    if isinstance(node, str):
        try:
            parts = urlsplit(node.strip())
        except ValueError:
            return
        if parts.scheme.casefold() not in {"http", "https"}:
            return
        if parts.username is not None or parts.password is not None:
            issue = CanaryRunIssue(
                code="EVIDENCE_URL_CONTAINS_USERINFO",
                message="证据中包含带有 userinfo 的 URL，已拒绝。",
                current_state=current_state,
                attempted_transition=attempted_transition,
                retryable=False,
            )
            raise CanaryRunError(issue)
        if parts.query:
            issue = CanaryRunIssue(
                code="EVIDENCE_URL_CONTAINS_QUERY",
                message="证据中包含带有 query 参数的 URL，已拒绝。",
                current_state=current_state,
                attempted_transition=attempted_transition,
                retryable=False,
            )
            raise CanaryRunError(issue)
        if parts.fragment:
            issue = CanaryRunIssue(
                code="EVIDENCE_URL_CONTAINS_FRAGMENT",
                message="证据中包含带有 fragment 的 URL，已拒绝。",
                current_state=current_state,
                attempted_transition=attempted_transition,
                retryable=False,
            )
            raise CanaryRunError(issue)
        return
    if isinstance(node, dict):
        for value in node.values():
            _scan_for_unsafe_urls(
                value,
                current_state=current_state,
                attempted_transition=attempted_transition,
            )
        return
    if isinstance(node, (list, tuple)):
        for value in node:
            _scan_for_unsafe_urls(
                value,
                current_state=current_state,
                attempted_transition=attempted_transition,
            )


def _contains_unsafe_url(value: str) -> bool:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    return (
        parts.scheme.casefold() in {"http", "https"}
        and (
            parts.username is not None
            or parts.password is not None
            or bool(parts.query)
            or bool(parts.fragment)
        )
    )


def _sanitize_response_value(value: Any) -> Any:
    if isinstance(value, str):
        return "[redacted-url]" if _contains_unsafe_url(value) else value
    if isinstance(value, dict):
        return {
            key: _sanitize_response_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_response_value(item) for item in value]
    return value


def _persisted_snapshot(record: CanaryRunRecord) -> RunSnapshot:
    try:
        plan = json.loads(record.plan_json)
        evidence = json.loads(record.evidence_json)
        stored_snapshot = json.loads(record.snapshot_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        _raise_issue(
            status_code=500,
            code="CANARY_RUN_PERSISTENCE_INVALID",
            message="已保存的 CanaryRun JSON 无法验证。",
            current_state=record.current_state,
            attempted_transition="load",
        )
    if not isinstance(plan, dict) or not isinstance(evidence, dict):
        _raise_issue(
            status_code=500,
            code="CANARY_RUN_PERSISTENCE_INVALID",
            message="已保存的 CanaryRun 结构无法验证。",
            current_state=record.current_state,
            attempted_transition="load",
        )
    computed_fingerprint = _snapshot_fingerprint(plan, evidence)
    snapshot = RunSnapshot(
        run_id=record.run_id,
        snapshot_fingerprint=computed_fingerprint,
        state=record.current_state,
        plan=plan,
        evidence=evidence,
        writes_business_database=False,
        downloads_performed=False,
        model_runs_performed=False,
        forms_gold=False,
        publishes_release=False,
    )
    if (
        record.snapshot_fingerprint != computed_fingerprint
        or _canonical_json(stored_snapshot)
        != _canonical_json(snapshot.as_dict())
    ):
        _raise_issue(
            status_code=500,
            code="CANARY_RUN_PERSISTENCE_INVALID",
            message="已保存的 CanaryRun 快照指纹或不变量无法验证。",
            current_state=record.current_state,
            attempted_transition="load",
        )
    return snapshot


def _response(record: CanaryRunRecord) -> CanaryRunResponse:
    snapshot = _persisted_snapshot(record)
    return CanaryRunResponse(
        run_id=record.run_id,
        display_name=_sanitize_response_value(record.display_name),
        state=snapshot.state,
        plan=_sanitize_response_value(snapshot.plan),
        evidence=_sanitize_response_value(snapshot.evidence),
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        writes_business_database=False,
        downloads_performed=False,
        model_runs_performed=False,
        forms_gold=False,
        publishes_release=False,
        created_by=_sanitize_response_value(record.created_by),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _get_record(db: Session, run_id: str) -> CanaryRunRecord:
    record = db.get(CanaryRunRecord, run_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CANARY_RUN_NOT_FOUND",
                "message": "CanaryRun 不存在。",
            },
        )
    return record


def _apply_transition(
    snapshot: RunSnapshot,
    transition: str,
    evidence: dict[str, Any],
) -> RunSnapshot:
    _scan_for_unsafe_urls(
        evidence,
        current_state=snapshot.state,
        attempted_transition=transition,
    )
    if transition == PREFLIGHT_READY:
        return advance_to_preflight_ready(
            snapshot,
            xlsx_preflight=evidence,
        )
    if transition == APPROVALS_READY:
        return advance_to_approvals_ready(
            snapshot,
            approval_artifact=evidence,
        )
    if transition == FREEZE_READY:
        return advance_to_freeze_ready(
            snapshot,
            fetch_config=evidence,
        )
    if transition == CANDIDATE_READY:
        return advance_to_candidate_ready(
            snapshot,
            manifest=evidence,
        )
    if transition == HUMAN_REVIEW_READY:
        expected_keys = {
            "candidate_preview",
            "human_review_handoff",
        }
        if set(evidence) != expected_keys:
            issue = CanaryRunIssue(
                code="TRANSITION_EVIDENCE_SHAPE_INVALID",
                message=(
                    "human_review_ready 证据必须且只能包含 "
                    "candidate_preview 与 human_review_handoff。"
                ),
                current_state=snapshot.state,
                attempted_transition=transition,
                retryable=False,
            )
            raise CanaryRunError(issue)
        return advance_to_human_review_ready(
            snapshot,
            candidate_preview=evidence["candidate_preview"],
            human_review_handoff=evidence["human_review_handoff"],
        )
    raise AssertionError(f"unsupported transition: {transition}")


def _predecessor_snapshot(
    snapshot: RunSnapshot,
    transition: str,
) -> RunSnapshot:
    evidence = dict(snapshot.evidence)
    for key in _TRANSITION_EVIDENCE_KEYS[transition]:
        evidence.pop(key, None)
    return RunSnapshot(
        run_id=snapshot.run_id,
        snapshot_fingerprint=_snapshot_fingerprint(
            snapshot.plan,
            evidence,
        ),
        state=_TRANSITION_PREVIOUS_STATE[transition],
        plan=snapshot.plan,
        evidence=evidence,
        writes_business_database=False,
        downloads_performed=False,
        model_runs_performed=False,
        forms_gold=False,
        publishes_release=False,
    )


def _is_idempotent_transition(
    snapshot: RunSnapshot,
    transition: str,
    evidence: dict[str, Any],
) -> bool:
    if snapshot.state != transition:
        return False
    replayed = _apply_transition(
        _predecessor_snapshot(snapshot, transition),
        transition,
        evidence,
    )
    return (
        replayed.snapshot_fingerprint
        == snapshot.snapshot_fingerprint
        and replayed.evidence == snapshot.evidence
    )


def _save_snapshot(
    db: Session,
    record: CanaryRunRecord,
    snapshot: RunSnapshot,
    *,
    expected_fingerprint: str,
) -> CanaryRunRecord | None:
    result = db.execute(
        update(CanaryRunRecord)
        .where(
            CanaryRunRecord.run_id == record.run_id,
            CanaryRunRecord.snapshot_fingerprint
            == expected_fingerprint,
        )
        .values(
            current_state=snapshot.state,
            plan_json=_canonical_json(snapshot.plan),
            evidence_json=_canonical_json(snapshot.evidence),
            snapshot_json=_canonical_json(snapshot.as_dict()),
            snapshot_fingerprint=snapshot.snapshot_fingerprint,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        db.expire_all()
        return db.get(CanaryRunRecord, record.run_id)
    db.commit()
    db.expire_all()
    return db.get(CanaryRunRecord, record.run_id)


def create_canary_run(
    db: Session,
    user: User,
    payload: CanaryRunCreateRequest,
) -> CanaryRunResponse:
    try:
        _scan_for_unsafe_urls(
            {
                "seed": payload.seed,
                "display_name": payload.display_name,
            },
            current_state=DRAFT,
            attempted_transition="create_run",
        )
        snapshot = create_run(
            payload.domain,
            target_size=payload.target_size,
            seed=payload.seed,
        )
    except CanaryRunError as exc:
        _raise_domain_error(exc)

    plan_json = _canonical_json(snapshot.plan)
    existing = db.get(CanaryRunRecord, snapshot.run_id)
    if existing is not None:
        if (
            existing.plan_json != plan_json
            or existing.display_name != payload.display_name
        ):
            _raise_issue(
                status_code=409,
                code="CANARY_RUN_IDEMPOTENCY_DRIFT",
                message="相同 run_id 已存在不同计划或显示名称。",
                current_state=existing.current_state,
                attempted_transition="create_run",
            )
        return _response(existing)

    record = CanaryRunRecord(
        run_id=snapshot.run_id,
        display_name=payload.display_name,
        current_state=snapshot.state,
        plan_json=plan_json,
        evidence_json=_canonical_json(snapshot.evidence),
        snapshot_json=_canonical_json(snapshot.as_dict()),
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        created_by=user.username,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(CanaryRunRecord, snapshot.run_id)
        if (
            existing is None
            or existing.plan_json != plan_json
            or existing.display_name != payload.display_name
        ):
            _raise_issue(
                status_code=409,
                code="CANARY_RUN_IDEMPOTENCY_DRIFT",
                message="并发创建遇到不同的 CanaryRun 内容。",
                current_state=DRAFT,
                attempted_transition="create_run",
                retryable=True,
            )
        return _response(existing)
    db.refresh(record)
    return _response(record)


def transition_canary_run(
    db: Session,
    run_id: str,
    transition: str,
    payload: CanaryTransitionRequest,
) -> CanaryRunResponse:
    record = _get_record(db, run_id)
    snapshot = _persisted_snapshot(record)
    try:
        if _is_idempotent_transition(
            snapshot,
            transition,
            payload.evidence,
        ):
            return _response(record)
    except CanaryRunError as exc:
        _raise_domain_error(exc)

    if (
        payload.expected_snapshot_fingerprint
        != snapshot.snapshot_fingerprint
    ):
        _raise_issue(
            status_code=409,
            code="CANARY_RUN_SNAPSHOT_STALE",
            message="expected_snapshot_fingerprint 已过期。",
            current_state=snapshot.state,
            attempted_transition=transition,
            retryable=True,
        )
    if snapshot.state == transition:
        _raise_issue(
            status_code=409,
            code="CANARY_RUN_EVIDENCE_CONFLICT",
            message="该状态已由不同的门禁证据写入。",
            current_state=snapshot.state,
            attempted_transition=transition,
        )

    try:
        advanced = _apply_transition(
            snapshot,
            transition,
            payload.evidence,
        )
    except CanaryRunError as exc:
        _raise_domain_error(exc)
    saved = _save_snapshot(
        db,
        record,
        advanced,
        expected_fingerprint=payload.expected_snapshot_fingerprint,
    )
    if saved is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CANARY_RUN_NOT_FOUND",
                "message": "CanaryRun 不存在。",
            },
        )
    latest = _persisted_snapshot(saved)
    try:
        if _is_idempotent_transition(
            latest,
            transition,
            payload.evidence,
        ):
            return _response(saved)
    except CanaryRunError as exc:
        _raise_domain_error(exc)
    _raise_issue(
        status_code=409,
        code="CANARY_RUN_SNAPSHOT_STALE",
        message="并发更新使 expected_snapshot_fingerprint 失效。",
        current_state=latest.state,
        attempted_transition=transition,
        retryable=True,
    )


def _is_idempotent_terminal(
    snapshot: RunSnapshot,
    target_state: str,
    reason: str,
) -> bool:
    evidence_key = _TERMINAL_EVIDENCE_KEYS[target_state]
    return (
        snapshot.state == target_state
        and snapshot.evidence.get(evidence_key) == {"reason": reason}
    )


def terminate_canary_run(
    db: Session,
    run_id: str,
    target_state: str,
    payload: CanaryTerminalRequest,
) -> CanaryRunResponse:
    record = _get_record(db, run_id)
    snapshot = _persisted_snapshot(record)
    try:
        _scan_for_unsafe_urls(
            payload.reason,
            current_state=snapshot.state,
            attempted_transition=target_state,
        )
    except CanaryRunError as exc:
        _raise_domain_error(exc)
    if _is_idempotent_terminal(
        snapshot,
        target_state,
        payload.reason,
    ):
        return _response(record)
    if (
        payload.expected_snapshot_fingerprint
        != snapshot.snapshot_fingerprint
    ):
        _raise_issue(
            status_code=409,
            code="CANARY_RUN_SNAPSHOT_STALE",
            message="expected_snapshot_fingerprint 已过期。",
            current_state=snapshot.state,
            attempted_transition=target_state,
            retryable=True,
        )
    if snapshot.state == target_state:
        _raise_issue(
            status_code=409,
            code="CANARY_RUN_EVIDENCE_CONFLICT",
            message="终止状态已由不同原因写入。",
            current_state=snapshot.state,
            attempted_transition=target_state,
        )
    try:
        terminal_snapshot = (
            cancel_run(snapshot, reason=payload.reason)
            if target_state == CANCELLED
            else fail_run(snapshot, reason=payload.reason)
        )
    except CanaryRunError as exc:
        _raise_domain_error(exc)
    saved = _save_snapshot(
        db,
        record,
        terminal_snapshot,
        expected_fingerprint=payload.expected_snapshot_fingerprint,
    )
    if saved is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CANARY_RUN_NOT_FOUND",
                "message": "CanaryRun 不存在。",
            },
        )
    latest = _persisted_snapshot(saved)
    if _is_idempotent_terminal(
        latest,
        target_state,
        payload.reason,
    ):
        return _response(saved)
    _raise_issue(
        status_code=409,
        code="CANARY_RUN_SNAPSHOT_STALE",
        message="并发更新使 expected_snapshot_fingerprint 失效。",
        current_state=latest.state,
        attempted_transition=target_state,
        retryable=True,
    )


def build_canary_router(
    require_user: Callable[..., User],
) -> APIRouter:
    router = APIRouter(prefix="/api/canary-runs", tags=["canary-runs"])

    @router.post("", response_model=CanaryRunResponse)
    def create_endpoint(
        payload: CanaryRunCreateRequest,
        user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> CanaryRunResponse:
        return create_canary_run(db, user, payload)

    @router.get("", response_model=CanaryRunListResponse)
    def list_endpoint(
        limit: int = Query(default=100, ge=1, le=500),
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> CanaryRunListResponse:
        records = db.scalars(
            select(CanaryRunRecord)
            .order_by(
                CanaryRunRecord.updated_at.desc(),
                CanaryRunRecord.run_id.asc(),
            )
            .limit(limit)
        ).all()
        return CanaryRunListResponse(
            items=[_response(record) for record in records]
        )

    @router.get("/{run_id}", response_model=CanaryRunResponse)
    def detail_endpoint(
        run_id: str,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> CanaryRunResponse:
        return _response(_get_record(db, run_id))

    @router.post(
        "/{run_id}/transitions/{transition}",
        response_model=CanaryRunResponse,
    )
    def transition_endpoint(
        run_id: str,
        transition: CanaryTransition,
        payload: CanaryTransitionRequest,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> CanaryRunResponse:
        return transition_canary_run(
            db,
            run_id,
            transition,
            payload,
        )

    @router.post("/{run_id}/cancel", response_model=CanaryRunResponse)
    def cancel_endpoint(
        run_id: str,
        payload: CanaryTerminalRequest,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> CanaryRunResponse:
        return terminate_canary_run(
            db,
            run_id,
            CANCELLED,
            payload,
        )

    @router.post("/{run_id}/fail", response_model=CanaryRunResponse)
    def fail_endpoint(
        run_id: str,
        payload: CanaryTerminalRequest,
        _user: User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> CanaryRunResponse:
        return terminate_canary_run(
            db,
            run_id,
            FAILED,
            payload,
        )

    return router
