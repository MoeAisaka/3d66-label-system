"""Field-level semantic tag quality metrics.

The functions here are pure calculations over frozen truth/prediction and
review evidence. They never decide publication or mutate persistence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import (
    BaselineRegressionRun,
    SampleSet,
    SampleSetItem,
    SemanticQualityMetricSnapshot,
    TagDemandContract,
)
import hashlib


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _semantic_entity_values(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        value = value.get("values")
    if not isinstance(value, list):
        return set()
    result: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            entity_id = item.get("entity_id") or item.get("value")
            if entity_id:
                result.add(str(entity_id))
        elif isinstance(item, str) and item.strip():
            result.add(item.strip())
    return result


def freeze_semantic_truth_snapshot(
    db: Session,
    *,
    category_key: str,
    asset_ids: list[int],
) -> dict[str, Any]:
    """Freeze the exact locked Gold truth revisions used by a new run."""
    rows = (
        db.scalars(
            select(SampleSetItem)
            .join(SampleSet, SampleSet.id == SampleSetItem.sample_set_id)
            .where(
                SampleSet.category_key == category_key,
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
    latest: dict[int, SampleSetItem] = {}
    for row in rows:
        latest.setdefault(row.asset_id, row)
    return {
        "schema_version": "semantic-truth-snapshot-v1",
        "assets": {
            str(asset_id): {
                "sample_set_id": row.sample_set_id,
                "sample_item_id": row.id,
                "truth_revision": row.truth_revision,
                "truth": _json_object(row.truth_json),
            }
            for asset_id, row in sorted(latest.items())
        },
    }


def _add_stats(
    target: dict[str, dict[str, int]],
    source: Mapping[str, Any],
    *,
    defaults: Mapping[str, int],
) -> None:
    for field_key, raw in source.items():
        if not isinstance(field_key, str) or not isinstance(raw, Mapping):
            continue
        row = target.setdefault(field_key, dict(defaults))
        for key in defaults:
            value = raw.get(key, 0)
            if isinstance(value, int) and not isinstance(value, bool):
                row[key] += value


def build_run_semantic_quality(
    run: BaselineRegressionRun | Any,
) -> tuple[SemanticQualityReport, dict[str, Any], dict[str, Any]]:
    """Build evidence only from snapshots frozen into one regression run."""
    execution = _json_object(run.execution_snapshot_json)
    truth_snapshot = execution.get("semantic_truth_snapshot")
    truth_snapshot = truth_snapshot if isinstance(truth_snapshot, Mapping) else {}
    frozen_assets = truth_snapshot.get("assets")
    frozen_assets = frozen_assets if isinstance(frozen_assets, Mapping) else {}
    truth_by_asset: dict[str, dict[str, set[str]]] = {}
    predicted_by_asset: dict[str, dict[str, set[str]]] = {}
    mapping_stats: dict[str, dict[str, int]] = {}
    review_stats: dict[str, dict[str, int]] = {}
    reconciliation_stats = {"expected": 0, "matched": 0}
    truth_revisions: list[int] = []
    reviewed_evidence_count = 0
    reconciled_evidence_count = 0
    for item in run.items:
        asset_key = str(item.asset_id)
        frozen = frozen_assets.get(asset_key)
        frozen = frozen if isinstance(frozen, Mapping) else {}
        truth_payload = frozen.get("truth")
        truth_payload = truth_payload if isinstance(truth_payload, Mapping) else {}
        semantic_truth = truth_payload.get("semantic")
        semantic_truth = semantic_truth if isinstance(semantic_truth, Mapping) else {}
        revision = frozen.get("truth_revision")
        if isinstance(revision, int):
            truth_revisions.append(revision)
        truth_by_asset[asset_key] = {
            str(field): _semantic_entity_values(value)
            for field, value in semantic_truth.items()
            if isinstance(field, str)
        }

        snapshot = _json_object(item.result_snapshot_json)
        stage_a = snapshot.get("stage_a")
        stage_a = stage_a if isinstance(stage_a, Mapping) else {}
        semantic_pred = stage_a.get("semantic") or stage_a.get("semantic_candidates") or {}
        semantic_pred = semantic_pred if isinstance(semantic_pred, Mapping) else {}
        predicted_by_asset[asset_key] = {
            str(field): _semantic_entity_values(value)
            for field, value in semantic_pred.items()
            if isinstance(field, str)
        }
        raw_mapping = stage_a.get("semantic_mapping_stats")
        raw_mapping = raw_mapping if isinstance(raw_mapping, Mapping) else {}
        for field_key, value in semantic_pred.items():
            if not isinstance(field_key, str):
                continue
            candidates = value.get("values") if isinstance(value, Mapping) else value
            candidate_count = len(candidates) if isinstance(candidates, list) else 0
            raw = raw_mapping.get(field_key)
            raw = raw if isinstance(raw, Mapping) else {}
            row = mapping_stats.setdefault(
                field_key,
                {"candidate": 0, "mapped": 0, "unmapped": 0, "conflicted": 0, "evaluated": 0},
            )
            row["candidate"] += int(raw.get("candidate", candidate_count))
            row["mapped"] += int(raw.get("mapped", candidate_count))
            row["unmapped"] += int(raw.get("unmapped", 0))
            row["conflicted"] += int(raw.get("conflicted", 0))
            row["evaluated"] += 1

        raw_review = stage_a.get("semantic_review_stats")
        if isinstance(raw_review, Mapping):
            _add_stats(
                review_stats,
                raw_review,
                defaults={
                    "corrected": 0,
                    "reviewed": 0,
                    "required": 0,
                    "null_truth": 0,
                    "null_correct": 0,
                    "bilingual": 0,
                    "bilingual_consistent": 0,
                },
            )
            reviewed_evidence_count += 1
        raw_reconciliation = stage_a.get("semantic_reconciliation_stats")
        if isinstance(raw_reconciliation, Mapping):
            for key in ("expected", "matched"):
                value = raw_reconciliation.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool):
                    reconciliation_stats[key] += value
            reconciled_evidence_count += 1

    report = compute_semantic_quality_metrics(
        truth_by_asset=truth_by_asset,
        predicted_by_asset=predicted_by_asset,
        mapping_stats=mapping_stats,
        review_stats=review_stats,
        reconciliation_stats=reconciliation_stats,
    )
    evidence = {
        "status": "ready" if frozen_assets else "unavailable_historical",
        "truth_source": "frozen_run_snapshot" if frozen_assets else "unavailable",
        "truth_asset_count": len(frozen_assets),
        "truth_revision_min": min(truth_revisions) if truth_revisions else None,
        "truth_revision_max": max(truth_revisions) if truth_revisions else None,
        "review_evidence_item_count": reviewed_evidence_count,
        "reconciliation_evidence_item_count": reconciled_evidence_count,
    }
    context = execution.get("semantic_quality_context")
    context = dict(context) if isinstance(context, Mapping) else {}
    return report, evidence, context


def persist_run_semantic_quality_snapshot(
    db: Session,
    *,
    run: BaselineRegressionRun,
) -> list[SemanticQualityMetricSnapshot]:
    if run.status not in {"completed", "partial_failed", "failed"}:
        return []
    report, evidence, context = build_run_semantic_quality(run)
    if evidence["status"] != "ready" or not report.fields:
        return []
    contract_id = context.get("contract_id")
    contract = db.get(TagDemandContract, contract_id) if isinstance(contract_id, int) else None
    if (
        contract is None
        or contract.contract_key != context.get("contract_key")
        or contract.version != context.get("contract_version")
        or contract.contract_hash != context.get("contract_hash")
    ):
        raise ValueError("回归冻结的语义质量合同上下文无效")
    site_scope = context.get("site_scope")
    asset_scope = context.get("asset_scope")
    if site_scope not in {"domestic", "overseas"}:
        raise ValueError("回归冻结的语义质量 site_scope 无效")
    if asset_scope not in {"whole", "single", "other", "unknown"}:
        raise ValueError("回归冻结的语义质量 asset_scope 无效")
    return persist_semantic_quality_snapshot(
        db,
        baseline_run_id=run.id,
        contract_id=contract.id,
        category_key=run.category_key,
        site_scope=site_scope,
        asset_scope=asset_scope,
        report=report,
    )


@dataclass(frozen=True)
class SemanticFieldQualityMetrics:
    field_key: str
    truth_count: int
    predicted_count: int
    true_positive_count: int
    precision: float | None
    recall: float | None
    mapping_coverage: float | None
    unmapped_rate: float | None
    conflict_rate: float | None
    null_semantics_accuracy: float | None
    correction_rate: float | None
    review_coverage: float | None
    bilingual_consistency: float | None
    reconciliation_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticQualityReport:
    fields: Mapping[str, SemanticFieldQualityMetrics]
    macro_precision: float | None
    macro_recall: float | None
    micro_precision: float | None
    micro_recall: float | None
    reconciliation_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "semantic-quality-metrics-v1",
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "aggregates": {
                "macro_precision": self.macro_precision,
                "macro_recall": self.macro_recall,
                "micro_precision": self.micro_precision,
                "micro_recall": self.micro_recall,
            },
            "reconciliation_rate": self.reconciliation_rate,
        }


def _field_keys(
    truth_by_asset: Mapping[str, Mapping[str, set[str]]],
    predicted_by_asset: Mapping[str, Mapping[str, set[str]]],
    mapping_stats: Mapping[str, Mapping[str, int]],
    review_stats: Mapping[str, Mapping[str, int]],
) -> list[str]:
    return sorted(
        set(mapping_stats)
        | set(review_stats)
        | {field for row in truth_by_asset.values() for field in row}
        | {field for row in predicted_by_asset.values() for field in row}
    )


def compute_semantic_quality_metrics(
    *,
    truth_by_asset: Mapping[str, Mapping[str, set[str]]],
    predicted_by_asset: Mapping[str, Mapping[str, set[str]]],
    mapping_stats: Mapping[str, Mapping[str, int]],
    review_stats: Mapping[str, Mapping[str, int]],
    reconciliation_stats: Mapping[str, int],
) -> SemanticQualityReport:
    fields: dict[str, SemanticFieldQualityMetrics] = {}
    for field_key in _field_keys(truth_by_asset, predicted_by_asset, mapping_stats, review_stats):
        truth_count = 0
        predicted_count = 0
        true_positive_count = 0
        for asset_key in set(truth_by_asset) | set(predicted_by_asset):
            truth = set((truth_by_asset.get(asset_key) or {}).get(field_key) or set())
            predicted = set((predicted_by_asset.get(asset_key) or {}).get(field_key) or set())
            truth_count += len(truth)
            predicted_count += len(predicted)
            true_positive_count += len(truth & predicted)
        mapping = mapping_stats.get(field_key) or {}
        review = review_stats.get(field_key) or {}
        evaluated = int(mapping.get("evaluated", 0))
        bilingual = int(review.get("bilingual", 0))
        fields[field_key] = SemanticFieldQualityMetrics(
            field_key=field_key,
            truth_count=truth_count,
            predicted_count=predicted_count,
            true_positive_count=true_positive_count,
            precision=_ratio(true_positive_count, predicted_count),
            recall=_ratio(true_positive_count, truth_count),
            mapping_coverage=_ratio(int(mapping.get("mapped", 0)), int(mapping.get("candidate", 0))),
            unmapped_rate=_ratio(int(mapping.get("unmapped", 0)), int(mapping.get("candidate", 0))),
            conflict_rate=_ratio(int(mapping.get("conflicted", 0)), evaluated),
            null_semantics_accuracy=_ratio(int(review.get("null_correct", 0)), int(review.get("null_truth", 0))),
            correction_rate=_ratio(int(review.get("corrected", 0)), int(review.get("reviewed", 0))),
            review_coverage=_ratio(int(review.get("reviewed", 0)), int(review.get("required", 0))),
            bilingual_consistency=_ratio(int(review.get("bilingual_consistent", 0)), bilingual),
            reconciliation_rate=_ratio(
                int(reconciliation_stats.get("matched", 0)),
                int(reconciliation_stats.get("expected", 0)),
            ),
        )
    precision_values = [item.precision for item in fields.values() if item.precision is not None]
    recall_values = [item.recall for item in fields.values() if item.recall is not None]
    total_truth = sum(item.truth_count for item in fields.values())
    total_predicted = sum(item.predicted_count for item in fields.values())
    total_tp = sum(item.true_positive_count for item in fields.values())
    return SemanticQualityReport(
        fields=fields,
        macro_precision=(sum(precision_values) / len(precision_values) if precision_values else None),
        macro_recall=(sum(recall_values) / len(recall_values) if recall_values else None),
        micro_precision=_ratio(total_tp, total_predicted),
        micro_recall=_ratio(total_tp, total_truth),
        reconciliation_rate=_ratio(
            int(reconciliation_stats.get("matched", 0)),
            int(reconciliation_stats.get("expected", 0)),
        ),
    )


def persist_semantic_quality_snapshot(
    db: Session,
    *,
    baseline_run_id: int,
    contract_id: int,
    category_key: str,
    site_scope: str,
    asset_scope: str,
    report: SemanticQualityReport,
) -> list[SemanticQualityMetricSnapshot]:
    """Persist one immutable field set plus an aggregate row for a run scope."""
    rows: list[SemanticQualityMetricSnapshot] = []
    metrics = list(report.fields.values())
    aggregate = SemanticFieldQualityMetrics(
        field_key="_aggregate",
        truth_count=sum(item.truth_count for item in metrics),
        predicted_count=sum(item.predicted_count for item in metrics),
        true_positive_count=sum(item.true_positive_count for item in metrics),
        precision=report.micro_precision,
        recall=report.micro_recall,
        mapping_coverage=_mean(item.mapping_coverage for item in metrics),
        unmapped_rate=_mean(item.unmapped_rate for item in metrics),
        conflict_rate=_mean(item.conflict_rate for item in metrics),
        null_semantics_accuracy=_mean(item.null_semantics_accuracy for item in metrics),
        correction_rate=_mean(item.correction_rate for item in metrics),
        review_coverage=_mean(item.review_coverage for item in metrics),
        bilingual_consistency=_mean(item.bilingual_consistency for item in metrics),
        reconciliation_rate=report.reconciliation_rate,
    )
    for metric in [*metrics, aggregate]:
        existing = db.scalar(
            select(SemanticQualityMetricSnapshot).where(
                SemanticQualityMetricSnapshot.baseline_run_id == baseline_run_id,
                SemanticQualityMetricSnapshot.contract_id == contract_id,
                SemanticQualityMetricSnapshot.category_key == category_key,
                SemanticQualityMetricSnapshot.site_scope == site_scope,
                SemanticQualityMetricSnapshot.asset_scope == asset_scope,
                SemanticQualityMetricSnapshot.field_key == metric.field_key,
            )
        )
        payload = metric.to_dict() | {
            "baseline_run_id": baseline_run_id,
            "contract_id": contract_id,
            "category_key": category_key,
            "site_scope": site_scope,
            "asset_scope": asset_scope,
        }
        payload_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        if existing is not None:
            if existing.metrics_hash != payload_hash:
                raise ValueError("同一基准回归语义指标快照不可覆盖")
            rows.append(existing)
            continue
        row = SemanticQualityMetricSnapshot(
            baseline_run_id=baseline_run_id,
            contract_id=contract_id,
            category_key=category_key,
            site_scope=site_scope,
            asset_scope=asset_scope,
            field_key=metric.field_key,
            truth_count=metric.truth_count,
            predicted_count=metric.predicted_count,
            true_positive_count=metric.true_positive_count,
            precision=metric.precision,
            recall=metric.recall,
            mapping_coverage=metric.mapping_coverage,
            unmapped_rate=metric.unmapped_rate,
            conflict_rate=metric.conflict_rate,
            null_semantics_accuracy=metric.null_semantics_accuracy,
            correction_rate=metric.correction_rate,
            review_coverage=metric.review_coverage,
            bilingual_consistency=metric.bilingual_consistency,
            reconciliation_rate=metric.reconciliation_rate,
            metrics_hash=payload_hash,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _mean(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else None
