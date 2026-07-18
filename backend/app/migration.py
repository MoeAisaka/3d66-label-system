from __future__ import annotations

from typing import Any


def _level_number(level: str | None) -> int | None:
    if not level or not level.startswith("L"):
        return None
    try:
        return int(level[1:])
    except ValueError:
        return None


def compare_results(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    audit_sample: bool = False,
) -> dict[str, Any]:
    reasons: list[str] = []
    baseline_level_value = baseline.get("final_level") or baseline.get("level")
    candidate_level_value = candidate.get("final_level") or candidate.get("level")
    baseline_level = _level_number(baseline_level_value)
    candidate_level = _level_number(candidate_level_value)
    level_delta = (
        candidate_level - baseline_level
        if baseline_level is not None and candidate_level is not None
        else None
    )
    if level_delta is None:
        reasons.append("至少一侧没有正式等级")
    elif level_delta != 0:
        reasons.append(f"等级变化 {baseline_level_value} → {candidate_level_value}")

    baseline_category = ((baseline.get("precheck") or {}).get("classification") or {}).get(
        "primary_category"
    )
    candidate_category = ((candidate.get("precheck") or {}).get("classification") or {}).get(
        "primary_category"
    )
    if baseline_category != candidate_category:
        reasons.append(f"主分类变化 {baseline_category or '无'} → {candidate_category or '无'}")

    confidence = candidate.get("confidence")
    if confidence is None or float(confidence) < 0.75:
        reasons.append("新模型置信度低于 0.75")
    if candidate.get("needs_review"):
        reasons.append("新模型主动请求复核")
    if audit_sample:
        reasons.append("一致样本 5% 抽检")

    return {
        "requires_review": bool(reasons),
        "reasons": reasons,
        "level_delta": level_delta,
        "baseline_level": baseline_level_value,
        "candidate_level": candidate_level_value,
        "baseline_score": baseline.get("score"),
        "candidate_score": candidate.get("score"),
        "baseline_category": baseline_category,
        "candidate_category": candidate_category,
    }
