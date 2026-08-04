"""ADR-0033 Task 3: isolated CRUD + validation for persisted v3 contracts.

An **isolated** router that gives the ADR-0033 v3 category-evaluation contract
(红线 + 子类目赛道 + 共性/特有维度组 + 分类映射) a persistence + CRUD +
server-side validation surface, so operators can 存 → 读 → 改 → 校验 candidate
configs before any线上接入.

Isolation boundaries (hard constraints — see the task brief):

- It stores into the standalone ``category_evaluation_v3_configs`` table only.
  It shares nothing with the v1 ``EvaluationCategoryProfile`` /
  ``category-pipeline-v1`` pipeline — separate key space, separate CRUD.
- Every write is **validated before it lands** by reusing the existing
  deterministic framework validators (``validate_category_evaluation_contract``
  — which delegates the redline block to ``validate_redline_policy`` — plus
  ``validate_classification_map`` and ``validate_subcategory_dimensions``).  No
  validation logic is re-implemented here.
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
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .category_evaluation_contract import (
    canonical_contract_hash,
    validate_category_evaluation_contract,
)
from .database import get_db
from .dimension_composition import validate_subcategory_dimensions
from .dimension_deduction_bridge import extract_dimension_deduction_rules
from .dimension_schema_registry import canonical_json
from .models import CategoryEvaluationV3Config
from .redline_policy import evaluate_redlines
from .subcategory_resolver import validate_classification_map


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
    contract: dict[str, Any]
    classification_map: dict[str, Any]
    subcategory_dimensions: dict[str, Any]
    dimension_deduction_rules: dict[str, Any]
    media_penalty_enabled: bool
    created_by: str
    created_at: Any
    updated_at: Any


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


def _extract_track_keys(contract: Any) -> set[str]:
    """Best-effort extraction of ``track_classification.tracks[*].key``.

    A malformed contract yields an empty set; the contract validator owns the
    authoritative check, this only feeds the classification-map validator the
    keys it needs to cross-check mapping targets.
    """
    if not isinstance(contract, dict):
        return set()
    track_classification = contract.get("track_classification")
    if not isinstance(track_classification, dict):
        return set()
    tracks = track_classification.get("tracks")
    if not isinstance(tracks, list):
        return set()
    return {
        track["key"]
        for track in tracks
        if isinstance(track, dict) and isinstance(track.get("key"), str)
    }


def _collect_validation_errors(
    payload: V3ConfigWriteRequest,
) -> list[ValidationErrorItem]:
    """Run every reused validator, aggregating failures with stable codes.

    Order: contract (delegates redline_policy) → an ``evaluate_redlines`` smoke
    check that the policy is actually consumable → classification_map (against
    the contract's track keys) → each subcategory-dimensions config.  Each
    validator's ``ValueError`` is captured with its ``.code``; nothing escapes
    as a 500.  An empty list means the artifacts are all valid.
    """
    errors: list[ValidationErrorItem] = []

    contract_ok = True
    try:
        validate_category_evaluation_contract(payload.contract)
    except ValueError as exc:
        contract_ok = False
        errors.append(ValidationErrorItem(
            target="contract",
            code=getattr(exc, "code", "invalid_contract"),
            message=str(exc),
        ))

    # Confirm the redline policy inside the contract is actually runnable by the
    # deterministic pre-filter (not just structurally valid).  Runs on a trivial
    # precheck; a hit/miss is irrelevant, only that it does not raise.
    if contract_ok:
        try:
            evaluate_redlines(
                {"production_fields": {"reason": []}},
                policy=payload.contract["redline_policy"],
            )
        except ValueError as exc:
            errors.append(ValidationErrorItem(
                target="redline_policy",
                code=getattr(exc, "code", "invalid_redline_policy"),
                message=str(exc),
            ))

    # Cross-check the classification map against the contract's track keys when
    # the contract parsed; otherwise fall back to whatever keys are extractable.
    valid_track_keys = _extract_track_keys(payload.contract)
    try:
        validate_classification_map(
            payload.classification_map, valid_track_keys=valid_track_keys
        )
    except ValueError as exc:
        errors.append(ValidationErrorItem(
            target="classification_map",
            code=getattr(exc, "code", "invalid_classification_map"),
            message=str(exc),
        ))

    if not isinstance(payload.subcategory_dimensions, dict):
        errors.append(ValidationErrorItem(
            target="subcategory_dimensions",
            code="subcategory_dimensions_not_object",
            message="subcategory_dimensions 必须是 {track_key: config} 对象",
        ))
    else:
        for track_key, config in payload.subcategory_dimensions.items():
            try:
                validate_subcategory_dimensions(config)
            except ValueError as exc:
                errors.append(ValidationErrorItem(
                    target=f"subcategory_dimensions.{track_key}",
                    code=getattr(exc, "code", "invalid_subcategory_dimensions"),
                    message=str(exc),
                ))

    return errors


def _guard_valid(payload: V3ConfigWriteRequest) -> None:
    """Raise a coded HTTP 400 (aggregating every failure) if any artifact fails.

    The detail carries the first failure's ``code``/``message`` plus the full
    ``errors`` list so the caller can render every problem at once.  No write
    happens unless this returns cleanly.
    """
    errors = _collect_validation_errors(payload)
    if errors:
        first = errors[0]
        raise HTTPException(
            status_code=400,
            detail={
                "code": first.code,
                "message": first.message,
                "errors": [error.model_dump() for error in errors],
            },
        )


def _summary(row: CategoryEvaluationV3Config) -> V3ConfigSummary:
    return V3ConfigSummary(
        id=row.id,
        category_key=row.category_key,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        contract_hash=row.contract_hash,
        media_penalty_enabled=row.media_penalty_enabled,
        updated_at=row.updated_at,
    )


def _detail(row: CategoryEvaluationV3Config) -> V3ConfigDetail:
    return V3ConfigDetail(
        id=row.id,
        category_key=row.category_key,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        contract_hash=row.contract_hash,
        contract=json.loads(row.contract_json),
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


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #


def build_category_evaluation_v3_config_router(
    require_user: Callable[..., Any],
) -> APIRouter:
    """Build the isolated v3-config CRUD + validation router.

    ``require_user`` is the login dependency (identical pattern to the other
    isolated routers); every endpoint depends on it.  Persistence is limited to
    the standalone ``category_evaluation_v3_configs`` table; no queueing,
    publishing, model calls or worker interaction happens here.
    """
    router = APIRouter(
        prefix="/api/category-evaluation/v3-config",
        tags=["category-evaluation-v3-config"],
    )

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
        return {"items": [_summary(row).model_dump() for row in rows]}

    @router.get("/{category_key}", response_model=V3ConfigDetail)
    def get_config(
        category_key: str,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Fetch one full v3 config (contract + map + dimensions)."""
        return _detail(_load(db, category_key))

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
        _guard_valid(payload)
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
                extract_dimension_deduction_rules(payload.subcategory_dimensions)
            ),
            media_penalty_enabled=payload.contract["common_modifiers"][
                "media_type_penalty"
            ].get("enabled", True),
            revision=1,
            contract_hash=canonical_contract_hash(payload.contract),
            created_by=created_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _detail(row)

    @router.put("/{category_key}", response_model=V3ConfigDetail)
    def update_config(
        category_key: str,
        payload: V3ConfigWriteRequest,
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Replace an existing v3 config — validate first, then bump revision.

        The body's ``category_key`` must match the path.  This updates the
        display name and the three artifacts, re-validates them, bumps
        ``revision`` and recomputes ``contract_hash``.  Lifecycle ``status``
        is not touched here — change it via ``PUT /{category_key}/status``.
        """
        if payload.category_key != category_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "category_key_mismatch",
                    "message": "请求体 category_key 必须与路径一致",
                },
            )
        _guard_valid(payload)
        row = _load(db, category_key)
        row.display_name = payload.display_name
        row.contract_json = canonical_json(payload.contract)
        row.classification_map_json = canonical_json(payload.classification_map)
        row.subcategory_dimensions_json = canonical_json(
            payload.subcategory_dimensions
        )
        row.dimension_deduction_rules_json = canonical_json(
            extract_dimension_deduction_rules(payload.subcategory_dimensions)
        )
        row.media_penalty_enabled = payload.contract["common_modifiers"][
            "media_type_penalty"
        ].get("enabled", True)
        row.revision = row.revision + 1
        row.contract_hash = canonical_contract_hash(payload.contract)
        db.commit()
        db.refresh(row)
        return _detail(row)

    @router.put("/{category_key}/status", response_model=V3ConfigDetail)
    def update_status(
        category_key: str,
        payload: dict[str, Any],
        _user: Any = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> V3ConfigDetail:
        """Change only a config's lifecycle status (draft/active/retired).

        Retiring a config is done here (there is deliberately no DELETE, so a
        referenced config can never be silently removed).  Does not bump the
        contract revision or re-validate artifacts.
        """
        status = payload.get("status")
        if status not in ("draft", "active", "retired"):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_status",
                    "message": "status 必须是 draft/active/retired 之一",
                },
            )
        row = _load(db, category_key)
        row.status = status
        db.commit()
        db.refresh(row)
        return _detail(row)

    @router.post("/{category_key}/validate", response_model=ValidateResponse)
    def validate_existing(
        category_key: str,
        payload: V3ConfigWriteRequest,
        _user: Any = Depends(require_user),
    ) -> ValidateResponse:
        """Dry-run validate a candidate config — no write, no DB access.

        Reuses the exact same validation as create/update but never persists;
        returns ``ok`` plus the aggregated coded error list.  The path
        ``category_key`` scopes the call; validation itself is over the body's
        artifacts only.
        """
        errors = _collect_validation_errors(payload)
        return ValidateResponse(ok=not errors, errors=errors)

    @router.post("/validate", response_model=ValidateResponse)
    def validate_candidate(
        payload: V3ConfigWriteRequest,
        _user: Any = Depends(require_user),
    ) -> ValidateResponse:
        """Dry-run validate a candidate config before it has a key — no write.

        Same reused validators as create/update; never persists.  Used by the
        editor before first save (no existing ``category_key`` yet).
        """
        errors = _collect_validation_errors(payload)
        return ValidateResponse(ok=not errors, errors=errors)

    return router
