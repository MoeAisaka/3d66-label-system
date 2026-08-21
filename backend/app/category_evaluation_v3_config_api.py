"""ADR-0033 Task 3: isolated CRUD + validation for persisted v3 contracts.

An **isolated** router that gives the ADR-0033 v3 category-evaluation contract
(红线 + 子类目赛道 + 共性/特有维度组 + 分类映射) a persistence + CRUD +
server-side validation surface, so operators can 存 → 读 → 改 → 校验 candidate
configs before any线上接入.

Isolation boundaries (hard constraints — see the task brief):

- It stores into the standalone ``category_evaluation_v3_configs`` table only.
  It shares nothing with the v1 ``EvaluationCategoryProfile`` /
  ``category-pipeline-v1`` pipeline — separate key space, separate CRUD.
- Every write is **validated before it lands** through the mechanism-profile
  registry, which delegates to the existing image or Proposal validators. No
  profile-specific validation logic is re-implemented here.
- Handlers are pure CRUD + validation: **no queue, no publish, no model calls,
  no touching of the frozen scoring path / worker.**

Framework ``ValueError`` subclasses (all carry a stable ``.code``) are caught
and surfaced as HTTP 400 with that ``code`` — never a 500.  A duplicate
``category_key`` is a coded 409; a missing config is a coded 404.

This module provides only a router *factory*
(``build_category_evaluation_v3_config_router``); wiring it into the application
(``include_router``) is done separately so no existing file is modified here.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .category_evaluation_contract import canonical_contract_hash
from .audit import append_audit_event
from .category_evaluation_v3_revisions import (
    CategoryEvaluationV3RevisionError,
    RevisionArtifacts,
    activate_candidate_revision,
    create_candidate_revision,
    ensure_projected_revision,
)
from .database import get_db
from .dimension_schema_registry import canonical_json
from .level_scale import resolve_level_scale
from .mechanism_profiles import (
    MechanismProfileError,
    describe_mechanism_profile,
    extract_profile_rule_mirror,
    mechanism_profile_catalog,
    profile_media_penalty_enabled,
    validate_mechanism_artifacts,
)
from .models import CategoryEvaluationV3Config, CategoryEvaluationV3Revision
from .models import BaselineRegressionRun, TagDemandContract
from .mechanism_release_gate import (
    CandidateReleaseGateError,
    evaluate_candidate_release_gate,
)
from .semantic_tag_contracts import (
    PLATFORM_SEMANTIC_CONTRACT_KEY,
    SemanticTagContractError,
    validate_tag_demand_contract,
)


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


class V3ConfigWriteRequest(BaseModel):
    """Body for create (POST) / replace (PUT) / dry-run validate.

    Artifacts are loosely typed so any malformed content reaches the reused
    framework validators and surfaces as a coded HTTP 400 rather than a
    pydantic 422 without a ``code``.  ``subcategory_dimensions`` is a
    ``{track_key: config}`` map, each config validated independently.
    """

    model_config = ConfigDict(extra="forbid")

    category_key: str
    display_name: str
    contract: dict[str, Any]
    classification_map: dict[str, Any]
    subcategory_dimensions: dict[str, Any] = Field(default_factory=dict)


class ValidationErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    code: str
    message: str


class ValidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    errors: list[ValidationErrorItem]


class V3ConfigSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    category_key: str
    display_name: str
    status: str
    revision: int
    contract_hash: str
    projected_revision_id: int
    candidate_count: int
    media_penalty_enabled: bool
    updated_at: Any


class V3ConfigDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    category_key: str
    display_name: str
    status: str
    revision: int
    contract_hash: str
    projected_revision_id: int
    candidate_count: int
    mechanism_profile: dict[str, Any]
    contract: dict[str, Any]
    classification_map: dict[str, Any]
    subcategory_dimensions: dict[str, Any]
    dimension_deduction_rules: dict[str, Any]
    media_penalty_enabled: bool
    created_by: str
    created_at: Any
    updated_at: Any
    semantic_tag_applicability: dict[str, Any] | None = None


class LevelScaleWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expected_contract_hash: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    level_scale: dict[str, Any]
    redline_hit_level: str | None = Field(default=None, pattern=r"^L[1-5]$")


class LevelScaleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str
    revision: int
    contract_hash: str
    level_scale: dict[str, Any] | None
    level_thresholds: list[dict[str, Any]] | None
    resolved_level_scale: dict[str, Any]


class V3RevisionWriteRequest(V3ConfigWriteRequest):
    parent_revision_id: int = Field(ge=1)
    expected_projected_revision: int = Field(ge=1)
    expected_projected_contract_hash: str = Field(min_length=64, max_length=64)


class V3RevisionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    category_key: str
    display_name: str
    status: str
    revision: int
    parent_revision_id: int | None
    contract_hash: str
    mechanism_profile: dict[str, Any]
    contract: dict[str, Any]
    classification_map: dict[str, Any]
    subcategory_dimensions: dict[str, Any]
    dimension_deduction_rules: dict[str, Any]
    media_penalty_enabled: bool
    created_by: str
    created_at: Any
    updated_at: Any


class V3RevisionActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regression_run_id: int = Field(ge=1)
    expected_projected_revision: int = Field(ge=1)
    expected_projected_contract_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=1000)


class V3RevisionActivationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str
    activated_revision: V3RevisionDetail
    regression_run_id: int
    regression_evidence: dict[str, Any]
    mechanism_refresh: dict[str, Any]
    audit_event_key: str


# --------------------------------------------------------------------------- #
# Helpers (pure)
# --------------------------------------------------------------------------- #


def _coded_400(exc: Exception) -> HTTPException:
    """Wrap a framework ``ValueError`` (with ``.code``) as an HTTP 400."""
    return HTTPException(
        status_code=400,
        detail={
            "code": getattr(exc, "code", "invalid_input"),
            "message": str(exc),
        },
    )


def _collect_validation_errors(
    payload: V3ConfigWriteRequest,
    db: Session | None = None,
) -> list[ValidationErrorItem]:
    """Run every reused validator, aggregating failures with stable codes.

    Order: contract (delegates redline_policy) → an ``evaluate_redlines`` smoke
    check that the policy is actually consumable → classification_map (against
    the contract's track keys) → each subcategory-dimensions config.  Each
    validator's ``ValueError`` is captured with its ``.code``; nothing escapes
    as a 500.  An empty list means the artifacts are all valid.
    """
    try:
        validate_mechanism_artifacts(
            payload.contract,
            payload.classification_map,
            payload.subcategory_dimensions,
            db=db,
        )
    except MechanismProfileError as exc:
        return [
            ValidationErrorItem(
                target=exc.target,
                code=exc.code,
                message=str(exc),
            )
        ]
    return []


def _guard_valid(payload: V3ConfigWriteRequest, db: Session | None = None) -> str:
    """Raise a coded HTTP 400 (aggregating every failure) if any artifact fails.

    The detail carries the first failure's ``code``/``message`` plus the full
    ``errors`` list so the caller can render every problem at once.  No write
    happens unless this returns cleanly.
    """
    try:
        return validate_mechanism_artifacts(
            payload.contract,
            payload.classification_map,
            payload.subcategory_dimensions,
            db=db,
        )
    except MechanismProfileError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": exc.code,
                "message": str(exc),
                "errors": [
                    ValidationErrorItem(
                        target=exc.target,
                        code=exc.code,
                        message=str(exc),
                    ).model_dump()
                ],
            },
        ) from exc


def _candidate_count(db: Session, category_key: str) -> int:
    return int(
        db.scalar(
            select(func.count(CategoryEvaluationV3Revision.id)).where(
                CategoryEvaluationV3Revision.category_key == category_key,
                CategoryEvaluationV3Revision.status == "candidate",
            )
        )
        or 0
    )


def _projected_revision(
    db: Session,
    row: CategoryEvaluationV3Config,
) -> CategoryEvaluationV3Revision:
    if row.projected_revision_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "projected_revision_missing",
                "message": "运行时合同缺少版本投影，请先完成数据库迁移或修复",
            },
        )
    projected = db.get(CategoryEvaluationV3Revision, row.projected_revision_id)
    if projected is None or projected.category_key != row.category_key:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "projected_revision_invalid",
                "message": "运行时合同版本投影无效，请先完成数据库修复",
            },
        )
    return projected


def _summary(db: Session, row: CategoryEvaluationV3Config) -> V3ConfigSummary:
    projected = _projected_revision(db, row)
    return V3ConfigSummary(
        id=row.id,
        category_key=row.category_key,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        contract_hash=row.contract_hash,
        projected_revision_id=projected.id,
        candidate_count=_candidate_count(db, row.category_key),
        media_penalty_enabled=row.media_penalty_enabled,
        updated_at=row.updated_at,
    )


def _detail(db: Session, row: CategoryEvaluationV3Config) -> V3ConfigDetail:
    projected = _projected_revision(db, row)
    contract = json.loads(row.contract_json)
    semantic_tag_applicability: dict[str, Any] | None = None
    active_tag_contract = db.scalar(
        select(TagDemandContract)
        .where(
            TagDemandContract.contract_key == PLATFORM_SEMANTIC_CONTRACT_KEY,
            TagDemandContract.status == "active",
        )
        .order_by(TagDemandContract.version.desc(), TagDemandContract.id.desc())
        .limit(1)
    )
    if active_tag_contract is not None:
        try:
            tag_definition = validate_tag_demand_contract(
                json.loads(active_tag_contract.definition_json)
            )
            fields = tag_definition.category_applicability.get(row.category_key)
            if fields is not None:
                counts: dict[str, int] = {}
                for status in fields.values():
                    counts[status] = counts.get(status, 0) + 1
                semantic_tag_applicability = {
                    "contract_id": active_tag_contract.id,
                    "contract_version": active_tag_contract.version,
                    "contract_hash": active_tag_contract.contract_hash,
                    "field_counts": counts,
                    "fields": dict(fields),
                }
        except (json.JSONDecodeError, SemanticTagContractError):
            semantic_tag_applicability = {
                "contract_id": active_tag_contract.id,
                "contract_version": active_tag_contract.version,
                "error": "平台语义标签需求合同无效",
            }
    return V3ConfigDetail(
        id=row.id,
        category_key=row.category_key,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        contract_hash=row.contract_hash,
        projected_revision_id=projected.id,
        candidate_count=_candidate_count(db, row.category_key),
        mechanism_profile=asdict(describe_mechanism_profile(contract)),
        contract=contract,
        classification_map=json.loads(row.classification_map_json),
        subcategory_dimensions=json.loads(row.subcategory_dimensions_json),
        dimension_deduction_rules=json.loads(
            row.dimension_deduction_rules_json or "{}"
        ),
        media_penalty_enabled=row.media_penalty_enabled,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        semantic_tag_applicability=semantic_tag_applicability,
    )


def _revision_detail(row: CategoryEvaluationV3Revision) -> V3RevisionDetail:
    contract = json.loads(row.contract_json)
    return V3RevisionDetail(
        id=row.id,
        category_key=row.category_key,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        parent_revision_id=row.parent_revision_id,
        contract_hash=row.contract_hash,
        mechanism_profile=asdict(describe_mechanism_profile(contract)),
        contract=contract,
        classification_map=json.loads(row.classification_map_json),
        subcategory_dimensions=json.loads(row.subcategory_dimensions_json),
        dimension_deduction_rules=json.loads(
            row.dimension_deduction_rules_json or "{}"
        ),
        media_penalty_enabled=row.media_penalty_enabled,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _immutable_projection_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "active_projection_immutable",
            "message": "现役合同只能通过已批准机制发布原子切换，请先创建候选版本",
        },
    )


def _load(db: Session, category_key: str) -> CategoryEvaluationV3Config:
    row = db.scalar(
        select(CategoryEvaluationV3Config).where(
            CategoryEvaluationV3Config.category_key == category_key
        )
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "v3_config_not_found",
                "message": f"未找到 category_key={category_key} 的 v3 配置",
            },
        )
    return row


def _level_scale_response(row: CategoryEvaluationV3Config) -> LevelScaleResponse:
    contract = json.loads(row.contract_json)
    return LevelScaleResponse(
        category_key=row.category_key,
        revision=row.revision,
        contract_hash=row.contract_hash,
        level_scale=contract.get("level_scale"),
        level_thresholds=contract.get("level_thresholds"),
        resolved_level_scale=resolve_level_scale(contract),
    )


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #


def build_category_evaluation_v3_config_router(
    require_user: Callable[..., Any],
    require_admin: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the isolated v3-config CRUD + validation router.

    ``require_user`` is the login dependency (identical pattern to the other
    isolated routers); every endpoint depends on it.  Persistence is limited to
    the standalone ``category_evaluation_v3_configs`` table; no queueing,
    publishing, model calls or worker interaction happens here.
    """
    admin_dependency = require_admin or require_user
    router = APIRouter(
        prefix="/api/category-evaluation/v3-config",
        tags=["category-evaluation-v3-config"],
    )

    @router.get(
        "/{category_key}/level-scale", response_model=LevelScaleResponse
    )
    def get_level_scale(
        category_key: str,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> LevelScaleResponse:
        """Read the raw and normalized level contract with concurrency guards."""
        return _level_scale_response(_load(db, category_key))

    @router.put(
        "/{category_key}/level-scale", response_model=LevelScaleResponse
    )
    def update_level_scale(
        category_key: str,
        payload: LevelScaleWriteRequest,
        user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> LevelScaleResponse:
        """Legacy mutation is closed; edit the full artifact into a candidate."""
        _load(db, category_key)
        raise _immutable_projection_error()

    @router.get("/", response_model=dict[str, Any])
    def list_configs(
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        """List every persisted v3 config (summary fields only)."""
        rows = db.scalars(
            select(CategoryEvaluationV3Config).order_by(
                CategoryEvaluationV3Config.updated_at.desc(),
                CategoryEvaluationV3Config.id.desc(),
            )
        ).all()
        return {"items": [_summary(db, row).model_dump() for row in rows]}

    @router.get("/profiles", response_model=dict[str, Any])
    def list_mechanism_profiles(
        _user: Any = Depends(require_user),
    ) -> dict[str, Any]:
        """Expose controlled editor/execution capabilities without plugin code."""
        return {"items": mechanism_profile_catalog()}

    @router.get("/{category_key}", response_model=V3ConfigDetail)
    def get_config(
        category_key: str,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Fetch one full v3 config (contract + map + dimensions)."""
        return _detail(db, _load(db, category_key))

    @router.post("/", response_model=V3ConfigDetail, status_code=201)
    def create_config(
        payload: V3ConfigWriteRequest,
        user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Create a new v3 config — validate first, then persist as a draft.

        Every artifact is validated by the reused framework validators before
        anything lands; a failure becomes a coded 400 and nothing is written.
        A duplicate ``category_key`` is a coded 409.
        """
        profile_type = _guard_valid(payload, db)
        existing = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == payload.category_key
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "v3_config_duplicate_key",
                    "message": f"category_key={payload.category_key} 已存在",
                },
            )
        created_by = getattr(user, "username", None)
        if not isinstance(created_by, str) or not created_by:
            created_by = "system"
        row = CategoryEvaluationV3Config(
            category_key=payload.category_key,
            display_name=payload.display_name,
            status="draft",
            contract_json=canonical_json(payload.contract),
            classification_map_json=canonical_json(payload.classification_map),
            subcategory_dimensions_json=canonical_json(
                payload.subcategory_dimensions
            ),
            dimension_deduction_rules_json=canonical_json(
                extract_profile_rule_mirror(
                    profile_type, payload.subcategory_dimensions, payload.contract
                )
            ),
            media_penalty_enabled=profile_media_penalty_enabled(
                profile_type, payload.contract
            ),
            revision=1,
            contract_hash=canonical_contract_hash(payload.contract),
            created_by=created_by,
        )
        db.add(row)
        db.flush()
        ensure_projected_revision(db, row)
        db.commit()
        db.refresh(row)
        return _detail(db, row)

    @router.get("/{category_key}/revisions", response_model=dict[str, Any])
    def list_revisions(
        category_key: str,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        projected = _load(db, category_key)
        projected_revision = _projected_revision(db, projected)
        rows = db.scalars(
            select(CategoryEvaluationV3Revision)
            .where(CategoryEvaluationV3Revision.category_key == category_key)
            .order_by(
                CategoryEvaluationV3Revision.revision.desc(),
                CategoryEvaluationV3Revision.id.desc(),
            )
        ).all()
        return {
            "projected_revision_id": projected_revision.id,
            "candidate_count": sum(row.status == "candidate" for row in rows),
            "items": [_revision_detail(row).model_dump() for row in rows],
        }

    @router.get(
        "/{category_key}/revisions/{revision}",
        response_model=V3RevisionDetail,
    )
    def get_revision(
        category_key: str,
        revision: int,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3RevisionDetail:
        _load(db, category_key)
        row = db.scalar(
            select(CategoryEvaluationV3Revision).where(
                CategoryEvaluationV3Revision.category_key == category_key,
                CategoryEvaluationV3Revision.revision == revision,
            )
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "v3_revision_not_found",
                    "message": f"未找到 {category_key} revision={revision}",
                },
            )
        return _revision_detail(row)

    @router.post(
        "/{category_key}/revisions/{revision}/activate",
        response_model=V3RevisionActivationResponse,
    )
    def activate_revision(
        category_key: str,
        revision: int,
        payload: V3RevisionActivationRequest,
        user: Any = Depends(admin_dependency),
        db: Session = Depends(get_db),
    ) -> V3RevisionActivationResponse:
        """Atomically activate a candidate after a completed passing regression."""
        projected = _load(db, category_key)
        candidate = db.scalar(
            select(CategoryEvaluationV3Revision).where(
                CategoryEvaluationV3Revision.category_key == category_key,
                CategoryEvaluationV3Revision.revision == revision,
            )
        )
        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "v3_revision_not_found",
                    "message": f"未找到 {category_key} revision={revision}",
                },
            )
        regression_run = db.get(BaselineRegressionRun, payload.regression_run_id)
        try:
            report = evaluate_candidate_release_gate(
                db,
                category_key=category_key,
                projected=projected,
                candidate=candidate,
                regression_run=regression_run,
                expected_projected_revision=payload.expected_projected_revision,
                expected_projected_contract_hash=payload.expected_projected_contract_hash,
            )
        except CandidateReleaseGateError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        if report.get("approval_allowed") is not True:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "candidate_quality_gate_failed",
                    "message": "候选回归未通过质量门禁，不能启用",
                    "regressions": report.get("regressions") or [],
                    "report": report,
                },
            )

        actor = getattr(user, "username", None) or "system"
        try:
            if not report.get("idempotent"):
                activate_candidate_revision(
                    db,
                    projected,
                    candidate,
                    actor=actor,
                )
            mechanism_refresh = {
                "category_key": category_key,
                "v3_revision_id": candidate.id,
                "revision": candidate.revision,
                "contract_hash": candidate.contract_hash,
                "regression_run_id": regression_run.id,
            }
            event = append_audit_event(
                db,
                category="category_evaluation_v3",
                action="revision_activated",
                subject_type="category_evaluation_v3_revision",
                subject_id=candidate.id,
                actor=actor,
                payload={
                    "category_key": category_key,
                    "revision": candidate.revision,
                    "regression_run_id": regression_run.id,
                    "note": payload.note.strip(),
                    "gate_report": report,
                },
                event_key=(
                    f"category-evaluation-v3:{category_key}:revision:{candidate.revision}:"
                    "activated"
                ),
            )
            db.commit()
            db.refresh(candidate)
            return V3RevisionActivationResponse(
                category_key=category_key,
                activated_revision=_revision_detail(candidate),
                regression_run_id=regression_run.id,
                regression_evidence=report,
                mechanism_refresh=mechanism_refresh,
                audit_event_key=event.event_key,
            )
        except CategoryEvaluationV3RevisionError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except Exception:
            db.rollback()
            raise

    @router.post(
        "/{category_key}/revisions",
        response_model=V3RevisionDetail,
        status_code=201,
    )
    def append_candidate_revision(
        category_key: str,
        payload: V3RevisionWriteRequest,
        response: Response,
        user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3RevisionDetail:
        if payload.category_key != category_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "category_key_mismatch",
                    "message": "请求体 category_key 必须与路径一致",
                },
            )
        projected = _load(db, category_key)
        actor = getattr(user, "username", None) or "system"
        try:
            revision, created = create_candidate_revision(
                db,
                projected,
                parent_revision_id=payload.parent_revision_id,
                artifacts=RevisionArtifacts(
                    display_name=payload.display_name,
                    contract=payload.contract,
                    classification_map=payload.classification_map,
                    subcategory_dimensions=payload.subcategory_dimensions,
                ),
                expected_projected_revision=payload.expected_projected_revision,
                expected_projected_hash=(
                    payload.expected_projected_contract_hash
                ),
                actor=actor,
            )
        except MechanismProfileError as exc:
            raise _coded_400(exc) from exc
        except CategoryEvaluationV3RevisionError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        db.commit()
        db.refresh(revision)
        if not created:
            response.status_code = 200
        return _revision_detail(revision)

    @router.put("/{category_key}", response_model=V3ConfigDetail)
    def update_config(
        category_key: str,
        payload: V3ConfigWriteRequest,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Legacy replacement is closed; callers must create a candidate."""
        if payload.category_key != category_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "category_key_mismatch",
                    "message": "请求体 category_key 必须与路径一致",
                },
            )
        _load(db, category_key)
        raise _immutable_projection_error()

    @router.put("/{category_key}/status", response_model=V3ConfigDetail)
    def update_status(
        category_key: str,
        payload: dict[str, Any],
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Legacy lifecycle mutation is closed pending an approved release."""
        _load(db, category_key)
        raise _immutable_projection_error()

    @router.post("/{category_key}/validate", response_model=ValidateResponse)
    def validate_existing(
        category_key: str,
        payload: V3ConfigWriteRequest,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> ValidateResponse:
        """Dry-run validate a candidate config — no write.

        Reuses the exact same validation as create/update but never persists;
        returns ``ok`` plus the aggregated coded error list.  The path
        ``category_key`` scopes the call; validation itself is over the body's
        artifacts only.
        """
        errors = _collect_validation_errors(payload, db)
        return ValidateResponse(ok=not errors, errors=errors)

    @router.post("/validate", response_model=ValidateResponse)
    def validate_candidate(
        payload: V3ConfigWriteRequest,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> ValidateResponse:
        """Dry-run validate a candidate config before it has a key — no write.

        Same reused validators as create/update; never persists.  Used by the
        editor before first save (no existing ``category_key`` yet).
        """
        errors = _collect_validation_errors(payload, db)
        return ValidateResponse(ok=not errors, errors=errors)

    return router
