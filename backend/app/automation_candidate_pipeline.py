"""Immutable candidate manifests and paired-regression planning."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .audit import canonical_json


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ImmutableCandidatePackage:
    package_key: str
    manifest: Mapping[str, Any]


def build_immutable_candidate_package(candidate: Mapping[str, Any]) -> ImmutableCandidatePackage:
    if not isinstance(candidate, Mapping):
        raise ValueError("候选包必须是对象")
    required = (
        "category_key",
        "lane_key",
        "mechanism_fingerprint",
        "route_decision",
        "prompt_snapshot",
        "v3_snapshot",
    )
    missing = [key for key in required if key not in candidate]
    if missing:
        raise ValueError("候选包缺少字段：" + ",".join(missing))
    fingerprint = str(candidate["mechanism_fingerprint"])
    if len(fingerprint) != 64:
        raise ValueError("候选包机制指纹无效")
    normalized = {
        "schema_version": "automation-candidate-v1",
        "category_key": str(candidate["category_key"]),
        "lane_key": str(candidate["lane_key"]),
        "mechanism_fingerprint": fingerprint,
        "route_decision": candidate["route_decision"],
        "prompt_snapshot": candidate["prompt_snapshot"],
        "v3_snapshot": candidate["v3_snapshot"],
        "change_reasons": candidate.get("change_reasons", []),
    }
    digest = hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
    return ImmutableCandidatePackage(
        package_key=f"candidate-{digest}",
        manifest=_freeze(normalized),
    )


def build_three_role_regression_plan(
    *,
    candidate_package: ImmutableCandidatePackage,
    sample_roles: Mapping[str, list[int] | tuple[int, ...]],
) -> dict[str, Any]:
    required = ("target_error", "stable_control", "blind_holdout")
    missing = [role for role in required if not sample_roles.get(role)]
    if missing:
        raise ValueError("三角色回归缺少：" + ",".join(missing))
    sample_ids = tuple(
        int(sample_id)
        for role in required
        for sample_id in sample_roles[role]
    )
    if len(set(sample_ids)) != len(sample_ids):
        raise ValueError("三角色回归样本不得重复")
    return {
        "schema_version": "automation-paired-regression-plan-v1",
        "candidate_package_key": candidate_package.package_key,
        "roles": required,
        "sample_ids": sample_ids,
        "blind_holdout_sealed": True,
    }

