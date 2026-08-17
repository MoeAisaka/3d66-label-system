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
from .automation_batching import create_automation_batch, select_ready_lane
from .database import session_scope
from .models import (
    AutomationBatch,
    AutomationLanePolicy,
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
from .strategy_bundle import build_model_config_snapshot


logger = logging.getLogger("3d66.automation")
OPTIMIZER_MODEL_CALLS_PER_RUN = 2
MATERIALIZE_REGRESSION_SAFETY_SECONDS = 120
AUTOMATION_CONFIGURATION_BLOCKER_CODES = frozenset(
    {
        "optimizer_config_incomplete",
        "regression_binding_missing",
        "baseline_category_contract_incomplete",
        "baseline_category_contract_mismatch",
        "baseline_strategy_bundle_invalid",
        "baseline_strategy_bundle_missing",
        "baseline_strategy_bundle_not_found",
        "baseline_strategy_bundle_ambiguous",
        "baseline_strategy_bundle_contract_mismatch",
    }
)


class AutomationConfigurationBlocker(ValueError):
    """Stable, actionable reason why one automation cohort cannot run."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _category_contract_definition_errors(
    db: Session,
    *,
    profile: EvaluationCategoryProfile | None,
    require_prompt_b: bool,
) -> list[str]:
    if profile is None:
        return ["category_profile_missing"]
    errors: list[str] = []
    required_refs = {
        "prompt_a": (profile.prompt_a_id, PromptVersion, "A"),
        "model_config": (profile.model_config_id, ModelConfig, None),
    }
    if require_prompt_b:
        required_refs["prompt_b"] = (profile.prompt_b_id, PromptVersion, "B")
    for name, (identity, model_type, expected_stage) in required_refs.items():
        if identity is None:
            errors.append(f"{name}_id_missing")
            continue
        entity = db.get(model_type, identity)
        if entity is None:
            errors.append(f"{name}_missing")
        elif expected_stage is not None and entity.stage != expected_stage:
            errors.append(f"{name}_stage_mismatch")
    if not (profile.rubric_version or "").strip():
        errors.append("rubric_version_missing")
    if not profile.dimension_schema_key or not profile.dimension_schema_version:
        errors.append("dimension_contract_missing")
    return list(dict.fromkeys(errors))


def _resolve_category_baseline_bundle(
    db: Session,
    *,
    profile: EvaluationCategoryProfile | None,
    base_prompt: PromptVersion,
    freeze_automatic_binding: bool,
) -> tuple[StrategyBundle, str]:
    """Resolve one explicit or uniquely matching full category contract."""
    if profile is None:
        raise AutomationConfigurationBlocker(
            "baseline_category_contract_incomplete",
            "类目配置不存在；请由管理员先创建并启用类目合同。",
        )
    definition_errors = _category_contract_definition_errors(
        db, profile=profile, require_prompt_b=True
    )
    if definition_errors:
        raise AutomationConfigurationBlocker(
            "baseline_category_contract_incomplete",
            "类目基线合同未完整配置 A/B Prompt、模型、Rubric 和维度 Schema；"
            "请在后台高级设置补齐后重试。",
        )
    configured_prompt_b = db.get(PromptVersion, profile.prompt_b_id)
    if configured_prompt_b is None or configured_prompt_b.version != base_prompt.version:
        raise AutomationConfigurationBlocker(
            "baseline_category_contract_mismatch",
            "待优化案例的 B Prompt 与当前类目合同不一致；"
            "请先归档旧案例或恢复对应类目版本。",
        )

    config = _safe_json_object(profile.automation_config_json)
    baseline_bundle_id = config.get("baseline_strategy_bundle_id")
    if baseline_bundle_id is not None:
        if (
            not isinstance(baseline_bundle_id, int)
            or isinstance(baseline_bundle_id, bool)
            or baseline_bundle_id < 1
        ):
            raise AutomationConfigurationBlocker(
                "baseline_strategy_bundle_invalid",
                "类目显式基线 ID 无效；请由管理员在后台高级设置重新选择。",
            )
        baseline = db.get(StrategyBundle, baseline_bundle_id)
        if baseline is None:
            raise AutomationConfigurationBlocker(
                "baseline_strategy_bundle_missing",
                "类目显式绑定的基线 Bundle 不存在；"
                "请由管理员在后台高级设置重新选择。",
            )
        errors = category_bundle_contract_errors(
            db,
            profile=profile,
            bundle=baseline,
            require_complete=True,
            require_prompt_b=True,
            enforce_baseline_id=True,
        )
        if errors or baseline.prompt_b_version != base_prompt.version:
            raise AutomationConfigurationBlocker(
                "baseline_strategy_bundle_contract_mismatch",
                "显式基线 Bundle 与类目 A/B Prompt、模型、Rubric 或维度合同不一致；"
                "请由管理员在后台高级设置重新选择。",
            )
        return baseline, str(config.get("baseline_binding_source") or "explicit_legacy")

    candidates = db.scalars(
        select(StrategyBundle).where(
            StrategyBundle.prompt_b_version == base_prompt.version
        )
    ).all()
    matches = [
        candidate
        for candidate in candidates
        if not category_bundle_contract_errors(
            db,
            profile=profile,
            bundle=candidate,
            require_complete=True,
            require_prompt_b=True,
            enforce_baseline_id=False,
        )
    ]
    if not matches:
        raise AutomationConfigurationBlocker(
            "baseline_strategy_bundle_not_found",
            "没有 StrategyBundle 完整匹配当前类目的 A/B Prompt、模型、Rubric 和维度合同；"
            "请先生成该组合的 Bundle，或由管理员在后台高级设置显式绑定。",
        )
    if len(matches) > 1:
        raise AutomationConfigurationBlocker(
            "baseline_strategy_bundle_ambiguous",
            f"当前类目合同匹配到 {len(matches)} 个 StrategyBundle；"
            "请由管理员在后台高级设置显式选择一个基线。",
        )
    baseline = matches[0]
    if freeze_automatic_binding:
        profile.automation_config_json = canonical_json(
            {
                **config,
                "baseline_strategy_bundle_id": baseline.id,
                "baseline_binding_source": "auto_contract",
            }
        )
        profile.automation_revision += 1
        db.flush()
    return baseline, "auto_contract"


def automation_lifecycle_status(status: str) -> str:
    """Expose the production state machine while retaining legacy DB values."""
    return {
        "planned": "pending",
        "awaiting_executor": "pending",
        "processing": "processing",
        "succeeded": "running",
        "running": "running",
        "awaiting_release_review": "awaiting_release_review",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(status, status)


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
        calls = OPTIMIZER_MODEL_CALLS_PER_RUN
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
        self,
        db: Session,
        *,
        base_prompt: PromptVersion,
        category_key: str = "space_image",
        freeze_automatic_binding: bool = True,
    ) -> dict[str, Any]:
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == category_key
            )
        )
        baseline, binding_source = _resolve_category_baseline_bundle(
            db,
            profile=profile,
            base_prompt=base_prompt,
            freeze_automatic_binding=freeze_automatic_binding,
        )
        assert profile is not None
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
                return {
                    "sample_set_id": sample_set.id,
                    "baseline_strategy_bundle_id": baseline.id,
                    "category_contract": {
                        "category_key": category_key,
                        "profile_id": profile.id,
                        "pipeline_revision": profile.pipeline_revision,
                        "automation_revision": profile.automation_revision,
                        "baseline_strategy_bundle_id": baseline.id,
                        "baseline_binding_source": binding_source,
                        "prompt_a_id": profile.prompt_a_id,
                        "prompt_b_id": profile.prompt_b_id,
                        "model_config_id": profile.model_config_id,
                        "rubric_version": profile.rubric_version,
                        "dimension_schema_key": profile.dimension_schema_key,
                        "dimension_schema_version": profile.dimension_schema_version,
                    },
                    "samples": [
                        {"sample_item_id": target.id, "role": "target_error"},
                        {"sample_item_id": stable.id, "role": "stable_control"},
                        {"sample_item_id": blind.id, "role": "blind_holdout"},
                    ],
                }
        raise AutomationConfigurationBlocker(
            "regression_binding_missing",
            f"类目 {category_key} 没有可用于三角色配对回归的锁定黄金样本；"
            "请补齐 target_error、stable_control 和 blind_holdout。",
        )

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
        category_contract = binding.get("category_contract")
        if not isinstance(category_contract, dict):
            raise ValueError("自动优化缺少冻结类目合同")
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == run.category_key
            )
        )
        frozen_identity = {
            "prompt_a_id": profile.prompt_a_id if profile is not None else None,
            "prompt_b_id": profile.prompt_b_id if profile is not None else None,
            "model_config_id": profile.model_config_id if profile is not None else None,
            "rubric_version": profile.rubric_version if profile is not None else None,
            "dimension_schema_key": (
                profile.dimension_schema_key if profile is not None else None
            ),
            "dimension_schema_version": (
                profile.dimension_schema_version if profile is not None else None
            ),
        }
        if (
            baseline is None
            or baseline.prompt_b_version != run.base_prompt_version
            or category_contract.get("category_key") != run.category_key
            or profile is None
            or category_contract.get("profile_id") != profile.id
            or category_contract.get("pipeline_revision") != profile.pipeline_revision
            or category_contract.get("automation_revision") != profile.automation_revision
            or category_contract.get("baseline_strategy_bundle_id") != baseline.id
            or any(
                field in category_contract
                and category_contract[field] != current_value
                for field, current_value in frozen_identity.items()
            )
            or category_bundle_contract_errors(
                db,
                profile=profile,
                bundle=baseline,
                require_complete=True,
                require_prompt_b=True,
                enforce_baseline_id=True,
            )
        ):
            raise ValueError("自动优化基线策略已失配")
        prompt_a = db.scalar(
            select(PromptVersion).where(
                PromptVersion.stage == "A",
                PromptVersion.version == baseline.prompt_a_version,
            )
        )
        model_configs = db.scalars(
            select(ModelConfig).where(
                ModelConfig.model_id == baseline.model_id,
                ModelConfig.active.is_(True),
            )
        ).all()
        model_matches = []
        for model in model_configs:
            try:
                if build_model_config_snapshot(model) == json.loads(
                    baseline.model_config_snapshot
                ):
                    model_matches.append(model)
            except json.JSONDecodeError:
                break
        model_config = model_matches[0] if len(model_matches) == 1 else None
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
                category_key=run.category_key,
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
            assert_bundle_pair_category_contract(baseline, candidate_bundle)
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


def _safe_json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


_BUNDLE_CATEGORY_IDENTITY_FIELDS = (
    "model_id",
    "model_config_snapshot",
    "prompt_a_version",
    "rubric_version",
    "engine_version",
    "sampling_policy_revision",
    "risk_review_version",
    "agent_plan_version",
    "dimension_route_policy_id",
    "dimension_schema_set_snapshot",
    "label_field_set_snapshot",
    "resolved_schema_contract_version",
    "dimension_route_policy_snapshot",
    "evaluation_profile_set_snapshot",
)


def assert_bundle_pair_category_contract(
    baseline: StrategyBundle,
    candidate: StrategyBundle,
) -> None:
    """Require candidate and baseline to differ only by the B prompt."""
    mismatched = [
        field
        for field in _BUNDLE_CATEGORY_IDENTITY_FIELDS
        if getattr(baseline, field) != getattr(candidate, field)
    ]
    if mismatched:
        raise ValueError(
            "基线与候选 StrategyBundle 的类目合同不一致："
            + "、".join(mismatched)
        )


def category_bundle_contract_errors(
    db: Session,
    *,
    profile: EvaluationCategoryProfile | None,
    bundle: StrategyBundle,
    require_complete: bool,
    require_prompt_b: bool,
    enforce_baseline_id: bool,
) -> list[str]:
    """Validate one immutable bundle against the persisted category contract."""
    if profile is None:
        return ["category_profile_missing"]
    errors: list[str] = []
    if require_complete:
        errors.extend(
            _category_contract_definition_errors(
                db, profile=profile, require_prompt_b=require_prompt_b
            )
        )
    config = _safe_json_object(profile.automation_config_json)
    baseline_bundle_id = config.get("baseline_strategy_bundle_id")
    if enforce_baseline_id and baseline_bundle_id is not None:
        if (
            not isinstance(baseline_bundle_id, int)
            or isinstance(baseline_bundle_id, bool)
            or baseline_bundle_id != bundle.id
        ):
            errors.append("baseline_strategy_bundle_mismatch")

    required_refs = {
        "prompt_a_id": profile.prompt_a_id,
        "model_config_id": profile.model_config_id,
    }
    if require_prompt_b:
        required_refs["prompt_b_id"] = profile.prompt_b_id
    explicit_refs = any(value is not None for value in required_refs.values())
    contract_declared = (
        require_complete
        or explicit_refs
        or baseline_bundle_id is not None
        or profile.dimension_schema_key is not None
        or profile.dimension_schema_version is not None
    )
    if require_complete or explicit_refs:
        errors.extend(
            f"{name}_missing"
            for name, value in required_refs.items()
            if value is None
        )

    prompt_a = db.get(PromptVersion, profile.prompt_a_id) if profile.prompt_a_id else None
    if prompt_a is not None and (
        prompt_a.stage != "A" or prompt_a.version != bundle.prompt_a_version
    ):
        errors.append("prompt_a_mismatch")
    if require_prompt_b:
        prompt_b = db.get(PromptVersion, profile.prompt_b_id) if profile.prompt_b_id else None
        if prompt_b is not None and (
            prompt_b.stage != "B" or prompt_b.version != bundle.prompt_b_version
        ):
            errors.append("prompt_b_mismatch")

    model = db.get(ModelConfig, profile.model_config_id) if profile.model_config_id else None
    if model is not None:
        if model.model_id != bundle.model_id:
            errors.append("model_id_mismatch")
        else:
            try:
                frozen_model = json.loads(bundle.model_config_snapshot)
            except json.JSONDecodeError:
                errors.append("model_snapshot_invalid")
            else:
                if frozen_model != build_model_config_snapshot(model):
                    errors.append("model_snapshot_mismatch")
    if contract_declared and profile.rubric_version != bundle.rubric_version:
        errors.append("rubric_version_mismatch")

    schema_key = profile.dimension_schema_key
    schema_version = profile.dimension_schema_version
    if bool(schema_key) != bool(schema_version):
        errors.append("dimension_contract_incomplete")
    elif require_complete and not schema_key:
        errors.append("dimension_contract_missing")
    elif schema_key and schema_version:
        try:
            schema_set = json.loads(bundle.dimension_schema_set_snapshot or "")
        except json.JSONDecodeError:
            errors.append("dimension_contract_invalid")
        else:
            schemas = schema_set.get("schemas") if isinstance(schema_set, dict) else None
            if not isinstance(schemas, list) or not any(
                isinstance(item, dict)
                and item.get("schema_key") == schema_key
                and item.get("version") == schema_version
                for item in schemas
            ):
                errors.append("dimension_contract_mismatch")
    return list(dict.fromkeys(errors))


def _config_is_ready(config: Any | None) -> bool:
    return (
        config is not None
        and bool(getattr(config, "encrypted_api_key", None))
        and int(getattr(config, "input_micros_per_million_tokens", 0) or 0) > 0
        and int(getattr(config, "output_micros_per_million_tokens", 0) or 0) > 0
        and int(getattr(config, "max_input_tokens", 0) or 0) > 0
        and int(getattr(config, "max_tokens", 0) or 0) > 0
    )


def _execution_lease_seconds(
    policy: AutomationPolicy,
    adapter: OptimizationAdapter | None,
) -> int:
    return int(
        _execution_lease_details(policy, adapter)["effective_lease_seconds"]
    )


def _execution_lease_details(
    policy: AutomationPolicy,
    adapter: OptimizationAdapter | None,
) -> dict[str, Any]:
    """Explain the policy floor and optimizer worst-case execution window."""
    config = getattr(adapter, "config", None)
    try:
        timeout_seconds = int(getattr(config, "timeout_seconds", 0) or 0)
        max_retries = max(0, int(getattr(config, "max_retries", 0) or 0))
    except (TypeError, ValueError):
        timeout_seconds = 0
        max_retries = 0
    optimizer_timeout_budget = (
        timeout_seconds * (max_retries + 1) * OPTIMIZER_MODEL_CALLS_PER_RUN
        if config is not None and timeout_seconds > 0
        else 0
    )
    safety_seconds = (
        MATERIALIZE_REGRESSION_SAFETY_SECONDS
        if optimizer_timeout_budget > 0
        else 0
    )
    effective_seconds = max(
        policy.lease_seconds,
        optimizer_timeout_budget + safety_seconds,
    )
    return {
        "effective_lease_seconds": effective_seconds,
        # Retain the original generic key for readers of existing audit payloads.
        "effective_seconds": effective_seconds,
        "policy_seconds": policy.lease_seconds,
        "optimizer_timeout_seconds": timeout_seconds,
        "optimizer_max_retries": max_retries,
        "optimizer_call_count": (
            OPTIMIZER_MODEL_CALLS_PER_RUN if optimizer_timeout_budget > 0 else 0
        ),
        "optimizer_timeout_budget_seconds": optimizer_timeout_budget,
        "materialize_regression_safety_seconds": safety_seconds,
        "source": (
            "optimizer_timeout_budget"
            if effective_seconds > policy.lease_seconds
            else "policy_minimum"
        ),
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
    reservation_time: datetime | None = None,
) -> None:
    row = _budget_row(db, reservation_time or now)
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


def _try_claim_run_budget(
    db: Session,
    *,
    run_id: int,
) -> bool:
    """Claim one run's reservation before touching the aggregate day balance."""
    claimed = db.execute(
        update(AutomationOptimizationRun)
        .where(
            AutomationOptimizationRun.id == run_id,
            AutomationOptimizationRun.budget_settled.is_(False),
        )
        .values(budget_settled=True)
    )
    return int(claimed.rowcount or 0) == 1


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
    recovered_cases = 0
    if run_ids:
        runs = db.scalars(
            select(AutomationOptimizationRun).where(
                AutomationOptimizationRun.id.in_(run_ids),
                AutomationOptimizationRun.status == "processing",
            )
        ).all()
        for run in runs:
            claimed_budget = _try_claim_run_budget(db, run_id=run.id)
            if claimed_budget:
                try:
                    _settle_budget(
                        db,
                        now=current,
                        reserved=run.estimated_cost_micros,
                        actual=run.estimated_cost_micros,
                        reservation_time=_aware(run.created_at),
                    )
                except Exception:
                    db.execute(
                        update(AutomationOptimizationRun)
                        .where(AutomationOptimizationRun.id == run.id)
                        .values(budget_settled=False)
                    )
                    raise
            recovered = db.execute(
                update(OptimizationCaseQueue)
                .where(
                    OptimizationCaseQueue.automation_run_id == run.id,
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
            recovered_cases += int(recovered.rowcount or 0)
            db.execute(
                update(AutomationOptimizationRun)
                .where(
                    AutomationOptimizationRun.id == run.id,
                    AutomationOptimizationRun.status == "processing",
                )
                .values(
                    status="failed",
                    retryable=True,
                    error_message="lease_expired",
                    finished_at=current,
                )
            )
    orphaned = db.execute(
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
    recovered_cases += int(orphaned.rowcount or 0)
    return recovered_cases


def _fair_category_order(
    db: Session,
    categories: list[str],
) -> list[str]:
    """Persistent round-robin order using each category's last planned run."""
    markers: dict[str, tuple[datetime, int] | None] = {}
    for category_key in categories:
        latest_run = db.scalar(
            select(AutomationOptimizationRun)
            .where(AutomationOptimizationRun.category_key == category_key)
            .order_by(
                AutomationOptimizationRun.id.desc()
            )
            .limit(1)
        )
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == category_key
            )
        )
        timestamps = [
            value
            for value in (
                _aware(latest_run.created_at) if latest_run is not None else None,
                _aware(profile.automation_last_triggered_at)
                if profile is not None
                else None,
            )
            if value is not None
        ]
        markers[category_key] = (
            (max(timestamps), latest_run.id if latest_run is not None else 0)
            if timestamps
            else None
        )
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        categories,
        key=lambda category_key: (
            markers[category_key] is not None,
            markers[category_key][0]
            if markers[category_key] is not None
            else minimum,
            markers[category_key][1]
            if markers[category_key] is not None
            else 0,
            category_key,
        ),
    )


def _select_ready_case_cohort(
    db: Session,
    *,
    available: list[OptimizationCaseQueue],
    policy: AutomationPolicy,
    now: datetime,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select one runnable category/prompt cohort without starving later cohorts."""
    lane_policies = db.scalars(
        select(AutomationLanePolicy).order_by(
            AutomationLanePolicy.category_key.asc(),
            AutomationLanePolicy.pipeline_kind.asc(),
            AutomationLanePolicy.generation.asc(),
            AutomationLanePolicy.revision.desc(),
            AutomationLanePolicy.id.desc(),
        )
    ).all()
    strict_lane_mode = any(lane.status == "enabled" for lane in lane_policies)
    if strict_lane_mode:
        last_triggered_at_by_lane: dict[tuple[str, str, int, str, str, str], datetime] = {}
        batches = db.scalars(select(AutomationBatch)).all()
        for batch in batches:
            frozen_policy = _safe_json_object(batch.frozen_policy_json)
            prompt_version = frozen_policy.get("prompt_version")
            if not isinstance(prompt_version, str) or not prompt_version:
                continue
            lane_key = (
                str(batch.category_key),
                str(batch.pipeline_kind),
                int(batch.generation),
                str(batch.mechanism_fingerprint),
                str(batch.route_key),
                prompt_version,
            )
            triggered_at = batch.started_at or batch.created_at
            if triggered_at is None:
                continue
            previous = last_triggered_at_by_lane.get(lane_key)
            if previous is None or _aware(triggered_at) > _aware(previous):
                last_triggered_at_by_lane[lane_key] = triggered_at

        selected_lane, skipped = select_ready_lane(
            available=available,
            lane_policies=lane_policies,
            policy=policy,
            now=now,
            last_triggered_at_by_lane=last_triggered_at_by_lane,
        )
        if selected_lane is None:
            return None, skipped
        category_key = selected_lane["category_key"]
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == category_key
            )
        )
        category_config = _safe_json_object(
            profile.automation_config_json if profile else "{}"
        )
        return (
            {
                "category_key": category_key,
                "category_cases": selected_lane["selected_cases"],
                "category_config": category_config,
                "profile": profile,
                "case_threshold": selected_lane["case_threshold"],
                "max_candidates": selected_lane["max_candidates"],
                "trigger_case": selected_lane["trigger_case"],
                "prompt_version": selected_lane["prompt_version"],
                "same_prompt": selected_lane["selected_cases"],
                "strict_lane_mode": True,
                "lane": selected_lane["lane"],
                "lane_key": selected_lane["lane_key"],
                "pipeline_kind": selected_lane["pipeline_kind"],
                "automation_generation": selected_lane["generation"],
                "mechanism_fingerprint": selected_lane["mechanism_fingerprint"],
                "route_key": selected_lane["route_key"],
                "trigger_reason": selected_lane["trigger_reason"],
            },
            skipped,
        )

    immediate_severities = set(json.loads(policy.immediate_severities_json or "[]"))
    categories = _fair_category_order(
        db,
        list(dict.fromkeys(case.category_key for case in available)),
    )
    skipped: list[dict[str, Any]] = []
    for category_key in categories:
        category_cases = [case for case in available if case.category_key == category_key]
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == category_key
            )
        )
        category_config = _safe_json_object(
            profile.automation_config_json if profile else "{}"
        )
        if category_config.get("enabled") is False:
            skipped.append(
                {
                    "code": "category_disabled",
                    "category_key": category_key,
                    "severity": "blocking",
                    "message": f"类目 {category_key} 的自动优化关闭。",
                }
            )
            continue

        case_threshold = max(
            1, int(category_config.get("case_threshold", policy.case_threshold))
        )
        max_candidates = max(
            1, int(category_config.get("max_candidates", policy.max_candidates))
        )
        trigger_case = next(
            (case for case in category_cases if case.severity in immediate_severities),
            None,
        )
        prompt_versions = list(dict.fromkeys(case.prompt_version for case in category_cases))
        prompt_version = trigger_case.prompt_version if trigger_case else None
        same_prompt: list[OptimizationCaseQueue] = []
        if prompt_version is not None:
            same_prompt = [
                case for case in category_cases if case.prompt_version == prompt_version
            ]
        else:
            for candidate_version in prompt_versions:
                candidate_cases = [
                    case
                    for case in category_cases
                    if case.prompt_version == candidate_version
                ]
                if len(candidate_cases) >= case_threshold:
                    prompt_version = candidate_version
                    same_prompt = candidate_cases
                    break
        if prompt_version is None:
            first_version = prompt_versions[0]
            first_count = sum(
                case.prompt_version == first_version for case in category_cases
            )
            skipped.append(
                {
                    "code": "threshold_wait",
                    "category_key": category_key,
                    "prompt_version": first_version,
                    "available": first_count,
                    "required": case_threshold,
                    "severity": "waiting",
                    "message": (
                        f"类目 {category_key} 的同一提示词版本案例 "
                        f"{first_count}/{case_threshold}，尚未达到组批门槛。"
                    ),
                }
            )
            continue

        cooldown_seconds = max(
            0, int(category_config.get("cooldown_seconds", policy.cooldown_seconds))
        )
        last_triggered_at = (
            profile.automation_last_triggered_at
            if profile is not None
            else policy.last_triggered_at
        )
        cooldown_until = (
            _aware(last_triggered_at) + timedelta(seconds=cooldown_seconds)
            if last_triggered_at is not None
            else None
        )
        if trigger_case is None and cooldown_until is not None and cooldown_until > now:
            skipped.append(
                {
                    "code": "cooldown",
                    "category_key": category_key,
                    "severity": "waiting",
                    "message": f"类目 {category_key} 仍在冷却窗口。",
                    "cooldown_until": cooldown_until.isoformat(),
                }
            )
            continue
        return (
            {
                "category_key": category_key,
                "category_cases": category_cases,
                "category_config": category_config,
                "profile": profile,
                "case_threshold": case_threshold,
                "max_candidates": max_candidates,
                "trigger_case": trigger_case,
                "prompt_version": prompt_version,
                "same_prompt": same_prompt,
            },
            skipped,
        )
    return None, skipped


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
            OptimizationCaseQueue.admission_state.in_(
                ["eligible", "admitted"]
            ),
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

    # Select one category/prompt cohort per tick. A disabled, under-threshold,
    # unconfigured, or gold-incomplete category must not starve another category.
    supplied_adapter = adapter
    remaining = list(available)
    skipped_cohorts: list[dict[str, Any]] = []
    regression_binding: dict[str, Any] | None = None
    while True:
        cohort, skipped = _select_ready_case_cohort(
            db, available=remaining, policy=policy, now=current
        )
        skipped_cohorts.extend(skipped)
        if cohort is None:
            reason = skipped_cohorts[0]
            return {
                "status": (
                    "executor_config_blocked"
                    if reason["code"] in AUTOMATION_CONFIGURATION_BLOCKER_CODES
                    else reason["code"]
                ),
                "reason": reason["code"],
                "message": reason["message"],
                **{
                    key: value
                    for key, value in reason.items()
                    if key not in {"code", "message", "severity"}
                },
                "skipped_cohorts": skipped_cohorts,
                "recovered_leases": recovered,
            }
        category_key = cohort["category_key"]
        profile = cohort["profile"]
        case_threshold = cohort["case_threshold"]
        max_candidates = cohort["max_candidates"]
        trigger_case = cohort["trigger_case"]
        prompt_version = cohort["prompt_version"]
        same_prompt = cohort["same_prompt"]
        strict_lane_mode = bool(cohort.get("strict_lane_mode", False))
        lane = cohort.get("lane")
        lane_key = cohort.get("lane_key")
        trigger_reason = cohort.get("trigger_reason")
        adapter = supplied_adapter or configured_optimization_adapter(
            db, category_key=category_key
        )
        if policy.dry_run:
            break
        if adapter is None:
            skipped_cohorts.append(
                {
                    "code": "optimizer_config_incomplete",
                    "category_key": category_key,
                    "severity": "blocking",
                    "message": f"类目 {category_key} 的优化模型未配置完整。",
                }
            )
            if strict_lane_mode:
                blocked_ids = {case.id for case in same_prompt}
                remaining = [case for case in remaining if case.id not in blocked_ids]
            else:
                remaining = [case for case in remaining if case.category_key != category_key]
            continue
        try:
            if hasattr(adapter, "bind_base_prompt"):
                adapter.bind_base_prompt(db, version=prompt_version)  # type: ignore[attr-defined]
                base_prompt_category = getattr(  # type: ignore[attr-defined]
                    adapter.base_prompt, "category_key", None
                )
                if (
                    base_prompt_category is not None
                    and base_prompt_category != category_key
                ):
                    raise AutomationConfigurationBlocker(
                        "cross_category_prompt",
                        f"类目 {category_key} 的自动优化基础提示词属于其他类目。",
                    )
            if hasattr(adapter, "prepare_regression_binding"):
                regression_binding = adapter.prepare_regression_binding(  # type: ignore[attr-defined]
                    db,
                    base_prompt=adapter.base_prompt,  # type: ignore[attr-defined]
                    category_key=category_key,
                )
        except AutomationConfigurationBlocker as exc:
            skipped_cohorts.append(
                {
                    "code": exc.code,
                    "category_key": category_key,
                    "severity": "blocking",
                    "message": exc.message,
                }
            )
            if strict_lane_mode:
                blocked_ids = {case.id for case in same_prompt}
                remaining = [case for case in remaining if case.id not in blocked_ids]
            else:
                remaining = [case for case in remaining if case.category_key != category_key]
            continue
        except ValueError:
            skipped_cohorts.append(
                {
                    "code": "regression_binding_missing",
                    "category_key": category_key,
                    "severity": "blocking",
                    "message": f"类目 {category_key} 缺少同类目三角色锁定黄金集。",
                }
            )
            if strict_lane_mode:
                blocked_ids = {case.id for case in same_prompt}
                remaining = [case for case in remaining if case.id not in blocked_ids]
            else:
                remaining = [case for case in remaining if case.category_key != category_key]
            continue
        break

    selected = same_prompt[:case_threshold]
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
    if strict_lane_mode:
        frozen_input.update(
            {
                "lane_key": list(lane_key),
                "pipeline_kind": cohort["pipeline_kind"],
                "automation_generation": cohort["automation_generation"],
                "mechanism_fingerprint": cohort["mechanism_fingerprint"],
                "route_key": cohort["route_key"],
            }
        )
    if regression_binding is not None:
        frozen_input["regression_binding"] = regression_binding
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

    batch: AutomationBatch | None = None
    if strict_lane_mode:
        try:
            batch = create_automation_batch(
                db,
                lane=lane,
                selected_cases=selected,
                policy=policy,
                trigger_reason=trigger_reason or "threshold",
                now=current,
            )
        except ValueError as exc:
            return {
                "status": "lane_snapshot_mismatch",
                "reason": "lane_snapshot_mismatch",
                "message": str(exc),
                "lane_key": list(lane_key),
                "recovered_leases": recovered,
            }
        if batch.status in {
            "leased",
            "processing",
            "completed",
            "awaiting_release_review",
        }:
            return {
                "status": "already_planned",
                "batch_id": batch.id,
                "lane_key": list(lane_key),
                "dry_run": policy.dry_run,
                "case_count": len(selected),
                "recovered_leases": recovered,
            }
        frozen_input["batch_id"] = batch.id

    batch_identity = (
        {"batch_id": batch.id, "lane_key": list(lane_key)}
        if batch is not None
        else {}
    )

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
    execution_lease = _execution_lease_details(policy, adapter)
    execution_lease_seconds = int(execution_lease["effective_lease_seconds"])
    frozen_input["policy"]["effective_lease_seconds"] = execution_lease_seconds
    # Compatibility alias for API clients that already consumed this field.
    frozen_input["policy"]["execution_lease_seconds"] = execution_lease_seconds
    lease_until = current + timedelta(seconds=execution_lease_seconds)
    execution_lease["expires_at"] = lease_until.isoformat()
    frozen_input["policy"]["execution_lease"] = execution_lease
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
                **batch_identity,
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
        return {
            "status": "already_planned",
            "run_id": existing.id,
            **batch_identity,
        }

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
            trigger_reason
            or (
                f"immediate:{trigger_case.severity}"
                if trigger_case is not None
                else "case_threshold"
            )
        ),
        case_ids_json=canonical_json(selected_ids),
        frozen_input_json=canonical_json(frozen_input),
        estimated_cost_micros=estimated_cost,
        created_by=worker_id,
        created_at=current,
    )
    db.add(run)
    db.flush()
    if batch is not None:
        batch.status = "completed" if policy.dry_run else "processing"
        batch.started_at = None if policy.dry_run else current
        batch.finished_at = current if policy.dry_run else None
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
    if profile is not None:
        profile.automation_last_triggered_at = current
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
            "effective_lease_seconds": execution_lease_seconds,
            "execution_lease_seconds": execution_lease_seconds,
            "execution_lease": execution_lease,
            **batch_identity,
        },
        event_key=f"automation-run-planned:{run.run_key}",
    )
    if policy.dry_run or adapter is None:
        return {
            "status": run.status,
            "lifecycle_status": automation_lifecycle_status(run.status),
            "run_id": run.id,
            "dry_run": policy.dry_run,
            "case_count": len(selected_ids),
            "recovered_leases": recovered,
            **batch_identity,
        }

    db.commit()
    run.status = "processing"
    if batch is not None:
        batch.status = "processing"
        batch.started_at = current
    append_audit_event(
        db,
        category="automation",
        action="executor_started",
        subject_type="automation_optimization_run",
        subject_id=run.id,
        actor=worker_id,
        payload={"estimated_cost_micros": estimated_cost, **batch_identity},
        event_key=f"automation-run-processing:{run.run_key}",
    )
    db.commit()
    budget_settled = False
    budget_claimed = False
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
        if not _try_claim_run_budget(db, run_id=run.id):
            raise RuntimeError("automation_lease_lost")
        budget_claimed = True
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
                "effective_lease_seconds": execution_lease_seconds,
                "execution_lease": execution_lease,
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
        if batch is not None:
            batch.status = "completed"
            batch.finished_at = current
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
                "effective_lease_seconds": execution_lease_seconds,
                "execution_lease": execution_lease,
                **batch_identity,
            },
            event_key=f"automation-run-reviewed:{run.run_key}",
        )
        return {
            "status": run.status,
            "lifecycle_status": automation_lifecycle_status(run.status),
            "run_id": run.id,
            "candidate_count": run.candidate_count,
            "effective_lease_seconds": execution_lease_seconds,
            "execution_lease_seconds": execution_lease_seconds,
            **batch_identity,
        }
    except Exception as exc:
        safe_error, retryable = _safe_executor_error(exc)
        if not budget_settled:
            if budget_claimed:
                _settle_budget(
                    db,
                    now=current,
                    reserved=estimated_cost,
                    actual=estimated_cost,
                )
                budget_settled = True
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
            elif not budget_claimed:
                settled = _try_claim_run_budget(db, run_id=run.id)
                if settled:
                    _settle_budget(
                        db,
                        now=current,
                        reserved=estimated_cost,
                        actual=estimated_cost,
                    )
                if not settled:
                    safe_error, retryable = (
                        "automation_budget_settlement_conflict",
                        False,
                    )
        run.status = "failed"
        run.error_message = safe_error
        run.retryable = retryable
        run.finished_at = current
        if batch is not None:
            batch.status = "failed"
            batch.error_code = safe_error
            batch.error_message = safe_error
            batch.finished_at = current
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
                **batch_identity,
            },
            event_key=f"automation-run-failed:{run.run_key}",
        )
        return {
            "status": "failed",
            "run_id": run.id,
            "retry_at": retry_at.isoformat() if retry_at else None,
            "recovered_leases": recovered,
            **batch_identity,
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
    readiness: str | None = None,
    blockers: list[dict[str, Any]] | None = None,
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
    if readiness is not None:
        row.readiness = readiness[:20]
    if blockers is not None:
        row.blockers_json = canonical_json(blockers)
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
    active_lease_owners = {
        owner
        for owner in db.scalars(
            select(OptimizationCaseQueue.lease_owner).where(
                OptimizationCaseQueue.status == "processing",
                OptimizationCaseQueue.lease_owner.is_not(None),
                OptimizationCaseQueue.lease_expires_at.is_not(None),
                OptimizationCaseQueue.lease_expires_at > current,
            )
        ).all()
        if owner
    }
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
        heartbeat_active = seen_at is not None and seen_at >= active_cutoff
        lease_active = row.worker_id in active_lease_owners
        active = heartbeat_active or lease_active
        if active:
            active_count += 1
        workers.append(
            {
                "worker_id": row.worker_id,
                "active": active,
                "active_reason": (
                    "heartbeat"
                    if heartbeat_active
                    else "processing_lease"
                    if lease_active
                    else "stale"
                ),
                "started_at": row.started_at,
                "last_seen_at": row.last_seen_at,
                "last_tick_at": row.last_tick_at,
                "last_status": row.last_status,
                "readiness": row.readiness,
                "blockers": _safe_json_list(row.blockers_json),
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
            OptimizationCaseQueue.admission_state.in_(
                ["eligible", "admitted"]
            ),
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
        "skipped_cohorts": [],
    }
    category_key: str | None = None
    adapter: RealOptimizationAdapter | None = None
    if not available:
        block("queue_empty", "没有达到可组批条件的纠偏案例。", severity="info")
    else:
        cohort, skipped_cohorts = _select_ready_case_cohort(
            db, available=available, policy=policy, now=current
        )
        queue["skipped_cohorts"] = skipped_cohorts
        if cohort is None:
            for item in skipped_cohorts:
                block(item["code"], item["message"], severity=item["severity"])
            if skipped_cohorts:
                first = skipped_cohorts[0]
                category_key = first.get("category_key")
                queue.update(
                    {
                        "next_category_key": first.get("category_key"),
                        "next_prompt_version": first.get("prompt_version"),
                        "available_for_prompt": first.get("available", 0),
                        "required_for_prompt": first.get(
                            "required", policy.case_threshold
                        ),
                    }
                )
            cohort = None
        if cohort is None and category_key is not None:
            adapter = configured_optimization_adapter(db, category_key=category_key)
            if adapter is None:
                block(
                    "optimizer_config_incomplete",
                    "优化模型缺少密钥、输入上限或非零计价。",
                    severity="warning" if policy.dry_run else "blocking",
                )
        if cohort is not None:
            category_key = cohort["category_key"]
            case_threshold = cohort["case_threshold"]
            prompt_version = cohort["prompt_version"]
            same_prompt = cohort["same_prompt"]
            queue.update(
                {
                    "next_category_key": category_key,
                    "next_prompt_version": prompt_version,
                    "available_for_prompt": len(same_prompt),
                    "required_for_prompt": case_threshold,
                }
            )
        if cohort is not None:
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
                        db,
                        base_prompt=adapter.base_prompt,
                        category_key=category_key,
                        freeze_automatic_binding=False,
                    )
                except AutomationConfigurationBlocker as exc:
                    block(exc.code, exc.message)
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
            policy = db.get(AutomationPolicy, 1)
            if policy is None:
                policy = AutomationPolicy(id=1)
                db.add(policy)
                db.flush()
            runtime = automation_runtime_status(db, policy)
            persisted_result = {
                **result,
                "readiness": runtime["status"],
                "blockers": runtime["blockers"],
                "checked_at": runtime["checked_at"].isoformat(),
            }
            record_automation_worker_status(
                db,
                worker_id=worker_id,
                status=str(result.get("status", "unknown")),
                result=persisted_result,
                readiness=runtime["status"],
                blockers=runtime["blockers"],
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
                readiness="blocked",
                blockers=[
                    {
                        "code": "worker_error",
                        "message": "Worker 最近一次检查失败。",
                        "severity": "blocking",
                    }
                ],
            )
        return {"status": "worker_error", "error_message": safe_error}
