"""Build deterministic, read-only SQL for 3D/SU source identity verification."""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict


_TABLE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"
)


class SourceIdentityProbeError(ValueError):
    """Raised when a probe cannot be generated safely."""


class SourceIdentityProbeBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    table_name: str
    queries: dict[str, str]
    probe_hash: str


def build_three_d_su_identity_probe(table_name: str) -> SourceIdentityProbeBundle:
    """Return four SELECT statements and a stable hash without executing them."""

    if not _TABLE_PATTERN.fullmatch(table_name):
        raise SourceIdentityProbeError("表名必须是 project.table 格式的安全标识符")

    queries = {
        "scope": (
            f"SELECT res_type, COUNT(*) AS row_count "
            f"FROM {table_name} "
            "WHERE res_type IN (1, 6) "
            "GROUP BY res_type ORDER BY res_type"
        ),
        "nulls": (
            "SELECT res_type, "
            "SUM(CASE WHEN ll_id IS NULL OR TRIM(CAST(ll_id AS STRING)) = '' "
            "THEN 1 ELSE 0 END) AS ll_id_blank_count, "
            "SUM(CASE WHEN res_id IS NULL OR TRIM(CAST(res_id AS STRING)) = '' "
            "THEN 1 ELSE 0 END) AS res_id_blank_count "
            f"FROM {table_name} WHERE res_type IN (1, 6) "
            "GROUP BY res_type ORDER BY res_type"
        ),
        "duplicates": (
            "SELECT res_type, ll_id, COUNT(*) AS row_count "
            f"FROM {table_name} "
            "WHERE res_type IN (1, 6) AND ll_id IS NOT NULL "
            "GROUP BY res_type, ll_id HAVING COUNT(*) > 1 "
            "ORDER BY row_count DESC, res_type, ll_id"
        ),
        "res_id_conflicts": (
            "SELECT res_type, ll_id, COUNT(DISTINCT res_id) AS res_id_count "
            f"FROM {table_name} "
            "WHERE res_type IN (1, 6) AND ll_id IS NOT NULL "
            "GROUP BY res_type, ll_id HAVING COUNT(DISTINCT res_id) > 1 "
            "ORDER BY res_id_count DESC, res_type, ll_id"
        ),
    }
    canonical = json.dumps(
        {"table_name": table_name, "queries": queries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return SourceIdentityProbeBundle(
        table_name=table_name,
        queries=queries,
        probe_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
