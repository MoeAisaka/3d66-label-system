"""EvaluationPackage aggregate, canonical manifest and human release gate."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import append_audit_event
from .category_pipeline import (
    DIMENSION_OPTIONS,
    dimension_options_from_definition,
    dimension_selection_payload,
    legacy_preprocess_to_pipeline,
    validate_pipeline_config,
)
from .database import get_db
from .models import (
    AutomationOptimizationRun,
    DimensionRoutePolicy,
    DimensionSchema,
    EvaluationCategoryProfile,
    EvaluationPackage,
    ModelConfig,
    PromptMetricSnapshot,
    PromptRegressionRun,
    PromptVersion,
    SampleSet,
    SamplingPolicy,
    StrategyBundle,
    User,
)
from .strategy_bundle import (
    build_model_config_snapshot,
    build_strategy_snapshot,
    safe_strategy_snapshot_payload,
)
from .optimization_automation import category_bundle_contract_errors


MANIFEST_SCHEMA_VERSION = "evaluation-package-manifest-v1"
TERMINAL_REGRESSION_STATUSES = frozenset(
    {"passed", "regressed", "failed", "error", "cancelled"}
)
COMPLETED_AUTOMATION_STATUSES = frozenset(
    {"succeeded", "awaiting_release_review"}
)


def _category_pipeline(
    profile: EvaluationCategoryProfile,
    db: Session | None = None,
) -> dict[str, Any]:
    try:
        raw = json.loads(profile.pipeline_config_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict) or raw.get("schema_version") != "category-pipeline-v1":
        try:
            legacy = json.loads(profile.preprocess_config_json or "{}")
        except json.JSONDecodeError:
            legacy = {}
        raw = legacy_preprocess_to_pipeline(profile.category_key, legacy)
    allowed_dimension_keys = [item["key"] for item in DIMENSION_OPTIONS]
    if (
        db is not None
        and profile.dimension_schema_key
        and profile.dimension_schema_version
    ):
        schema = db.scalar(
            select(DimensionSchema).where(
                DimensionSchema.schema_key == profile.dimension_schema_key,
                DimensionSchema.version == profile.dimension_schema_version,
            )
        )
        if schema is not None:
            try:
                definition = json.loads(schema.definition_json)
            except json.JSONDecodeError:
                definition = None
            options = dimension_options_from_definition(definition)
            if options:
                allowed_dimension_keys = [item["key"] for item in options]
    try:
        return validate_pipeline_config(
            raw,
            allowed_dimension_keys=allowed_dimension_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"类目流水线配置损坏：{exc}") from None


class EvaluationPackageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_key: str | None = Field(default=None, min_length=1, max_length=160)
    request_key: str | None = Field(default=None, min_length=1, max_length=160)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    category_key: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{2,39}$"
    )
    regression_run_id: int = Field(ge=1)
    automation_run_id: int | None = Field(default=None, ge=1)
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    dimension_schema_id: int | None = Field(default=None, ge=1)
    dimension_route_policy_id: int | None = Field(default=None, ge=1)
    sample_set_id: int | None = Field(default=None, ge=1)
    baseline_strategy_bundle_id: int | None = Field(default=None, ge=1)
    candidate_strategy_bundle_id: int | None = Field(default=None, ge=1)
    metric_snapshot_id: int | None = Field(default=None, ge=1)
    ai_recommendation: str | None = Field(default=None, max_length=40)
    change_summary: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def validate_package_key(self) -> "EvaluationPackageCreateRequest":
        keys = [
            value.strip()
            for value in (
                self.package_key,
                self.request_key,
                self.idempotency_key,
            )
            if value is not None and value.strip()
        ]
        if len(set(keys)) > 1:
            raise ValueError("评测包幂等键只能填写一个且不能互相冲突")
        if not keys:
            raise ValueError("创建评测包必须填写 package_key")
        return self

    def normalized_package_key(self) -> str:
        return str(
            self.package_key or self.request_key or self.idempotency_key
        ).strip()


class EvaluationPackageFromAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_key: str | None = Field(default=None, min_length=1, max_length=160)
    regression_run_id: int | None = Field(default=None, ge=1)
    metric_snapshot_id: int | None = Field(default=None, ge=1)
    dimension_schema_id: int | None = Field(default=None, ge=1)
    dimension_route_policy_id: int | None = Field(default=None, ge=1)
    ai_recommendation: str | None = Field(default=None, max_length=40)
    change_summary: str | None = Field(default=None, max_length=10_000)


class EvaluationPackageReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=4000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("二审意见不得为空白")
        return normalized


class EvaluationPackageDecisionRequest(EvaluationPackageReviewRequest):
    decision: Literal["approved", "rejected"]


class EvaluationPackagePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=4000)


class EvaluationPackageArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("归档原因不得为空白")
        return normalized


def canonical_json(value: Any) -> str:
    """Return byte-stable UTF-8 JSON used by package manifests."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("评测包冻结清单包含无法规范化的数据") from exc


def canonical_manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def _loads_object(value: str | None, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}必须是 JSON 对象")
    return payload


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.isoformat()


def _secret_safe(payload: dict[str, Any]) -> dict[str, Any]:
    forbidden = {
        "apikey",
        "authorization",
        "cookie",
        "credentials",
        "encryptedapikey",
        "password",
        "rawresponse",
        "rawresponsea",
        "rawresponseb",
        "rawresponseriskreview",
        "sessiontoken",
    }

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            safe: dict[str, Any] = {}
            for key, value in node.items():
                normalized = "".join(
                    character
                    for character in str(key).lower()
                    if character.isalnum()
                )
                if (
                    normalized in forbidden
                    or "rawresponse" in normalized
                    or "rawmodeloutput" in normalized
                    or "rawprovideroutput" in normalized
                ):
                    continue
                safe[str(key)] = strip(value)
            return safe
        if isinstance(node, list):
            return [strip(value) for value in node]
        return node

    return safe_strategy_snapshot_payload(strip(payload))


def _prompt_snapshot(prompt: PromptVersion) -> dict[str, Any]:
    definition = {
        "id": prompt.id,
        "stage": prompt.stage,
        "name": prompt.name,
        "version": prompt.version,
        "rubric_version": prompt.rubric_version,
        "system_prompt": prompt.system_prompt,
        "user_prompt": prompt.user_prompt,
        "change_note": prompt.change_note,
    }
    safe = _secret_safe(definition)
    safe["canonical_hash"] = canonical_manifest_hash(safe)
    return safe


def _resolve_prompt(
    db: Session,
    *,
    prompt_id: int,
    stage: str,
    expected_version: str,
) -> PromptVersion:
    prompt = db.get(PromptVersion, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail=f"{stage} 阶段提示词不存在")
    if prompt.stage != stage or prompt.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"{stage} 阶段提示词与候选 StrategyBundle 不一致",
        )
    return prompt


def _strategy_snapshot_from_bundle(
    db: Session,
    *,
    bundle: StrategyBundle,
    preferred_snapshot: str | None,
    prompt_a_id: int | None,
    prompt_b_id: int | None,
) -> tuple[dict[str, Any], PromptVersion, PromptVersion | None]:
    if preferred_snapshot and preferred_snapshot.strip() not in {"", "{}"}:
        snapshot = safe_strategy_snapshot_payload(preferred_snapshot)
        if (
            snapshot.get("bundle_id") != bundle.id
            or snapshot.get("canonical_hash") != bundle.canonical_hash
        ):
            raise HTTPException(
                status_code=409,
                detail="回归冻结策略与 StrategyBundle 身份不一致",
            )
        prompt_a_data = snapshot.get("prompt_a")
        prompt_b_data = snapshot.get("prompt_b")
        if not isinstance(prompt_a_data, dict):
            raise HTTPException(status_code=409, detail="回归快照缺少 A 阶段提示词")
        resolved_a_id = prompt_a_id or int(prompt_a_data.get("id") or 0)
        resolved_b_id = (
            prompt_b_id
            or (
                int(prompt_b_data.get("id") or 0)
                if isinstance(prompt_b_data, dict)
                else None
            )
        )
    else:
        resolved_a_id = prompt_a_id or 0
        resolved_b_id = prompt_b_id
        snapshot = {}
    if resolved_a_id <= 0:
        matches = db.scalars(
            select(PromptVersion).where(
                PromptVersion.stage == "A",
                PromptVersion.version == bundle.prompt_a_version,
            )
        ).all()
        if len(matches) != 1:
            raise HTTPException(
                status_code=409,
                detail="候选 StrategyBundle 无法唯一解析 A 阶段提示词",
            )
        resolved_a_id = matches[0].id
    prompt_a = _resolve_prompt(
        db,
        prompt_id=resolved_a_id,
        stage="A",
        expected_version=bundle.prompt_a_version,
    )

    def assert_frozen_prompt(
        prompt: PromptVersion,
        frozen: Any,
        *,
        stage: str,
    ) -> None:
        if not isinstance(frozen, dict):
            raise HTTPException(
                status_code=409,
                detail=f"回归冻结策略缺少 {stage} 阶段提示词定义",
            )
        live = _secret_safe(
            {
                "id": prompt.id,
                "stage": prompt.stage,
                "name": prompt.name,
                "version": prompt.version,
                "rubric_version": prompt.rubric_version,
                "system_prompt": prompt.system_prompt,
                "user_prompt": prompt.user_prompt,
            }
        )
        if any(frozen.get(key) != value for key, value in live.items()):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{stage} 阶段提示词已偏离回归冻结策略，"
                    "请重新回归并创建新评测包"
                ),
            )

    if snapshot:
        assert_frozen_prompt(
            prompt_a,
            snapshot.get("prompt_a"),
            stage="A",
        )
    prompt_b: PromptVersion | None = None
    if bundle.prompt_b_version is not None:
        if resolved_b_id is None:
            matches = db.scalars(
                select(PromptVersion).where(
                    PromptVersion.stage == "B",
                    PromptVersion.version == bundle.prompt_b_version,
                )
            ).all()
            if len(matches) != 1:
                raise HTTPException(
                    status_code=409,
                    detail="候选 StrategyBundle 无法唯一解析 B 阶段提示词",
                )
            resolved_b_id = matches[0].id
        prompt_b = _resolve_prompt(
            db,
            prompt_id=resolved_b_id,
            stage="B",
            expected_version=bundle.prompt_b_version,
        )
        if snapshot:
            assert_frozen_prompt(
                prompt_b,
                snapshot.get("prompt_b"),
                stage="B",
            )
    elif resolved_b_id is not None:
        raise HTTPException(
            status_code=409,
            detail="单提示词 StrategyBundle 不能冻结 B 阶段提示词",
        )
    if not snapshot:
        policy = (
            db.scalar(
                select(SamplingPolicy).where(
                    SamplingPolicy.revision
                    == bundle.sampling_policy_revision
                )
            )
            if bundle.sampling_policy_revision is not None
            else None
        )
        try:
            snapshot = safe_strategy_snapshot_payload(
                build_strategy_snapshot(
                    bundle,
                    prompt_a,
                    prompt_b,
                    policy,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return snapshot, prompt_a, prompt_b


def _regression_recommendation(run: PromptRegressionRun) -> str:
    if run.regression_mode == "paired":
        if run.status not in TERMINAL_REGRESSION_STATUSES:
            return "pending"
        return (
            "pass"
            if run.status == "passed" and run.recommendation == "pass"
            else "fail"
        )
    metrics = _loads_object(run.metrics_json, label="回归指标")
    if run.status not in TERMINAL_REGRESSION_STATUSES:
        return "pending"
    return (
        "pass"
        if run.status == "passed"
        and metrics.get("release_gate_passed") is True
        else "fail"
    )


def _regression_snapshot(run: PromptRegressionRun) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in sorted(run.items, key=lambda value: value.id):
        truth = _loads_object(item.truth_snapshot_json, label="回归真值快照")
        comparison = _loads_object(item.comparison_json, label="回归比较证据")
        baseline = _loads_object(item.baseline_result_json, label="基线结果快照")
        candidate = _loads_object(item.candidate_result_json, label="候选结果快照")
        items.append(
            {
                "id": item.id,
                "sample_item_id": item.sample_item_id,
                "asset_id": item.sample_item.asset_id,
                "asset_name": item.sample_item.asset.original_name,
                "image_url": f"/api/assets/{item.sample_item.asset_id}/file",
                "sample_role": item.sample_role,
                "status": item.status,
                "passed": item.passed,
                "truth_snapshot": truth or None,
                "comparison": comparison,
                "baseline_result": baseline,
                "candidate_result": candidate,
                "baseline_evaluation_id": item.baseline_evaluation_id,
                "candidate_evaluation_id": item.candidate_evaluation_id,
                "evaluation_id": item.evaluation_id,
            }
        )
    return _secret_safe(
        {
            "id": run.id,
            "name": run.name,
            "mode": run.regression_mode,
            "status": run.status,
            "terminal": run.status in TERMINAL_REGRESSION_STATUSES,
            "recommendation": _regression_recommendation(run),
            "threshold": run.threshold,
            "total": run.total,
            "completed": run.completed,
            "passed": run.passed,
            "failed": run.failed,
            "sample_set_version": run.sample_set_version,
            "sample_manifest": _loads_object(
                run.sample_manifest_json, label="回归黄金集清单"
            ),
            "metric_rules_version": run.metric_rules_version,
            "metric_rules": _loads_object(
                run.metric_rules_json, label="回归指标规则"
            ),
            "metrics": _loads_object(run.metrics_json, label="回归指标"),
            "summary": _loads_object(run.summary_json, label="回归摘要"),
            "legacy_paired_approval": {
                "status": run.approval_status,
                "approved_by": run.approved_by,
                "approval_note": run.approval_note,
                "approved_at": _iso(run.approved_at),
                "release_authority": False,
            },
            "created_by": run.created_by,
            "created_at": _iso(run.created_at),
            "finished_at": _iso(run.finished_at),
            "items": items,
        }
    )


def _golden_sample_snapshot(
    sample_set: SampleSet,
    regression: PromptRegressionRun,
) -> dict[str, Any]:
    frozen_manifest = _loads_object(
        regression.sample_manifest_json, label="回归黄金集清单"
    )
    frozen_by_item = {
        int(item.get("sample_item_id")): item
        for item in frozen_manifest.get("items", [])
        if isinstance(item, dict)
        and isinstance(item.get("sample_item_id"), int)
    }
    roles = {item.sample_item_id: item.sample_role for item in regression.items}
    items: list[dict[str, Any]] = []
    for item in sorted(sample_set.items, key=lambda value: value.id):
        frozen = frozen_by_item.get(item.id, {})
        truth_snapshot = frozen.get("truth_snapshot")
        truth = (
            truth_snapshot.get("truth")
            if isinstance(truth_snapshot, dict)
            else _loads_object(item.truth_json, label="黄金样本真值")
        )
        items.append(
            {
                "sample_item_id": item.id,
                "asset_id": item.asset_id,
                "asset_name": item.asset.original_name,
                "asset_sha256": item.asset.sha256,
                "mime_type": item.asset.mime_type,
                "image_url": f"/api/assets/{item.asset_id}/file",
                "role": frozen.get("role") or roles.get(item.id),
                "expected_level": item.expected_level,
                "expected_category": item.expected_category,
                "truth_revision": frozen.get("truth_revision", item.truth_revision),
                "truth": truth or {},
                "source_evaluation_id": item.source_result_id,
            }
        )
    item_manifest_hash = hashlib.sha256(
        canonical_json(items).encode("utf-8")
    ).hexdigest()
    return _secret_safe(
        {
            "id": sample_set.id,
            "name": sample_set.name,
            "description": sample_set.description,
            "kind": sample_set.kind,
            "status": sample_set.status,
            "category_key": sample_set.category_key,
            "item_count": len(items),
            "judgable_item_count": sum(
                1 for item in items if bool(item["truth"])
            ),
            "items_manifest_hash": item_manifest_hash,
            "items": items,
        }
    )


def _dimension_snapshot(
    *,
    candidate_strategy: dict[str, Any],
    dimension_schema: DimensionSchema | None,
    route_policy: DimensionRoutePolicy | None,
    category_pipeline: dict[str, Any],
) -> dict[str, Any]:
    explicit_schema = None
    if dimension_schema is not None:
        explicit_schema = {
            "id": dimension_schema.id,
            "schema_key": dimension_schema.schema_key,
            "version": dimension_schema.version,
            "schema_type": dimension_schema.schema_type,
            "family_key": dimension_schema.family_key,
            "display_name": dimension_schema.display_name,
            "status": dimension_schema.status,
            "canonical_hash": dimension_schema.canonical_hash,
            "definition": _loads_object(
                dimension_schema.definition_json,
                label="维度 Schema 定义",
            ),
        }
    explicit_route = None
    if route_policy is not None:
        explicit_route = {
            "id": route_policy.id,
            "policy_key": route_policy.policy_key,
            "version": route_policy.version,
            "display_name": route_policy.display_name,
            "status": route_policy.status,
            "canonical_hash": route_policy.canonical_hash,
            "definition": _loads_object(
                route_policy.definition_json,
                label="维度路由策略定义",
            ),
        }
    explicit_definition = (
        explicit_schema.get("definition")
        if isinstance(explicit_schema, dict)
        else None
    )
    category_selection = dimension_selection_payload(
        category_pipeline,
        dimension_options=(
            dimension_options_from_definition(explicit_definition)
            if isinstance(explicit_definition, dict)
            else list(DIMENSION_OPTIONS)
        ) or list(DIMENSION_OPTIONS),
        schema_key=(dimension_schema.schema_key if dimension_schema else None),
        schema_version=(dimension_schema.version if dimension_schema else None),
        schema_hash=(dimension_schema.canonical_hash if dimension_schema else None),
    )
    return _secret_safe(
        {
            "strategy_schema_version": candidate_strategy.get("schema_version"),
            "resolved_schema_contract_version": candidate_strategy.get(
                "resolved_schema_contract_version"
            ),
            "route_policy_id": candidate_strategy.get(
                "dimension_route_policy_id"
            ),
            "route_policy_snapshot": candidate_strategy.get(
                "dimension_route_policy_snapshot"
            ),
            "dimension_schema_set": candidate_strategy.get(
                "dimension_schema_set"
            ),
            "evaluation_profile_set": candidate_strategy.get(
                "evaluation_profile_set_snapshot"
            ),
            "label_field_set": candidate_strategy.get("label_field_set"),
            "explicit_schema": explicit_schema,
            "explicit_route_policy": explicit_route,
            "category_selection": category_selection,
        }
    )


def _category_snapshot(
    db: Session,
    *,
    category_key: str,
) -> dict[str, Any]:
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == category_key
        )
    )
    if profile is None:
        return {"category_key": category_key, "profile": None}
    automation = _loads_object(
        profile.automation_config_json, label="类目自动化配置快照"
    )
    return _secret_safe(
        {
            "category_key": category_key,
            "profile": {
                "id": profile.id,
                "display_name": profile.display_name,
                "status": profile.status,
                "rubric_version": profile.rubric_version,
                "dimension_schema_key": profile.dimension_schema_key,
                "dimension_schema_version": profile.dimension_schema_version,
                "pipeline_revision": profile.pipeline_revision,
                "automation_revision": profile.automation_revision,
                "baseline_strategy_bundle_id": automation.get(
                    "baseline_strategy_bundle_id"
                ),
                "pipeline_config": _category_pipeline(profile, db),
            },
        }
    )


def _automation_snapshot(run: AutomationOptimizationRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    result = _loads_object(run.result_json, label="自动优化结果")
    candidates = result.get("candidates")
    candidate_evidence: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for index, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                continue
            system_prompt = str(candidate.get("system_prompt") or "")
            user_prompt = str(candidate.get("user_prompt") or "")
            candidate_evidence.append(
                {
                    "index": index,
                    "change_note": str(candidate.get("change_note") or ""),
                    "system_prompt_hash": hashlib.sha256(
                        system_prompt.encode("utf-8")
                    ).hexdigest(),
                    "user_prompt_hash": hashlib.sha256(
                        user_prompt.encode("utf-8")
                    ).hexdigest(),
                }
            )
    snapshot = {
        "id": run.id,
        "run_key": run.run_key,
        "category_key": run.category_key,
        "base_prompt_version": run.base_prompt_version,
        "policy_revision": run.policy_revision,
        "status": run.status,
        "dry_run": run.dry_run,
        "trigger_reason": run.trigger_reason,
        "case_ids": json.loads(run.case_ids_json or "[]"),
        "candidate_count": run.candidate_count,
        "estimated_cost_micros": run.estimated_cost_micros,
        "actual_cost_micros": run.actual_cost_micros,
        "usage": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "total_tokens": run.total_tokens,
        },
        "created_by": run.created_by,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
        "result_binding": {
            "prompt_ids": result.get("prompt_ids") or [],
            "regression_ids": result.get("regression_ids") or [],
            "regression_plan": result.get("regression"),
            "release_requires_human_review": result.get(
                "release_requires_human_review", True
            ),
            "candidate_evidence": candidate_evidence,
        },
        "publishes_automatically": False,
    }
    return _secret_safe(snapshot)


def _metric_snapshot(metric: PromptMetricSnapshot | None) -> dict[str, Any] | None:
    if metric is None:
        return None
    return _secret_safe(
        {
            "id": metric.id,
            "prompt_id": metric.prompt_id,
            "task_set_key": metric.task_set_key,
            "task_set_hash": metric.task_set_hash,
            "evaluation_ids": json.loads(metric.evaluation_ids_json or "[]"),
            "metrics": _loads_object(metric.metrics_json, label="提示词指标快照"),
            "total_count": metric.total_count,
            "reviewed_count": metric.reviewed_count,
            "created_by": metric.created_by,
            "created_at": _iso(metric.created_at),
        }
    )


def _automation_result_ids(run: AutomationOptimizationRun) -> tuple[list[int], list[int]]:
    result = _loads_object(run.result_json, label="自动优化结果")
    prompt_ids = result.get("prompt_ids") or []
    regression_ids = result.get("regression_ids") or []
    if (
        not isinstance(prompt_ids, list)
        or not all(isinstance(value, int) for value in prompt_ids)
        or not isinstance(regression_ids, list)
        or not all(isinstance(value, int) for value in regression_ids)
    ):
        raise HTTPException(status_code=409, detail="自动优化结果缺少候选/回归身份")
    return prompt_ids, regression_ids


def _build_package_material(
    db: Session,
    payload: EvaluationPackageCreateRequest,
) -> dict[str, Any]:
    regression = db.get(PromptRegressionRun, payload.regression_run_id)
    if regression is None:
        raise HTTPException(status_code=404, detail="回归任务不存在")
    sample_set_id = payload.sample_set_id or regression.sample_set_id
    sample_set = db.get(SampleSet, sample_set_id)
    if sample_set is None:
        raise HTTPException(status_code=404, detail="黄金 SampleSet 不存在")
    if sample_set.id != regression.sample_set_id:
        raise HTTPException(status_code=409, detail="回归任务不属于指定黄金 SampleSet")
    if sample_set.kind != "golden" or sample_set.status != "locked":
        raise HTTPException(status_code=409, detail="评测包只能冻结已锁定黄金 SampleSet")
    category_key = payload.category_key or sample_set.category_key
    if sample_set.category_key != category_key:
        raise HTTPException(status_code=409, detail="评测包与黄金集类目不一致")
    if any(item.asset.category_key != category_key for item in sample_set.items):
        raise HTTPException(status_code=409, detail="黄金集包含其他类目素材，不能生成评测包")

    automation = (
        db.get(AutomationOptimizationRun, payload.automation_run_id)
        if payload.automation_run_id is not None
        else None
    )
    if payload.automation_run_id is not None and automation is None:
        raise HTTPException(status_code=404, detail="自动优化 Run 不存在")
    if automation is not None:
        if automation.status not in COMPLETED_AUTOMATION_STATUSES or automation.dry_run:
            raise HTTPException(status_code=409, detail="自动优化 Run 尚未完成真实候选生成")
        if automation.category_key != category_key:
            raise HTTPException(status_code=409, detail="自动优化 Run 与黄金集类目不一致")
        prompt_ids, regression_ids = _automation_result_ids(automation)
        if regression.id not in regression_ids:
            raise HTTPException(status_code=409, detail="回归任务不属于该自动优化 Run")
        if regression.trigger_prompt_id not in prompt_ids:
            raise HTTPException(status_code=409, detail="回归候选提示词不属于该自动优化 Run")

    candidate_bundle_id = (
        payload.candidate_strategy_bundle_id
        or regression.candidate_strategy_bundle_id
    )
    if candidate_bundle_id is None and regression.regression_mode != "paired":
        result_bundle_ids = {
            item.evaluation.strategy_bundle_id
            for item in regression.items
            if item.evaluation is not None
            and item.evaluation.strategy_bundle_id is not None
        }
        if len(result_bundle_ids) == 1:
            candidate_bundle_id = result_bundle_ids.pop()
    if candidate_bundle_id is None:
        raise HTTPException(status_code=409, detail="回归任务缺少候选 StrategyBundle")
    candidate_bundle = db.get(StrategyBundle, candidate_bundle_id)
    if candidate_bundle is None:
        raise HTTPException(status_code=404, detail="候选 StrategyBundle 不存在")
    if (
        regression.candidate_strategy_bundle_id is not None
        and regression.candidate_strategy_bundle_id != candidate_bundle.id
    ):
        raise HTTPException(status_code=409, detail="候选 StrategyBundle 与回归任务不一致")

    baseline_bundle_id = (
        payload.baseline_strategy_bundle_id
        if payload.baseline_strategy_bundle_id is not None
        else regression.baseline_strategy_bundle_id
    )
    baseline_bundle = (
        db.get(StrategyBundle, baseline_bundle_id)
        if baseline_bundle_id is not None
        else None
    )
    if baseline_bundle_id is not None and baseline_bundle is None:
        raise HTTPException(status_code=404, detail="基线 StrategyBundle 不存在")
    if (
        regression.baseline_strategy_bundle_id is not None
        and baseline_bundle_id != regression.baseline_strategy_bundle_id
    ):
        raise HTTPException(status_code=409, detail="基线 StrategyBundle 与回归任务不一致")

    candidate_strategy, prompt_a, prompt_b = _strategy_snapshot_from_bundle(
        db,
        bundle=candidate_bundle,
        preferred_snapshot=regression.candidate_strategy_snapshot_json,
        prompt_a_id=payload.prompt_a_id or regression.prompt_a_id,
        prompt_b_id=(
            payload.prompt_b_id
            if candidate_bundle.prompt_b_version is not None
            else None
        )
        or (
            regression.prompt_b_id
            if candidate_bundle.prompt_b_version is not None
            else None
        ),
    )
    if any(
        prompt.category_key != category_key
        for prompt in (prompt_a, prompt_b)
        if prompt is not None
    ):
        raise HTTPException(status_code=409, detail="评测包候选提示词属于其他评测类目")
    source_automation_ids = {
        prompt.source_automation_run_id
        for prompt in (prompt_a, prompt_b)
        if prompt is not None and prompt.source_automation_run_id is not None
    }
    if len(source_automation_ids) > 1:
        raise HTTPException(
            status_code=409,
            detail="候选提示词来自不同自动优化 Run，不能形成同一评测包",
        )
    if source_automation_ids and (
        automation is None or automation.id not in source_automation_ids
    ):
        raise HTTPException(
            status_code=409,
            detail="自动优化候选必须冻结其来源 Automation Run",
        )
    baseline_strategy = None
    if baseline_bundle is not None:
        baseline_strategy, _, _ = _strategy_snapshot_from_bundle(
            db,
            bundle=baseline_bundle,
            preferred_snapshot=regression.baseline_strategy_snapshot_json,
            prompt_a_id=None,
            prompt_b_id=None,
        )

    dimension_schema = (
        db.get(DimensionSchema, payload.dimension_schema_id)
        if payload.dimension_schema_id is not None
        else None
    )
    if payload.dimension_schema_id is not None and dimension_schema is None:
        raise HTTPException(status_code=404, detail="维度 Schema 不存在")
    category_profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == category_key
        )
    )
    if category_profile is None:
        raise HTTPException(status_code=409, detail="评测包类目配置不存在")
    pipeline = _category_pipeline(category_profile, db)
    pipeline_mode = pipeline.get("prompt_mode")
    package_mode = "single" if prompt_b is None else "ab"
    if pipeline_mode == "follow":
        raise HTTPException(
            status_code=409,
            detail="跟随任务类目不能发布固定评测包基线，请先由管理员选择单提示词或 A/B 模式",
        )
    if pipeline_mode != package_mode:
        raise HTTPException(
            status_code=409,
            detail="评测包提示词模式与类目流水线合同不一致",
        )
    if (
        dimension_schema is not None
        and category_profile is not None
        and dimension_schema.schema_key != category_profile.dimension_schema_key
    ):
        raise HTTPException(
            status_code=409,
            detail="维度 Schema 与评测包类目配置不一致",
        )
    route_policy = (
        db.get(DimensionRoutePolicy, payload.dimension_route_policy_id)
        if payload.dimension_route_policy_id is not None
        else None
    )
    if payload.dimension_route_policy_id is not None and route_policy is None:
        raise HTTPException(status_code=404, detail="维度路由策略不存在")
    metric = (
        db.get(PromptMetricSnapshot, payload.metric_snapshot_id)
        if payload.metric_snapshot_id is not None
        else None
    )
    if payload.metric_snapshot_id is not None and metric is None:
        raise HTTPException(status_code=404, detail="指标快照不存在")
    if metric is not None and metric.prompt_id not in {
        prompt_a.id,
        prompt_b.id if prompt_b is not None else None,
    }:
        raise HTTPException(status_code=409, detail="指标快照不属于评测包候选提示词")

    recommendation = (
        payload.ai_recommendation or _regression_recommendation(regression)
    ).strip()
    change_summary = (
        payload.change_summary
        if payload.change_summary is not None
        else (prompt_b.change_note if prompt_b is not None else prompt_a.change_note)
    ).strip()
    regression_snapshot = _regression_snapshot(regression)
    manifest = _secret_safe(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "category": _category_snapshot(db, category_key=category_key),
            "prompts": {
                "mode": "single" if prompt_b is None else "dual",
                "a": _prompt_snapshot(prompt_a),
                "b": _prompt_snapshot(prompt_b) if prompt_b is not None else None,
            },
            "dimensions": _dimension_snapshot(
                candidate_strategy=candidate_strategy,
                dimension_schema=dimension_schema,
                route_policy=route_policy,
                category_pipeline=pipeline,
            ),
            "golden_sample_set": _golden_sample_snapshot(sample_set, regression),
            "strategies": {
                "baseline": baseline_strategy,
                "candidate": candidate_strategy,
            },
            "regression": regression_snapshot,
            "automation": _automation_snapshot(automation),
            "metrics": {
                "regression": regression_snapshot["metrics"],
                "regression_summary": regression_snapshot["summary"],
                "prompt_metric_snapshot": _metric_snapshot(metric),
            },
            "ai": {
                "recommendation": recommendation,
                "change_summary": change_summary,
                "publishes_automatically": False,
            },
            "identity": {
                "model_id": candidate_bundle.model_id,
                "rubric_version": candidate_bundle.rubric_version,
                "engine_version": candidate_bundle.engine_version,
                "risk_review_version": candidate_bundle.risk_review_version,
                "agent_plan_version": candidate_bundle.agent_plan_version,
                "candidate_strategy_hash": candidate_bundle.canonical_hash,
                "baseline_strategy_hash": (
                    baseline_bundle.canonical_hash
                    if baseline_bundle is not None
                    else None
                ),
            },
        }
    )
    return {
        "regression": regression,
        "automation": automation,
        "sample_set": sample_set,
        "candidate_bundle": candidate_bundle,
        "baseline_bundle": baseline_bundle,
        "prompt_a": prompt_a,
        "prompt_b": prompt_b,
        "dimension_schema": dimension_schema,
        "route_policy": route_policy,
        "metric": metric,
        "category_key": category_key,
        "recommendation": recommendation,
        "change_summary": change_summary,
        "manifest": manifest,
    }


def _request_definition(
    payload: EvaluationPackageCreateRequest,
    material: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "evaluation-package-request-v1",
        "package_key": payload.normalized_package_key(),
        "category_key": material["category_key"],
        "regression_run_id": material["regression"].id,
        "automation_run_id": (
            material["automation"].id if material["automation"] is not None else None
        ),
        "prompt_a_id": material["prompt_a"].id,
        "prompt_b_id": material["prompt_b"].id if material["prompt_b"] is not None else None,
        "dimension_schema_id": (
            material["dimension_schema"].id
            if material["dimension_schema"] is not None
            else None
        ),
        "dimension_route_policy_id": (
            material["route_policy"].id
            if material["route_policy"] is not None
            else None
        ),
        "sample_set_id": material["sample_set"].id,
        "baseline_strategy_bundle_id": (
            material["baseline_bundle"].id
            if material["baseline_bundle"] is not None
            else None
        ),
        "candidate_strategy_bundle_id": material["candidate_bundle"].id,
        "metric_snapshot_id": (
            material["metric"].id if material["metric"] is not None else None
        ),
        "ai_recommendation": material["recommendation"],
        "change_summary": material["change_summary"],
    }


def _assert_manifest_valid(package: EvaluationPackage) -> dict[str, Any]:
    try:
        manifest = _loads_object(
            package.canonical_manifest_json,
            label="评测包冻结清单",
        )
        recomputed = canonical_manifest_hash(manifest)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="评测包冻结清单已损坏，请停止发布并联系管理员",
        ) from exc
    if recomputed != package.canonical_manifest_hash:
        raise HTTPException(
            status_code=409,
            detail="评测包规范哈希无法复算，请停止发布并联系管理员",
        )
    return manifest


def _assert_live_prompts_match(package: EvaluationPackage, manifest: dict[str, Any]) -> None:
    prompt_manifest = manifest.get("prompts") or {}
    expected_a = prompt_manifest.get("a")
    expected_b = prompt_manifest.get("b")
    if not isinstance(expected_a, dict) or _prompt_snapshot(package.prompt_a) != expected_a:
        raise HTTPException(
            status_code=409,
            detail="A 阶段提示词已偏离评测包冻结内容，请创建新评测包",
        )
    if package.prompt_b is None:
        if expected_b is not None:
            raise HTTPException(status_code=409, detail="单提示词评测包冻结身份已损坏")
    elif not isinstance(expected_b, dict) or _prompt_snapshot(package.prompt_b) != expected_b:
        raise HTTPException(
            status_code=409,
            detail="B 阶段提示词已偏离评测包冻结内容，请创建新评测包",
        )


def _refresh_validating_package(db: Session, package: EvaluationPackage) -> bool:
    if package.status != "validating":
        return False
    run = db.get(PromptRegressionRun, package.regression_run_id)
    if run is None or run.status not in TERMINAL_REGRESSION_STATUSES:
        return False
    manifest = _assert_manifest_valid(package)
    regression = _regression_snapshot(run)
    manifest["regression"] = regression
    metrics = manifest.setdefault("metrics", {})
    metrics["regression"] = regression["metrics"]
    metrics["regression_summary"] = regression["summary"]
    manifest.setdefault("ai", {})["recommendation"] = regression[
        "recommendation"
    ]
    manifest = _secret_safe(manifest)
    package.canonical_manifest_json = canonical_json(manifest)
    package.canonical_manifest_hash = canonical_manifest_hash(manifest)
    package.ai_recommendation = str(regression["recommendation"])
    package.status = "awaiting_review"
    package.updated_at = datetime.now(timezone.utc)
    append_audit_event(
        db,
        category="evaluation_package",
        action="validation_completed",
        subject_type="evaluation_package",
        subject_id=package.id,
        actor="system",
        payload={
            "manifest_hash": package.canonical_manifest_hash,
            "recommendation": package.ai_recommendation,
        },
        event_key=f"evaluation-package-validation:{package.id}",
    )
    db.flush()
    return True


def _package_payload(
    db: Session,
    package: EvaluationPackage,
    *,
    include_manifest: bool,
    refresh_validating: bool = True,
) -> dict[str, Any]:
    if refresh_validating and _refresh_validating_package(db, package):
        db.commit()
        db.refresh(package)
    manifest = _assert_manifest_valid(package)
    payload: dict[str, Any] = {
        "id": package.id,
        "package_key": package.package_key,
        "status": package.status,
        "category_key": package.category_key,
        "prompt_mode": package.prompt_mode,
        "prompt_a_id": package.prompt_a_id,
        "prompt_b_id": package.prompt_b_id,
        "dimension_schema_id": package.dimension_schema_id,
        "dimension_route_policy_id": package.dimension_route_policy_id,
        "sample_set_id": package.sample_set_id,
        "baseline_strategy_bundle_id": package.baseline_strategy_bundle_id,
        "candidate_strategy_bundle_id": package.candidate_strategy_bundle_id,
        "regression_run_id": package.regression_run_id,
        "automation_run_id": package.automation_run_id,
        "metric_snapshot_id": package.metric_snapshot_id,
        "canonical_manifest_hash": package.canonical_manifest_hash,
        "manifest_hash_valid": True,
        "ai_recommendation": package.ai_recommendation,
        "change_summary": package.change_summary,
        "review": {
            "revision": package.review_revision,
            "decision": package.review_decision,
            "note": package.review_note,
            "reviewed_by": package.reviewed_by,
            "reviewed_at": package.reviewed_at,
        },
        "publish": {
            "published_by": package.published_by,
            "published_at": package.published_at,
            "publishes_automatically": False,
        },
        "archive": {
            "archived_by": package.archived_by,
            "archived_at": package.archived_at,
            "reason": package.archive_reason,
        },
        "created_by": package.created_by,
        "created_at": package.created_at,
        "updated_at": package.updated_at,
    }
    if include_manifest:
        payload.update(
            {
                "canonical_manifest": manifest,
                "category": manifest.get("category"),
                "prompts": manifest.get("prompts"),
                "dimensions": manifest.get("dimensions"),
                "golden_sample_set": manifest.get("golden_sample_set"),
                "strategies": manifest.get("strategies"),
                "regression": manifest.get("regression"),
                "automation": manifest.get("automation"),
                "metrics": manifest.get("metrics"),
                "identity": manifest.get("identity"),
                "ai": manifest.get("ai"),
            }
        )
    return _secret_safe(payload)


def create_evaluation_package(
    db: Session,
    *,
    payload: EvaluationPackageCreateRequest,
    actor: str,
) -> tuple[EvaluationPackage, bool]:
    material = _build_package_material(db, payload)
    request_definition = _request_definition(payload, material)
    request_hash = hashlib.sha256(
        canonical_json(request_definition).encode("utf-8")
    ).hexdigest()
    existing = db.scalar(
        select(EvaluationPackage).where(
            EvaluationPackage.package_key == payload.normalized_package_key()
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="相同评测包幂等键对应的冻结请求已变化",
            )
        return existing, True
    manifest = material["manifest"]
    manifest_json = canonical_json(manifest)
    package = EvaluationPackage(
        package_key=payload.normalized_package_key(),
        request_hash=request_hash,
        category_key=material["category_key"],
        prompt_mode="single" if material["prompt_b"] is None else "dual",
        prompt_a_id=material["prompt_a"].id,
        prompt_b_id=(
            material["prompt_b"].id if material["prompt_b"] is not None else None
        ),
        dimension_schema_id=(
            material["dimension_schema"].id
            if material["dimension_schema"] is not None
            else None
        ),
        dimension_route_policy_id=(
            material["route_policy"].id
            if material["route_policy"] is not None
            else None
        ),
        sample_set_id=material["sample_set"].id,
        baseline_strategy_bundle_id=(
            material["baseline_bundle"].id
            if material["baseline_bundle"] is not None
            else None
        ),
        candidate_strategy_bundle_id=material["candidate_bundle"].id,
        regression_run_id=material["regression"].id,
        automation_run_id=(
            material["automation"].id if material["automation"] is not None else None
        ),
        metric_snapshot_id=(
            material["metric"].id if material["metric"] is not None else None
        ),
        canonical_manifest_json=manifest_json,
        canonical_manifest_hash=canonical_manifest_hash(manifest),
        ai_recommendation=material["recommendation"],
        change_summary=material["change_summary"],
        status=(
            "awaiting_review"
            if material["regression"].status in TERMINAL_REGRESSION_STATUSES
            else "validating"
        ),
        created_by=actor,
    )
    db.add(package)
    try:
        db.flush()
        append_audit_event(
            db,
            category="evaluation_package",
            action="created",
            subject_type="evaluation_package",
            subject_id=package.id,
            actor=actor,
            payload={
                "package_key": package.package_key,
                "status": package.status,
                "manifest_hash": package.canonical_manifest_hash,
                "publishes_automatically": False,
            },
            event_key="evaluation-package-created:"
            + hashlib.sha256(package.package_key.encode("utf-8")).hexdigest(),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(EvaluationPackage).where(
                EvaluationPackage.package_key == payload.normalized_package_key()
            )
        )
        if concurrent is not None and concurrent.request_hash == request_hash:
            return concurrent, True
        raise HTTPException(
            status_code=409,
            detail="评测包创建发生并发冲突，请刷新后重试",
        ) from None
    db.refresh(package)
    return package, False


def _review_evaluation_package(
    db: Session,
    *,
    package: EvaluationPackage,
    decision: Literal["approved", "rejected"],
    note: str,
    actor: str,
) -> tuple[EvaluationPackage, bool]:
    _refresh_validating_package(db, package)
    _assert_manifest_valid(package)
    if package.status in {decision, "published", "archived"}:
        expected_statuses = (
            {"approved", "published", "archived"}
            if decision == "approved"
            else {"rejected", "archived"}
        )
        if (
            package.status in expected_statuses
            and package.review_decision == decision
            and package.reviewed_by == actor
            and package.review_note == note
        ):
            return package, True
    if package.status != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=f"评测包当前状态 {package.status} 不允许二审",
        )
    if decision == "approved":
        manifest = _assert_manifest_valid(package)
        regression = manifest.get("regression")
        regression_passed = (
            isinstance(regression, dict)
            and regression.get("terminal") is True
            and regression.get("recommendation") == "pass"
        )
        if not regression_passed or package.ai_recommendation != "pass":
            raise HTTPException(
                status_code=409,
                detail="回归建议未通过，不能批准评测包；请拒绝并重新验证",
            )
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(EvaluationPackage)
        .where(
            EvaluationPackage.id == package.id,
            EvaluationPackage.status == "awaiting_review",
        )
        .values(
            status=decision,
            review_decision=decision,
            review_note=note,
            reviewed_by=actor,
            reviewed_at=now,
            review_revision=EvaluationPackage.review_revision + 1,
            updated_at=now,
        )
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        current = db.get(EvaluationPackage, package.id)
        if (
            current is not None
            and current.status == decision
            and current.reviewed_by == actor
            and current.review_note == note
        ):
            return current, True
        raise HTTPException(
            status_code=409,
            detail="评测包二审已被其他操作更新，请刷新后重试",
        )
    append_audit_event(
        db,
        category="evaluation_package",
        action=decision,
        subject_type="evaluation_package",
        subject_id=package.id,
        actor=actor,
        payload={"note": note, "manifest_hash": package.canonical_manifest_hash},
        event_key=f"evaluation-package-review:{package.id}:{decision}",
    )
    db.commit()
    db.expire_all()
    current = db.get(EvaluationPackage, package.id)
    if current is None:
        raise HTTPException(status_code=404, detail="评测包不存在")
    return current, False


def _publish_prompt(
    db: Session,
    prompt: PromptVersion,
    *,
    previous_prompt_id: int | None,
    now: datetime,
) -> None:
    if prompt.status == "published":
        return
    db.execute(
        update(PromptVersion)
        .where(PromptVersion.id == prompt.id)
        .values(
            status="published",
            rollback_prompt_id=(
                previous_prompt_id
                if previous_prompt_id is not None and previous_prompt_id != prompt.id
                else None
            ),
            updated_at=now,
        )
    )


def _activate_package_category_baseline(
    db: Session,
    *,
    package: EvaluationPackage,
) -> tuple[int | None, int | None]:
    """Make the human-approved package the category's executable baseline."""
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == package.category_key
        )
    )
    if profile is None or profile.status != "active":
        raise HTTPException(status_code=409, detail="评测包所属类目不存在或未启用")
    manifest = _assert_manifest_valid(package)
    frozen_profile = (manifest.get("category") or {}).get("profile")
    if not isinstance(frozen_profile, dict):
        raise HTTPException(status_code=409, detail="评测包缺少冻结类目合同")
    expected_revision = frozen_profile.get("automation_revision")
    expected_baseline_id = frozen_profile.get("baseline_strategy_bundle_id")
    expected_pipeline_revision = frozen_profile.get("pipeline_revision")
    frozen_pipeline = frozen_profile.get("pipeline_config")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        raise HTTPException(status_code=409, detail="评测包缺少冻结类目修订号")
    if not isinstance(expected_pipeline_revision, int) or isinstance(
        expected_pipeline_revision, bool
    ):
        raise HTTPException(status_code=409, detail="评测包缺少冻结流水线修订号")
    if not isinstance(frozen_pipeline, dict):
        raise HTTPException(status_code=409, detail="评测包缺少冻结流水线合同")
    pipeline = _category_pipeline(profile, db)
    if (
        profile.pipeline_revision != expected_pipeline_revision
        or canonical_json(pipeline) != canonical_json(frozen_pipeline)
    ):
        raise HTTPException(
            status_code=409,
            detail="类目流水线已在评测包创建后变化，请重新回归并生成新评测包",
        )
    expected_mode = "single" if package.prompt_mode == "single" else "ab"
    if pipeline.get("prompt_mode") != expected_mode:
        raise HTTPException(status_code=409, detail="评测包提示词模式与当前类目流水线合同不一致")
    bundle = package.candidate_strategy_bundle
    try:
        frozen_model = json.loads(bundle.model_config_snapshot)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="候选模型快照损坏") from exc
    model_matches = [
        model
        for model in db.scalars(
            select(ModelConfig).where(
                ModelConfig.model_id == bundle.model_id,
                ModelConfig.active.is_(True),
            )
        ).all()
        if build_model_config_snapshot(model) == frozen_model
    ]
    if len(model_matches) != 1:
        raise HTTPException(status_code=409, detail="候选模型配置无法唯一解析")
    try:
        automation = json.loads(profile.automation_config_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="类目自动化配置损坏") from exc
    if not isinstance(automation, dict):
        raise HTTPException(status_code=409, detail="类目自动化配置不是对象")

    previous_prompt_ids = (profile.prompt_a_id, profile.prompt_b_id)
    next_dimension_key = profile.dimension_schema_key
    next_dimension_version = profile.dimension_schema_version
    if package.dimension_schema is not None:
        next_dimension_key = package.dimension_schema.schema_key
        next_dimension_version = package.dimension_schema.version
    elif not next_dimension_key or not next_dimension_version:
        try:
            schema_set = json.loads(bundle.dimension_schema_set_snapshot or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=409, detail="候选维度合同损坏") from exc
        schemas = schema_set.get("schemas") if isinstance(schema_set, dict) else None
        candidates = [
            item
            for item in schemas or []
            if isinstance(item, dict)
            and isinstance(item.get("schema_key"), str)
            and isinstance(item.get("version"), str)
        ]
        if len(candidates) != 1:
            raise HTTPException(status_code=409, detail="候选维度合同无法唯一解析")
        next_dimension_key = candidates[0]["schema_key"]
        next_dimension_version = candidates[0]["version"]
    current_baseline_id = automation.get("baseline_strategy_bundle_id")
    if profile.automation_revision != expected_revision or current_baseline_id != expected_baseline_id:
        raise HTTPException(
            status_code=409,
            detail="类目基线已在评测包创建后变化，请重新回归并生成新评测包",
        )
    next_automation_json = canonical_json(
        {
            **automation,
            "baseline_strategy_bundle_id": bundle.id,
            "baseline_binding_source": "evaluation_package",
        }
    )
    previous_values = (
        profile.prompt_a_id,
        profile.prompt_b_id,
        profile.model_config_id,
        profile.rubric_version,
        profile.dimension_schema_key,
        profile.dimension_schema_version,
        profile.automation_config_json,
    )
    with db.no_autoflush:
        profile.prompt_a_id = package.prompt_a_id
        profile.prompt_b_id = package.prompt_b_id
        profile.model_config_id = model_matches[0].id
        profile.rubric_version = bundle.rubric_version
        profile.dimension_schema_key = next_dimension_key
        profile.dimension_schema_version = next_dimension_version
        profile.automation_config_json = next_automation_json
        errors = category_bundle_contract_errors(
            db,
            profile=profile,
            bundle=bundle,
            require_complete=True,
            require_prompt_b=package.prompt_b_id is not None,
            enforce_baseline_id=True,
        )
        (
            profile.prompt_a_id,
            profile.prompt_b_id,
            profile.model_config_id,
            profile.rubric_version,
            profile.dimension_schema_key,
            profile.dimension_schema_version,
            profile.automation_config_json,
        ) = previous_values
    if errors:
        raise HTTPException(
            status_code=409,
            detail="评测包与类目执行合同不一致，不能发布：" + "、".join(errors),
        )
    updated = db.execute(
        update(EvaluationCategoryProfile)
        .where(
            EvaluationCategoryProfile.id == profile.id,
            EvaluationCategoryProfile.automation_revision == expected_revision,
            EvaluationCategoryProfile.pipeline_revision == expected_pipeline_revision,
            EvaluationCategoryProfile.automation_config_json == profile.automation_config_json,
            EvaluationCategoryProfile.pipeline_config_json == canonical_json(frozen_pipeline),
        )
        .values(
            prompt_a_id=package.prompt_a_id,
            prompt_b_id=package.prompt_b_id,
            model_config_id=model_matches[0].id,
            rubric_version=bundle.rubric_version,
            dimension_schema_key=next_dimension_key,
            dimension_schema_version=next_dimension_version,
            automation_config_json=next_automation_json,
            automation_revision=expected_revision + 1,
        )
    )
    if int(updated.rowcount or 0) != 1:
        raise HTTPException(
            status_code=409,
            detail="类目基线已被其他发布更新，请刷新后重新生成评测包",
        )
    db.expire(profile)
    return previous_prompt_ids


def publish_evaluation_package(
    db: Session,
    *,
    package: EvaluationPackage,
    actor: str,
    note: str = "",
) -> tuple[EvaluationPackage, bool]:
    manifest = _assert_manifest_valid(package)
    if package.status == "published":
        return package, True
    if package.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="只有已批准的评测包可以发布",
        )
    _assert_live_prompts_match(package, manifest)
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(EvaluationPackage)
        .where(
            EvaluationPackage.id == package.id,
            EvaluationPackage.status == "approved",
        )
        .values(
            status="published",
            published_by=actor,
            published_at=now,
            review_revision=EvaluationPackage.review_revision + 1,
            updated_at=now,
        )
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        current = db.get(EvaluationPackage, package.id)
        if current is not None and current.status == "published":
            return current, True
        raise HTTPException(
            status_code=409,
            detail="评测包发布已被其他操作更新，请刷新后重试",
        )
    try:
        previous_prompt_a_id, previous_prompt_b_id = _activate_package_category_baseline(
            db, package=package
        )
    except Exception:
        db.rollback()
        raise
    _publish_prompt(
        db,
        package.prompt_a,
        previous_prompt_id=previous_prompt_a_id,
        now=now,
    )
    if package.prompt_b is not None:
        _publish_prompt(
            db,
            package.prompt_b,
            previous_prompt_id=previous_prompt_b_id,
            now=now,
        )
    append_audit_event(
        db,
        category="evaluation_package",
        action="published",
        subject_type="evaluation_package",
        subject_id=package.id,
        actor=actor,
        payload={
            "note": note.strip(),
            "manifest_hash": package.canonical_manifest_hash,
            "prompt_a_id": package.prompt_a_id,
            "prompt_b_id": package.prompt_b_id,
            "human_gate": True,
        },
        event_key=f"evaluation-package-published:{package.id}",
    )
    db.commit()
    db.expire_all()
    current = db.get(EvaluationPackage, package.id)
    if current is None:
        raise HTTPException(status_code=404, detail="评测包不存在")
    return current, False


def _archive_evaluation_package(
    db: Session,
    *,
    package: EvaluationPackage,
    actor: str,
    reason: str,
) -> tuple[EvaluationPackage, bool]:
    _assert_manifest_valid(package)
    if package.status == "archived":
        if package.archived_by == actor and package.archive_reason == reason:
            return package, True
        raise HTTPException(status_code=409, detail="评测包归档结论已经冻结")
    if package.status not in {"approved", "rejected", "published"}:
        raise HTTPException(
            status_code=409,
            detail=f"评测包当前状态 {package.status} 不允许归档",
        )
    previous_status = package.status
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(EvaluationPackage)
        .where(
            EvaluationPackage.id == package.id,
            EvaluationPackage.status == package.status,
        )
        .values(
            status="archived",
            archived_by=actor,
            archived_at=now,
            archive_reason=reason,
            review_revision=EvaluationPackage.review_revision + 1,
            updated_at=now,
        )
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        current = db.get(EvaluationPackage, package.id)
        if (
            current is not None
            and current.status == "archived"
            and current.archived_by == actor
            and current.archive_reason == reason
        ):
            return current, True
        raise HTTPException(
            status_code=409,
            detail="评测包归档已被其他操作更新，请刷新后重试",
        )
    append_audit_event(
        db,
        category="evaluation_package",
        action="archived",
        subject_type="evaluation_package",
        subject_id=package.id,
        actor=actor,
        payload={"reason": reason, "previous_status": previous_status},
        event_key=f"evaluation-package-archived:{package.id}",
    )
    db.commit()
    db.expire_all()
    current = db.get(EvaluationPackage, package.id)
    if current is None:
        raise HTTPException(status_code=404, detail="评测包不存在")
    return current, False


def _from_automation_payload(
    db: Session,
    *,
    automation_run_id: int,
    payload: EvaluationPackageFromAutomationRequest,
) -> EvaluationPackageCreateRequest:
    run = db.get(AutomationOptimizationRun, automation_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="自动优化 Run 不存在")
    if run.status not in COMPLETED_AUTOMATION_STATUSES or run.dry_run:
        raise HTTPException(status_code=409, detail="自动优化 Run 尚未完成真实候选生成")
    _, regression_ids = _automation_result_ids(run)
    regression_run_id = payload.regression_run_id
    if regression_run_id is None:
        if len(regression_ids) != 1:
            raise HTTPException(
                status_code=409,
                detail="自动优化 Run 包含多个候选，请明确选择 regression_run_id",
            )
        regression_run_id = regression_ids[0]
    if regression_run_id not in regression_ids:
        raise HTTPException(status_code=409, detail="回归任务不属于该自动优化 Run")
    return EvaluationPackageCreateRequest(
        package_key=(
            payload.package_key
            or f"automation:{automation_run_id}:regression:{regression_run_id}"
        ),
        category_key=run.category_key,
        regression_run_id=regression_run_id,
        automation_run_id=automation_run_id,
        metric_snapshot_id=payload.metric_snapshot_id,
        dimension_schema_id=payload.dimension_schema_id,
        dimension_route_policy_id=payload.dimension_route_policy_id,
        ai_recommendation=payload.ai_recommendation,
        change_summary=payload.change_summary,
    )


def build_evaluation_package_router(
    read_user_dependency: Callable[..., User],
    write_user_dependency: Callable[..., User],
    admin_user_dependency: Callable[..., User],
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["evaluation-packages"])

    @router.post("/evaluation-packages")
    def create_package(
        payload: EvaluationPackageCreateRequest,
        user: User = Depends(write_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        package, duplicate = create_evaluation_package(
            db, payload=payload, actor=user.username
        )
        detail = _package_payload(db, package, include_manifest=True)
        return {"duplicate": duplicate, "package": detail, **detail}

    def create_from_automation(
        automation_run_id: int,
        payload: EvaluationPackageFromAutomationRequest,
        user: User,
        db: Session,
    ) -> dict[str, Any]:
        create_payload = _from_automation_payload(
            db,
            automation_run_id=automation_run_id,
            payload=payload,
        )
        package, duplicate = create_evaluation_package(
            db, payload=create_payload, actor=user.username
        )
        detail = _package_payload(db, package, include_manifest=True)
        return {"duplicate": duplicate, "package": detail, **detail}

    @router.post("/evaluation-packages/from-automation/{automation_run_id}")
    def create_package_from_automation(
        automation_run_id: int,
        payload: EvaluationPackageFromAutomationRequest,
        user: User = Depends(write_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        return create_from_automation(automation_run_id, payload, user, db)

    @router.post("/automation-runs/{automation_run_id}/evaluation-package")
    def create_package_from_automation_alias(
        automation_run_id: int,
        payload: EvaluationPackageFromAutomationRequest,
        user: User = Depends(write_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        return create_from_automation(automation_run_id, payload, user, db)

    @router.get("/evaluation-packages")
    def list_packages(
        status: Literal[
            "validating",
            "awaiting_review",
            "approved",
            "rejected",
            "published",
            "archived",
        ]
        | None = None,
        category_key: str | None = None,
        limit: int = Query(default=200, ge=1, le=500),
        _user: User = Depends(read_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        statement = select(EvaluationPackage).order_by(
            EvaluationPackage.created_at.desc(),
            EvaluationPackage.id.desc(),
        )
        if status is not None:
            statement = statement.where(EvaluationPackage.status == status)
        if category_key is not None:
            statement = statement.where(
                EvaluationPackage.category_key == category_key
            )
        packages = db.scalars(statement.limit(limit)).all()
        return {
            "items": [
                _package_payload(db, package, include_manifest=False)
                for package in packages
            ]
        }

    @router.get("/evaluation-packages/{package_id}")
    def get_package(
        package_id: int,
        _user: User = Depends(read_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        package = db.get(EvaluationPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="评测包不存在")
        return _package_payload(db, package, include_manifest=True)

    @router.post("/evaluation-packages/{package_id}/validate")
    def validate_package(
        package_id: int,
        _user: User = Depends(write_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        package = db.get(EvaluationPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="评测包不存在")
        changed = _refresh_validating_package(db, package)
        if not changed and package.status == "validating":
            raise HTTPException(status_code=409, detail="回归任务尚未完成，评测包仍在验证")
        db.commit()
        db.refresh(package)
        return _package_payload(db, package, include_manifest=True)

    def review_package(
        package_id: int,
        *,
        decision: Literal["approved", "rejected"],
        note: str,
        user: User,
        db: Session,
    ) -> dict[str, Any]:
        package = db.get(EvaluationPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="评测包不存在")
        current, duplicate = _review_evaluation_package(
            db,
            package=package,
            decision=decision,
            note=note,
            actor=user.username,
        )
        detail = _package_payload(db, current, include_manifest=True)
        return {"duplicate": duplicate, "package": detail, **detail}

    @router.post("/evaluation-packages/{package_id}/approve")
    def approve_package(
        package_id: int,
        payload: EvaluationPackageReviewRequest,
        user: User = Depends(admin_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        return review_package(
            package_id,
            decision="approved",
            note=payload.note,
            user=user,
            db=db,
        )

    @router.post("/evaluation-packages/{package_id}/reject")
    def reject_package(
        package_id: int,
        payload: EvaluationPackageReviewRequest,
        user: User = Depends(admin_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        return review_package(
            package_id,
            decision="rejected",
            note=payload.note,
            user=user,
            db=db,
        )

    @router.post("/evaluation-packages/{package_id}/review")
    def decide_package(
        package_id: int,
        payload: EvaluationPackageDecisionRequest,
        user: User = Depends(admin_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        return review_package(
            package_id,
            decision=payload.decision,
            note=payload.note,
            user=user,
            db=db,
        )

    @router.post("/evaluation-packages/{package_id}/publish")
    def publish_package(
        package_id: int,
        payload: EvaluationPackagePublishRequest | None = None,
        user: User = Depends(admin_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        package = db.get(EvaluationPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="评测包不存在")
        current, duplicate = publish_evaluation_package(
            db,
            package=package,
            actor=user.username,
            note=payload.note if payload is not None else "",
        )
        detail = _package_payload(db, current, include_manifest=True)
        return {"duplicate": duplicate, "package": detail, **detail}

    @router.post("/evaluation-packages/{package_id}/archive")
    def archive_package(
        package_id: int,
        payload: EvaluationPackageArchiveRequest,
        user: User = Depends(admin_user_dependency),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        package = db.get(EvaluationPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="评测包不存在")
        current, duplicate = _archive_evaluation_package(
            db,
            package=package,
            actor=user.username,
            reason=payload.reason,
        )
        detail = _package_payload(db, current, include_manifest=True)
        return {"duplicate": duplicate, "package": detail, **detail}

    return router
