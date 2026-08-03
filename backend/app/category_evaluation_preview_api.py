"""ADR-0033 Phase 4-preview: read-only + dry-run category-evaluation API.

An **isolated** router that exposes the already-assembled inspiration-image v3
contract and the end-to-end ``evaluate_one`` orchestrator to the frontend for
preview / integration work.  It is deliberately side-effect-free:

- **Read-only**: the contract endpoint just calls the seed's ``build_*``
  functions and returns their pure output.
- **Dry-run**: the evaluate endpoint runs the pure ``evaluate_one`` chain over
  a request-supplied precheck + simulated 调用B grades and returns the result.
- **No production side effects**: no DB writes, no queue, no publish, no model
  calls, no touching of the frozen scoring path or the worker.  Every handler
  is a pure computation over the request body plus the frozen seed config.

This module provides only a router *factory*
(``build_category_evaluation_preview_router``); wiring it into the application
(``include_router``) is done separately so no existing file is modified here.

Module ``ValueError`` subclasses raised by the reused framework layers
(``CategoryEvaluationContractError`` / ``SubcategoryResolverError`` /
``DimensionCompositionError`` / grade-bridge / aggregator errors — all carry a
stable ``.code``) are caught and surfaced as HTTP 400 with that ``code``, never
as a 500.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .category_evaluation_contract import (
    CategoryEvaluationContractError,
    validate_category_evaluation_contract,
)
from .dimension_composition import (
    DimensionCompositionError,
    validate_subcategory_dimensions,
)
from .inspiration_category_seed import (
    INSPIRATION_SEED_VERSION,
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
    evaluate_one,
)
from .subcategory_resolver import (
    SubcategoryResolverError,
    validate_classification_map,
)


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


class EvaluatePreviewRequest(BaseModel):
    """Body for the dry-run evaluate endpoint.

    ``precheck`` simulates 调用A's payload; ``common_grades_by_track`` and
    ``specific_grades_by_track`` simulate 调用B's per-track grades.  All are kept
    loosely typed so any malformed content reaches the pure framework layer and
    surfaces as a coded HTTP 400 rather than a pydantic 422 without a ``code``.
    """

    model_config = ConfigDict(extra="forbid")

    precheck: dict[str, Any]
    common_grades_by_track: dict[str, Any] = Field(default_factory=dict)
    specific_grades_by_track: dict[str, Any] = Field(default_factory=dict)


class ValidatePreviewRequest(BaseModel):
    """Body for the validate endpoint; every artifact is optional.

    An artifact that is omitted (``None``) is not validated and its
    corresponding ``*_valid`` flag stays ``True`` (nothing was supplied to
    reject).  ``subcategory_dimensions`` is a ``{track_key: config}`` map, each
    config validated independently.
    """

    model_config = ConfigDict(extra="forbid")

    contract: dict[str, Any] | None = None
    classification_map: dict[str, Any] | None = None
    subcategory_dimensions: dict[str, Any] | None = None


class ContractPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: dict[str, Any]
    classification_map: dict[str, Any]
    subcategory_dimensions: dict[str, Any]
    seed_version: str


class ValidationErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    code: str
    message: str


class ValidatePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_valid: bool
    classification_map_valid: bool
    subcategory_dimensions_valid: bool
    errors: list[ValidationErrorItem]


# --------------------------------------------------------------------------- #
# Helpers (pure)
# --------------------------------------------------------------------------- #


def _extract_track_keys(contract: Any) -> set[str]:
    """Best-effort extraction of track keys from a (possibly broken) contract.

    Reads only the structure needed to collect ``track_classification.tracks[*].key``;
    a malformed contract yields an empty set so the caller can fall back to the
    seed contract's keys.
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


def _seed_track_keys() -> set[str]:
    """The frozen inspiration-image seed contract's track keys."""
    return _extract_track_keys(build_inspiration_v3_contract())


def _coded_400(exc: Exception) -> HTTPException:
    """Wrap a framework ``ValueError`` (with ``.code``) as an HTTP 400."""
    return HTTPException(
        status_code=400,
        detail={
            "code": getattr(exc, "code", "invalid_input"),
            "message": str(exc),
        },
    )


def _run_validation(payload: ValidatePreviewRequest) -> ValidatePreviewResponse:
    """Run each supplied validator, aggregating failures into ``errors``.

    No validator failure escapes as a 500: each is caught and recorded with its
    stable ``.code`` and the corresponding ``*_valid`` flag flipped to ``False``.
    """
    errors: list[ValidationErrorItem] = []

    contract_valid = True
    if payload.contract is not None:
        try:
            validate_category_evaluation_contract(payload.contract)
        except ValueError as exc:
            contract_valid = False
            errors.append(ValidationErrorItem(
                target="contract",
                code=getattr(exc, "code", "invalid_contract"),
                message=str(exc),
            ))

    classification_map_valid = True
    if payload.classification_map is not None:
        valid_track_keys = _extract_track_keys(payload.contract) or _seed_track_keys()
        try:
            validate_classification_map(
                payload.classification_map, valid_track_keys=valid_track_keys
            )
        except ValueError as exc:
            classification_map_valid = False
            errors.append(ValidationErrorItem(
                target="classification_map",
                code=getattr(exc, "code", "invalid_classification_map"),
                message=str(exc),
            ))

    subcategory_dimensions_valid = True
    if payload.subcategory_dimensions is not None:
        for track_key, config in payload.subcategory_dimensions.items():
            try:
                validate_subcategory_dimensions(config)
            except ValueError as exc:
                subcategory_dimensions_valid = False
                errors.append(ValidationErrorItem(
                    target=f"subcategory_dimensions.{track_key}",
                    code=getattr(exc, "code", "invalid_subcategory_dimensions"),
                    message=str(exc),
                ))

    return ValidatePreviewResponse(
        contract_valid=contract_valid,
        classification_map_valid=classification_map_valid,
        subcategory_dimensions_valid=subcategory_dimensions_valid,
        errors=errors,
    )


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #


def build_category_evaluation_preview_router(
    require_user: Callable[..., Any],
) -> APIRouter:
    """Build the isolated read-only + dry-run preview router.

    ``require_user`` is the login dependency (identical pattern to the other
    isolated routers); every endpoint depends on it.  The router performs no
    persistence, queueing, publishing or model calls.
    """
    router = APIRouter(
        prefix="/api/category-evaluation/preview",
        tags=["category-evaluation-preview"],
    )

    @router.get("/inspiration/contract", response_model=ContractPreviewResponse)
    def get_inspiration_contract(
        _user: Any = Depends(require_user),
    ) -> ContractPreviewResponse:
        """Read-only: return the assembled inspiration-image v3 config."""
        return ContractPreviewResponse(
            contract=build_inspiration_v3_contract(),
            classification_map=build_inspiration_classification_map(),
            subcategory_dimensions=build_inspiration_subcategory_dimensions(),
            seed_version=INSPIRATION_SEED_VERSION,
        )

    @router.post("/inspiration/evaluate", response_model=dict[str, Any])
    def evaluate_inspiration(
        payload: EvaluatePreviewRequest,
        _user: Any = Depends(require_user),
    ) -> dict[str, Any]:
        """Dry-run: score one image through the pure ``evaluate_one`` chain.

        Uses the frozen seed contract / classification map / per-track dimension
        configs; the request supplies only the precheck and simulated grades.
        No storage is touched.  Any framework ``ValueError`` (with ``.code``) or
        a missing-track ``KeyError`` becomes an HTTP 400, never a 500.
        """
        try:
            return evaluate_one(
                contract=build_inspiration_v3_contract(),
                classification_map=build_inspiration_classification_map(),
                subcategory_dimensions=build_inspiration_subcategory_dimensions(),
                precheck=payload.precheck,
                common_grades_by_track=payload.common_grades_by_track,
                specific_grades_by_track=payload.specific_grades_by_track,
            )
        except ValueError as exc:
            raise _coded_400(exc) from exc
        except KeyError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_track_config",
                    "message": str(exc),
                },
            ) from exc

    @router.post("/validate", response_model=ValidatePreviewResponse)
    def validate_artifacts(
        payload: ValidatePreviewRequest,
        _user: Any = Depends(require_user),
    ) -> ValidatePreviewResponse:
        """Validate any supplied contract / classification map / dimension configs.

        Runs each validator on the artifacts that were provided and aggregates
        failures into ``errors`` (each with a stable ``code``); no validation
        failure escapes as a 500.
        """
        return _run_validation(payload)

    return router
