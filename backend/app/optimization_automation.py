from __future__ import annotations

import hashlib
import json
import logging
import traceback
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
    AutomationWorkerStatus,
    Asset,
    EvaluationCategoryProfile,
    ModelConfig,
    ModelNodeBinding,
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


logger = logging.getLogger("3d66.automation")


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
        self, db: Session, *, base_prompt: PromptVersion, category_key: str = "space_image"
    ) -> dict[str, Any]:
        sample_sets = db.scalars(
            select(SampleSet)
            .where(
                SampleSet.kind == "golden",
                SampleSet.status == "locked",
                SampleSet.category_key == category_key,
            )
            .order_by(SampleSet.id.desc())
        ).all()
        for sample_set in sample_sets:
            if any(item.asset.category_key != category_key for item in sample_set.items):
                continue
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


def _safe_json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _config_is_ready(config: Any | None) -> bool:
    return (
        config is not None
        and bool(getattr(config, "encrypted_api_key", None))
        and int(getattr(config, "input_micros_per_million_tokens", 0) or 0) > 0
        and int(getattr(config, "output_micros_per_million_tokens", 0) or 0) > 0
        and int(getattr(config, "max_input_tokens", 0) or 0) > 0
        and int(getattr(config, "max_tokens", 0) or 0) > 0
    )


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

    # Select one category cohort per tick. Cases from different pipelines must
    # never share an optimizer run, even when they use the same prompt version.
    category_key = available[0].category_key
    category_cases = [case for case in available if case.category_key == category_key]
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == category_key
        )
    )
    category_config = {}
    if profile is not None:
        try:
            category_config = json.loads(profile.automation_config_json or "{}")
        except json.JSONDecodeError:
            category_config = {}
    if category_config.get("enabled") is False:
        return {"status": "category_disabled", "category_key": category_key, "recovered_leases": recovered}
    case_threshold = max(1, int(category_config.get("case_threshold", policy.case_threshold)))
    max_candidates = max(1, int(category_config.get("max_candidates", policy.max_candidates)))
    if adapter is None:
        adapter = configured_optimization_adapter(db, category_key=category_key)

    immediate = set(json.loads(policy.immediate_severities_json or "[]"))
    trigger_case = next(
        (case for case in category_cases if case.severity in immediate),
        None,
    )
    prompt_version = trigger_case.prompt_version if trigger_case else category_cases[0].prompt_version
    same_prompt = [
        case for case in category_cases if case.prompt_version == prompt_version
    ]
    if trigger_case is None and len(same_prompt) < case_threshold:
        return {
            "status": "threshold_wait",
            "available": len(same_prompt),
            "required": case_threshold,
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
            "cooldown_until": (
                _aware(policy.last_triggered_at)
                + timedelta(seconds=policy.cooldown_seconds)
            ).isoformat(),
            "recovered_leases": recovered,
        }

    selected = same_prompt[:case_threshold]
    if not policy.dry_run and adapter is None:
        return {
            "status": "executor_config_blocked",
            "reason": "optimizer_config_incomplete",
            "category_key": category_key,
            "recovered_leases": recovered,
        }
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
        "category_key": category_key,
        "policy": _policy_payload(policy),
        "cases": frozen_cases,
    }
    try:
        if adapter is not None and hasattr(adapter, "bind_base_prompt"):
            adapter.bind_base_prompt(db, version=prompt_version)  # type: ignore[attr-defined]
        if adapter is not None and hasattr(adapter, "prepare_regression_binding"):
            frozen_input["regression_binding"] = adapter.prepare_regression_binding(  # type: ignore[attr-defined]
                db, base_prompt=adapter.base_prompt, category_key=category_key  # type: ignore[attr-defined]
            )
    except ValueError:
        return {
            "status": "executor_config_blocked",
            "reason": "pricing_or_regression_binding_missing",
            "recovered_leases": recovered,
        }
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
                "category_key": category_key,
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
        category_key=category_key,
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
            max_candidates=max_candidates,
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
        if not result.candidates or len(result.candidates) > max_candidates:
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
        return {
            "status": "failed",
            "run_id": run.id,
            "retry_at": retry_at.isoformat() if retry_at else None,
        }


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


def _candidate_optimizer_configs(
    db: Session, *, category_key: str | None = None
) -> list[tuple[str, Any]]:
    candidates: list[tuple[str, Any]] = []
    if category_key:
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == category_key
            )
        )
        if profile is not None and profile.optimizer_config_id is not None:
            config = db.get(OptimizerConfig, profile.optimizer_config_id)
            candidates.append(("category_optimizer_config", config))
        category_binding = db.scalar(
            select(ModelNodeBinding).where(
                ModelNodeBinding.node_key == "optimization",
                ModelNodeBinding.category_key == category_key,
                ModelNodeBinding.enabled.is_(True),
            )
        )
        candidates.append((
            "category_model_node",
            category_binding.model
            if category_binding is not None and category_binding.model.active
            else None,
        ))
    global_binding = db.scalar(
        select(ModelNodeBinding).where(
            ModelNodeBinding.node_key == "optimization",
            ModelNodeBinding.category_key.is_(None),
            ModelNodeBinding.enabled.is_(True),
        )
    )
    candidates.append((
        "global_model_node",
        global_binding.model
        if global_binding is not None and global_binding.model.active
        else None,
    ))
    candidates.append((
        "optimizer_config",
        db.scalar(select(OptimizerConfig).order_by(OptimizerConfig.id.asc())),
    ))
    return candidates


def optimizer_configuration_status(
    db: Session, *, category_key: str | None = None
) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    for source, config in _candidate_optimizer_configs(db, category_key=category_key):
        ready = _config_is_ready(config)
        checked.append(
            {
                "source": source,
                "configured": ready,
                "model_id": getattr(config, "model_id", None) if config is not None else None,
                "has_api_key": bool(getattr(config, "encrypted_api_key", None))
                if config is not None
                else False,
                "has_input_pricing": int(
                    getattr(config, "input_micros_per_million_tokens", 0) or 0
                )
                > 0
                if config is not None
                else False,
                "has_output_pricing": int(
                    getattr(config, "output_micros_per_million_tokens", 0) or 0
                )
                > 0
                if config is not None
                else False,
                "has_input_limit": int(getattr(config, "max_input_tokens", 0) or 0)
                > 0
                if config is not None
                else False,
                "has_output_limit": int(getattr(config, "max_tokens", 0) or 0) > 0
                if config is not None
                else False,
            }
        )
        if ready:
            return {
                "configured": True,
                "source": source,
                "model_id": getattr(config, "model_id", None),
                "checked": checked,
            }
    return {
        "configured": False,
        "source": None,
        "model_id": None,
        "checked": checked,
    }


def configured_optimization_adapter(
    db: Session, *, category_key: str | None = None
) -> RealOptimizationAdapter | None:
    for _source, config in _candidate_optimizer_configs(db, category_key=category_key):
        if _config_is_ready(config):
            return RealOptimizationAdapter(config=config)
    return None


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


def record_automation_worker_status(
    db: Session,
    *,
    worker_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error_message: str = "",
    now: datetime | None = None,
) -> AutomationWorkerStatus:
    current = now or _now()
    row = db.get(AutomationWorkerStatus, worker_id)
    if row is None:
        row = AutomationWorkerStatus(worker_id=worker_id, started_at=current)
        db.add(row)
    row.last_seen_at = current
    row.last_tick_at = current
    row.last_status = status[:80]
    row.last_error = error_message[:500]
    row.last_result_json = canonical_json(result or {})
    row.consecutive_errors = row.consecutive_errors + 1 if error_message else 0
    row.updated_at = current
    db.flush()
    return row


def touch_automation_worker_status(
    db: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> AutomationWorkerStatus:
    current = now or _now()
    row = db.get(AutomationWorkerStatus, worker_id)
    if row is None:
        row = AutomationWorkerStatus(worker_id=worker_id, started_at=current)
        db.add(row)
    row.last_seen_at = current
    row.updated_at = current
    db.flush()
    return row


def automation_worker_snapshot(
    db: Session, *, now: datetime | None = None, active_seconds: int = 30
) -> dict[str, Any]:
    current = now or _now()
    active_cutoff = current - timedelta(seconds=active_seconds)
    rows = db.scalars(
        select(AutomationWorkerStatus)
        .order_by(
            AutomationWorkerStatus.last_seen_at.desc(),
            AutomationWorkerStatus.worker_id.asc(),
        )
        .limit(20)
    ).all()
    workers = []
    active_count = 0
    for row in rows:
        seen_at = _aware(row.last_seen_at)
        active = seen_at is not None and seen_at >= active_cutoff
        if active:
            active_count += 1
        workers.append(
            {
                "worker_id": row.worker_id,
                "active": active,
                "started_at": row.started_at,
                "last_seen_at": row.last_seen_at,
                "last_tick_at": row.last_tick_at,
                "last_status": row.last_status,
                "last_error": row.last_error,
                "last_result": _safe_json_object(row.last_result_json),
                "consecutive_errors": row.consecutive_errors,
            }
        )
    return {
        "active_worker_count": active_count,
        "stale_after_seconds": active_seconds,
        "workers": workers,
    }


def _eligible_cases(db: Session, policy: AutomationPolicy, now: datetime) -> list[OptimizationCaseQueue]:
    return db.scalars(
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
                OptimizationCaseQueue.next_attempt_at <= now,
            ),
            or_(
                OptimizationCaseQueue.lease_expires_at.is_(None),
                OptimizationCaseQueue.lease_expires_at <= now,
            ),
        )
        .order_by(
            OptimizationCaseQueue.created_at.asc(),
            OptimizationCaseQueue.id.asc(),
        )
    ).all()


def automation_runtime_status(
    db: Session, policy: AutomationPolicy, *, now: datetime | None = None
) -> dict[str, Any]:
    current = now or _now()
    worker = automation_worker_snapshot(db, now=current)
    blockers: list[dict[str, Any]] = []

    def block(code: str, message: str, *, severity: str = "blocking") -> None:
        blockers.append({"code": code, "message": message, "severity": severity})

    if worker["active_worker_count"] == 0:
        block("worker_not_seen", "未检测到常驻 Worker 心跳；请用正式启动器启动服务。")
    if not policy.enabled:
        block("policy_disabled", "自动组批总开关关闭。")
    if policy.dry_run:
        block("dry_run_enabled", "当前为 dry-run，只生成试跑计划，不调用优化模型。", severity="warning")

    budget = automation_budget_status(db, policy, now=current)
    if not policy.dry_run and policy.daily_budget_micros <= 0:
        block("budget_not_set", "真实执行需要设置大于 0 的每日预算。")
    elif not policy.dry_run and budget["remaining_micros"] <= 0:
        block("budget_exhausted", "今日自动优化预算已用尽。")

    available = _eligible_cases(db, policy, current)
    queue: dict[str, Any] = {
        "eligible_case_count": len(available),
        "next_category_key": None,
        "next_prompt_version": None,
        "available_for_prompt": 0,
        "required_for_prompt": policy.case_threshold,
    }
    category_key: str | None = None
    adapter: RealOptimizationAdapter | None = None
    if not available:
        block("queue_empty", "没有达到可组批条件的纠偏案例。", severity="info")
    else:
        category_key = available[0].category_key
        category_cases = [case for case in available if case.category_key == category_key]
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == category_key
            )
        )
        category_config = _safe_json_object(profile.automation_config_json if profile else "{}")
        if category_config.get("enabled") is False:
            block("category_disabled", f"类目 {category_key} 的自动优化关闭。")
        case_threshold = max(1, int(category_config.get("case_threshold", policy.case_threshold)))
        immediate = set(json.loads(policy.immediate_severities_json or "[]"))
        trigger_case = next(
            (case for case in category_cases if case.severity in immediate),
            None,
        )
        prompt_version = (
            trigger_case.prompt_version if trigger_case else category_cases[0].prompt_version
        )
        same_prompt = [
            case for case in category_cases if case.prompt_version == prompt_version
        ]
        queue.update(
            {
                "next_category_key": category_key,
                "next_prompt_version": prompt_version,
                "available_for_prompt": len(same_prompt),
                "required_for_prompt": case_threshold,
            }
        )
        if trigger_case is None and len(same_prompt) < case_threshold:
            block(
                "threshold_wait",
                f"同一提示词版本案例 {len(same_prompt)}/{case_threshold}，尚未达到组批门槛。",
                severity="waiting",
            )
        if (
            trigger_case is None
            and policy.last_triggered_at is not None
            and _aware(policy.last_triggered_at)
            + timedelta(seconds=policy.cooldown_seconds)
            > current
        ):
            block("cooldown", "上一批刚触发完成，仍在冷却窗口。", severity="waiting")
        adapter = configured_optimization_adapter(db, category_key=category_key)
        if adapter is None:
            block(
                "optimizer_config_incomplete",
                "优化模型缺少密钥、输入上限或非零计价。",
                severity="warning" if policy.dry_run else "blocking",
            )
        elif not policy.dry_run:
            try:
                adapter.bind_base_prompt(db, version=prompt_version)
                adapter.prepare_regression_binding(
                    db, base_prompt=adapter.base_prompt, category_key=category_key
                )
            except ValueError:
                block(
                    "regression_binding_missing",
                    "缺少同类目三角色锁定黄金集，无法创建发布前配对回归。",
                )

    blocking_codes = {
        item["code"]
        for item in blockers
        if item["severity"] == "blocking"
    }
    status = (
        "blocked"
        if blocking_codes
        else "waiting"
        if blockers
        else "ready"
    )
    return {
        "status": status,
        "checked_at": current,
        "worker": worker,
        "queue": queue,
        "optimizer": optimizer_configuration_status(db, category_key=category_key),
        "budget": budget,
        "blockers": blockers,
    }


def optimization_worker_tick(worker_id: str) -> dict[str, Any]:
    """Run one queue iteration; persisted defaults remain fail-closed."""
    with session_scope() as db:
        record_automation_worker_status(
            db,
            worker_id=worker_id,
            status="checking",
            result={"status": "checking"},
        )
    try:
        with session_scope() as db:
            from .regression import reconcile_automation_review_states

            reconcile_automation_review_states(db)
            result = consume_optimization_queue_once(db, worker_id=worker_id)
            record_automation_worker_status(
                db,
                worker_id=worker_id,
                status=str(result.get("status", "unknown")),
                result=result,
            )
            return result
    except Exception as exc:
        safe_error, _retryable = _safe_executor_error(exc)
        trace = traceback.extract_tb(exc.__traceback__)
        location = (
            f"{trace[-1].filename.rsplit('/', 1)[-1]}:"
            f"{trace[-1].lineno}:{trace[-1].name}"
            if trace
            else "unknown"
        )
        logger.error(
            "自动优化 tick 异常：%s exception=%s location=%s",
            safe_error,
            type(exc).__name__,
            location,
        )
        with session_scope() as db:
            record_automation_worker_status(
                db,
                worker_id=worker_id,
                status="worker_error",
                result={"status": "worker_error", "error_message": safe_error},
                error_message=safe_error,
            )
        return {"status": "worker_error", "error_message": safe_error}
