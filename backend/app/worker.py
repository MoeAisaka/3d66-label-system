from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
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

    prompt_a = _prompt_for_job("A", prompt_a_id)
    prompt_b = _prompt_for_job("B", prompt_b_id)
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
    client = DoubaoClient(model_config)
    response_a = await client.chat_json(
        prompt_a.system_prompt, user_a, image_path=image_path, mime_type=asset.mime_type
    )
    _ensure_job_processing(job_id)
    precheck = response_a.parsed
    scope_status = (precheck.get("classification") or {}).get("scope_status")

    aesthetic = None
    response_b = None
    if scope_status in {"in_scope", "boundary"}:
        _set_job(job_id, stage="aesthetic", progress=48)
        user_b = prompt_b.user_prompt.replace(
            "{{precheck_json}}", json.dumps(precheck, ensure_ascii=False)
        ).replace("{{rubric_version}}", prompt_b.rubric_version)
        response_b = await client.chat_json(
            prompt_b.system_prompt, user_b, image_path=image_path, mime_type=asset.mime_type
        )
        _ensure_job_processing(job_id)
        aesthetic = response_b.parsed

    _set_job(job_id, stage="scoring", progress=86)
    _ensure_job_processing(job_id)
    scoring = calculate_score(precheck, aesthetic)
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        current_job = db.get(EvaluationJob, job_id)
        if current_job is None or current_job.status != "processing":
            raise JobInterrupted("任务已暂停或取消，忽略本次模型返回")
        db.add(
            EvaluationResult(
                asset_id=asset.id,
                job_id=job_id,
                precheck_json=json.dumps(precheck, ensure_ascii=False),
                aesthetic_json=json.dumps(aesthetic, ensure_ascii=False) if aesthetic else None,
                scoring_json=json.dumps(scoring, ensure_ascii=False),
                raw_response_a=json.dumps(response_a.raw_payload, ensure_ascii=False),
                raw_response_b=(
                    json.dumps(response_b.raw_payload, ensure_ascii=False) if response_b else None
                ),
                score=scoring.get("score"),
                level=scoring.get("level"),
                confidence=scoring.get("confidence"),
                needs_review=bool(scoring.get("needs_review")),
                model_id=model_config.model_id,
                prompt_a_version=prompt_a.version,
                prompt_b_version=prompt_b.version if response_b else None,
                rubric_version=prompt_b.rubric_version,
                engine_version=ENGINE_VERSION,
            )
        )
        db.execute(
            update(Asset).where(Asset.id == asset.id).values(status="evaluated")
        )
        db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.id == job_id)
            .values(status="completed", stage="done", progress=100, finished_at=now)
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
        logger.exception("评测任务 %s 失败", job_id)
        _set_job(
            job_id,
            status="failed",
            stage="error",
            error_message=str(exc)[:4000],
            finished_at=datetime.now(timezone.utc),
        )
    return True


def run_forever(poll_seconds: float = 1.5) -> None:
    init_database()
    with session_scope() as db:
        seed_defaults(db)
    logger.info("Worker 已启动：%s", WORKER_ID)
    while True:
        worked = asyncio.run(process_one())
        if not worked:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
