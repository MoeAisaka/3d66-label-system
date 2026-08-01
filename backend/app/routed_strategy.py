"""Pure A-after-route resolution for immutable StrategyBundle v3 snapshots."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from .dimension_router import (
    DimensionRouteContractError,
    resolve_dimension_route,
)
from .dimension_schema_registry import canonical_hash, canonical_json
from .models import PromptVersion, SamplingPolicy, StrategyBundle
from .strategy_bundle import (
    EVALUATION_PROFILE_SET_FORMAT_VERSION,
    ROUTED_STRATEGY_SCHEMA_VERSION,
    ROUTE_POLICY_SNAPSHOT_FORMAT_VERSION,
    build_strategy_snapshot,
    validate_routed_bundle_contract,
)


RESOLVED_PROFILE_FORMAT_VERSION = "resolved-evaluation-profile-v1"
ROUTE_INPUT_ALLOWED_PATHS = (
    "classification.scope_status",
    "classification.primary_category",
    "classification.primary_confidence",
    "scene_scope.type",
    "media_form.white_background_product.status",
    "image_quality.quality_severity",
    "needs_review",
)


class RoutedStrategyContractError(ValueError):
    """Raised when a frozen v3 route/profile contract cannot be replayed."""


def _load_json_object(value: str | None, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "")
    except json.JSONDecodeError as exc:
        raise RoutedStrategyContractError(f"{label} 不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise RoutedStrategyContractError(f"{label} 必须是 JSON 对象")
    return payload


def load_frozen_evaluation_profiles(
    bundle: StrategyBundle,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and validate only the immutable snapshots stored on a v3 bundle."""
    if bundle.strategy_schema_version != ROUTED_STRATEGY_SCHEMA_VERSION:
        raise RoutedStrategyContractError(
            "只有 strategy-bundle-v3 支持 A 后路由解析"
        )
    route_policy_snapshot = _load_json_object(
        bundle.dimension_route_policy_snapshot,
        label="冻结路由策略",
    )
    profile_set = _load_json_object(
        bundle.evaluation_profile_set_snapshot,
        label="冻结 EvaluationProfile 集合",
    )
    if (
        route_policy_snapshot.get("format_version")
        != ROUTE_POLICY_SNAPSHOT_FORMAT_VERSION
        or profile_set.get("format_version")
        != EVALUATION_PROFILE_SET_FORMAT_VERSION
    ):
        raise RoutedStrategyContractError("v3 冻结快照版本不受支持")
    expected_policy_id = (
        f"{route_policy_snapshot.get('policy_key')}@"
        f"{route_policy_snapshot.get('version')}"
    )
    if bundle.dimension_route_policy_id != expected_policy_id:
        raise RoutedStrategyContractError(
            "Bundle 与冻结路由策略身份不一致"
        )
    try:
        validate_routed_bundle_contract(
            route_policy_snapshot=route_policy_snapshot,
            profile_set=profile_set,
        )
    except ValueError as exc:
        raise RoutedStrategyContractError(str(exc)) from exc
    return deepcopy(route_policy_snapshot), deepcopy(profile_set)


def _schema_identity(
    schema: dict[str, Any] | None,
) -> tuple[str, str, str] | None:
    if schema is None:
        return None
    key = schema.get("schema_key")
    version = schema.get("version")
    schema_hash = schema.get("canonical_hash")
    if not all(isinstance(item, str) and item for item in (key, version, schema_hash)):
        raise RoutedStrategyContractError("路由结果的 Schema 身份不完整")
    return key, version, schema_hash


def _get_path(payload: dict[str, Any], dotted_path: str) -> object:
    current: object = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(
    payload: dict[str, Any],
    dotted_path: str,
    value: object,
) -> None:
    parts = dotted_path.split(".")
    current = payload
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = deepcopy(value)


def _route_input_snapshot(
    precheck: dict[str, Any],
    policy_definition: dict[str, Any],
) -> dict[str, Any]:
    input_contract = policy_definition.get("input_contract")
    allowed_paths = (
        input_contract.get("allowed_paths")
        if isinstance(input_contract, dict)
        else None
    )
    if allowed_paths != list(ROUTE_INPUT_ALLOWED_PATHS):
        raise RoutedStrategyContractError(
            "冻结路由策略的允许输入字段不符合安全合同"
        )
    snapshot: dict[str, Any] = {}
    for path in allowed_paths:
        _set_path(snapshot, path, _get_path(precheck, path))
    return snapshot


def resolve_frozen_evaluation_profile(
    *,
    bundle: StrategyBundle,
    precheck: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one profile using only A output and the bundle's frozen set."""
    if not isinstance(precheck, dict):
        raise RoutedStrategyContractError("A 预检输出必须是对象")
    route_policy_snapshot, profile_set = (
        load_frozen_evaluation_profiles(bundle)
    )
    policy_definition = route_policy_snapshot["definition"]
    profiles = profile_set["profiles"]
    frozen_schemas = [
        deepcopy(profile["dimension_schema"])
        for profile in profiles.values()
    ]
    try:
        route_decision = resolve_dimension_route(
            precheck,
            frozen_policy=policy_definition,
            frozen_schemas=frozen_schemas,
            execution_context=profile_set["execution_context"],
        )
    except DimensionRouteContractError as exc:
        raise RoutedStrategyContractError(str(exc)) from exc
    if route_decision.get("policy_hash") != route_policy_snapshot.get(
        "canonical_hash"
    ):
        raise RoutedStrategyContractError(
            "路由结果与冻结路由策略哈希不一致"
        )

    selected_profile: dict[str, Any] | None = None
    resolved_identity = _schema_identity(
        route_decision.get("dimension_schema")
    )
    if resolved_identity is not None:
        matches = [
            profile
            for profile in profiles.values()
            if _schema_identity(profile.get("dimension_schema"))
            == resolved_identity
        ]
        if len(matches) != 1:
            raise RoutedStrategyContractError(
                "路由结果必须且只能命中一个冻结 EvaluationProfile"
            )
        selected_profile = matches[0]

    resolution_status = str(route_decision.get("status"))
    blocked_reasons: list[str] = []
    if selected_profile is not None:
        prompt_b = selected_profile.get("prompt_b")
        gate = selected_profile.get("release_gate")
        execution_context = profile_set["execution_context"]
        if resolution_status == "resolved" and prompt_b is None:
            blocked_reasons.append("prompt_contract_missing")
        if execution_context == "production":
            if not isinstance(gate, dict):
                blocked_reasons.append("release_gate_missing")
            else:
                blocked_reasons.extend(
                    str(item)
                    for item in gate.get("blocked_reasons", [])
                    if isinstance(item, str) and item
                )
                if gate.get("publishing_blocked") is not False:
                    blocked_reasons.append("publishing_blocked")
            if selected_profile.get("status") != "published":
                blocked_reasons.append("profile_not_published")
        if resolution_status == "resolved" and blocked_reasons:
            resolution_status = "blocked"

    return {
        "format_version": RESOLVED_PROFILE_FORMAT_VERSION,
        "execution_context": profile_set["execution_context"],
        "route_policy_hash": route_policy_snapshot["canonical_hash"],
        "route_input_snapshot": _route_input_snapshot(
            precheck,
            policy_definition,
        ),
        "route_decision_snapshot": route_decision,
        "resolution_status": resolution_status,
        "resolved_evaluation_profile": deepcopy(selected_profile),
        "needs_review": bool(
            route_decision.get("needs_review")
            or resolution_status in {"blocked", "core_fallback", "unassessable"}
        ),
        "blocked_reasons": sorted(set(blocked_reasons)),
    }


def build_routed_evaluation_strategy_snapshot(
    *,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    sampling_policy: SamplingPolicy | None,
    precheck: dict[str, Any],
    resolution_timestamp: datetime,
) -> str:
    """Build a complete, hash-verified v3 A-after-route result snapshot."""
    if resolution_timestamp.utcoffset() is None:
        raise RoutedStrategyContractError(
            "resolution_timestamp 必须包含时区"
        )
    base_snapshot = json.loads(
        build_strategy_snapshot(
            bundle,
            prompt_a,
            None,
            sampling_policy,
        )
    )
    resolved = resolve_frozen_evaluation_profile(
        bundle=bundle,
        precheck=precheck,
    )
    profile = resolved.pop("resolved_evaluation_profile")
    schema = (
        profile.get("dimension_schema")
        if isinstance(profile, dict)
        else None
    )
    prompt_b = (
        profile.get("prompt_b")
        if isinstance(profile, dict)
        else None
    )
    label_set = (
        profile.get("label_field_set")
        if isinstance(profile, dict)
        else None
    )
    resolution = {
        **resolved,
        "strategy_bundle_id": bundle.id,
        "strategy_bundle_hash": bundle.canonical_hash,
        "resolved_evaluation_profile_key": (
            profile.get("profile_key")
            if isinstance(profile, dict)
            else None
        ),
        "resolved_evaluation_profile_hash": (
            profile.get("canonical_hash")
            if isinstance(profile, dict)
            else None
        ),
        "resolved_dimension_schema_key": (
            schema.get("schema_key")
            if isinstance(schema, dict)
            else None
        ),
        "resolved_dimension_schema_version": (
            schema.get("version")
            if isinstance(schema, dict)
            else None
        ),
        "resolved_dimension_schema_hash": (
            schema.get("canonical_hash")
            if isinstance(schema, dict)
            else None
        ),
        "resolved_dimensions_snapshot": (
            deepcopy(schema.get("definition"))
            if isinstance(schema, dict)
            else None
        ),
        "resolved_prompt_b_id": (
            prompt_b.get("id")
            if isinstance(prompt_b, dict)
            else None
        ),
        "resolved_prompt_b_version": (
            prompt_b.get("version")
            if isinstance(prompt_b, dict)
            else None
        ),
        "resolved_prompt_b_hash": (
            prompt_b.get("canonical_hash")
            if isinstance(prompt_b, dict)
            else None
        ),
        "resolved_label_field_set_hash": (
            label_set.get("canonical_hash")
            if isinstance(label_set, dict)
            else None
        ),
        "resolution_timestamp": resolution_timestamp.isoformat(),
    }
    resolution["resolved_snapshot_hash"] = canonical_hash(resolution)
    base_snapshot.update(resolution)
    return canonical_json(base_snapshot)
