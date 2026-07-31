from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Literal

from .dimension_route_registry import (
    ROUTE_POLICY_DEFINITION_FORMAT_VERSION,
)
from .dimension_schema_registry import canonical_hash


class DimensionRouteContractError(ValueError):
    """Raised when a frozen route policy or schema set is incomplete."""


def _path(payload: dict[str, Any], dotted_path: str) -> object:
    current: object = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _frozen_schema_index(
    frozen_schemas: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for schema in frozen_schemas:
        key = schema.get("schema_key")
        version = schema.get("version")
        definition = schema.get("definition")
        stored_hash = schema.get("canonical_hash")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(version, str)
            or not version
            or not isinstance(definition, dict)
            or not isinstance(stored_hash, str)
            or stored_hash != canonical_hash(definition)
        ):
            raise DimensionRouteContractError(
                "冻结 DimensionSchema 身份、定义或规范哈希无效"
            )
        identity = (key, version)
        if identity in index:
            raise DimensionRouteContractError("冻结 Schema 集合包含重复身份")
        index[identity] = schema
    return index


def _validate_policy(
    policy: dict[str, Any],
    frozen_index: dict[tuple[str, str], dict[str, Any]],
) -> None:
    if (
        policy.get("format_version")
        != ROUTE_POLICY_DEFINITION_FORMAT_VERSION
    ):
        raise DimensionRouteContractError("未知的维度路由策略定义版本")
    routes = policy.get("family_routes")
    if not isinstance(routes, dict) or set(routes) != {
        "space",
        "product",
        "graphic",
        "intent",
        "common",
    }:
        raise DimensionRouteContractError("路由策略的候选素材族集合不完整")
    for family_key, route in routes.items():
        if not isinstance(route, dict):
            raise DimensionRouteContractError(
                f"素材族 {family_key} 的路由定义无效"
            )
        schema_ref = route.get("schema_ref")
        if not isinstance(schema_ref, dict):
            raise DimensionRouteContractError(
                f"素材族 {family_key} 未冻结 DimensionSchema 引用"
            )
        identity = (
            schema_ref.get("schema_key"),
            schema_ref.get("version"),
        )
        schema = frozen_index.get(identity)
        if schema is None:
            raise DimensionRouteContractError(
                f"素材族 {family_key} 命中了未冻结的 DimensionSchema"
            )
        if (
            schema_ref.get("canonical_hash") != schema["canonical_hash"]
            or schema_ref.get("family_key") != schema.get("family_key")
            or schema_ref.get("status") != schema.get("status")
        ):
            raise DimensionRouteContractError(
                f"素材族 {family_key} 的冻结 Schema 身份不一致"
            )


def _route_decision(
    *,
    policy: dict[str, Any],
    family_key: str,
    status: str,
    reason: str,
    confidence: float,
    needs_review: bool,
    unassessable_reason: str | None = None,
) -> dict[str, Any]:
    route = policy["family_routes"][family_key]
    schema_ref = (
        deepcopy(route["schema_ref"])
        if status != "unassessable"
        else None
    )
    return {
        "schema_version": "dimension-route-decision-v1",
        "policy_key": policy["policy_key"],
        "policy_version": policy["policy_version"],
        "policy_hash": canonical_hash(policy),
        "status": status,
        "family_key": family_key,
        "resolved_schema_family_key": (
            schema_ref["family_key"] if schema_ref is not None else None
        ),
        "dimension_schema": schema_ref,
        "route_reason": reason,
        "route_confidence": round(max(0.0, min(confidence, 1.0)), 4),
        "needs_review": needs_review,
        "unassessable_reason": unassessable_reason,
    }


def resolve_dimension_route(
    precheck: dict[str, Any],
    *,
    frozen_policy: dict[str, Any],
    frozen_schemas: list[dict[str, Any]],
    execution_context: Literal["calibration", "production"],
) -> dict[str, Any]:
    """Resolve one A-stage output using only the frozen policy and schema set."""
    frozen_index = _frozen_schema_index(frozen_schemas)
    _validate_policy(frozen_policy, frozen_index)
    if (
        frozen_policy.get("activation_scope") == "calibration_only"
        and execution_context != "calibration"
    ):
        raise DimensionRouteContractError(
            "候选路由策略仅允许用于人工校准，不得进入生产评测"
        )

    severity = _path(precheck, "image_quality.quality_severity")
    if severity == "unusable":
        return _route_decision(
            policy=frozen_policy,
            family_key="common",
            status="unassessable",
            reason="image_unusable",
            confidence=1.0,
            needs_review=True,
            unassessable_reason="image_unusable",
        )
    if severity not in {
        "normal",
        "slight",
        "moderate",
        "severe",
        "uncertain",
    }:
        return _route_decision(
            policy=frozen_policy,
            family_key="common",
            status="core_fallback",
            reason="invalid_or_missing_quality_severity",
            confidence=0.0,
            needs_review=True,
        )

    scope_status = _path(precheck, "classification.scope_status")
    primary_category = _path(precheck, "classification.primary_category")
    primary_confidence = _path(
        precheck, "classification.primary_confidence"
    )
    scene_scope = _path(precheck, "scene_scope.type")
    white_background = _path(
        precheck, "media_form.white_background_product.status"
    )
    model_needs_review = precheck.get("needs_review")
    if (
        scope_status not in {"in_scope", "boundary", "out_of_scope"}
        or not isinstance(primary_category, str)
        or not primary_category
        or not isinstance(primary_confidence, (int, float))
        or isinstance(primary_confidence, bool)
        or not math.isfinite(float(primary_confidence))
        or not 0.0 <= float(primary_confidence) <= 1.0
        or scene_scope
        not in {
            "full_space",
            "partial_space",
            "detail_closeup",
            "object_only",
            "uncertain",
        }
        or white_background not in {"yes", "no", "uncertain"}
        or not isinstance(model_needs_review, bool)
    ):
        return _route_decision(
            policy=frozen_policy,
            family_key="common",
            status="core_fallback",
            reason="invalid_or_missing_route_input",
            confidence=0.0,
            needs_review=True,
        )
    confidence = float(primary_confidence)
    category_family = frozen_policy["category_family_map"].get(
        primary_category
    )
    product_signal = (
        white_background
        == frozen_policy["product_signals"][
            "white_background_product_status"
        ]
        or scene_scope
        in set(frozen_policy["product_signals"]["scene_scope_types"])
    )

    if category_family in {"graphic", "intent"}:
        return _route_decision(
            policy=frozen_policy,
            family_key=category_family,
            status="core_fallback",
            reason=f"{category_family}_pack_not_ready",
            confidence=confidence,
            needs_review=True,
        )
    if category_family == "space" and product_signal:
        return _route_decision(
            policy=frozen_policy,
            family_key="common",
            status="core_fallback",
            reason="space_product_signal_conflict",
            confidence=min(confidence, 0.5),
            needs_review=True,
        )
    if category_family == "product" or product_signal:
        return _route_decision(
            policy=frozen_policy,
            family_key="product",
            status="resolved",
            reason=(
                "controlled_product_category"
                if category_family == "product"
                else "product_media_signal"
            ),
            confidence=max(confidence, 0.9 if product_signal else 0.0),
            needs_review=(
                model_needs_review
                or white_background == "uncertain"
                or scene_scope == "uncertain"
            ),
        )
    if category_family == "space" and scope_status in {
        "in_scope",
        "boundary",
    }:
        return _route_decision(
            policy=frozen_policy,
            family_key="space",
            status="resolved",
            reason="controlled_space_category",
            confidence=confidence,
            needs_review=(
                model_needs_review
                or scope_status == "boundary"
            ),
        )
    return _route_decision(
        policy=frozen_policy,
        family_key="common",
        status="core_fallback",
        reason="unknown_family_core_fallback",
        confidence=confidence,
        needs_review=True,
    )
