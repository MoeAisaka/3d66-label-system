from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .database import init_database, session_scope
from .doubao import DoubaoClient
from .loop_engine import (
    advance_loop_attempt,
    assert_safe_normalized_payload,
    normalize_base_evaluation_result,
    normalize_targeted_model_result,
    request_fingerprint,
    validate_result_scope,
)
from .media import prepare_model_image, prepare_pdf_model_input
from .models import (
    Asset,
    CircuitBreaker,
    EvaluationControl,
    EvaluationCategoryProfile,
    CATEGORY_PROFILE_DEFAULTS,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    LoopAttempt,
    LoopRun,
    PromptRegressionItem,
    PromptVersion,
    QueueSchedulerState,
    ReviewPanel,
    ReviewWorkflowPolicy,
    SamplingPolicy,
    StrategyBundle,
)
from .queue_scheduler import (
    QUEUE_CLASSES,
    DeterministicQueueScheduler,
    QueueJob,
    QueuePolicy,
    TechnicalFailure,
    bounded_retry_after_seconds,
    classify_technical_failure,
    record_breaker_failure,
    retry_delay_seconds,
)
from .optimization_automation import optimization_worker_tick
from .scoring import ENGINE_VERSION, calculate_score
from .schema_adapter import (
    adapt_combined_aesthetic_response,
    is_combined_aesthetic_response,
    normalize_precheck_business_rules,
)
from .dimension_schema_registry import (
    space_schema_definition_for_scoring_profile,
)
from .regression import (
    complete_paired_regression_item,
    complete_regression_item,
    fail_regression_item,
)
from .risk_review import (
    RISK_REVIEW_VERSION,
    apply_risk_review,
    build_risk_review_system_prompt,
    build_risk_review_user_prompt,
    risk_review_reasons,
)
from .seed import seed_defaults
from .strategy_bundle import (
    ROUTED_STRATEGY_SCHEMA_VERSION,
    STRATEGY_SCHEMA_VERSION,
    build_evaluation_strategy_snapshot,
    build_model_config_snapshot,
    build_strategy_snapshot,
    get_or_create_bundle,
    resolve_frozen_dimension_entry,
)
from .baseline_regression import complete_baseline_item, fail_baseline_item


settings = get_settings()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(settings.log_dir / "worker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("3d66.worker")
WORKER_ID = f"{socket.gethostname()}-{id(object())}"


class JobInterrupted(RuntimeError):
    """The operator paused or canceled a job while a model call was in flight."""


PDF_SUMMARY_SYSTEM_PROMPT = """你是 3d66 方案 PDF 的多模态前处理器。
结合提供的 PDF 页图接触表与抽取/OCR 文本，先形成独立于最终评分的事实摘要。
只输出 JSON 对象，字段固定为：document_type、summary、key_points、visual_findings、risks、confidence。
summary 是 1 至 1200 字的中文摘要；key_points、visual_findings、risks 均为字符串数组；
confidence 是 0 至 1 的数字。不得评分、不得输出 L1-L5，也不得臆造页图和文本中不存在的信息。"""


def _pdf_summary_user_prompt(document_context: dict[str, object]) -> str:
    return (
        "请总结这份 PDF 方案。页图以随请求附带的接触表为准。\n\n"
        "抽取/OCR 文本：\n"
        + str(document_context.get("text") or "")
    )


def _validated_pdf_summary(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RuntimeError("PDF 多模态总结返回结构无效")

    def text_field(key: str, *, required: bool, limit: int) -> str:
        value = payload.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("PDF 多模态总结返回结构无效")
        return value.strip()[:limit]

    def text_list(key: str) -> list[str]:
        value = payload.get(key)
        if not isinstance(value, list):
            raise RuntimeError("PDF 多模态总结返回结构无效")
        return [
            item.strip()[:500]
            for item in value[:12]
            if isinstance(item, str) and item.strip()
        ]

    confidence = payload.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= float(confidence) <= 1
    ):
        raise RuntimeError("PDF 多模态总结返回结构无效")
    return {
        "schema_version": "pdf-multimodal-summary-v1",
        "status": "completed",
        "document_type": text_field(
            "document_type",
            required=False,
            limit=120,
        ),
        "summary": text_field("summary", required=True, limit=1200),
        "key_points": text_list("key_points"),
        "visual_findings": text_list("visual_findings"),
        "risks": text_list("risks"),
        "confidence": float(confidence),
    }


def _category_prompt_context(
    *,
    category_key: str,
    preprocess_config: dict[str, object],
    document_context: dict[str, object] | None,
    pdf_summary: dict[str, object] | None,
) -> str:
    if category_key == "pdf_text":
        if document_context is None or pdf_summary is None:
            raise RuntimeError("PDF 前处理或多模态总结未完成")
        return (
            "\n\nPDF 类目冻结上下文：\n"
            + json.dumps(
                {
                    "document_text": document_context.get("text") or "",
                    "multimodal_summary": pdf_summary,
                },
                ensure_ascii=False,
            )
            + "\n请基于文档正文、页图与总结执行当前类目规则，不要把页眉页脚当成评测主体。"
        )
    if category_key == "material_image" and preprocess_config.get(
        "material_focus",
        True,
    ):
        return (
            "\n\n材质图类目专项规则：优先检查纹理尺度与连续性、反光和粗糙度是否真实、"
            "接缝/收口/拼接关系、工艺瑕疵、重复贴图与拉伸，以及图片是否足以支持材质判断。"
            "所有结论必须引用图中可见证据；看不清时明确降低置信度并要求人工复核。"
        )
    return ""


def _frozen_category_contract(
    job: EvaluationJob,
    asset: Asset,
) -> dict[str, object] | None:
    if not job.category_profile_snapshot_json:
        return None
    try:
        snapshot = json.loads(job.category_profile_snapshot_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("任务冻结类目配置损坏") from exc
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != "evaluation-category-profile-v1"
        or snapshot.get("category_key") != job.category_key
        or snapshot.get("prompt_a_id") != job.prompt_a_id
        or snapshot.get("prompt_b_id") != job.prompt_b_id
    ):
        raise RuntimeError("任务冻结类目配置与 Job 不一致")

    profile_id = snapshot.get("profile_id")
    model_config_id = snapshot.get("model_config_id")
    if (
        not isinstance(profile_id, int)
        or isinstance(profile_id, bool)
        or profile_id < 1
        or not isinstance(model_config_id, int)
        or isinstance(model_config_id, bool)
        or model_config_id < 1
    ):
        raise RuntimeError("任务冻结类目配置身份损坏")

    allowed_mime_types = snapshot.get("allowed_mime_types")
    if (
        not isinstance(allowed_mime_types, list)
        or not allowed_mime_types
        or any(
            not isinstance(item, str) or not item
            for item in allowed_mime_types
        )
        or asset.mime_type not in allowed_mime_types
    ):
        raise RuntimeError("任务冻结类目 MIME 合同损坏")
    if not isinstance(snapshot.get("preprocess_config"), dict):
        raise RuntimeError("任务冻结类目前处理配置损坏")
    rubric_version = snapshot.get("rubric_version")
    if not isinstance(rubric_version, str) or not rubric_version:
        raise RuntimeError("任务冻结类目 rubric 配置损坏")
    dimension_schema_key = snapshot.get("dimension_schema_key")
    dimension_schema_version = snapshot.get("dimension_schema_version")
    if (
        (dimension_schema_key is None) != (dimension_schema_version is None)
        or dimension_schema_key is not None
        and (
            not isinstance(dimension_schema_key, str)
            or not dimension_schema_key
            or not isinstance(dimension_schema_version, str)
            or not dimension_schema_version
        )
    ):
        raise RuntimeError("任务冻结类目维度配置损坏")

    frozen_model = snapshot.get("model_config")
    if not isinstance(frozen_model, dict):
        raise RuntimeError("任务冻结类目模型快照损坏")
    try:
        rebuilt_model = build_model_config_snapshot(SimpleNamespace(**frozen_model))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("任务冻结类目模型快照损坏") from exc
    if rebuilt_model != frozen_model:
        raise RuntimeError("任务冻结类目模型快照损坏")
    return snapshot


def aesthetic_grade_collapse(aesthetic: dict[str, object] | None) -> bool:
    """Return true when six or more of the eight aesthetic dimensions share one grade."""
    if not aesthetic:
        return False
    dimensions = aesthetic.get("dimensions")
    if not isinstance(dimensions, dict) or len(dimensions) != 8:
        return False
    grades = []
    for item in dimensions.values():
        if not isinstance(item, dict) or not isinstance(item.get("grade"), int):
            return False
        grades.append(item["grade"])
    return max(Counter(grades).values(), default=0) >= 6


def _prompt_for_job(stage: str, prompt_id: int | None) -> PromptVersion:
    with session_scope() as db:
        prompt = db.get(PromptVersion, prompt_id) if prompt_id else None
        if prompt is not None and prompt.stage != stage:
            prompt = None
        if prompt is None:
            prompt = db.scalar(
                select(PromptVersion)
                .where(PromptVersion.stage == stage, PromptVersion.status == "published")
                .order_by(PromptVersion.created_at.desc())
            )
        if not prompt:
            raise RuntimeError(f"没有已发布的调用 {stage} 提示词")
        return prompt


def _single_prompt_for_job(prompt_id: int | None) -> PromptVersion:
    with session_scope() as db:
        prompt = db.get(PromptVersion, prompt_id) if prompt_id else None
        if not prompt:
            raise RuntimeError("单提示词版本不存在")
        return prompt


def _is_job_breaker_open(
    job: EvaluationJob,
    open_breakers: set[tuple[str, str]],
) -> bool:
    if (
        job.strategy_bundle_id is not None
        and ("strategy", str(job.strategy_bundle_id)) in open_breakers
    ):
        return True
    return bool(
        job.batch_key
        and ("batch", job.batch_key) in open_breakers
    )


def claim_next_job() -> int | None:
    with session_scope() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        control = db.get(EvaluationControl, 1)
        if control is not None and control.paused:
            return None
        configured_model = db.scalar(
            select(ModelConfig)
            .where(
                ModelConfig.encrypted_api_key.is_not(None),
                ModelConfig.active.is_(True),
            )
            .order_by(ModelConfig.active.desc(), ModelConfig.id.asc())
            .limit(1)
        )
        if configured_model is None:
            return None
        now = datetime.now(timezone.utc)
        queued_jobs = db.scalars(
            select(EvaluationJob)
            .where(
                EvaluationJob.status == "queued",
                (
                    EvaluationJob.retry_after_at.is_(None)
                    | (EvaluationJob.retry_after_at <= now)
                ),
            )
            .order_by(EvaluationJob.created_at.asc(), EvaluationJob.id.asc())
        ).all()
        if not queued_jobs:
            return None
        open_breakers = set(
            db.execute(
                select(
                    CircuitBreaker.scope_type,
                    CircuitBreaker.scope_key,
                ).where(CircuitBreaker.state == "open")
            ).all()
        )
        eligible_jobs = [
            job
            for job in queued_jobs
            if not _is_job_breaker_open(job, open_breakers)
        ]
        if not eligible_jobs:
            return None
        running = {queue: 0 for queue in QUEUE_CLASSES}
        for queue_class, count in db.execute(
            select(
                EvaluationJob.queue_class,
                func.count(EvaluationJob.id),
            )
            .where(EvaluationJob.status == "processing")
            .group_by(EvaluationJob.queue_class)
        ):
            queue = queue_class or "production_batch"
            if queue in running:
                running[queue] = count
        policy = QueuePolicy(global_limit=configured_model.max_concurrency)
        scheduler_state = db.get(QueueSchedulerState, 1)
        if scheduler_state is None:
            scheduler_state = QueueSchedulerState(
                id=1,
                policy_version=policy.version,
                global_limit=policy.global_limit,
            )
            db.add(scheduler_state)
            db.flush()
        if (
            scheduler_state.policy_version != policy.version
            or scheduler_state.global_limit != policy.global_limit
        ):
            scheduler_state.policy_version = policy.version
            scheduler_state.global_limit = policy.global_limit
            scheduler_state.validation_deficit = 0
            scheduler_state.interactive_deficit = 0
            scheduler_state.production_batch_deficit = 0
            scheduler_state.canary_deficit = 0
            scheduler_state.recovery_deficit = 0
            scheduler_state.dispatch_count = 0
            scheduler_state.last_recovery_dispatch = None
        scheduler = DeterministicQueueScheduler(
            policy,
            deficits={
                "validation": scheduler_state.validation_deficit,
                "interactive": scheduler_state.interactive_deficit,
                "production_batch": scheduler_state.production_batch_deficit,
                "canary": scheduler_state.canary_deficit,
                "recovery": scheduler_state.recovery_deficit,
            },
            dispatch_count=scheduler_state.dispatch_count,
            last_recovery_dispatch=scheduler_state.last_recovery_dispatch,
        )
        selected = scheduler.choose_job(
            [
                QueueJob(
                    id=job.id,
                    queue_class=job.queue_class or "production_batch",
                    created_at=job.created_at,
                )
                for job in eligible_jobs
            ],
            running=running,
        )
        if selected is None:
            return None
        job_id = selected.id
        result = db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.id == job_id, EvaluationJob.status == "queued")
            .values(
                status="processing",
                stage="precheck",
                progress=5,
                worker_id=WORKER_ID,
                attempts=EvaluationJob.attempts + 1,
                started_at=datetime.now(timezone.utc),
                error_message="",
            )
        )
        if result.rowcount != 1:
            return None
        persisted = scheduler.export_state()
        deficits = persisted["deficits"]
        scheduler_state.validation_deficit = deficits["validation"]
        scheduler_state.interactive_deficit = deficits["interactive"]
        scheduler_state.production_batch_deficit = deficits[
            "production_batch"
        ]
        scheduler_state.canary_deficit = deficits["canary"]
        scheduler_state.recovery_deficit = deficits["recovery"]
        scheduler_state.dispatch_count = persisted["dispatch_count"]
        scheduler_state.last_recovery_dispatch = persisted[
            "last_recovery_dispatch"
        ]
        return job_id


def _set_job(job_id: int, **values: object) -> None:
    with session_scope() as db:
        db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.id == job_id, EvaluationJob.status == "processing")
            .values(**values)
        )


def _ensure_job_processing(job_id: int) -> None:
    with session_scope() as db:
        status = db.scalar(select(EvaluationJob.status).where(EvaluationJob.id == job_id))
        if status != "processing":
            raise JobInterrupted(f"任务已被操作员设为 {status or '不存在'}")


def _targeted_loop_user_prompt(
    attempt: LoopAttempt,
    *,
    metadata: dict[str, object],
) -> str:
    targets = json.loads(attempt.target_dimensions_json)
    previous = json.loads(attempt.input_evidence_json)
    evidence_field = (
        "arbitration_evidence"
        if attempt.business_round == 3
        else "evidence"
    )
    value_field = (
        "suggested_values"
        if attempt.business_round == 3
        else "dimension_values"
    )
    return (
        "这是冻结 Loop 的局部复判，不得返回完整分类、dimensions、"
        "classification、full_output、precheck、aesthetic 或 scoring。\n"
        f"业务轮次：{attempt.business_round}；kind：{attempt.kind}。\n"
        f"只允许目标维度：{json.dumps(targets, ensure_ascii=False)}。\n"
        f"图片元数据：{json.dumps(metadata, ensure_ascii=False)}。\n"
        "上一轮的规范化证据摘要："
        f"{json.dumps(previous, ensure_ascii=False)}。\n"
        "严格只返回 JSON 对象，且仅包含："
        f"{value_field}（键必须恰好等于目标维度）、"
        f"{evidence_field}（逐目标维度给出本轮新证据）、"
        "confidence_by_dimension（0到1），以及确有必要时的"
        "needs_human/review_required/force_human=true。"
    )


async def _evaluate_targeted_loop_job(
    *,
    job_id: int,
    job: EvaluationJob,
    attempt: LoopAttempt,
    client: DoubaoClient,
    prompt_a: PromptVersion,
    image_path: Path,
    asset: Asset,
    metadata: dict[str, object],
    model_mime_type: str | None = None,
) -> None:
    targets = json.loads(attempt.target_dimensions_json)
    response = await client.chat_json(
        prompt_a.system_prompt,
        _targeted_loop_user_prompt(attempt, metadata=metadata),
        image_path=image_path,
        mime_type=model_mime_type or asset.mime_type,
    )
    _ensure_job_processing(job_id)
    assert_safe_normalized_payload(response.parsed)
    validate_result_scope(
        business_round=attempt.business_round,
        target_dimensions=targets,
        normalized_result=response.parsed,
    )
    normalized = normalize_targeted_model_result(
        response.parsed,
        business_round=attempt.business_round,
        target_dimensions=targets,
    )
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        current_job = db.get(EvaluationJob, job_id)
        current_attempt = db.get(LoopAttempt, attempt.id)
        if (
            current_job is None
            or current_job.status != "processing"
            or current_attempt is None
        ):
            raise JobInterrupted("Loop job 已被暂停、取消或移除")
        loop_run = db.get(LoopRun, current_attempt.loop_run_id)
        if loop_run is None or loop_run.status != "waiting_result":
            raise JobInterrupted("Loop 已结束，忽略迟到模型结果")
        fingerprint = request_fingerprint(normalized)
        advance_loop_attempt(
            db,
            loop_run=loop_run,
            attempt=current_attempt,
            normalized_result=normalized,
            result_idempotency_key=(
                f"loop-job:{current_job.root_job_id or current_job.id}:"
                f"{current_job.technical_attempt}"
            ),
            result_fingerprint=fingerprint,
            technical_attempt=current_job.technical_attempt,
            next_queue_class=(
                current_job.origin_queue_class or "interactive"
            ),
        )
        current_job.status = "completed"
        current_job.stage = "done"
        current_job.progress = 100
        current_job.finished_at = now


async def evaluate_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(EvaluationJob, job_id)
        if not job:
            raise RuntimeError("任务不存在")
        asset = db.get(Asset, job.asset_id)
        if not asset:
            raise RuntimeError("图片不存在")
        category_profile_snapshot = _frozen_category_contract(job, asset)
        if category_profile_snapshot is not None:
            category_preprocess_config = category_profile_snapshot[
                "preprocess_config"
            ]
            category_model_config_id = category_profile_snapshot[
                "model_config_id"
            ]
            frozen_category_model = category_profile_snapshot["model_config"]
        else:
            category_profile = db.scalar(
                select(EvaluationCategoryProfile).where(
                    EvaluationCategoryProfile.category_key == job.category_key,
                )
            )
            category_model_config_id = None
        if category_profile_snapshot is None and category_profile is None:
            # Older direct callers created jobs before category profiles were
            # introduced. Materialize only the compatibility default; normal
            # production databases are seeded by migrations and can still
            # explicitly retire a profile.
            defaults = CATEGORY_PROFILE_DEFAULTS.get(job.category_key)
            if defaults is None:
                raise RuntimeError("评测类目配置不存在或已停用")
            category_profile = EvaluationCategoryProfile(
                category_key=job.category_key,
                display_name=defaults["display_name"],
                allowed_mime_types_json=defaults["allowed_mime_types_json"],
                preprocess_config_json=defaults["preprocess_config_json"],
                status="active",
                rubric_version="rubric-v2.1",
                created_by="compatibility-default",
            )
            db.add(category_profile)
            db.flush()
        elif category_profile_snapshot is None and category_profile.status != "active":
            raise RuntimeError("评测类目配置不存在或已停用")
        if category_profile_snapshot is None:
            try:
                category_preprocess_config = json.loads(
                    category_profile.preprocess_config_json or "{}"
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError("评测类目前处理配置损坏") from exc
            category_model_config_id = category_profile.model_config_id
            frozen_category_model = None
        prompt_a_id = job.prompt_a_id
        prompt_b_id = job.prompt_b_id
        strategy_bundle_id = job.strategy_bundle_id
        loop_attempt = (
            db.get(LoopAttempt, job.loop_attempt_id)
            if job.loop_attempt_id is not None
            else None
        )
        frozen_bundle = (
            db.get(StrategyBundle, strategy_bundle_id)
            if strategy_bundle_id is not None
            else None
        )
        if strategy_bundle_id is not None and frozen_bundle is None:
            raise RuntimeError("任务绑定的 StrategyBundle 不存在")
        if (
            frozen_bundle is not None
            and frozen_bundle.strategy_schema_version
            == ROUTED_STRATEGY_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "strategy-bundle-v3 尚未接入生产 Worker；"
                "仅允许冻结合同与校准准备"
            )
        if frozen_bundle is not None:
            credential = db.scalar(
                select(ModelConfig)
                .where(
                    ModelConfig.model_id == frozen_bundle.model_id,
                    ModelConfig.encrypted_api_key.is_not(None),
                )
                .order_by(ModelConfig.id.asc())
                .limit(1)
            )
            if credential is None:
                raise RuntimeError("冻结模型缺少可用凭据")
            try:
                frozen_model = json.loads(
                    frozen_bundle.model_config_snapshot
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError("冻结模型配置损坏") from exc
            if (
                not isinstance(frozen_model, dict)
                or frozen_model.get("model_id") != frozen_bundle.model_id
            ):
                raise RuntimeError("冻结模型配置与 StrategyBundle 不一致")
            model_config = SimpleNamespace(
                **frozen_model,
                encrypted_api_key=credential.encrypted_api_key,
            )
            prompt_a = (
                db.get(PromptVersion, prompt_a_id)
                if prompt_a_id is not None
                else None
            )
            prompt_b = (
                db.get(PromptVersion, prompt_b_id)
                if prompt_b_id is not None
                else None
            )
            if (
                prompt_a is None
                or prompt_a.stage != "A"
                or prompt_a.version != frozen_bundle.prompt_a_version
                or (
                    frozen_bundle.prompt_b_version is None
                    and prompt_b is not None
                )
                or (
                    frozen_bundle.prompt_b_version is not None
                    and (
                        prompt_b is None
                        or prompt_b.stage != "B"
                        or prompt_b.version
                        != frozen_bundle.prompt_b_version
                    )
                )
            ):
                raise RuntimeError("任务冻结 Prompt 与 StrategyBundle 不一致")
            if frozen_bundle.engine_version != ENGINE_VERSION:
                raise RuntimeError("冻结评分引擎版本当前不可执行，拒绝漂移")
            sampling_policy = (
                None
                if frozen_bundle.sampling_policy_revision is None
                else db.scalar(
                    select(SamplingPolicy).where(
                        SamplingPolicy.revision
                        == frozen_bundle.sampling_policy_revision
                    )
                )
            )
            if (
                frozen_bundle.sampling_policy_revision is not None
                and sampling_policy is None
            ):
                raise RuntimeError("冻结抽样策略修订不存在")
            frozen_strategy_snapshot = build_strategy_snapshot(
                bundle=frozen_bundle,
                prompt_a=prompt_a,
                prompt_b=prompt_b,
                sampling_policy=sampling_policy,
            )
        else:
            if frozen_category_model is not None:
                credential = db.get(ModelConfig, category_model_config_id)
                if credential is None or credential.encrypted_api_key is None:
                    raise RuntimeError("任务冻结类目模型缺少可用凭据")
                model_config = SimpleNamespace(
                    **frozen_category_model,
                    encrypted_api_key=credential.encrypted_api_key,
                )
            else:
                model_statement = select(ModelConfig)
                if category_model_config_id is None:
                    model_statement = model_statement.where(
                        ModelConfig.active.is_(True)
                    )
                else:
                    model_statement = model_statement.where(
                        ModelConfig.id == category_model_config_id
                    )
                model_config = db.scalar(
                    model_statement.order_by(ModelConfig.id.asc())
                )
                if model_config is None:
                    raise RuntimeError("模型配置不存在")
            prompt_a = None
            prompt_b = None
            sampling_policy = None
            frozen_strategy_snapshot = None

    single_mode = prompt_b_id is None
    if prompt_a is None:
        prompt_a = (
            _single_prompt_for_job(prompt_a_id)
            if single_mode
            else _prompt_for_job("A", prompt_a_id)
        )
    if not single_mode and prompt_b is None:
        prompt_b = _prompt_for_job("B", prompt_b_id)
    if category_profile_snapshot is not None:
        frozen_rubric = category_profile_snapshot["rubric_version"]
        prompt_rubrics = {prompt_a.rubric_version}
        if prompt_b is not None:
            prompt_rubrics.add(prompt_b.rubric_version)
        if prompt_rubrics != {frozen_rubric}:
            raise RuntimeError("任务冻结类目 rubric 与 Prompt 不一致")
        if category_profile_snapshot.get("dimension_schema_key") is not None:
            raise RuntimeError(
                "任务冻结类目维度候选尚未通过生产校准门"
            )
    image_path = settings.upload_dir / asset.stored_name
    if not image_path.exists():
        raise RuntimeError("原始素材文件不存在")
    document_context: dict[str, object] | None = None
    if job.category_key == "pdf_text":
        pdf_input = prepare_pdf_model_input(
            image_path,
            content_sha256=asset.sha256,
            cache_dir=settings.upload_dir / ".derived" / "pdf",
            max_pages=int(category_preprocess_config.get("max_pages", 4)),
            max_text_chars=int(
                category_preprocess_config.get("max_text_chars", 24_000)
            ),
        )
        model_image_path = pdf_input.preview_path
        model_mime_type = pdf_input.preview_mime_type
        document_context = pdf_input.context
    else:
        model_image_path, model_mime_type = prepare_model_image(
            image_path,
            mime_type=asset.mime_type,
            content_sha256=asset.sha256,
            cache_dir=settings.upload_dir / ".derived" / "evaluation",
        )

    preprocess_snapshot: dict[str, object] = {
        "schema_version": "evaluation-preprocess-v1",
        "status": "completed",
        "category_key": job.category_key,
        "source_mime_type": asset.mime_type,
        "model_mime_type": model_mime_type,
        "config": category_preprocess_config,
    }
    if document_context is not None:
        document_text = str(document_context.get("text") or "")
        preprocess_snapshot["pdf"] = {
            key: value
            for key, value in document_context.items()
            if key != "text"
        }
        preprocess_snapshot["text_excerpt"] = document_text[:2_000]

    client_config = SimpleNamespace(
        encrypted_api_key=model_config.encrypted_api_key,
        provider=model_config.provider,
        base_url=model_config.base_url,
        api_path=model_config.api_path,
        model_id=model_config.model_id,
        temperature=model_config.temperature,
        max_tokens=model_config.max_tokens,
        timeout_seconds=model_config.timeout_seconds,
        max_retries=0,
        structured_output=model_config.structured_output,
    )
    # Provider calls do not retry in-place: recoverable failures are persisted
    # as recovery jobs so retry lineage, attempt count and Retry-After survive.
    client = DoubaoClient(client_config)  # type: ignore[arg-type]
    pdf_summary: dict[str, object] | None = None
    if document_context is not None:
        summary_enabled = category_preprocess_config.get(
            "multimodal_summary",
            True,
        )
        if not isinstance(summary_enabled, bool):
            raise RuntimeError("PDF 多模态总结配置损坏")
        if not summary_enabled:
            raise RuntimeError("PDF 类目必须启用多模态总结前置处理")
        _set_job(job_id, stage="pdf_summary", progress=12)
        summary_response = await client.chat_json(
            PDF_SUMMARY_SYSTEM_PROMPT,
            _pdf_summary_user_prompt(document_context),
            image_path=model_image_path,
            mime_type=model_mime_type,
        )
        _ensure_job_processing(job_id)
        pdf_summary = _validated_pdf_summary(summary_response.parsed)
        pdf_summary["model_id"] = model_config.model_id
        preprocess_snapshot["multimodal_summary"] = pdf_summary

    metadata = {
        "width": asset.width,
        "height": asset.height,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "category_key": job.category_key,
    }
    category_prompt_context = _category_prompt_context(
        category_key=job.category_key,
        preprocess_config=category_preprocess_config,
        document_context=document_context,
        pdf_summary=pdf_summary,
    )
    user_a = prompt_a.user_prompt.replace(
        "{{image_metadata}}", json.dumps(metadata, ensure_ascii=False)
    ) + category_prompt_context
    if single_mode:
        _set_job(job_id, stage="single", progress=20)
    if loop_attempt is not None and loop_attempt.business_round in (2, 3):
        _set_job(job_id, stage=loop_attempt.kind, progress=24)
        await _evaluate_targeted_loop_job(
            job_id=job_id,
            job=job,
            attempt=loop_attempt,
            client=client,
            prompt_a=prompt_a,
            image_path=model_image_path,
            asset=asset,
            metadata=metadata,
            model_mime_type=model_mime_type,
        )
        return
    response_a = await client.chat_json(
        prompt_a.system_prompt,
        user_a,
        image_path=model_image_path,
        mime_type=model_mime_type,
    )
    _ensure_job_processing(job_id)
    combined_response = is_combined_aesthetic_response(response_a.parsed)
    if single_mode and not combined_response:
        raise RuntimeError("单提示词必须一次返回分类、画质和八个美感维度的完整结构")
    if combined_response:
        precheck, aesthetic = adapt_combined_aesthetic_response(response_a.parsed)
    else:
        precheck = response_a.parsed
        aesthetic = None
    precheck = normalize_precheck_business_rules(precheck)
    scope_status = (precheck.get("classification") or {}).get("scope_status")

    response_b = None
    response_b_attempts: list[object] = []
    if not single_mode and not combined_response and scope_status == "in_scope":
        if prompt_b is None:
            prompt_b = _prompt_for_job("B", prompt_b_id)
        _set_job(job_id, stage="aesthetic", progress=48)
        user_b = prompt_b.user_prompt.replace(
            "{{precheck_json}}", json.dumps(precheck, ensure_ascii=False)
        ).replace("{{rubric_version}}", prompt_b.rubric_version)
        user_b += category_prompt_context
        response_b = await client.chat_json(
            prompt_b.system_prompt,
            user_b,
            image_path=model_image_path,
            mime_type=model_mime_type,
        )
        response_b_attempts.append(response_b.raw_payload)
        _ensure_job_processing(job_id)
        aesthetic = response_b.parsed
        if (
            prompt_b.version.endswith("split.3") or "lite" in prompt_b.version
        ) and aesthetic_grade_collapse(aesthetic):
            _set_job(job_id, stage="aesthetic_repair", progress=68)
            repair_user = (
                user_b
                + "\n\n上一次输出未通过系统校验：八个维度中至少六个等级相同，出现评分坍缩。"
                + "请重新查看图片，逐维对照优势与缺陷，至少形成两个有独立证据支持的等级档位。"
                + "不得为了制造差异而随意改分；每次升降都必须对应图片中的具体证据。"
                + "\n\n上一次输出：\n"
                + json.dumps(aesthetic, ensure_ascii=False)
            )
            response_b = await client.chat_json(
                prompt_b.system_prompt,
                repair_user,
                image_path=model_image_path,
                mime_type=model_mime_type,
            )
            response_b_attempts.append(response_b.raw_payload)
            _ensure_job_processing(job_id)
            aesthetic = response_b.parsed

    risk_review_report = None
    risk_review_raw = None
    dimension_definition = (
        resolve_frozen_dimension_entry(
            bundle=frozen_bundle,
            aesthetic=aesthetic,
        )["definition"]
        if frozen_bundle is not None
        and frozen_bundle.strategy_schema_version
        == STRATEGY_SCHEMA_VERSION
        else space_schema_definition_for_scoring_profile(
            aesthetic.get("scoring_profile")
            if isinstance(aesthetic, dict)
            else None
        )
    )
    preliminary_scoring = calculate_score(
        precheck,
        aesthetic,
        dimension_schema=dimension_definition,
    )
    trigger_reasons = risk_review_reasons(
        precheck,
        aesthetic,
        preliminary_scoring,
        dimension_schema=dimension_definition,
    )
    risk_review_enabled = bool(model_config.high_risk_review_enabled)
    if frozen_bundle is not None:
        risk_review_enabled = (
            risk_review_enabled
            and frozen_bundle.risk_review_version == RISK_REVIEW_VERSION
        )
    if risk_review_enabled and aesthetic and trigger_reasons:
        _set_job(job_id, stage="risk_review", progress=76)
        try:
            risk_response = await client.chat_json(
                build_risk_review_system_prompt(
                    dimension_definition
                ),
                build_risk_review_user_prompt(
                    precheck,
                    aesthetic,
                    preliminary_scoring,
                    dimension_schema=dimension_definition,
                ),
                image_path=model_image_path,
                mime_type=model_mime_type,
            )
            risk_review_raw = risk_response.raw_payload
            risk_review_report = apply_risk_review(
                precheck,
                aesthetic,
                risk_response.parsed,
                dimension_schema=dimension_definition,
            )
            risk_review_report["trigger_reasons"] = trigger_reasons
            precheck = normalize_precheck_business_rules(precheck)
        except Exception as exc:
            risk_review_report = {
                "version": RISK_REVIEW_VERSION,
                "triggered": True,
                "verdict": "error",
                "trigger_reasons": trigger_reasons,
                "reasons": [str(exc)[:500]],
                "corrections": [],
            }
            aesthetic["needs_review"] = True
            review_reasons = list(aesthetic.get("review_reasons") or [])
            review_reasons.append("高风险复核调用失败，需要人工确认")
            aesthetic["review_reasons"] = list(dict.fromkeys(review_reasons))

    _set_job(job_id, stage="scoring", progress=86)
    _ensure_job_processing(job_id)
    if frozen_bundle is not None and (
        frozen_bundle.strategy_schema_version
        == STRATEGY_SCHEMA_VERSION
    ):
        dimension_definition = resolve_frozen_dimension_entry(
            bundle=frozen_bundle,
            aesthetic=aesthetic,
        )["definition"]
    elif frozen_bundle is None:
        dimension_definition = (
            space_schema_definition_for_scoring_profile(
                aesthetic.get("scoring_profile")
                if isinstance(aesthetic, dict)
                else None
            )
        )
    scoring = calculate_score(
        precheck,
        aesthetic,
        dimension_schema=dimension_definition,
    )
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        current_job = db.get(EvaluationJob, job_id)
        if current_job is None or current_job.status != "processing":
            raise JobInterrupted("任务已暂停或取消，忽略本次模型返回")

        rubric_version = (
            prompt_b.rubric_version if prompt_b else prompt_a.rubric_version
        )
        if frozen_bundle is not None:
            bundle = db.get(StrategyBundle, frozen_bundle.id)
            if bundle is None:
                raise RuntimeError("任务冻结 StrategyBundle 已不存在")
            if frozen_strategy_snapshot is None:
                raise RuntimeError("任务冻结策略快照缺失")
            rubric_version = bundle.rubric_version
        else:
            sampling_policy = db.get(SamplingPolicy, 1)
            risk_review_version_used = (
                RISK_REVIEW_VERSION if risk_review_report else None
            )
            bundle = get_or_create_bundle(
                db=db,
                model_config=model_config,
                prompt_a=prompt_a,
                prompt_b=prompt_b,
                rubric_version=rubric_version,
                engine_version=ENGINE_VERSION,
                risk_review_version=risk_review_version_used,
                sampling_policy=sampling_policy,
            )
        strategy_snapshot = build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=sampling_policy,
            aesthetic=aesthetic,
        )

        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job_id,
            strategy_bundle_id=bundle.id,
            strategy_snapshot_json=strategy_snapshot,
            preprocess_json=json.dumps(
                preprocess_snapshot, ensure_ascii=False, sort_keys=True
            ),
            precheck_json=json.dumps(precheck, ensure_ascii=False),
            aesthetic_json=json.dumps(aesthetic, ensure_ascii=False) if aesthetic else None,
            scoring_json=json.dumps(scoring, ensure_ascii=False),
            raw_response_a=json.dumps(response_a.raw_payload, ensure_ascii=False),
            raw_response_b=(
                json.dumps(
                    response_b_attempts[0]
                    if len(response_b_attempts) == 1
                    else {"attempts": response_b_attempts},
                    ensure_ascii=False,
                )
                if response_b
                else None
            ),
            raw_response_risk_review=(
                json.dumps(risk_review_raw, ensure_ascii=False) if risk_review_raw else None
            ),
            risk_review_json=(
                json.dumps(risk_review_report, ensure_ascii=False)
                if risk_review_report
                else None
            ),
            score=scoring.get("score"),
            level=scoring.get("level"),
            confidence=scoring.get("confidence"),
            needs_review=bool(scoring.get("needs_review")),
            model_id=bundle.model_id,
            prompt_a_version=prompt_a.version,
            prompt_b_version=prompt_b.version if response_b and prompt_b else None,
            risk_review_version=bundle.risk_review_version,
            rubric_version=rubric_version,
            engine_version=bundle.engine_version,
        )
        db.add(result)
        db.flush()
        low_confidence_threshold = (
            sampling_policy.low_confidence_threshold
            if sampling_policy is not None
            else 0.7
        )
        is_baseline_regression = (
            current_job.baseline_regression_item_id is not None
        )
        if (
            not is_baseline_regression
            and (
                result.confidence is None
                or result.confidence < low_confidence_threshold
            )
        ):
            review_policy = db.get(ReviewWorkflowPolicy, 1)
            db.add(
                ReviewPanel(
                    evaluation_id=result.id,
                    required_reviewers=(
                        review_policy.initial_reviewers
                        if review_policy is not None
                        else 1
                    ),
                    status="collecting",
                )
            )
        if not is_baseline_regression:
            db.execute(
                update(Asset)
                .where(Asset.id == asset.id)
                .values(status="evaluated")
            )
        db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.id == job_id)
            .values(status="completed", stage="done", progress=100, finished_at=now)
        )
        if current_job.loop_attempt_id:
            current_attempt = db.get(
                LoopAttempt, current_job.loop_attempt_id
            )
            loop_run = (
                db.get(LoopRun, current_attempt.loop_run_id)
                if current_attempt is not None
                else None
            )
            if current_attempt is None or loop_run is None:
                raise RuntimeError("LoopAttempt 关联已损坏")
            normalized_loop_result = normalize_base_evaluation_result(
                precheck=precheck,
                aesthetic=aesthetic,
                scoring=scoring,
            )
            advance_loop_attempt(
                db,
                loop_run=loop_run,
                attempt=current_attempt,
                normalized_result=normalized_loop_result,
                result_idempotency_key=(
                    f"loop-job:{current_job.root_job_id or current_job.id}:"
                    f"{current_job.technical_attempt}"
                ),
                result_fingerprint=request_fingerprint(
                    normalized_loop_result
                ),
                technical_attempt=current_job.technical_attempt,
                next_queue_class=(
                    current_job.origin_queue_class or "interactive"
                ),
            )
        if current_job.regression_item_id:
            regression_item = db.get(
                PromptRegressionItem,
                current_job.regression_item_id,
            )
            if (
                regression_item is not None
                and regression_item.run.regression_mode == "paired"
            ):
                baseline = db.scalar(
                    select(EvaluationResult)
                    .where(
                        EvaluationResult.asset_id == result.asset_id,
                        EvaluationResult.strategy_bundle_id
                        == regression_item.run.baseline_strategy_bundle_id,
                    )
                    .order_by(
                        EvaluationResult.created_at.desc(),
                        EvaluationResult.id.desc(),
                    )
                    .limit(1)
                )
                candidate = db.scalar(
                    select(EvaluationResult)
                    .where(
                        EvaluationResult.asset_id == result.asset_id,
                        EvaluationResult.strategy_bundle_id
                        == regression_item.run.candidate_strategy_bundle_id,
                    )
                    .order_by(
                        EvaluationResult.created_at.desc(),
                        EvaluationResult.id.desc(),
                    )
                    .limit(1)
                )
                if baseline is not None and candidate is not None:
                    complete_paired_regression_item(
                        db,
                        item=regression_item,
                        baseline=baseline,
                        candidate=candidate,
                    )
            else:
                complete_regression_item(
                    db, current_job.regression_item_id, result
                )
        if current_job.baseline_regression_item_id:
            complete_baseline_item(
                db,
                item_id=current_job.baseline_regression_item_id,
                result=result,
            )


def _technical_failure_from_exception(exc: BaseException) -> TechnicalFailure:
    response = getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(response, "status_code", None)
    retry_after_seconds: float | None = None
    if status_code == 429:
        headers = getattr(exc, "headers", None)
        if headers is None:
            headers = getattr(response, "headers", {})
        headers = headers or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value is not None:
            retry_after_seconds = bounded_retry_after_seconds(value)
            if retry_after_seconds is None:
                try:
                    retry_at = parsedate_to_datetime(str(value))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    date_delay = max(
                        0.0,
                        (retry_at - datetime.now(timezone.utc)).total_seconds(),
                    )
                    retry_after_seconds = bounded_retry_after_seconds(
                        date_delay
                    )
                except (TypeError, ValueError, OverflowError):
                    retry_after_seconds = None
    return classify_technical_failure(
        exc,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
    )


def _aware_or_none(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _record_job_breakers(
    db,
    job: EvaluationJob,
    failure: TechnicalFailure,
    now: datetime,
) -> bool:
    scopes: list[tuple[str, str]] = []
    if job.strategy_bundle_id is not None:
        scopes.append(("strategy", str(job.strategy_bundle_id)))
    if job.batch_key:
        scopes.append(("batch", job.batch_key))
    opened = False
    for scope_type, scope_key in scopes:
        breaker = db.scalar(
            select(CircuitBreaker).where(
                CircuitBreaker.scope_type == scope_type,
                CircuitBreaker.scope_key == scope_key,
            )
        )
        if breaker is None:
            breaker = CircuitBreaker(
                scope_type=scope_type,
                scope_key=scope_key,
            )
            db.add(breaker)
        decision = record_breaker_failure(
            state=breaker.state,
            failure_count=breaker.failure_count,
            window_started_at=_aware_or_none(
                breaker.window_started_at
            ),
            now=now,
            retryable=failure.retryable,
        )
        breaker.state = decision.state
        breaker.failure_count = decision.failure_count
        breaker.window_started_at = decision.window_started_at
        breaker.last_failure_at = now
        if decision.state == "open":
            breaker.opened_at = decision.opened_at
            breaker.cooldown_until = decision.cooldown_until
            breaker.reason = decision.reason
            opened = True
    return opened


def _handle_technical_failure(
    job_id: int,
    exc: BaseException,
) -> bool:
    """Persist a safe failure and return whether a recovery job was created."""
    failure = _technical_failure_from_exception(exc)
    now = datetime.now(timezone.utc)
    try:
        with session_scope() as db:
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            claimed = db.execute(
                update(EvaluationJob)
                .where(
                    EvaluationJob.id == job_id,
                    EvaluationJob.status == "processing",
                )
                .values(
                    status="failure_handling",
                    technical_error_type=failure.error_type,
                    error_message=f"technical:{failure.error_type}",
                    finished_at=now,
                )
            )
            if claimed.rowcount != 1:
                parent = db.get(EvaluationJob, job_id)
                if parent is None:
                    return False
                if parent.technical_attempt >= 2:
                    return False
                next_attempt = min(2, parent.technical_attempt + 1)
                root_id = parent.root_job_id or parent.id
                return (
                    db.scalar(
                        select(EvaluationJob.id).where(
                            EvaluationJob.root_job_id == root_id,
                            EvaluationJob.technical_attempt
                            == next_attempt,
                        )
                    )
                    is not None
                )
            parent = db.get(EvaluationJob, job_id)
            if parent is None:
                return False
            if parent.root_job_id is None:
                parent.root_job_id = parent.id
                db.flush()
            breaker_open = _record_job_breakers(
                db, parent, failure, now
            )
            can_retry = (
                failure.retryable
                and parent.technical_attempt < 2
                and not breaker_open
            )
            if can_retry:
                next_attempt = parent.technical_attempt + 1
                delay = retry_delay_seconds(
                    next_attempt,
                    jitter_key=parent.root_job_id or parent.id,
                    retry_after_seconds=failure.retry_after_seconds,
                )
                child = EvaluationJob(
                    asset_id=parent.asset_id,
                    category_key=parent.category_key,
                    category_profile_snapshot_json=(
                        parent.category_profile_snapshot_json
                    ),
                    prompt_a_id=parent.prompt_a_id,
                    prompt_b_id=parent.prompt_b_id,
                    regression_item_id=parent.regression_item_id,
                    baseline_regression_item_id=(
                        parent.baseline_regression_item_id
                    ),
                    strategy_bundle_id=parent.strategy_bundle_id,
                    loop_attempt_id=parent.loop_attempt_id,
                    parent_job_id=parent.id,
                    root_job_id=parent.root_job_id or parent.id,
                    queue_class="recovery",
                    origin_queue_class=(
                        parent.origin_queue_class
                        or parent.queue_class
                        or "production_batch"
                    ),
                    technical_attempt=next_attempt,
                    technical_error_type=failure.error_type,
                    retry_after_at=now + timedelta(seconds=delay),
                    batch_key=parent.batch_key,
                    status="queued",
                    stage="waiting_retry",
                )
                db.add(child)
                parent.status = "retrying"
                parent.stage = "technical_retry"
                db.flush()
                return True
            parent.status = "failed"
            parent.stage = (
                "error" if failure.retryable else "p0_error"
            )
            if parent.regression_item_id:
                fail_regression_item(
                    db,
                    parent.regression_item_id,
                    f"technical:{failure.error_type}",
                )
            if parent.baseline_regression_item_id:
                fail_baseline_item(
                    db,
                    item_id=parent.baseline_regression_item_id,
                    error_code=f"technical:{failure.error_type}",
                    job_id=parent.id,
                )
            return False
    except IntegrityError:
        with session_scope() as db:
            parent = db.get(EvaluationJob, job_id)
            if parent is None:
                return False
            if parent.technical_attempt >= 2:
                return False
            root_id = parent.root_job_id or parent.id
            return (
                db.scalar(
                    select(EvaluationJob.id).where(
                        EvaluationJob.root_job_id == root_id,
                        EvaluationJob.technical_attempt
                        == min(2, parent.technical_attempt + 1),
                    )
                )
                is not None
            )


async def process_one() -> bool:
    job_id = claim_next_job()
    if job_id is None:
        return False
    logger.info("开始评测任务 %s", job_id)
    try:
        await evaluate_job(job_id)
        logger.info("完成评测任务 %s", job_id)
    except JobInterrupted as exc:
        logger.info("评测任务 %s 已中断：%s", job_id, exc)
    except Exception as exc:
        failure = _technical_failure_from_exception(exc)
        logger.error(
            "评测任务 %s 技术失败：%s",
            job_id,
            failure.error_type,
        )
        _handle_technical_failure(job_id, exc)
    return True


def run_forever(
    poll_seconds: float = 1.5,
    should_continue: Callable[[], bool] | None = None,
) -> None:
    init_database()
    with session_scope() as db:
        seed_defaults(db)
    logger.info("Worker 已启动：%s", WORKER_ID)
    while should_continue is None or should_continue():
        worked = asyncio.run(process_one())
        automation = optimization_worker_tick(WORKER_ID)
        if automation["status"] not in {
            "disabled",
            "idle",
            "threshold_wait",
            "cooldown",
            "budget_blocked",
        }:
            logger.info("自动优化队列状态：%s", automation["status"])
        if not worked:
            time.sleep(poll_seconds)
    logger.info("Worker 检测到主服务已退出，正在停止：%s", WORKER_ID)


if __name__ == "__main__":
    run_forever()
