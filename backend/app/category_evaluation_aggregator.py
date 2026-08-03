"""ADR-0033 deterministic category-evaluation aggregator (framework-first phase).

This module is a **pure-function** scoring engine: it performs no IO, no
network, no database and no model calls.  Given an already-frozen v3 contract,
a 调用A ``precheck`` payload and a 调用B ``dimension_result`` payload, it returns
the final score, L-level and a fully explainable evidence chain.  Output is a
fixed-shape, JSON-serializable, input-stable ``dict`` so the decision is
regressible.

Level semantics (``doc-l5-worst-v1``): this engine follows the DingTalk
document direction where **L5 = worst (redline / 淘汰档) and L1 = best**.  The
score is 0-100 (higher is better) but the L-level direction is inverted: a high
score maps to a low L number.  The score→level table lives here as an explicit,
contract-overridable ordered threshold list.

Out of scope (later independent phases): wiring into the worker/production
path and the global L-direction migration.  ``scoring.py`` uses the opposite
direction (L5 = best) and is intentionally left untouched; this is a separate
new-semantics engine tagged with ``LEVEL_SEMANTICS_VERSION``.
"""

from __future__ import annotations

import math
from typing import Any

from .category_evaluation_contract import (
    CategoryEvaluationContractError,
    validate_category_evaluation_contract,
)
from .redline_policy import evaluate_redlines


AGGREGATOR_VERSION = "category-evaluation-aggregator-v1"
LEVEL_SEMANTICS_VERSION = "doc-l5-worst-v1"

# doc-l5-worst-v1 default score→level table.  Ordered high→low; the first entry
# whose ``min_score`` the score reaches wins.  The trailing 0 entry is the
# catch-all worst bucket.  Overridable via contract["level_thresholds"].
DEFAULT_LEVEL_THRESHOLDS: tuple[dict[str, Any], ...] = (
    {"min_score": 80, "level": "L1"},
    {"min_score": 60, "level": "L2"},
    {"min_score": 40, "level": "L3"},
    {"min_score": 20, "level": "L4"},
    {"min_score": 0, "level": "L5"},
)

_VALID_LEVELS = frozenset({"L1", "L2", "L3", "L4", "L5"})

# 调用A ``production_fields.trait`` text → common media-penalty key.  Both the
# frozen production enum (schema_adapter.PRODUCTION_TRAIT_VALUES) and the doc's
# shorthand wording are accepted; anything else falls back to ``other``.
_TRAIT_TO_MEDIA_KEY: dict[str, str] = {
    "实景照片": "real_photo",
    "实拍": "real_photo",
    "3D数字效果图": "render_3d",
    "3D效果图": "render_3d",
    "AI图": "ai_image",
    "其它": "other",
    "其他": "other",
}
_MEDIA_FALLBACK_KEY = "other"


class CategoryEvaluationAggregatorError(ValueError):
    """Raised when aggregation cannot proceed (fail-closed).

    Carries a stable ``code`` for programmatic branching independent of the
    (localized) message text, matching the Phase 1 error convention.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _round_half_up(value: float) -> int:
    """Round-half-up (四舍五入) for a non-negative score, deterministically."""
    return int(math.floor(float(value) + 0.5))


def _clamp_score(value: float) -> int:
    """Round-half-up then clamp to the integer range [0, 100]."""
    return max(0, min(100, _round_half_up(value)))


def _step(step: str, score_after: Any, note: str) -> dict[str, Any]:
    return {"step": step, "score_after": score_after, "note": note}


def _resolve_level_thresholds(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the contract's ``level_thresholds`` or the doc default.

    A contract override must be a non-empty list of ``{min_score, level}``
    objects with legal L values and integer 0..100 ``min_score``; anything else
    fails closed.  The returned table is sorted high→low by ``min_score`` and
    must cover ``min_score`` 0 so every score maps to a level.
    """
    override = contract.get("level_thresholds")
    if override is None:
        return list(DEFAULT_LEVEL_THRESHOLDS)

    if not isinstance(override, list) or not override:
        raise CategoryEvaluationAggregatorError(
            "level_thresholds_invalid", "level_thresholds 必须是非空数组"
        )
    resolved: list[dict[str, Any]] = []
    for entry in override:
        if not isinstance(entry, dict):
            raise CategoryEvaluationAggregatorError(
                "level_thresholds_invalid", "level_thresholds 每项必须是对象"
            )
        min_score = entry.get("min_score")
        level = entry.get("level")
        if not _is_int(min_score) or not 0 <= min_score <= 100:
            raise CategoryEvaluationAggregatorError(
                "level_thresholds_invalid",
                "level_thresholds.min_score 必须是 0 至 100 的整数",
            )
        if level not in _VALID_LEVELS:
            raise CategoryEvaluationAggregatorError(
                "level_thresholds_invalid",
                "level_thresholds.level 必须是 L1 至 L5 之一",
            )
        resolved.append({"min_score": min_score, "level": level})

    resolved.sort(key=lambda item: item["min_score"], reverse=True)
    if resolved[-1]["min_score"] != 0:
        raise CategoryEvaluationAggregatorError(
            "level_thresholds_invalid",
            "level_thresholds 必须包含 min_score=0 的兜底档",
        )
    return resolved


def _score_to_level(score: int, thresholds: list[dict[str, Any]]) -> str:
    """Map a clamped score to an L-level via the high→low threshold table."""
    for entry in thresholds:
        if score >= entry["min_score"]:
            return entry["level"]
    # Unreachable: table is validated to include min_score=0.
    return thresholds[-1]["level"]


def _resolve_track(contract: dict[str, Any], track_key: Any) -> dict[str, Any]:
    """Resolve the active track (node 1), falling back to ``default_track``.

    Fail-closed: an explicit ``track_key`` that is not defined in the contract
    raises.  Returns the track definition dict.
    """
    block = contract["track_classification"]
    tracks = {track["key"]: track for track in block["tracks"]}
    resolved_key = track_key if track_key is not None else block["default_track"]
    if resolved_key not in tracks:
        raise CategoryEvaluationAggregatorError(
            "track_key_unknown", f"未知赛道 track_key：{resolved_key}"
        )
    return tracks[resolved_key]


def _trait_to_media_key(precheck: dict[str, Any]) -> tuple[str, bool]:
    """Map ``precheck.production_fields.trait`` to a media-penalty key.

    Returns ``(media_key, uncertain)``.  A missing/unknown trait safely falls
    back to ``other`` and flags uncertainty so the caller can record evidence.
    """
    production_fields = precheck.get("production_fields")
    trait = production_fields.get("trait") if isinstance(production_fields, dict) else None
    if isinstance(trait, str):
        key = _TRAIT_TO_MEDIA_KEY.get(trait.strip())
        if key is not None:
            return key, False
    return _MEDIA_FALLBACK_KEY, True


def _apply_dimension_deductions(
    dimension_result: Any,
    base_score: int,
    dimension_max: int,
) -> tuple[float, dict[str, Any]]:
    """Node 2: subtract dimension deductions from ``base_score + dimension_max``.

    ``dimension_result["deductions"]`` maps ``dimension_key`` → a non-negative
    deduction.  Cumulative deductions are clamped to ``dimension_max`` so the
    dimension block's net contribution never goes below 0.  Returns
    ``(score_after_dimensions, evidence)``.
    """
    if not isinstance(dimension_result, dict):
        raise CategoryEvaluationAggregatorError(
            "dimension_result_invalid", "dimension_result 必须是对象"
        )
    deductions = dimension_result.get("deductions")
    if not isinstance(deductions, dict):
        raise CategoryEvaluationAggregatorError(
            "dimension_deductions_invalid", "dimension_result.deductions 必须是对象"
        )

    raw_total = 0.0
    applied_by_key: dict[str, float] = {}
    for key, value in deductions.items():
        if not isinstance(key, str) or not key:
            raise CategoryEvaluationAggregatorError(
                "dimension_key_invalid", "维度扣分的 key 必须是非空字符串"
            )
        if not _is_number(value) or value < 0:
            raise CategoryEvaluationAggregatorError(
                "dimension_deduction_negative",
                f"维度扣分 {key} 必须是 >=0 的数值",
            )
        raw_total += float(value)
        applied_by_key[key] = float(value)

    applied_total = min(raw_total, float(dimension_max))
    clamped = raw_total > dimension_max
    score_after = float(base_score) + float(dimension_max) - applied_total

    evidence = {
        "initial_score": base_score + dimension_max,
        "raw_deduction_total": raw_total,
        "applied_deduction_total": applied_total,
        "clamped_to_dimension_max": clamped,
        "deductions": applied_by_key,
    }
    if isinstance(dimension_result.get("evidence"), (list, dict)):
        evidence["dimension_evidence"] = dimension_result["evidence"]
    return score_after, evidence


def aggregate_category_evaluation(
    contract: Any,
    precheck: Any,
    dimension_result: Any,
    *,
    track_key: Any = None,
) -> dict[str, Any]:
    """Deterministically aggregate a frozen v3 contract + precheck + dimensions.

    Pure function: no IO/network/DB/model calls.  Executes the fixed node order
    (validate → redline → track → dimension deductions → media penalty →
    high-score veto → track cap → score→level), recording explainable evidence
    for every step in ``steps``/``caps``.  Follows ``doc-l5-worst-v1`` semantics
    (L5 = worst, L1 = best).  Output is fixed-shape, JSON-serializable and
    stable for a given input.
    """
    steps: list[dict[str, Any]] = []
    caps: list[dict[str, Any]] = []

    # Step 1 — contract validation (fail-closed, wraps Phase 1's validator).
    try:
        validate_category_evaluation_contract(contract)
    except CategoryEvaluationContractError as exc:
        raise CategoryEvaluationAggregatorError(
            f"contract.{exc.code}", str(exc)
        ) from exc

    if not isinstance(precheck, dict):
        raise CategoryEvaluationAggregatorError(
            "precheck_invalid", "precheck 必须是对象"
        )

    thresholds = _resolve_level_thresholds(contract)

    # Step 2 — redline (node 0).  A hit terminates before any scoring step.
    redline = evaluate_redlines(precheck, policy=contract["redline_policy"])
    if redline.get("hit"):
        hit_score_cap = redline["hit_score_cap"]
        score = _clamp_score(hit_score_cap)
        hit_level = redline["hit_level"]
        caps.append({
            "cap": "redline",
            "reason": f"红线命中 {redline['hit_rules']}，总分封顶至 {hit_score_cap}",
        })
        steps.append(_step(
            "redline",
            score,
            f"红线命中 {redline['hit_rules']}，直出 {hit_level} 并终止后续流程",
        ))
        return {
            "aggregator_version": AGGREGATOR_VERSION,
            "level_semantics_version": LEVEL_SEMANTICS_VERSION,
            "hard_reject": True,
            "terminated_at": "redline",
            "track_key": None,
            "base_score": None,
            "dimension_max": None,
            "score": score,
            "level": hit_level,
            "raw_level": hit_level,
            "hit_rules": list(redline["hit_rules"]),
            "caps": caps,
            "steps": steps,
        }

    steps.append(_step("redline", None, "无红线命中，进入分数计算流程"))

    # Step 3 — track resolution (node 1).
    track = _resolve_track(contract, track_key)
    resolved_track_key = track["key"]
    base_score = track["base_score"]
    dimension_max = track["dimension_max"]
    track_cap = track["track_cap"]
    steps.append(_step(
        "track",
        base_score + dimension_max,
        f"赛道 {resolved_track_key}：基准分 {base_score} + 维度满分 {dimension_max}"
        f"，赛道上限 {track_cap}",
    ))

    # Step 4 — dimension deductions (node 2).
    score_after_dimensions, dim_evidence = _apply_dimension_deductions(
        dimension_result, base_score, dimension_max
    )
    steps.append(_step(
        "dimensions",
        score_after_dimensions,
        "扣减维度扣分（累计封顶到维度满分）："
        f"应用扣分 {dim_evidence['applied_deduction_total']}"
        + ("（已封顶到维度满分）" if dim_evidence["clamped_to_dimension_max"] else ""),
    ))

    # Step 5 — fixed media-type penalty (common modifier).
    media_key, media_uncertain = _trait_to_media_key(precheck)
    penalties = contract["common_modifiers"]["media_type_penalty"]["penalties"]
    media_penalty = penalties[media_key]
    score_after_media = score_after_dimensions + float(media_penalty)
    media_note = f"媒介类型 {media_key}，固定扣分 {media_penalty}"
    if media_uncertain:
        media_note += "（trait 缺失/未知，安全落 other 并记不确定性）"
    steps.append(_step("media", score_after_media, media_note))

    # Step 6 — high-score veto (一票压分).
    veto = contract["common_modifiers"]["high_score_veto"]
    veto_threshold = veto["threshold"]
    veto_cap_to = veto["cap_to"]
    hard_defects = precheck.get("hard_defects")
    has_hard_defects = isinstance(hard_defects, list) and len(hard_defects) > 0
    score_after_veto = score_after_media
    if score_after_media >= veto_threshold and has_hard_defects:
        score_after_veto = min(score_after_media, float(veto_cap_to))
        caps.append({
            "cap": "high_score_veto",
            "reason": f"分数 {score_after_media} 达到 {veto_threshold} 且命中硬伤"
            f" {hard_defects}，强制压至 {veto_cap_to}",
        })
        steps.append(_step(
            "veto",
            score_after_veto,
            f"高分一票压分触发：封顶至 {veto_cap_to}",
        ))
    else:
        steps.append(_step(
            "veto",
            score_after_veto,
            "高分一票压分未触发（未达阈值或无硬伤信号）",
        ))

    # Step 7 — track cap, then clamp to integer [0, 100].
    capped = min(score_after_veto, float(track_cap))
    if score_after_veto > track_cap:
        caps.append({
            "cap": "track_cap",
            "reason": f"赛道上限 {track_cap}，由 {score_after_veto} 封顶",
        })
    score = _clamp_score(capped)
    steps.append(_step("track_cap", score, f"赛道封顶至 {track_cap} 后取整"))

    # Step 8 — score → L level (doc-l5-worst).  raw_level ignores the veto cap.
    raw_score = _clamp_score(min(score_after_media, float(track_cap)))
    raw_level = _score_to_level(raw_score, thresholds)
    level = _score_to_level(score, thresholds)
    steps.append(_step(
        "level",
        score,
        f"分数 {score} → {level}（未压分前为 {raw_level}）",
    ))

    return {
        "aggregator_version": AGGREGATOR_VERSION,
        "level_semantics_version": LEVEL_SEMANTICS_VERSION,
        "hard_reject": False,
        "terminated_at": None,
        "track_key": resolved_track_key,
        "base_score": base_score,
        "dimension_max": dimension_max,
        "score": score,
        "level": level,
        "raw_level": raw_level,
        "hit_rules": list(redline["hit_rules"]),
        "caps": caps,
        "steps": steps,
    }
