from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from .models import (
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationResult,
)


LEVELS = ("L1", "L2", "L3", "L4", "L5")
TERMINAL_RUN_STATUSES = {"completed", "partial_failed", "failed"}


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


def result_snapshot(result: EvaluationResult) -> dict[str, Any]:
    precheck = _json_object(result.precheck_json)
    scoring = _json_object(result.scoring_json)
    predicted_level = result.level if result.level in LEVELS else None
    return {
        "schema_version": "baseline-result-v1",
        "evaluation_id": result.id,
        "job_id": result.job_id,
        "strategy_bundle_id": result.strategy_bundle_id,
        "predicted_level": predicted_level,
        "authoritative_score": result.score,
        "cap_reasons": scoring.get("caps")
        if isinstance(scoring.get("caps"), list)
        else [],
        "stage_a": precheck,
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
