from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .audit import canonical_json
from .label_export import spreadsheet_safe_text
from .models import SampleSet


QualityAssetExportFormat = Literal["csv", "json", "manifest"]

QUALITY_ASSET_COLUMNS = (
    "asset_id",
    "asset_name",
    "category_key",
    "expected_level",
    "expected_category",
    "truth_revision",
    "truth_updated_by",
    "truth_updated_at",
    "source_result_id",
    "source_model_id",
    "source_prompt_a_version",
    "source_prompt_b_version",
    "truth_json",
)

FIELD_DEFINITIONS = {
    "asset_id": "Label System 内部稳定素材标识",
    "asset_name": "素材原始文件名",
    "category_key": "业务类目键",
    "expected_level": "人工确认的目标等级",
    "expected_category": "人工确认的目标分类",
    "truth_revision": "该素材真值的追加式修订号",
    "truth_updated_by": "最近真值修订人",
    "truth_updated_at": "最近真值修订时间",
    "source_result_id": "形成首版或最近真值的评测结果标识",
    "source_model_id": "来源评测模型标识，不包含凭据",
    "source_prompt_a_version": "来源 A 阶段提示词版本",
    "source_prompt_b_version": "来源 B 阶段提示词版本",
    "truth": "正式人工真值；不包含候选机制、模型原始响应或凭据",
}


@dataclass(frozen=True)
class QualityAssetExport:
    content: bytes
    media_type: str
    extension: str
    row_count: int
    dataset_version: str
    manifest_hash: str


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _rows(sample_set: SampleSet) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(sample_set.items, key=lambda value: (value.asset_id, value.id)):
        truth = json.loads(item.truth_json or "{}")
        rows.append(
            {
                "asset_id": item.asset_id,
                "asset_name": item.asset.original_name,
                "category_key": sample_set.category_key,
                "expected_level": item.expected_level,
                "expected_category": item.expected_category,
                "truth_revision": item.truth_revision,
                "truth_updated_by": item.truth_updated_by,
                "truth_updated_at": _iso(item.truth_updated_at),
                "source_result_id": item.source_result_id,
                "source_model_id": item.source_result.model_id,
                "source_prompt_a_version": item.source_result.prompt_a_version,
                "source_prompt_b_version": item.source_result.prompt_b_version,
                "truth": truth,
            }
        )
    return rows


def quality_asset_manifest(sample_set: SampleSet) -> dict[str, Any]:
    rows = _rows(sample_set)
    revisions = [int(item["truth_revision"]) for item in rows]
    dataset_hash = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    manifest_base = {
        "schema_version": "quality-asset-manifest-v1",
        "sample_set_id": sample_set.id,
        "sample_set_name": sample_set.name,
        "sample_set_kind": sample_set.kind,
        "sample_set_status": sample_set.status,
        "category_key": sample_set.category_key,
        "item_count": len(rows),
        "truth_revision_min": min(revisions) if revisions else 0,
        "truth_revision_max": max(revisions) if revisions else 0,
        "dataset_hash": dataset_hash,
        "dataset_version": f"sample-set-{sample_set.id}:{dataset_hash[:16]}",
        "field_definitions": FIELD_DEFINITIONS,
        "excludes": [
            "model_credentials",
            "raw_model_responses",
            "candidate_mechanisms",
            "internal_tokens",
        ],
    }
    manifest_hash = hashlib.sha256(
        canonical_json(manifest_base).encode("utf-8")
    ).hexdigest()
    return {**manifest_base, "manifest_hash": manifest_hash}


def _csv_export(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=QUALITY_ASSET_COLUMNS)
    writer.writeheader()
    for row in rows:
        csv_row = {
            key: row.get(key, "")
            for key in QUALITY_ASSET_COLUMNS
            if key != "truth_json"
        }
        csv_row["truth_json"] = canonical_json(row["truth"])
        writer.writerow(
            {key: spreadsheet_safe_text(value) for key, value in csv_row.items()}
        )
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def build_quality_asset_export(
    sample_set: SampleSet,
    *,
    format: QualityAssetExportFormat,
) -> QualityAssetExport:
    rows = _rows(sample_set)
    manifest = quality_asset_manifest(sample_set)
    if format == "manifest":
        content = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        return QualityAssetExport(
            content=content,
            media_type="application/json; charset=utf-8",
            extension="manifest.json",
            row_count=len(rows),
            dataset_version=manifest["dataset_version"],
            manifest_hash=manifest["manifest_hash"],
        )
    if format == "json":
        payload = {
            "schema_version": "quality-asset-export-v1",
            "manifest": manifest,
            "items": rows,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return QualityAssetExport(
            content=content,
            media_type="application/json; charset=utf-8",
            extension="json",
            row_count=len(rows),
            dataset_version=manifest["dataset_version"],
            manifest_hash=manifest["manifest_hash"],
        )
    return QualityAssetExport(
        content=_csv_export(rows),
        media_type="text/csv; charset=utf-8",
        extension="csv",
        row_count=len(rows),
        dataset_version=manifest["dataset_version"],
        manifest_hash=manifest["manifest_hash"],
    )
