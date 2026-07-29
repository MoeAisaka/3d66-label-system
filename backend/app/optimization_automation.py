from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .database import session_scope
from .models import (
    AutomationOptimizationRun,
    AutomationPolicy,
    OptimizationCaseQueue,
)


@dataclass(frozen=True)
class AutomationAdapterResult:
    candidates: list[dict[str, Any]]
    regression: dict[str, Any]
    actual_cost_micros: int = 0


class OptimizationAdapter(Protocol):
    def optimize(
        self,
        *,
        frozen_input: dict[str, Any],
        max_candidates: int,
    ) -> AutomationAdapterResult: ...


class DeterministicOptimizationAdapter:
    """A test-only adapter. It never performs network or model calls."""

    def __init__(self, result: AutomationAdapterResult):
        self.result = result

    def optimize(
        self,
        *,
        frozen_input: dict[str, Any],
        max_candidates: int,
    ) -> AutomationAdapterResult:
        del frozen_input
        if len(self.result.candidates) > max_candidates:
            raise ValueError("测试适配器返回的候选数超过策略上限")
        return self.result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _policy_payload(policy: AutomationPolicy) -> dict[str, Any]:
    return {
        "enabled": policy.enabled,
        "dry_run": policy.dry_run,
        "revision": policy.revision,
        "case_threshold": policy.case_threshold,
        "immediate_severities": json.loads(
            policy.immediate_severities_json or "[]"
        ),
        "daily_budget_micros": policy.daily_budget_micros,
        "cooldown_seconds": policy.cooldown_seconds,
        "max_candidates": policy.max_candidates,
        "lease_seconds": policy.lease_seconds,
        "max_attempts": policy.max_attempts,
        "base_retry_seconds": policy.base_retry_seconds,
    }


def _budget_used_today(db: Session, now: datetime) -> int:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(AutomationOptimizationRun.actual_cost_micros),
                    0,
                )
            ).where(AutomationOptimizationRun.created_at >= day_start)
        )
        or 0
    )


def recover_expired_leases(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    current = now or _now()
    result = db.execute(
        update(OptimizationCaseQueue)
        .where(
            OptimizationCaseQueue.status == "processing",
            OptimizationCaseQueue.lease_expires_at.is_not(None),
            OptimizationCaseQueue.lease_expires_at <= current,
        )
        .values(
            status="failed",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=current,
            last_error="lease_expired",
            updated_at=current,
        )
    )
    return int(result.rowcount or 0)


def consume_optimization_queue_once(
    db: Session,
    *,
    worker_id: str,
    adapter: OptimizationAdapter | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    policy = db.get(AutomationPolicy, 1)
    if policy is None:
        policy = AutomationPolicy(id=1)
        db.add(policy)
        db.flush()
    recovered = recover_expired_leases(db, now=current)
    if not policy.enabled:
        return {
            "status": "disabled",
            "dry_run": policy.dry_run,
            "recovered_leases": recovered,
        }

    available = db.scalars(
        select(OptimizationCaseQueue)
        .where(
            OptimizationCaseQueue.status.in_(["pending", "failed"]),
            OptimizationCaseQueue.attempt_count < policy.max_attempts,
            or_(
                OptimizationCaseQueue.next_attempt_at.is_(None),
                OptimizationCaseQueue.next_attempt_at <= current,
            ),
            or_(
                OptimizationCaseQueue.lease_expires_at.is_(None),
                OptimizationCaseQueue.lease_expires_at <= current,
            ),
        )
        .order_by(
            OptimizationCaseQueue.created_at.asc(),
            OptimizationCaseQueue.id.asc(),
        )
    ).all()
    if not available:
        return {"status": "idle", "recovered_leases": recovered}

    immediate = set(json.loads(policy.immediate_severities_json or "[]"))
    trigger_case = next(
        (case for case in available if case.severity in immediate),
        None,
    )
    prompt_version = (
        trigger_case.prompt_version if trigger_case else available[0].prompt_version
    )
    same_prompt = [
        case for case in available if case.prompt_version == prompt_version
    ]
    if trigger_case is None and len(same_prompt) < policy.case_threshold:
        return {
            "status": "threshold_wait",
            "available": len(same_prompt),
            "required": policy.case_threshold,
            "recovered_leases": recovered,
        }
    if (
        trigger_case is None
        and policy.last_triggered_at is not None
        and _aware(policy.last_triggered_at)
        + timedelta(seconds=policy.cooldown_seconds)
        > current
    ):
        return {
            "status": "cooldown",
            "cooldown_until": _aware(policy.last_triggered_at)
            + timedelta(seconds=policy.cooldown_seconds),
            "recovered_leases": recovered,
        }

    selected = (
        same_prompt[: policy.case_threshold]
        if trigger_case is None
        else same_prompt[: max(1, policy.case_threshold)]
    )
    estimated_cost = 0 if policy.dry_run else len(selected) * 1000
    used = _budget_used_today(db, current)
    if (
        not policy.dry_run
        and (
            policy.daily_budget_micros <= 0
            or used + estimated_cost > policy.daily_budget_micros
        )
    ):
        append_audit_event(
            db,
            category="automation",
            action="budget_blocked",
            subject_type="automation_policy",
            subject_id=policy.id,
            actor=worker_id,
            payload={
                "used_micros": used,
                "estimated_micros": estimated_cost,
                "budget_micros": policy.daily_budget_micros,
            },
        )
        return {
            "status": "budget_blocked",
            "used_micros": used,
            "estimated_micros": estimated_cost,
            "budget_micros": policy.daily_budget_micros,
            "recovered_leases": recovered,
        }

    lease_token = uuid.uuid4().hex
    lease_until = current + timedelta(seconds=policy.lease_seconds)
    selected_ids = [case.id for case in selected]
    claimed = db.execute(
        update(OptimizationCaseQueue)
        .where(
            OptimizationCaseQueue.id.in_(selected_ids),
            OptimizationCaseQueue.status.in_(["pending", "failed"]),
            or_(
                OptimizationCaseQueue.lease_expires_at.is_(None),
                OptimizationCaseQueue.lease_expires_at <= current,
            ),
        )
        .values(
            status="processing",
            lease_owner=worker_id,
            lease_token=lease_token,
            lease_expires_at=lease_until,
            attempt_count=OptimizationCaseQueue.attempt_count + 1,
            next_attempt_at=None,
            last_error="",
            updated_at=current,
        )
    )
    if int(claimed.rowcount or 0) != len(selected_ids):
        db.rollback()
        return {"status": "lease_conflict", "claimed": int(claimed.rowcount or 0)}

    frozen_cases = [
        {
            "id": case.id,
            "idempotency_key": case.idempotency_key,
            "source_type": case.source_type,
            "prompt_version": case.prompt_version,
            "severity": case.severity,
            "case": json.loads(case.case_json),
        }
        for case in selected
    ]
    frozen_input = {
        "schema_version": "automation-input-v1",
        "prompt_version": prompt_version,
        "policy": _policy_payload(policy),
        "cases": frozen_cases,
    }
    run_key = hashlib.sha256(
        canonical_json(
            {
                "policy_revision": policy.revision,
                "case_ids": selected_ids,
                "prompt_version": prompt_version,
            }
        ).encode("utf-8")
    ).hexdigest()
    existing = db.scalar(
        select(AutomationOptimizationRun).where(
            AutomationOptimizationRun.run_key == run_key
        )
    )
    if existing is not None:
        db.execute(
            update(OptimizationCaseQueue)
            .where(OptimizationCaseQueue.id.in_(selected_ids))
            .values(
                status="batched",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                automation_run_id=existing.id,
                updated_at=current,
            )
        )
        return {"status": "already_planned", "run_id": existing.id}

    run = AutomationOptimizationRun(
        run_key=run_key,
        base_prompt_version=prompt_version,
        policy_revision=policy.revision,
        status=(
            "planned"
            if policy.dry_run
            else ("running" if adapter is not None else "awaiting_executor")
        ),
        dry_run=policy.dry_run,
        trigger_reason=(
            f"immediate:{trigger_case.severity}"
            if trigger_case is not None
            else "case_threshold"
        ),
        case_ids_json=canonical_json(selected_ids),
        frozen_input_json=canonical_json(frozen_input),
        estimated_cost_micros=estimated_cost,
        created_by=worker_id,
    )
    db.add(run)
    db.flush()
    db.execute(
        update(OptimizationCaseQueue)
        .where(
            OptimizationCaseQueue.id.in_(selected_ids),
            OptimizationCaseQueue.lease_token == lease_token,
        )
        .values(
            status="batched" if policy.dry_run or adapter is None else "processing",
            lease_owner=None if policy.dry_run or adapter is None else worker_id,
            lease_token=None if policy.dry_run or adapter is None else lease_token,
            lease_expires_at=None if policy.dry_run or adapter is None else lease_until,
            automation_run_id=run.id,
            updated_at=current,
        )
    )
    policy.last_triggered_at = current
    append_audit_event(
        db,
        category="automation",
        action="run_planned",
        subject_type="automation_optimization_run",
        subject_id=run.id,
        actor=worker_id,
        payload={
            "dry_run": policy.dry_run,
            "trigger_reason": run.trigger_reason,
            "case_ids": selected_ids,
            "estimated_cost_micros": estimated_cost,
        },
        event_key=f"automation-run-planned:{run.run_key}",
    )
    if policy.dry_run or adapter is None:
        return {
            "status": run.status,
            "run_id": run.id,
            "dry_run": policy.dry_run,
            "case_count": len(selected_ids),
            "recovered_leases": recovered,
        }

    try:
        result = adapter.optimize(
            frozen_input=frozen_input,
            max_candidates=policy.max_candidates,
        )
        if result.actual_cost_micros < 0:
            raise ValueError("实际成本不能为负数")
        if (
            policy.daily_budget_micros <= 0
            or used + result.actual_cost_micros
            > policy.daily_budget_micros
        ):
            raise RuntimeError("adapter_result_exceeds_budget")
        run.result_json = canonical_json(
            {
                "candidates": result.candidates,
                "regression": result.regression,
                "release_requires_human_review": True,
                "publishes_automatically": False,
            }
        )
        run.candidate_count = len(result.candidates)
        run.actual_cost_micros = result.actual_cost_micros
        run.status = "awaiting_release_review"
        run.finished_at = current
        db.execute(
            update(OptimizationCaseQueue)
            .where(
                OptimizationCaseQueue.id.in_(selected_ids),
                OptimizationCaseQueue.automation_run_id == run.id,
            )
            .values(
                status="completed",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                updated_at=current,
            )
        )
        append_audit_event(
            db,
            category="automation",
            action="awaiting_release_review",
            subject_type="automation_optimization_run",
            subject_id=run.id,
            actor=worker_id,
            payload={
                "candidate_count": run.candidate_count,
                "actual_cost_micros": run.actual_cost_micros,
                "auto_publish": False,
            },
            event_key=f"automation-run-reviewed:{run.run_key}",
        )
        return {
            "status": run.status,
            "run_id": run.id,
            "candidate_count": run.candidate_count,
        }
    except Exception as exc:
        safe_error = str(exc)[:300]
        run.status = "failed"
        run.error_message = safe_error
        run.finished_at = current
        retry_at = current + timedelta(
            seconds=policy.base_retry_seconds
            * (2 ** max(0, max(case.attempt_count for case in selected) - 1))
        )
        db.execute(
            update(OptimizationCaseQueue)
            .where(
                OptimizationCaseQueue.id.in_(selected_ids),
                OptimizationCaseQueue.automation_run_id == run.id,
            )
            .values(
                status="failed",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                next_attempt_at=retry_at,
                last_error=safe_error,
                updated_at=current,
            )
        )
        append_audit_event(
            db,
            category="automation",
            action="run_failed",
            subject_type="automation_optimization_run",
            subject_id=run.id,
            actor=worker_id,
            payload={"error": safe_error, "retry_at": retry_at.isoformat()},
            event_key=f"automation-run-failed:{run.run_key}",
        )
        return {"status": "failed", "run_id": run.id, "retry_at": retry_at}


def optimization_worker_tick(worker_id: str) -> dict[str, Any]:
    """Safe worker hook: default adapter is intentionally absent."""
    with session_scope() as db:
        return consume_optimization_queue_once(
            db,
            worker_id=worker_id,
            adapter=None,
        )
