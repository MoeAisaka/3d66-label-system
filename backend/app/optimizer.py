from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .dimension_deduction_bridge import (
    SUPPORTED_PLACEHOLDERS,
    unknown_placeholders,
)
from .dimension_schema_registry import canonical_hash
from .doubao import DoubaoClient, DoubaoError, DoubaoResponse
from .models import ModelNodeBinding, OptimizerConfig, PromptOptimizationRun
from .nas_storage import NasStorageError, resolve_asset_path
from .regression import (
    dimension_contract_for_result,
    latest_review_for_result,
)


DIAGNOSTIC_OUTPUT_BUDGET = 2048
SYNTHESIS_OUTPUT_BUDGET = 4096
OPTIMIZER_REASONING_EFFORT = "high"
MAX_DIAGNOSTIC_IMAGES = 8
MAX_DIAGNOSTIC_SINGLE_IMAGE_BYTES = 16 * 1024 * 1024
MAX_DIAGNOSTIC_IMAGE_BYTES = 32 * 1024 * 1024
STAGE_AUDIT_FIELDS = (
    "status",
    "attempt_count",
    "upstream_status_code",
    "request_correlation_id",
    "elapsed_ms",
    "error_type",
    "error_message",
    "output_budget",
    "reasoning_effort",
)
_AUDIT_STATUSES = {
    "not_recorded",
    "pending",
    "running",
    "succeeded",
    "failed",
}
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
_SAFE_ERROR_TYPE = re.compile(r"^[a-z0-9_.-]{1,80}$")


@dataclass(frozen=True)
class AutomationCandidateGeneration:
    candidates: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    total_tokens: int


def reject_unrunnable_candidate_prompt(user_prompt: str, *, source: str) -> None:
    """Reject an AI-written candidate body that the engine could never execute.

    A candidate carrying an unsupported ``{{placeholder}}`` can be created and
    even adopted, then fail on every single regression sample -- the operator
    would have to read N identical failures before learning the model wrote a
    broken prompt.  Catching it at generation time makes the diagnosis immediate.
    """
    unknown = unknown_placeholders(user_prompt)
    if not unknown:
        return
    raise ValueError(
        f"{source}正文含平台不支持的占位符 {'、'.join(unknown)}，"
        f"服务端无法替换它们，这样的候选在回归时每条样本都会被拒。"
        f"可用的占位符只有 {'、'.join(SUPPORTED_PLACEHOLDERS)}"
    )


async def generate_automation_candidates(
    *,
    config: OptimizerConfig,
    base_prompt: Any,
    frozen_input: dict[str, Any],
    max_candidates: int,
) -> AutomationCandidateGeneration:
    """Run the existing diagnosis/synthesis pattern for queued correction cases."""
    client = DoubaoClient(config)
    diagnostic = await client.chat_json(
        "你是3D66提示词纠偏诊断专家。人工真值优先。只输出合法JSON，"
        "字段为 summary、patterns、prompt_risks；不得生成最终提示词。",
        json.dumps(
            {
                "base_prompt": {
                    "stage": base_prompt.stage,
                    "version": base_prompt.version,
                    "system_prompt": base_prompt.system_prompt,
                    "user_prompt": base_prompt.user_prompt,
                },
                "cases": frozen_input["cases"],
            },
            ensure_ascii=False,
        ),
        output_budget=DIAGNOSTIC_OUTPUT_BUDGET,
        reasoning_effort=OPTIMIZER_REASONING_EFFORT,
        structured_output=True,
    )
    if (
        diagnostic.input_tokens is None
        or diagnostic.output_tokens is None
        or diagnostic.total_tokens is None
    ):
        raise RuntimeError("optimizer_usage_missing")
    synthesis = await client.chat_json(
        "你是3D66提示词优化专家。根据诊断做最小且可回归的修改，"
        "保留原有字段、JSON结构、安全边界和调用变量。只输出合法JSON，"
        "字段 candidates；每个候选必须含 system_prompt、user_prompt、change_note。"
        f"user_prompt 里只允许出现这些双花括号占位符：{'、'.join(SUPPORTED_PLACEHOLDERS)}；"
        "写出其他双花括号标记会导致候选无法执行。",
        json.dumps(
            {
                "base_prompt": {
                    "stage": base_prompt.stage,
                    "version": base_prompt.version,
                    "system_prompt": base_prompt.system_prompt,
                    "user_prompt": base_prompt.user_prompt,
                },
                "diagnosis": diagnostic.parsed,
                "candidate_limit": max_candidates,
            },
            ensure_ascii=False,
        ),
        output_budget=SYNTHESIS_OUTPUT_BUDGET,
        reasoning_effort=OPTIMIZER_REASONING_EFFORT,
        structured_output=True,
    )
    if (
        synthesis.input_tokens is None
        or synthesis.output_tokens is None
        or synthesis.total_tokens is None
    ):
        raise RuntimeError("optimizer_usage_missing")
    responses = (diagnostic, synthesis)
    candidates = synthesis.parsed.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        single = {
            "system_prompt": synthesis.parsed.get("candidate_system_prompt"),
            "user_prompt": synthesis.parsed.get("candidate_user_prompt"),
            "change_note": synthesis.parsed.get("change_note"),
        }
        candidates = [single]
    if len(candidates) > max_candidates:
        raise ValueError("优化模型返回候选数超过策略上限")
    normalized = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("优化候选结构无效")
        system_prompt = str(candidate.get("system_prompt") or "").strip()
        user_prompt = str(candidate.get("user_prompt") or "").strip()
        change_note = str(candidate.get("change_note") or "").strip()
        if not system_prompt or not user_prompt or not change_note:
            raise ValueError("优化候选缺少提示词或变更说明")
        reject_unrunnable_candidate_prompt(user_prompt, source="优化候选")
        normalized.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "change_note": change_note,
            }
        )
    return AutomationCandidateGeneration(
        candidates=normalized,
        input_tokens=sum(int(response.input_tokens or 0) for response in responses),
        output_tokens=sum(int(response.output_tokens or 0) for response in responses),
        total_tokens=sum(int(response.total_tokens or 0) for response in responses),
    )


def _empty_stage_audit() -> dict[str, Any]:
    return {
        "status": "not_recorded",
        "attempt_count": 0,
        "upstream_status_code": None,
        "request_correlation_id": None,
        "elapsed_ms": None,
        "error_type": None,
        "error_message": None,
        "output_budget": None,
        "reasoning_effort": None,
    }


def _normalized_message(value: Any, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:limit]


def sanitize_stage_audit(value: Any) -> dict[str, Any]:
    """Return the only optimizer transport fields allowed across storage/API."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            value = {}
    source = value if isinstance(value, dict) else {}
    audit = _empty_stage_audit()

    status = source.get("status")
    if status in _AUDIT_STATUSES:
        audit["status"] = status

    attempt_count = source.get("attempt_count")
    if (
        isinstance(attempt_count, int)
        and not isinstance(attempt_count, bool)
        and 0 <= attempt_count <= 100
    ):
        audit["attempt_count"] = attempt_count

    status_code = source.get("upstream_status_code")
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    ):
        audit["upstream_status_code"] = status_code

    request_id = source.get("request_correlation_id")
    if isinstance(request_id, str) and _SAFE_IDENTIFIER.fullmatch(request_id):
        audit["request_correlation_id"] = request_id

    elapsed_ms = source.get("elapsed_ms")
    if (
        isinstance(elapsed_ms, int)
        and not isinstance(elapsed_ms, bool)
        and 0 <= elapsed_ms <= 86_400_000
    ):
        audit["elapsed_ms"] = elapsed_ms

    error_type = source.get("error_type")
    if isinstance(error_type, str) and _SAFE_ERROR_TYPE.fullmatch(error_type):
        audit["error_type"] = error_type
    error_message = source.get("error_message")
    if isinstance(error_message, str):
        audit["error_message"] = _normalized_message(error_message)

    output_budget = source.get("output_budget")
    if (
        isinstance(output_budget, int)
        and not isinstance(output_budget, bool)
        and 1 <= output_budget <= 1_000_000
    ):
        audit["output_budget"] = output_budget

    reasoning_effort = source.get("reasoning_effort")
    if reasoning_effort in {"low", "medium", "high"}:
        audit["reasoning_effort"] = reasoning_effort
    return audit


def stage_audit_payload(run: PromptOptimizationRun) -> dict[str, dict[str, Any]]:
    return {
        "diagnostic": sanitize_stage_audit(run.diagnostic_audit_json),
        "synthesis": sanitize_stage_audit(run.synthesis_audit_json),
    }


def _stage_audit_json(**values: Any) -> str:
    return json.dumps(
        sanitize_stage_audit(values),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _normalized_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, DoubaoError):
        error_type = str(exc.technical_error_type)
        if not _SAFE_ERROR_TYPE.fullmatch(error_type):
            error_type = "model_error"
        return error_type, _normalized_message(str(exc)) or "模型调用失败"
    if isinstance(exc, ValueError):
        return (
            "invalid_stage_output",
            _normalized_message(str(exc)) or "优化阶段输出无效",
        )
    return "internal_error", "提示词优化阶段发生内部错误"


def _response_context(
    source: DoubaoResponse | Exception | None,
) -> tuple[int | None, str | None, int]:
    if source is None:
        return None, None, 0
    status_code = getattr(source, "upstream_status_code", None)
    request_id = getattr(source, "request_correlation_id", None)
    attempt_count = getattr(source, "attempt_count", 0)
    return status_code, request_id, attempt_count


def _review_record(item: Any) -> dict[str, Any] | None:
    result = item.source_result
    review = latest_review_for_result(result)
    if not review or review.decision not in {"approved", "corrected"}:
        return None
    aesthetic = json.loads(result.aesthetic_json) if result.aesthetic_json else {}
    definition, dimension_keys, identity = (
        dimension_contract_for_result(result)
    )
    definitions_by_key = {
        dimension.get("key"): dimension
        for dimension in definition.get("dimensions") or []
        if isinstance(dimension, dict)
    }
    dimension_names = {
        key: definitions_by_key.get(key, {}).get("label")
        for key in dimension_keys
    }
    if not all(
        isinstance(label, str) and bool(label)
        for label in dimension_names.values()
    ):
        raise ValueError("DimensionSchema 缺少优化器所需的维度名称")
    dimension_schema = (
        {
            key: identity[key]
            for key in (
                "schema_id",
                "schema_key",
                "version",
                "canonical_hash",
            )
        }
        if identity is not None
        else {
            "schema_id": None,
            "schema_key": "dimension.space.compatibility",
            "version": str(
                definition.get("compatibility_revision")
                or "legacy-derived"
            ),
            "canonical_hash": canonical_hash(definition),
        }
    )
    corrections = json.loads(review.corrections_json or "[]")
    return {
        "asset_id": item.asset_id,
        "asset_name": item.asset.original_name,
        "source_evaluation_id": result.id,
        "source_review_id": review.id,
        "decision": review.decision,
        "model_level": result.level,
        "human_level": review.corrected_level or result.level,
        "model_dimensions": (aesthetic or {}).get("dimensions", {}),
        "human_corrections": corrections,
        "human_note": review.note,
        "model_id": result.model_id,
        "prompt_version": result.prompt_b_version,
        "dimension_schema": dimension_schema,
        "dimension_names": dimension_names,
    }


def _select_records(items: list[Any]) -> tuple[list[tuple[Any, dict[str, Any]]], list[int], int]:
    eligible: list[tuple[Any, dict[str, Any]]] = []
    for item in items:
        record = _review_record(item)
        if record:
            eligible.append((item, record))
    eligible.sort(key=lambda pair: pair[0].asset_id)
    schema_hashes = {
        str((record.get("dimension_schema") or {}).get("canonical_hash") or "")
        for _, record in eligible
    }
    if "" in schema_hashes:
        raise ValueError("人工样本缺少可复算的维度规则身份")
    if len(schema_hashes) > 1:
        raise ValueError(
            "同一优化任务不能混用不同 DimensionSchema 的人工样本"
        )

    holdout_ids: list[int] = []
    analysis_pool: list[tuple[Any, dict[str, Any]]] = []
    for index, pair in enumerate(eligible):
        if len(eligible) >= 10 and index % 5 == 4:
            holdout_ids.append(pair[0].asset_id)
        else:
            analysis_pool.append(pair)

    corrected = [pair for pair in analysis_pool if pair[1]["decision"] == "corrected"]
    controls = [pair for pair in analysis_pool if pair[1]["decision"] == "approved"]

    def stable_sample_key(pair: tuple[Any, dict[str, Any]]) -> str:
        item, _ = pair
        identity = (
            f"{getattr(item.asset, 'sha256', item.asset.stored_name)}:"
            f"{item.asset_id}"
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    corrected.sort(key=stable_sample_key)
    controls.sort(key=stable_sample_key)
    # Keep the complete deterministic role pools until file validity and request
    # limits are applied, so invalid early records can be backfilled safely.
    selected = corrected + controls
    for _, record in selected:
        record["sample_role"] = (
            "target_error"
            if record["decision"] == "corrected"
            else "stable_control"
        )
    return selected, holdout_ids, len(eligible)


def _bounded_diagnostic_records(
    selected: list[tuple[Any, dict[str, Any]]],
    upload_dir: Any,
    *,
    asset_path_resolver: Any | None = None,
) -> tuple[list[tuple[Any, dict[str, Any], Any]], int, int]:
    """Choose a deterministic role-stratified request under metadata limits."""
    candidates: dict[str, list[tuple[Any, dict[str, Any], Any, int]]] = {
        "target_error": [],
        "stable_control": [],
    }
    omitted_count = 0
    for item, record in selected:
        try:
            image_path = (
                asset_path_resolver(item.asset)
                if asset_path_resolver is not None
                else upload_dir / item.asset.stored_name
            )
            image_bytes = image_path.stat().st_size
        except NasStorageError:
            # NAS 挂载不可用或哈希漂移是环境故障，不能和"图片太大"一样静默跳过：
            # 那会让优化器在几乎没有真实图片的情况下照常出结论。
            raise
        except OSError:
            omitted_count += 1
            continue
        if not 0 < image_bytes <= MAX_DIAGNOSTIC_SINGLE_IMAGE_BYTES:
            omitted_count += 1
            continue
        candidates[record["sample_role"]].append(
            (item, record, image_path, image_bytes)
        )

    ordered = []
    corrected = candidates["target_error"]
    controls = candidates["stable_control"]
    had_controls = bool(controls)
    if corrected:
        ordered.append(corrected.pop(0))
    if controls:
        ordered.append(controls.pop(0))
    while corrected or controls:
        if corrected:
            ordered.append(corrected.pop(0))
        if controls:
            ordered.append(controls.pop(0))

    bounded: list[tuple[Any, dict[str, Any], Any]] = []
    total_bytes = 0
    for item, record, image_path, image_bytes in ordered:
        if (
            len(bounded) >= MAX_DIAGNOSTIC_IMAGES
            or total_bytes + image_bytes > MAX_DIAGNOSTIC_IMAGE_BYTES
        ):
            omitted_count += 1
            continue
        bounded.append((item, record, image_path))
        total_bytes += image_bytes
    corrected_count = sum(
        1 for _, record, _ in bounded if record["decision"] == "corrected"
    )
    if corrected_count == 0:
        raise ValueError("安全限额内至少需要一张带有人工纠错的图片")
    if had_controls and not any(
        record["decision"] == "approved" for _, record, _ in bounded
    ):
        raise ValueError("安全限额内无法保留稳定对照样本")
    return bounded, total_bytes, omitted_count


async def run_prompt_optimization(run_id: int) -> None:
    db = SessionLocal()
    run: PromptOptimizationRun | None = None
    active_stage = "diagnostic"
    try:
        run = db.get(PromptOptimizationRun, run_id)
        binding = db.scalar(select(ModelNodeBinding).where(ModelNodeBinding.node_key == "diagnostic", ModelNodeBinding.enabled.is_(True)))
        config = binding.model if binding is not None and binding.model.active else db.scalar(select(OptimizerConfig).limit(1))
        if not run or not config:
            return
        run.status = "running"
        run.progress = 3
        run.candidate_system_prompt = ""
        run.candidate_user_prompt = ""
        run.change_note = ""
        run.error_message = ""
        run.diagnosis_json = "{}"
        run.diagnostic_audit_json = _stage_audit_json(
            status="running",
            attempt_count=0,
            output_budget=DIAGNOSTIC_OUTPUT_BUDGET,
            reasoning_effort=OPTIMIZER_REASONING_EFFORT,
        )
        run.synthesis_audit_json = _stage_audit_json(
            status="pending",
            attempt_count=0,
            output_budget=SYNTHESIS_OUTPUT_BUDGET,
            reasoning_effort=OPTIMIZER_REASONING_EFFORT,
        )
        db.commit()

        prompt = run.base_prompt
        sample_set = run.sample_set
        selected, holdout_ids, eligible_count = _select_records(list(sample_set.items))
        corrected_count = sum(1 for _, record in selected if record["decision"] == "corrected")
        if not selected:
            raise ValueError("样本集中没有已完成人工确认的图片")
        if corrected_count == 0:
            raise ValueError("至少需要一张带有人工纠错的图片，只有确认正确的样本无法定位提示词问题")
        dimension_schema = selected[0][1]["dimension_schema"]
        dimension_names = selected[0][1]["dimension_names"]

        run.sample_count = eligible_count
        run.corrected_count = corrected_count
        db.commit()

        client = DoubaoClient(config)
        diagnostic_system = (
            "你是空间与建筑图片美感评测提示词的错误分析专家。"
            "你只负责根据模型结果与人工结果的差异定位问题，不生成最终提示词。"
            "人工纠错是参考标准；已确认正确的样本是防退化对照。"
            "区分视觉识别问题、提示词规则问题和外部评分引擎问题。"
            "不得为单张图片编写特殊规则，只有重复规律才能进入修改建议。"
            "只输出合法JSON，字段为 summary、cases、patterns、prompt_risks。"
        )
        settings = get_settings()
        if hasattr(settings, "nas_maps_root"):
            bounded_records, diagnostic_image_bytes, omitted_image_count = (
                _bounded_diagnostic_records(
                    selected,
                    settings.upload_dir,
                    asset_path_resolver=lambda asset: resolve_asset_path(asset, settings),
                )
            )
        else:
            # Keep lightweight optimizer test doubles and older integrations
            # compatible with the pre-NAS settings shape.
            bounded_records, diagnostic_image_bytes, omitted_image_count = (
                _bounded_diagnostic_records(selected, settings.upload_dir)
            )
        selected = [(item, record) for item, record, _ in bounded_records]
        corrected_count = sum(
            1 for _, record in selected if record["decision"] == "corrected"
        )
        if corrected_count == 0:
            raise ValueError("安全限额内至少需要一张带有人工纠错的图片")
        run.corrected_count = corrected_count
        db.commit()
        holdout_set = set(holdout_ids)
        sample_items = [
            {
                "sample_item_id": item.id,
                "role": record["sample_role"],
            }
            for item, record in selected
        ]
        sample_items.extend(
            {
                "sample_item_id": item.id,
                "role": "blind_holdout",
            }
            for item in sample_set.items
            if item.asset_id in holdout_set
        )
        sample_items.sort(
            key=lambda item: (item["role"], item["sample_item_id"])
        )
        samples = []
        for item, record, image_path in bounded_records:
            sample_text = json.dumps(
                {
                    "task": "诊断这张图片的模型判断与人工纠错差异",
                    "dimension_names": dimension_names,
                    "sample": record,
                },
                ensure_ascii=False,
            )
            samples.append(
                (
                    sample_text,
                    image_path,
                    item.asset.mime_type,
                )
            )

        diagnostic_started = time.perf_counter()
        diagnostic_response: DoubaoResponse | None = None
        try:
            diagnostic_response = await client.chat_json_images(
                diagnostic_system,
                samples,
                max_attempts=1,
                output_budget=DIAGNOSTIC_OUTPUT_BUDGET,
                reasoning_effort=OPTIMIZER_REASONING_EFFORT,
                structured_output=True,
                max_image_count=MAX_DIAGNOSTIC_IMAGES,
                max_single_image_bytes=MAX_DIAGNOSTIC_SINGLE_IMAGE_BYTES,
                max_total_image_bytes=MAX_DIAGNOSTIC_IMAGE_BYTES,
            )
        except Exception as exc:
            status_code, request_id, attempt_count = _response_context(exc)
            error_type, error_message = _normalized_error(exc)
            run.diagnostic_audit_json = _stage_audit_json(
                status="failed",
                attempt_count=attempt_count,
                upstream_status_code=status_code,
                request_correlation_id=request_id,
                elapsed_ms=_elapsed_ms(diagnostic_started),
                error_type=error_type,
                error_message=error_message,
                output_budget=DIAGNOSTIC_OUTPUT_BUDGET,
                reasoning_effort=OPTIMIZER_REASONING_EFFORT,
            )
            run.status = "failed"
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        sample_policy = {
            "eligible_count": eligible_count,
            "analysis_count": len(selected),
            "corrected_count": corrected_count,
            "control_count": len(selected) - corrected_count,
            "diagnostic_image_bytes": (
                diagnostic_response.input_image_bytes
                if diagnostic_response.input_image_bytes is not None
                else diagnostic_image_bytes
            ),
            "diagnostic_image_limit": MAX_DIAGNOSTIC_IMAGES,
            "diagnostic_byte_limit": MAX_DIAGNOSTIC_IMAGE_BYTES,
            "omitted_image_count": omitted_image_count,
            "blind_holdout_count": len(holdout_ids),
            "blind_holdout_asset_ids": holdout_ids,
            "sample_items": sample_items,
            "regression_roles": [
                "target_error",
                "stable_control",
                "blind_holdout",
            ],
            "dimension_schema": dimension_schema,
        }
        diagnostic_result = diagnostic_response.parsed
        run.diagnosis_json = json.dumps(
            {
                **diagnostic_result,
                "diagnostic": diagnostic_result,
                "batch_diagnoses": [diagnostic_result],
                "sample_policy": sample_policy,
            },
            ensure_ascii=False,
        )
        status_code, request_id, attempt_count = _response_context(
            diagnostic_response
        )
        run.diagnostic_audit_json = _stage_audit_json(
            status="succeeded",
            attempt_count=attempt_count,
            upstream_status_code=status_code,
            request_correlation_id=request_id,
            elapsed_ms=_elapsed_ms(diagnostic_started),
            output_budget=diagnostic_response.output_budget
            or DIAGNOSTIC_OUTPUT_BUDGET,
            reasoning_effort=diagnostic_response.reasoning_effort
            or OPTIMIZER_REASONING_EFFORT,
        )
        run.progress = 72
        # 诊断结果和审计必须先成为独立持久化事实，再允许进入合成阶段。
        db.commit()

        synthesis_system = (
            "你是3D66美感评测提示词优化专家。根据人工校验样本的分组诊断，"
            "对当前提示词做最小范围、可回归验证的修改。"
            "锁定并保留原有JSON输出结构、字段名、业务分类、安全边界和调用变量。"
            "优先修改维度定义、等级边界、常见误判规则和正反例。"
            "证据不足时保持原文；不得针对单张图片增加特例。"
            f"candidate_user_prompt 里只允许出现这些双花括号占位符："
            f"{'、'.join(SUPPORTED_PLACEHOLDERS)}；写出其他双花括号标记会导致候选无法执行。"
            "输出合法JSON，必须包含 summary、diagnosis、prompt_changes、"
            "candidate_system_prompt、candidate_user_prompt、change_note、validation_focus。"
        )
        synthesis_input = json.dumps(
            {
                "base_prompt": {
                    "stage": prompt.stage,
                    "version": prompt.version,
                    "system_prompt": prompt.system_prompt,
                    "user_prompt": prompt.user_prompt,
                },
                "sample_policy": {
                    "eligible_count": eligible_count,
                    "analysis_count": len(selected),
                    "corrected_count": corrected_count,
                    "control_count": len(selected) - corrected_count,
                    "blind_holdout_count": len(holdout_ids),
                    "blind_holdout_asset_ids": holdout_ids,
                    "sample_items": sample_items,
                    "regression_roles": [
                        "target_error",
                        "stable_control",
                        "blind_holdout",
                    ],
                    "dimension_schema": dimension_schema,
                    "note": "盲测样本没有发送给提示词生成模型，后续用于主模型回测。",
                },
                "batch_diagnoses": [diagnostic_result],
            },
            ensure_ascii=False,
        )
        active_stage = "synthesis"
        run.progress = 76
        run.synthesis_audit_json = _stage_audit_json(
            status="running",
            attempt_count=0,
            output_budget=SYNTHESIS_OUTPUT_BUDGET,
            reasoning_effort=OPTIMIZER_REASONING_EFFORT,
        )
        db.commit()

        synthesis_started = time.perf_counter()
        synthesis_response: DoubaoResponse | None = None
        try:
            synthesis_response = await client.chat_json(
                synthesis_system,
                synthesis_input,
                max_attempts=1,
                output_budget=SYNTHESIS_OUTPUT_BUDGET,
                reasoning_effort=OPTIMIZER_REASONING_EFFORT,
                structured_output=True,
            )
            result = synthesis_response.parsed
            candidate_system = str(
                result.get("candidate_system_prompt") or ""
            ).strip()
            candidate_user = str(
                result.get("candidate_user_prompt") or ""
            ).strip()
            if not candidate_system or not candidate_user:
                raise ValueError(
                    "诊断模型没有生成完整的候选 System Prompt 和 User Prompt"
                )
            reject_unrunnable_candidate_prompt(
                candidate_user, source="诊断模型生成的候选 User Prompt"
            )
        except Exception as exc:
            context_source: DoubaoResponse | Exception = (
                synthesis_response if synthesis_response is not None else exc
            )
            status_code, request_id, attempt_count = _response_context(
                context_source
            )
            error_type, error_message = _normalized_error(exc)
            run.synthesis_audit_json = _stage_audit_json(
                status="failed",
                attempt_count=attempt_count,
                upstream_status_code=status_code,
                request_correlation_id=request_id,
                elapsed_ms=_elapsed_ms(synthesis_started),
                error_type=error_type,
                error_message=error_message,
                output_budget=SYNTHESIS_OUTPUT_BUDGET,
                reasoning_effort=OPTIMIZER_REASONING_EFFORT,
            )
            run.status = "failed"
            run.candidate_system_prompt = ""
            run.candidate_user_prompt = ""
            run.change_note = ""
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        run.diagnosis_json = json.dumps(
            {
                **result,
                "diagnostic": diagnostic_result,
                "batch_diagnoses": [diagnostic_result],
                "sample_policy": sample_policy,
            },
            ensure_ascii=False,
        )
        run.candidate_system_prompt = candidate_system
        run.candidate_user_prompt = candidate_user
        run.change_note = str(result.get("change_note") or "诊断模型根据人工校验样本生成的候选提示词")
        status_code, request_id, attempt_count = _response_context(
            synthesis_response
        )
        run.synthesis_audit_json = _stage_audit_json(
            status="succeeded",
            attempt_count=attempt_count,
            upstream_status_code=status_code,
            request_correlation_id=request_id,
            elapsed_ms=_elapsed_ms(synthesis_started),
            output_budget=synthesis_response.output_budget
            or SYNTHESIS_OUTPUT_BUDGET,
            reasoning_effort=synthesis_response.reasoning_effort
            or OPTIMIZER_REASONING_EFFORT,
        )
        run.status = "completed"
        run.progress = 100
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(PromptOptimizationRun, run_id)
        if run:
            error_type, error_message = _normalized_error(exc)
            audit_field = (
                "synthesis_audit_json"
                if active_stage == "synthesis"
                else "diagnostic_audit_json"
            )
            setattr(
                run,
                audit_field,
                _stage_audit_json(
                    status="failed",
                    attempt_count=0,
                    error_type=error_type,
                    error_message=error_message,
                    output_budget=(
                        SYNTHESIS_OUTPUT_BUDGET
                        if active_stage == "synthesis"
                        else DIAGNOSTIC_OUTPUT_BUDGET
                    ),
                    reasoning_effort=OPTIMIZER_REASONING_EFFORT,
                ),
            )
            run.status = "failed"
            run.candidate_system_prompt = ""
            run.candidate_user_prompt = ""
            run.change_note = ""
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
