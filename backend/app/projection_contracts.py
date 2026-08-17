from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import (
    LocalProjectionRow,
    ProjectionContract,
    ProjectionManifest,
    ProjectionReconciliation,
    PublishedLabel,
)


BUILTIN_LOCAL_TARGETS = {
    "unified_dimension": "unified_dimension_table",
    "search_labels": "search_labels_small_table",
    "quality_governance": "quality_governance_small_table",
}

_ALLOWED_ROOTS = {
    "content_key",
    "category_key",
    "level",
    "score",
    "classification",
    "dimensions",
    "production_fields",
    "image_quality",
    "media_form",
    "semantic",
    "quality",
    "governance",
}
_ALLOWED_PROVENANCE = {
    "asset_version_id",
    "asset_id",
    "asset_sha256",
    "asset_scope",
    "is_single",
    "evaluation_id",
    "final_review_id",
    "strategy_bundle_id",
    "model_id",
    "prompt_a_version",
    "prompt_b_version",
    "rubric_version",
    "engine_version",
    "tag_contract_version",
    "normalization_version",
    "mapping_version",
}
_ALLOWED_LABEL_META = {
    "$label.id",
    "$label.release_id",
    "$label.version",
    "$label.schema_version",
    "$label.payload_hash",
    "$label.published_at",
}
_FORBIDDEN_TOKENS = {
    "raw_response",
    "candidate",
    "human_review",
    "review",
    "credential",
    "api_key",
    "password",
    "token",
    "secret",
    "manual_process",
}


class ProjectionContractError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_projection_contract(
    *,
    target_role: str,
    table_name: str,
    environment: str,
    primary_key: list[str],
    field_mappings: Mapping[str, str],
    write_policy: str = "local_only",
    target_key: str | None = None,
    category_key: str | None = None,
    field_contract_id: int | None = None,
    max_batch_size: int = 500,
    adapter_key: str = "local-sqlite",
) -> None:
    if environment == "shadow":
        if (
            write_policy != "shadow_only"
            or not target_key
            or not category_key
            or not field_contract_id
            or field_contract_id < 1
            or not adapter_key.strip()
        ):
            raise ProjectionContractError(
                "影子投影必须绑定目标、类目、字段合同和 shadow_only 策略"
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
            raise ProjectionContractError("影子表名必须是已校验标识符")
    else:
        expected_table = BUILTIN_LOCAL_TARGETS.get(target_role)
        if expected_table is None or table_name != expected_table:
            raise ProjectionContractError("仅支持已登记的本地大维表和小表目标")
        if environment not in {"local", "test"} or write_policy != "local_only":
            raise ProjectionContractError("本地投影只允许 local/test 与 local_only")
    if not 1 <= max_batch_size <= 500:
        raise ProjectionContractError("投影批次必须在 1 到 500 条之间")
    if primary_key != ["content_key"]:
        raise ProjectionContractError("本地投影主键必须为 content_key")
    if not field_mappings or field_mappings.get("content_key") != "content_key":
        raise ProjectionContractError("字段映射必须包含 content_key")
    for target_field, source_path in field_mappings.items():
        if not target_field.strip() or not source_path.strip():
            raise ProjectionContractError("字段映射不能为空")
        if source_path in _ALLOWED_LABEL_META:
            continue
        root, _, suffix = source_path.partition(".")
        if root == "provenance" and suffix in _ALLOWED_PROVENANCE:
            continue
        normalized = f"{target_field}.{source_path}".lower()
        if any(token in normalized for token in _FORBIDDEN_TOKENS):
            raise ProjectionContractError(
                f"禁止将候选、凭据、模型原始响应或人工过程字段投影：{source_path}"
            )
        if root in _ALLOWED_ROOTS:
            continue
        raise ProjectionContractError(
            f"禁止将候选、凭据、模型原始响应或人工过程字段投影：{source_path}"
        )


def contract_payload(contract: ProjectionContract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "contract_key": contract.contract_key,
        "version": contract.version,
        "target_role": contract.target_role,
        "table_name": contract.table_name,
        "environment": contract.environment,
        "adapter_key": contract.adapter_key,
        "target_key": contract.target_key,
        "write_policy": contract.write_policy,
        "category_key": contract.category_key,
        "field_contract_id": contract.field_contract_id,
        "max_batch_size": contract.max_batch_size,
        "primary_key": json.loads(contract.primary_key_json),
        "field_mappings": json.loads(contract.field_mappings_json),
        "input_versions": json.loads(contract.input_versions_json),
        "mode": contract.mode,
        "idempotency_key_template": contract.idempotency_key_template,
        "checkpoint": json.loads(contract.checkpoint_json),
        "reconciliation": json.loads(contract.reconciliation_json),
        "rollback": json.loads(contract.rollback_json),
        "owner": contract.owner,
        "status": contract.status,
        "contract_hash": contract.contract_hash,
        "created_by": contract.created_by,
        "created_at": contract.created_at,
    }


def create_contract_version(
    db: Session,
    *,
    contract_key: str,
    target_role: str,
    table_name: str,
    environment: str,
    primary_key: list[str],
    field_mappings: Mapping[str, str],
    input_versions: Mapping[str, Any],
    mode: str,
    idempotency_key_template: str,
    checkpoint: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    rollback: Mapping[str, Any],
    owner: str,
    status: str,
    created_by: str,
    adapter_key: str = "local-sqlite",
    target_key: str | None = None,
    write_policy: str = "local_only",
    category_key: str | None = None,
    field_contract_id: int | None = None,
    max_batch_size: int = 500,
) -> ProjectionContract:
    validate_projection_contract(
        target_role=target_role,
        table_name=table_name,
        environment=environment,
        primary_key=primary_key,
        field_mappings=field_mappings,
        write_policy=write_policy,
        target_key=target_key,
        category_key=category_key,
        field_contract_id=field_contract_id,
        max_batch_size=max_batch_size,
        adapter_key=adapter_key,
    )
    latest = db.scalar(
        select(ProjectionContract)
        .where(ProjectionContract.contract_key == contract_key)
        .order_by(ProjectionContract.version.desc())
        .limit(1)
    )
    version = (latest.version + 1) if latest else 1
    definition = {
        "contract_key": contract_key,
        "version": version,
        "target_role": target_role,
        "table_name": table_name,
        "environment": environment,
        "adapter_key": adapter_key,
        "target_key": target_key,
        "write_policy": write_policy,
        "category_key": category_key,
        "field_contract_id": field_contract_id,
        "max_batch_size": max_batch_size,
        "primary_key": primary_key,
        "field_mappings": dict(field_mappings),
        "input_versions": dict(input_versions),
        "mode": mode,
        "idempotency_key_template": idempotency_key_template,
        "checkpoint": dict(checkpoint),
        "reconciliation": dict(reconciliation),
        "rollback": dict(rollback),
        "owner": owner,
        "status": status,
    }
    contract = ProjectionContract(
        contract_key=contract_key,
        version=version,
        target_role=target_role,
        table_name=table_name,
        environment=environment,
        adapter_key=adapter_key,
        target_key=target_key,
        write_policy=write_policy,
        category_key=category_key,
        field_contract_id=field_contract_id,
        max_batch_size=max_batch_size,
        primary_key_json=canonical_json(primary_key),
        field_mappings_json=canonical_json(dict(field_mappings)),
        input_versions_json=canonical_json(dict(input_versions)),
        mode=mode,
        idempotency_key_template=idempotency_key_template,
        checkpoint_json=canonical_json(dict(checkpoint)),
        reconciliation_json=canonical_json(dict(reconciliation)),
        rollback_json=canonical_json(dict(rollback)),
        owner=owner,
        status=status,
        contract_hash=_hash(definition),
        created_by=created_by,
    )
    db.add(contract)
    db.flush()
    return contract


def _lookup(payload: Mapping[str, Any], path: str) -> Any:
    parts = path.split(".")
    if len(parts) == 4 and parts[0] == "semantic" and parts[2] in {"primary_name", "weighted_names"}:
        field = payload.get("semantic")
        field_payload = field.get(parts[1]) if isinstance(field, Mapping) else None
        values = field_payload.get("values") if isinstance(field_payload, Mapping) else None
        if not isinstance(values, list):
            return "" if parts[2] == "primary_name" else ""
        locale = parts[3]
        rendered: list[tuple[str, float | None]] = []
        for item in values:
            if not isinstance(item, Mapping):
                continue
            names = item.get("localized_names") or item.get("names") or {}
            name = names.get(locale) if isinstance(names, Mapping) else None
            name = str(name or item.get("value") or "").strip()
            if name:
                weight = item.get("weight")
                rendered.append((name, float(weight) if isinstance(weight, (int, float)) and not isinstance(weight, bool) else None))
        if parts[2] == "primary_name":
            return rendered[0][0] if rendered else ""
        return ",".join(
            f"{name}_{weight:g}" if weight is not None else name
            for name, weight in rendered
        )
    if path == "provenance.is_single":
        provenance = payload.get("provenance")
        if isinstance(provenance, Mapping) and provenance.get("is_single") is not None:
            return int(bool(provenance["is_single"]))
        return 1 if isinstance(provenance, Mapping) and provenance.get("asset_scope") == "single" else 0
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _label_meta(label: PublishedLabel, source_path: str) -> Any:
    return {
        "$label.id": label.id,
        "$label.release_id": label.release_id,
        "$label.version": label.version,
        "$label.schema_version": label.label_schema_version,
        "$label.payload_hash": label.payload_hash,
        "$label.published_at": label.published_at.astimezone(timezone.utc).isoformat()
        if label.published_at.tzinfo
        else label.published_at.replace(tzinfo=timezone.utc).isoformat(),
    }[source_path]


def _projection_row(
    label: PublishedLabel,
    *,
    field_mappings: Mapping[str, str],
) -> dict[str, Any]:
    payload = json.loads(label.label_payload_json or "{}")
    row = {
        target: (
            _label_meta(label, source)
            if source.startswith("$label.")
            else _lookup(payload, source)
        )
        for target, source in sorted(field_mappings.items())
    }
    row["_projection_version"] = {
        "published_label_id": label.id,
        "label_version": label.version,
    }
    return row


def _manifest_payload(
    contract: ProjectionContract,
    *,
    rows: list[dict[str, Any]],
    labels: list[PublishedLabel],
) -> dict[str, Any]:
    payload_hash = _hash(rows)
    provenance = [json.loads(label.label_payload_json or "{}").get("provenance") or {} for label in labels]
    input_versions = {
        "label_schema_versions": sorted({label.label_schema_version for label in labels}),
        "label_release_versions": sorted({label.version for label in labels}),
        "asset_versions": sorted(
            {str(item.get("asset_version_id") or item.get("asset_sha256") or item.get("asset_id")) for item in provenance if item.get("asset_version_id") or item.get("asset_sha256") or item.get("asset_id")}
        ),
        "mechanism_versions": sorted(
            {f"strategy-bundle:{item['strategy_bundle_id']}" for item in provenance if item.get("strategy_bundle_id") is not None}
        ),
        "model_versions": sorted(
            {str(item["model_id"]) for item in provenance if item.get("model_id")}
        ),
        "tag_contract_versions": sorted(
            {str(item["tag_contract_version"]) for item in provenance if item.get("tag_contract_version")}
        ),
        "mapping_versions": sorted(
            {str(item["mapping_version"]) for item in provenance if item.get("mapping_version")}
        ),
    }
    base = {
        "schema_version": "projection-manifest-v1",
        "contract_id": contract.id,
        "contract_key": contract.contract_key,
        "contract_version": contract.version,
        "contract_hash": contract.contract_hash,
        "target_role": contract.target_role,
        "target_table": contract.table_name,
        "environment": contract.environment,
        "mode": contract.mode,
        "row_count": len(rows),
        "content_keys": [str(row["content_key"]) for row in rows],
        "payload_hash": payload_hash,
        "input_versions": input_versions,
        "rows": rows,
    }
    return {**base, "manifest_hash": _hash(base)}


def build_projection_manifest(
    db: Session,
    *,
    contract: ProjectionContract,
) -> tuple[ProjectionManifest, dict[str, Any]]:
    mappings = json.loads(contract.field_mappings_json)
    labels = list(
        db.scalars(
            select(PublishedLabel)
            .where(PublishedLabel.status == "published")
            .order_by(PublishedLabel.content_key.asc(), PublishedLabel.id.asc())
        ).all()
    )
    rows = [_projection_row(label, field_mappings=mappings) for label in labels]
    payload = _manifest_payload(contract, rows=rows, labels=labels)
    existing = db.scalar(
        select(ProjectionManifest).where(
            ProjectionManifest.contract_id == contract.id,
            ProjectionManifest.manifest_hash == payload["manifest_hash"],
        )
    )
    if existing is not None:
        return existing, payload
    manifest = ProjectionManifest(
        contract_id=contract.id,
        manifest_hash=payload["manifest_hash"],
        payload_hash=payload["payload_hash"],
        row_count=payload["row_count"],
        content_keys_json=canonical_json(payload["content_keys"]),
        input_versions_json=canonical_json(payload["input_versions"]),
        rows_json=canonical_json(rows),
    )
    db.add(manifest)
    db.flush()
    return manifest, payload


def manifest_payload(manifest: ProjectionManifest, contract: ProjectionContract) -> dict[str, Any]:
    rows = json.loads(manifest.rows_json)
    return {
        "id": manifest.id,
        "schema_version": "projection-manifest-v1",
        "contract_id": contract.id,
        "contract_key": contract.contract_key,
        "contract_version": contract.version,
        "contract_hash": contract.contract_hash,
        "target_role": contract.target_role,
        "target_table": contract.table_name,
        "environment": contract.environment,
        "mode": contract.mode,
        "row_count": manifest.row_count,
        "content_keys": json.loads(manifest.content_keys_json),
        "payload_hash": manifest.payload_hash,
        "manifest_hash": manifest.manifest_hash,
        "input_versions": json.loads(manifest.input_versions_json),
        "rows": rows,
        "created_at": manifest.created_at,
    }


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    reason: str
    row_count: int
    missing_count: int
    unexpected_count: int
    payload_hash: str
    version_match: bool


class LocalProjectionAdapter:
    def apply(
        self,
        db: Session,
        *,
        contract: ProjectionContract,
        manifest: ProjectionManifest,
    ) -> None:
        rows = json.loads(manifest.rows_json)
        expected_keys = {str(row["content_key"]) for row in rows}
        existing = {
            row.content_key: row
            for row in db.scalars(
                select(LocalProjectionRow).where(
                    LocalProjectionRow.table_name == contract.table_name
                )
            ).all()
        }
        for row in rows:
            content_key = str(row["content_key"])
            version = row.get("_projection_version") or {}
            raw = canonical_json(row)
            stored = existing.get(content_key)
            if stored is None:
                stored = LocalProjectionRow(
                    table_name=contract.table_name,
                    content_key=content_key,
                    contract_id=contract.id,
                    contract_version=contract.version,
                    published_label_id=int(version["published_label_id"]),
                    label_version=int(version["label_version"]),
                    payload_json=raw,
                    payload_hash=_hash(row),
                )
                db.add(stored)
            else:
                stored.contract_id = contract.id
                stored.contract_version = contract.version
                stored.published_label_id = int(version["published_label_id"])
                stored.label_version = int(version["label_version"])
                stored.payload_json = raw
                stored.payload_hash = _hash(row)
                stored.updated_at = datetime.now(timezone.utc)
        if contract.mode == "snapshot":
            stale_keys = set(existing) - expected_keys
            if stale_keys:
                db.execute(
                    delete(LocalProjectionRow).where(
                        LocalProjectionRow.table_name == contract.table_name,
                        LocalProjectionRow.content_key.in_(stale_keys),
                    )
                )
        db.flush()

    def reconcile(
        self,
        db: Session,
        *,
        contract: ProjectionContract,
        manifest: ProjectionManifest,
    ) -> ReconciliationResult:
        expected_rows = json.loads(manifest.rows_json)
        actual_models = list(
            db.scalars(
                select(LocalProjectionRow)
                .where(LocalProjectionRow.table_name == contract.table_name)
                .order_by(LocalProjectionRow.content_key.asc())
            ).all()
        )
        actual_rows = [json.loads(row.payload_json) for row in actual_models]
        expected_keys = {str(row["content_key"]) for row in expected_rows}
        actual_keys = {row.content_key for row in actual_models}
        missing = expected_keys - actual_keys
        unexpected = actual_keys - expected_keys
        actual_hash = _hash(actual_rows)
        version_match = all(
            row.contract_id == contract.id and row.contract_version == contract.version
            for row in actual_models
        )
        reason = ""
        if missing:
            reason = "missing_rows"
        elif unexpected:
            reason = "unexpected_rows"
        elif actual_hash != manifest.payload_hash:
            reason = "payload_hash_mismatch"
        elif not version_match:
            reason = "version_mismatch"
        return ReconciliationResult(
            status="matched" if not reason else "drift",
            reason=reason,
            row_count=len(actual_rows),
            missing_count=len(missing),
            unexpected_count=len(unexpected),
            payload_hash=actual_hash,
            version_match=version_match,
        )


def persist_reconciliation(
    db: Session,
    *,
    contract: ProjectionContract,
    manifest: ProjectionManifest,
    result: ReconciliationResult,
) -> ProjectionReconciliation:
    record = ProjectionReconciliation(
        contract_id=contract.id,
        manifest_id=manifest.id,
        target_table=contract.table_name,
        status=result.status,
        reason=result.reason,
        row_count=result.row_count,
        missing_count=result.missing_count,
        unexpected_count=result.unexpected_count,
        expected_payload_hash=manifest.payload_hash,
        actual_payload_hash=result.payload_hash,
        version_match=result.version_match,
        checkpoint_json=contract.checkpoint_json,
        compensation_json=canonical_json(
            {
                "retryable": result.status != "matched",
                "strategy": "rebuild_from_published_labels",
                "canonical_rows_mutated": False,
            }
        ),
    )
    db.add(record)
    db.flush()
    return record


def reconciliation_payload(record: ProjectionReconciliation) -> dict[str, Any]:
    return {
        "id": record.id,
        "contract_id": record.contract_id,
        "manifest_id": record.manifest_id,
        "target_table": record.target_table,
        "status": record.status,
        "reason": record.reason,
        "row_count": record.row_count,
        "missing_count": record.missing_count,
        "unexpected_count": record.unexpected_count,
        "expected_payload_hash": record.expected_payload_hash,
        "payload_hash": record.actual_payload_hash,
        "version_match": record.version_match,
        "checkpoint": json.loads(record.checkpoint_json),
        "compensation": json.loads(record.compensation_json),
        "created_at": record.created_at,
    }
