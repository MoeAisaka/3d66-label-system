from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from .config import get_settings
from .database import init_database, session_scope
from .doubao import DoubaoClient
from .models import (
    Asset,
    EvaluationControl,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    PromptVersion,
)
from .scoring import ENGINE_VERSION, calculate_score
from .schema_adapter import (
    adapt_combined_aesthetic_response,
    is_combined_aesthetic_response,
    normalize_precheck_business_rules,
)
from .regression import complete_regression_item, fail_regression_item
from .risk_review import (
    RISK_REVIEW_SYSTEM_PROMPT,
    RISK_REVIEW_VERSION,
    apply_risk_review,
    build_risk_review_user_prompt,
    risk_review_reasons,
)
from .seed import seed_defaults


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


def claim_next_job() -> int | None:
    with session_scope() as db:
        control = db.get(EvaluationControl, 1)
        if control is not None and control.paused:
            return None
        configured_model = db.scalar(
            select(ModelConfig.id)
            .where(
                ModelConfig.active.is_(True),
                ModelConfig.encrypted_api_key.is_not(None),
            )
            .limit(1)
        )
        if configured_model is None:
            return None
        job_id = db.scalar(
            select(EvaluationJob.id)
            .where(EvaluationJob.status == "queued")
            .order_by(EvaluationJob.created_at.asc())
            .limit(1)
        )
        if job_id is None:
            return None
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
        return job_id if result.rowcount == 1 else None


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


async def evaluate_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(EvaluationJob, job_id)
        if not job:
            raise RuntimeError("任务不存在")
        asset = db.get(Asset, job.asset_id)
        model_config = db.scalar(
            select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id.asc())
        )
        if not asset or not model_config:
            raise RuntimeError("图片或模型配置不存在")
        prompt_a_id = job.prompt_a_id
        prompt_b_id = job.prompt_b_id

    single_mode = prompt_b_id is None
    prompt_a = (
        _single_prompt_for_job(prompt_a_id)
        if single_mode
        else _prompt_for_job("A", prompt_a_id)
    )
    prompt_b = None
    image_path = settings.upload_dir / asset.stored_name
    if not image_path.exists():
        raise RuntimeError("原始图片文件不存在")

    metadata = {
        "width": asset.width,
        "height": asset.height,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
    }
    user_a = prompt_a.user_prompt.replace(
        "{{image_metadata}}", json.dumps(metadata, ensure_ascii=False)
    )
    if single_mode:
        _set_job(job_id, stage="single", progress=20)
    client = DoubaoClient(model_config)
    response_a = await client.chat_json(
        prompt_a.system_prompt, user_a, image_path=image_path, mime_type=asset.mime_type
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
    if not single_mode and not combined_response and scope_status in {"in_scope", "boundary"}:
        prompt_b = _prompt_for_job("B", prompt_b_id)
        _set_job(job_id, stage="aesthetic", progress=48)
        user_b = prompt_b.user_prompt.replace(
            "{{precheck_json}}", json.dumps(precheck, ensure_ascii=False)
        ).replace("{{rubric_version}}", prompt_b.rubric_version)
        response_b = await client.chat_json(
            prompt_b.system_prompt, user_b, image_path=image_path, mime_type=asset.mime_type
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
                image_path=image_path,
                mime_type=asset.mime_type,
            )
            response_b_attempts.append(response_b.raw_payload)
            _ensure_job_processing(job_id)
            aesthetic = response_b.parsed

    risk_review_report = None
    risk_review_raw = None
    preliminary_scoring = calculate_score(precheck, aesthetic)
    trigger_reasons = risk_review_reasons(precheck, aesthetic, preliminary_scoring)
    if model_config.high_risk_review_enabled and aesthetic and trigger_reasons:
        _set_job(job_id, stage="risk_review", progress=76)
        try:
            risk_response = await client.chat_json(
                RISK_REVIEW_SYSTEM_PROMPT,
                build_risk_review_user_prompt(precheck, aesthetic, preliminary_scoring),
                image_path=image_path,
                mime_type=asset.mime_type,
            )
            risk_review_raw = risk_response.raw_payload
            risk_review_report = apply_risk_review(precheck, aesthetic, risk_response.parsed)
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
    scoring = calculate_score(precheck, aesthetic)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        current_job = db.get(EvaluationJob, job_id)
        if current_job is None or current_job.status != "processing":
            raise JobInterrupted("任务已暂停或取消，忽略本次模型返回")
        result = EvaluationResult(
                asset_id=asset.id,
                job_id=job_id,
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
                model_id=model_config.model_id,
                prompt_a_version=prompt_a.version,
                prompt_b_version=prompt_b.version if response_b and prompt_b else None,
                risk_review_version=(
                    RISK_REVIEW_VERSION if risk_review_report else None
                ),
                rubric_version=prompt_b.rubric_version if prompt_b else prompt_a.rubric_version,
                engine_version=ENGINE_VERSION,
            )
        db.add(result)
        db.flush()
        db.execute(
            update(Asset).where(Asset.id == asset.id).values(status="evaluated")
        )
        db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.id == job_id)
            .values(status="completed", stage="done", progress=100, finished_at=now)
        )
        if current_job.regression_item_id:
            complete_regression_item(db, current_job.regression_item_id, result)


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
        logger.exception("评测任务 %s 失败", job_id)
        _set_job(
            job_id,
            status="failed",
            stage="error",
            error_message=str(exc)[:4000],
            finished_at=datetime.now(timezone.utc),
        )
        with session_scope() as db:
            failed_job = db.get(EvaluationJob, job_id)
            if failed_job and failed_job.regression_item_id:
                fail_regression_item(db, failed_job.regression_item_id, str(exc))
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
        if not worked:
            time.sleep(poll_seconds)
    logger.info("Worker 检测到主服务已退出，正在停止：%s", WORKER_ID)


if __name__ == "__main__":
    run_forever()
