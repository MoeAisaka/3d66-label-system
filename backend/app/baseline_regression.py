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
    BaselineCorrectionRun,
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationJob,
    EvaluationResult,
)
from .category_pipeline import dimension_selection_from_job_snapshot


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
_QUALITY_SEVERITY_LABELS = {
    "none": "无明显问题",
    "slight": "轻微",
    "moderate": "中等",
    "severe": "严重",
    "unusable": "不可用",
}
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


def baseline_set_fingerprint(
    items: Iterable[Mapping[str, Any]],
    *,
    category_key: str = "space_image",
) -> str:
    manifest = {
        "schema_version": "baseline-set-v2",
        "category_key": category_key,
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


def _raw_text(value: str | None) -> str | None:
    payload = _json_object(value)
    raw_text = payload.get("raw_text")
    return raw_text if isinstance(raw_text, str) else None


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


def _image_quality_summary(precheck: Mapping[str, Any]) -> dict[str, Any]:
    quality = precheck.get("image_quality")
    if not isinstance(quality, dict):
        return {
            "status": "missing",
            "severity": None,
            "severity_label": "",
            "confidence": None,
            "evidence": [],
        }
    severity = quality.get("quality_severity")
    confidence = quality.get("confidence")
    evidence = quality.get("evidence")
    return {
        "status": "available",
        "severity": str(severity) if isinstance(severity, str) else None,
        "severity_label": _QUALITY_SEVERITY_LABELS.get(
            str(severity), str(severity) if severity is not None else ""
        ),
        "confidence": (
            float(confidence)
            if isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            else None
        ),
        "evidence": (
            [
                str(item)
                for item in evidence
                if isinstance(item, str) and item.strip()
            ][:5]
            if isinstance(evidence, list)
            else []
        ),
    }


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
        "all_dimensions": sorted(
            dimensions, key=lambda item: (item["grade"], item["key"])
        ),
        "image_quality": _image_quality_summary(precheck),
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
    job = result.job
    dimension_selection = dimension_selection_from_job_snapshot(
        job.category_profile_snapshot_json if job is not None else None
    )
    execution_snapshot = _json_object(
        job.category_profile_snapshot_json if job is not None else None
    )
    execution_mode = str(execution_snapshot.get("execution_mode") or "structured")
    scored = (
        predicted_level in LEVELS
        and isinstance(result.score, (int, float))
        and not isinstance(result.score, bool)
    )
    return {
        "schema_version": "baseline-result-v3",
        "evaluation_id": result.id,
        "job_id": result.job_id,
        "strategy_bundle_id": result.strategy_bundle_id,
        "category_key": (
            job.category_key if job is not None else result.asset.category_key
        ),
        "dimension_selection": dimension_selection,
        "execution_mode": execution_mode,
        "interpretation": {
            "status": "scored" if scored else "manual_required",
            "raw_text_a": _raw_text(result.raw_response_a),
            "raw_text_b": _raw_text(result.raw_response_b),
        },
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
    unscored_count = 0
    manual_required_count = 0
    deviations = 0

    for item in rows:
        status = str(item.get("status") or "queued")
        if status not in {"completed", "failed"}:
            continue
        completed_count += 1
        expected = str(item.get("expected_level") or "")
        actual_value = item.get("predicted_level")
        actual = str(actual_value) if actual_value is not None else ""
        if status == "failed":
            failed_count += 1
            continue
        manual_required = bool(item.get("manual_required"))
        if expected not in LEVELS or actual not in LEVELS:
            unscored_count += 1
            manual_required_count += 1 if manual_required or actual not in LEVELS else 0
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

    denominator = valid_predictions
    total = len(rows)
    return {
        "schema_version": "baseline-level-metrics-v2",
        "levels": list(LEVELS),
        "total": total,
        "completed": completed_count,
        "pending": total - completed_count,
        "denominator": denominator,
        "valid_predictions": valid_predictions,
        "failed": failed_count,
        "unscored": unscored_count,
        "manual_required": manual_required_count,
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
        "manual_required": (
            (snapshot.get("interpretation") or {}).get("status")
            == "manual_required"
        ),
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
    invalid_reasons: list[str] = []
    if result.level not in LEVELS:
        invalid_reasons.append("missing_level")
    if (
        not isinstance(result.score, (int, float))
        or isinstance(result.score, bool)
    ):
        invalid_reasons.append("no_authoritative_score")
    run_execution = _json_object(item.run.execution_snapshot_json)
    execution_mode = str(run_execution.get("execution_mode") or "structured")
    if invalid_reasons and execution_mode == "structured":
        item.evaluation_id = result.id
        item.job_id = result.job_id
        item.result_snapshot_json = canonical_json(result_snapshot(result))
        item.status = "failed"
        item.error_message = "invalid_evaluation_result:" + ",".join(invalid_reasons)
        item.finished_at = datetime.now(timezone.utc)
        job = db.get(EvaluationJob, result.job_id) if result.job_id is not None else None
        if job is not None:
            job.status = "failed"
            job.stage = "failed"
            job.progress = 100
            job.error_message = item.error_message[:500]
            job.finished_at = item.finished_at
        refresh_baseline_run(item.run)
        return
    frozen_selection = run_execution.get("dimension_selection")
    if frozen_selection is not None:
        if result.job is None or result.job.category_key != item.run.category_key:
            raise ValueError("基准回归结果类目与冻结 run 不一致")
        result_selection = dimension_selection_from_job_snapshot(
            result.job.category_profile_snapshot_json
        )
        if result_selection != frozen_selection:
            raise ValueError("基准回归结果维度选择与冻结 run 不一致")
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


def correction_input_snapshot(
    run: BaselineRegressionRun,
    selected_items: Iterable[BaselineRegressionItem],
) -> dict[str, Any]:
    """Freeze validated deviation rows for deterministic correction analysis."""

    items = sorted(selected_items, key=lambda item: item.id)
    if run.status not in TERMINAL_RUN_STATUSES:
        raise ValueError("基准回归尚未结束，不能创建纠偏分析")
    if not items:
        raise ValueError("至少选择一个已完成偏差样本")
    if any(item.run_id != run.id for item in items):
        raise ValueError("纠偏样本不属于同一基准回归")

    try:
        execution_snapshot = json.loads(run.execution_snapshot_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("基准回归执行快照损坏") from exc
    if not isinstance(execution_snapshot, dict):
        raise ValueError("基准回归执行快照损坏")
    dimension_selection = execution_snapshot.get("dimension_selection")
    if dimension_selection is not None and not isinstance(
        dimension_selection, dict
    ):
        raise ValueError("基准回归冻结维度选择损坏")

    frozen_rows: list[dict[str, Any]] = []
    for item in items:
        try:
            result = json.loads(item.result_snapshot_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"条目 #{item.id} 结果快照损坏") from exc
        if not isinstance(result, dict):
            raise ValueError(f"条目 #{item.id} 结果快照损坏")
        predicted = result.get("predicted_level")
        if (
            item.status != "completed"
            or predicted not in LEVELS
            or predicted == item.expected_level
        ):
            raise ValueError(f"条目 #{item.id} 不是已完成偏差样本")
        frozen_rows.append(
            {
                "item_id": item.id,
                "asset_id": item.asset_id,
                "evaluation_id": item.evaluation_id,
                "expected_level": item.expected_level,
                "predicted_level": predicted,
                "authoritative_score": result.get("authoritative_score"),
                "confidence": result.get("confidence"),
                "needs_review": result.get("needs_review"),
                "level_explanation": result.get("level_explanation") or {},
                "category_key": result.get("category_key") or run.category_key,
                "dimension_selection": result.get("dimension_selection"),
                "versions": result.get("versions") or {},
            }
        )
    if any(row["category_key"] != run.category_key for row in frozen_rows):
        raise ValueError("偏差样本与基准回归类目不一致")

    return {
        "schema_version": "baseline-correction-input-v1",
        "baseline_run_id": run.id,
        "baseline_set_id": run.baseline_set_id,
        "baseline_set_fingerprint": run.baseline_set_fingerprint,
        "category_key": run.category_key,
        "strategy_bundle_id": run.strategy_bundle_id,
        "execution_snapshot": execution_snapshot,
        "dimension_selection": dimension_selection,
        "run_metrics": _json_object(run.metrics_json),
        "items": frozen_rows,
    }


def _rank_counts(counter: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(
            counter.items(), key=lambda pair: (-pair[1], pair[0])
        )
    ]


def deterministic_correction_report(
    input_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Analyze frozen deviations without a model call or publication side effect."""

    raw_items = input_snapshot.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("纠偏分析输入不包含样本")
    rows: list[dict[str, Any]] = []
    direction_counts: dict[str, int] = {}
    distance_counts: dict[str, int] = {}
    pair_counts: dict[str, int] = {}
    weak_dimension_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    cap_counts: dict[str, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("纠偏分析样本结构损坏")
        expected = raw.get("expected_level")
        predicted = raw.get("predicted_level")
        if expected not in LEVELS or predicted not in LEVELS or expected == predicted:
            raise ValueError("纠偏分析仅接受有效偏差样本")
        expected_index = LEVELS.index(expected)
        predicted_index = LEVELS.index(predicted)
        distance = abs(expected_index - predicted_index)
        direction = "under_rated" if predicted_index > expected_index else "over_rated"
        pair = f"{expected}→{predicted}"
        direction_counts[direction] = direction_counts.get(direction, 0) + 1
        distance_counts[str(distance)] = distance_counts.get(str(distance), 0) + 1
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
        explanation = raw.get("level_explanation")
        explanation = explanation if isinstance(explanation, dict) else {}
        for dimension in explanation.get("weak_dimensions") or []:
            if isinstance(dimension, dict) and isinstance(dimension.get("key"), str):
                key = dimension["key"]
                weak_dimension_counts[key] = weak_dimension_counts.get(key, 0) + 1
        quality = explanation.get("image_quality")
        if isinstance(quality, dict) and isinstance(quality.get("severity"), str):
            key = quality["severity"]
            quality_counts[key] = quality_counts.get(key, 0) + 1
        for cap in explanation.get("caps") or []:
            if isinstance(cap, dict):
                reason = cap.get("reason") or cap.get("cap")
            else:
                reason = cap
            if isinstance(reason, str) and reason.strip():
                key = reason.strip()
                cap_counts[key] = cap_counts.get(key, 0) + 1
        rows.append(
            {
                "status": "completed",
                "expected_level": expected,
                "predicted_level": predicted,
            }
        )

    accuracy = compute_level_metrics(rows)
    average_distance = sum(
        int(distance) * count for distance, count in distance_counts.items()
    ) / len(raw_items)
    dimension_selection = input_snapshot.get("dimension_selection")
    dimension_selection = (
        dimension_selection if isinstance(dimension_selection, dict) else {}
    )
    top_direction = _rank_counts(direction_counts)[0]["key"]
    prompt_recommendations = [
        {
            "code": "inspect_level_anchors",
            "priority": "high" if direction_counts[top_direction] >= len(raw_items) * 0.6 else "medium",
            "message": (
                "模型更常给出低于真值质量的等级，优先检查负向证据和封顶措辞。"
                if top_direction == "under_rated"
                else "模型更常给出高于真值质量的等级，优先收紧高等级锚点和反例。"
            ),
            "supporting_samples": direction_counts[top_direction],
        }
    ]
    if cap_counts:
        prompt_recommendations.append(
            {
                "code": "inspect_cap_evidence",
                "priority": "medium",
                "message": "偏差样本集中出现等级限制，核对提示词证据与服务端封顶条件。",
                "supporting_samples": sum(cap_counts.values()),
            }
        )
    if dimension_selection.get("mode") == "none":
        dimension_recommendations = []
    elif weak_dimension_counts:
        top_dimensions = [
            item["key"] for item in _rank_counts(weak_dimension_counts)[:3]
        ]
        dimension_recommendations = [
            {
                "dimension_key": key,
                "priority": "high" if weak_dimension_counts[key] >= max(3, len(raw_items) // 2) else "medium",
                "message": "复核该维度的定义、权重和证据锚点。",
                "signals": {"weak_dimension": weak_dimension_counts[key]},
            }
            for key in top_dimensions
        ]
    else:
        dimension_recommendations = []

    return {
        "schema_version": "baseline-correction-report-v1",
        "status": "automatic_candidate_pipeline",
        "category_key": input_snapshot.get("category_key"),
        "baseline_run_id": input_snapshot.get("baseline_run_id"),
        "selection": {
            "policy": "explicit_completed_deviations",
            "count": len(raw_items),
            "item_ids": [int(item["item_id"]) for item in raw_items],
        },
        "accuracy_report": {
            "run_metrics": input_snapshot.get("run_metrics") or {},
            "selected_deviation_count": len(raw_items),
            "average_level_distance": round(average_distance, 3),
            "direction_counts": direction_counts,
            "confusion_pairs": [
                {"pair": pair, "count": count}
                for pair, count in sorted(pair_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        "attribution": {
            "dominant_direction": top_direction,
            "prompt_only": dimension_selection.get("mode") == "none",
            "dimension_signal_count": len(weak_dimension_counts),
        },
        "prompt_suggestions": prompt_recommendations,
        "dimension_suggestions": dimension_recommendations,
        "confidence": "high" if len(raw_items) >= 30 else "medium" if len(raw_items) >= 10 else "low",
        "risks": (["差异样本少于 10，建议仅作优化候选。"] if len(raw_items) < 10 else []),
        "publication": {
            "allowed": False,
            "next_state": "automatic_candidate_regression",
            "message": "系统将自动生成候选并执行回归；仅最终启用或拒绝需要人工决策。",
        },
    }


def execute_correction_run(correction: BaselineCorrectionRun) -> None:
    """Persist deterministic analysis before automatic orchestration continues."""

    try:
        snapshot = json.loads(correction.input_snapshot_json)
    except json.JSONDecodeError as exc:
        raise ValueError("纠偏分析冻结输入损坏") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("纠偏分析冻结输入损坏")
    correction.status = "processing"
    correction.stage = "analysis"
    correction.progress = 25
    correction.report_json = canonical_json(
        deterministic_correction_report(snapshot)
    )
    correction.stage = "candidate_generation"
    correction.blockers_json = "[]"
    correction.error_code = ""
    correction.error_message = ""
    correction.finished_at = None


def fail_correction_run(
    correction: BaselineCorrectionRun,
    *,
    error_code: str,
    error_message: str,
) -> None:
    correction.status = "failed"
    correction.progress = 0
    correction.blockers_json = canonical_json(
        [
            {
                "code": error_code,
                "message": error_message[:500],
                "retryable": correction.attempt_count < 3,
            }
        ]
    )
    correction.error_code = error_code[:80]
    correction.error_message = error_message[:500]
    correction.finished_at = datetime.now(timezone.utc)
