from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from .models import (
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationResult,
)


LEVELS = ("L1", "L2", "L3", "L4", "L5")
TERMINAL_RUN_STATUSES = {"completed", "partial_failed", "failed"}
LEVEL_LABELS = {
    "L1": "好",
    "L2": "中等",
    "L3": "中差",
    "L4": "极差",
    "L5": "过滤",
}
_LEVEL_BY_LABEL = {label: level for level, label in LEVEL_LABELS.items()}
_FILENAME_TOKEN_SPLIT = re.compile(
    r"[\s._\-—–/\\,，;；:：()（）\[\]【】{}]+"
)
_FILENAME_LEVEL_CODE = re.compile(r"(?<![a-z0-9])l([1-5])(?![a-z0-9])")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def baseline_set_fingerprint(items: Iterable[Mapping[str, Any]]) -> str:
    manifest = {
        "schema_version": "baseline-set-v1",
        "items": sorted(
            (
                {
                    "asset_id": int(item["asset_id"]),
                    "asset_sha256": str(item["asset_sha256"]),
                    "expected_level": str(item["expected_level"]),
                }
                for item in items
            ),
            key=lambda item: item["asset_id"],
        ),
    }
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def filename_level_suggestion(filename: str) -> dict[str, Any]:
    """Suggest one baseline level from explicit filename tokens.

    A suggestion is advisory only. Ambiguous filenames intentionally return no
    level so callers can fall back to the batch default and keep the field
    editable.
    """

    stem = unicodedata.normalize("NFKC", Path(filename).stem).casefold()
    matches: list[dict[str, str]] = []
    for match in _FILENAME_LEVEL_CODE.finditer(stem):
        matches.append(
            {
                "level": f"L{match.group(1)}",
                "token": match.group(0),
            }
        )
    for token in _FILENAME_TOKEN_SPLIT.split(stem):
        label = token.strip()
        if label in _LEVEL_BY_LABEL:
            matches.append(
                {
                    "level": _LEVEL_BY_LABEL[label],
                    "token": label,
                }
            )

    unique_levels = sorted({item["level"] for item in matches})
    status = (
        "matched"
        if len(unique_levels) == 1
        else "conflict"
        if unique_levels
        else "unmatched"
    )
    return {
        "schema_version": "filename-level-suggestion-v1",
        "status": status,
        "suggested_level": unique_levels[0] if status == "matched" else None,
        "matches": matches,
    }


def _dimension_reason_items(aesthetic: Mapping[str, Any]) -> list[dict[str, Any]]:
    dimensions = aesthetic.get("dimensions")
    if not isinstance(dimensions, dict):
        return []
    items: list[dict[str, Any]] = []
    for key, raw in dimensions.items():
        if not isinstance(raw, dict):
            continue
        grade = raw.get("grade")
        if (
            not isinstance(grade, (int, float))
            or isinstance(grade, bool)
            or not 1 <= float(grade) <= 5
        ):
            continue
        evidence = raw.get("evidence")
        defects = raw.get("defects")
        items.append(
            {
                "key": str(key),
                "grade": int(grade),
                "evidence": [
                    str(item)
                    for item in evidence
                    if isinstance(item, str) and item.strip()
                ][:3]
                if isinstance(evidence, list)
                else [],
                "defects": [
                    str(item)
                    for item in defects
                    if isinstance(item, str) and item.strip()
                ][:3]
                if isinstance(defects, list)
                else [],
            }
        )
    return items


def level_explanation(
    *,
    precheck: Mapping[str, Any],
    aesthetic: Mapping[str, Any],
    scoring: Mapping[str, Any],
    predicted_level: str | None,
    authoritative_score: float | None,
) -> dict[str, Any]:
    classification = precheck.get("classification")
    scope_status = (
        classification.get("scope_status")
        if isinstance(classification, dict)
        else None
    )
    dimensions = _dimension_reason_items(aesthetic)
    strongest = sorted(
        (item for item in dimensions if item["grade"] >= 4),
        key=lambda item: (-item["grade"], item["key"]),
    )[:3]
    weakest = sorted(
        (item for item in dimensions if item["grade"] <= 2),
        key=lambda item: (item["grade"], item["key"]),
    )[:3]
    caps = scoring.get("caps")
    review_reasons = scoring.get("review_reasons")
    status = (
        "out_of_scope"
        if scope_status == "out_of_scope"
        else "available"
        if predicted_level in LEVELS and authoritative_score is not None
        else "incomplete"
    )
    message = (
        "素材超出评测范围，未形成正式美感等级"
        if status == "out_of_scope"
        else "评测结果不完整，未同时冻结有效等级与服务端分数"
        if status == "incomplete"
        else ""
    )
    return {
        "schema_version": "baseline-level-explanation-v1",
        "status": status,
        "predicted_level": predicted_level,
        "authoritative_score": authoritative_score,
        "scope_status": scope_status,
        "strong_dimensions": strongest,
        "weak_dimensions": weakest,
        "caps": caps if isinstance(caps, list) else [],
        "review_reasons": (
            [str(item) for item in review_reasons if isinstance(item, str)]
            if isinstance(review_reasons, list)
            else []
        ),
        "message": message,
    }


def result_snapshot(result: EvaluationResult) -> dict[str, Any]:
    precheck = _json_object(result.precheck_json)
    aesthetic = _json_object(result.aesthetic_json)
    scoring = _json_object(result.scoring_json)
    predicted_level = result.level if result.level in LEVELS else None
    return {
        "schema_version": "baseline-result-v2",
        "evaluation_id": result.id,
        "job_id": result.job_id,
        "strategy_bundle_id": result.strategy_bundle_id,
        "predicted_level": predicted_level,
        "authoritative_score": result.score,
        "cap_reasons": scoring.get("caps")
        if isinstance(scoring.get("caps"), list)
        else [],
        "stage_a": precheck,
        "level_explanation": level_explanation(
            precheck=precheck,
            aesthetic=aesthetic,
            scoring=scoring,
            predicted_level=predicted_level,
            authoritative_score=result.score,
        ),
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "versions": {
            "model": result.model_id,
            "prompt_a": result.prompt_a_version,
            "prompt_b": result.prompt_b_version,
            "rubric": result.rubric_version,
            "engine": result.engine_version,
        },
    }


def compute_level_metrics(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    matrix = {
        expected: {actual: 0 for actual in LEVELS}
        for expected in LEVELS
    }
    exact_hits = 0
    adjacent_hits = 0
    completed_count = 0
    valid_predictions = 0
    failed_count = 0
    deviations = 0

    for item in rows:
        status = str(item.get("status") or "queued")
        if status not in {"completed", "failed"}:
            continue
        completed_count += 1
        expected = str(item.get("expected_level") or "")
        actual_value = item.get("predicted_level")
        actual = str(actual_value) if actual_value is not None else ""
        if status == "failed" or expected not in LEVELS or actual not in LEVELS:
            failed_count += 1
            continue
        valid_predictions += 1
        matrix[expected][actual] += 1
        delta = abs(LEVELS.index(expected) - LEVELS.index(actual))
        if delta == 0:
            exact_hits += 1
        else:
            deviations += 1
        if delta <= 1:
            adjacent_hits += 1

    denominator = completed_count
    total = len(rows)
    return {
        "schema_version": "baseline-level-metrics-v1",
        "levels": list(LEVELS),
        "total": total,
        "completed": completed_count,
        "pending": total - completed_count,
        "denominator": denominator,
        "valid_predictions": valid_predictions,
        "failed": failed_count,
        "exact_hits": exact_hits,
        "adjacent_hits": adjacent_hits,
        "deviations": deviations,
        "exact_accuracy": exact_hits / denominator if denominator else 0.0,
        "adjacent_accuracy": adjacent_hits / denominator if denominator else 0.0,
        "confusion_matrix": matrix,
    }


def _item_metric_payload(item: BaselineRegressionItem) -> dict[str, Any]:
    snapshot = _json_object(item.result_snapshot_json)
    return {
        "status": item.status,
        "expected_level": item.expected_level,
        "predicted_level": snapshot.get("predicted_level"),
    }


def refresh_baseline_run(run: BaselineRegressionRun) -> dict[str, Any]:
    metrics = compute_level_metrics(_item_metric_payload(item) for item in run.items)
    run.completed = metrics["completed"]
    run.valid_predictions = metrics["valid_predictions"]
    run.failed = metrics["failed"]
    run.metrics_json = canonical_json(metrics)
    if metrics["pending"] == 0:
        if metrics["failed"] == run.total:
            run.status = "failed"
        elif metrics["failed"]:
            run.status = "partial_failed"
        else:
            run.status = "completed"
        run.finished_at = run.finished_at or datetime.now(timezone.utc)
    else:
        run.status = "running"
        run.finished_at = None
    return metrics


def complete_baseline_item(
    db: Session,
    *,
    item_id: int,
    result: EvaluationResult,
) -> None:
    item = db.get(BaselineRegressionItem, item_id)
    if item is None:
        raise ValueError("基准回归条目不存在")
    if item.status == "completed":
        if item.evaluation_id == result.id:
            return
        raise ValueError("基准回归条目已绑定其他评测结果")
    if item.asset_id != result.asset_id:
        raise ValueError("基准回归结果素材与冻结条目不一致")
    if result.strategy_bundle_id != item.run.strategy_bundle_id:
        raise ValueError("基准回归结果策略与冻结 run 不一致")
    item.evaluation_id = result.id
    item.job_id = result.job_id
    item.result_snapshot_json = canonical_json(result_snapshot(result))
    item.status = "completed"
    item.error_message = ""
    item.finished_at = datetime.now(timezone.utc)
    refresh_baseline_run(item.run)


def fail_baseline_item(
    db: Session,
    *,
    item_id: int,
    error_code: str,
    job_id: int | None = None,
) -> None:
    item = db.get(BaselineRegressionItem, item_id)
    if item is None or item.status == "completed":
        return
    item.status = "failed"
    if job_id is not None:
        item.job_id = job_id
    item.error_message = error_code[:200]
    item.finished_at = datetime.now(timezone.utc)
    refresh_baseline_run(item.run)


def run_comparison(
    current: BaselineRegressionRun,
    previous: BaselineRegressionRun | None,
) -> dict[str, Any]:
    current_metrics = _json_object(current.metrics_json)
    previous_metrics = _json_object(previous.metrics_json) if previous else {}
    comparable = bool(
        previous is not None
        and current.status in TERMINAL_RUN_STATUSES
        and previous.status in TERMINAL_RUN_STATUSES
        and current.baseline_set_fingerprint
        == previous.baseline_set_fingerprint
        and current_metrics.get("denominator", 0) > 0
        and previous_metrics.get("denominator", 0) > 0
    )
    return {
        "comparable": comparable,
        "previous_run_id": previous.id if previous else None,
        "current_sequence_no": current.sequence_no,
        "previous_sequence_no": previous.sequence_no if previous else None,
        "exact_accuracy_delta": (
            current_metrics.get("exact_accuracy", 0.0)
            - previous_metrics.get("exact_accuracy", 0.0)
            if comparable
            else None
        ),
        "adjacent_accuracy_delta": (
            current_metrics.get("adjacent_accuracy", 0.0)
            - previous_metrics.get("adjacent_accuracy", 0.0)
            if comparable
            else None
        ),
        "current": {
            key: current_metrics.get(key, 0)
            for key in ("total", "valid_predictions", "failed")
        },
        "previous": (
            {
                key: previous_metrics.get(key, 0)
                for key in ("total", "valid_predictions", "failed")
            }
            if previous
            else None
        ),
    }
