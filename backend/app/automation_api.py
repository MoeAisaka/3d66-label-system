"""Evidence-first API for global automation lanes and candidate review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import append_audit_event
from .database import get_db
from .models import (
    AutomationBatch,
    AutomationBatchCase,
    AutomationLanePolicy,
    AutomationOptimizationRun,
    AutomationPolicy,
    OptimizationCaseQueue,
)


class CandidateDecisionRequest(BaseModel):
    decision: str
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_decision(self) -> "CandidateDecisionRequest":
        if self.decision not in {"approved", "rejected"}:
            raise ValueError("候选决策只能是 approved 或 rejected")
        return self


def _json(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError:
        return fallback
    return parsed


def _batch_payload(batch: AutomationBatch, db: Session) -> dict[str, Any]:
    cases = db.scalars(
        select(AutomationBatchCase).where(AutomationBatchCase.batch_id == batch.id)
    ).all()
    return {
        "id": batch.id,
        "batch_key": batch.batch_key,
        "category_key": batch.category_key,
        "pipeline_kind": batch.pipeline_kind,
        "generation": batch.generation,
        "mechanism_fingerprint": batch.mechanism_fingerprint,
        "route_key": batch.route_key,
        "case_ids": [case.case_id for case in cases],
        "case_set_hash": batch.case_set_hash,
        "frozen_policy": _json(batch.frozen_policy_json, {}),
        "status": batch.status,
        "trigger_reason": batch.trigger_reason,
        "error_code": batch.error_code,
        "error_message": batch.error_message,
        "created_at": batch.created_at,
        "started_at": batch.started_at,
        "finished_at": batch.finished_at,
    }


def build_automation_router(
    current_user: Callable[..., Any],
    admin_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/automation", tags=["automation"])

    @router.get("/overview")
    def overview(
        _user: Any = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        policy = db.get(AutomationPolicy, 1)
        lanes = db.scalars(select(AutomationLanePolicy).order_by(AutomationLanePolicy.category_key)).all()
        active_batches = db.scalar(
            select(func.count()).select_from(AutomationBatch).where(
                AutomationBatch.status.in_(["queued", "leased", "processing", "awaiting_release_review"])
            )
        ) or 0
        pending_candidates = db.scalar(
            select(func.count()).select_from(AutomationOptimizationRun).where(
                AutomationOptimizationRun.status == "awaiting_release_review"
            )
        ) or 0
        historical = db.scalar(
            select(func.count()).select_from(OptimizationCaseQueue).where(
                OptimizationCaseQueue.admission_state == "historical_audit"
            )
        ) or 0
        return {
            "policy": {
                "enabled": bool(policy and policy.enabled),
                "dry_run": bool(policy.dry_run) if policy else True,
                "daily_budget_micros": int(policy.daily_budget_micros) if policy else 0,
            },
            "lanes": [_lane_payload(lane) for lane in lanes],
            "active_batches": int(active_batches),
            "historical_audit": int(historical),
            "pending_candidates": int(pending_candidates),
            "auto_publish_enabled": False,
            "stock_rerun_enabled": False,
        }

    @router.get("/lanes")
    def lanes(
        pipeline_kind: str | None = None,
        _user: Any = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        query = select(AutomationLanePolicy).order_by(AutomationLanePolicy.category_key)
        if pipeline_kind is not None:
            if pipeline_kind not in {"incremental", "baseline"}:
                raise HTTPException(status_code=422, detail="链路类型无效")
            query = query.where(AutomationLanePolicy.pipeline_kind == pipeline_kind)
        return {"items": [_lane_payload(lane) for lane in db.scalars(query).all()]}

    @router.get("/batches/{batch_id}")
    def batch_detail(
        batch_id: int,
        _user: Any = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        batch = db.get(AutomationBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="批次不存在")
        return _batch_payload(batch, db)

    @router.get("/historical-audit")
    def historical_audit(
        _user: Any = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        cases = db.scalars(
            select(OptimizationCaseQueue)
            .where(OptimizationCaseQueue.admission_state == "historical_audit")
            .order_by(OptimizationCaseQueue.created_at)
            .limit(500)
        ).all()
        return {"items": [_case_payload(case) for case in cases], "bulk_admit_enabled": False}

    @router.get("/candidates")
    def candidates(
        _user: Any = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        runs = db.scalars(
            select(AutomationOptimizationRun)
            .where(AutomationOptimizationRun.status == "awaiting_release_review")
            .order_by(AutomationOptimizationRun.created_at.desc())
            .limit(200)
        ).all()
        return {"items": [_run_payload(run) for run in runs], "auto_publish_enabled": False}

    @router.post("/candidates/{candidate_id}/decision")
    def decide_candidate(
        candidate_id: int,
        payload: CandidateDecisionRequest,
        user: Any = Depends(admin_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        run = db.get(AutomationOptimizationRun, candidate_id)
        if run is None:
            raise HTTPException(status_code=404, detail="候选不存在")
        if run.status != "awaiting_release_review":
            raise HTTPException(status_code=409, detail="候选尚未进入人工二审")
        result = _json(run.result_json, {})
        result["human_decision"] = payload.decision
        result["human_note"] = payload.note
        result["decided_by"] = getattr(user, "username", "admin")
        result["decided_at"] = datetime.now(timezone.utc).isoformat()
        run.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        run.status = "succeeded" if payload.decision == "approved" else "failed"
        run.finished_at = datetime.now(timezone.utc)
        append_audit_event(
            db, category="automation", action="candidate_decision",
            subject_type="automation_optimization_run", subject_id=run.id,
            actor=getattr(user, "username", "admin"),
            payload={"decision": payload.decision, "note": payload.note, "auto_publish": False},
            event_key=f"automation:candidate-decision:{run.id}:{payload.decision}",
        )
        db.commit()
        return {"id": run.id, "decision": payload.decision, "auto_publish": False, "stock_rerun": False}

    return router


def _lane_payload(lane: AutomationLanePolicy) -> dict[str, Any]:
    return {
        "id": lane.id,
        "category_key": lane.category_key,
        "pipeline_kind": lane.pipeline_kind,
        "generation": lane.generation,
        "status": lane.status,
        "mechanism_fingerprint": lane.mechanism_fingerprint,
        "mechanism_fingerprint_prefix": lane.mechanism_fingerprint[:12],
        "case_threshold": lane.case_threshold,
        "min_batch_size": lane.min_batch_size,
        "daily_budget_micros": lane.daily_budget_micros,
        "golden_sets": {
            "target_error": lane.target_sample_set_id,
            "stable_control": lane.stable_control_set_id,
            "blind_holdout": lane.blind_holdout_set_id,
        },
    }


def _case_payload(case: OptimizationCaseQueue) -> dict[str, Any]:
    return {
        "id": case.id,
        "category_key": case.category_key,
        "pipeline_kind": case.pipeline_kind,
        "admission_state": case.admission_state,
        "source_type": case.source_type,
        "created_at": case.created_at,
    }


def _run_payload(run: AutomationOptimizationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "category_key": run.category_key,
        "status": run.status,
        "dry_run": run.dry_run,
        "candidate_count": run.candidate_count,
        "estimated_cost_micros": run.estimated_cost_micros,
        "actual_cost_micros": run.actual_cost_micros,
        "result": _json(run.result_json, {}),
        "auto_publish_enabled": False,
    }

