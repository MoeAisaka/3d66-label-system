from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    BaselineCorrectionRun,
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationJob,
    EvaluationResult,
    SampleSet,
    SampleSetItem,
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
BASELINE_OPTIMIZATION_CASE_PURPOSE = (
    "把偏差样本沉淀到全局优化案例池，供后续自动组批和长期机制优化使用；"
    "不影响当前纠偏分析，不修改本轮真值，也不自动启用候选。"
)
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
_RELEASE_KEY_FIELD_PREFIXES = (
    "scope_status",
    "primary_category",
    "quality_severity",
    "media_form.",
    "media_type.",
    "shooting_method.",
    "hard_gate.",
    "level_cap",
    "dimensions.",
    "production_fields.",
)


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
        "stage_b": aesthetic,
        "scoring": scoring,
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


def _field_value_key(value: Any) -> str:
    if value is None:
        return "__missing__"
    if isinstance(value, str):
        return value if value else "__empty__"
    return canonical_json(value)


def _flatten_metric_fields(
    payload: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in sorted(payload):
        value = payload[key]
        field_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            fields.update(_flatten_metric_fields(value, prefix=field_key))
        else:
            fields[field_key] = value
    return fields


def metric_truth_fields(
    truth: Mapping[str, Any],
    *,
    expected_level: str,
) -> dict[str, Any]:
    """Normalize frozen human truth into stable field paths for evidence."""
    normalized: dict[str, Any] = {"level": expected_level}
    for key in (
        "scope_status",
        "primary_category",
        "quality_severity",
        "media_form",
        "media_type",
        "shooting_method",
        "hard_gate",
        "level_cap",
        "dimensions",
    ):
        value = truth.get(key)
        if value is not None:
            normalized[key] = value
    if "primary_category" not in normalized and truth.get("category") is not None:
        normalized["primary_category"] = truth["category"]

    key_fields = truth.get("key_fields")
    if isinstance(key_fields, Mapping):
        aliases = {
            "classification.scope_status": "scope_status",
            "classification.primary_category": "primary_category",
            "image_quality.quality_severity": "quality_severity",
        }
        for key, value in key_fields.items():
            normalized[aliases.get(str(key), str(key))] = value
    return _flatten_metric_fields(normalized)


def metric_prediction_fields(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Read candidate fields from the immutable baseline result snapshot."""
    stage_a = snapshot.get("stage_a")
    stage_a = stage_a if isinstance(stage_a, Mapping) else {}
    classification = stage_a.get("classification")
    classification = classification if isinstance(classification, Mapping) else {}
    quality = stage_a.get("image_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    predicted: dict[str, Any] = {
        "level": snapshot.get("predicted_level"),
        "scope_status": classification.get("scope_status"),
        "primary_category": classification.get("primary_category"),
        "quality_severity": quality.get("quality_severity"),
    }
    for key in (
        "media_form",
        "media_type",
        "shooting_method",
        "production_fields",
    ):
        value = stage_a.get(key)
        if isinstance(value, Mapping):
            predicted[key] = value

    explanation = snapshot.get("level_explanation")
    explanation = explanation if isinstance(explanation, Mapping) else {}
    raw_dimensions = explanation.get("all_dimensions")
    if isinstance(raw_dimensions, list):
        dimensions = {
            str(item["key"]): item.get("grade")
            for item in raw_dimensions
            if isinstance(item, Mapping) and item.get("key") is not None
        }
        if dimensions:
            predicted["dimensions"] = dimensions
    return _flatten_metric_fields(predicted)


def compute_field_metrics(
    items: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate exact field evidence without making release decisions."""
    rows = list(items)
    field_keys = sorted(
        {
            field_key
            for row in rows
            for field_key in (row.get("truth_fields") or {})
        }
    )
    field_metrics: list[dict[str, Any]] = []
    all_failures: set[int] = set()

    for field_key in field_keys:
        confusion: dict[str, dict[str, int]] = {}
        expected_counts: dict[str, int] = {}
        expected_hits: dict[str, int] = {}
        support = 0
        tp = 0
        failures: list[int] = []
        for row in rows:
            truth_fields = row.get("truth_fields") or {}
            if field_key not in truth_fields:
                continue
            support += 1
            expected = _field_value_key(truth_fields[field_key])
            predicted_fields = row.get("prediction_fields") or {}
            predicted = _field_value_key(predicted_fields.get(field_key))
            confusion.setdefault(expected, {})[predicted] = (
                confusion.setdefault(expected, {}).get(predicted, 0) + 1
            )
            expected_counts[expected] = expected_counts.get(expected, 0) + 1
            if expected == predicted:
                tp += 1
                expected_hits[expected] = expected_hits.get(expected, 0) + 1
            else:
                sample_id = int(row["sample_id"])
                failures.append(sample_id)
                all_failures.add(sample_id)
        mismatch_count = support - tp
        recalls = [
            expected_hits.get(expected, 0) / count
            for expected, count in expected_counts.items()
            if count
        ]
        field_metrics.append(
            {
                "field_key": field_key,
                "support": support,
                "tp": tp,
                "fp": mismatch_count,
                "fn": mismatch_count,
                "accuracy": tp / support if support else 0.0,
                "recall": sum(recalls) / len(recalls) if recalls else 0.0,
                "confusion_matrix": {
                    expected: dict(sorted(predictions.items()))
                    for expected, predictions in sorted(confusion.items())
                },
                "failure_sample_ids": sorted(set(failures)),
            }
        )

    supported = [item for item in field_metrics if item["support"]]
    total_support = sum(item["support"] for item in supported)
    total_tp = sum(item["tp"] for item in supported)
    total_fp = sum(item["fp"] for item in supported)
    total_fn = sum(item["fn"] for item in supported)
    return {
        "field_metrics": field_metrics,
        "aggregates": {
            "macro": {
                "field_count": len(supported),
                "accuracy": (
                    sum(item["accuracy"] for item in supported) / len(supported)
                    if supported
                    else 0.0
                ),
                "recall": (
                    sum(item["recall"] for item in supported) / len(supported)
                    if supported
                    else 0.0
                ),
            },
            "micro": {
                "support": total_support,
                "tp": total_tp,
                "fp": total_fp,
                "fn": total_fn,
                "accuracy": total_tp / total_support if total_support else 0.0,
                "recall": total_tp / (total_tp + total_fn)
                if total_tp + total_fn
                else 0.0,
            },
        },
        "failure_sample_ids": sorted(all_failures),
    }


def build_baseline_field_metrics(
    db: Session,
    run: BaselineRegressionRun,
) -> dict[str, Any]:
    """Build read-only field evidence from a frozen run and locked Gold."""
    asset_ids = [item.asset_id for item in run.items]
    golden_items = (
        db.scalars(
            select(SampleSetItem)
            .join(SampleSet, SampleSet.id == SampleSetItem.sample_set_id)
            .where(
                SampleSet.category_key == run.category_key,
                SampleSet.kind == "golden",
                SampleSet.status == "locked",
                SampleSetItem.asset_id.in_(asset_ids),
            )
            .order_by(
                SampleSetItem.asset_id.asc(),
                SampleSetItem.truth_revision.desc(),
                SampleSetItem.id.desc(),
            )
        ).all()
        if asset_ids
        else []
    )
    golden_by_asset: dict[int, SampleSetItem] = {}
    for item in golden_items:
        golden_by_asset.setdefault(item.asset_id, item)

    metric_rows: list[dict[str, Any]] = []
    truth_sources: set[int] = set()
    truth_revisions: list[int] = []
    snapshot_versions: dict[str, set[str]] = {
        "model": set(),
        "prompt_a": set(),
        "prompt_b": set(),
        "rubric": set(),
        "engine": set(),
    }
    asset_hashes: list[str] = []
    for item in run.items:
        snapshot = _json_object(item.result_snapshot_json)
        golden = golden_by_asset.get(item.asset_id)
        truth = _json_object(golden.truth_json) if golden else {}
        if golden is not None:
            truth_sources.add(golden.sample_set_id)
            truth_revisions.append(golden.truth_revision)
        asset_snapshot = _json_object(item.baseline_set_item.asset_snapshot_json)
        asset_hash = asset_snapshot.get("sha256")
        if isinstance(asset_hash, str):
            asset_hashes.append(asset_hash)
        for key, value in (snapshot.get("versions") or {}).items():
            if key in snapshot_versions and value is not None:
                snapshot_versions[key].add(str(value))
        metric_rows.append(
            {
                "sample_id": item.asset_id,
                "truth_fields": metric_truth_fields(
                    truth,
                    expected_level=item.expected_level,
                ),
                "prediction_fields": metric_prediction_fields(snapshot),
            }
        )

    metrics = compute_field_metrics(metric_rows)
    golden_asset_ids = set(golden_by_asset)
    metrics["golden_failure_sample_ids"] = sorted(
        golden_asset_ids & set(metrics["failure_sample_ids"])
    )
    execution = _json_object(run.execution_snapshot_json)
    v3_bundle = execution.get("v3_authoritative_bundle")
    v3_bundle = v3_bundle if isinstance(v3_bundle, dict) else {}
    mechanism = v3_bundle.get("contract")
    mechanism = mechanism if isinstance(mechanism, dict) else {}
    bundle = run.strategy_bundle
    return {
        "schema_version": "baseline-field-metrics-v1",
        "run_id": run.id,
        "category_key": run.category_key,
        **metrics,
        "versions": {
            "model": sorted(snapshot_versions["model"] or {bundle.model_id}),
            "prompt": {
                "a": sorted(
                    snapshot_versions["prompt_a"] or {bundle.prompt_a_version}
                ),
                "b": sorted(
                    snapshot_versions["prompt_b"]
                    or ({bundle.prompt_b_version} if bundle.prompt_b_version else set())
                ),
            },
            "mechanism": {
                "spec_version": mechanism.get("spec_version"),
                "rubric": sorted(
                    snapshot_versions["rubric"] or {bundle.rubric_version}
                ),
                "engine": sorted(
                    snapshot_versions["engine"] or {bundle.engine_version}
                ),
                "strategy_bundle_id": bundle.id,
                "strategy_canonical_id": bundle.canonical_hash,
            },
            "asset": {
                "baseline_set_fingerprint": run.baseline_set_fingerprint,
                "count": len(asset_ids),
                "payload_hash": hashlib.sha256(
                    canonical_json(sorted(asset_hashes)).encode("utf-8")
                ).hexdigest(),
            },
            "truth": {
                "locked_sample_set_ids": sorted(truth_sources),
                "revision_min": min(truth_revisions) if truth_revisions else 0,
                "revision_max": max(truth_revisions) if truth_revisions else 0,
                "matched_asset_count": len(golden_by_asset),
            },
        },
        "decision_policy": {
            "evidence_only": True,
            "auto_activate_candidate": False,
        },
    }


def field_metric_release_regressions(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return field evidence that must block candidate approval."""
    baseline_by_key = {
        str(item.get("field_key")): item
        for item in baseline.get("field_metrics") or []
        if isinstance(item, Mapping)
    }
    candidate_by_key = {
        str(item.get("field_key")): item
        for item in candidate.get("field_metrics") or []
        if isinstance(item, Mapping)
    }
    regressions: list[dict[str, Any]] = []
    for field_key in sorted(set(baseline_by_key) & set(candidate_by_key)):
        if not field_key.startswith(_RELEASE_KEY_FIELD_PREFIXES):
            continue
        baseline_metric = baseline_by_key[field_key]
        candidate_metric = candidate_by_key[field_key]
        accuracy_delta = float(candidate_metric.get("accuracy", 0.0)) - float(
            baseline_metric.get("accuracy", 0.0)
        )
        recall_delta = float(candidate_metric.get("recall", 0.0)) - float(
            baseline_metric.get("recall", 0.0)
        )
        if accuracy_delta < 0 or recall_delta < 0:
            regressions.append(
                {
                    "code": "key_field_regressed",
                    "message": f"关键字段 {field_key} 低于基准",
                    "field_key": field_key,
                    "accuracy_delta": accuracy_delta,
                    "recall_delta": recall_delta,
                }
            )

    failures = sorted(
        {
            int(item)
            for item in candidate.get("golden_failure_sample_ids") or []
        }
    )
    if failures:
        regressions.append(
            {
                "code": "golden_set_failure",
                "message": "候选在锁定黄金真值上仍存在字段失败",
                "failure_sample_ids": failures,
            }
        )
    return regressions


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
        _persist_semantic_quality_if_terminal(db, item.run)
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
    _persist_semantic_quality_if_terminal(db, item.run)


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
    _persist_semantic_quality_if_terminal(db, item.run)


def _persist_semantic_quality_if_terminal(
    db: Session,
    run: BaselineRegressionRun,
) -> None:
    if run.status not in TERMINAL_RUN_STATUSES:
        return
    from .semantic_tag_quality import persist_run_semantic_quality_snapshot

    persist_run_semantic_quality_snapshot(db, run=run)


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


_CORRECTION_LAYER_ORDER = ("A", "B", "V3")
_HUMAN_NODE_LAYER = {
    "call_a_field": "A",
    "precheck_field": "A",
    "redline": "A",
    "track": "A",
    "dimension_rule": "V3",
    "final_level": "V3",
}


def _strict_json_list(value: str | None, *, label: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}损坏") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{label}损坏")
    return parsed


def _evidence_timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _node_correction_source(event: Mapping[str, Any]) -> str:
    policy = event.get("corrector_policy")
    if event.get("corrector_confidence") is not None or (
        isinstance(policy, str) and policy.strip()
    ):
        return "automatic"
    return "human"


def _normalized_node_corrections(
    evaluation: EvaluationResult,
    *,
    item_id: int,
) -> list[dict[str, Any]]:
    raw_history = _strict_json_list(
        evaluation.correction_history_json,
        label=f"条目 #{item_id} 节点纠偏历史",
    )
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_history, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"条目 #{item_id} 节点纠偏历史损坏")
        evidence = raw.get("evidence")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            raise ValueError(
                f"条目 #{item_id} 第 {index} 条节点纠偏证据损坏"
            )
        normalized.append(
            {
                "correction_key": raw.get("correction_key"),
                "node_type": raw.get("node_type"),
                "node_path": raw.get("node_path"),
                "old_value": raw.get("old_value"),
                "new_value": raw.get("new_value"),
                "evidence": evidence,
                "reason": raw.get("reason") or "",
                "corrector": raw.get("corrector") or "",
                "corrector_confidence": raw.get("corrector_confidence"),
                "corrector_policy": raw.get("corrector_policy"),
                "corrected_at": _evidence_timestamp(raw.get("corrected_at")),
                "downstream_recomputed": bool(
                    raw.get("downstream_recomputed")
                ),
                "source": _node_correction_source(raw),
            }
        )
    return normalized


def _visible_human_reviews(
    evaluation: EvaluationResult,
    *,
    item_id: int,
) -> tuple[list[dict[str, Any]], int | None]:
    panel = getattr(evaluation, "review_panel", None)
    reviews = list(getattr(evaluation, "reviews", []) or [])
    if panel is not None and getattr(panel, "status", None) != "completed":
        reviews = [review for review in reviews if review.panel_id is None]
    reviews.sort(
        key=lambda review: (
            _evidence_timestamp(getattr(review, "created_at", None)) or "",
            int(getattr(review, "id", 0) or 0),
        )
    )
    final_review_id = None
    if panel is not None and getattr(panel, "status", None) == "completed":
        panel_final_id = getattr(panel, "final_review_id", None)
        if isinstance(panel_final_id, int):
            final_review_id = panel_final_id
    elif getattr(evaluation, "review_stage", None) == "completed" and reviews:
        legacy_final_id = getattr(reviews[-1], "id", None)
        if isinstance(legacy_final_id, int):
            final_review_id = legacy_final_id

    normalized: list[dict[str, Any]] = []
    for review in reviews:
        corrections = _strict_json_list(
            getattr(review, "corrections_json", None),
            label=f"条目 #{item_id} 人工审核字段纠错",
        )
        if any(not isinstance(correction, dict) for correction in corrections):
            raise ValueError(f"条目 #{item_id} 人工审核字段纠错损坏")
        review_id = getattr(review, "id", None)
        normalized.append(
            {
                "review_id": review_id,
                "reviewer_name": getattr(review, "reviewer_name", "") or "",
                "stage": getattr(review, "stage", None),
                "decision": getattr(review, "decision", None),
                "corrected_level": getattr(review, "corrected_level", None),
                "corrected_score": getattr(review, "corrected_score", None),
                "note": getattr(review, "note", "") or "",
                "corrections": corrections,
                "panel_id": getattr(review, "panel_id", None),
                "panel_revision": getattr(review, "panel_revision", None),
                "created_at": _evidence_timestamp(
                    getattr(review, "created_at", None)
                ),
                "is_final": review_id == final_review_id,
            }
        )
    return normalized, final_review_id


def _human_evidence_layers(
    node_corrections: Iterable[Mapping[str, Any]],
    human_reviews: Iterable[Mapping[str, Any]],
) -> list[str]:
    layers: set[str] = set()
    for event in node_corrections:
        if event.get("source") != "human":
            continue
        layer = _HUMAN_NODE_LAYER.get(str(event.get("node_type") or ""))
        if layer is not None:
            layers.add(layer)
    for review in human_reviews:
        routed_correction = False
        for correction in review.get("corrections") or []:
            target_type = correction.get("target_type")
            if target_type == "key_field":
                layers.add("A")
                routed_correction = True
            elif target_type == "dimension":
                layers.add("B")
                routed_correction = True
        if (
            not routed_correction
            and review.get("decision") == "corrected"
            and review.get("corrected_level") in LEVELS
        ):
            layers.add("V3")
    return [layer for layer in _CORRECTION_LAYER_ORDER if layer in layers]


def _correction_context(
    item: BaselineRegressionItem,
) -> dict[str, Any]:
    evaluation = getattr(item, "evaluation", None)
    if evaluation is None:
        return {
            "schema_version": "baseline-correction-human-evidence-v1",
            "evaluation_id": item.evaluation_id,
            "review_stage": None,
            "review_revision": None,
            "final_review_id": None,
            "node_corrections": [],
            "human_reviews": [],
            "human_evidence_count": 0,
            "automatic_evidence_count": 0,
            "affected_layers": [],
        }
    node_corrections = _normalized_node_corrections(
        evaluation,
        item_id=item.id,
    )
    human_reviews, final_review_id = _visible_human_reviews(
        evaluation,
        item_id=item.id,
    )
    human_node_count = sum(
        1 for event in node_corrections if event["source"] == "human"
    )
    return {
        "schema_version": "baseline-correction-human-evidence-v1",
        "evaluation_id": evaluation.id,
        "review_stage": evaluation.review_stage,
        "review_revision": evaluation.review_revision,
        "final_review_id": final_review_id,
        "node_corrections": node_corrections,
        "human_reviews": human_reviews,
        "human_evidence_count": human_node_count + len(human_reviews),
        "automatic_evidence_count": len(node_corrections) - human_node_count,
        "affected_layers": _human_evidence_layers(
            node_corrections,
            human_reviews,
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
                "correction_context": _correction_context(item),
            }
        )
    if any(row["category_key"] != run.category_key for row in frozen_rows):
        raise ValueError("偏差样本与基准回归类目不一致")

    return {
        "schema_version": "baseline-correction-input-v2",
        "baseline_run_id": run.id,
        "baseline_set_id": run.baseline_set_id,
        "baseline_set_fingerprint": run.baseline_set_fingerprint,
        "category_key": run.category_key,
        "strategy_bundle_id": run.strategy_bundle_id,
        "execution_snapshot": execution_snapshot,
        "correction_contract": (
            {
                "contract_version": _json_object(
                    run.correction_contract_json
                ).get("contract_version"),
                "contract_hash": run.correction_contract_hash,
                "category_key": run.category_key,
            }
            if getattr(run, "correction_contract_hash", None)
            else None
        ),
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
    sample_evidence: list[dict[str, Any]] = []
    samples_with_human_evidence = 0
    human_evidence_count = 0
    automatic_evidence_count = 0
    affected_layer_counts: dict[str, int] = {}
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
        context = raw.get("correction_context")
        context = context if isinstance(context, dict) else {}
        node_corrections = context.get("node_corrections")
        node_corrections = (
            [event for event in node_corrections if isinstance(event, dict)]
            if isinstance(node_corrections, list)
            else []
        )
        human_node_corrections = [
            event for event in node_corrections if event.get("source") == "human"
        ]
        human_reviews = context.get("human_reviews")
        human_reviews = (
            [review for review in human_reviews if isinstance(review, dict)]
            if isinstance(human_reviews, list)
            else []
        )
        frozen_human_count = context.get("human_evidence_count")
        sample_human_count = (
            frozen_human_count
            if isinstance(frozen_human_count, int)
            and not isinstance(frozen_human_count, bool)
            and frozen_human_count >= 0
            else len(human_node_corrections) + len(human_reviews)
        )
        frozen_automatic_count = context.get("automatic_evidence_count")
        sample_automatic_count = (
            frozen_automatic_count
            if isinstance(frozen_automatic_count, int)
            and not isinstance(frozen_automatic_count, bool)
            and frozen_automatic_count >= 0
            else sum(
                1
                for event in node_corrections
                if event.get("source") == "automatic"
            )
        )
        raw_layers = context.get("affected_layers")
        sample_layers = (
            [
                layer
                for layer in _CORRECTION_LAYER_ORDER
                if layer in raw_layers
            ]
            if isinstance(raw_layers, list)
            else []
        )
        if sample_human_count:
            samples_with_human_evidence += 1
        human_evidence_count += sample_human_count
        automatic_evidence_count += sample_automatic_count
        for layer in sample_layers:
            affected_layer_counts[layer] = (
                affected_layer_counts.get(layer, 0) + 1
            )
        sample_evidence.append(
            {
                "item_id": int(raw["item_id"]),
                "asset_id": raw.get("asset_id"),
                "evaluation_id": context.get("evaluation_id")
                or raw.get("evaluation_id"),
                "review_stage": context.get("review_stage"),
                "review_revision": context.get("review_revision"),
                "final_review_id": context.get("final_review_id"),
                "affected_layers": sample_layers,
                "human_evidence_count": sample_human_count,
                "human_node_corrections": human_node_corrections,
                "human_reviews": human_reviews,
                "excluded_automatic_evidence_count": sample_automatic_count,
            }
        )
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

    affected_layers = [
        layer
        for layer in _CORRECTION_LAYER_ORDER
        if affected_layer_counts.get(layer, 0) > 0
    ]
    required_prompt_stage = (
        affected_layers[0]
        if affected_layers in (["A"], ["B"])
        else None
    )
    allowed_prompt_stages = (
        [required_prompt_stage]
        if required_prompt_stage is not None
        else ["A", "B"]
    )
    route_decision: dict[str, Any] = {
        "layers": affected_layers,
        "route_key": "+".join(affected_layers),
        "dependency_order": affected_layers,
        "reason_codes": [],
        "evidence_paths": [],
        "confidence": "high" if affected_layers else "low",
    }
    if affected_layers:
        try:
            from .automation_routing import route_correction_evidence

            route = route_correction_evidence(
                {
                    "node_corrections": [
                        node
                        for item in raw_items
                        for node in item.get("human_node_corrections", [])
                    ]
                }
            )
            route_decision = {
                "layers": list(route.layers),
                "route_key": route.route_key,
                "dependency_order": list(route.dependency_order),
                "reason_codes": list(route.reason_codes),
                "evidence_paths": list(route.evidence_paths),
                "confidence": route.confidence,
            }
        except ValueError:
            # Keep the existing report usable for older snapshots; the new
            # intake gate will reject the same malformed evidence before use.
            pass

    return {
        "schema_version": "baseline-correction-report-v2",
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
        "evidence_summary": {
            "selected_sample_count": len(raw_items),
            "samples_with_human_evidence": samples_with_human_evidence,
            "human_evidence_count": human_evidence_count,
            "automatic_evidence_count": automatic_evidence_count,
            "coverage_rate": round(
                samples_with_human_evidence / len(raw_items),
                4,
            ),
            "affected_layer_counts": affected_layer_counts,
            "affected_layers": affected_layers,
        },
        "sample_evidence": sample_evidence,
        "candidate_routing": {
            "policy": "human_evidence_only",
            "affected_layers": affected_layers,
            "allowed_prompt_stages": allowed_prompt_stages,
            "required_prompt_stage": required_prompt_stage,
        },
        "route_decision": route_decision,
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
