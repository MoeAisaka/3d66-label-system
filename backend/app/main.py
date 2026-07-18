from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal, get_db, init_database
from .doubao import DoubaoClient
from .migration import compare_results
from .models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    MigrationItem,
    MigrationRun,
    ModelConfig,
    PromptVersion,
    SessionToken,
    User,
)
from .security import (
    create_session_token,
    hash_session_token,
    protect_secret,
    verify_password,
)
from .seed import seed_defaults


settings = get_settings()
COOKIE_NAME = "3d66_session"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class ModelConfigUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=300)
    api_path: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=128, le=65536)
    timeout_seconds: int = Field(ge=10, le=600)
    max_retries: int = Field(ge=0, le=5)
    max_concurrency: int = Field(ge=1, le=10)
    structured_output: bool = True


class EnqueueRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)


class PromptCreateRequest(BaseModel):
    stage: str = Field(pattern="^[AB]$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    system_prompt: str = Field(min_length=20)
    user_prompt: str = Field(min_length=5)
    rubric_version: str = Field(min_length=1, max_length=40)
    change_note: str = ""
    source: str = "manual"


class PromptAiReviseRequest(BaseModel):
    prompt_id: int
    instruction: str = Field(min_length=4, max_length=2000)


class ReviewRequest(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(approved|corrected|rejected)$")
    corrected_level: str | None = Field(default=None, pattern="^L[1-5]$")
    note: str = Field(default="", max_length=2000)


class MigrationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    baseline_model_id: str = Field(min_length=1, max_length=200)
    sample_size: int = Field(default=200, ge=1, le=500)


class MigrationReviewRequest(BaseModel):
    verdict: str = Field(pattern="^(candidate_better|same|baseline_better)$")
    reviewer_name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=2000)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()
    yield


app = FastAPI(title="3d66 标签系统", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def current_user(
    session_cookie: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    if not session_cookie:
        raise HTTPException(status_code=401, detail="请先登录")
    token_hash = hash_session_token(session_cookie)
    session = db.scalar(select(SessionToken).where(SessionToken.token_hash == token_hash))
    if not session or _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="登录已过期")
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用")
    return user


def _asset_payload(asset: Asset, result: EvaluationResult | None = None) -> dict[str, Any]:
    return {
        "id": asset.id,
        "name": asset.original_name,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "status": asset.status,
        "created_at": asset.created_at,
        "image_url": f"/api/assets/{asset.id}/file",
        "evaluation": _result_payload(result) if result else None,
    }


def _result_payload(result: EvaluationResult | None) -> dict[str, Any] | None:
    if not result:
        return None
    return {
        "id": result.id,
        "asset_id": result.asset_id,
        "job_id": result.job_id,
        "precheck": json.loads(result.precheck_json),
        "aesthetic": json.loads(result.aesthetic_json) if result.aesthetic_json else None,
        "scoring": json.loads(result.scoring_json),
        "score": result.score,
        "level": result.level,
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "versions": {
            "model": result.model_id,
            "prompt_a": result.prompt_a_version,
            "prompt_b": result.prompt_b_version,
            "rubric": result.rubric_version,
            "engine": result.engine_version,
        },
        "created_at": result.created_at,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "3d66-label-system"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token, token_hash = create_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    db.add(SessionToken(token_hash=token_hash, user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_days * 24 * 3600,
        path="/",
    )
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if session_cookie:
        db.query(SessionToken).filter(
            SessionToken.token_hash == hash_session_token(session_cookie)
        ).delete()
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@app.get("/api/dashboard")
def dashboard(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset_count = db.scalar(select(func.count()).select_from(Asset)) or 0
    queued = db.scalar(
        select(func.count()).select_from(EvaluationJob).where(EvaluationJob.status == "queued")
    ) or 0
    processing = db.scalar(
        select(func.count()).select_from(EvaluationJob).where(EvaluationJob.status == "processing")
    ) or 0
    review_count = db.scalar(
        select(func.count()).select_from(EvaluationResult).where(EvaluationResult.needs_review.is_(True))
    ) or 0
    level_rows = db.execute(
        select(EvaluationResult.level, func.count(EvaluationResult.id))
        .where(EvaluationResult.level.is_not(None))
        .group_by(EvaluationResult.level)
    ).all()
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    return {
        "asset_count": asset_count,
        "queued": queued,
        "processing": processing,
        "needs_review": review_count,
        "levels": {level: count for level, count in level_rows},
        "model": {
            "name": model.name if model else "未配置",
            "model_id": model.model_id if model else "",
            "has_api_key": bool(model and model.encrypted_api_key),
        },
    }


@app.post("/api/assets/upload")
async def upload_assets(
    files: list[UploadFile] = File(...),
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if len(files) > 100:
        raise HTTPException(status_code=400, detail="单次最多上传 100 张图片")
    uploaded: list[dict[str, Any]] = []
    for upload in files:
        data = await upload.read()
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail=f"{upload.filename} 为空或超过 25MB")
        try:
            image = Image.open(io.BytesIO(data))
            image.verify()
            image = Image.open(io.BytesIO(data))
            width, height = image.size
            detected_mime = Image.MIME.get(image.format or "", upload.content_type or "")
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"{upload.filename} 不是有效图片") from exc
        mime_type = detected_mime or upload.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_MIME:
            raise HTTPException(status_code=400, detail=f"{upload.filename} 仅支持 JPG、PNG、WebP")
        digest = hashlib.sha256(data).hexdigest()
        existing = db.scalar(select(Asset).where(Asset.sha256 == digest).order_by(Asset.id.desc()))
        if existing:
            uploaded.append({**_asset_payload(existing), "duplicate": True})
            continue
        extension = mimetypes.guess_extension(mime_type) or Path(upload.filename or "image").suffix or ".jpg"
        stored_name = f"{uuid.uuid4().hex}{extension.lower()}"
        (settings.upload_dir / stored_name).write_bytes(data)
        asset = Asset(
            original_name=upload.filename or stored_name,
            stored_name=stored_name,
            mime_type=mime_type,
            size_bytes=len(data),
            width=width,
            height=height,
            sha256=digest,
        )
        db.add(asset)
        db.flush()
        uploaded.append({**_asset_payload(asset), "duplicate": False})
    db.commit()
    return {"items": uploaded}


@app.get("/api/assets")
def list_assets(
    limit: int = 100,
    offset: int = 0,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    assets = db.scalars(
        select(Asset).order_by(Asset.created_at.desc()).offset(max(0, offset)).limit(min(200, limit))
    ).all()
    items = []
    for asset in assets:
        result = db.scalar(
            select(EvaluationResult)
            .where(EvaluationResult.asset_id == asset.id)
            .order_by(EvaluationResult.created_at.desc())
            .limit(1)
        )
        items.append(_asset_payload(asset, result))
    total = db.scalar(select(func.count()).select_from(Asset)) or 0
    return {"items": items, "total": total}


@app.get("/api/assets/{asset_id}/file")
def asset_file(
    asset_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    path = settings.upload_dir / asset.stored_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片文件缺失")
    return FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)


@app.get("/api/assets/{asset_id}")
def asset_detail(
    asset_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    result = db.scalar(
        select(EvaluationResult)
        .where(EvaluationResult.asset_id == asset.id)
        .order_by(EvaluationResult.created_at.desc())
        .limit(1)
    )
    return _asset_payload(asset, result)


@app.post("/api/jobs/enqueue")
def enqueue_jobs(
    payload: EnqueueRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    assets = db.scalars(select(Asset).where(Asset.id.in_(payload.asset_ids))).all()
    if len(assets) != len(set(payload.asset_ids)):
        raise HTTPException(status_code=404, detail="部分图片不存在")
    jobs = []
    for asset in assets:
        job = EvaluationJob(asset_id=asset.id)
        asset.status = "queued"
        db.add(job)
        db.flush()
        jobs.append(job.id)
    db.commit()
    return {"job_ids": jobs}


@app.get("/api/jobs")
def list_jobs(
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    jobs = db.scalars(
        select(EvaluationJob).order_by(EvaluationJob.created_at.desc()).limit(min(limit, 200))
    ).all()
    return {
        "items": [
            {
                "id": job.id,
                "asset_id": job.asset_id,
                "asset_name": job.asset.original_name,
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "attempts": job.attempts,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ]
    }


@app.get("/api/model-config")
def get_model_config(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return {
        "id": config.id,
        "name": config.name,
        "provider": config.provider,
        "base_url": config.base_url,
        "api_path": config.api_path,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "max_concurrency": config.max_concurrency,
        "structured_output": config.structured_output,
        "has_api_key": bool(config.encrypted_api_key),
        "api_key_mask": "••••••••" if config.encrypted_api_key else "",
        "updated_at": config.updated_at,
    }


@app.put("/api/model-config")
def update_model_config(
    payload: ModelConfigUpdate,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not config:
        config = ModelConfig()
        db.add(config)
    for field in (
        "name",
        "base_url",
        "api_path",
        "model_id",
        "temperature",
        "max_tokens",
        "timeout_seconds",
        "max_retries",
        "max_concurrency",
        "structured_output",
    ):
        setattr(config, field, getattr(payload, field))
    if payload.api_key:
        config.encrypted_api_key = protect_secret(payload.api_key.strip())
    db.commit()
    return {"ok": True}


@app.post("/api/model-config/test")
async def test_model_config(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        text = await DoubaoClient(config).test_connection()
        return {"ok": True, "message": text or "连接成功"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/prompts")
def list_prompts(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    prompts = db.scalars(select(PromptVersion).order_by(PromptVersion.created_at.desc())).all()
    return {
        "items": [
            {
                "id": prompt.id,
                "stage": prompt.stage,
                "name": prompt.name,
                "version": prompt.version,
                "system_prompt": prompt.system_prompt,
                "user_prompt": prompt.user_prompt,
                "rubric_version": prompt.rubric_version,
                "status": prompt.status,
                "source": prompt.source,
                "change_note": prompt.change_note,
                "created_by": prompt.created_by,
                "created_at": prompt.created_at,
            }
            for prompt in prompts
        ]
    }


@app.post("/api/prompts")
def create_prompt(
    payload: PromptCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    exists = db.scalar(select(PromptVersion).where(PromptVersion.version == payload.version))
    if exists:
        raise HTTPException(status_code=409, detail="提示词版本号已存在")
    prompt = PromptVersion(**payload.model_dump(), status="draft", created_by=user.username)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return {"id": prompt.id}


@app.post("/api/prompts/{prompt_id}/publish")
def publish_prompt(
    prompt_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    prompt = db.get(PromptVersion, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    db.execute(
        update(PromptVersion)
        .where(PromptVersion.stage == prompt.stage, PromptVersion.status == "published")
        .values(status="archived")
    )
    prompt.status = "published"
    db.commit()
    return {"ok": True}


@app.post("/api/prompts/ai-revise")
async def ai_revise_prompt(
    payload: PromptAiReviseRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    prompt = db.get(PromptVersion, payload.prompt_id)
    config = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not prompt or not config:
        raise HTTPException(status_code=404, detail="提示词或模型配置不存在")
    system = (
        "你是提示词编辑助手。只输出合法JSON，字段为 system_prompt、user_prompt、change_note。"
        "保持原有业务规则和输出JSON结构，不得删除安全边界，不得直接发布。"
    )
    user_text = json.dumps(
        {
            "stage": prompt.stage,
            "current_system_prompt": prompt.system_prompt,
            "current_user_prompt": prompt.user_prompt,
            "revision_instruction": payload.instruction,
        },
        ensure_ascii=False,
    )
    try:
        response = await DoubaoClient(config).chat_json(system, user_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "system_prompt": str(response.parsed.get("system_prompt", "")),
        "user_prompt": str(response.parsed.get("user_prompt", "")),
        "change_note": str(response.parsed.get("change_note", "AI 修改草案")),
    }


@app.post("/api/evaluations/{evaluation_id}/review")
def create_review(
    evaluation_id: int,
    payload: ReviewRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    review = HumanReview(evaluation_id=evaluation_id, **payload.model_dump())
    if payload.decision == "approved":
        evaluation.needs_review = False
    db.add(review)
    db.commit()
    db.refresh(review)
    return {"id": review.id}


def _migration_summary(run: MigrationRun, items: list[MigrationItem]) -> dict[str, Any]:
    completed = sum(1 for item in items if item.candidate_result_id is not None)
    review_required = sum(1 for item in items if item.requires_review)
    reviewed = sum(1 for item in items if item.human_verdict is not None)
    verdicts = {
        verdict: sum(1 for item in items if item.human_verdict == verdict)
        for verdict in ("candidate_better", "same", "baseline_better")
    }
    auto_pass = sum(
        1 for item in items if item.candidate_result_id is not None and not item.requires_review
    )
    exact_rate = round(auto_pass / completed, 4) if completed else 0.0
    return {
        "id": run.id,
        "name": run.name,
        "baseline_model_id": run.baseline_model_id,
        "candidate_model_id": run.candidate_model_id,
        "sample_size": len(items),
        "status": run.status,
        "completed": completed,
        "pending": len(items) - completed,
        "review_required": review_required,
        "reviewed": reviewed,
        "auto_exact_rate": exact_rate,
        "verdicts": verdicts,
        "created_by": run.created_by,
        "created_at": run.created_at,
    }


def _refresh_migration(db: Session, run: MigrationRun) -> list[MigrationItem]:
    items = db.scalars(
        select(MigrationItem)
        .where(MigrationItem.run_id == run.id)
        .order_by(MigrationItem.id.asc())
    ).all()
    for item in items:
        if item.candidate_result_id is not None:
            continue
        candidate = db.scalar(
            select(EvaluationResult)
            .where(
                EvaluationResult.asset_id == item.asset_id,
                EvaluationResult.model_id == run.candidate_model_id,
                EvaluationResult.created_at >= run.created_at,
            )
            .order_by(EvaluationResult.created_at.desc())
            .limit(1)
        )
        if not candidate:
            continue
        baseline_payload = _result_payload(item.baseline_result) or {}
        candidate_payload = _result_payload(candidate) or {}
        base_comparison = compare_results(baseline_payload, candidate_payload)
        audit_sample = not base_comparison["requires_review"] and item.asset_id % 20 == run.id % 20
        comparison = compare_results(
            baseline_payload,
            candidate_payload,
            audit_sample=audit_sample,
        )
        item.candidate_result_id = candidate.id
        item.requires_review = comparison["requires_review"]
        item.status = "review_required" if item.requires_review else "auto_pass"
        item.comparison_reason = json.dumps(comparison, ensure_ascii=False)

    pending = any(item.candidate_result_id is None for item in items)
    unreviewed = any(item.requires_review and not item.human_verdict for item in items)
    regression = any(item.human_verdict == "baseline_better" for item in items)
    if pending:
        run.status = "running"
    elif unreviewed:
        run.status = "review"
    elif regression:
        run.status = "regressed"
    else:
        run.status = "accepted"
    db.commit()
    return items


@app.get("/api/migrations/context")
def migration_context(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    rows = db.execute(
        select(EvaluationResult.model_id, func.count(func.distinct(EvaluationResult.asset_id)))
        .group_by(EvaluationResult.model_id)
        .order_by(func.count(func.distinct(EvaluationResult.asset_id)).desc())
    ).all()
    return {
        "candidate": {
            "model_id": model.model_id if model else "",
            "name": model.name if model else "未配置",
            "has_api_key": bool(model and model.encrypted_api_key),
        },
        "baselines": [
            {"model_id": model_id, "asset_count": count} for model_id, count in rows
        ],
    }


@app.post("/api/migrations")
def create_migration(
    payload: MigrationCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    if not model:
        raise HTTPException(status_code=400, detail="请先配置候选模型")
    if model.model_id == payload.baseline_model_id:
        raise HTTPException(status_code=400, detail="旧模型与候选模型不能相同")

    all_results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.model_id == payload.baseline_model_id)
        .order_by(EvaluationResult.created_at.desc())
    ).all()
    latest_by_asset: dict[int, EvaluationResult] = {}
    for result in all_results:
        latest_by_asset.setdefault(result.asset_id, result)
    if not latest_by_asset:
        raise HTTPException(status_code=400, detail="没有找到旧模型的历史评测结果")

    buckets: dict[tuple[str, str], list[EvaluationResult]] = {}
    for result in latest_by_asset.values():
        try:
            precheck = json.loads(result.precheck_json)
        except json.JSONDecodeError:
            precheck = {}
        category = ((precheck.get("classification") or {}).get("primary_category") or "无法判断")
        key = (result.level or "无等级", category)
        buckets.setdefault(key, []).append(result)

    selected: list[EvaluationResult] = []
    ordered_buckets = [buckets[key] for key in sorted(buckets)]
    while len(selected) < min(payload.sample_size, len(latest_by_asset)):
        progressed = False
        for bucket in ordered_buckets:
            if bucket and len(selected) < payload.sample_size:
                selected.append(bucket.pop(0))
                progressed = True
        if not progressed:
            break

    run = MigrationRun(
        name=payload.name,
        baseline_model_id=payload.baseline_model_id,
        candidate_model_id=model.model_id,
        sample_size=len(selected),
        created_by=user.username,
    )
    db.add(run)
    db.flush()
    for baseline in selected:
        db.add(
            MigrationItem(
                run_id=run.id,
                asset_id=baseline.asset_id,
                baseline_result_id=baseline.id,
            )
        )
        db.add(EvaluationJob(asset_id=baseline.asset_id))
        asset = db.get(Asset, baseline.asset_id)
        if asset:
            asset.status = "queued"
    db.commit()
    return {"id": run.id}


@app.get("/api/migrations")
def list_migrations(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    runs = db.scalars(select(MigrationRun).order_by(MigrationRun.created_at.desc())).all()
    summaries = []
    for run in runs:
        items = _refresh_migration(db, run)
        summaries.append(_migration_summary(run, items))
    return {"items": summaries}


@app.get("/api/migrations/{run_id}")
def migration_detail(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(MigrationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="迁移评测不存在")
    items = _refresh_migration(db, run)
    return {
        "summary": _migration_summary(run, items),
        "items": [
            {
                "id": item.id,
                "asset_id": item.asset_id,
                "asset_name": item.asset.original_name,
                "image_url": f"/api/assets/{item.asset_id}/file",
                "status": item.status,
                "requires_review": item.requires_review,
                "comparison": (
                    json.loads(item.comparison_reason) if item.comparison_reason else None
                ),
                "human_verdict": item.human_verdict,
                "reviewer_name": item.reviewer_name,
                "review_note": item.review_note,
                "baseline": _result_payload(item.baseline_result),
                "candidate": _result_payload(item.candidate_result),
            }
            for item in items
        ],
    }


@app.post("/api/migrations/{run_id}/items/{item_id}/review")
def review_migration_item(
    run_id: int,
    item_id: int,
    payload: MigrationReviewRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.scalar(
        select(MigrationItem).where(MigrationItem.id == item_id, MigrationItem.run_id == run_id)
    )
    if not item or item.candidate_result_id is None:
        raise HTTPException(status_code=404, detail="迁移样本尚未生成候选结果")
    item.human_verdict = payload.verdict
    item.reviewer_name = payload.reviewer_name
    item.review_note = payload.note
    item.reviewed_at = datetime.now(timezone.utc)
    item.status = "reviewed"
    db.commit()
    run = db.get(MigrationRun, run_id)
    if run:
        _refresh_migration(db, run)
    return {"ok": True}


@app.get("/{full_path:path}")
def frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API 不存在")
    requested = settings.frontend_dist / full_path
    if full_path and requested.exists() and requested.is_file():
        return FileResponse(requested)
    index = settings.frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="前端尚未构建，请先运行前端开发服务")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
