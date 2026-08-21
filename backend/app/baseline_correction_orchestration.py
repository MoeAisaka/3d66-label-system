"""Automatic mechanism-candidate and baseline-regression orchestration."""

from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .baseline_regression import (
    build_baseline_field_metrics,
    compute_level_metrics,
    deterministic_correction_report,
    field_metric_release_regressions,
)
from .category_evaluation_v3_revisions import (
    RevisionArtifacts,
    create_candidate_revision,
    ensure_projected_revision,
    revision_bundle,
)
from .category_evaluation_contract import (
    CategoryEvaluationPromptBindingError,
    bind_category_evaluation_prompt_versions,
    validate_category_evaluation_prompt_bindings,
)
from .correction_contract import (
    correction_contract_hash,
    freeze_contract_from_execution_snapshot,
)
from .automation_candidate_pipeline import build_immutable_candidate_package
from .doubao import DoubaoClient
from .models import (
    BaselineCorrectionRun,
    BaselineRegressionItem,
    BaselineRegressionRun,
    CategoryEvaluationV3Config,
    CategoryEvaluationV3Revision,
    EvaluationJob,
    ModelConfig,
    ModelRegistryEntry,
    OptimizerConfig,
    PromptVersion,
    SamplingPolicy,
)
from .mechanism_profiles import MechanismProfileError, validate_mechanism_artifacts
from .mechanism_release_gate import (
    CandidateReleaseGateError,
    evaluate_candidate_release_gate,
)
from .risk_review import RISK_REVIEW_VERSION
from .scoring import ENGINE_VERSION
from .strategy_bundle import build_strategy_snapshot, get_or_create_bundle


@dataclass(frozen=True)
class GeneratedPromptCandidate:
    stage: str
    system_prompt: str
    user_prompt: str
    change_note: str


@dataclass(frozen=True)
class GeneratedMechanismCandidate:
    prompt: GeneratedPromptCandidate
    revision: RevisionArtifacts
    summary: dict[str, Any]
    model_snapshot: dict[str, Any]
    generation_trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedCorrectionGeneration:
    correction: BaselineCorrectionRun
    projected: CategoryEvaluationV3Config
    active_revision: CategoryEvaluationV3Revision
    active_prompts: Mapping[str, PromptVersion]
    execution: dict[str, Any]
    report: dict[str, Any]
    orchestration: dict[str, Any]


class CorrectionMechanismGenerator(Protocol):
    def generate(
        self,
        *,
        db: Session | None,
        correction: BaselineCorrectionRun,
        active_revision: CategoryEvaluationV3Revision,
        active_prompts: Mapping[str, PromptVersion],
        report: Mapping[str, Any],
    ) -> GeneratedMechanismCandidate | Mapping[str, Any]: ...


class CorrectionOrchestrationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CorrectionCandidateGenerationError(CorrectionOrchestrationError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        generation_trace: list[dict[str, Any]],
    ) -> None:
        super().__init__(code, message)
        self.generation_trace = generation_trace


def _json_object(value: str | None, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise CorrectionOrchestrationError(
            "CORRECTION_FROZEN_JSON_INVALID",
            f"{label}损坏",
        ) from exc
    if not isinstance(payload, dict):
        raise CorrectionOrchestrationError(
            "CORRECTION_FROZEN_JSON_INVALID",
            f"{label}损坏",
        )
    return payload


def _merge_json_patch(base: Any, patch: Any) -> Any:
    """Apply RFC 7396 merge-patch semantics to a detached JSON value."""
    if not isinstance(patch, Mapping):
        return deepcopy(patch)
    result = deepcopy(dict(base)) if isinstance(base, Mapping) else {}
    for key, patch_value in patch.items():
        if patch_value is None:
            result.pop(str(key), None)
            continue
        result[str(key)] = _merge_json_patch(result.get(str(key)), patch_value)
    return result


def _salvage_candidate_delta(value: Any) -> dict[str, Any]:
    """Keep only valid, independently reusable fields from a failed attempt."""
    if not isinstance(value, Mapping):
        return {}
    payload: Mapping[str, Any] = value
    if not isinstance(payload.get("prompt"), Mapping) or not isinstance(
        payload.get("revision"), Mapping
    ):
        for wrapper in ("candidate", "mechanism_candidate", "unified_candidate"):
            nested = payload.get(wrapper)
            if isinstance(nested, Mapping):
                merged = dict(nested)
                if "summary" not in merged and "summary" in payload:
                    merged["summary"] = payload["summary"]
                payload = merged
                break

    salvaged: dict[str, Any] = {}
    prompt = payload.get("prompt")
    if isinstance(prompt, Mapping):
        prompt_delta: dict[str, Any] = {}
        stage = prompt.get("stage")
        if isinstance(stage, str) and stage.strip().upper() in {"A", "B"}:
            prompt_delta["stage"] = stage.strip().upper()
        for field_name in ("system_prompt", "user_prompt", "change_note"):
            field_value = prompt.get(field_name)
            if isinstance(field_value, str) and field_value.strip():
                prompt_delta[field_name] = field_value
        salvaged["prompt"] = prompt_delta

    revision = payload.get("revision")
    if isinstance(revision, Mapping):
        revision_delta: dict[str, Any] = {}
        display_name = revision.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            revision_delta["display_name"] = display_name
        for field_name in ("contract", "classification_map", "subcategory_dimensions"):
            field_value = revision.get(field_name)
            if isinstance(field_value, Mapping):
                revision_delta[field_name] = deepcopy(dict(field_value))
        salvaged["revision"] = revision_delta

    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        salvaged["summary"] = deepcopy(dict(summary))
    return salvaged


def _normalize_generated_candidate(
    value: GeneratedMechanismCandidate | Mapping[str, Any],
    *,
    active_revision: CategoryEvaluationV3Revision | None = None,
    active_prompts: Mapping[str, PromptVersion] | None = None,
) -> GeneratedMechanismCandidate:
    if isinstance(value, GeneratedMechanismCandidate):
        return value
    if not isinstance(value, Mapping):
        raise CorrectionOrchestrationError(
            "CORRECTION_GENERATOR_OUTPUT_INVALID",
            "调优模型未返回结构化机制候选",
        )
    payload: Mapping[str, Any] = value
    if not isinstance(payload.get("prompt"), Mapping) or not isinstance(
        payload.get("revision"), Mapping
    ):
        for wrapper in ("candidate", "mechanism_candidate", "unified_candidate"):
            nested = payload.get(wrapper)
            if isinstance(nested, Mapping):
                merged = dict(nested)
                for key in ("summary", "model_snapshot", "generation_trace"):
                    if key not in merged and key in payload:
                        merged[key] = payload[key]
                payload = merged
                break
    prompt = payload.get("prompt")
    revision = payload.get("revision")
    invalid_fields: list[str] = []
    if not isinstance(prompt, Mapping):
        invalid_fields.append("prompt")
    if not isinstance(revision, Mapping):
        invalid_fields.append("revision")
    if invalid_fields:
        raise CorrectionOrchestrationError(
            "CORRECTION_GENERATOR_OUTPUT_INVALID",
            "调优模型返回的统一机制候选缺少或无效字段："
            + "、".join(invalid_fields),
        )
    stage = str(prompt.get("stage") or "").strip().upper()
    base_prompt = (active_prompts or {}).get(stage)
    system_prompt = str(
        prompt.get("system_prompt")
        if "system_prompt" in prompt
        else getattr(base_prompt, "system_prompt", "")
    ).strip()
    user_prompt = str(
        prompt.get("user_prompt")
        if "user_prompt" in prompt
        else getattr(base_prompt, "user_prompt", "")
    ).strip()
    change_note = str(prompt.get("change_note") or "").strip()
    active_bundle = revision_bundle(active_revision) if active_revision is not None else {}
    display_name = str(
        revision.get("display_name")
        if "display_name" in revision
        else getattr(active_revision, "display_name", "")
    ).strip()

    def composed_revision_field(field_name: str) -> Any:
        base = active_bundle.get(field_name)
        if field_name not in revision:
            return deepcopy(base)
        return _merge_json_patch(base, revision[field_name])

    contract = composed_revision_field("contract")
    classification_map = composed_revision_field("classification_map")
    subcategory_dimensions = composed_revision_field("subcategory_dimensions")
    if stage not in {"A", "B"}:
        invalid_fields.append("prompt.stage")
    if not system_prompt:
        invalid_fields.append("prompt.system_prompt")
    if not user_prompt:
        invalid_fields.append("prompt.user_prompt")
    if not change_note:
        invalid_fields.append("prompt.change_note")
    if not display_name:
        invalid_fields.append("revision.display_name")
    if not isinstance(contract, dict):
        invalid_fields.append("revision.contract")
    if not isinstance(classification_map, dict):
        invalid_fields.append("revision.classification_map")
    if not isinstance(subcategory_dimensions, dict):
        invalid_fields.append("revision.subcategory_dimensions")
    if invalid_fields:
        raise CorrectionOrchestrationError(
            "CORRECTION_GENERATOR_OUTPUT_INVALID",
            "调优模型返回的统一机制候选缺少或无效字段："
            + "、".join(invalid_fields),
        )
    summary = payload.get("summary")
    model_snapshot = payload.get("model_snapshot")
    generation_trace = payload.get("generation_trace")
    return GeneratedMechanismCandidate(
        prompt=GeneratedPromptCandidate(
            stage=stage,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            change_note=change_note,
        ),
        revision=RevisionArtifacts(
            display_name=display_name,
            contract=contract,
            classification_map=classification_map,
            subcategory_dimensions=subcategory_dimensions,
        ),
        summary=dict(summary) if isinstance(summary, Mapping) else {},
        model_snapshot=(
            dict(model_snapshot) if isinstance(model_snapshot, Mapping) else {}
        ),
        generation_trace=(
            [dict(item) for item in generation_trace if isinstance(item, Mapping)]
            if isinstance(generation_trace, list)
            else []
        ),
    )


def _candidate_routing_constraints(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    routing = report.get("candidate_routing")
    if not isinstance(routing, Mapping):
        return {
            "policy": "backward_compatible",
            "affected_layers": [],
            "allowed_prompt_stages": ["A", "B"],
            "required_prompt_stage": None,
        }
    raw_allowed = routing.get("allowed_prompt_stages")
    allowed = (
        [stage for stage in ("A", "B") if stage in raw_allowed]
        if isinstance(raw_allowed, list)
        else []
    )
    if not allowed:
        allowed = ["A", "B"]
    required = routing.get("required_prompt_stage")
    if required not in {"A", "B"}:
        required = None
    if required is not None:
        allowed = [required]
    raw_layers = routing.get("affected_layers")
    affected_layers = (
        [layer for layer in ("A", "B", "V3") if layer in raw_layers]
        if isinstance(raw_layers, list)
        else []
    )
    return {
        "policy": str(routing.get("policy") or "human_evidence_only"),
        "affected_layers": affected_layers,
        "allowed_prompt_stages": allowed,
        "required_prompt_stage": required,
    }


def _validate_candidate_routing(
    candidate: GeneratedMechanismCandidate,
    report: Mapping[str, Any],
) -> None:
    routing = _candidate_routing_constraints(report)
    allowed = routing["allowed_prompt_stages"]
    if candidate.prompt.stage in allowed:
        return
    required = routing.get("required_prompt_stage")
    expected = required or "、".join(allowed)
    layers = "、".join(routing.get("affected_layers") or []) or "未指定"
    raise CorrectionOrchestrationError(
        "CORRECTION_PROMPT_STAGE_MISMATCH",
        f"人工纠偏证据指向 {layers} 层，候选提示词必须使用 {expected} 阶段",
    )


def _active_projection(
    db: Session,
    category_key: str,
) -> tuple[CategoryEvaluationV3Config, CategoryEvaluationV3Revision]:
    projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == category_key,
            CategoryEvaluationV3Config.status == "active",
        )
    )
    if projected is None:
        raise CorrectionOrchestrationError(
            "CORRECTION_ACTIVE_MECHANISM_MISSING",
            "当前类目缺少现役 v3 评测机制",
        )
    active_revision = ensure_projected_revision(db, projected)
    if active_revision.status != "active":
        raise CorrectionOrchestrationError(
            "CORRECTION_ACTIVE_REVISION_INVALID",
            "当前类目的现役 revision 状态无效",
        )
    return projected, active_revision


def _active_prompts(
    db: Session,
    correction: BaselineCorrectionRun,
) -> tuple[dict[str, PromptVersion], dict[str, Any]]:
    run = correction.baseline_run
    execution = _json_object(
        run.execution_snapshot_json,
        label="基准回归执行快照",
    )
    prompts: dict[str, PromptVersion] = {}
    for stage, field in (("A", "prompt_a_id"), ("B", "prompt_b_id")):
        prompt_id = execution.get(field)
        if prompt_id is None:
            continue
        prompt = db.get(PromptVersion, prompt_id)
        if (
            prompt is None
            or prompt.stage != stage
            or prompt.category_key != correction.category_key
        ):
            raise CorrectionOrchestrationError(
                "CORRECTION_BASE_PROMPT_INVALID",
                f"基准回归冻结的 {stage} 提示词无法解析",
            )
        prompts[stage] = prompt
    if "A" not in prompts:
        raise CorrectionOrchestrationError(
            "CORRECTION_BASE_PROMPT_INVALID",
            "基准回归缺少冻结的 A 提示词",
        )
    return prompts, execution


def _generator_payload(
    candidate: GeneratedMechanismCandidate,
) -> dict[str, Any]:
    return {
        "prompt": {
            "stage": candidate.prompt.stage,
            "system_prompt": candidate.prompt.system_prompt,
            "user_prompt": candidate.prompt.user_prompt,
            "change_note": candidate.prompt.change_note,
        },
        "revision": {
            "display_name": candidate.revision.display_name,
            "contract": candidate.revision.contract,
            "classification_map": candidate.revision.classification_map,
            "subcategory_dimensions": candidate.revision.subcategory_dimensions,
        },
        "summary": candidate.summary,
        "model_snapshot": candidate.model_snapshot,
        "generation_trace": candidate.generation_trace,
    }


def _candidate_from_orchestration(
    orchestration: Mapping[str, Any],
) -> GeneratedMechanismCandidate | None:
    generated = orchestration.get("generated_candidate")
    if not isinstance(generated, Mapping):
        return None
    return _normalize_generated_candidate(generated)


def _candidate_prompt_version(
    correction: BaselineCorrectionRun,
    base: PromptVersion,
) -> str:
    raw = f"auto-c{correction.id}-{correction.category_key}-{base.stage.lower()}"
    return raw[:40]


def _ensure_candidate_prompt(
    db: Session,
    *,
    correction: BaselineCorrectionRun,
    candidate: GeneratedMechanismCandidate,
    active_prompts: Mapping[str, PromptVersion],
    orchestration: dict[str, Any],
) -> PromptVersion:
    prompt_state = orchestration.get("candidate_prompt")
    if isinstance(prompt_state, Mapping):
        prompt_id = prompt_state.get("id")
        if isinstance(prompt_id, int):
            existing = db.get(PromptVersion, prompt_id)
            if existing is not None:
                return existing
    base = active_prompts.get(candidate.prompt.stage)
    if base is None:
        raise CorrectionOrchestrationError(
            "CORRECTION_PROMPT_STAGE_INVALID",
            f"当前机制没有可替换的 {candidate.prompt.stage} 提示词",
        )
    version = _candidate_prompt_version(correction, base)
    existing = db.scalar(
        select(PromptVersion).where(
            PromptVersion.category_key == correction.category_key,
            PromptVersion.stage == candidate.prompt.stage,
            PromptVersion.version == version,
        )
    )
    if existing is not None:
        if (
            existing.system_prompt != candidate.prompt.system_prompt
            or existing.user_prompt != candidate.prompt.user_prompt
            or existing.rollback_prompt_id != base.id
        ):
            raise CorrectionOrchestrationError(
                "CORRECTION_PROMPT_VERSION_CONFLICT",
                "自动候选提示词版本已被其他内容占用",
            )
        prompt = existing
    else:
        prompt = PromptVersion(
            category_key=correction.category_key,
            pipeline_scope=base.pipeline_scope,
            stage=candidate.prompt.stage,
            name=f"{base.name} 自动纠偏候选 #{correction.id}"[:120],
            version=version,
            system_prompt=candidate.prompt.system_prompt,
            user_prompt=candidate.prompt.user_prompt,
            rubric_version=base.rubric_version,
            status="draft",
            source="auto_correction",
            rollback_prompt_id=base.id,
            change_note=candidate.prompt.change_note,
            created_by="automatic-correction",
        )
        db.add(prompt)
        db.flush()
    orchestration["candidate_prompt"] = {
        "id": prompt.id,
        "stage": prompt.stage,
        "base_prompt_id": base.id,
        "version": prompt.version,
    }
    return prompt


def _model_config_for_run(
    db: Session,
    run: BaselineRegressionRun,
    execution: Mapping[str, Any],
) -> ModelConfig:
    config_id = execution.get("model_config_id")
    model = db.get(ModelConfig, config_id) if isinstance(config_id, int) else None
    if model is None:
        matches = db.scalars(
            select(ModelConfig).where(
                ModelConfig.model_id == run.strategy_bundle.model_id
            )
        ).all()
        if len(matches) == 1:
            model = matches[0]
    if model is None or model.model_id != run.strategy_bundle.model_id:
        raise CorrectionOrchestrationError(
            "CORRECTION_BASE_MODEL_INVALID",
            "基准回归冻结的主模型配置无法解析",
        )
    return model


def _sampling_policy_for_run(
    db: Session,
    run: BaselineRegressionRun,
) -> SamplingPolicy | None:
    revision = run.strategy_bundle.sampling_policy_revision
    if revision is None:
        return None
    policy = db.scalar(
        select(SamplingPolicy).where(SamplingPolicy.revision == revision)
    )
    if policy is None:
        raise CorrectionOrchestrationError(
            "CORRECTION_SAMPLING_POLICY_INVALID",
            "基准回归冻结的抽样策略无法解析",
        )
    return policy


def _candidate_prompt_pair(
    *,
    candidate_prompt: PromptVersion,
    active_prompts: Mapping[str, PromptVersion],
) -> tuple[PromptVersion, PromptVersion | None]:
    prompt_a = (
        candidate_prompt
        if candidate_prompt.stage == "A"
        else active_prompts["A"]
    )
    prompt_b = active_prompts.get("B")
    if candidate_prompt.stage == "B":
        if prompt_b is None:
            raise CorrectionOrchestrationError(
                "CORRECTION_PROMPT_STAGE_INVALID",
                "单提示词机制不能生成 B 提示词候选",
            )
        prompt_b = candidate_prompt
    return prompt_a, prompt_b


def _bound_candidate_artifacts(
    candidate: GeneratedMechanismCandidate,
    *,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
) -> RevisionArtifacts:
    return RevisionArtifacts(
        display_name=candidate.revision.display_name,
        contract=bind_category_evaluation_prompt_versions(
            candidate.revision.contract,
            prompt_a_version=prompt_a.version,
            prompt_b_version=prompt_b.version if prompt_b is not None else None,
        ),
        classification_map=candidate.revision.classification_map,
        subcategory_dimensions=candidate.revision.subcategory_dimensions,
    )


def _create_candidate_baseline_run(
    db: Session,
    *,
    correction: BaselineCorrectionRun,
    candidate_revision: CategoryEvaluationV3Revision,
    candidate_prompt: PromptVersion,
    active_prompts: Mapping[str, PromptVersion],
    execution: Mapping[str, Any],
    orchestration: dict[str, Any],
) -> BaselineRegressionRun:
    if correction.regression_run_id is not None:
        existing = db.get(BaselineRegressionRun, correction.regression_run_id)
        if existing is None:
            raise CorrectionOrchestrationError(
                "CORRECTION_REGRESSION_BINDING_INVALID",
                "自动候选回归绑定已损坏",
            )
        return existing
    source = correction.baseline_run
    model = _model_config_for_run(db, source, execution)
    sampling_policy = _sampling_policy_for_run(db, source)
    prompt_a, prompt_b = _candidate_prompt_pair(
        candidate_prompt=candidate_prompt,
        active_prompts=active_prompts,
    )
    try:
        validate_category_evaluation_prompt_bindings(
            revision_bundle(candidate_revision)["contract"],
            prompt_a_version=prompt_a.version,
            prompt_b_version=prompt_b.version if prompt_b is not None else None,
        )
    except CategoryEvaluationPromptBindingError as exc:
        raise CorrectionOrchestrationError(
            "CORRECTION_CANDIDATE_PROMPT_BINDING_INVALID",
            str(exc),
        ) from exc
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=(prompt_b or prompt_a).rubric_version,
        engine_version=source.strategy_bundle.engine_version or ENGINE_VERSION,
        risk_review_version=source.strategy_bundle.risk_review_version,
        sampling_policy=sampling_policy,
        agent_plan_version=source.strategy_bundle.agent_plan_version,
    )
    strategy_snapshot = build_strategy_snapshot(
        bundle,
        prompt_a,
        prompt_b,
        sampling_policy,
    )
    candidate_execution = dict(execution)
    candidate_execution["prompt_a_id"] = prompt_a.id
    candidate_execution["prompt_b_id"] = prompt_b.id if prompt_b is not None else None
    candidate_execution["rubric_version"] = (prompt_b or prompt_a).rubric_version
    frozen_bundle = revision_bundle(candidate_revision)
    frozen_bundle.update(
        {
            "config_revision": candidate_revision.revision,
            "candidate_revision_id": candidate_revision.id,
        }
    )
    candidate_execution["v3_authoritative_bundle"] = frozen_bundle
    candidate_execution["correction_context"] = {
        "baseline_correction_id": correction.id,
        "source_baseline_run_id": source.id,
        "candidate_revision_id": candidate_revision.id,
    }
    candidate_correction_contract = freeze_contract_from_execution_snapshot(
        category_key=source.category_key,
        execution_snapshot=candidate_execution,
    )
    candidate_execution["correction_contract"] = candidate_correction_contract
    execution_snapshot = canonical_json(candidate_execution)
    maximum_sequence = db.scalar(
        select(func.max(BaselineRegressionRun.sequence_no)).where(
            BaselineRegressionRun.baseline_set_id == source.baseline_set_id
        )
    ) or 0
    initial_metrics = compute_level_metrics(
        {
            "status": "queued",
            "expected_level": item.expected_level,
            "predicted_level": None,
        }
        for item in source.items
    )
    run = BaselineRegressionRun(
        baseline_set_id=source.baseline_set_id,
        sequence_no=maximum_sequence + 1,
        previous_run_id=source.id,
        strategy_bundle_id=bundle.id,
        category_key=source.category_key,
        strategy_snapshot_json=strategy_snapshot,
        execution_snapshot_json=execution_snapshot,
        correction_contract_json=canonical_json(candidate_correction_contract),
        correction_contract_hash=correction_contract_hash(candidate_correction_contract),
        baseline_set_fingerprint=source.baseline_set_fingerprint,
        status="running",
        total=len(source.items),
        metrics_json=canonical_json(initial_metrics),
        created_by="automatic-correction",
    )
    db.add(run)
    db.flush()
    run_items = [
        BaselineRegressionItem(
            run_id=run.id,
            baseline_set_item_id=item.baseline_set_item_id,
            asset_id=item.asset_id,
            expected_level=item.expected_level,
            status="queued",
        )
        for item in source.items
    ]
    db.add_all(run_items)
    db.flush()
    batch_key = f"baseline-correction:{correction.id}:{uuid.uuid4().hex}"
    jobs = [
        EvaluationJob(
            asset_id=item.asset_id,
            category_key=source.category_key,
            category_profile_snapshot_json=execution_snapshot,
            prompt_a_id=prompt_a.id,
            prompt_b_id=prompt_b.id if prompt_b is not None else None,
            baseline_regression_item_id=run_item.id,
            strategy_bundle_id=bundle.id,
            queue_class="validation",
            origin_queue_class="validation",
            batch_key=batch_key,
        )
        for item, run_item in zip(source.items, run_items, strict=True)
    ]
    db.add_all(jobs)
    db.flush()
    for run_item, job in zip(run_items, jobs, strict=True):
        run_item.job_id = job.id
    correction.regression_run_id = run.id
    orchestration["regression"] = {
        "run_id": run.id,
        "job_ids": [job.id for job in jobs],
        "source_run_id": source.id,
        "baseline_set_fingerprint": source.baseline_set_fingerprint,
    }
    return run


def prepare_correction_generation(
    db: Session,
    correction: BaselineCorrectionRun,
) -> PreparedCorrectionGeneration:
    """Freeze deterministic correction inputs in a short database transaction."""
    if correction.status in {"awaiting_decision", "approved", "rejected"}:
        raise CorrectionOrchestrationError(
            "CORRECTION_NOT_RUNNABLE",
            "纠偏任务已进入终态，不能重新执行",
        )
    correction.status = "processing"
    correction.stage = "analysis"
    correction.progress = 10
    correction.blockers_json = "[]"
    correction.error_code = ""
    correction.error_message = ""
    correction.finished_at = None
    snapshot = _json_object(correction.input_snapshot_json, label="纠偏冻结输入")
    report = deterministic_correction_report(snapshot)
    correction.report_json = canonical_json(report)
    orchestration = _json_object(correction.orchestration_json, label="纠偏编排快照")
    projected, active_revision = _active_projection(db, correction.category_key)
    active_prompts, execution = _active_prompts(db, correction)
    orchestration.setdefault(
        "base_projection",
        {
            "config_id": projected.id,
            "revision_id": active_revision.id,
            "revision": projected.revision,
            "contract_hash": projected.contract_hash,
        },
    )
    correction.stage = "candidate_generation"
    correction.progress = 35
    correction.orchestration_json = canonical_json(orchestration)
    return PreparedCorrectionGeneration(
        correction=correction,
        projected=projected,
        active_revision=active_revision,
        active_prompts=active_prompts,
        execution=execution,
        report=report,
        orchestration=orchestration,
    )


def generate_correction_candidate(
    prepared: PreparedCorrectionGeneration,
    generator: CorrectionMechanismGenerator,
) -> GeneratedMechanismCandidate:
    """Call the tuning model without holding a SQLAlchemy Session or DB lock."""
    generated = _candidate_from_orchestration(prepared.orchestration)
    if generated is not None:
        _validate_candidate_routing(generated, prepared.report)
        return generated
    generated = _normalize_generated_candidate(
        generator.generate(
            db=None,
            correction=prepared.correction,
            active_revision=prepared.active_revision,
            active_prompts=prepared.active_prompts,
            report=prepared.report,
        )
    )
    _validate_candidate_routing(generated, prepared.report)
    return generated


def advance_correction_run(
    db: Session,
    correction: BaselineCorrectionRun,
    generator: CorrectionMechanismGenerator | None,
    generated_candidate: GeneratedMechanismCandidate | Mapping[str, Any] | None = None,
) -> None:
    """Advance a correction through analysis, candidate creation and regression."""
    if correction.status in {"awaiting_decision", "approved", "rejected"}:
        return
    correction.status = "processing"
    correction.stage = "analysis"
    correction.progress = 10
    correction.blockers_json = "[]"
    correction.error_code = ""
    correction.error_message = ""
    correction.finished_at = None
    snapshot = _json_object(correction.input_snapshot_json, label="纠偏冻结输入")
    report = deterministic_correction_report(snapshot)
    correction.report_json = canonical_json(report)
    orchestration = _json_object(
        correction.orchestration_json,
        label="纠偏编排快照",
    )
    projected, active_revision = _active_projection(db, correction.category_key)
    active_prompts, execution = _active_prompts(db, correction)
    orchestration.setdefault(
        "base_projection",
        {
            "config_id": projected.id,
            "revision_id": active_revision.id,
            "revision": projected.revision,
            "contract_hash": projected.contract_hash,
        },
    )
    correction.stage = "candidate_generation"
    correction.progress = 35
    if generated_candidate is not None:
        generated = _normalize_generated_candidate(generated_candidate)
        orchestration["generated_candidate"] = _generator_payload(generated)
    else:
        generated = _candidate_from_orchestration(orchestration)
    if generated is None:
        if generator is None:
            raise CorrectionOrchestrationError(
                "CORRECTION_GENERATOR_MISSING",
                "纠偏候选生成器未配置",
            )
        generated = _normalize_generated_candidate(
            generator.generate(
                db=db,
                correction=correction,
                active_revision=active_revision,
                active_prompts=active_prompts,
                report=report,
            )
        )
        orchestration["generated_candidate"] = _generator_payload(generated)
        correction.orchestration_json = canonical_json(orchestration)
    _validate_candidate_routing(generated, report)
    mechanism_fingerprint = str(
        report.get("mechanism_fingerprint")
        or generated.model_snapshot.get("mechanism_fingerprint")
        or ""
    )
    candidate_manifest = {
        "category_key": correction.category_key,
        "lane_key": str(report.get("route_decision", {}).get("route_key") or ""),
        "mechanism_fingerprint": mechanism_fingerprint,
        "route_decision": report.get("route_decision") or {},
        "prompt_snapshot": {
            "stage": generated.prompt.stage,
            "change_note": generated.prompt.change_note,
        },
        "v3_snapshot": generated.revision.contract,
        "change_reasons": generated.summary.get("change_codes", []),
    }
    if len(mechanism_fingerprint) == 64 and all(
        character in "0123456789abcdef" for character in mechanism_fingerprint
    ):
        package = build_immutable_candidate_package(candidate_manifest)
        orchestration["candidate_package"] = {
            "package_key": package.package_key,
            "manifest": json.loads(
                canonical_json(
                    {
                        "schema_version": "automation-candidate-v1",
                        **candidate_manifest,
                    }
                )
            ),
        }
        correction.orchestration_json = canonical_json(orchestration)
    if generated.revision.contract.get("category_key") != correction.category_key:
        raise CorrectionOrchestrationError(
            "CORRECTION_CANDIDATE_CATEGORY_MISMATCH",
            "调优模型返回的机制候选属于其他类目",
        )
    correction.stage = "candidate_validation"
    correction.progress = 55
    candidate_prompt = _ensure_candidate_prompt(
        db,
        correction=correction,
        candidate=generated,
        active_prompts=active_prompts,
        orchestration=orchestration,
    )
    prompt_a, prompt_b = _candidate_prompt_pair(
        candidate_prompt=candidate_prompt,
        active_prompts=active_prompts,
    )
    bound_artifacts = _bound_candidate_artifacts(
        generated,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
    )
    if correction.candidate_revision_id is None:
        candidate_revision, _created = create_candidate_revision(
            db,
            projected,
            parent_revision_id=active_revision.id,
            artifacts=bound_artifacts,
            expected_projected_revision=projected.revision,
            expected_projected_hash=projected.contract_hash,
            actor="automatic-correction",
        )
        correction.candidate_revision_id = candidate_revision.id
    else:
        candidate_revision = db.get(
            CategoryEvaluationV3Revision,
            correction.candidate_revision_id,
        )
        if candidate_revision is None:
            raise CorrectionOrchestrationError(
                "CORRECTION_CANDIDATE_BINDING_INVALID",
                "自动候选 revision 绑定已损坏",
            )
        try:
            validate_category_evaluation_prompt_bindings(
                revision_bundle(candidate_revision)["contract"],
                prompt_a_version=prompt_a.version,
                prompt_b_version=(
                    prompt_b.version if prompt_b is not None else None
                ),
            )
        except CategoryEvaluationPromptBindingError as exc:
            raise CorrectionOrchestrationError(
                "CORRECTION_CANDIDATE_PROMPT_BINDING_INVALID",
                str(exc),
            ) from exc
    orchestration["candidate_revision"] = {
        "id": candidate_revision.id,
        "revision": candidate_revision.revision,
        "contract_hash": candidate_revision.contract_hash,
    }
    orchestration["candidate_summary"] = generated.summary
    orchestration["tuning_model"] = generated.model_snapshot
    correction.orchestration_json = canonical_json(orchestration)
    correction.stage = "regression"
    correction.progress = 75
    _create_candidate_baseline_run(
        db,
        correction=correction,
        candidate_revision=candidate_revision,
        candidate_prompt=candidate_prompt,
        active_prompts=active_prompts,
        execution=execution,
        orchestration=orchestration,
    )
    correction.orchestration_json = canonical_json(orchestration)


def refresh_correction_run(
    db: Session,
    correction: BaselineCorrectionRun,
) -> BaselineCorrectionRun:
    """Advance a candidate regression to the final human-decision gate."""
    if (
        correction.status != "processing"
        or correction.stage != "regression"
        or correction.regression_run_id is None
    ):
        return correction
    regression = db.get(BaselineRegressionRun, correction.regression_run_id)
    if regression is None:
        raise CorrectionOrchestrationError(
            "CORRECTION_REGRESSION_BINDING_INVALID",
            "自动候选回归绑定已损坏",
        )
    if regression.status == "running":
        correction.progress = max(correction.progress, 75)
        return correction
    baseline = correction.baseline_run
    if regression.status not in {"completed", "partial_failed", "failed"}:
        return correction
    candidate = db.get(CategoryEvaluationV3Revision, correction.candidate_revision_id)
    projected = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == correction.category_key,
            CategoryEvaluationV3Config.status == "active",
        )
    )
    try:
        release_report = evaluate_candidate_release_gate(
            db,
            category_key=correction.category_key,
            projected=projected,
            candidate=candidate,
            regression_run=regression,
            expected_projected_revision=(
                int(_json_object(correction.orchestration_json, label="纠偏编排快照")
                    .get("base_projection", {})
                    .get("revision", -1))
            ),
            expected_projected_contract_hash=str(
                _json_object(correction.orchestration_json, label="纠偏编排快照")
                .get("base_projection", {})
                .get("contract_hash", "")
            ),
        )
    except CandidateReleaseGateError as exc:
        baseline_metrics = _json_object(baseline.metrics_json, label="基准回归指标")
        candidate_metrics = _json_object(regression.metrics_json, label="候选回归指标")
        release_report = {
            "schema_version": "baseline-correction-regression-v1",
            "run_id": regression.id,
            "status": regression.status,
            "comparable": False,
            "baseline_metrics": baseline_metrics,
            "candidate_metrics": candidate_metrics,
            "baseline_field_metrics": build_baseline_field_metrics(db, baseline),
            "candidate_field_metrics": build_baseline_field_metrics(db, regression),
            "exact_accuracy_delta": None,
            "adjacent_accuracy_delta": None,
            "regressions": [{"code": exc.code, "message": str(exc)}],
            "recommendation": "reject",
            "approval_allowed": False,
        }
    report = _json_object(correction.report_json, label="纠偏分析报告")
    report["candidate_regression"] = release_report
    correction.report_json = canonical_json(report)
    correction.status = "awaiting_decision"
    correction.stage = "decision"
    correction.progress = 100
    correction.blockers_json = "[]"
    correction.error_code = ""
    correction.error_message = ""
    correction.finished_at = datetime.now(timezone.utc)
    return correction


class RegisteredTuningMechanismGenerator:
    def __init__(self, entry: ModelRegistryEntry, config: Any) -> None:
        self.entry = entry
        self.config = config

    def generate(
        self,
        *,
        db: Session,
        correction: BaselineCorrectionRun,
        active_revision: CategoryEvaluationV3Revision,
        active_prompts: Mapping[str, PromptVersion],
        report: Mapping[str, Any],
    ) -> GeneratedMechanismCandidate:
        del db
        client = DoubaoClient(self.config)
        routing_constraints = _candidate_routing_constraints(report)
        model_snapshot = {
            "registry_entry_id": self.entry.id,
            "role": self.entry.role,
            "provider": self.entry.provider,
            "protocol": self.entry.protocol,
            "model_id": self.entry.model_id,
            "thinking_mode": self.entry.thinking_mode,
            "level": self.entry.level,
        }
        generator_input = {
            "schema_version": "baseline-correction-generator-input-v3",
            "category_key": correction.category_key,
            "correction_report": report,
            "routing_constraints": routing_constraints,
            "active_revision": revision_bundle(active_revision),
            "active_prompts": {
                stage: {
                    "stage": prompt.stage,
                    "name": prompt.name,
                    "version": prompt.version,
                    "system_prompt": prompt.system_prompt,
                    "user_prompt": prompt.user_prompt,
                    "rubric_version": prompt.rubric_version,
                }
                for stage, prompt in active_prompts.items()
            },
            "required_output": {
                "prompt": {
                    "stage": "A or B",
                    "system_prompt": "changed full text; omit to inherit",
                    "user_prompt": "changed full text; omit to inherit",
                    "change_note": "required summary",
                },
                "revision": {
                    "display_name": "optional candidate name",
                    "contract": "optional JSON merge patch",
                    "classification_map": "optional JSON merge patch",
                    "subcategory_dimensions": "optional JSON merge patch",
                },
                "summary": {"change_codes": ["code"]},
            },
        }
        system_prompt = (
            "你是特鹏标签中台的评测机制调优器。人工真值优先。根据纠偏报告只返回需要修改的"
            "提示词和等级规则差异；平台会从冻结现役机制继承未返回字段，并在服务端合成、严格"
            "校验和回归。必须同时返回 prompt 与 revision 对象，revision 可为空对象；不得发布"
            "或启用。候选提示词阶段必须遵守 routing_constraints，自动纠偏记录不能冒充人工"
            "证据。JSON 对象中的 null 表示删除字段。只输出合法 JSON。"
        )
        user_prompt = json.dumps(generator_input, ensure_ascii=False)
        generation_trace: list[dict[str, Any]] = []
        last_error: CorrectionOrchestrationError | None = None
        preserved_delta: dict[str, Any] = {}
        for attempt in range(1, 3):
            response = asyncio.run(
                client.chat_json(
                    system_prompt,
                    user_prompt,
                    output_budget=min(max(1, int(self.entry.max_tokens)), 12000),
                    reasoning_effort=(
                        "high"
                        if self.entry.level in {"advanced", "expert"}
                        else "medium"
                    ),
                    structured_output=True,
                )
            )
            raw_text = str(getattr(response, "raw_text", ""))
            trace_entry: dict[str, Any] = {
                "attempt": attempt,
                "request_correlation_id": getattr(
                    response, "request_correlation_id", None
                ),
                "provider_attempt_count": getattr(response, "attempt_count", None),
                "usage": {
                    "input_tokens": getattr(response, "input_tokens", None),
                    "output_tokens": getattr(response, "output_tokens", None),
                    "total_tokens": getattr(response, "total_tokens", None),
                },
                "raw_text": raw_text[:65536],
                "raw_text_truncated": len(raw_text) > 65536,
            }
            parsed = getattr(response, "parsed", None)
            try:
                if not isinstance(parsed, dict):
                    raise CorrectionOrchestrationError(
                        "CORRECTION_GENERATOR_OUTPUT_INVALID",
                        "调优模型未返回 JSON 对象",
                    )
                current_delta = _salvage_candidate_delta(parsed)
                candidate_payload = _merge_json_patch(preserved_delta, parsed)
                candidate_payload = _merge_json_patch(
                    candidate_payload, preserved_delta
                )
                candidate_payload = _merge_json_patch(
                    candidate_payload, current_delta
                )
                if "summary" in current_delta:
                    candidate_payload["summary"] = deepcopy(current_delta["summary"])
                candidate_payload["model_snapshot"] = model_snapshot
                candidate = _normalize_generated_candidate(
                    candidate_payload,
                    active_revision=active_revision,
                    active_prompts=active_prompts,
                )
                _validate_candidate_routing(candidate, report)
                try:
                    validate_mechanism_artifacts(
                        candidate.revision.contract,
                        candidate.revision.classification_map,
                        candidate.revision.subcategory_dimensions,
                        require_database=False,
                    )
                except MechanismProfileError as exc:
                    raise CorrectionOrchestrationError(
                        "CORRECTION_GENERATOR_OUTPUT_INVALID",
                        f"{exc.target} 校验失败：{exc}",
                    ) from exc
            except CorrectionOrchestrationError as exc:
                last_error = exc
                trace_entry.update(
                    {
                        "status": "invalid",
                        "error_code": exc.code,
                        "error_message": str(exc),
                    }
                )
                generation_trace.append(trace_entry)
                if attempt == 2:
                    raise CorrectionCandidateGenerationError(
                        exc.code,
                        str(exc),
                        generation_trace=generation_trace,
                    ) from exc
                preserved_delta = _merge_json_patch(
                    preserved_delta,
                    _salvage_candidate_delta(parsed),
                )
                user_prompt = json.dumps(
                    {
                        "schema_version": "baseline-correction-generator-repair-v1",
                        "original_input": generator_input,
                        "invalid_output": parsed,
                        "validation_error": {
                            "code": exc.code,
                            "message": str(exc),
                        },
                        "instruction": (
                            "修复结构错误后重新输出候选 JSON。只修改需要修复的字段；"
                            "未修改字段继续由平台继承。"
                        ),
                    },
                    ensure_ascii=False,
                )
                continue
            trace_entry["status"] = "valid"
            generation_trace.append(trace_entry)
            return replace(candidate, generation_trace=generation_trace)
        assert last_error is not None
        raise CorrectionCandidateGenerationError(
            last_error.code,
            str(last_error),
            generation_trace=generation_trace,
        )


def configured_correction_generator(
    db: Session,
) -> CorrectionMechanismGenerator:
    entry = db.scalar(
        select(ModelRegistryEntry)
        .where(
            ModelRegistryEntry.role == "tuning",
            ModelRegistryEntry.active.is_(True),
        )
        .order_by(ModelRegistryEntry.id.asc())
        .limit(1)
    )
    if entry is None:
        raise CorrectionOrchestrationError(
            "CORRECTION_TUNING_MODEL_MISSING",
            "未配置可用的调优模型；请由管理员在模型注册中心启用调优模型",
        )
    try:
        capabilities = json.loads(entry.capabilities_json or "[]")
    except json.JSONDecodeError as exc:
        raise CorrectionOrchestrationError(
            "CORRECTION_TUNING_MODEL_INVALID",
            "调优模型能力配置损坏",
        ) from exc
    if (
        not entry.structured_output
        or not isinstance(capabilities, list)
        or "text" not in capabilities
        or "structured_output" not in capabilities
        or entry.max_tokens < 1
    ):
        raise CorrectionOrchestrationError(
            "CORRECTION_TUNING_MODEL_INVALID",
            "调优模型必须支持文本与结构化输出，并配置有效输出上限",
        )
    source: ModelConfig | OptimizerConfig | None = None
    if entry.source_optimizer_config_id is not None:
        source = db.get(OptimizerConfig, entry.source_optimizer_config_id)
    elif entry.source_model_config_id is not None:
        source = db.get(ModelConfig, entry.source_model_config_id)
    encrypted_api_key = entry.encrypted_api_key or getattr(
        source, "encrypted_api_key", None
    )
    if not encrypted_api_key:
        raise CorrectionOrchestrationError(
            "CORRECTION_TUNING_MODEL_CREDENTIAL_MISSING",
            "调优模型尚未配置凭据",
        )
    config = SimpleNamespace(
        provider=entry.provider,
        protocol=entry.protocol,
        base_url=entry.base_url,
        api_path=entry.api_path,
        model_id=entry.model_id,
        encrypted_api_key=encrypted_api_key,
        temperature=entry.temperature,
        max_tokens=entry.max_tokens,
        timeout_seconds=entry.timeout_seconds,
        max_retries=entry.max_retries,
        structured_output=entry.structured_output,
        thinking_mode=entry.thinking_mode,
    )
    return RegisteredTuningMechanismGenerator(entry, config)
