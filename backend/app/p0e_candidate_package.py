"""Deterministic, offline-only candidate package preview for P0-E."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from .p0e_image_freeze import safe_source_url
from .p0e_safe_import import ImportIssue, ImportPreflightError


CANDIDATE_PACKAGE_VERSION = "p0e-candidate-preview-v1"
_CONFLICT_STATES = {"conflict", "conflicted", "disputed"}


def _reject(code: str, message: str) -> None:
    raise ImportPreflightError(ImportIssue(code=code, message=message))


def _safe_row_id(row: Mapping[str, Any], fallback: int) -> str:
    business_id = str(row.get("source_business_id") or "").strip()
    if business_id:
        return business_id[:160]
    source_file = str(row.get("source_file") or "[unknown]")
    source_file = source_file.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        source_row = int(row.get("source_row") or fallback)
    except (TypeError, ValueError):
        source_row = fallback
    return f"{source_file[:120]}:{source_row}"


def _canonical_url_key(value: str) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme.casefold() != "https" or not parts.hostname:
        return None
    try:
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
        port = parts.port
    except (UnicodeError, ValueError):
        return None
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port and port != 443 else host
    # Query parameters can contain credentials or tracking tokens.  They are
    # intentionally ignored both for duplicate detection and returned output.
    return urlunsplit(("https", netloc, parts.path or "/", "", ""))


def _truth_pair(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("historical_grade") or "").strip(),
        str(row.get("historical_category") or "").strip(),
    )


def _row_sort_key(row: Mapping[str, Any], *, seed: str) -> tuple[str, str]:
    row_id = str(row["_row_id"])
    digest = hashlib.sha256(f"{seed}\0{row_id}".encode("utf-8")).hexdigest()
    return digest, row_id


def _public_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    allowed_fields = (
        "domain",
        "source_file",
        "source_row",
        "source_business_id",
        "source_url",
        "historical_grade",
        "historical_category",
        "truth_status",
        "sample_role",
        "risk",
    )
    result = {field: row.get(field) for field in allowed_fields}
    result["source_url"] = (
        safe_source_url(str(row.get("source_url") or ""))
        if row.get("source_url")
        else None
    )
    result["preview_id"] = row["_row_id"]
    result["stratum"] = {
        "category": str(row.get("historical_category") or "unknown"),
        "grade": str(row.get("historical_grade") or "unknown"),
        "risk": str(row.get("risk") or "unknown").casefold(),
    }
    return result


def build_candidate_package_preview(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_size: int = 40,
    seed: str = "p0e-3d-v1",
) -> dict[str, Any]:
    """Build a reproducible 30–50 row preview; never claim or mutate Gold."""

    if target_size < 30 or target_size > 50:
        _reject(
            "CANDIDATE_TARGET_SIZE_INVALID",
            "候选包目标数量必须在 30 到 50 之间。",
        )
    if not seed:
        _reject("CANDIDATE_SEED_REQUIRED", "候选包必须提供固定 seed。")

    normalized_rows: list[dict[str, Any]] = []
    for fallback, source in enumerate(rows, start=1):
        row = dict(source)
        row["_row_id"] = _safe_row_id(row, fallback)
        row["_url_key"] = _canonical_url_key(
            str(row.get("source_url") or "")
        )
        normalized_rows.append(row)

    url_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    business_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized_rows:
        if row["_url_key"]:
            url_groups[str(row["_url_key"])].append(row)
        business_id = str(row.get("source_business_id") or "").strip()
        if business_id:
            business_groups[business_id].append(row)
    duplicate_url_ids = {
        str(row["_row_id"])
        for group in url_groups.values()
        if len(group) > 1
        for row in group
    }
    conflict_ids: set[str] = {
        str(row["_row_id"])
        for row in normalized_rows
        if bool(row.get("conflict"))
        or str(row.get("truth_status") or "").casefold() in _CONFLICT_STATES
    }
    for groups in (url_groups, business_groups):
        for group in groups.values():
            truths = {_truth_pair(row) for row in group}
            if len(truths) > 1:
                conflict_ids.update(str(row["_row_id"]) for row in group)

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = defaultdict(int)
    for row in normalized_rows:
        reasons: list[str] = []
        domain = str(row.get("domain") or "").strip().casefold()
        if domain != "3d":
            reasons.append("not_3d_domain")
        grade, category = _truth_pair(row)
        source_name = str(row.get("source_file") or "").casefold()
        is_3dreason = "3dreason" in source_name or str(
            row.get("source_dataset") or ""
        ).casefold() == "3dreason"
        if not grade or not category:
            reasons.append(
                "3dreason_missing_human_grade_or_category"
                if is_3dreason
                else "missing_human_grade_or_category"
            )
        row_id = str(row["_row_id"])
        if row_id in duplicate_url_ids:
            reasons.append("duplicate_url")
        if row_id in conflict_ids:
            reasons.append("conflicting_truth")
        if reasons:
            unique_reasons = sorted(set(reasons))
            for reason in unique_reasons:
                exclusion_counts[reason] += 1
            excluded.append(
                {
                    "preview_id": row_id,
                    "source_file": str(row.get("source_file") or "")
                    .replace("\\", "/")
                    .rsplit("/", 1)[-1],
                    "source_row": row.get("source_row"),
                    "source_url": (
                        safe_source_url(str(row.get("source_url") or ""))
                        if row.get("source_url")
                        else None
                    ),
                    "reasons": unique_reasons,
                }
            )
        else:
            eligible.append(row)

    strata: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        stratum = (
            str(row.get("historical_category") or "unknown"),
            str(row.get("historical_grade") or "unknown"),
            str(row.get("risk") or "unknown").casefold(),
        )
        strata[stratum].append(row)
    for stratum, bucket in strata.items():
        bucket.sort(key=lambda row: _row_sort_key(row, seed=f"{seed}\0{stratum}"))

    ordered_strata = sorted(
        strata,
        key=lambda stratum: (
            hashlib.sha256(
                f"{seed}\0{stratum}".encode("utf-8")
            ).hexdigest(),
            stratum,
        ),
    )
    selected: list[dict[str, Any]] = []
    index = 0
    while len(selected) < target_size:
        added = False
        for stratum in ordered_strata:
            bucket = strata[stratum]
            if index < len(bucket):
                selected.append(bucket[index])
                added = True
                if len(selected) == target_size:
                    break
        if not added:
            break
        index += 1

    selected_public = [_public_candidate(row) for row in selected]
    selected_public.sort(
        key=lambda row: (
            str(row["stratum"]["category"]),
            str(row["stratum"]["grade"]),
            str(row["stratum"]["risk"]),
            str(row["preview_id"]),
        )
    )
    excluded.sort(key=lambda row: str(row["preview_id"]))
    complete = len(selected_public) == target_size
    return {
        "schema_version": CANDIDATE_PACKAGE_VERSION,
        "mode": "offline_deterministic_preview",
        "domain": "3D",
        "seed": seed,
        "target_size": target_size,
        "selected_count": len(selected_public),
        "eligible_count": len(eligible),
        "excluded_count": len(excluded),
        "complete_for_requested_preview": complete,
        "status": "preview_ready" if complete else "preview_incomplete",
        "forms_gold": False,
        "downloads_performed": False,
        "model_runs_performed": False,
        "selected": selected_public,
        "excluded": excluded,
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
    }
