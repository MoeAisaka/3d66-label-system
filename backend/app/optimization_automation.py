from __future__ import annotations

import hashlib
import json
import uuid
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select, update, text
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .database import session_scope
from .models import (
    AutomationOptimizationRun,
    AutomationBudgetDay,
    AutomationPolicy,
    Asset,
    ModelConfig,
    OptimizerConfig,
    OptimizationCaseQueue,
    PromptVersion,
    SampleSet,
    SamplingPolicy,
    StrategyBundle,
)
from .doubao import response_usage
from .optimizer import generate_automation_candidates
from .regression import latest_review_for_result, reviewed_truth_snapshot
from .strategy_bundle import get_or_create_bundle


@dataclass(frozen=True)
class AutomationAdapterResult:
    candidates: list[dict[str, Any]]
    regression: dict[str, Any]
    actual_cost_micros: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class OptimizationAdapter(Protocol):
    def optimize(
        self,
        *,
        frozen_input: dict[str, Any],
        max_candidates: int,
    ) -> AutomationAdapterResult: ...

    def estimate_cost_micros(self, *, frozen_input: dict[str, Any]) -> int: ...


class RealOptimizationAdapter:
    """Production adapter backed by the configured optimizer model only."""

    def __init__(
        self, *, config: Any, base_prompt: PromptVersion | None = None
    ):
        self.config = config
        self.base_prompt = base_prompt

    def bind_base_prompt(self, db: Session, *, version: str) -> None:
        prompt = db.scalar(
            select(PromptVersion).where(
                PromptVersion.stage == "B",
                PromptVersion.version == version,
            )
        )
        if prompt is None:
            raise ValueError("优化案例对应的 B 提示词不存在")
        self.base_prompt = prompt

    def estimate_cost_micros(self, *, frozen_input: dict[str, Any]) -> int:
        if (
            self.config.max_input_tokens <= 0
            or self.config.input_micros_per_million_tokens <= 0
            or self.config.output_micros_per_million_tokens <= 0
        ):
            raise ValueError("优化模型缺少输入上限或计价配置")
        calls = 2
        input_tokens = calls * self.config.max_input_tokens
        output_tokens = calls * self.config.max_tokens
        return _token_cost(
            input_tokens,
            output_tokens,
            self.config.input_micros_per_million_tokens,
            self.config.output_micros_per_million_tokens,
        )

    def optimize(
        self,
        *,
        frozen_input: dict[str, Any],
        max_candidates: int,
    ) -> AutomationAdapterResult:
        if self.base_prompt is None:
            raise ValueError("优化执行器尚未绑定基础提示词")
        generated = asyncio.run(
            generate_automation_candidates(
                config=self.config,
                base_prompt=self.base_prompt,
                frozen_input=frozen_input,
                max_candidates=max_candidates,
            )
        )
        return AutomationAdapterResult(
            candidates=generated.candidates,
            regression={
                "status": "awaiting_results",
                "roles": ["target_error", "stable_control", "blind_holdout"],
            },
            actual_cost_micros=_token_cost(
                generated.input_tokens,
                generated.output_tokens,
                self.config.input_micros_per_million_tokens,
                self.config.output_micros_per_million_tokens,
            ),
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
            total_tokens=generated.total_tokens,
        )

    def prepare_regression_binding(
        self, db: Session, *, base_prompt: PromptVersion
    ) -> dict[str, Any]:
        sample_sets = db.scalars(
            select(SampleSet)
            .where(SampleSet.kind == "golden", SampleSet.status == "locked")
            .order_by(SampleSet.id.desc())
        ).all()
        for sample_set in sample_sets:
            target = stable = blind = None
            for item in sample_set.items:
                review = latest_review_for_result(item.source_result)
                if review is None or review.decision not in {"approved", "corrected"}:
                    continue
                if target is None and review.decision == "corrected":
                    target = item
                elif stable is None and review.decision == "approved":
                    stable = item
                elif blind is None:
                    blind = item
            if target is not None and stable is not None and blind is not None:
                baseline = db.scalar(
                    select(StrategyBundle)
                    .where(StrategyBundle.prompt_b_version == base_prompt.version)
                    .order_by(StrategyBundle.id.desc())
                )
                if baseline is None:
                    continue
                return {
                    "sample_set_id": sample_set.id,
                    "baseline_strategy_bundle_id": baseline.id,
                    "samples": [
                        {"sample_item_id": target.id, "role": "target_error"},
                        {"sample_item_id": stable.id, "role": "stable_control"},
                        {"sample_item_id": blind.id, "role": "blind_holdout"},
                    ],
                }
        raise ValueError("没有可用于三角色配对回归的锁定黄金样本")

    def materialize(
        self,
        db: Session,
        *,
        run: AutomationOptimizationRun,
        result: AutomationAdapterResult,
        worker_id: str,
    ) -> dict[str, list[int]]:
        from .main import (
            PairedRegressionCreateRequest,
            PairedRegressionSampleRequest,
            _create_paired_regression,
        )

        binding = json.loads(run.frozen_input_json).get("regression_binding")
        if not isinstance(binding, dict):
            raise ValueError("自动优化缺少三角色回归绑定")
        baseline = db.get(
            StrategyBundle, int(binding["baseline_strategy_bundle_id"])
        )
        if baseline is None or baseline.prompt_b_version != run.base_prompt_version:
            raise ValueError("自动优化基线策略已失配")
        prompt_a = db.scalar(
            select(PromptVersion).where(
                PromptVersion.stage == "A",
                PromptVersion.version == baseline.prompt_a_version,
            )
        )
        model_config = db.scalar(
            select(ModelConfig).where(
                ModelConfig.model_id == baseline.model_id,
                ModelConfig.active.is_(True),
            )
        )
        if prompt_a is None or model_config is None:
            raise ValueError("自动优化基线配置无法解析")
        policy = (
            None
            if baseline.sampling_policy_revision is None
            else db.scalar(
                select(SamplingPolicy).where(
                    SamplingPolicy.revision
                    == baseline.sampling_policy_revision
                )
            )
        )
        prompt_ids: list[int] = []
        regression_ids: list[int] = []
        for index, generated in enumerate(result.candidates, start=1):
            version = f"auto-b-{run.id}-{index}"
            if db.scalar(
                select(PromptVersion.id).where(PromptVersion.version == version)
            ) is not None:
                raise ValueError("自动候选版本已存在")
            candidate = PromptVersion(
                stage="B",
                name=f"自动优化候选 #{run.id}.{index}",
                version=version,
                system_prompt=str(generated["system_prompt"]),
                user_prompt=str(generated["user_prompt"]),
                rubric_version=baseline.rubric_version,
                status="draft",
                source="optimizer",
                source_automation_run_id=run.id,
                change_note=str(generated["change_note"]),
                created_by=worker_id,
            )
            db.add(candidate)
            db.flush()
            candidate_bundle = get_or_create_bundle(
                db=db,
                model_config=model_config,
                prompt_a=prompt_a,
                prompt_b=candidate,
                rubric_version=baseline.rubric_version,
                engine_version=baseline.engine_version,
                risk_review_version=baseline.risk_review_version,
                sampling_policy=policy,
                agent_plan_version=baseline.agent_plan_version,
            )
            if candidate_bundle.model_config_snapshot != baseline.model_config_snapshot:
                raise ValueError("自动优化期间模型配置已漂移")
            regression = _create_paired_regression(
                PairedRegressionCreateRequest(
                    name=f"自动优化候选 #{run.id}.{index} 配对回归",
                    sample_set_id=int(binding["sample_set_id"]),
                    baseline_strategy_bundle_id=baseline.id,
                    candidate_strategy_bundle_id=candidate_bundle.id,
                    trigger_prompt_id=candidate.id,
                    samples=[
                        PairedRegressionSampleRequest(**sample)
                        for sample in binding["samples"]
                    ],
                    metric_rules_version="automation-paired-v1",
                    aesthetic_accuracy_max_drop=0,
                    whole_image_accuracy_max_drop=0,
                    level_consistency_max_drop=0,
                ),
                user=SimpleNamespace(username=worker_id),
                db=db,
                commit=False,
            )
            prompt_ids.append(candidate.id)
            regression_ids.append(int(regression["id"]))
        return {"prompt_ids": prompt_ids, "regression_ids": regression_ids}


def _token_cost(
    input_tokens: int,
    output_tokens: int,
    input_price: int,
    output_price: int,
) -> int:
    if input_price <= 0 or output_price <= 0:
        raise ValueError("模型计价未完整配置")
    return (
        input_tokens * input_price + output_tokens * output_price + 999_999
    ) // 1_000_000


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

    def estimate_cost_micros(self, *, frozen_input: dict[str, Any]) -> int:
        del frozen_input
        return self.result.actual_cost_micros


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


def _legacy_budget_used_today(db: Session, now: datetime) -> int:
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


def _budget_row(db: Session, now: datetime) -> AutomationBudgetDay:
    key = now.date().isoformat()
    row = db.get(AutomationBudgetDay, key)
    if row is None:
        row = AutomationBudgetDay(
            budget_date=key,
            spent_micros=_legacy_budget_used_today(db, now),
        )
        db.add(row)
        db.flush()
    return row


def _budget_used_today(db: Session, now: datetime) -> int:
    row = _budget_row(db, now)
    return row.spent_micros + row.reserved_micros


def _reserve_budget(
    db: Session,
    *,
    now: datetime,
    amount: int,
    budget: int,
) -> bool:
    row = _budget_row(db, now)
    updated = db.execute(
        update(AutomationBudgetDay)
        .where(
            AutomationBudgetDay.budget_date == row.budget_date,
            AutomationBudgetDay.spent_micros
            + AutomationBudgetDay.reserved_micros
            + amount
            <= budget,
        )
        .values(
            reserved_micros=AutomationBudgetDay.reserved_micros + amount,
            updated_at=now,
        )
    )
    return int(updated.rowcount or 0) == 1


def _settle_budget(
    db: Session,
    *,
    now: datetime,
    reserved: int,
    actual: int,
) -> None:
    row = _budget_row(db, now)
    updated = db.execute(
        update(AutomationBudgetDay)
        .where(
            AutomationBudgetDay.budget_date == row.budget_date,
            AutomationBudgetDay.reserved_micros >= reserved,
        )
        .values(
            reserved_micros=AutomationBudgetDay.reserved_micros - reserved,
            spent_micros=AutomationBudgetDay.spent_micros + actual,
            updated_at=now,
        )
    )
    if int(updated.rowcount or 0) != 1:
        raise RuntimeError("automation_budget_settlement_conflict")


def _try_settle_budget(
    db: Session,
    *,
    now: datetime,
    reserved: int,
    actual: int,
) -> bool:
    row = _budget_row(db, now)
    updated = db.execute(
        update(AutomationBudgetDay)
        .where(
            AutomationBudgetDay.budget_date == row.budget_date,
            AutomationBudgetDay.reserved_micros >= reserved,
        )
        .values(
            reserved_micros=AutomationBudgetDay.reserved_micros - reserved,
            spent_micros=AutomationBudgetDay.spent_micros + actual,
            updated_at=now,
        )
    )
    return int(updated.rowcount or 0) == 1


def recover_expired_leases(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    current = now or _now()
    run_ids = list(set(
        db.scalars(
            select(OptimizationCaseQueue.automation_run_id).where(
                OptimizationCaseQueue.status == "processing",
                OptimizationCaseQueue.lease_expires_at.is_not(None),
                OptimizationCaseQueue.lease_expires_at <= current,
                OptimizationCaseQueue.automation_run_id.is_not(None),
            )
        ).all()
    ))
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
    if run_ids:
        for run in db.scalars(
            select(AutomationOptimizationRun).where(
                AutomationOptimizationRun.id.in_(run_ids),
                AutomationOptimizationRun.status == "processing",
            )
        ).all():
            _settle_budget(
                db,
                now=current,
                reserved=run.estimated_cost_micros,
                actual=run.estimated_cost_micros,
            )
        db.execute(
            update(AutomationOptimizationRun)
            .where(
                AutomationOptimizationRun.id.in_(run_ids),
                AutomationOptimizationRun.status == "processing",
            )
            .values(
                status="failed",
                retryable=True,
                error_message="lease_expired",
                finished_at=current,
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
    if db.get_bind().dialect.name == "sqlite" and not db.in_transaction():
        db.execute(text("BEGIN IMMEDIATE"))
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
            or_(
                OptimizationCaseQueue.status == "pending",
                and_(
                    OptimizationCaseQueue.status == "failed",
                    OptimizationCaseQueue.next_attempt_at.is_not(None),
                ),
            ),
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
    if adapter is not None and hasattr(adapter, "bind_base_prompt"):
        adapter.bind_base_prompt(db, version=prompt_version)  # type: ignore[attr-defined]
    if adapter is not None and hasattr(adapter, "prepare_regression_binding"):
        frozen_input["regression_binding"] = adapter.prepare_regression_binding(  # type: ignore[attr-defined]
            db, base_prompt=adapter.base_prompt  # type: ignore[attr-defined]
        )
    try:
        estimated_cost = (
            0
            if policy.dry_run or adapter is None
            else (
                adapter.estimate_cost_micros(frozen_input=frozen_input)
                if hasattr(adapter, "estimate_cost_micros")
                else policy.daily_budget_micros
            )
        )
    except ValueError:
        return {
            "status": "executor_config_blocked",
            "reason": "pricing_or_regression_binding_missing",
            "recovered_leases": recovered,
        }
    used = _budget_used_today(db, current)
    if (
        not policy.dry_run and adapter is not None
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

    if (
        not policy.dry_run
        and adapter is not None
        and not _reserve_budget(
            db,
            now=current,
            amount=estimated_cost,
            budget=policy.daily_budget_micros,
        )
    ):
        db.rollback()
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

    run_key = hashlib.sha256(
        canonical_json(
            {
                "policy_revision": policy.revision,
                "case_ids": selected_ids,
                "prompt_version": prompt_version,
                "attempts": {
                    str(case.id): case.attempt_count + 1 for case in selected
                },
            }
        ).encode("utf-8")
    ).hexdigest()
    existing = db.scalar(
        select(AutomationOptimizationRun).where(
            AutomationOptimizationRun.run_key == run_key
        )
    )
    if existing is not None:
        if not policy.dry_run and adapter is not None:
            _settle_budget(
                db,
                now=current,
                reserved=estimated_cost,
                actual=0,
            )
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
            else "awaiting_executor"
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

    db.commit()
    run.status = "processing"
    append_audit_event(
        db,
        category="automation",
        action="executor_started",
        subject_type="automation_optimization_run",
        subject_id=run.id,
        actor=worker_id,
        payload={"estimated_cost_micros": estimated_cost},
        event_key=f"automation-run-processing:{run.run_key}",
    )
    db.commit()
    budget_settled = False
    try:
        result = adapter.optimize(
            frozen_input=frozen_input,
            max_candidates=policy.max_candidates,
        )
        if result.actual_cost_micros < 0:
            raise ValueError("实际成本不能为负数")
        if (
            result.input_tokens is None
            or result.output_tokens is None
            or result.total_tokens is None
        ):
            raise RuntimeError("optimizer_usage_missing")
        if result.actual_cost_micros > estimated_cost:
            raise RuntimeError("optimizer_usage_exceeds_reserved_cost")
        if not result.candidates or len(result.candidates) > policy.max_candidates:
            raise ValueError("优化执行器返回的候选数量无效")
        active_leases = int(
            db.scalar(
                select(func.count())
                .select_from(OptimizationCaseQueue)
                .where(
                    OptimizationCaseQueue.id.in_(selected_ids),
                    OptimizationCaseQueue.automation_run_id == run.id,
                    OptimizationCaseQueue.status == "processing",
                    OptimizationCaseQueue.lease_token == lease_token,
                )
            )
            or 0
        )
        if active_leases != len(selected_ids):
            raise RuntimeError("automation_lease_lost")
        with db.begin_nested():
            materialized = (
                adapter.materialize(  # type: ignore[attr-defined]
                    db,
                    run=run,
                    result=result,
                    worker_id=worker_id,
                )
                if hasattr(adapter, "materialize")
                else {"prompt_ids": [], "regression_ids": []}
            )
        _settle_budget(
            db,
            now=current,
            reserved=estimated_cost,
            actual=result.actual_cost_micros,
        )
        budget_settled = True
        run.result_json = canonical_json(
            {
                "candidates": result.candidates,
                "regression": result.regression,
                **materialized,
                "release_requires_human_review": True,
                "publishes_automatically": False,
            }
        )
        run.candidate_count = len(result.candidates)
        run.actual_cost_micros = result.actual_cost_micros
        run.input_tokens = result.input_tokens
        run.output_tokens = result.output_tokens
        run.total_tokens = result.total_tokens
        run.status = "succeeded"
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
            action="succeeded",
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
        safe_error, retryable = _safe_executor_error(exc)
        if not budget_settled:
            active_leases = int(
                db.scalar(
                    select(func.count())
                    .select_from(OptimizationCaseQueue)
                    .where(
                        OptimizationCaseQueue.id.in_(selected_ids),
                        OptimizationCaseQueue.automation_run_id == run.id,
                        OptimizationCaseQueue.status == "processing",
                        OptimizationCaseQueue.lease_token == lease_token,
                    )
                )
                or 0
            )
            if active_leases != len(selected_ids):
                safe_error, retryable = "automation_lease_lost", True
            settled = _try_settle_budget(
                db,
                now=current,
                reserved=estimated_cost,
                actual=estimated_cost,
            )
            if not settled and safe_error != "automation_lease_lost":
                safe_error, retryable = (
                    "automation_budget_settlement_conflict",
                    False,
                )
        run.status = "failed"
        run.error_message = safe_error
        run.retryable = retryable
        run.finished_at = current
        attempt = max(case.attempt_count for case in selected)
        retry_at = (
            current
            + timedelta(
                seconds=policy.base_retry_seconds
                * (2 ** max(0, attempt - 1))
            )
            if retryable and attempt < policy.max_attempts
            else None
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
            payload={
                "error": safe_error,
                "retry_at": retry_at.isoformat() if retry_at else None,
                "retryable": retryable,
            },
            event_key=f"automation-run-failed:{run.run_key}",
        )
        return {"status": "failed", "run_id": run.id, "retry_at": retry_at}


def _safe_executor_error(exc: Exception) -> tuple[str, bool]:
    known = {
        "optimizer_usage_missing",
        "optimizer_usage_exceeds_reserved_cost",
        "automation_lease_lost",
        "automation_budget_settlement_conflict",
    }
    message = str(exc)
    if message in known:
        return message, False
    technical_type = str(getattr(exc, "technical_error_type", ""))
    if technical_type in {
        "timeout", "network", "429", "provider5xx",
        "json_truncated", "transient_parse",
    }:
        return f"model_{technical_type}", bool(getattr(exc, "retryable", True))
    if isinstance(exc, ValueError):
        return "invalid_executor_output", False
    return "automation_executor_failed", False


def configured_optimization_adapter(db: Session) -> RealOptimizationAdapter | None:
    config = db.scalar(select(OptimizerConfig).order_by(OptimizerConfig.id.asc()))
    if (
        config is None
        or not config.encrypted_api_key
        or config.input_micros_per_million_tokens <= 0
        or config.output_micros_per_million_tokens <= 0
        or config.max_input_tokens <= 0
        or config.max_tokens <= 0
    ):
        return None
    return RealOptimizationAdapter(config=config)


def automation_budget_status(
    db: Session, policy: AutomationPolicy, *, now: datetime | None = None
) -> dict[str, int]:
    row = _budget_row(db, now or _now())
    used = row.spent_micros + row.reserved_micros
    return {
        "spent_micros": row.spent_micros,
        "reserved_micros": row.reserved_micros,
        "used_micros": used,
        "remaining_micros": max(0, policy.daily_budget_micros - used),
        "limit_micros": policy.daily_budget_micros,
    }


def optimization_worker_tick(worker_id: str) -> dict[str, Any]:
    """Run one queue iteration; persisted defaults remain fail-closed."""
    with session_scope() as db:
        return consume_optimization_queue_once(
            db,
            worker_id=worker_id,
            adapter=configured_optimization_adapter(db),
        )
