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
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SkipValidation,
    WithJsonSchema,
    model_validator,
)
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal, get_db, init_database
from .doubao import DoubaoClient
from .migration import compare_results
from .models import (
    Asset,
    CircuitBreaker,
    EvaluationControl,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    MigrationItem,
    MigrationRun,
    ModelConfig,
    LoopAttempt,
    LoopRun,
    OptimizerConfig,
    PromptOptimizationRun,
    PromptRegressionItem,
    PromptRegressionRun,
    PromptVersion,
    QueueSchedulerState,
    SampleSet,
    SampleSetItem,
    SampleTruthRevision,
    SamplingPolicy,
    SessionToken,
    StrategyBundle,
    User,
)
from .loop_engine import (
    LoopContractError,
    ROUND_KIND,
    advance_loop_attempt,
    assert_safe_normalized_payload,
    canonical_json,
    enqueue_loop_evaluation_job,
    normalize_targeted_model_result,
    request_fingerprint,
    validate_result_scope,
    validate_submission_scope,
)
from .queue_scheduler import (
    QUEUE_CLASSES,
    DeterministicQueueScheduler,
    QueuePolicy,
)
from .security import (
    MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    SecretStorageError,
    create_session_token,
    hash_session_token,
    protect_secret,
    verify_password,
)
from .optimizer import run_prompt_optimization, stage_audit_payload
from .p0e_canary_api import build_canary_router
from .seed import seed_defaults
from .schema_adapter import repair_combined_aesthetic_results, rescore_stored_results
from .regression import (
    SAMPLE_ROLES,
    complete_paired_regression_item,
    paired_gate_policy,
    refresh_paired_regression_run,
    reviewed_truth_snapshot,
    truth_from_result,
)
from .review_sampling import build_review_sampling
from .scoring import calculate_corrected_score
from .strategy_bundle import (
    build_strategy_snapshot,
    safe_strategy_snapshot_payload,
)


settings = get_settings()
COOKIE_NAME = "3d66_session"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def _protected_api_key(
    value: SecretStr | None,
    *,
    account: str,
) -> str | None:
    if value is None:
        return None
    secret = value.get_secret_value().strip()
    if not secret:
        raise HTTPException(status_code=422, detail="API Key 不能为空")
    if len(secret) > 1000:
        raise HTTPException(status_code=422, detail="API Key 长度不能超过 1000 个字符")
    try:
        return protect_secret(secret, account=account)
    except SecretStorageError:
        raise HTTPException(status_code=500, detail="API Key 安全存储失败") from None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class ModelConfigUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=300)
    api_path: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(
        default=None,
        json_schema_extra={"maxLength": 1000},
    )
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=128, le=65536)
    timeout_seconds: int = Field(ge=10, le=600)
    max_retries: int = Field(ge=0, le=5)
    max_concurrency: int = Field(ge=1, le=10)
    structured_output: bool = True
    high_risk_review_enabled: bool = True


class OptimizerConfigUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=300)
    api_path: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(
        default=None,
        json_schema_extra={"maxLength": 1000},
    )
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=12000, ge=512, le=65536)
    timeout_seconds: int = Field(default=300, ge=10, le=900)
    max_retries: int = Field(default=1, ge=0, le=5)
    structured_output: bool = True


class SamplingPolicyUpdate(BaseModel):
    sample_rate: int = Field(ge=0, le=100)
    low_confidence_threshold: float = Field(ge=0, le=1)
    medium_confidence_threshold: float = Field(ge=0, le=1)
    cold_start_required_count: int = Field(ge=0, le=100)
    high_level_required_from: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def validate_confidence_thresholds(self) -> "SamplingPolicyUpdate":
        if self.medium_confidence_threshold < self.low_confidence_threshold:
            raise ValueError("中置信度上限不能低于低置信度阈值")
        return self


class EnqueueRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    prompt_id: int | None = Field(default=None, ge=1)
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    queue_class: Literal[
        "validation",
        "interactive",
        "production_batch",
        "canary",
    ] | None = None
    manual_recheck: bool = False

    @model_validator(mode="after")
    def validate_prompt_mode(self) -> "EnqueueRequest":
        if self.prompt_id and (self.prompt_a_id or self.prompt_b_id):
            raise ValueError("单提示词模式不能同时选择 A/B 提示词")
        if self.manual_recheck and len(self.asset_ids) != 1:
            raise ValueError("人工单图复判只能包含一张图片")
        if self.manual_recheck and self.queue_class not in (None, "interactive"):
            raise ValueError("人工单图复判固定进入 interactive")
        return self


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


class ReviewCorrection(BaseModel):
    target_type: str = Field(pattern="^dimension$")
    field_key: str = Field(min_length=1, max_length=80)
    model_value: int | str | None = None
    human_value: int | str | None = None
    reason_codes: list[str] = Field(min_length=1, max_length=8)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_changed_value(self) -> "ReviewCorrection":
        if self.target_type == "dimension":
            if not isinstance(self.human_value, int) or not 1 <= self.human_value <= 5:
                raise ValueError("维度纠错必须填写 1 至 5 的人工分数")
            if self.human_value == self.model_value:
                raise ValueError("人工维度分数必须与模型分数不同")
        return self


class ReviewRequest(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(approved|corrected|rejected)$")
    corrected_level: str | None = Field(default=None, pattern="^L[1-5]$")
    note: str = Field(default="", max_length=2000)
    corrections: list[ReviewCorrection] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_correction(self) -> "ReviewRequest":
        if self.decision == "corrected":
            if self.corrected_level is not None:
                raise ValueError("最终等级由维度纠正自动计算，不能手工指定")
            if not self.corrections:
                raise ValueError("修改结果时必须填写至少一个维度纠正")
        elif self.corrected_level is not None or self.corrections:
            raise ValueError("只有修改结果时才能填写修正等级或维度纠错")
        return self


class PromptOptimizationCreateRequest(BaseModel):
    prompt_id: int = Field(ge=1)
    sample_set_id: int = Field(ge=1)


class SampleSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    kind: str = Field(default="test", pattern="^(golden|test)$")


class SampleSetAddItemsRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=1000)
    expected_level: str | None = Field(default=None, pattern="^L[1-5]$")


class SampleSetItemUpdateRequest(BaseModel):
    expected_level: str | None = Field(default=None, pattern="^L[1-5]$")
    note: str = Field(default="", max_length=2000)
    truth: dict[str, Any] | None = None
    revision_reason: str = Field(default="", max_length=2000)


class SampleSetStatusRequest(BaseModel):
    status: str = Field(pattern="^(draft|locked)$")


class RegressionCreateRequest(BaseModel):
    sample_set_id: int | None = Field(default=None, ge=1)
    prompt_a_id: int | None = Field(default=None, ge=1)
    prompt_b_id: int | None = Field(default=None, ge=1)
    threshold: float = Field(default=0.9, ge=0.5, le=1.0)


class PairedRegressionSampleRequest(BaseModel):
    sample_item_id: int = Field(ge=1)
    role: str = Field(pattern="^(target_error|stable_control|blind_holdout)$")


class PairedRegressionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sample_set_id: int = Field(ge=1)
    baseline_strategy_bundle_id: int = Field(ge=1)
    candidate_strategy_bundle_id: int = Field(ge=1)
    samples: list[PairedRegressionSampleRequest] = Field(
        min_length=3, max_length=1000
    )
    metric_rules_version: str = Field(min_length=1, max_length=80)
    aesthetic_accuracy_max_drop: float = Field(ge=0, le=1)
    whole_image_accuracy_max_drop: float = Field(ge=0, le=1)
    level_consistency_max_drop: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_paired_contract(self) -> "PairedRegressionCreateRequest":
        if self.baseline_strategy_bundle_id == self.candidate_strategy_bundle_id:
            raise ValueError("基线与候选 StrategyBundle 必须不同")
        item_ids = [sample.sample_item_id for sample in self.samples]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("同一冻结样本不能重复")
        roles = {sample.role for sample in self.samples}
        if roles != set(SAMPLE_ROLES):
            raise ValueError(
                "配对回归必须同时包含 target_error、stable_control、blind_holdout"
            )
        return self


class PairedRegressionResultsRequest(BaseModel):
    baseline_evaluation_id: int = Field(ge=1)
    candidate_evaluation_id: int = Field(ge=1)


class PairedRegressionApprovalRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    reviewer_name: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=2000)


class MigrationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    baseline_model_id: str = Field(min_length=1, max_length=200)
    sample_size: int = Field(default=200, ge=1, le=500)
    sample_set_id: int | None = Field(default=None, ge=1)


class MigrationReviewRequest(BaseModel):
    verdict: str = Field(pattern="^(candidate_better|same|baseline_better)$")
    reviewer_name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=2000)


def _missing_loop_value() -> object:
    return _LOOP_MISSING


def _loop_create_schema(schema: dict[str, Any]) -> None:
    schema.pop("additionalProperties", None)
    schema["required"] = [
        "asset_id",
        "strategy_bundle_id",
        "idempotency_key",
    ]


def _loop_result_schema(schema: dict[str, Any]) -> None:
    schema.pop("additionalProperties", None)
    schema["required"] = [
        "idempotency_key",
        "strategy_bundle_id",
        "kind",
        "normalized_result",
    ]


_LOOP_MISSING = object()
LoopPositiveInteger = Annotated[
    SkipValidation[int],
    WithJsonSchema({"type": "integer", "minimum": 1.0}),
]
LoopIdempotencyKey = Annotated[
    SkipValidation[str],
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 8,
            "maxLength": 160,
        }
    ),
]
LoopEvidenceObject = Annotated[
    SkipValidation[dict[str, Any]],
    WithJsonSchema(
        {
            "type": "object",
            "additionalProperties": True,
        }
    ),
]
LoopNullableModelId = Annotated[
    SkipValidation[str | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "maxLength": 200},
                {"type": "null"},
            ]
        }
    ),
]
LoopNullablePromptVersion = Annotated[
    SkipValidation[str | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "maxLength": 40},
                {"type": "null"},
            ]
        }
    ),
]
LoopNullableSource = Annotated[
    SkipValidation[str | None],
    WithJsonSchema(
        {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["interactive", "validation"],
                },
                {"type": "null"},
            ]
        }
    ),
]
LoopResultKind = Annotated[
    SkipValidation[str],
    WithJsonSchema(
        {
            "type": "string",
            "enum": ["base", "targeted_recheck", "arbitration"],
        }
    ),
]
LoopTargetDimensions = Annotated[
    SkipValidation[list[str]],
    WithJsonSchema(
        {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 100,
        }
    ),
]
LoopConflicts = Annotated[
    SkipValidation[list[dict[str, Any]]],
    WithJsonSchema(
        {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
            },
            "maxItems": 100,
        }
    ),
]
LoopTechnicalAttempt = Annotated[
    SkipValidation[int],
    WithJsonSchema({"type": "integer", "const": 0}),
]
LoopNullableCost = Annotated[
    SkipValidation[float | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "minimum": 0.0},
                {"type": "null"},
            ]
        }
    ),
]
LoopNullableLatency = Annotated[
    SkipValidation[int | None],
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": 0.0},
                {"type": "null"},
            ]
        }
    ),
]


class LoopCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra=_loop_create_schema,
    )

    asset_id: LoopPositiveInteger = Field(
        default_factory=_missing_loop_value
    )
    strategy_bundle_id: LoopPositiveInteger = Field(
        default_factory=_missing_loop_value
    )
    idempotency_key: LoopIdempotencyKey = Field(
        default_factory=_missing_loop_value
    )
    input_evidence: LoopEvidenceObject = Field(default_factory=dict)
    model_id: LoopNullableModelId = None
    prompt_a_version: LoopNullablePromptVersion = None
    prompt_b_version: LoopNullablePromptVersion = None
    source: LoopNullableSource = None


class LoopResultRequest(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra=_loop_result_schema,
    )

    idempotency_key: LoopIdempotencyKey = Field(
        default_factory=_missing_loop_value
    )
    strategy_bundle_id: LoopPositiveInteger = Field(
        default_factory=_missing_loop_value
    )
    kind: LoopResultKind = Field(default_factory=_missing_loop_value)
    target_dimensions: LoopTargetDimensions = Field(default_factory=list)
    normalized_result: LoopEvidenceObject = Field(
        default_factory=_missing_loop_value
    )
    conflicts: LoopConflicts = Field(default_factory=list)
    technical_attempt: LoopTechnicalAttempt = 0
    cost: LoopNullableCost = None
    latency_ms: LoopNullableLatency = None
    model_id: LoopNullableModelId = None
    prompt_a_version: LoopNullablePromptVersion = None
    prompt_b_version: LoopNullablePromptVersion = None


class BreakerOpenRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=80)
    cooldown_seconds: int = Field(default=300, ge=1, le=86400)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    db = SessionLocal()
    try:
        seed_defaults(db)
        repair_combined_aesthetic_results(db)
        rescore_stored_results(db)
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


app.include_router(build_canary_router(current_user))


def _asset_payload(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "name": asset.original_name,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "created_at": asset.created_at,
        "image_url": f"/api/assets/{asset.id}/file",
    }


def _evaluation_asset_payload(
    result: EvaluationResult, sampling: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **_asset_payload(result.asset),
        "evaluation": _result_payload(result),
        "sampling": sampling or {},
    }


def _review_sampling_decisions(db: Session) -> dict[int, dict[str, Any]]:
    policy = db.get(SamplingPolicy, 1) or SamplingPolicy(id=1)
    sample_rate = policy.sample_rate if policy.sample_rate is not None else 10
    low_threshold = policy.low_confidence_threshold if policy.low_confidence_threshold is not None else 0.7
    medium_threshold = policy.medium_confidence_threshold if policy.medium_confidence_threshold is not None else 0.9
    cold_start_count = policy.cold_start_required_count if policy.cold_start_required_count is not None else 5
    high_level_from = policy.high_level_required_from if policy.high_level_required_from is not None else 4
    policy_revision = policy.revision if policy.revision is not None else 1
    all_results = db.scalars(
        select(EvaluationResult).order_by(
            EvaluationResult.created_at.asc(), EvaluationResult.id.asc()
        )
    ).all()
    golden_asset_ids = set(
        db.scalars(
            select(SampleSetItem.asset_id)
            .join(SampleSet, SampleSet.id == SampleSetItem.sample_set_id)
            .where(SampleSet.kind == "golden", SampleSet.status == "locked")
        ).all()
    )
    previous_level_by_asset: dict[int, str | None] = {}
    combination_counts: dict[tuple[str, str, str | None], int] = {}
    decisions: dict[int, dict[str, Any]] = {}
    for result in all_results:
        combination = (
            result.model_id,
            result.prompt_a_version,
            result.prompt_b_version,
        )
        combination_counts[combination] = combination_counts.get(combination, 0) + 1
        decisions[result.id] = build_review_sampling(
            result,
            is_golden=result.asset_id in golden_asset_ids,
            previous_level=previous_level_by_asset.get(result.asset_id),
            combination_index=combination_counts[combination],
            sample_rate=sample_rate,
            low_confidence_threshold=low_threshold,
            medium_confidence_threshold=medium_threshold,
            cold_start_required_count=cold_start_count,
            high_level_required_from=high_level_from,
            policy_version=f"smart-sampling-v1.1/policy-{policy_revision}",
        )
        previous_level_by_asset[result.asset_id] = result.level
    return decisions


def _result_payload(result: EvaluationResult | None) -> dict[str, Any] | None:
    if not result:
        return None
    latest_review = result.reviews[-1] if result.reviews else None
    final_level = (
        latest_review.corrected_level or result.level
        if latest_review and latest_review.decision == "corrected"
        else result.level
    )
    final_score = (
        latest_review.corrected_score
        if latest_review
        and latest_review.decision == "corrected"
        and latest_review.corrected_score is not None
        else result.score
    )
    single_prompt = result.job.prompt_b_id is None and result.prompt_b_version is None
    return {
        "id": result.id,
        "asset_id": result.asset_id,
        "job_id": result.job_id,
        "prompt_id": result.job.prompt_a_id if single_prompt else None,
        "prompt_a_id": result.job.prompt_a_id if not single_prompt else None,
        "prompt_b_id": result.job.prompt_b_id,
        "precheck": json.loads(result.precheck_json),
        "aesthetic": json.loads(result.aesthetic_json) if result.aesthetic_json else None,
        "scoring": json.loads(result.scoring_json),
        "score": result.score,
        "level": result.level,
        "final_level": final_level,
        "final_score": final_score,
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "human_review": (
            {
                "id": latest_review.id,
                "reviewer_name": latest_review.reviewer_name,
                "decision": latest_review.decision,
                "corrected_level": latest_review.corrected_level,
                "corrected_score": latest_review.corrected_score,
                "note": latest_review.note,
                "corrections": json.loads(latest_review.corrections_json or "[]"),
                "created_at": latest_review.created_at,
            }
            if latest_review
            else None
        ),
        "risk_review": (
            json.loads(result.risk_review_json) if result.risk_review_json else None
        ),
        "versions": {
            "model": result.model_id,
            "prompt": result.prompt_a_version if single_prompt else None,
            "prompt_a": result.prompt_a_version if not single_prompt else None,
            "prompt_b": result.prompt_b_version,
            "risk_review": result.risk_review_version,
            "rubric": result.rubric_version,
            "engine": result.engine_version,
        },
        "created_at": result.created_at,
        "updated_at": result.updated_at,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "3d66-label-system"}


def _assert_bundle_versions(
    bundle: StrategyBundle,
    *,
    model_id: str | None,
    prompt_a_version: str | None,
    prompt_b_version: str | None,
) -> None:
    supplied = {
        "model_id": model_id,
        "prompt_a_version": prompt_a_version,
        "prompt_b_version": prompt_b_version,
    }
    actual = {
        "model_id": bundle.model_id,
        "prompt_a_version": bundle.prompt_a_version,
        "prompt_b_version": bundle.prompt_b_version,
    }
    if any(
        value is not None and value != actual[field]
        for field, value in supplied.items()
    ):
        raise HTTPException(status_code=409, detail="Loop 内禁止模型或提示词策略漂移")


def _reject_invalid_loop_request() -> None:
    raise LoopContractError("Loop 请求不符合接口约束")


def _is_loop_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_optional_bounded_loop_string(
    value: object,
    *,
    max_length: int,
) -> bool:
    return value is None or (
        isinstance(value, str) and len(value) <= max_length
    )


def _assert_loop_request_is_safe(
    payload: LoopCreateRequest | LoopResultRequest,
) -> None:
    """Scan the untouched request before any type or contract validation."""
    request_data = payload.model_dump(warnings=False)
    assert_safe_normalized_payload(request_data)
    if payload.model_extra:
        _reject_invalid_loop_request()

    idempotency_key = request_data.get("idempotency_key")
    if not (
        isinstance(idempotency_key, str)
        and 8 <= len(idempotency_key) <= 160
    ):
        _reject_invalid_loop_request()
    if not (
        _is_loop_integer(request_data.get("strategy_bundle_id"))
        and request_data["strategy_bundle_id"] >= 1
    ):
        _reject_invalid_loop_request()
    for field, max_length in (
        ("model_id", 200),
        ("prompt_a_version", 40),
        ("prompt_b_version", 40),
    ):
        if not _is_optional_bounded_loop_string(
            request_data.get(field),
            max_length=max_length,
        ):
            _reject_invalid_loop_request()

    if isinstance(payload, LoopCreateRequest):
        if not (
            _is_loop_integer(request_data.get("asset_id"))
            and request_data["asset_id"] >= 1
        ):
            _reject_invalid_loop_request()
        if not isinstance(request_data.get("input_evidence"), dict):
            _reject_invalid_loop_request()
        source = request_data.get("source")
        if source is not None and (
            not isinstance(source, str)
            or source not in {"interactive", "validation"}
        ):
            _reject_invalid_loop_request()
        return

    kind = request_data.get("kind")
    if not isinstance(kind, str) or kind not in {
        "base",
        "targeted_recheck",
        "arbitration",
    }:
        _reject_invalid_loop_request()
    target_dimensions = request_data.get("target_dimensions")
    if not (
        isinstance(target_dimensions, list)
        and len(target_dimensions) <= 100
        and all(isinstance(item, str) for item in target_dimensions)
    ):
        _reject_invalid_loop_request()
    if not isinstance(request_data.get("normalized_result"), dict):
        _reject_invalid_loop_request()
    conflicts = request_data.get("conflicts")
    if not (
        isinstance(conflicts, list)
        and len(conflicts) <= 100
        and all(isinstance(item, dict) for item in conflicts)
    ):
        _reject_invalid_loop_request()
    technical_attempt = request_data.get("technical_attempt")
    if not (_is_loop_integer(technical_attempt) and technical_attempt == 0):
        _reject_invalid_loop_request()
    cost = request_data.get("cost")
    if not (
        cost is None
        or (
            isinstance(cost, (int, float))
            and not isinstance(cost, bool)
            and cost >= 0
        )
    ):
        _reject_invalid_loop_request()
    latency_ms = request_data.get("latency_ms")
    if not (
        latency_ms is None
        or (_is_loop_integer(latency_ms) and latency_ms >= 0)
    ):
        _reject_invalid_loop_request()


def _loop_attempt_payload(attempt: LoopAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "round": attempt.business_round,
        "kind": attempt.kind,
        "target_dimensions": json.loads(attempt.target_dimensions_json),
        "input_evidence": json.loads(attempt.input_evidence_json),
        "normalized_result": (
            json.loads(attempt.normalized_result_json)
            if attempt.normalized_result_json
            else None
        ),
        "conflicts": json.loads(attempt.conflict_json),
        "status": attempt.status,
        "technical_attempt": attempt.technical_attempt,
        "cost": attempt.cost,
        "latency_ms": attempt.latency_ms,
        "created_at": attempt.created_at,
        "completed_at": attempt.completed_at,
    }


def _loop_payload(loop_run: LoopRun) -> dict[str, Any]:
    return {
        "id": loop_run.id,
        "asset_id": loop_run.asset_id,
        "strategy_bundle_id": loop_run.strategy_bundle_id,
        "strategy": {
            "model_id": loop_run.strategy_bundle.model_id,
            "prompt_a_version": loop_run.strategy_bundle.prompt_a_version,
            "prompt_b_version": loop_run.strategy_bundle.prompt_b_version,
            "rubric_version": loop_run.strategy_bundle.rubric_version,
            "engine_version": loop_run.strategy_bundle.engine_version,
        },
        "status": loop_run.status,
        "current_round": loop_run.current_round,
        "decision": json.loads(loop_run.decision_json or "{}"),
        "attempts": [
            _loop_attempt_payload(attempt) for attempt in loop_run.attempts
        ],
        "created_at": loop_run.created_at,
        "updated_at": loop_run.updated_at,
        "completed_at": loop_run.completed_at,
    }


@app.post("/api/loops")
@app.post("/api/loop-runs")
def create_loop(
    payload: LoopCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        _assert_loop_request_is_safe(payload)
    except LoopContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fingerprint_payload = payload.model_dump(exclude={"idempotency_key"})
    fingerprint = request_fingerprint(fingerprint_payload)
    existing = db.scalar(
        select(LoopRun).where(
            LoopRun.idempotency_key == payload.idempotency_key
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="idempotency_key 已用于不同 Loop 请求",
            )
        return _loop_payload(existing)

    asset = db.get(Asset, payload.asset_id)
    bundle = db.get(StrategyBundle, payload.strategy_bundle_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    if bundle is None:
        raise HTTPException(status_code=404, detail="StrategyBundle 不存在")
    _assert_bundle_versions(
        bundle,
        model_id=payload.model_id,
        prompt_a_version=payload.prompt_a_version,
        prompt_b_version=payload.prompt_b_version,
    )

    loop_run = LoopRun(
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
        asset_id=asset.id,
        strategy_bundle_id=bundle.id,
        status="waiting_result",
        current_round=1,
        decision_json=canonical_json(
            {
                "status": "waiting_result",
                "machine_converged": False,
                "needs_human": False,
                "reason_codes": ["ROUND1_RESULT_REQUIRED"],
                "evidence": {},
                "next_round": 1,
                "next_kind": ROUND_KIND[1],
                "target_dimensions": [],
            }
        ),
        created_by=user.username,
    )
    loop_run.attempts.append(
        LoopAttempt(
            business_round=1,
            kind=ROUND_KIND[1],
            target_dimensions_json="[]",
            input_evidence_json=canonical_json(payload.input_evidence),
            status="waiting_result",
        )
    )
    db.add(loop_run)
    try:
        db.flush()
        evidence_source = str(
            payload.input_evidence.get("source")
            or payload.input_evidence.get("queue_class")
            or ""
        ).lower()
        queue_class = (
            "validation"
            if payload.source == "validation"
            or evidence_source in {
                "validation",
                "golden_regression",
                "paired_regression",
            }
            else "interactive"
        )
        enqueue_loop_evaluation_job(
            db,
            loop_run=loop_run,
            attempt=loop_run.attempts[0],
            queue_class=queue_class,
        )
        db.commit()
    except (IntegrityError, LoopContractError) as exc:
        db.rollback()
        existing = db.scalar(
            select(LoopRun).where(
                LoopRun.idempotency_key == payload.idempotency_key
            )
        )
        if isinstance(exc, LoopContractError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if existing is None or existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Loop 创建发生幂等冲突",
            )
        return _loop_payload(existing)
    db.refresh(loop_run)
    return _loop_payload(loop_run)


@app.get("/api/loops/{loop_id}")
@app.get("/api/loop-runs/{loop_id}")
def loop_detail(
    loop_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    loop_run = db.get(LoopRun, loop_id)
    if loop_run is None:
        raise HTTPException(status_code=404, detail="Loop 不存在")
    return _loop_payload(loop_run)


@app.post("/api/loops/{loop_id}/attempts/{business_round}/result")
@app.post("/api/loop-runs/{loop_id}/attempts/{business_round}/result")
def submit_loop_result(
    loop_id: int,
    business_round: int,
    payload: LoopResultRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        _assert_loop_request_is_safe(payload)
    except LoopContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    submitted_dimensions = [
        dimension.strip() for dimension in payload.target_dimensions
    ]
    if (
        any(not dimension for dimension in submitted_dimensions)
        or len(submitted_dimensions) != len(set(submitted_dimensions))
    ):
        raise HTTPException(
            status_code=409,
            detail="target_dimensions 不能包含空值或重复值",
        )
    if business_round not in ROUND_KIND:
        raise HTTPException(status_code=409, detail="最多三轮，禁止第四轮")
    loop_run = db.get(LoopRun, loop_id)
    if loop_run is None:
        raise HTTPException(status_code=404, detail="Loop 不存在")
    if payload.strategy_bundle_id != loop_run.strategy_bundle_id:
        raise HTTPException(status_code=409, detail="Loop 内禁止 StrategyBundle 漂移")
    _assert_bundle_versions(
        loop_run.strategy_bundle,
        model_id=payload.model_id,
        prompt_a_version=payload.prompt_a_version,
        prompt_b_version=payload.prompt_b_version,
    )
    attempt = db.scalar(
        select(LoopAttempt).where(
            LoopAttempt.loop_run_id == loop_run.id,
            LoopAttempt.business_round == business_round,
        )
    )
    content = payload.model_dump(exclude={"idempotency_key"})
    content["target_dimensions"] = submitted_dimensions
    fingerprint = request_fingerprint(content)
    if attempt is not None and attempt.status == "completed":
        if (
            attempt.result_idempotency_key == payload.idempotency_key
            and attempt.result_fingerprint == fingerprint
        ):
            return _loop_payload(loop_run)
        raise HTTPException(status_code=409, detail="完成的 LoopAttempt 不可变")
    if (
        attempt is None
        or loop_run.status != "waiting_result"
        or business_round != loop_run.current_round
    ):
        raise HTTPException(status_code=409, detail="禁止越序提交 Loop 轮次")

    expected_dimensions = json.loads(attempt.target_dimensions_json)
    try:
        validate_submission_scope(
            business_round=business_round,
            expected_kind=attempt.kind,
            expected_dimensions=expected_dimensions,
            submitted_kind=payload.kind,
            submitted_dimensions=submitted_dimensions,
        )
        validate_result_scope(
            business_round=business_round,
            target_dimensions=submitted_dimensions,
            normalized_result={
                **payload.normalized_result,
                **({"conflicts": payload.conflicts} if payload.conflicts else {}),
            },
        )
    except LoopContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    normalized_result = dict(payload.normalized_result)
    if business_round in (2, 3):
        normalized_result = normalize_targeted_model_result(
            normalized_result,
            business_round=business_round,
            target_dimensions=submitted_dimensions,
        )
    elif payload.conflicts:
        # Round 1 conflict declarations are conservative input only; later
        # rounds derive actual conflicts from adjacent server-normalized values.
        normalized_result["conflicts"] = payload.conflicts
    jobs = db.scalars(
        select(EvaluationJob)
        .where(EvaluationJob.loop_attempt_id == attempt.id)
        .order_by(
            EvaluationJob.technical_attempt.desc(),
            EvaluationJob.id.desc(),
        )
    ).all()
    if any(job.status == "processing" for job in jobs):
        raise HTTPException(
            status_code=409,
            detail="关联 EvaluationJob 正在执行，禁止人工结果覆盖",
        )
    if any(job.technical_attempt > 0 for job in jobs):
        raise HTTPException(
            status_code=409,
            detail="技术 recovery 链已启动，人工导入只能使用 attempt 0",
        )
    for job in jobs:
        if job.status == "queued":
            job.status = "canceled"
            job.stage = "manual_attach"
            job.finished_at = datetime.now(timezone.utc)
    try:
        advance_loop_attempt(
            db,
            loop_run=loop_run,
            attempt=attempt,
            normalized_result=normalized_result,
            result_idempotency_key=payload.idempotency_key,
            result_fingerprint=fingerprint,
            technical_attempt=0,
            cost=payload.cost,
            latency_ms=payload.latency_ms,
            next_queue_class=(
                jobs[0].origin_queue_class
                if jobs and jobs[0].origin_queue_class
                else "interactive"
            ),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent_attempt = db.scalar(
            select(LoopAttempt).where(
                LoopAttempt.loop_run_id == loop_id,
                LoopAttempt.business_round == business_round,
            )
        )
        concurrent_loop = db.get(LoopRun, loop_id)
        if (
            concurrent_attempt is not None
            and concurrent_loop is not None
            and concurrent_attempt.status == "completed"
            and concurrent_attempt.result_idempotency_key
            == payload.idempotency_key
            and concurrent_attempt.result_fingerprint == fingerprint
        ):
            return _loop_payload(concurrent_loop)
        raise HTTPException(
            status_code=409,
            detail="LoopAttempt 并发提交冲突",
        )
    except LoopContractError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(loop_run)
    return _loop_payload(loop_run)


def _breaker_payload(breaker: CircuitBreaker) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cooldown_until = breaker.cooldown_until
    if cooldown_until is not None and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    return {
        "id": breaker.id,
        "scope_type": breaker.scope_type,
        "scope_key": breaker.scope_key,
        "state": breaker.state,
        "failure_count": breaker.failure_count,
        "window_started_at": breaker.window_started_at,
        "last_failure_at": breaker.last_failure_at,
        "opened_at": breaker.opened_at,
        "cooldown_until": breaker.cooldown_until,
        "cooldown_elapsed": bool(
            cooldown_until is not None and cooldown_until <= now
        ),
        "reason": breaker.reason,
        "reset_by": breaker.reset_by,
        "reset_at": breaker.reset_at,
        "updated_at": breaker.updated_at,
    }


@app.get("/api/circuit-breakers")
def list_circuit_breakers(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    breakers = db.scalars(
        select(CircuitBreaker).order_by(
            CircuitBreaker.scope_type.asc(),
            CircuitBreaker.scope_key.asc(),
        )
    ).all()
    return {"items": [_breaker_payload(breaker) for breaker in breakers]}


@app.post("/api/circuit-breakers/{scope_type}/{scope_key}/open")
def open_circuit_breaker(
    scope_type: Literal["strategy", "batch"],
    scope_key: str,
    payload: BreakerOpenRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
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
    now = datetime.now(timezone.utc)
    breaker.state = "open"
    breaker.opened_at = now
    breaker.cooldown_until = now + timedelta(
        seconds=payload.cooldown_seconds
    )
    breaker.reason = payload.reason
    breaker.reset_by = None
    breaker.reset_at = None
    db.commit()
    db.refresh(breaker)
    return _breaker_payload(breaker)


@app.post("/api/circuit-breakers/{scope_type}/{scope_key}/reset")
def reset_circuit_breaker(
    scope_type: Literal["strategy", "batch"],
    scope_key: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    breaker = db.scalar(
        select(CircuitBreaker).where(
            CircuitBreaker.scope_type == scope_type,
            CircuitBreaker.scope_key == scope_key,
        )
    )
    if breaker is None:
        raise HTTPException(status_code=404, detail="Circuit breaker 不存在")
    breaker.state = "closed"
    breaker.failure_count = 0
    breaker.window_started_at = None
    breaker.last_failure_at = None
    breaker.opened_at = None
    breaker.cooldown_until = None
    breaker.reason = None
    breaker.reset_by = user.username
    breaker.reset_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(breaker)
    return _breaker_payload(breaker)


@app.get("/api/queues/status")
def queue_status(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    pending = {queue: 0 for queue in QUEUE_CLASSES}
    running = {queue: 0 for queue in QUEUE_CLASSES}
    dispatchable = {queue: 0 for queue in QUEUE_CLASSES}
    delayed = {queue: 0 for queue in QUEUE_CLASSES}
    rows = db.execute(
        select(
            EvaluationJob.queue_class,
            EvaluationJob.status,
            func.count(EvaluationJob.id),
        )
        .where(EvaluationJob.status.in_(("queued", "processing")))
        .group_by(EvaluationJob.queue_class, EvaluationJob.status)
    ).all()
    for queue_class, status, count in rows:
        queue = queue_class or "production_batch"
        if queue not in pending:
            continue
        if status == "queued":
            pending[queue] = count
        else:
            running[queue] = count
    blocked = {queue: 0 for queue in QUEUE_CLASSES}
    open_breakers = set(
        db.execute(
            select(
                CircuitBreaker.scope_type,
                CircuitBreaker.scope_key,
            ).where(CircuitBreaker.state == "open")
        ).all()
    )
    now = datetime.now(timezone.utc)
    for job in db.scalars(
        select(EvaluationJob).where(EvaluationJob.status == "queued")
    ):
        queue = job.queue_class or "production_batch"
        strategy_blocked = (
            job.strategy_bundle_id is not None
            and ("strategy", str(job.strategy_bundle_id)) in open_breakers
        )
        batch_blocked = bool(
            job.batch_key
            and ("batch", job.batch_key) in open_breakers
        )
        if queue not in blocked:
            continue
        breaker_blocked = strategy_blocked or batch_blocked
        retry_at = job.retry_after_at
        if retry_at is not None and retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        retry_delayed = retry_at is not None and retry_at > now
        if breaker_blocked:
            blocked[queue] += 1
        if retry_delayed:
            delayed[queue] += 1
        if not breaker_blocked and not retry_delayed:
            dispatchable[queue] += 1
    model = db.scalar(
        select(ModelConfig)
        .where(ModelConfig.active.is_(True))
        .order_by(ModelConfig.id.asc())
    )
    policy = QueuePolicy(
        global_limit=model.max_concurrency if model else 2
    )
    persisted = db.get(QueueSchedulerState, 1)
    scheduler = DeterministicQueueScheduler(
        policy,
        deficits=(
            {
                "validation": persisted.validation_deficit,
                "interactive": persisted.interactive_deficit,
                "production_batch": (
                    persisted.production_batch_deficit
                ),
                "canary": persisted.canary_deficit,
                "recovery": persisted.recovery_deficit,
            }
            if persisted is not None
            and persisted.policy_version == policy.version
            and persisted.global_limit == policy.global_limit
            else None
        ),
        dispatch_count=(
            persisted.dispatch_count
            if persisted is not None
            and persisted.policy_version == policy.version
            and persisted.global_limit == policy.global_limit
            else 0
        ),
        last_recovery_dispatch=(
            persisted.last_recovery_dispatch
            if persisted is not None
            and persisted.policy_version == policy.version
            and persisted.global_limit == policy.global_limit
            else None
        ),
    )
    snapshot = scheduler.snapshot(
        pending=dispatchable,
        running=running,
    )
    for item in snapshot["queues"]:
        queue = item["queue_class"]
        item["pending"] = pending[queue]
        item["pending_total"] = pending[queue]
        item["blocked_by_breaker"] = blocked[queue]
        item["delayed_by_retry_after"] = delayed[queue]
        item["dispatchable_pending"] = dispatchable[queue]
    return snapshot


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
    result_rows = db.scalars(
        select(EvaluationResult).order_by(EvaluationResult.created_at.desc())
    ).all()
    latest_results: dict[int, EvaluationResult] = {}
    for result in result_rows:
        latest_results.setdefault(result.asset_id, result)
    review_count = sum(1 for result in latest_results.values() if result.needs_review)
    levels: dict[str, int] = {}
    for result in latest_results.values():
        payload = _result_payload(result) or {}
        final_level = payload.get("final_level")
        if final_level:
            levels[final_level] = levels.get(final_level, 0) + 1
    model = db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)))
    return {
        "asset_count": asset_count,
        "queued": queued,
        "processing": processing,
        "needs_review": review_count,
        "levels": levels,
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
        select(Asset).order_by(Asset.created_at.desc()).offset(max(0, offset)).limit(min(1000, limit))
    ).all()
    total = db.scalar(select(func.count()).select_from(Asset)) or 0
    return {"items": [_asset_payload(asset) for asset in assets], "total": total}


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
    return _asset_payload(asset)


@app.get("/api/evaluations")
def list_evaluations(
    limit: int = 100,
    offset: int = 0,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    results = db.scalars(
        select(EvaluationResult)
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .offset(max(0, offset))
        .limit(min(1000, limit))
    ).all()
    sampling = _review_sampling_decisions(db)
    total = db.scalar(select(func.count()).select_from(EvaluationResult)) or 0
    return {
        "items": [
            _evaluation_asset_payload(result, sampling.get(result.id)) for result in results
        ],
        "total": total,
    }


@app.get("/api/evaluations/{evaluation_id}")
def evaluation_detail(
    evaluation_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = db.get(EvaluationResult, evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="评测结果不存在")
    sampling = _review_sampling_decisions(db)
    return _evaluation_asset_payload(result, sampling.get(result.id))


@app.post("/api/jobs/enqueue")
def enqueue_jobs(
    payload: EnqueueRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    assets = db.scalars(select(Asset).where(Asset.id.in_(payload.asset_ids))).all()
    if len(assets) != len(set(payload.asset_ids)):
        raise HTTPException(status_code=404, detail="部分图片不存在")

    def selected_prompt(stage: str, prompt_id: int | None) -> PromptVersion:
        if prompt_id is not None:
            prompt = db.get(PromptVersion, prompt_id)
            if not prompt or prompt.stage != stage:
                raise HTTPException(status_code=400, detail=f"提示词 {stage} 版本无效")
            return prompt
        prompt = db.scalar(
            select(PromptVersion)
            .where(PromptVersion.stage == stage, PromptVersion.status == "published")
            .order_by(PromptVersion.created_at.desc())
            .limit(1)
        )
        if not prompt:
            raise HTTPException(status_code=400, detail=f"没有可用的提示词 {stage} 发布版本")
        return prompt

    single_prompt = db.get(PromptVersion, payload.prompt_id) if payload.prompt_id else None
    if payload.prompt_id and not single_prompt:
        raise HTTPException(status_code=400, detail="单提示词版本无效")
    prompt_a = None if single_prompt else selected_prompt("A", payload.prompt_a_id)
    prompt_b = None if single_prompt else selected_prompt("B", payload.prompt_b_id)
    jobs = []
    queue_class = (
        "interactive"
        if payload.manual_recheck
        else payload.queue_class or "production_batch"
    )
    batch_key = f"enqueue:{uuid.uuid4().hex}"
    for asset in assets:
        job = EvaluationJob(
            asset_id=asset.id,
            prompt_a_id=single_prompt.id if single_prompt else prompt_a.id,
            prompt_b_id=None if single_prompt else prompt_b.id,
            queue_class=queue_class,
            origin_queue_class=queue_class,
            batch_key=batch_key,
        )
        asset.status = "queued"
        db.add(job)
        db.flush()
        jobs.append(job.id)
    db.commit()
    return {
        "job_ids": jobs,
        "batch_key": batch_key,
        "queue_class": queue_class,
    }


@app.get("/api/jobs")
def list_jobs(
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    jobs = db.scalars(
        select(EvaluationJob).order_by(EvaluationJob.created_at.desc()).limit(min(limit, 200))
    ).all()
    result_versions = {
        result.job_id: result
        for result in db.scalars(
            select(EvaluationResult).where(EvaluationResult.job_id.in_([job.id for job in jobs]))
        ).all()
    } if jobs else {}
    return {
        "items": [
            {
                "id": job.id,
                "asset_id": job.asset_id,
                "asset_name": job.asset.original_name,
                "prompt_a_version": (
                    job.prompt_a.version if job.prompt_a and job.prompt_b else
                    result_versions[job.id].prompt_a_version if job.id in result_versions else None
                ),
                "prompt_b_version": (
                    job.prompt_b.version if job.prompt_b else
                    result_versions[job.id].prompt_b_version if job.id in result_versions else None
                ),
                "prompt_version": (
                    job.prompt_a.version if job.prompt_a and not job.prompt_b else None
                ),
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "attempts": job.attempts,
                "queue_class": job.queue_class,
                "origin_queue_class": job.origin_queue_class,
                "parent_job_id": job.parent_job_id,
                "technical_attempt": job.technical_attempt,
                "technical_error_type": job.technical_error_type,
                "retry_after_at": job.retry_after_at,
                "batch_key": job.batch_key,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ]
    }


def _evaluation_control(db: Session) -> EvaluationControl:
    control = db.get(EvaluationControl, 1)
    if control is None:
        control = EvaluationControl(id=1)
        db.add(control)
        db.flush()
    return control


@app.get("/api/jobs/control")
def get_job_control(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    counts = dict(
        db.execute(
            select(EvaluationJob.status, func.count(EvaluationJob.id)).group_by(EvaluationJob.status)
        ).all()
    )
    return {
        "paused": control.paused,
        "queued_count": counts.get("queued", 0),
        "processing_count": counts.get("processing", 0),
        "paused_count": counts.get("paused", 0),
        "active_count": sum(counts.get(status, 0) for status in ("queued", "processing", "paused")),
        "updated_at": control.updated_at,
    }


@app.post("/api/jobs/control/pause")
def pause_all_jobs(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    control.paused = True
    result = db.execute(
        update(EvaluationJob)
        .where(EvaluationJob.status.in_(("queued", "processing")))
        .values(status="paused", stage="paused", worker_id=None)
    )
    db.commit()
    return {"ok": True, "affected": result.rowcount}


@app.post("/api/jobs/control/resume")
def resume_all_jobs(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    control.paused = False
    paused_jobs = db.scalars(
        select(EvaluationJob).where(EvaluationJob.status == "paused")
    ).all()
    for job in paused_jobs:
        job.status = "queued"
        job.stage = "waiting"
        job.worker_id = None
        job.asset.status = "queued"
    db.commit()
    return {"ok": True, "affected": len(paused_jobs)}


@app.post("/api/jobs/control/cancel")
def cancel_all_jobs(
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    control = _evaluation_control(db)
    control.paused = False
    active_jobs = db.scalars(
        select(EvaluationJob).where(
            EvaluationJob.status.in_(("queued", "processing", "paused"))
        )
    ).all()
    now = datetime.now(timezone.utc)
    for job in active_jobs:
        has_result = db.scalar(
            select(EvaluationResult.id)
            .where(EvaluationResult.asset_id == job.asset_id)
            .limit(1)
        )
        job.status = "canceled"
        job.stage = "canceled"
        job.worker_id = None
        job.finished_at = now
        job.asset.status = "evaluated" if has_result else "uploaded"
    db.commit()
    return {"ok": True, "affected": len(active_jobs)}


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
        "high_risk_review_enabled": config.high_risk_review_enabled,
        "has_api_key": bool(config.encrypted_api_key),
        "api_key_mask": "••••••••" if config.encrypted_api_key else "",
        "updated_at": config.updated_at,
    }


def _sampling_policy_payload(policy: SamplingPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "version": f"smart-sampling-v1.1/policy-{policy.revision}",
        "revision": policy.revision,
        "sample_rate": policy.sample_rate,
        "low_confidence_threshold": policy.low_confidence_threshold,
        "medium_confidence_threshold": policy.medium_confidence_threshold,
        "cold_start_required_count": policy.cold_start_required_count,
        "high_level_required_from": policy.high_level_required_from,
        "updated_by": policy.updated_by,
        "updated_at": policy.updated_at,
    }


@app.get("/api/sampling-policy")
def get_sampling_policy(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    policy = db.get(SamplingPolicy, 1)
    if not policy:
        policy = SamplingPolicy(id=1)
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return _sampling_policy_payload(policy)


@app.put("/api/sampling-policy")
def update_sampling_policy(
    payload: SamplingPolicyUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = db.get(SamplingPolicy, 1)
    if not policy:
        policy = SamplingPolicy(id=1)
        db.add(policy)
    for field in (
        "sample_rate",
        "low_confidence_threshold",
        "medium_confidence_threshold",
        "cold_start_required_count",
        "high_level_required_from",
    ):
        setattr(policy, field, getattr(payload, field))
    policy.revision = (policy.revision or 0) + 1
    policy.updated_by = user.display_name
    db.commit()
    db.refresh(policy)
    return _sampling_policy_payload(policy)


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
        "high_risk_review_enabled",
    ):
        setattr(config, field, getattr(payload, field))
    protected_api_key = _protected_api_key(
        payload.api_key,
        account=MODEL_CONFIG_KEYCHAIN_ACCOUNT,
    )
    if protected_api_key is not None:
        config.encrypted_api_key = protected_api_key
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


def _optimizer_config_payload(config: OptimizerConfig) -> dict[str, Any]:
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
        "structured_output": config.structured_output,
        "has_api_key": bool(config.encrypted_api_key),
        "api_key_mask": "••••••••" if config.encrypted_api_key else "",
        "updated_at": config.updated_at,
    }


@app.get("/api/optimizer-config")
def get_optimizer_config(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not config:
        config = OptimizerConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return _optimizer_config_payload(config)


@app.put("/api/optimizer-config")
def update_optimizer_config(
    payload: OptimizerConfigUpdate,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not config:
        config = OptimizerConfig()
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
        "structured_output",
    ):
        setattr(config, field, getattr(payload, field))
    protected_api_key = _protected_api_key(
        payload.api_key,
        account=OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
    )
    if protected_api_key is not None:
        config.encrypted_api_key = protected_api_key
    db.commit()
    return {"ok": True}


@app.post("/api/optimizer-config/test")
async def test_optimizer_config(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not config:
        raise HTTPException(status_code=404, detail="提示词诊断模型配置不存在")
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
                "updated_at": prompt.updated_at,
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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(PromptVersion, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="提示词版本不存在")
    db.execute(
        update(PromptVersion)
        .where(PromptVersion.stage == prompt.stage, PromptVersion.status == "published")
        .values(status="archived")
    )
    prompt.status = "published"
    db.flush()
    prompt_a = prompt if prompt.stage == "A" else db.scalar(
        select(PromptVersion)
        .where(PromptVersion.stage == "A", PromptVersion.status == "published")
        .order_by(PromptVersion.created_at.desc())
    )
    prompt_b = prompt if prompt.stage == "B" else db.scalar(
        select(PromptVersion)
        .where(PromptVersion.stage == "B", PromptVersion.status == "published")
        .order_by(PromptVersion.created_at.desc())
    )
    regression_ids: list[int] = []
    if prompt_a and prompt_b:
        regression_ids = _create_regression_runs(
            db,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            created_by=user.username,
            trigger_prompt_id=prompt.id,
        )
    db.commit()
    return {"ok": True, "regression_run_ids": regression_ids}


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


def _optimization_payload(run: PromptOptimizationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "base_prompt_id": run.base_prompt_id,
        "base_prompt_version": run.base_prompt.version,
        "sample_set_id": run.sample_set_id,
        "sample_set_name": run.sample_set.name,
        "optimizer_model_id": run.optimizer_model_id,
        "status": run.status,
        "progress": run.progress,
        "sample_count": run.sample_count,
        "corrected_count": run.corrected_count,
        "diagnosis": json.loads(run.diagnosis_json or "{}"),
        "stage_audit": stage_audit_payload(run),
        "candidate_system_prompt": run.candidate_system_prompt,
        "candidate_user_prompt": run.candidate_user_prompt,
        "change_note": run.change_note,
        "error_message": run.error_message,
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


@app.get("/api/prompt-optimizations")
def list_prompt_optimizations(
    limit: int = 20,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(PromptOptimizationRun)
        .order_by(PromptOptimizationRun.created_at.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return {"items": [_optimization_payload(run) for run in runs]}


@app.get("/api/prompt-optimizations/{run_id}")
def get_prompt_optimization(
    run_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptOptimizationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="提示词优化任务不存在")
    return _optimization_payload(run)


@app.post("/api/prompt-optimizations")
def create_prompt_optimization(
    payload: PromptOptimizationCreateRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    prompt = db.get(PromptVersion, payload.prompt_id)
    sample_set = db.get(SampleSet, payload.sample_set_id)
    config = db.scalar(select(OptimizerConfig).limit(1))
    if not prompt or not sample_set:
        raise HTTPException(status_code=404, detail="提示词或样本集不存在")
    if prompt.stage != "B":
        raise HTTPException(status_code=400, detail="样本驱动优化目前用于调用 B 的美感维度提示词")
    if not config or not config.encrypted_api_key:
        raise HTTPException(status_code=400, detail="请先在模型配置中填写 SOL API Key")
    if not sample_set.items:
        raise HTTPException(status_code=400, detail="样本集还没有图片")
    active_run = db.scalar(
        select(PromptOptimizationRun).where(
            PromptOptimizationRun.status.in_(["queued", "running"])
        )
    )
    if active_run:
        raise HTTPException(status_code=409, detail="已有提示词优化任务正在运行")
    run = PromptOptimizationRun(
        base_prompt_id=prompt.id,
        sample_set_id=sample_set.id,
        optimizer_model_id=config.model_id,
        created_by=user.username,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(run_prompt_optimization, run.id)
    return {"id": run.id}


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
    correction_data = [item.model_dump() for item in payload.corrections]
    corrected_score = None
    corrected_level = None
    if payload.decision == "corrected":
        try:
            recalculated = calculate_corrected_score(
                json.loads(evaluation.precheck_json),
                json.loads(evaluation.aesthetic_json) if evaluation.aesthetic_json else None,
                correction_data,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        corrected_score = recalculated.get("score")
        corrected_level = recalculated.get("level")
        if corrected_score is None or corrected_level is None:
            raise HTTPException(status_code=400, detail="当前结果无法自动计算正式等级")
    review_data = payload.model_dump(exclude={"corrections", "corrected_level"})
    review = HumanReview(
        evaluation_id=evaluation_id,
        corrected_level=corrected_level,
        corrected_score=corrected_score,
        corrections_json=json.dumps(correction_data, ensure_ascii=False),
        **review_data,
    )
    if payload.decision in {"approved", "corrected"}:
        evaluation.needs_review = False
    else:
        evaluation.needs_review = True
    evaluation.updated_at = datetime.now(timezone.utc)
    db.add(review)
    db.commit()
    db.refresh(review)
    return {"id": review.id}


def _sample_set_summary(sample_set: SampleSet) -> dict[str, Any]:
    truth_complete = sum(1 for item in sample_set.items if bool(json.loads(item.truth_json or "{}")))
    return {
        "id": sample_set.id,
        "name": sample_set.name,
        "description": sample_set.description,
        "kind": sample_set.kind,
        "status": sample_set.status,
        "item_count": len(sample_set.items),
        "truth_complete_count": truth_complete,
        "created_by": sample_set.created_by,
        "created_at": sample_set.created_at,
    }


def _sample_set_item_payload(item: SampleSetItem) -> dict[str, Any]:
    source = _result_payload(item.source_result)
    truth = json.loads(item.truth_json or "{}")
    return {
        "id": item.id,
        "asset_id": item.asset_id,
        "asset_name": item.asset.original_name,
        "image_url": f"/api/assets/{item.asset_id}/file",
        "expected_level": item.expected_level,
        "expected_category": item.expected_category,
        "note": item.note,
        "truth": truth,
        "truth_revision": item.truth_revision,
        "truth_updated_by": item.truth_updated_by,
        "truth_updated_at": item.truth_updated_at,
        "source_model_id": item.source_result.model_id,
        "source_level": source.get("final_level") if source else None,
        "added_by": item.added_by,
        "created_at": item.created_at,
    }


def _create_regression_runs(
    db: Session,
    *,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion,
    created_by: str,
    trigger_prompt_id: int | None = None,
    sample_set_id: int | None = None,
    threshold: float = 0.9,
) -> list[int]:
    query = select(SampleSet).where(
        SampleSet.kind == "golden",
        SampleSet.status == "locked",
    )
    if sample_set_id is not None:
        query = query.where(SampleSet.id == sample_set_id)
    sample_sets = db.scalars(query.order_by(SampleSet.created_at.asc())).all()
    run_ids: list[int] = []
    for sample_set in sample_sets:
        eligible = [item for item in sample_set.items if json.loads(item.truth_json or "{}")]
        if not eligible:
            continue
        run = PromptRegressionRun(
            name=f"{prompt_a.version} + {prompt_b.version} · {sample_set.name}",
            sample_set_id=sample_set.id,
            trigger_prompt_id=trigger_prompt_id,
            prompt_a_id=prompt_a.id,
            prompt_b_id=prompt_b.id,
            threshold=threshold,
            total=len(eligible),
            status="queued",
            created_by=created_by,
        )
        db.add(run)
        db.flush()
        for sample_item in eligible:
            regression_item = PromptRegressionItem(
                run_id=run.id,
                sample_item_id=sample_item.id,
                status="queued",
            )
            db.add(regression_item)
            db.flush()
            job = EvaluationJob(
                asset_id=sample_item.asset_id,
                prompt_a_id=prompt_a.id,
                prompt_b_id=prompt_b.id,
                regression_item_id=regression_item.id,
                queue_class="validation",
                origin_queue_class="validation",
                batch_key=f"regression:{run.id}",
            )
            db.add(job)
            db.flush()
            regression_item.job_id = job.id
        run_ids.append(run.id)
    return run_ids


@app.get("/api/sample-sets")
def list_sample_sets(
    _user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    sample_sets = db.scalars(select(SampleSet).order_by(SampleSet.created_at.desc())).all()
    return {"items": [_sample_set_summary(sample_set) for sample_set in sample_sets]}


@app.post("/api/sample-sets")
def create_sample_set(
    payload: SampleSetCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写样本集名称")
    if db.scalar(select(SampleSet).where(SampleSet.name == name)):
        raise HTTPException(status_code=400, detail="样本集名称已存在")
    sample_set = SampleSet(
        name=name,
        description=payload.description.strip(),
        kind=payload.kind,
        created_by=user.username,
    )
    db.add(sample_set)
    db.commit()
    db.refresh(sample_set)
    return {"id": sample_set.id}


@app.get("/api/sample-sets/{sample_set_id}")
def sample_set_detail(
    sample_set_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    return {
        "summary": _sample_set_summary(sample_set),
        "items": [_sample_set_item_payload(item) for item in sample_set.items],
    }


@app.post("/api/sample-sets/{sample_set_id}/items")
def add_sample_set_items(
    sample_set_id: int,
    payload: SampleSetAddItemsRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    requested_ids = list(dict.fromkeys(payload.asset_ids))
    assets = db.scalars(select(Asset).where(Asset.id.in_(requested_ids))).all()
    assets_by_id = {asset.id: asset for asset in assets}
    missing = [asset_id for asset_id in requested_ids if asset_id not in assets_by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"有 {len(missing)} 张素材不存在")
    existing_ids = {item.asset_id for item in sample_set.items}
    added = 0
    skipped: list[int] = []
    for asset_id in requested_ids:
        if asset_id in existing_ids:
            skipped.append(asset_id)
            continue
        result = db.scalar(
            select(EvaluationResult)
            .where(EvaluationResult.asset_id == asset_id)
            .order_by(EvaluationResult.created_at.desc())
            .limit(1)
        )
        if not result:
            skipped.append(asset_id)
            continue
        result_payload = _result_payload(result) or {}
        human_review = result_payload.get("human_review") or {}
        if human_review.get("decision") not in {"approved", "corrected"}:
            skipped.append(asset_id)
            continue
        precheck = result_payload.get("precheck") or {}
        category = ((precheck.get("classification") or {}).get("primary_category") or "无法判断")
        item = SampleSetItem(
                sample_set_id=sample_set.id,
                asset_id=asset_id,
                source_result_id=result.id,
                expected_level=payload.expected_level or result_payload.get("final_level"),
                expected_category=category,
                truth_json=json.dumps(
                    truth_from_result(result, payload.expected_level), ensure_ascii=False
                ),
                truth_updated_by=user.username,
                truth_updated_at=datetime.now(timezone.utc),
                added_by=user.username,
            )
        db.add(item)
        db.flush()
        db.add(
            SampleTruthRevision(
                sample_item_id=item.id,
                revision=1,
                truth_json=item.truth_json,
                reason="收录样本时建立首版标准答案",
                reviewer_name=user.username,
            )
        )
        added += 1
    if not added:
        raise HTTPException(status_code=400, detail="所选素材未评测或已经在样本集中")
    db.commit()
    return {"added": added, "skipped": skipped}


@app.patch("/api/sample-sets/{sample_set_id}/items/{item_id}")
def update_sample_set_item(
    sample_set_id: int,
    item_id: int,
    payload: SampleSetItemUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.id == item_id,
            SampleSetItem.sample_set_id == sample_set_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    item.expected_level = payload.expected_level
    item.note = payload.note.strip()
    if payload.truth is not None:
        truth = dict(payload.truth)
        if payload.expected_level:
            truth["level"] = payload.expected_level
        item.truth_revision += 1
        item.truth_json = json.dumps(truth, ensure_ascii=False)
        item.truth_updated_by = user.username
        item.truth_updated_at = datetime.now(timezone.utc)
        db.add(
            SampleTruthRevision(
                sample_item_id=item.id,
                revision=item.truth_revision,
                truth_json=item.truth_json,
                reason=payload.revision_reason.strip() or "更新标准答案",
                reviewer_name=user.username,
            )
        )
    db.commit()
    return {"ok": True}


@app.patch("/api/sample-sets/{sample_set_id}/status")
def update_sample_set_status(
    sample_set_id: int,
    payload: SampleSetStatusRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    sample_set = db.get(SampleSet, sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    if payload.status == "locked":
        if sample_set.kind != "golden":
            raise HTTPException(status_code=400, detail="只有黄金样本集需要锁定")
        incomplete = [item for item in sample_set.items if not json.loads(item.truth_json or "{}")]
        if not sample_set.items or incomplete:
            raise HTTPException(status_code=400, detail="请先补全所有黄金样本的标准答案")
    sample_set.status = payload.status
    db.commit()
    return {"ok": True}


@app.get("/api/sample-sets/{sample_set_id}/items/{item_id}/history")
def sample_item_history(
    sample_set_id: int,
    item_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.id == item_id,
            SampleSetItem.sample_set_id == sample_set_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    results = db.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.asset_id == item.asset_id)
        .order_by(EvaluationResult.created_at.desc())
    ).all()
    evaluations = []
    for result in results:
        payload = _result_payload(result) or {}
        payload["reviews"] = [
            {
                "id": review.id,
                "reviewer_name": review.reviewer_name,
                "decision": review.decision,
                "corrected_level": review.corrected_level,
                "note": review.note,
                "corrections": json.loads(review.corrections_json or "[]"),
                "created_at": review.created_at,
            }
            for review in result.reviews
        ]
        evaluations.append(payload)
    revisions = db.scalars(
        select(SampleTruthRevision)
        .where(SampleTruthRevision.sample_item_id == item.id)
        .order_by(SampleTruthRevision.revision.desc())
    ).all()
    regression_items = db.scalars(
        select(PromptRegressionItem)
        .where(PromptRegressionItem.sample_item_id == item.id)
        .order_by(PromptRegressionItem.created_at.desc())
    ).all()
    return {
        "item": _sample_set_item_payload(item),
        "evaluations": evaluations,
        "truth_revisions": [
            {
                "id": revision.id,
                "revision": revision.revision,
                "truth": json.loads(revision.truth_json or "{}"),
                "reason": revision.reason,
                "reviewer_name": revision.reviewer_name,
                "created_at": revision.created_at,
            }
            for revision in revisions
        ],
        "regressions": [
            {
                "id": regression.id,
                "run_id": regression.run_id,
                "run_name": regression.run.name,
                "status": regression.status,
                "passed": regression.passed,
                "comparison": json.loads(regression.comparison_json or "{}"),
                "created_at": regression.created_at,
                "finished_at": regression.finished_at,
            }
            for regression in regression_items
        ],
    }


@app.delete("/api/sample-sets/{sample_set_id}/items/{item_id}")
def remove_sample_set_item(
    sample_set_id: int,
    item_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.id == item_id,
            SampleSetItem.sample_set_id == sample_set_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    db.delete(item)
    db.commit()
    return {"ok": True}


def _regression_summary(run: PromptRegressionRun) -> dict[str, Any]:
    metrics = json.loads(run.metrics_json or "{}")
    candidate_strategy = (
        json.loads(run.candidate_strategy_snapshot_json or "{}")
        if run.regression_mode == "paired"
        else {}
    )
    candidate_prompt_a = candidate_strategy.get("prompt_a") or {}
    candidate_prompt_b = candidate_strategy.get("prompt_b") or {}
    payload = {
        "id": run.id,
        "name": run.name,
        "regression_mode": run.regression_mode,
        "sample_set_id": run.sample_set_id,
        "sample_set_name": run.sample_set.name,
        "prompt_a_id": (
            candidate_prompt_a.get("id")
            if candidate_prompt_a
            else run.prompt_a_id
        ),
        "prompt_a_version": (
            candidate_prompt_a.get("version")
            if candidate_prompt_a
            else run.prompt_a.version
        ),
        "prompt_b_id": (
            candidate_prompt_b.get("id")
            if candidate_prompt_b
            else run.prompt_b_id
        ),
        "prompt_b_version": (
            candidate_prompt_b.get("version")
            if candidate_prompt_b
            else run.prompt_b.version
        ),
        "status": run.status,
        "threshold": run.threshold,
        "total": run.total,
        "completed": run.completed,
        "passed": run.passed,
        "failed": run.failed,
        "pass_rate": metrics.get("pass_rate", 0),
        "release_gate_passed": metrics.get("release_gate_passed", False),
        "created_by": run.created_by,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }
    if run.regression_mode == "paired":
        payload.update(
            {
                "baseline_strategy_bundle_id": run.baseline_strategy_bundle_id,
                "candidate_strategy_bundle_id": run.candidate_strategy_bundle_id,
                "sample_set_version": run.sample_set_version,
                "metric_rules_version": run.metric_rules_version,
                "metric_rules": json.loads(run.metric_rules_json or "{}"),
                "recommendation": run.recommendation,
                "approval_status": run.approval_status,
                "approved_by": run.approved_by,
                "approval_note": run.approval_note,
                "approved_at": run.approved_at,
                "summary": json.loads(run.summary_json or "{}"),
            }
        )
    return payload


@app.get("/api/prompt-regressions")
def list_prompt_regressions(
    limit: int = 100,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runs = db.scalars(
        select(PromptRegressionRun)
        .order_by(PromptRegressionRun.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    return {"items": [_regression_summary(run) for run in runs]}


def _paired_metric_rules(payload: PairedRegressionCreateRequest) -> dict[str, Any]:
    return {
        "schema_version": "paired-metric-rules-v1",
        "version": payload.metric_rules_version.strip(),
        "thresholds": {
            "aesthetic_accuracy_max_drop": payload.aesthetic_accuracy_max_drop,
            "whole_image_accuracy_max_drop": payload.whole_image_accuracy_max_drop,
            "level_consistency_max_drop": payload.level_consistency_max_drop,
        },
        **paired_gate_policy(),
    }


def _latest_bundle_result(
    db: Session, *, asset_id: int, strategy_bundle_id: int
) -> EvaluationResult | None:
    return db.scalar(
        select(EvaluationResult)
        .where(
            EvaluationResult.asset_id == asset_id,
            EvaluationResult.strategy_bundle_id == strategy_bundle_id,
        )
        .order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .limit(1)
    )


def _ensure_paired_validation_job(
    db: Session,
    *,
    item: PromptRegressionItem,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion,
) -> EvaluationJob:
    existing = db.scalar(
        select(EvaluationJob).where(
            EvaluationJob.regression_item_id == item.id,
            EvaluationJob.strategy_bundle_id == bundle.id,
        )
    )
    if existing is not None:
        return existing
    job = EvaluationJob(
        asset_id=item.sample_item.asset_id,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        regression_item_id=item.id,
        strategy_bundle_id=bundle.id,
        queue_class="validation",
        origin_queue_class="validation",
        technical_attempt=0,
        batch_key=f"paired-regression:{item.run_id}",
        status="queued",
        stage="waiting",
    )
    db.add(job)
    db.flush()
    return job


def _strategy_snapshot_for_bundle(
    db: Session, bundle: StrategyBundle
) -> tuple[str, PromptVersion, PromptVersion]:
    """Resolve the exact immutable bundle definition without result history."""
    if bundle.prompt_b_version is None:
        raise ValueError("配对回归的 StrategyBundle 必须包含 B 阶段提示词")
    prompt_a_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "A",
            PromptVersion.version == bundle.prompt_a_version,
        )
    ).all()
    prompt_b_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "B",
            PromptVersion.version == bundle.prompt_b_version,
        )
    ).all()
    if bundle.sampling_policy_revision is None:
        policy_matches: list[SamplingPolicy | None] = [None]
    else:
        policy_matches = list(
            db.scalars(
                select(SamplingPolicy).where(
                    SamplingPolicy.revision
                    == bundle.sampling_policy_revision
                )
            ).all()
        )

    matches: list[tuple[str, PromptVersion, PromptVersion]] = []
    for prompt_a in prompt_a_matches:
        for prompt_b in prompt_b_matches:
            for policy in policy_matches:
                try:
                    snapshot = safe_strategy_snapshot_payload(
                        build_strategy_snapshot(
                            bundle, prompt_a, prompt_b, policy
                        )
                    )
                    snapshot_json = json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except ValueError:
                    continue
                matches.append((snapshot_json, prompt_a, prompt_b))
    if len(matches) != 1:
        raise ValueError(
            "StrategyBundle 缺少唯一可验证的 Prompt/采样策略定义；"
            "不能依赖历史评测结果补全"
        )
    return matches[0]


@app.post("/api/paired-regressions")
def create_paired_regression(
    payload: PairedRegressionCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sample_set = db.get(SampleSet, payload.sample_set_id)
    if not sample_set:
        raise HTTPException(status_code=404, detail="样本集不存在")
    if sample_set.kind != "golden" or sample_set.status != "locked":
        raise HTTPException(
            status_code=400, detail="配对回归只能使用已锁定的黄金样本集"
        )
    baseline_bundle = db.get(
        StrategyBundle, payload.baseline_strategy_bundle_id
    )
    candidate_bundle = db.get(
        StrategyBundle, payload.candidate_strategy_bundle_id
    )
    if not baseline_bundle or not candidate_bundle:
        raise HTTPException(
            status_code=400, detail="基线或候选 StrategyBundle 不存在"
        )
    if baseline_bundle.id == candidate_bundle.id:
        raise HTTPException(
            status_code=400, detail="基线与候选 StrategyBundle 必须不同"
        )

    try:
        (
            baseline_strategy_snapshot_json,
            baseline_prompt_a,
            baseline_prompt_b,
        ) = _strategy_snapshot_for_bundle(db, baseline_bundle)
        (
            candidate_strategy_snapshot_json,
            candidate_prompt_a,
            candidate_prompt_b,
        ) = _strategy_snapshot_for_bundle(db, candidate_bundle)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    requested_ids = [sample.sample_item_id for sample in payload.samples]
    sample_items = db.scalars(
        select(SampleSetItem).where(
            SampleSetItem.sample_set_id == sample_set.id,
            SampleSetItem.id.in_(requested_ids),
        )
    ).all()
    items_by_id = {item.id: item for item in sample_items}
    if set(items_by_id) != set(requested_ids):
        raise HTTPException(
            status_code=400, detail="存在不属于该样本集的样本"
        )

    frozen: list[dict[str, Any]] = []
    for requested in payload.samples:
        sample_item = items_by_id[requested.sample_item_id]
        try:
            truth_snapshot, review = reviewed_truth_snapshot(
                sample_item.source_result, requested.role
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        frozen.append(
            {
                "sample_item": sample_item,
                "role": requested.role,
                "truth_snapshot": truth_snapshot,
                "source_review_id": review.id,
            }
        )

    manifest = {
        "schema_version": "paired-sample-set-v1",
        "sample_set_id": sample_set.id,
        "items": [
            {
                "sample_item_id": entry["sample_item"].id,
                "asset_id": entry["sample_item"].asset_id,
                "role": entry["role"],
                "truth_revision": entry["sample_item"].truth_revision,
                "truth_snapshot": entry["truth_snapshot"],
            }
            for entry in frozen
        ],
    }
    manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sample_set_version = hashlib.sha256(
        manifest_json.encode("utf-8")
    ).hexdigest()
    metric_rules = _paired_metric_rules(payload)
    metric_rules_json = json.dumps(
        metric_rules,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prior_rule_run = db.scalar(
        select(PromptRegressionRun)
        .where(
            PromptRegressionRun.regression_mode == "paired",
            PromptRegressionRun.metric_rules_version
            == payload.metric_rules_version.strip(),
        )
        .order_by(PromptRegressionRun.id.asc())
        .limit(1)
    )
    if (
        prior_rule_run
        and prior_rule_run.metric_rules_json != metric_rules_json
    ):
        raise HTTPException(
            status_code=400,
            detail="同一指标规则版本不能对应不同阈值",
        )

    run = PromptRegressionRun(
        name=payload.name.strip(),
        sample_set_id=sample_set.id,
        prompt_a_id=candidate_prompt_a.id,
        prompt_b_id=candidate_prompt_b.id,
        regression_mode="paired",
        baseline_strategy_bundle_id=baseline_bundle.id,
        candidate_strategy_bundle_id=candidate_bundle.id,
        baseline_strategy_snapshot_json=baseline_strategy_snapshot_json,
        candidate_strategy_snapshot_json=candidate_strategy_snapshot_json,
        sample_set_version=sample_set_version,
        sample_manifest_json=manifest_json,
        metric_rules_version=payload.metric_rules_version.strip(),
        metric_rules_json=metric_rules_json,
        summary_json="{}",
        recommendation="pending",
        approval_status="pending",
        threshold=1.0,
        total=len(frozen),
        status="waiting_results",
        created_by=user.username,
    )
    db.add(run)
    db.flush()

    created_items: list[PromptRegressionItem] = []
    for entry in frozen:
        sample_item = entry["sample_item"]
        item = PromptRegressionItem(
            run_id=run.id,
            sample_item_id=sample_item.id,
            sample_role=entry["role"],
            source_evaluation_id=sample_item.source_result_id,
            source_review_id=entry["source_review_id"],
            truth_snapshot_json=json.dumps(
                entry["truth_snapshot"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            status="waiting_results",
        )
        db.add(item)
        db.flush()
        created_items.append(item)

    pending_item_ids: list[int] = []
    pending_job_ids: list[int] = []
    for item in created_items:
        sample_item = item.sample_item
        baseline = _latest_bundle_result(
            db,
            asset_id=sample_item.asset_id,
            strategy_bundle_id=baseline_bundle.id,
        )
        candidate = _latest_bundle_result(
            db,
            asset_id=sample_item.asset_id,
            strategy_bundle_id=candidate_bundle.id,
        )
        if baseline and candidate:
            complete_paired_regression_item(
                db,
                item=item,
                baseline=baseline,
                candidate=candidate,
            )
        else:
            pending_item_ids.append(item.id)
            if baseline is None:
                pending_job_ids.append(
                    _ensure_paired_validation_job(
                        db,
                        item=item,
                        bundle=baseline_bundle,
                        prompt_a=baseline_prompt_a,
                        prompt_b=baseline_prompt_b,
                    ).id
                )
            if candidate is None:
                candidate_job = _ensure_paired_validation_job(
                    db,
                    item=item,
                    bundle=candidate_bundle,
                    prompt_a=candidate_prompt_a,
                    prompt_b=candidate_prompt_b,
                )
                pending_job_ids.append(candidate_job.id)
                item.job_id = candidate_job.id
    refresh_paired_regression_run(db, run)
    db.commit()
    return {
        "id": run.id,
        "status": run.status,
        "recommendation": run.recommendation,
        "approval_status": run.approval_status,
        "sample_set_version": run.sample_set_version,
        "pending_item_ids": pending_item_ids,
        "pending_job_ids": pending_job_ids,
    }


@app.post("/api/paired-regressions/{run_id}/items/{item_id}/results")
def attach_paired_regression_results(
    run_id: int,
    item_id: int,
    payload: PairedRegressionResultsRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(
        select(PromptRegressionItem).where(
            PromptRegressionItem.id == item_id,
            PromptRegressionItem.run_id == run_id,
        )
    )
    if not item or item.run.regression_mode != "paired":
        raise HTTPException(status_code=404, detail="配对回归项不存在")
    baseline = db.get(EvaluationResult, payload.baseline_evaluation_id)
    candidate = db.get(EvaluationResult, payload.candidate_evaluation_id)
    if not baseline or not candidate:
        raise HTTPException(status_code=400, detail="基线或候选评测结果不存在")
    try:
        comparison = complete_paired_regression_item(
            db,
            item=item,
            baseline=baseline,
            candidate=candidate,
        )
    except ValueError as exc:
        status_code = 409 if item.status == "completed" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    db.commit()
    return {
        "ok": True,
        "status": item.status,
        "passed": item.passed,
        "comparison": comparison,
        "recommendation": item.run.recommendation,
    }


@app.post("/api/paired-regressions/{run_id}/approval")
def approve_paired_regression(
    run_id: int,
    payload: PairedRegressionApprovalRequest,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptRegressionRun, run_id)
    if not run or run.regression_mode != "paired":
        raise HTTPException(status_code=404, detail="配对回归任务不存在")
    if run.recommendation == "pending":
        raise HTTPException(status_code=409, detail="配对回归尚未完成")
    if payload.status == "approved" and run.recommendation != "pass":
        raise HTTPException(
            status_code=409, detail="系统建议未通过，不能标记为人工批准"
        )
    if run.approval_status != "pending":
        if (
            run.approval_status == payload.status
            and run.approved_by == payload.reviewer_name
            and run.approval_note == payload.note
        ):
            return {
                "ok": True,
                "approval_status": run.approval_status,
                "published": False,
            }
        raise HTTPException(status_code=409, detail="人工批准结论已经冻结")
    run.approval_status = payload.status
    run.approved_by = payload.reviewer_name
    run.approval_note = payload.note
    run.approved_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "ok": True,
        "approval_status": run.approval_status,
        "published": False,
    }


@app.get("/api/prompt-regressions/{run_id}")
def prompt_regression_detail(
    run_id: int,
    outcome: Literal["passed", "failed", "pending", "sealed"] | None = None,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PromptRegressionRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="回归任务不存在")
    paired = run.regression_mode == "paired"
    if not paired:
        return {
            "summary": _regression_summary(run),
            "items": [
                {
                    "id": item.id,
                    "sample_item_id": item.sample_item_id,
                    "asset_id": item.sample_item.asset_id,
                    "asset_name": item.sample_item.asset.original_name,
                    "image_url": (
                        f"/api/assets/{item.sample_item.asset_id}/file"
                    ),
                    "sample_role": item.sample_role,
                    "source_evaluation_id": item.source_evaluation_id,
                    "source_review_id": item.source_review_id,
                    "truth_snapshot": None,
                    "expected": json.loads(
                        item.sample_item.truth_json or "{}"
                    ),
                    "status": item.status,
                    "passed": item.passed,
                    "comparison": json.loads(
                        item.comparison_json or "{}"
                    ),
                    "baseline_evaluation": None,
                    "candidate_evaluation": None,
                    "evaluation": _result_payload(item.evaluation),
                }
                for item in run.items
            ],
        }

    answer_visible = (
        run.status in {"passed", "regressed"}
        and run.recommendation in {"pass", "fail"}
        and run.finished_at is not None
    )
    item_payloads: list[dict[str, Any]] = []
    for item in run.items:
        truth_snapshot = json.loads(item.truth_snapshot_json or "{}")
        comparison = json.loads(item.comparison_json or "{}")
        baseline_result = json.loads(item.baseline_result_json or "{}")
        candidate_result = json.loads(item.candidate_result_json or "{}")
        blind_withheld = (
            item.sample_role == "blind_holdout" and not answer_visible
        )
        if blind_withheld:
            truth_snapshot_payload: dict[str, Any] | None = None
            expected_payload: dict[str, Any] | None = None
            comparison_payload: dict[str, Any] = {
                "withheld": True,
                "reason": "blind_holdout_pending",
            }
            baseline_result_payload = {
                key: baseline_result[key]
                for key in ("evaluation_id", "strategy_bundle_id", "fields")
                if key in baseline_result
            }
            candidate_result_payload = {
                key: candidate_result[key]
                for key in ("evaluation_id", "strategy_bundle_id", "fields")
                if key in candidate_result
            }
            diffs: list[dict[str, Any]] = []
            failure_reasons: list[dict[str, Any]] = []
            critical_regressions: list[str] = []
            new_severe_errors: list[dict[str, Any]] = []
            passed: bool | None = None
            failed: bool | None = None
            baseline_evaluation = None
            candidate_evaluation = None
        else:
            truth_snapshot_payload = truth_snapshot
            expected_payload = truth_snapshot.get("truth") or {}
            comparison_payload = comparison
            baseline_result_payload = baseline_result
            candidate_result_payload = candidate_result
            diffs = comparison.get("diffs") or []
            failure_reasons = comparison.get("failure_reasons") or []
            if item.status == "error" and not failure_reasons:
                failure_reasons = [
                    {
                        "code": "comparison_error",
                        "severity": "P0",
                        "message": str(
                            comparison.get("error")
                            or "样本配对比较失败"
                        ),
                    }
                ]
            critical_regressions = (
                comparison.get("critical_regressions") or []
            )
            new_severe_errors = (
                comparison.get("new_severe_errors") or []
            )
            passed = item.passed
            failed = (
                item.status == "error" or item.passed is False
                if item.status in {"completed", "error"}
                else None
            )
            baseline_evaluation = _result_payload(
                item.baseline_evaluation
            )
            candidate_evaluation = _result_payload(
                item.candidate_evaluation
            )

        item_outcome = (
            "sealed"
            if blind_withheld
            else "failed"
            if item.status == "error" or item.passed is False
            else "passed"
            if item.status == "completed" and item.passed is True
            else "pending"
        )
        item_payloads.append(
            {
                "id": item.id,
                "sample_item_id": item.sample_item_id,
                "asset_id": item.sample_item.asset_id,
                "asset_name": item.sample_item.asset.original_name,
                "image_url": (
                    f"/api/assets/{item.sample_item.asset_id}/file"
                ),
                "sample_role": item.sample_role,
                "source_evaluation_id": (
                    None
                    if blind_withheld
                    else item.source_evaluation_id
                ),
                "source_review_id": (
                    None if blind_withheld else item.source_review_id
                ),
                "truth_snapshot": truth_snapshot_payload,
                "expected": expected_payload,
                "answer_withheld": blind_withheld,
                "truth_revealed": not blind_withheld,
                "status": item.status,
                "outcome": item_outcome,
                "passed": passed,
                "failed": failed,
                "baseline_evaluation_id": item.baseline_evaluation_id,
                "candidate_evaluation_id": item.candidate_evaluation_id,
                "baseline_result": baseline_result_payload,
                "candidate_result": candidate_result_payload,
                "diffs": diffs,
                "failure_reasons": failure_reasons,
                "critical_regressions": critical_regressions,
                "new_severe_errors": new_severe_errors,
                "comparison": comparison_payload,
                "baseline_evaluation": baseline_evaluation,
                "candidate_evaluation": candidate_evaluation,
                "evaluation": candidate_evaluation,
            }
        )

    error_items = [
        {
            "item_id": item["id"],
            "sample_item_id": item["sample_item_id"],
            "asset_id": item["asset_id"],
            "asset_name": item["asset_name"],
            "image_url": item["image_url"],
            "sample_role": item["sample_role"],
            "status": item["status"],
            "passed": item["passed"],
            "baseline_evaluation_id": item["baseline_evaluation_id"],
            "candidate_evaluation_id": item["candidate_evaluation_id"],
            "failure_reasons": item["failure_reasons"],
            "critical_regressions": item["critical_regressions"],
            "new_severe_errors": item["new_severe_errors"],
        }
        for item in item_payloads
        if item["answer_withheld"] is False
        and (item["status"] == "error" or item["passed"] is False)
    ]
    filtered_items = (
        [
            item
            for item in item_payloads
            if item["outcome"] == outcome
        ]
        if outcome is not None
        else item_payloads
    )
    return {
        "summary": _regression_summary(run),
        "baseline_strategy": safe_strategy_snapshot_payload(
            run.baseline_strategy_snapshot_json
        ),
        "candidate_strategy": safe_strategy_snapshot_payload(
            run.candidate_strategy_snapshot_json
        ),
        "error_items": error_items,
        "item_filter": {"outcome": outcome},
        "items": filtered_items,
    }


@app.post("/api/prompt-regressions")
def create_prompt_regression(
    payload: RegressionCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt_a = db.get(PromptVersion, payload.prompt_a_id) if payload.prompt_a_id else db.scalar(
        select(PromptVersion)
        .where(PromptVersion.stage == "A", PromptVersion.status == "published")
        .order_by(PromptVersion.created_at.desc())
    )
    prompt_b = db.get(PromptVersion, payload.prompt_b_id) if payload.prompt_b_id else db.scalar(
        select(PromptVersion)
        .where(PromptVersion.stage == "B", PromptVersion.status == "published")
        .order_by(PromptVersion.created_at.desc())
    )
    if not prompt_a or prompt_a.stage != "A" or not prompt_b or prompt_b.stage != "B":
        raise HTTPException(status_code=400, detail="请选择有效的 A、B 提示词版本")
    run_ids = _create_regression_runs(
        db,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        created_by=user.username,
        sample_set_id=payload.sample_set_id,
        threshold=payload.threshold,
    )
    if not run_ids:
        raise HTTPException(status_code=400, detail="没有可回归的已锁定黄金样本")
    db.commit()
    return {"ids": run_ids}


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
        if item.sample_expected_level:
            baseline_payload["final_level"] = item.sample_expected_level
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
    sample_sets = db.scalars(select(SampleSet).order_by(SampleSet.created_at.desc())).all()
    return {
        "candidate": {
            "model_id": model.model_id if model else "",
            "name": model.name if model else "未配置",
            "has_api_key": bool(model and model.encrypted_api_key),
        },
        "baselines": [
            {"model_id": model_id, "asset_count": count} for model_id, count in rows
        ],
        "sample_sets": [_sample_set_summary(sample_set) for sample_set in sample_sets],
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

    selected: list[EvaluationResult] = []
    selected_sample_set: SampleSet | None = None
    sample_expected_levels: dict[int, str | None] = {}
    if payload.sample_set_id:
        selected_sample_set = db.get(SampleSet, payload.sample_set_id)
        if not selected_sample_set:
            raise HTTPException(status_code=404, detail="样本集不存在")
        selected = [
            latest_by_asset[item.asset_id]
            for item in selected_sample_set.items
            if item.asset_id in latest_by_asset
        ]
        sample_expected_levels = {
            item.asset_id: item.expected_level for item in selected_sample_set.items
        }
        if not selected:
            raise HTTPException(status_code=400, detail="该样本集没有对应旧模型历史结果的素材")
    else:
        buckets: dict[tuple[str, str], list[EvaluationResult]] = {}
        for result in latest_by_asset.values():
            try:
                precheck = json.loads(result.precheck_json)
            except json.JSONDecodeError:
                precheck = {}
            category = ((precheck.get("classification") or {}).get("primary_category") or "无法判断")
            key = (result.level or "无等级", category)
            buckets.setdefault(key, []).append(result)

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
        name=payload.name or (
            f"{model.model_id} · {selected_sample_set.name}"
            if selected_sample_set
            else f"{payload.baseline_model_id} → {model.model_id}"
        ),
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
                sample_expected_level=sample_expected_levels.get(baseline.asset_id),
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
