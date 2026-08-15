"""Field-level semantic tag quality metrics.

The functions here are pure calculations over frozen truth/prediction and
review evidence. They never decide publication or mutate persistence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import SemanticQualityMetricSnapshot
import hashlib


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


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
