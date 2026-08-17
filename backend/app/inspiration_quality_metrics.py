"""Five-level and three-bucket quality metrics for inspiration images."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

LEVELS = ("L1", "L2", "L3", "L4", "L5")
BUCKETS = {
    "recommendation": frozenset({"L1", "L2"}),
    "ordinary": frozenset({"L3", "L4"}),
    "filter": frozenset({"L5"}),
}


def _level(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in LEVELS else None


def _binary(rows: list[tuple[str, str]], positive: set[str]) -> dict[str, Any]:
    predicted = sum(pred in positive for _truth, pred in rows)
    actual = sum(truth in positive for truth, _pred in rows)
    true_positive = sum(truth in positive and pred in positive for truth, pred in rows)
    false_positive = predicted - true_positive
    false_negative = actual - true_positive
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / actual if actual else 0.0
    return {
        "support": len(rows),
        "denominator": actual,
        "predicted_count": predicted,
        "actual_count": actual,
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "precision": precision,
        "recall": recall,
    }


def three_bucket_fallback_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized = [
        (truth, pred)
        for row in rows
        if (truth := _level(row.get("truth"))) is not None
        and (pred := _level(row.get("pred"))) is not None
    ]
    output: dict[str, dict[str, Any]] = {}
    for bucket, members in BUCKETS.items():
        output[bucket] = _binary(
            normalized,
            set(members),
        )
    return output


def compute_inspiration_quality_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [
        (truth, pred)
        for row in rows
        if (truth := _level(row.get("truth"))) is not None
        and (pred := _level(row.get("pred"))) is not None
    ]
    per_level: dict[str, dict[str, Any]] = {}
    confusion = Counter(normalized)
    for level in LEVELS:
        per_level[level] = _binary(normalized, {level})
    recommendation = _binary(normalized, set(BUCKETS["recommendation"]))
    recommendation["share"] = (
        sum(pred in BUCKETS["recommendation"] for _truth, pred in normalized) / len(normalized)
        if normalized
        else 0.0
    )
    return {
        "total": len(normalized),
        "per_level": per_level,
        "recommendation": recommendation,
        "three_bucket": three_bucket_fallback_metrics(
            [{"truth": truth, "pred": pred} for truth, pred in normalized]
        ),
        "confusion": {f"{truth}->{pred}": count for (truth, pred), count in sorted(confusion.items())},
        "diagnostics": {
            "l1_overpromotion_cost": per_level["L1"]["fp"],
            "l5_missed_filter_count": per_level["L5"]["fn"],
        },
    }


def quality_gate(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    recommendation = metrics.get("recommendation") or {}
    if float(recommendation.get("share", 0.0)) > 0.35:
        failures.append({"gate": "recommendation_share", "value": recommendation.get("share"), "limit": 0.35})
    checks = (("recommendation", recommendation), ("L3", (metrics.get("per_level") or {}).get("L3", {})), ("L4", (metrics.get("per_level") or {}).get("L4", {})), ("L5", (metrics.get("per_level") or {}).get("L5", {})))
    for name, values in checks:
        for metric in ("precision", "recall"):
            if float(values.get(metric, 0.0)) < 0.80:
                failures.append({"gate": f"{name}_{metric}", "value": values.get(metric, 0.0), "limit": 0.80})
    return failures

