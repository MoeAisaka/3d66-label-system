"""Versioned category/pipeline lanes for safe automation dispatch.

This module intentionally contains only deterministic, side-effect-free lane
contracts. Persistence and dispatch are layered on top in later tasks.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping


PipelineKind = Literal["incremental", "baseline"]
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_mechanism_fingerprint(
    *,
    model_snapshot: Mapping[str, Any],
    call_a_snapshot: Mapping[str, Any],
    call_b_snapshot: Mapping[str, Any],
    dimension_contract: Mapping[str, Any],
    v3_rules: Mapping[str, Any],
    scoring_engine_version: str,
    level_mapping: Mapping[str, Any],
) -> str:
    """Return a stable SHA-256 over every mechanism input that affects output."""

    if not scoring_engine_version or not scoring_engine_version.strip():
        raise ValueError("scoring_engine_version must be non-empty")
    return _sha256(
        {
            "model_snapshot": dict(model_snapshot),
            "call_a_snapshot": dict(call_a_snapshot),
            "call_b_snapshot": dict(call_b_snapshot),
            "dimension_contract": dict(dimension_contract),
            "v3_rules": dict(v3_rules),
            "scoring_engine_version": scoring_engine_version,
            "level_mapping": dict(level_mapping),
        }
    )


def build_lane_key(
    *,
    category_key: str,
    pipeline_kind: PipelineKind,
    generation: int,
    mechanism_fingerprint: str,
    route_key: str,
) -> str:
    """Build a readable, deterministic identity for one isolated automation lane."""

    _validate_lane_identity(
        category_key=category_key,
        pipeline_kind=pipeline_kind,
        generation=generation,
        mechanism_fingerprint=mechanism_fingerprint,
        route_key=route_key,
    )
    digest = _sha256(
        {
            "category_key": category_key,
            "pipeline_kind": pipeline_kind,
            "generation": generation,
            "mechanism_fingerprint": mechanism_fingerprint,
            "route_key": route_key,
        }
    )
    return f"lane:{category_key}:{pipeline_kind}:g{generation}:{route_key}:{digest}"


def validate_lane_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Validate the immutable fields required before a case can join a lane."""

    missing = [
        field
        for field in (
            "category_key",
            "pipeline_kind",
            "generation",
            "mechanism_fingerprint",
            "route_key",
        )
        if field not in snapshot
    ]
    if missing:
        raise ValueError(f"lane snapshot missing required fields: {', '.join(missing)}")
    _validate_lane_identity(
        category_key=snapshot["category_key"],
        pipeline_kind=snapshot["pipeline_kind"],
        generation=snapshot["generation"],
        mechanism_fingerprint=snapshot["mechanism_fingerprint"],
        route_key=snapshot["route_key"],
    )


def _validate_lane_identity(
    *,
    category_key: Any,
    pipeline_kind: Any,
    generation: Any,
    mechanism_fingerprint: Any,
    route_key: Any,
) -> None:
    if not isinstance(category_key, str) or not category_key.strip():
        raise ValueError("category_key must be non-empty")
    if pipeline_kind not in ("incremental", "baseline"):
        raise ValueError("pipeline_kind must be 'incremental' or 'baseline'")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("generation must be a positive integer")
    if not isinstance(mechanism_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
        mechanism_fingerprint
    ):
        raise ValueError("mechanism_fingerprint must be a lowercase SHA-256 hex digest")
    if not isinstance(route_key, str) or not route_key.strip():
        raise ValueError("route_key must be non-empty")
