"""Deterministic preview for Owner-confirmed historical correction workbooks.

The preview is deliberately non-persistent.  It reuses the hardened P0-E XLSX
reader, preserves source provenance, and never promotes a row to Gold.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .p0e_safe_import import ImportTarget, XlsxLimits, preflight_xlsx


HISTORICAL_PREVIEW_SCHEMA_VERSION = "historical-corrections-preview-v1"
_HUMAN_LEVEL_FIELDS = ("评测等级", "人工等级", "human_grade", "human_level")
_MODEL_LEVEL_FIELDS = ("豆包等级", "grade", "model_grade", "模型等级")
_REASON_FIELDS = ("hwreason", "人工原因", "纠偏原因")
_FALLBACK_REASON_FIELDS = ("reason", "cons", "image_defects")
_BUSINESS_KEY_FIELDS = (
    "llid",
    "ll_id",
    "img_id",
    "fa_img_id",
    "source_business_id",
    "img_url",
    "image_url",
    "url",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalized_key(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _clean(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _field(
    values: dict[str, Any],
    columns: list[dict[str, Any]],
    candidates: Iterable[str],
) -> object | None:
    candidate_set = {_normalized_key(candidate) for candidate in candidates}
    for column in columns:
        if _normalized_key(column["raw_header"]) not in candidate_set:
            continue
        value = _clean(values.get(str(column["internal_name"])))
        if value is not None:
            return value
    return None


def _row_hash(
    *,
    file_hash: str,
    sheet: str,
    source_row: int,
    values: dict[str, Any],
) -> str:
    material = {
        "file_hash": file_hash,
        "sheet": sheet,
        "source_row": source_row,
        "values": values,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _holdout(row_hash: str, ratio: float) -> bool:
    threshold = int(ratio * 10_000)
    bucket = int(row_hash[:8], 16) % 10_000
    return bucket < threshold


def _business_key(
    values: dict[str, Any],
    columns: list[dict[str, Any]],
    row_hash: str,
    source_file: str,
) -> str:
    value = _field(values, columns, _BUSINESS_KEY_FIELDS)
    if value is None:
        return f"row:{row_hash}"
    namespace = "su" if "su" in _normalized_key(source_file) else "3d"
    return f"business:{namespace}:{_normalized_key(value)}"


def map_historical_row(
    *,
    source_file: str,
    content_sha256: str,
    sheet: str,
    source_row: int,
    columns: list[dict[str, Any]],
    values: dict[str, Any],
    blind_holdout_ratio: float = 0.2,
) -> dict[str, Any]:
    """Map one RAW row into a correction candidate without inventing truth."""

    if not 0 <= blind_holdout_ratio < 1:
        raise ValueError("blind_holdout_ratio 必须在 [0, 1) 范围")
    row_hash = _row_hash(
        file_hash=content_sha256,
        sheet=sheet,
        source_row=source_row,
        values=values,
    )
    source_reason_only = "3dreason" in _normalized_key(source_file)
    human_level = None if source_reason_only else _field(
        values, columns, _HUMAN_LEVEL_FIELDS
    )
    model_level = _field(values, columns, _MODEL_LEVEL_FIELDS)
    human_reason = _field(values, columns, _REASON_FIELDS)
    reason = human_reason or _field(values, columns, _FALLBACK_REASON_FIELDS)
    reason_only = source_reason_only or human_level is None
    if reason_only:
        role = "reason_only"
    elif _holdout(row_hash, blind_holdout_ratio):
        role = "blind_holdout"
    elif human_reason is not None or (
        human_level is not None
        and model_level is not None
        and _normalized_key(human_level) != _normalized_key(model_level)
    ):
        role = "target_error"
    else:
        role = "stable_control"
    return {
        "schema_version": HISTORICAL_PREVIEW_SCHEMA_VERSION,
        "dedupe_key": _business_key(
            values, columns, row_hash, source_file
        ),
        "sample_role": role,
        "correction_candidate": {
            "scope": "overall",
            "human_level": human_level,
            "model_level": model_level,
            "reason": reason,
            "reason_only": reason_only,
        },
        "provenance": {
            "source_file": source_file,
            "sheet": sheet,
            "source_row": source_row,
            "source_file_sha256": content_sha256,
            "source_row_sha256": row_hash,
            "owner_confirmed": True,
        },
        "forms_gold": False,
    }


def preview_historical_workbooks(
    files: list[tuple[str, bytes]],
    *,
    blind_holdout_ratio: float = 0.2,
) -> dict[str, Any]:
    """Safely parse uploaded bytes and return a bounded, deterministic plan."""

    if not files:
        raise ValueError("至少需要一个 XLSX 文件")
    file_summaries: list[dict[str, Any]] = []
    mapped: list[dict[str, Any]] = []
    duplicate_count = 0
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="3d66-historical-preview-") as temp:
        root = Path(temp)
        for index, (filename, content) in enumerate(files):
            safe_name = f"{index:04d}.xlsx"
            path = root / safe_name
            path.write_bytes(content)
            preflight = preflight_xlsx(
                path,
                target=ImportTarget(domain="3D", target_kind="candidate"),
                limits=XlsxLimits(preview_rows=100_000),
            )
            # The parser sees the safe temporary basename.  Restore the
            # authenticated upload name only as display/provenance metadata.
            sheet = str(preflight["sheet"]["name"])
            file_count = 0
            for row in preflight["raw_preview"]:
                item = map_historical_row(
                    source_file=filename,
                    content_sha256=str(preflight["content_sha256"]),
                    sheet=sheet,
                    source_row=int(row["source_row"]),
                    columns=preflight["columns"],
                    values=row["values_by_internal_name"],
                    blind_holdout_ratio=blind_holdout_ratio,
                )
                if item["dedupe_key"] in seen:
                    duplicate_count += 1
                    continue
                seen.add(item["dedupe_key"])
                mapped.append(item)
                file_count += 1
            file_summaries.append(
                {
                    "source_file": filename,
                    "content_sha256": preflight["content_sha256"],
                    "sheet": preflight["sheet"],
                    "batch_key": preflight["batch_key"],
                    "preview_item_count": file_count,
                }
            )
    mapped.sort(
        key=lambda item: (
            item["dedupe_key"],
            item["provenance"]["source_file"],
            item["provenance"]["source_row"],
        )
    )
    role_counts = {
        role: sum(1 for item in mapped if item["sample_role"] == role)
        for role in (
            "target_error",
            "stable_control",
            "blind_holdout",
            "reason_only",
        )
    }
    return {
        "schema_version": HISTORICAL_PREVIEW_SCHEMA_VERSION,
        "mode": "preview_only",
        "files": file_summaries,
        "summary": {
            "uploaded_file_count": len(files),
            "unique_item_count": len(mapped),
            "duplicate_count": duplicate_count,
            "blind_holdout_ratio": blind_holdout_ratio,
            "role_counts": role_counts,
        },
        "items": mapped,
        "writes_business_database": False,
        "downloads_performed": False,
        "model_runs_performed": False,
        "forms_gold": False,
    }
