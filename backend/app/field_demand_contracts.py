from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import Asset, AssetVersion, FieldDemandContract


_CONTRACT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_ALLOWED_CANONICAL_ROOTS = {"semantic", "quality", "governance"}
_FORBIDDEN_PATH_PARTS = {
    "candidate",
    "raw",
    "raw_response",
    "manual",
    "manual_process",
    "credential",
    "credentials",
    "secret",
    "token",
    "password",
    "api_key",
}
_STATUSES = {"draft", "active", "retired"}


class FieldDemandContractError(ValueError):
    pass


def _required_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise FieldDemandContractError(f"{label}不能为空")
    return normalized


def _normalize_occurred_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_fields(fields: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not fields:
        raise FieldDemandContractError("字段需求合同至少包含一个 Canonical 字段")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in fields:
        item = dict(field)
        field_key = _required_text(str(item.get("field_key") or ""), label="field_key")
        if not _FIELD_KEY_PATTERN.fullmatch(field_key):
            raise FieldDemandContractError(f"field_key 格式无效：{field_key}")
        if field_key in seen:
            raise FieldDemandContractError(f"字段键重复：{field_key}")
        seen.add(field_key)

        source_path = _required_text(
            str(item.get("source_path") or ""), label="source_path"
        )
        path_parts = [part.lower() for part in source_path.split(".") if part]
        if (
            len(path_parts) < 2
            or path_parts[0] not in _ALLOWED_CANONICAL_ROOTS
            or any(part in _FORBIDDEN_PATH_PARTS for part in path_parts)
        ):
            raise FieldDemandContractError(
                "字段来源必须是 semantic.*、quality.* 或 governance.* Canonical 路径"
            )
        data_type = _required_text(str(item.get("data_type") or ""), label="data_type")
        required = item.get("required")
        if not isinstance(required, bool):
            raise FieldDemandContractError(f"字段 {field_key} 的 required 必须是布尔值")
        item.update(
            {
                "field_key": field_key,
                "source_path": source_path,
                "data_type": data_type,
                "required": required,
            }
        )
        try:
            canonical_json(item)
        except (TypeError, ValueError) as exc:
            raise FieldDemandContractError(f"字段 {field_key} 不是合法 JSON 定义") from exc
        normalized.append(item)
    return sorted(normalized, key=lambda item: str(item["field_key"]))


def _normalize_thresholds(thresholds: Mapping[str, Any]) -> dict[str, float]:
    if not {"accuracy", "recall"} <= set(thresholds):
        raise FieldDemandContractError("阈值必须包含 accuracy 和 recall")
    normalized: dict[str, float] = {}
    for key, value in thresholds.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FieldDemandContractError(f"阈值 {key} 必须是 0 到 1 的数字")
        numeric = float(value)
        if numeric < 0 or numeric > 1:
            raise FieldDemandContractError(f"阈值 {key} 必须在 0 到 1 之间")
        normalized[str(key)] = numeric
    return dict(sorted(normalized.items()))


def create_field_demand_contract(
    db: Session,
    *,
    contract_key: str,
    category_key: str,
    consumer_key: str,
    owner: str,
    fields: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
    status: str,
    created_by: str,
) -> FieldDemandContract:
    normalized_key = _required_text(contract_key, label="contract_key")
    if not _CONTRACT_KEY_PATTERN.fullmatch(normalized_key):
        raise FieldDemandContractError("contract_key 格式无效")
    normalized_category = _required_text(category_key, label="category_key")
    normalized_consumer = _required_text(consumer_key, label="consumer_key")
    normalized_owner = _required_text(owner, label="owner")
    normalized_creator = _required_text(created_by, label="created_by")
    normalized_status = status.strip().lower()
    if normalized_status not in _STATUSES:
        raise FieldDemandContractError("status 必须是 draft、active 或 retired")
    normalized_fields = _normalize_fields(fields)
    normalized_thresholds = _normalize_thresholds(thresholds)
    definition = {
        "contract_key": normalized_key,
        "category_key": normalized_category,
        "consumer_key": normalized_consumer,
        "owner": normalized_owner,
        "fields": normalized_fields,
        "thresholds": normalized_thresholds,
        "status": normalized_status,
    }
    contract_hash = _hash(definition)
    existing = db.scalar(
        select(FieldDemandContract).where(
            FieldDemandContract.contract_key == normalized_key,
            FieldDemandContract.contract_hash == contract_hash,
        )
    )
    if existing is not None:
        return existing
    latest_version = db.scalar(
        select(func.max(FieldDemandContract.version)).where(
            FieldDemandContract.contract_key == normalized_key
        )
    )
    contract = FieldDemandContract(
        contract_key=normalized_key,
        version=int(latest_version or 0) + 1,
        category_key=normalized_category,
        consumer_key=normalized_consumer,
        owner=normalized_owner,
        fields_json=canonical_json(normalized_fields),
        thresholds_json=canonical_json(normalized_thresholds),
        status=normalized_status,
        contract_hash=contract_hash,
        created_by=normalized_creator,
    )
    db.add(contract)
    db.flush()
    return contract


def record_asset_version(
    db: Session,
    *,
    source_system: str,
    source_content_id: str,
    source_version: str,
    asset: Asset,
    occurred_at: datetime,
) -> tuple[AssetVersion, bool]:
    if asset.id is None:
        raise FieldDemandContractError("asset 必须先持久化")
    normalized_source = _required_text(source_system, label="source_system")
    normalized_content = _required_text(source_content_id, label="source_content_id")
    normalized_version = _required_text(source_version, label="source_version")
    normalized_time = _normalize_occurred_at(occurred_at)
    definition = {
        "source_system": normalized_source,
        "source_content_id": normalized_content,
        "source_version": normalized_version,
        "asset_id": asset.id,
        "sha256": asset.sha256,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "occurred_at": normalized_time.isoformat(),
    }
    payload_hash = _hash(definition)
    existing = db.scalar(
        select(AssetVersion).where(
            AssetVersion.source_system == normalized_source,
            AssetVersion.source_content_id == normalized_content,
            AssetVersion.source_version == normalized_version,
        )
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise FieldDemandContractError(
                "ASSET_VERSION_CONFLICT：同一来源版本对应了不同不可变素材载荷"
            )
        return existing, True
    latest_version = db.scalar(
        select(func.max(AssetVersion.version)).where(AssetVersion.asset_id == asset.id)
    )
    version = AssetVersion(
        source_system=normalized_source,
        source_content_id=normalized_content,
        source_version=normalized_version,
        asset_id=asset.id,
        version=int(latest_version or 0) + 1,
        asset_sha256=asset.sha256,
        storage_backend=getattr(asset, "storage_backend", "local") or "local",
        source_uri=getattr(asset, "source_uri", None),
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        occurred_at=normalized_time,
        payload_hash=payload_hash,
    )
    db.add(version)
    db.flush()
    return version, False


def field_demand_contract_payload(contract: FieldDemandContract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "contract_key": contract.contract_key,
        "version": contract.version,
        "category_key": contract.category_key,
        "consumer_key": contract.consumer_key,
        "owner": contract.owner,
        "fields": json.loads(contract.fields_json),
        "thresholds": json.loads(contract.thresholds_json),
        "status": contract.status,
        "contract_hash": contract.contract_hash,
        "created_by": contract.created_by,
        "created_at": contract.created_at,
    }


def asset_version_payload(version: AssetVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "source_system": version.source_system,
        "source_content_id": version.source_content_id,
        "source_version": version.source_version,
        "asset_id": version.asset_id,
        "sha256": version.asset_sha256,
        "mime_type": version.mime_type,
        "size_bytes": version.size_bytes,
        "occurred_at": version.occurred_at,
        "payload_hash": version.payload_hash,
        "created_at": version.created_at,
    }
