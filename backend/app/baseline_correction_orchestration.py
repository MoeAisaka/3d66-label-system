"""Automatic mechanism-candidate and baseline-regression orchestration."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .baseline_regression import compute_level_metrics, deterministic_correction_report
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


def _normalize_generated_candidate(
    value: GeneratedMechanismCandidate | Mapping[str, Any],
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
                for key in ("summary", "model_snapshot"):
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
    system_prompt = str(prompt.get("system_prompt") or "").strip()
    user_prompt = str(prompt.get("user_prompt") or "").strip()
    change_note = str(prompt.get("change_note") or "").strip()
    display_name = str(revision.get("display_name") or "").strip()
    contract = revision.get("contract")
    classification_map = revision.get("classification_map")
    subcategory_dimensions = revision.get("subcategory_dimensions")
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
        return generated
    return _normalize_generated_candidate(
        generator.generate(
            db=None,
            correction=prepared.correction,
            active_revision=prepared.active_revision,
            active_prompts=prepared.active_prompts,
            report=prepared.report,
        )
    )


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
    baseline_metrics = _json_object(
        baseline.metrics_json,
        label="基准回归指标",
    )
    candidate_metrics = _json_object(
        regression.metrics_json,
        label="候选回归指标",
    )
    comparable = bool(
        regression.baseline_set_fingerprint
        == baseline.baseline_set_fingerprint
        and candidate_metrics.get("denominator", 0) > 0
        and baseline_metrics.get("denominator", 0) > 0
    )
    exact_delta = (
        float(candidate_metrics.get("exact_accuracy", 0.0))
        - float(baseline_metrics.get("exact_accuracy", 0.0))
        if comparable
        else None
    )
    adjacent_delta = (
        float(candidate_metrics.get("adjacent_accuracy", 0.0))
        - float(baseline_metrics.get("adjacent_accuracy", 0.0))
        if comparable
        else None
    )
    regressions: list[dict[str, Any]] = []
    if not comparable:
        regressions.append(
            {"code": "not_comparable", "message": "候选回归与基准不可比"}
        )
    if regression.status != "completed":
        regressions.append(
            {
                "code": "candidate_run_incomplete",
                "message": "候选回归存在失败条目",
            }
        )
    if exact_delta is not None and exact_delta < 0:
        regressions.append(
            {
                "code": "exact_accuracy_regressed",
                "message": "Exact Accuracy 低于基准",
                "delta": exact_delta,
            }
        )
    if adjacent_delta is not None and adjacent_delta < 0:
        regressions.append(
            {
                "code": "adjacent_accuracy_regressed",
                "message": "Adjacent Accuracy 低于基准",
                "delta": adjacent_delta,
            }
        )
    if int(candidate_metrics.get("failed", 0)) > int(
        baseline_metrics.get("failed", 0)
    ):
        regressions.append(
            {
                "code": "failed_count_regressed",
                "message": "候选回归失败条目增加",
            }
        )
    approval_allowed = comparable and not regressions
    report = _json_object(correction.report_json, label="纠偏分析报告")
    report["candidate_regression"] = {
        "schema_version": "baseline-correction-regression-v1",
        "run_id": regression.id,
        "status": regression.status,
        "comparable": comparable,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "exact_accuracy_delta": exact_delta,
        "adjacent_accuracy_delta": adjacent_delta,
        "regressions": regressions,
        "recommendation": "approve" if approval_allowed else "reject",
        "approval_allowed": approval_allowed,
    }
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
        response = asyncio.run(
            client.chat_json(
                "你是标签实验台的评测机制调优器。人工真值优先。根据纠偏报告生成一个完整、"
                "可校验、可回归的统一机制候选。必须同时返回 prompt 与 revision；revision 必须"
                "包含完整 contract、classification_map、subcategory_dimensions，不得只返回差异，"
                "不得发布或启用。只输出合法 JSON。",
                json.dumps(
                    {
                        "schema_version": "baseline-correction-generator-input-v1",
                        "category_key": correction.category_key,
                        "correction_report": report,
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
                                "system_prompt": "full text",
                                "user_prompt": "full text",
                                "change_note": "summary",
                            },
                            "revision": {
                                "display_name": "candidate name",
                                "contract": "full object",
                                "classification_map": "full object",
                                "subcategory_dimensions": "full object",
                            },
                            "summary": {"change_codes": ["code"]},
                        },
                    },
                    ensure_ascii=False,
                ),
                output_budget=min(max(1, int(self.entry.max_tokens)), 12000),
                reasoning_effort=(
                    "high" if self.entry.level in {"advanced", "expert"} else "medium"
                ),
                structured_output=True,
            )
        )
        parsed = response.parsed
        if not isinstance(parsed, dict):
            raise CorrectionOrchestrationError(
                "CORRECTION_GENERATOR_OUTPUT_INVALID",
                "调优模型未返回 JSON 对象",
            )
        parsed = dict(parsed)
        parsed["model_snapshot"] = {
            "registry_entry_id": self.entry.id,
            "role": self.entry.role,
            "provider": self.entry.provider,
            "protocol": self.entry.protocol,
            "model_id": self.entry.model_id,
            "thinking_mode": self.entry.thinking_mode,
            "level": self.entry.level,
        }
        return _normalize_generated_candidate(parsed)


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
