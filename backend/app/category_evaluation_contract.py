"""ADR-0033 v3 evaluation contract skeleton (framework-first phase).

Pure **definition + validation** only.  This module performs no IO, no
network, no database and no model calls.  It does not execute evaluation and
is not wired into the worker path in this phase.

The v3 contract (``evaluation-category-profile-v3``) is carried as a ``dict``
and extends v2 with three additional blocks — ``redline_policy`` (delegated to
``redline_policy.validate_redline_policy``), ``track_classification``
(``track-classification-v1``) and ``common_modifiers`` (``common-modifiers-v1``).
v2 fields are intentionally not redefined here.
"""

from __future__ import annotations

import re
from typing import Any

from .dimension_schema_registry import canonical_hash as _canonical_hash
from .dimension_schema_registry import canonical_json as _canonical_json
from .redline_policy import (
    RedlinePolicyError,
    validate_redline_policy,
)


CATEGORY_EVALUATION_CONTRACT_VERSION = "evaluation-category-profile-v3"
TRACK_CLASSIFICATION_FORMAT_VERSION = "track-classification-v1"
COMMON_MODIFIERS_FORMAT_VERSION = "common-modifiers-v1"

_TRACK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
_MEDIA_PENALTY_KEYS = frozenset({"real_photo", "render_3d", "ai_image", "other"})


class CategoryEvaluationContractError(ValueError):
    """Raised when a v3 evaluation contract is invalid.

    Carries a stable ``code`` for programmatic branching independent of the
    (localized) message text.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_track_classification(block: Any) -> None:
    if not isinstance(block, dict):
        raise CategoryEvaluationContractError(
            "track_classification_not_object", "track_classification 必须是对象"
        )
    if block.get("format_version") != TRACK_CLASSIFICATION_FORMAT_VERSION:
        raise CategoryEvaluationContractError(
            "track_classification_version",
            f"track_classification 版本必须是 {TRACK_CLASSIFICATION_FORMAT_VERSION}",
        )

    tracks = block.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise CategoryEvaluationContractError(
            "tracks_empty", "track_classification.tracks 必须是非空数组"
        )

    seen_keys: set[str] = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise CategoryEvaluationContractError(
                "track_not_object", "赛道定义必须是对象"
            )
        key = track.get("key")
        if not isinstance(key, str) or not _TRACK_KEY_PATTERN.match(key):
            raise CategoryEvaluationContractError(
                "track_key_invalid", "赛道 key 不符合命名规范"
            )
        if key in seen_keys:
            raise CategoryEvaluationContractError(
                "track_key_duplicate", f"赛道 key 重复：{key}"
            )
        seen_keys.add(key)

        if not isinstance(track.get("label"), str) or not track["label"].strip():
            raise CategoryEvaluationContractError(
                "track_label_invalid", "赛道 label 必须是非空字符串"
            )

        base_score = track.get("base_score")
        dimension_max = track.get("dimension_max")
        track_cap = track.get("track_cap")
        for name, value in (
            ("base_score", base_score),
            ("dimension_max", dimension_max),
            ("track_cap", track_cap),
        ):
            if not _is_int(value) or not 0 <= value <= 100:
                raise CategoryEvaluationContractError(
                    "track_score_out_of_range",
                    f"赛道 {name} 必须是 0 至 100 的整数",
                )
        if not base_score + dimension_max <= track_cap <= 100:
            raise CategoryEvaluationContractError(
                "track_cap_inconsistent",
                "赛道必须满足 base_score+dimension_max<=track_cap<=100",
            )

        ref = track.get("dimension_schema_ref")
        if not isinstance(ref, dict):
            raise CategoryEvaluationContractError(
                "track_schema_ref_invalid", "赛道 dimension_schema_ref 必须是对象"
            )
        for field in ("schema_key", "version"):
            if not isinstance(ref.get(field), str) or not ref[field].strip():
                raise CategoryEvaluationContractError(
                    "track_schema_ref_invalid",
                    f"赛道 dimension_schema_ref.{field} 必须是非空字符串",
                )

    default_track = block.get("default_track")
    if default_track not in seen_keys:
        raise CategoryEvaluationContractError(
            "default_track_unknown", "default_track 必须是已定义的赛道 key"
        )


def _validate_common_modifiers(block: Any) -> None:
    if not isinstance(block, dict):
        raise CategoryEvaluationContractError(
            "common_modifiers_not_object", "common_modifiers 必须是对象"
        )
    if block.get("format_version") != COMMON_MODIFIERS_FORMAT_VERSION:
        raise CategoryEvaluationContractError(
            "common_modifiers_version",
            f"common_modifiers 版本必须是 {COMMON_MODIFIERS_FORMAT_VERSION}",
        )

    media = block.get("media_type_penalty")
    if not isinstance(media, dict):
        raise CategoryEvaluationContractError(
            "media_penalty_not_object", "media_type_penalty 必须是对象"
        )
    penalties = media.get("penalties")
    if not isinstance(penalties, dict) or set(penalties) != _MEDIA_PENALTY_KEYS:
        raise CategoryEvaluationContractError(
            "media_penalty_keys",
            "media_type_penalty.penalties 必须且只能包含四个媒介键",
        )
    for name, value in penalties.items():
        if not _is_int(value) or value > 0:
            raise CategoryEvaluationContractError(
                "media_penalty_value",
                f"media_type_penalty.penalties.{name} 必须是 <=0 的整数",
            )
    baseline = media.get("baseline")
    if baseline not in _MEDIA_PENALTY_KEYS:
        raise CategoryEvaluationContractError(
            "media_penalty_baseline", "media_type_penalty.baseline 必须是允许的媒介键"
        )
    if penalties[baseline] != 0:
        raise CategoryEvaluationContractError(
            "media_penalty_baseline_nonzero", "基准媒介的降权必须为 0"
        )

    veto = block.get("high_score_veto")
    if not isinstance(veto, dict):
        raise CategoryEvaluationContractError(
            "veto_not_object", "high_score_veto 必须是对象"
        )
    threshold = veto.get("threshold")
    cap_to = veto.get("cap_to")
    for name, value in (("threshold", threshold), ("cap_to", cap_to)):
        if not _is_int(value) or not 0 <= value <= 100:
            raise CategoryEvaluationContractError(
                "veto_out_of_range",
                f"high_score_veto.{name} 必须是 0 至 100 的整数",
            )
    if not cap_to < threshold:
        raise CategoryEvaluationContractError(
            "veto_inconsistent", "high_score_veto.cap_to 必须小于 threshold"
        )


def validate_category_evaluation_contract(contract: Any) -> None:
    """Fail-closed validation of a v3 contract, block by block.

    Delegates the ``redline_policy`` block to task 1's validator and re-raises
    its ``RedlinePolicyError`` as ``CategoryEvaluationContractError`` so callers
    of this contract see a single error type with a stable ``.code``.
    """
    if not isinstance(contract, dict):
        raise CategoryEvaluationContractError(
            "contract_not_object", "评测合同必须是对象"
        )
    if contract.get("schema_version") != CATEGORY_EVALUATION_CONTRACT_VERSION:
        raise CategoryEvaluationContractError(
            "schema_version_unsupported",
            f"评测合同版本必须是 {CATEGORY_EVALUATION_CONTRACT_VERSION}",
        )

    for block_key in ("redline_policy", "track_classification", "common_modifiers"):
        if block_key not in contract:
            raise CategoryEvaluationContractError(
                "block_missing", f"评测合同缺少 {block_key} 区块"
            )

    try:
        validate_redline_policy(contract["redline_policy"])
    except RedlinePolicyError as exc:
        raise CategoryEvaluationContractError(
            f"redline_policy.{exc.code}", str(exc)
        ) from exc

    _validate_track_classification(contract["track_classification"])
    _validate_common_modifiers(contract["common_modifiers"])


def canonical_contract_hash(contract: dict[str, Any]) -> str:
    """Stable sha256 hex of the canonical JSON of a contract.

    Reuses ``dimension_schema_registry.canonical_json`` / ``canonical_hash``
    (sort_keys, compact separators, ensure_ascii=False), so key order is
    irrelevant and structurally equivalent contracts hash identically.
    """
    return _canonical_hash(contract)


def canonical_contract_json(contract: dict[str, Any]) -> str:
    """Canonical JSON string of a contract (key-order independent)."""
    return _canonical_json(contract)
