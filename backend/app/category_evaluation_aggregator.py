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

The production worker is v3-only and persists this semantic version.  Older
unversioned evaluation rows remain historical evidence and are not silently
reinterpreted by this engine.
"""

from __future__ import annotations

import math
from typing import Any

from .category_evaluation_contract import (
    CategoryEvaluationContractError,
    COMMON_MODIFIERS_V2_FORMAT_VERSION,
    validate_category_evaluation_contract,
)
from .level_scale import (
    DEFAULT_THRESHOLDS,
    resolve_level_scale,
    score_to_level,
)
from .inspiration_quality_rules import QualityRulesError, load_quality_rules
from .redline_policy import evaluate_redlines


AGGREGATOR_VERSION = "category-evaluation-aggregator-v2-rule-deduction"
LEVEL_SEMANTICS_VERSION = "doc-l5-worst-v1"

# doc-l5-worst-v1 default score→level table.  Ordered high→low; the first entry
# whose ``min_score`` the score reaches wins.  The trailing 0 entry is the
# catch-all worst bucket.  Overridable via contract["level_thresholds"].
DEFAULT_LEVEL_THRESHOLDS = DEFAULT_THRESHOLDS

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
    "ai_generated": "ai_image",
    "AI生成": "ai_image",
    "其它": "other",
    "其他": "other",
    "other": "other",
    "3d_render": "render_3d",
    "3D render": "render_3d",
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
    """Backward-compatible threshold view for foundation callers."""
    return list(resolve_level_scale(contract)["thresholds"])


def _score_to_level(score: int, thresholds: list[dict[str, Any]]) -> str:
    """Map a clamped score to an L-level via the high→low threshold table."""
    return score_to_level(score, {"thresholds": thresholds})


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


def _trait_to_media_key(
    precheck: dict[str, Any], media_config: dict[str, Any]
) -> tuple[str, bool]:
    """Map ``precheck.production_fields.trait`` to a media-penalty key.

    Returns ``(media_key, uncertain)``.  A missing/unknown trait safely falls
    back to ``other`` and flags uncertainty so the caller can record evidence.
    """
    production_fields = precheck.get("production_fields")
    trait = production_fields.get("trait") if isinstance(production_fields, dict) else None
    penalties = media_config.get("penalties")
    if not isinstance(penalties, dict) or not penalties:
        return "", True
    if isinstance(trait, str):
        trait = trait.strip()
        if trait in penalties:
            return trait, False
        aliases = media_config.get("aliases")
        if isinstance(aliases, dict):
            alias_target = aliases.get(trait)
            if isinstance(alias_target, str) and alias_target in penalties:
                return alias_target, False
        legacy_key = _TRAIT_TO_MEDIA_KEY.get(trait)
        if legacy_key in penalties:
            return legacy_key, False
    fallback = media_config.get("fallback")
    if isinstance(fallback, str) and fallback in penalties:
        return fallback, True
    if _MEDIA_FALLBACK_KEY in penalties:
        return _MEDIA_FALLBACK_KEY, True
    baseline = media_config.get("baseline")
    if isinstance(baseline, str) and baseline in penalties:
        return baseline, True
    return next(iter(penalties)), True


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
    if isinstance(dimension_result.get("dimension_deductions"), dict):
        evidence["dimension_deductions"] = dimension_result["dimension_deductions"]
    warning = dimension_result.get("warning")
    if isinstance(dimension_result.get("prompt_identity"), dict):
        evidence["prompt_identity"] = dimension_result["prompt_identity"]
    if isinstance(warning, str) and warning:
        evidence["warning"] = warning
    evidence["mode"] = (
        "rule_deduction"
        if dimension_result.get("mode") == "rule_deduction"
        else "grade_fallback"
    )
    return score_after, evidence


def _track_score_adjustment(
    contract: dict[str, Any], track_key: str
) -> tuple[float, dict[str, float], dict[str, Any]]:
    raw = contract.get("track_adjustments")
    adjustment = raw.get(track_key) if isinstance(raw, dict) else None
    if not isinstance(adjustment, dict):
        return 0.0, {"deduction": 0.0, "bonus": 0.0}, {
            "primitive": "track_adjustment",
            "track_key": track_key,
            "deduction": 0.0,
            "bonus": 0.0,
            "applied": False,
        }
    deduction = float(adjustment.get("deduction", 0.0))
    bonus = float(adjustment.get("bonus", 0.0))
    return bonus - deduction, {"deduction": deduction, "bonus": bonus}, {
        "primitive": "track_adjustment",
        "track_key": track_key,
        "deduction": deduction,
        "bonus": bonus,
        "applied": bool(deduction or bonus),
    }


def _load_quality_rules_or_fail(
    contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """装载运营可配置的质量规则块；合同没有该块时返回 (None, [])。"""
    try:
        loaded = load_quality_rules(contract)
    except QualityRulesError as exc:
        raise CategoryEvaluationAggregatorError(
            f"quality_rules.{exc.code}", str(exc)
        ) from exc
    if loaded is None:
        return None, []
    return loaded


def _score_cap_for_level_threshold(
    thresholds: list[dict[str, Any]], level: str
) -> int:
    """``level`` 档还能容纳的最高整数分（更优档 min_score - 1；最优档为 100）。"""
    for index, entry in enumerate(thresholds):
        if entry["level"] == level:
            if index == 0:
                return 100
            return int(thresholds[index - 1]["min_score"]) - 1
    raise CategoryEvaluationAggregatorError(
        "quality_rules_cap_level_disabled",
        f"质量规则封顶等级 {level} 未在等级档位启用",
    )


def _quality_reason_values(precheck: dict[str, Any]) -> list[str]:
    value = (precheck.get("production_fields") or {}).get("reason")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _apply_quality_defect_exemptions(
    precheck: dict[str, Any],
    exemptions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """按硬伤例外名单过滤 precheck 副本；输入保持只读。

    维度门槛（require_dimensions）依赖八维 grade+shortcomings 证据，本聚合路径
    的调用B输出是规则命中形状、拿不到这份证据，因此 fail-closed：佐证关键词
    命中但维度门槛无从核实的豁免**不生效**，只在 notes 里向运营说明原因。
    """
    import copy as _copy
    import json as _json

    adjusted = _copy.deepcopy(precheck)
    applied: list[dict[str, Any]] = []
    notes: list[str] = []
    evidence_text = _json.dumps(
        precheck.get("decisive_evidence"), ensure_ascii=False, sort_keys=True
    )
    for exemption in exemptions:
        source = exemption["source"]
        defect_key = exemption["defect_key"]
        defects = adjusted.get(source)
        if not isinstance(defects, list) or defect_key not in defects:
            continue
        if not any(
            token in evidence_text
            for token in exemption["evidence_contains_any"]
        ):
            continue
        if exemption["foundation_requirements"]:
            notes.append(
                f"硬伤例外「{exemption['key']}」佐证关键词已命中，但本评测路径"
                f"没有八维档位输出、维度门槛无法核实，按不豁免处理"
            )
            continue
        adjusted[source] = [item for item in defects if item != defect_key]
        applied.append({
            "rule": "hard_defect_exemption",
            "key": exemption["key"],
            "defect_key": defect_key,
        })
    return adjusted, applied, notes


def _apply_hard_defect_penalty(
    precheck: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    policy = (contract.get("common_modifiers") or {}).get("hard_defect_penalty")
    if not isinstance(policy, dict) or not policy.get("enabled", True):
        return 0.0, {
            "primitive": "hard_defect_penalty",
            "enabled": False,
            "hit_count": 0,
            "per_hit": 0.0,
            "deduction": 0.0,
        }
    source = policy.get("source", "hard_defects")
    sources = ["hard_defects"] if source == "hard_defects" else (
        ["image_defects"] if source == "image_defects" else ["hard_defects", "image_defects"]
    )
    hits: list[str] = []
    for source_key in sources:
        values = precheck.get(source_key)
        if isinstance(values, list):
            hits.extend(item for item in values if isinstance(item, str) and item.strip())
    per_hit = float(policy["per_hit"])
    deduction = per_hit * len(hits)
    return -deduction, {
        "primitive": "hard_defect_penalty",
        "enabled": True,
        "source": source,
        "hit_count": len(hits),
        "hits": hits,
        "per_hit": per_hit,
        "deduction": deduction,
    }


def _apply_v2_hard_defect_policy(
    *,
    precheck: dict[str, Any],
    veto: dict[str, Any],
    score: float,
) -> tuple[float, dict[str, Any]]:
    """Resolve source-qualified rev4 defects and apply the strongest action."""
    source_values: dict[str, set[str]] = {}
    for source in ("hard_defects", "image_defects"):
        raw_values = precheck.get(source)
        source_values[source] = (
            {
                item
                for item in raw_values
                if isinstance(item, str) and item
            }
            if isinstance(raw_values, list)
            else set()
        )
    matched: list[dict[str, Any]] = []
    modifiers: list[dict[str, Any]] = []
    for rule in veto["rules"]:
        if rule["key"] not in source_values[rule["source"]]:
            continue
        if rule.get("kind", "defect") == "modifier":
            modifiers.append(rule)
        else:
            matched.append(rule)

    tiers = veto["tiers"]
    tier_counts: dict[str, int] = {}
    for rule in matched:
        tier = rule["severity"]
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    escalation = veto.get("escalation")
    escalated = bool(
        isinstance(escalation, dict)
        and tier_counts.get(escalation["source_tier"], 0)
        >= escalation["minimum_distinct_hits"]
    )
    resolved_tier: str | None = None
    if escalated:
        resolved_tier = escalation["target_tier"]
    else:
        capping_tiers = {
            rule["severity"]
            for rule in matched
            if tiers[rule["severity"]]["action"] == "cap"
        }
        if capping_tiers:
            resolved_tier = min(
                capping_tiers,
                key=lambda tier: tiers[tier]["cap_to"],
            )
        elif matched:
            # A contract may name its record-only tier freely. Preserve the
            # policy's first matching severity instead of assuming a legacy key.
            resolved_tier = matched[0]["severity"]

    cap_to = (
        tiers[resolved_tier]["cap_to"]
        if resolved_tier is not None
        and tiers[resolved_tier]["action"] == "cap"
        else None
    )
    modifier_applied = bool(modifiers and matched)
    action = {
        "policy_version": veto.get("policy_version"),
        "matched_rules": [
            {
                "key": rule["key"],
                "source": rule["source"],
                "tier": rule["severity"],
            }
            for rule in matched
        ],
        "matched_modifiers": [rule["key"] for rule in modifiers],
        "tier_counts": tier_counts,
        "resolved_tier": resolved_tier,
        "cap_to": cap_to,
        "escalated": escalated,
        "modifier_applied": modifier_applied,
        "cancel_exemption": bool(
            modifier_applied
            and any(rule.get("cancel_exemption") is True for rule in modifiers)
        ),
    }
    if veto.get("enabled", True) and cap_to is not None:
        return min(score, float(cap_to)), action
    return score, action


def aggregate_category_evaluation(
    contract: Any,
    precheck: Any,
    dimension_result: Any,
    *,
    track_key: Any = None,
    initial_score: int | float | None = None,
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

    level_scale = resolve_level_scale(contract)
    thresholds = level_scale["thresholds"]

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
            "level_scale": level_scale,
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

    if initial_score is not None and (
        isinstance(initial_score, bool)
        or not isinstance(initial_score, (int, float))
        or not math.isfinite(float(initial_score))
        or not 0 <= float(initial_score) <= 100
    ):
        raise CategoryEvaluationAggregatorError(
            "initial_score_invalid", "调用B aesthetic_score 必须在 0-100 之间"
        )

    # Step 3 — track resolution (node 1).
    track = _resolve_track(contract, track_key)
    resolved_track_key = track["key"]
    base_score = track["base_score"]
    dimension_max = track["dimension_max"]
    track_cap = track["track_cap"]
    if initial_score is None:
        steps.append(_step(
            "track",
            base_score + dimension_max,
            f"赛道 {resolved_track_key}：基准分 {base_score} + 维度满分 {dimension_max}"
            f"，赛道上限 {track_cap}",
        ))
    else:
        steps.append(_step(
            "track",
            float(initial_score),
            f"赛道 {resolved_track_key}：调用B美感基础分 {initial_score}，赛道上限 {track_cap}",
        ))
        steps.append(_step(
            "b_aesthetic_foundation",
            float(initial_score),
            "等级撮合器以调用B aesthetic_score 作为初始分",
        ))

    # Step 4 — dimension deductions (node 2).
    if initial_score is None:
        score_after_dimensions, dim_evidence = _apply_dimension_deductions(
            dimension_result, base_score, dimension_max
        )
    else:
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
        applied_total = min(raw_total, 100.0)
        score_after_dimensions = max(0.0, float(initial_score) - applied_total)
        dim_evidence = {
            "initial_score": float(initial_score),
            "raw_deduction_total": raw_total,
            "applied_deduction_total": applied_total,
            "clamped_to_dimension_max": raw_total > 100.0,
            "deductions": applied_by_key,
            "mode": "rule_deduction",
        }
        if isinstance(dimension_result.get("evidence"), (list, dict)):
            dim_evidence["dimension_evidence"] = dimension_result["evidence"]
    rule_mode = dim_evidence["mode"] == "rule_deduction"
    dimension_note = (
        "维度扣分（规则命中）："
        if rule_mode
        else "维度扣分（@deprecated grade_points fallback）："
    )
    dimension_note += f"应用扣分 {dim_evidence['applied_deduction_total']}"
    if dim_evidence["clamped_to_dimension_max"]:
        dimension_note += "（已封顶到维度满分）"
    if dim_evidence.get("warning"):
        dimension_note += f"；{dim_evidence['warning']}"
    steps.append(_step(
        "dimension_rule_deduction" if rule_mode else "dimensions",
        score_after_dimensions,
        dimension_note,
    ))

    common_modifier_evidence: list[dict[str, Any]] = []
    track_adjustment_delta, track_adjustment, track_adjustment_evidence = (
        _track_score_adjustment(contract, resolved_track_key)
    )
    score_after_track_adjustment = score_after_dimensions + track_adjustment_delta
    if track_adjustment_evidence["applied"]:
        steps.append(_step(
            "track_adjustment",
            score_after_track_adjustment,
            f"赛道固定调整：加 {track_adjustment['bonus']}，扣 {track_adjustment['deduction']}",
        ))
        common_modifier_evidence.append(track_adjustment_evidence)
    else:
        steps.append(_step(
            "track_adjustment_skipped",
            score_after_track_adjustment,
            "赛道未声明固定调整",
        ))

    # Step 5 — fixed media-type penalty (common modifier).
    common_modifiers = contract.get("common_modifiers") or {}
    media_config = common_modifiers.get("media_type_penalty")
    if not isinstance(media_config, dict):
        media_config = {"enabled": False}
    media_enabled = media_config.get("enabled", True)
    if media_enabled:
        media_key, media_uncertain = _trait_to_media_key(precheck, media_config)
        penalties = media_config["penalties"]
        media_penalty = penalties[media_key]
        score_after_media = score_after_track_adjustment + float(media_penalty)
        media_note = f"媒介类型 {media_key}，固定扣分 {media_penalty}"
        if media_uncertain:
            media_note += "（trait 缺失/未知，安全落 other 并记不确定性）"
        steps.append(_step("media", score_after_media, media_note))
    else:
        media_key = None
        media_penalty = 0
        score_after_media = score_after_track_adjustment
        steps.append(_step("media_skipped", score_after_media, "媒介降权已关闭，节点 penalty=0"))

    if media_enabled:
        common_modifier_evidence.append({
            "primitive": "media_penalty",
            "enabled": True,
            "media_key": media_key,
            "penalty": float(media_penalty),
        })

    # 质量规则（运营可配置块）：软封顶在硬伤扣分/一票压分之前应用，
    # 硬伤例外先过滤 precheck 再进后续硬伤类节点，与基座路径语义对齐。
    quality_soft_cap, quality_exemptions = _load_quality_rules_or_fail(contract)
    quality_evidence: dict[str, Any] = {
        "soft_cap_applied": False,
        "exemptions_applied": [],
        "notes": [],
    }
    score_after_quality = score_after_media
    if quality_soft_cap is not None and any(
        reason in quality_soft_cap["match_any"]
        for reason in _quality_reason_values(precheck)
    ):
        if "cap_to" in quality_soft_cap:
            resolved_cap_to = int(quality_soft_cap["cap_to"])
            cap_note = f"总分压到 {resolved_cap_to} 以内"
        else:
            cap_to_level = str(quality_soft_cap["cap_to_level"])
            resolved_cap_to = _score_cap_for_level_threshold(
                thresholds, cap_to_level
            )
            cap_note = f"总分压到 {cap_to_level} 上界 {resolved_cap_to} 以内"
            escalation = quality_soft_cap.get("filter_escalation") or {}
            if escalation.get("dimensions_at_most"):
                quality_evidence["notes"].append(
                    "软封顶的维度分档升级条件需要八维档位输出，本评测路径无此证据，未评估升级"
                )
        if score_after_quality > resolved_cap_to:
            score_after_quality = float(resolved_cap_to)
            quality_evidence["soft_cap_applied"] = True
            caps.append({
                "cap": quality_soft_cap["key"],
                "reason": f"判定理由命中软封顶关键词，{cap_note}",
            })
            steps.append(_step(
                "quality_soft_cap",
                score_after_quality,
                f"质量规则软封顶：{cap_note}",
            ))

    policy_precheck = precheck
    if quality_exemptions:
        policy_precheck, applied_exemptions, exemption_notes = (
            _apply_quality_defect_exemptions(precheck, quality_exemptions)
        )
        quality_evidence["exemptions_applied"] = applied_exemptions
        quality_evidence["notes"].extend(exemption_notes)
        for item in applied_exemptions:
            caps.append({
                "cap": "hard_defect_exemption",
                "reason": f"硬伤例外「{item['key']}」生效，{item['defect_key']} 不参与硬伤降级",
            })
        for note in exemption_notes:
            steps.append(_step("quality_exemption_skipped", score_after_quality, note))

    hard_defect_delta, hard_defect_evidence = _apply_hard_defect_penalty(
        policy_precheck, contract
    )
    score_after_hard_defect = score_after_quality + hard_defect_delta
    if hard_defect_evidence["enabled"]:
        steps.append(_step(
            "hard_defect_penalty",
            score_after_hard_defect,
            f"硬伤逐条扣分：命中 {hard_defect_evidence['hit_count']} 条，每条扣 {hard_defect_evidence['per_hit']}",
        ))
        common_modifier_evidence.append(hard_defect_evidence)

    # Step 6 — hard-defect policy. v1 is replay-only; v2 is monotonic.
    veto = common_modifiers.get("high_score_veto")
    if not isinstance(veto, dict):
        veto = {"enabled": False}
    hard_defect_action: dict[str, Any] | None = None
    if contract["common_modifiers"]["format_version"] == COMMON_MODIFIERS_V2_FORMAT_VERSION:
        if veto.get("enabled", True):
            score_after_veto, hard_defect_action = _apply_v2_hard_defect_policy(
                precheck=policy_precheck,
                veto=veto,
                score=score_after_hard_defect,
            )
        else:
            score_after_veto = score_after_hard_defect
            steps.append(_step("veto_skipped", score_after_veto, "高分硬伤封顶已关闭"))
        if hard_defect_action is not None and hard_defect_action["cap_to"] is not None:
            caps.append({
                "cap": "hard_defect_severity",
                "reason": (
                    f"命中 {hard_defect_action['matched_rules']}，"
                    f"按 tier {hard_defect_action['resolved_tier']} "
                    f"无条件 min(当前分, {hard_defect_action['cap_to']})"
                ),
            })
            steps.append(_step(
                "veto",
                score_after_veto,
                f"硬伤 tier {hard_defect_action['resolved_tier']}："
                f"封顶至 {hard_defect_action['cap_to']}",
            ))
        elif hard_defect_action is not None and hard_defect_action["resolved_tier"] is not None:
            steps.append(_step("veto", score_after_veto, "仅命中记录型缺陷，不压分"))
        elif hard_defect_action is not None:
            steps.append(_step("veto", score_after_veto, "无配置内硬伤信号"))
    else:
        veto_enabled = veto.get("enabled", True)
        veto_threshold = veto["threshold"]
        veto_cap_to = veto["cap_to"]
        hard_defects = policy_precheck.get("hard_defects")
        configured_rules = veto.get("rules")
        configured_hard_defects = (
            {rule["key"] for rule in configured_rules}
            if isinstance(configured_rules, list)
            else None
        )
        has_hard_defects = isinstance(hard_defects, list) and any(
            isinstance(item, str)
            and (configured_hard_defects is None or item in configured_hard_defects)
            for item in hard_defects
        )
        score_after_veto = score_after_hard_defect
        if veto_enabled and score_after_hard_defect >= veto_threshold and has_hard_defects:
            score_after_veto = min(score_after_hard_defect, float(veto_cap_to))
            caps.append({
                "cap": "high_score_veto",
                "reason": f"分数 {score_after_hard_defect} 达到 {veto_threshold} 且命中硬伤"
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
                "高分一票压分未触发（已关闭、未达阈值或无配置内硬伤信号）",
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
    raw_score = _clamp_score(min(score_after_hard_defect, float(track_cap)))
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
        "level_scale": level_scale,
        "hard_reject": False,
        "terminated_at": None,
        "track_key": resolved_track_key,
        "base_score": base_score if initial_score is None else None,
        "dimension_max": dimension_max,
        "initial_score": initial_score,
        "score": score,
        "level": level,
        "raw_level": raw_level,
        "hard_defect_action": hard_defect_action,
        "track_adjustment": track_adjustment,
        "hard_defect_penalty": -hard_defect_delta,
        "common_modifier_evidence": common_modifier_evidence,
        "quality_rules_evidence": quality_evidence,
        "hit_rules": list(redline["hit_rules"]),
        "dimension_evidence": dim_evidence,
        "media_penalty_enabled": bool(media_enabled),
        "media_key": media_key,
        "media_penalty": media_penalty,
        "caps": caps,
        "steps": steps,
    }
