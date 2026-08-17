from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, Mapping, Protocol

from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from .audit import canonical_json
from .models import (
    FieldDemandContract,
    ProjectionContract,
    ProjectionManifest,
    PublishedLabel,
    ShadowProjectionLease,
    ShadowProjectionRun,
    ShadowProjectionTarget,
)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN = {"candidate", "raw_response", "human_review", "credential", "secret", "token", "password"}
_RETRYABLE_CODES = {"PROJECTION_TRANSIENT_FAILURE", "PROJECTION_CIRCUIT_OPEN"}


class ShadowProjectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TransientShadowProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShadowSafetyEvidence:
    shadow_only: bool
    least_privileged: bool
    schema_fingerprint: str


@dataclass(frozen=True)
class ShadowManifest:
    manifest: ProjectionManifest
    rows: list[dict[str, Any]]
    row_count: int
    payload_hash: str
    manifest_hash: str


class ProjectionTargetAdapter(Protocol):
    def verify_shadow_target(self) -> ShadowSafetyEvidence: ...
    def apply_batch(self, *, batch_id: str, rows: list[dict[str, Any]]) -> None: ...
    def read_back(self, *, batch_id: str) -> list[dict[str, Any]]: ...
    def rollback_batch(self, *, batch_id: str) -> int: ...


class FixtureShadowProjectionAdapter:
    def __init__(self, *, shadow_only: bool, least_privileged: bool, schema_fingerprint: str, transient_before_apply: int = 0, transient_after_apply: int = 0, corrupt_readback: bool = False) -> None:
        self._evidence = ShadowSafetyEvidence(shadow_only, least_privileged, schema_fingerprint)
        self.transient_before_apply = transient_before_apply
        self.transient_after_apply = transient_after_apply
        self.corrupt_readback = corrupt_readback
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [dict(value) for _, value in sorted(self._rows.items())]

    def verify_shadow_target(self) -> ShadowSafetyEvidence:
        return self._evidence

    def apply_batch(self, *, batch_id: str, rows: list[dict[str, Any]]) -> None:
        if self.transient_before_apply > 0:
            self.transient_before_apply -= 1
            raise TransientShadowProjectionError("fixture transient before apply")
        for row in rows:
            self._rows[(batch_id, str(row["content_key"]))] = dict(row)
        if self.transient_after_apply > 0:
            self.transient_after_apply -= 1
            raise TransientShadowProjectionError("fixture transient after apply")

    def read_back(self, *, batch_id: str) -> list[dict[str, Any]]:
        rows = [dict(value) for (stored_batch, _), value in sorted(self._rows.items()) if stored_batch == batch_id]
        if self.corrupt_readback and rows:
            rows[0]["quality_score"] = -1
        return rows

    def rollback_batch(self, *, batch_id: str) -> int:
        keys = [key for key in self._rows if key[0] == batch_id]
        for key in keys:
            del self._rows[key]
        return len(keys)


class SqlShadowProjectionAdapter:
    def __init__(self, *, connection_factory: Callable[[], ContextManager[Connection]], table_name: str, evidence: ShadowSafetyEvidence) -> None:
        if not _IDENTIFIER.fullmatch(table_name):
            raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", "影子表名不是已校验标识符")
        self._connection_factory = connection_factory
        self._table_name = table_name
        self._evidence = evidence

    def verify_shadow_target(self) -> ShadowSafetyEvidence:
        return self._evidence

    def apply_batch(self, *, batch_id: str, rows: list[dict[str, Any]]) -> None:
        with self._connection_factory() as connection:
            for row in rows:
                raw = canonical_json(row)
                digest = _hash(row)
                connection.exec_driver_sql(
                    f'INSERT INTO "{self._table_name}" (batch_id, content_key, payload_json, payload_hash) VALUES (:batch_id,:content_key,:payload_json,:payload_hash) ON CONFLICT(batch_id,content_key) DO UPDATE SET payload_json=excluded.payload_json,payload_hash=excluded.payload_hash',
                    {"batch_id": batch_id, "content_key": str(row["content_key"]), "payload_json": raw, "payload_hash": digest},
                )

    def read_back(self, *, batch_id: str) -> list[dict[str, Any]]:
        with self._connection_factory() as connection:
            rows = connection.exec_driver_sql(f'SELECT payload_json FROM "{self._table_name}" WHERE batch_id=:batch_id ORDER BY content_key', {"batch_id": batch_id}).all()
        return [json.loads(row[0]) for row in rows]

    def rollback_batch(self, *, batch_id: str) -> int:
        with self._connection_factory() as connection:
            result = connection.exec_driver_sql(f'DELETE FROM "{self._table_name}" WHERE batch_id=:batch_id', {"batch_id": batch_id})
            return int(result.rowcount or 0)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", f"{label}不能为空")
    if "://" in normalized or "password=" in normalized.lower():
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", f"{label} 只能保存逻辑引用")
    return normalized


def create_shadow_projection_target(db: Session, *, target_key: str, adapter_key: str, connection_locator: str, secret_reference: str, schema_name: str, table_name: str, environment: str, shadow_only: bool, owner: str, schema_fingerprint: str, status: str, created_by: str) -> ShadowProjectionTarget:
    if environment != "shadow" or not shadow_only:
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", "本批目标必须是 shadow_only")
    if not _IDENTIFIER.fullmatch(schema_name) or not _IDENTIFIER.fullmatch(table_name):
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", "影子 schema/table 名无效")
    if not _SHA256.fullmatch(schema_fingerprint.lower()):
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", "影子目标 schema fingerprint 无效")
    if status not in {"draft", "active", "retired"}:
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", "影子目标状态无效")
    normalized_target_key = _required(target_key, "target_key")
    normalized_adapter_key = _required(adapter_key, "adapter_key")
    normalized_locator = _required(connection_locator, "connection_locator")
    normalized_secret_reference = _required(secret_reference, "secret_reference")
    normalized_owner = _required(owner, "owner")
    definition = {"target_key": normalized_target_key, "adapter_key": normalized_adapter_key, "connection_locator": normalized_locator, "secret_reference": normalized_secret_reference, "schema_name": schema_name, "table_name": table_name, "environment": environment, "shadow_only": shadow_only, "owner": normalized_owner, "schema_fingerprint": schema_fingerprint.lower(), "status": status}
    target_hash = _hash(definition)
    existing = db.scalar(select(ShadowProjectionTarget).where(ShadowProjectionTarget.target_key == normalized_target_key, ShadowProjectionTarget.target_hash == target_hash))
    if existing is not None:
        return existing
    version = int(db.scalar(select(func.max(ShadowProjectionTarget.version)).where(ShadowProjectionTarget.target_key == normalized_target_key)) or 0) + 1
    target = ShadowProjectionTarget(target_key=normalized_target_key, version=version, adapter_key=normalized_adapter_key, connection_locator=normalized_locator, secret_reference=normalized_secret_reference, schema_name=schema_name, table_name=table_name, environment="shadow", shadow_only=True, owner=normalized_owner, schema_fingerprint=schema_fingerprint.lower(), status=status, target_hash=target_hash, created_by=_required(created_by, "created_by"))
    db.add(target)
    db.flush()
    return target


def enqueue_shadow_projection_run(db: Session, *, projection_contract: ProjectionContract, field_contract: FieldDemandContract, target: ShadowProjectionTarget, max_rows: int, actor: str) -> ShadowProjectionRun:
    safe_max = max_rows if 1 <= max_rows <= 500 else 1
    run = ShadowProjectionRun(projection_contract_id=projection_contract.id, field_contract_id=field_contract.id, target_id=target.id, batch_id=f"shadow-{uuid.uuid4().hex}", status="queued", max_rows=safe_max, actor=actor)
    db.add(run)
    db.flush()
    if not 1 <= max_rows <= 500 or max_rows > projection_contract.max_batch_size:
        run.status = "blocked"
        run.error_code = "CANARY_LIMIT_EXCEEDED"
        run.error_message = "影子金丝雀单批不得超过合同上限或 500 条"
    elif projection_contract.environment != "shadow" or projection_contract.write_policy != "shadow_only" or projection_contract.status != "active":
        run.status = "blocked"; run.error_code = "SHADOW_CONTRACT_INACTIVE"; run.error_message = "影子投影合同未启用"
    elif target.status != "active" or not target.shadow_only or projection_contract.target_key != target.target_key:
        run.status = "blocked"; run.error_code = "SHADOW_TARGET_UNREGISTERED"; run.error_message = "影子目标未登记或不匹配"
    elif field_contract.status != "active" or projection_contract.field_contract_id != field_contract.id or projection_contract.category_key != field_contract.category_key:
        run.status = "blocked"; run.error_code = "FIELD_CONTRACT_MISMATCH"; run.error_message = "字段合同与投影合同不一致"
    return run


def _lookup(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, Mapping): return None
        value = value.get(part)
    return value


def build_shadow_manifest(db: Session, *, run: ShadowProjectionRun) -> ShadowManifest:
    contract = db.get(ProjectionContract, run.projection_contract_id)
    field_contract = db.get(FieldDemandContract, run.field_contract_id)
    if contract is None or field_contract is None:
        raise ShadowProjectionError("FIELD_CONTRACT_MISMATCH", "投影或字段合同不存在")
    mappings = json.loads(contract.field_mappings_json)
    field_paths = {item["source_path"] for item in json.loads(field_contract.fields_json)}
    for source in mappings.values():
        root = str(source).split(".", 1)[0]
        if root in {"semantic", "quality", "governance"} and source not in field_paths:
            raise ShadowProjectionError("FIELD_CONTRACT_MISMATCH", f"字段未登记：{source}")
    labels = list(db.scalars(select(PublishedLabel).where(PublishedLabel.status == "published", PublishedLabel.category_key == field_contract.category_key).order_by(PublishedLabel.content_key, PublishedLabel.id).limit(run.max_rows + 1)).all())
    if len(labels) > run.max_rows:
        raise ShadowProjectionError("CANARY_LIMIT_EXCEEDED", "正式事实超过金丝雀上限")
    rows: list[dict[str, Any]] = []
    for label in labels:
        payload = json.loads(label.label_payload_json or "{}")
        provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
        strategy_bundle_id = provenance.get("strategy_bundle_id")
        if (
            not provenance.get("asset_sha256")
            or isinstance(strategy_bundle_id, bool)
            or not isinstance(strategy_bundle_id, int)
            or strategy_bundle_id < 1
            or not provenance.get("model_id")
        ):
            raise ShadowProjectionError("PROJECTION_VERSION_GAP", "正式事实缺少资产、机制或模型版本")
        row = {}
        for target_field, source in sorted(mappings.items()):
            if any(token in str(source).lower() for token in _FORBIDDEN):
                raise ShadowProjectionError("UNPUBLISHED_FACT_DETECTED", "投影映射包含候选或过程字段")
            if source == "$label.version": value = label.version
            elif source == "content_key": value = label.content_key
            elif source == "category_key": value = label.category_key
            else: value = _lookup(payload, source)
            row[target_field] = value
        row["_projection_version"] = {"published_label_id": label.id, "label_version": label.version, "field_contract_id": field_contract.id, "field_contract_version": field_contract.version}
        rows.append(row)
    payload_hash = _hash(rows)
    base = {"contract_id": contract.id, "contract_hash": contract.contract_hash, "field_contract_id": field_contract.id, "field_contract_hash": field_contract.contract_hash, "batch_id": run.batch_id, "rows": rows, "payload_hash": payload_hash}
    manifest_hash = _hash(base)
    manifest = db.scalar(select(ProjectionManifest).where(ProjectionManifest.contract_id == contract.id, ProjectionManifest.manifest_hash == manifest_hash))
    if manifest is None:
        manifest = ProjectionManifest(contract_id=contract.id, manifest_hash=manifest_hash, payload_hash=payload_hash, row_count=len(rows), content_keys_json=canonical_json([row["content_key"] for row in rows]), input_versions_json=canonical_json({"field_contract_id": field_contract.id, "field_contract_version": field_contract.version}), rows_json=canonical_json(rows))
        db.add(manifest); db.flush()
    run.manifest_id = manifest.id; run.expected_row_count = len(rows); run.expected_payload_hash = payload_hash
    return ShadowManifest(manifest, rows, len(rows), payload_hash, manifest_hash)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _acquire_lease(db: Session, target_id: int, worker_id: str, now: datetime) -> ShadowProjectionLease | None:
    lease = db.scalar(select(ShadowProjectionLease).where(ShadowProjectionLease.target_id == target_id))
    if lease is not None and lease.worker_id != worker_id and _aware(lease.expires_at) > now:
        return None
    if lease is None:
        lease = ShadowProjectionLease(target_id=target_id, worker_id=worker_id, acquired_at=now, heartbeat_at=now, expires_at=now + timedelta(seconds=30)); db.add(lease)
    else:
        lease.worker_id = worker_id; lease.heartbeat_at = now; lease.expires_at = now + timedelta(seconds=30)
    db.flush(); return lease


def shadow_projection_worker_tick(db: Session, worker_id: str, *, adapter_resolver: Callable[[ShadowProjectionTarget], ProjectionTargetAdapter]) -> ShadowProjectionRun | None:
    now = datetime.now(timezone.utc)
    run = db.scalar(select(ShadowProjectionRun).where(ShadowProjectionRun.status == "queued", (ShadowProjectionRun.retry_after.is_(None) | (ShadowProjectionRun.retry_after <= now))).order_by(ShadowProjectionRun.id).limit(1))
    if run is None: return None
    target = db.get(ShadowProjectionTarget, run.target_id)
    if target is None: run.status="blocked"; run.error_code="SHADOW_TARGET_UNREGISTERED"; return run
    lease = _acquire_lease(db, target.id, worker_id, now)
    if lease is None: return None
    try:
        if target.circuit_opened_at is not None:
            run.status="blocked"; run.error_code="PROJECTION_CIRCUIT_OPEN"; return run
        adapter = adapter_resolver(target)
        evidence = adapter.verify_shadow_target()
        if not evidence.shadow_only:
            run.status="blocked"; run.error_code="SHADOW_TARGET_UNREGISTERED"; run.error_message="目标不是影子环境"; return run
        if not evidence.least_privileged:
            run.status="blocked"; run.error_code="SHADOW_PERMISSION_OVERBROAD"; run.error_message="目标写权限超出登记影子表"; return run
        if evidence.schema_fingerprint != target.schema_fingerprint:
            run.status="blocked"; run.error_code="SHADOW_SCHEMA_DRIFT"; run.error_message="影子目标 schema 漂移"; return run
        manifest = build_shadow_manifest(db, run=run)
        run.status="running"; run.worker_id=worker_id; run.attempt_count += 1; run.started_at = run.started_at or now
        adapter.apply_batch(batch_id=run.batch_id, rows=manifest.rows)
        actual = adapter.read_back(batch_id=run.batch_id)
        actual_hash = _hash(actual)
        run.actual_row_count=len(actual); run.actual_payload_hash=actual_hash
        if len(actual) != manifest.row_count or actual_hash != manifest.payload_hash:
            run.status="blocked"; run.error_code="PROJECTION_HASH_DRIFT"; run.error_message="影子批次读回对账失败"; return run
        run.checkpoint_json=canonical_json({"batch_id": run.batch_id, "manifest_id": manifest.manifest.id, "reconciled": True, "row_count": manifest.row_count, "payload_hash": manifest.payload_hash})
        run.status="succeeded"; run.error_code=""; run.error_message=""; run.retry_after=None; run.finished_at=datetime.now(timezone.utc)
        target.consecutive_failures=0
        return run
    except TransientShadowProjectionError:
        target.consecutive_failures += 1
        if target.consecutive_failures >= 3:
            target.circuit_opened_at=datetime.now(timezone.utc); run.status="blocked"; run.error_code="PROJECTION_CIRCUIT_OPEN"; run.error_message="连续三次瞬态失败，目标熔断"
        else:
            run.status="queued"; run.error_code="PROJECTION_TRANSIENT_FAILURE"; run.error_message="影子目标瞬态失败"; run.retry_after=datetime.now(timezone.utc) + timedelta(seconds=2 ** target.consecutive_failures)
        return run
    except ShadowProjectionError as exc:
        run.status="blocked"; run.error_code=exc.code; run.error_message=str(exc); return run
    finally:
        db.delete(lease); db.flush()


def rollback_shadow_projection_run(db: Session, *, run: ShadowProjectionRun, adapter: ProjectionTargetAdapter, actor: str) -> ShadowProjectionRun:
    if run.status == "rolled_back":
        return run
    if run.status not in {"succeeded", "blocked", "failed"}:
        raise ShadowProjectionError("PROJECTION_ROLLBACK_NOT_READY", "影子批次当前不可回滚")
    removed = adapter.rollback_batch(batch_id=run.batch_id)
    checkpoint = json.loads(run.checkpoint_json or "{}")
    checkpoint["rollback"] = {"removed_rows": removed, "actor": actor, "canonical_rows_mutated": False}
    run.checkpoint_json=canonical_json(checkpoint); run.status="rolled_back"; run.finished_at=datetime.now(timezone.utc)
    db.flush(); return run


def retry_shadow_projection_run(db: Session, *, run: ShadowProjectionRun, actor: str) -> ShadowProjectionRun:
    if run.error_code not in _RETRYABLE_CODES:
        raise ShadowProjectionError("PROJECTION_NOT_RETRYABLE", "当前阻塞不是可重试的瞬态故障")
    target = db.get(ShadowProjectionTarget, run.target_id)
    if target is None:
        raise ShadowProjectionError("SHADOW_TARGET_UNREGISTERED", "影子目标不存在")
    checkpoint = json.loads(run.checkpoint_json or "{}")
    checkpoint["resume"] = {
        "actor": _required(actor, "actor"),
        "previous_error_code": run.error_code,
        "circuit_reviewed": target.circuit_opened_at is not None,
    }
    target.consecutive_failures = 0
    target.circuit_opened_at = None
    run.status = "queued"
    run.worker_id = ""
    run.error_code = ""
    run.error_message = ""
    run.retry_after = None
    run.finished_at = None
    run.checkpoint_json = canonical_json(checkpoint)
    db.flush()
    return run


def shadow_projection_target_payload(target: ShadowProjectionTarget) -> dict[str, Any]:
    return {
        "id": target.id,
        "target_key": target.target_key,
        "version": target.version,
        "adapter_key": target.adapter_key,
        "connection_locator": target.connection_locator,
        "secret_status": "unresolved",
        "schema_name": target.schema_name,
        "table_name": target.table_name,
        "environment": target.environment,
        "shadow_only": target.shadow_only,
        "owner": target.owner,
        "schema_fingerprint": target.schema_fingerprint,
        "status": target.status,
        "target_hash": target.target_hash,
        "consecutive_failures": target.consecutive_failures,
        "circuit_opened_at": target.circuit_opened_at,
        "created_by": target.created_by,
        "created_at": target.created_at,
    }


def shadow_projection_run_payload(run: ShadowProjectionRun) -> dict[str, Any]:
    target = run.target
    return {
        "id": run.id,
        "projection_contract_id": run.projection_contract_id,
        "field_contract_id": run.field_contract_id,
        "target_id": run.target_id,
        "manifest_id": run.manifest_id,
        "batch_id": run.batch_id,
        "status": run.status,
        "worker_id": run.worker_id,
        "attempt_count": run.attempt_count,
        "max_rows": run.max_rows,
        "checkpoint": json.loads(run.checkpoint_json or "{}"),
        "expected_row_count": run.expected_row_count,
        "actual_row_count": run.actual_row_count,
        "expected_payload_hash": run.expected_payload_hash,
        "actual_payload_hash": run.actual_payload_hash,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "retry_after": run.retry_after,
        "actor": run.actor,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "target": shadow_projection_target_payload(target),
        "writes_production_database": False,
        "awaits_human_acceptance": run.status == "succeeded",
    }


def resolve_configured_shadow_projection_adapter(
    _target: ShadowProjectionTarget,
) -> ProjectionTargetAdapter:
    raise ShadowProjectionError(
        "SHADOW_ADAPTER_UNAVAILABLE",
        "影子目标运行时适配器与 secret 引用尚未激活",
    )
