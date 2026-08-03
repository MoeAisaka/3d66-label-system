"""ADR-0033 Phase 3.7 deterministic subcategory resolver (framework-first phase).

The authoritative pipeline is 红线 → **分类器(类目→子类目)** → 子类目 A/B →
维度(共性+特有) → 产出.  This module is the *classifier node*: it maps 调用A's
classification signal (``classification.primary_category`` etc.) onto a v3
contract **subcategory key (track key)** so the aggregator's ``track_key`` can
consume it.

Pure function: no IO, no network, no database, no model calls.  Deterministic
and JSON-serializable for a given input.  It does not touch the worker path,
does not flip L-levels and does not reach the frontend.

The classification map is carried as a standalone optional input (not baked
into the frozen contract) so the frozen contract stays untouched this phase.
"""

from __future__ import annotations

import math
from typing import Any


CLASSIFICATION_MAP_FORMAT_VERSION = "subcategory-classification-map-v1"

_VALID_SCOPE_STATUS = frozenset({"in_scope", "boundary", "out_of_scope"})


class SubcategoryResolverError(ValueError):
    """Raised when the resolver cannot proceed (fail-closed).

    Carries a stable ``code`` for programmatic branching independent of the
    (localized) message text, matching the earlier-phase error convention.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite_unit(value: Any) -> bool:
    """True iff ``value`` is a finite real number in the closed range [0, 1]."""
    return (
        _is_number(value)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def _track_keys(track_classification: Any) -> set[str]:
    """Extract the set of defined track keys from a v3 ``track_classification``.

    Only structurally sound entries are collected; this is a lightweight read,
    not a full contract validation (the aggregator/contract validator own that).
    Returns an empty set for a malformed block so callers fail closed.
    """
    if not isinstance(track_classification, dict):
        return set()
    tracks = track_classification.get("tracks")
    if not isinstance(tracks, list):
        return set()
    keys: set[str] = set()
    for track in tracks:
        if isinstance(track, dict) and isinstance(track.get("key"), str):
            keys.add(track["key"])
    return keys


def validate_classification_map(
    classification_map: Any,
    *,
    valid_track_keys: set[str],
) -> None:
    """Fail-closed validation of the standalone classification map.

    Every mapping target and the ``out_of_scope_subcategory`` target must be a
    track key that is defined in the contract (``valid_track_keys``); a wrong
    ``format_version`` or a malformed ``min_confidence`` fails closed too.
    """
    if not isinstance(classification_map, dict):
        raise SubcategoryResolverError(
            "classification_map_not_object", "classification_map 必须是对象"
        )
    if classification_map.get("format_version") != CLASSIFICATION_MAP_FORMAT_VERSION:
        raise SubcategoryResolverError(
            "classification_map_version",
            f"classification_map 版本必须是 {CLASSIFICATION_MAP_FORMAT_VERSION}",
        )

    min_confidence = classification_map.get("min_confidence")
    if not _is_finite_unit(min_confidence):
        raise SubcategoryResolverError(
            "min_confidence_invalid",
            "classification_map.min_confidence 必须是 0 至 1 的数值",
        )

    mapping = classification_map.get("category_to_subcategory")
    if not isinstance(mapping, dict) or not mapping:
        raise SubcategoryResolverError(
            "category_map_invalid",
            "classification_map.category_to_subcategory 必须是非空对象",
        )
    for category, target in mapping.items():
        if not isinstance(category, str) or not category:
            raise SubcategoryResolverError(
                "category_map_key_invalid",
                "category_to_subcategory 的类目 key 必须是非空字符串",
            )
        if target not in valid_track_keys:
            raise SubcategoryResolverError(
                "map_target_unknown",
                f"类目 {category} 映射到未定义的子类目 track：{target}",
            )

    out_of_scope_target = classification_map.get("out_of_scope_subcategory")
    if out_of_scope_target not in valid_track_keys:
        raise SubcategoryResolverError(
            "out_of_scope_target_unknown",
            "out_of_scope_subcategory 必须是合同已定义的 track key",
        )


def resolve_subcategory(
    precheck: Any,
    *,
    classification_map: dict,
    track_classification: dict,
) -> dict:
    """Resolve 调用A's classification signal to a subcategory (track) key.

    Pure function, deterministic.  Resolve order (each step appended to
    ``notes``):

    1. Validate ``classification_map`` (against the contract's track keys) and
       ``track_classification`` (must carry a ``default_track`` inside its keys).
    2. Read ``precheck.classification`` — ``scope_status`` / ``primary_category``
       / ``primary_confidence``.  Missing or illegal → ``default_track``,
       ``resolved_by="invalid_classification"``, ``needs_review=True``.
    3. ``scope_status == "out_of_scope"`` → ``out_of_scope_subcategory``,
       ``resolved_by="out_of_scope"``.
    4. ``primary_confidence < min_confidence`` → ``default_track``,
       ``resolved_by="low_confidence"``, ``needs_review=True``.
    5. ``primary_category`` hits ``category_to_subcategory`` → that subcategory,
       ``resolved_by="mapped"``.
    6. No mapping hit → ``default_track``, ``resolved_by="unmapped_category"``,
       ``needs_review=True``.

    ``boundary`` scope_status is treated as in-scope and continues through
    steps 4/5/6 (annotated in ``notes``).  Every default-fallback branch yields
    a ``track_key`` that exists in the contract (``default_track`` is validated).

    Returns ``{"track_key", "resolved_by", "needs_review", "primary_category",
    "confidence", "notes"}`` — a fixed-shape, JSON-serializable dict.
    """
    notes: list[str] = []

    # Step 1 — validate both inputs (fail-closed).
    valid_track_keys = _track_keys(track_classification)
    if not valid_track_keys:
        raise SubcategoryResolverError(
            "track_classification_invalid",
            "track_classification 必须携带至少一个合法 track key",
        )
    validate_classification_map(
        classification_map, valid_track_keys=valid_track_keys
    )

    default_track = (
        track_classification.get("default_track")
        if isinstance(track_classification, dict)
        else None
    )
    if default_track not in valid_track_keys:
        raise SubcategoryResolverError(
            "default_track_unknown",
            "track_classification.default_track 必须是已定义的 track key",
        )
    notes.append(f"contract default_track={default_track}")

    out_of_scope_target = classification_map["out_of_scope_subcategory"]
    mapping = classification_map["category_to_subcategory"]
    min_confidence = float(classification_map["min_confidence"])

    def _result(
        *,
        track_key: str,
        resolved_by: str,
        needs_review: bool,
        primary_category: Any,
        confidence: Any,
    ) -> dict:
        return {
            "track_key": track_key,
            "resolved_by": resolved_by,
            "needs_review": needs_review,
            "primary_category": primary_category if isinstance(primary_category, str) else None,
            "confidence": float(confidence) if _is_number(confidence) else None,
            "notes": list(notes),
        }

    # Step 2 — read + validate the classification signal.
    classification = precheck.get("classification") if isinstance(precheck, dict) else None
    scope_status = classification.get("scope_status") if isinstance(classification, dict) else None
    primary_category = (
        classification.get("primary_category") if isinstance(classification, dict) else None
    )
    primary_confidence = (
        classification.get("primary_confidence") if isinstance(classification, dict) else None
    )

    if (
        not isinstance(classification, dict)
        or scope_status not in _VALID_SCOPE_STATUS
        or not isinstance(primary_category, str)
        or not primary_category
        or not _is_finite_unit(primary_confidence)
    ):
        notes.append("classification 缺失或非法，落 default_track")
        return _result(
            track_key=default_track,
            resolved_by="invalid_classification",
            needs_review=True,
            primary_category=primary_category,
            confidence=primary_confidence,
        )

    confidence = float(primary_confidence)
    notes.append(
        f"classification scope_status={scope_status} "
        f"primary_category={primary_category} confidence={confidence}"
    )
    if scope_status == "boundary":
        notes.append("scope_status=boundary 视为在范围内继续解析")

    # Step 3 — explicit out-of-scope routing.
    if scope_status == "out_of_scope":
        notes.append(f"scope_status=out_of_scope → {out_of_scope_target}")
        return _result(
            track_key=out_of_scope_target,
            resolved_by="out_of_scope",
            needs_review=False,
            primary_category=primary_category,
            confidence=confidence,
        )

    # Step 4 — low-confidence fallback.
    if confidence < min_confidence:
        notes.append(
            f"confidence {confidence} < min_confidence {min_confidence}，落 default_track"
        )
        return _result(
            track_key=default_track,
            resolved_by="low_confidence",
            needs_review=True,
            primary_category=primary_category,
            confidence=confidence,
        )

    # Step 5 — mapped category hit.
    mapped_track = mapping.get(primary_category)
    if mapped_track is not None:
        notes.append(f"命中映射 {primary_category} → {mapped_track}")
        return _result(
            track_key=mapped_track,
            resolved_by="mapped",
            needs_review=False,
            primary_category=primary_category,
            confidence=confidence,
        )

    # Step 6 — unmapped category fallback.
    notes.append(f"类目 {primary_category} 未命中映射，落 default_track")
    return _result(
        track_key=default_track,
        resolved_by="unmapped_category",
        needs_review=True,
        primary_category=primary_category,
        confidence=confidence,
    )
