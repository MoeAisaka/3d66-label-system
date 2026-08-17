from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, ContextManager, Mapping, Protocol, Sequence

from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .field_demand_contracts import record_asset_version
from .label_governance import ingest_content_event, route_content_event_to_incremental_package
from .models import (
    Asset,
    ContentRecord,
    UpstreamReadRun,
    UpstreamSourceContract,
)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = {"draft", "active", "retired"}
_REQUIRED_MAPPINGS = {"content_id", "source_version", "category_key", "occurred_at"}
_FORBIDDEN_LOCATOR_PARTS = ("://", "password=", "token=", "secret=")


class ReadOnlySourceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceSafetyEvidence:
    read_only: bool
    verifier: str
    schema_fingerprint: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SourceCursor:
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRow:
    content_id: str
    source_version: str
    category_key: str
    occurred_at: datetime
    asset_id: int | None = None
    event_type: str = "content.created"
    source_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcePage:
    rows: tuple[SourceRow, ...]
    next_cursor: SourceCursor | None
    schema_fingerprint: str


class ReadOnlySourceAdapter(Protocol):
    def verify_read_only(self) -> SourceSafetyEvidence: ...

    def fetch_page(
        self, *, cursor: SourceCursor | None, limit: int
    ) -> SourcePage: ...


class FixtureReadOnlySourceAdapter:
    def __init__(
        self,
        *,
        read_only: bool,
        rows: Sequence[SourceRow],
        schema_fingerprint: str = "f" * 64,
    ) -> None:
        self._read_only = read_only
        self._rows = tuple(rows)
        self._schema_fingerprint = schema_fingerprint
        self.verify_count = 0
        self.fetch_count = 0
        self.last_limit: int | None = None

    def verify_read_only(self) -> SourceSafetyEvidence:
        self.verify_count += 1
        return SourceSafetyEvidence(
            read_only=self._read_only,
            verifier="fixture-explicit-read-only",
            schema_fingerprint=self._schema_fingerprint,
        )

    def fetch_page(
        self, *, cursor: SourceCursor | None, limit: int
    ) -> SourcePage:
        self.fetch_count += 1
        self.last_limit = limit
        rows = self._rows[:limit]
        next_cursor = (
            SourceCursor(
                {
                    "content_id": rows[-1].content_id,
                    "source_version": rows[-1].source_version,
                }
            )
            if rows
            else cursor
        )
        return SourcePage(
            rows=rows,
            next_cursor=next_cursor,
            schema_fingerprint=self._schema_fingerprint,
        )


class SqlReadOnlySourceAdapter:
    """Narrow SQL page adapter; it intentionally exposes no generic query API."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], ContextManager[Connection]],
        table_name: str,
        field_mappings: Mapping[str, str],
        schema_fingerprint: str,
    ) -> None:
        self._connection_factory = connection_factory
        self._table_name = _safe_identifier(table_name)
        self._field_mappings = {
            logical: _safe_identifier(column)
            for logical, column in field_mappings.items()
        }
        if not _REQUIRED_MAPPINGS <= set(self._field_mappings):
            raise ReadOnlySourceError(
                "SOURCE_MAPPING_INVALID", "SQL 来源字段映射不完整"
            )
        self._schema_fingerprint = _validate_hash(schema_fingerprint)

    def _verify_connection_read_only(
        self, connection: Connection
    ) -> SourceSafetyEvidence:
        dialect = connection.dialect.name
        if dialect == "sqlite":
            value = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
            read_only = bool(value)
            verifier = "sqlite:pragma-query-only"
        elif dialect == "postgresql":
            value = connection.exec_driver_sql(
                "SHOW transaction_read_only"
            ).scalar_one()
            read_only = str(value).strip().lower() in {"on", "true", "1"}
            verifier = "postgresql:transaction-read-only"
        elif dialect in {"mysql", "mariadb"}:
            value = connection.exec_driver_sql(
                "SELECT @@transaction_read_only"
            ).scalar_one()
            read_only = bool(value)
            verifier = f"{dialect}:transaction-read-only"
        else:
            return SourceSafetyEvidence(
                read_only=False,
                verifier=f"unsupported:{dialect}",
                schema_fingerprint=self._schema_fingerprint,
            )
        return SourceSafetyEvidence(
            read_only=read_only,
            verifier=verifier,
            schema_fingerprint=self._schema_fingerprint,
        )

    def verify_read_only(self) -> SourceSafetyEvidence:
        with self._connection_factory() as connection:
            return self._verify_connection_read_only(connection)

    def fetch_page(
        self, *, cursor: SourceCursor | None, limit: int
    ) -> SourcePage:
        _validate_limit(limit)
        aliases = sorted(self._field_mappings)
        select_list = ", ".join(
            f'"{self._field_mappings[name]}" AS "{name}"' for name in aliases
        )
        content_column = self._field_mappings["content_id"]
        version_column = self._field_mappings["source_version"]
        params: dict[str, Any] = {"limit": limit}
        where = ""
        cursor_values = dict(cursor.values) if cursor is not None else {}
        if cursor_values:
            params.update(
                {
                    "cursor_content_id": str(cursor_values.get("content_id", "")),
                    "cursor_source_version": str(
                        cursor_values.get("source_version", "")
                    ),
                }
            )
            where = (
                f' WHERE ("{content_column}" > :cursor_content_id) '
                f'OR ("{content_column}" = :cursor_content_id '
                f'AND "{version_column}" > :cursor_source_version)'
            )
        sql = (
            f'SELECT {select_list} FROM "{self._table_name}"{where} '
            f'ORDER BY "{content_column}", "{version_column}" LIMIT :limit'
        )
        with self._connection_factory() as connection:
            evidence = self._verify_connection_read_only(connection)
            if not evidence.read_only:
                raise ReadOnlySourceError(
                    "SOURCE_NOT_READ_ONLY", "分页读取连接无法证明为只读"
                )
            rows = connection.exec_driver_sql(sql, params).mappings().all()
        normalized = tuple(_source_row_from_mapping(row) for row in rows)
        next_cursor = (
            SourceCursor(
                {
                    "content_id": normalized[-1].content_id,
                    "source_version": normalized[-1].source_version,
                }
            )
            if normalized
            else cursor
        )
        return SourcePage(
            rows=normalized,
            next_cursor=next_cursor,
            schema_fingerprint=self._schema_fingerprint,
        )


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ReadOnlySourceError(
            "SOURCE_IDENTIFIER_INVALID", "来源表名和列名必须是已校验标识符"
        )
    return normalized


def _validate_hash(value: str) -> str:
    normalized = value.strip().lower()
    if not _HASH.fullmatch(normalized):
        raise ReadOnlySourceError(
            "SOURCE_SCHEMA_FINGERPRINT_INVALID", "schema fingerprint 必须是 SHA-256"
        )
    return normalized


def _required_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ReadOnlySourceError("SOURCE_CONTRACT_INVALID", f"{label}不能为空")
    return normalized


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 500:
        raise ReadOnlySourceError(
            "SOURCE_PAGE_LIMIT_EXCEEDED", "来源单次读取上限为 500 条"
        )


def _source_row_from_mapping(row: Mapping[str, Any]) -> SourceRow:
    occurred_at = row["occurred_at"]
    if isinstance(occurred_at, str):
        occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    asset_id = row.get("asset_id")
    return SourceRow(
        content_id=str(row["content_id"]),
        source_version=str(row["source_version"]),
        category_key=str(row["category_key"]),
        occurred_at=occurred_at,
        asset_id=int(asset_id) if asset_id is not None else None,
        event_type=str(row.get("event_type") or "content.created"),
    )


def create_upstream_source_contract(
    db: Session,
    *,
    contract_key: str,
    adapter_key: str,
    source_system: str,
    category_key: str,
    connection_locator: str,
    secret_reference: str,
    field_mappings: Mapping[str, str],
    cursor_definition: Mapping[str, Any],
    page_size: int,
    read_only: bool,
    schema_fingerprint: str,
    owner: str,
    status: str,
    created_by: str,
) -> UpstreamSourceContract:
    normalized_key = _required_text(contract_key, label="contract_key")
    if not _KEY.fullmatch(normalized_key):
        raise ReadOnlySourceError("SOURCE_CONTRACT_INVALID", "contract_key 格式无效")
    normalized_adapter = _required_text(adapter_key, label="adapter_key")
    normalized_source = _required_text(source_system, label="source_system")
    normalized_category = _required_text(category_key, label="category_key")
    normalized_locator = _required_text(connection_locator, label="connection_locator")
    normalized_secret_ref = _required_text(secret_reference, label="secret_reference")
    if any(part in normalized_locator.lower() for part in _FORBIDDEN_LOCATOR_PARTS):
        raise ReadOnlySourceError(
            "SOURCE_CONTRACT_SECRET_EXPOSED", "connection_locator 只能保存逻辑标识"
        )
    if any(part in normalized_secret_ref.lower() for part in _FORBIDDEN_LOCATOR_PARTS):
        raise ReadOnlySourceError(
            "SOURCE_CONTRACT_SECRET_EXPOSED", "secret_reference 只能保存引用键"
        )
    if not read_only:
        raise ReadOnlySourceError(
            "SOURCE_NOT_READ_ONLY", "来源合同必须声明为只读"
        )
    _validate_limit(page_size)
    normalized_status = status.strip().lower()
    if normalized_status not in _STATUSES:
        raise ReadOnlySourceError("SOURCE_CONTRACT_INVALID", "来源合同状态无效")
    mappings = {str(key): str(value).strip() for key, value in field_mappings.items()}
    if not _REQUIRED_MAPPINGS <= set(mappings) or any(not value for value in mappings.values()):
        raise ReadOnlySourceError(
            "SOURCE_MAPPING_INVALID", "来源字段映射必须包含内容、版本、类目和时间"
        )
    fingerprint = _validate_hash(schema_fingerprint)
    definition = {
        "contract_key": normalized_key,
        "adapter_key": normalized_adapter,
        "source_system": normalized_source,
        "category_key": normalized_category,
        "connection_locator": normalized_locator,
        "secret_reference": normalized_secret_ref,
        "field_mappings": dict(sorted(mappings.items())),
        "cursor_definition": dict(cursor_definition),
        "page_size": page_size,
        "read_only": True,
        "schema_fingerprint": fingerprint,
        "owner": _required_text(owner, label="owner"),
        "status": normalized_status,
    }
    contract_hash = hashlib.sha256(
        canonical_json(definition).encode("utf-8")
    ).hexdigest()
    existing = db.scalar(
        select(UpstreamSourceContract).where(
            UpstreamSourceContract.contract_key == normalized_key,
            UpstreamSourceContract.contract_hash == contract_hash,
        )
    )
    if existing is not None:
        return existing
    latest_version = db.scalar(
        select(func.max(UpstreamSourceContract.version)).where(
            UpstreamSourceContract.contract_key == normalized_key
        )
    )
    contract = UpstreamSourceContract(
        contract_key=normalized_key,
        version=int(latest_version or 0) + 1,
        adapter_key=normalized_adapter,
        source_system=normalized_source,
        category_key=normalized_category,
        connection_locator=normalized_locator,
        secret_reference=normalized_secret_ref,
        field_mappings_json=canonical_json(dict(sorted(mappings.items()))),
        cursor_definition_json=canonical_json(dict(cursor_definition)),
        page_size=page_size,
        read_only=True,
        schema_fingerprint=fingerprint,
        owner=definition["owner"],
        status=normalized_status,
        contract_hash=contract_hash,
        created_by=_required_text(created_by, label="created_by"),
    )
    db.add(contract)
    db.flush()
    return contract


def _cursor_payload(cursor: SourceCursor | None) -> dict[str, Any]:
    return dict(cursor.values) if cursor is not None else {}


def _row_payload(row: SourceRow) -> dict[str, Any]:
    occurred_at = row.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return {
        "content_id": row.content_id,
        "source_version": row.source_version,
        "category_key": row.category_key,
        "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
        "asset_id": row.asset_id,
        "event_type": row.event_type,
        "source_payload": dict(row.source_payload),
    }


def poll_upstream_source(
    db: Session,
    *,
    contract: UpstreamSourceContract,
    adapter: ReadOnlySourceAdapter,
    limit: int,
    actor: str,
    cursor: SourceCursor | None = None,
) -> UpstreamReadRun:
    _validate_limit(limit)
    run = UpstreamReadRun(
        source_contract_id=contract.id,
        source_contract_hash=contract.contract_hash,
        category_key=contract.category_key,
        requested_cursor_json=canonical_json(_cursor_payload(cursor)),
        requested_limit=limit,
        status="running",
        actor=_required_text(actor, label="actor"),
    )
    db.add(run)
    db.flush()
    if contract.status != "active" or not contract.read_only:
        run.status = "blocked"
        run.error_code = "SOURCE_CONTRACT_INACTIVE"
        run.error_message = "来源合同未启用或未声明只读"
        return run

    try:
        evidence = adapter.verify_read_only()
        if not evidence.read_only:
            run.status = "blocked"
            run.error_code = "SOURCE_NOT_READ_ONLY"
            run.error_message = "连接时无法证明来源为只读"
            return run
        if (
            evidence.schema_fingerprint
            and evidence.schema_fingerprint != contract.schema_fingerprint
        ):
            run.status = "blocked"
            run.error_code = "SOURCE_SCHEMA_DRIFT"
            run.error_message = "只读证据中的 schema fingerprint 与合同不一致"
            run.schema_fingerprint = evidence.schema_fingerprint
            return run
        page = adapter.fetch_page(cursor=cursor, limit=limit)
        run.schema_fingerprint = page.schema_fingerprint
        run.next_cursor_json = canonical_json(_cursor_payload(page.next_cursor))
        run.page_hash = hashlib.sha256(
            canonical_json([_row_payload(row) for row in page.rows]).encode("utf-8")
        ).hexdigest()
        if page.schema_fingerprint != contract.schema_fingerprint:
            run.status = "blocked"
            run.error_code = "SOURCE_SCHEMA_DRIFT"
            run.error_message = "来源页面 schema fingerprint 与合同不一致"
            return run

        row_count = 0
        duplicate_count = 0
        package_count = 0
        awaiting_material_count = 0
        with db.begin_nested():
            for row in page.rows:
                if row.category_key != contract.category_key:
                    raise ReadOnlySourceError(
                        "SOURCE_CATEGORY_MISMATCH", "来源行类目与合同不一致"
                    )
                asset: Asset | None = None
                asset_version_id: int | None = None
                if row.asset_id is not None:
                    asset = db.get(Asset, row.asset_id)
                    if asset is None or asset.category_key != row.category_key:
                        raise ReadOnlySourceError(
                            "SOURCE_ASSET_INVALID", "来源行绑定素材不存在或类目不一致"
                        )
                    asset_version, _asset_duplicate = record_asset_version(
                        db,
                        source_system=contract.source_system,
                        source_content_id=row.content_id,
                        source_version=row.source_version,
                        asset=asset,
                        occurred_at=row.occurred_at,
                    )
                    asset_version_id = asset_version.id
                event_id = "readonly-source:" + hashlib.sha256(
                    (
                        f"{contract.source_system}:{row.content_id}:"
                        f"{row.source_version}:{row.event_type}"
                    ).encode("utf-8")
                ).hexdigest()
                payload: dict[str, Any] = {
                    **dict(row.source_payload),
                    "content_id": row.content_id,
                    "content_version": row.source_version,
                    "category_key": row.category_key,
                }
                if asset is not None:
                    payload["asset_id"] = asset.id
                event, record, duplicate = ingest_content_event(
                    db,
                    event_id=event_id,
                    schema_version="content-ingress-v1",
                    event_type=row.event_type,
                    source_system=contract.source_system,
                    occurred_at=row.occurred_at,
                    payload=payload,
                    received_by=actor,
                )
                if asset_version_id is not None and event.status != "stale":
                    record.current_asset_version_id = asset_version_id
                _package, package_created, routing_status = (
                    route_content_event_to_incremental_package(
                        db,
                        event=event,
                        record=record,
                        duplicate=duplicate,
                        actor=actor,
                    )
                )
                row_count += 1
                duplicate_count += int(duplicate)
                package_count += int(package_created)
                if routing_status == "awaiting_material":
                    awaiting_material_count += 1
        run.row_count = row_count
        run.duplicate_count = duplicate_count
        run.package_count = package_count
        run.awaiting_material_count = awaiting_material_count
        run.status = "succeeded"
        return run
    except ReadOnlySourceError as exc:
        run.status = "blocked"
        run.error_code = exc.code
        run.error_message = str(exc)
        return run
    except Exception as exc:
        run.status = "failed"
        run.error_code = "SOURCE_READ_FAILED"
        run.error_message = type(exc).__name__
        return run


def source_contract_payload(contract: UpstreamSourceContract) -> dict[str, Any]:
    import json

    return {
        "id": contract.id,
        "contract_key": contract.contract_key,
        "version": contract.version,
        "adapter_key": contract.adapter_key,
        "source_system": contract.source_system,
        "category_key": contract.category_key,
        "connection_locator": contract.connection_locator,
        "secret_reference": contract.secret_reference,
        "secret_status": "unresolved",
        "field_mappings": json.loads(contract.field_mappings_json),
        "cursor_definition": json.loads(contract.cursor_definition_json),
        "page_size": contract.page_size,
        "read_only": contract.read_only,
        "schema_fingerprint": contract.schema_fingerprint,
        "owner": contract.owner,
        "status": contract.status,
        "contract_hash": contract.contract_hash,
        "created_by": contract.created_by,
        "created_at": contract.created_at,
    }


def source_run_payload(run: UpstreamReadRun) -> dict[str, Any]:
    import json

    return {
        "id": run.id,
        "source_contract_id": run.source_contract_id,
        "source_contract_hash": run.source_contract_hash,
        "category_key": run.category_key,
        "requested_cursor": json.loads(run.requested_cursor_json),
        "next_cursor": json.loads(run.next_cursor_json),
        "requested_limit": run.requested_limit,
        "status": run.status,
        "schema_fingerprint": run.schema_fingerprint,
        "page_hash": run.page_hash,
        "row_count": run.row_count,
        "package_count": run.package_count,
        "duplicate_count": run.duplicate_count,
        "awaiting_material_count": run.awaiting_material_count,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "attempt_count": run.attempt_count,
        "retry_after": run.retry_after,
        "actor": run.actor,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
