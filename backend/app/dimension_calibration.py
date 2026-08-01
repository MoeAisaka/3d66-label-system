"""Isolated persistence and state machine for routed dimension calibration."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .dimension_schema_registry import canonical_hash, canonical_json
from .models import (
    Asset,
    DimensionCalibrationItem,
    DimensionCalibrationRun,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
)
from .routed_strategy import (
    build_routed_evaluation_strategy_snapshot,
    load_frozen_evaluation_profiles,
)
from .scoring import calculate_score
from .strategy_bundle import (
    ROUTED_STRATEGY_SCHEMA_VERSION,
    build_strategy_snapshot,
)


CALIBRATION_MANIFEST_FORMAT_VERSION = "dimension-calibration-manifest-v1"
CALIBRATION_RUN_FORMAT_VERSION = "dimension-calibration-run-v1"
CALIBRATION_ITEM_TERMINAL_STATUSES = {
    "completed",
    "core_fallback",
    "blocked",
    "unassessable",
    "failed",
}
CALIBRATION_BUSINESS_STATUSES = {
    "completed",
    "core_fallback",
    "blocked",
    "unassessable",
}
_RUN_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_ERROR_TYPE = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")
_MAX_RAW_RESPONSE_CHARS = 1_000_000


class DimensionCalibrationContractError(ValueError):
    """Raised when a calibration definition or result is unsafe."""


class DimensionCalibrationStateError(RuntimeError):
    """Raised when a calibration item cannot make the requested transition."""


def _json_object(value: str | dict[str, Any], *, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DimensionCalibrationContractError(
            f"{label} 不是有效 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise DimensionCalibrationContractError(f"{label} 必须是 JSON 对象")
    return payload


def _aware_timestamp(value: datetime | None = None) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise DimensionCalibrationContractError("时间必须包含时区")
    return timestamp


def _asset_snapshot(asset: Asset) -> dict[str, Any]:
    return {
        "format_version": "dimension-calibration-asset-v1",
        "asset_id": asset.id,
        "original_name": asset.original_name,
        "stored_name": asset.stored_name,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "sha256": asset.sha256,
        "status": asset.status,
    }


def _unique_bundle_inputs(
    db: Session,
    bundle: StrategyBundle,
) -> tuple[str, PromptVersion, SamplingPolicy | None]:
    prompt_matches = db.scalars(
        select(PromptVersion).where(
            PromptVersion.stage == "A",
            PromptVersion.version == bundle.prompt_a_version,
        )
    ).all()
    if bundle.sampling_policy_revision is None:
        policy_matches: list[SamplingPolicy | None] = [None]
    else:
        policy_matches = list(
            db.scalars(
                select(SamplingPolicy).where(
                    SamplingPolicy.revision
                    == bundle.sampling_policy_revision
                )
            ).all()
        )
    matches: list[tuple[str, PromptVersion, SamplingPolicy | None]] = []
    for prompt_a in prompt_matches:
        for policy in policy_matches:
            try:
                snapshot = build_strategy_snapshot(
                    bundle,
                    prompt_a,
                    None,
                    policy,
                )
            except ValueError:
                continue
            matches.append((snapshot, prompt_a, policy))
    if len(matches) != 1:
        raise DimensionCalibrationContractError(
            "v3 Bundle 缺少唯一可验证的 A 提示词或抽样策略"
        )
    return matches[0]


def _validate_calibration_bundle(
    db: Session,
    bundle: StrategyBundle,
) -> tuple[str, PromptVersion, SamplingPolicy | None]:
    if bundle.strategy_schema_version != ROUTED_STRATEGY_SCHEMA_VERSION:
        raise DimensionCalibrationContractError(
            "维度校准只允许 strategy-bundle-v3"
        )
    try:
        _route_policy, profile_set = load_frozen_evaluation_profiles(bundle)
    except ValueError as exc:
        raise DimensionCalibrationContractError(str(exc)) from exc
    if profile_set["execution_context"] != "calibration":
        raise DimensionCalibrationContractError(
            "维度校准拒绝 production 上下文 Bundle"
        )
    return _unique_bundle_inputs(db, bundle)


def _calibration_definition(
    *,
    bundle: StrategyBundle,
    asset_snapshots: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    manifest = {
        "format_version": CALIBRATION_MANIFEST_FORMAT_VERSION,
        "strategy_bundle_hash": bundle.canonical_hash,
        "assets": asset_snapshots,
    }
    manifest["canonical_hash"] = canonical_hash(manifest)
    definition = {
        "format_version": CALIBRATION_RUN_FORMAT_VERSION,
        "strategy_bundle_id": bundle.id,
        "strategy_bundle_hash": bundle.canonical_hash,
        "asset_manifest_hash": manifest["canonical_hash"],
    }
    return manifest, canonical_hash(definition)


def create_dimension_calibration_run(
    db: Session,
    *,
    run_key: str,
    strategy_bundle_id: int,
    asset_ids: list[int],
    created_by: str,
) -> DimensionCalibrationRun:
    """Create or replay one immutable, isolated calibration definition."""
    if not _RUN_KEY.fullmatch(run_key):
        raise DimensionCalibrationContractError("校准运行键格式无效")
    if not created_by.strip() or len(created_by) > 80:
        raise DimensionCalibrationContractError("校准创建者格式无效")
    if not 1 <= len(asset_ids) <= 100:
        raise DimensionCalibrationContractError("校准资产数量必须为 1–100")
    if len(set(asset_ids)) != len(asset_ids):
        raise DimensionCalibrationContractError("校准资产 ID 不得重复")

    bundle = db.get(StrategyBundle, strategy_bundle_id)
    if bundle is None:
        raise DimensionCalibrationContractError("StrategyBundle 不存在")
    strategy_snapshot, _prompt_a, _sampling = (
        _validate_calibration_bundle(db, bundle)
    )
    assets = list(
        db.scalars(
            select(Asset)
            .where(Asset.id.in_(asset_ids))
            .order_by(Asset.id.asc())
        ).all()
    )
    if len(assets) != len(asset_ids):
        raise DimensionCalibrationContractError("校准资产不存在")
    invalid_assets = [
        asset.id
        for asset in assets
        if asset.status not in {"uploaded", "evaluated"}
    ]
    if invalid_assets:
        raise DimensionCalibrationContractError(
            "校准资产状态不可用："
            + "、".join(str(item) for item in invalid_assets)
        )
    asset_snapshots = [_asset_snapshot(asset) for asset in assets]
    manifest, definition_hash = _calibration_definition(
        bundle=bundle,
        asset_snapshots=asset_snapshots,
    )

    existing = db.scalar(
        select(DimensionCalibrationRun).where(
            DimensionCalibrationRun.run_key == run_key
        )
    )
    if existing is not None:
        if existing.definition_hash != definition_hash:
            raise DimensionCalibrationContractError(
                "同一校准运行键已绑定不同定义"
            )
        return existing

    run = DimensionCalibrationRun(
        run_key=run_key,
        strategy_bundle_id=bundle.id,
        strategy_bundle_hash=bundle.canonical_hash,
        strategy_snapshot_json=strategy_snapshot,
        asset_manifest_json=canonical_json(manifest),
        definition_hash=definition_hash,
        status="queued",
        total=len(assets),
        created_by=created_by.strip(),
    )
    db.add(run)
    db.flush()
    for asset, snapshot in zip(assets, asset_snapshots, strict=True):
        db.add(
            DimensionCalibrationItem(
                run_id=run.id,
                asset_id=asset.id,
                asset_snapshot_json=canonical_json(snapshot),
                status="queued",
            )
        )
    db.flush()
    return run


def _refresh_run_summary(
    db: Session,
    *,
    run_id: int,
    now: datetime,
) -> DimensionCalibrationRun:
    counts = {
        status: count
        for status, count in db.execute(
            select(
                DimensionCalibrationItem.status,
                func.count(DimensionCalibrationItem.id),
            )
            .where(DimensionCalibrationItem.run_id == run_id)
            .group_by(DimensionCalibrationItem.status)
        ).all()
    }
    run = db.get(DimensionCalibrationRun, run_id)
    if run is None:
        raise DimensionCalibrationStateError("校准运行不存在")
    run.processing = int(counts.get("processing", 0))
    run.completed = int(counts.get("completed", 0))
    run.core_fallback = int(counts.get("core_fallback", 0))
    run.blocked = int(counts.get("blocked", 0))
    run.unassessable = int(counts.get("unassessable", 0))
    run.failed = int(counts.get("failed", 0))
    terminal_count = sum(
        int(counts.get(status, 0))
        for status in CALIBRATION_ITEM_TERMINAL_STATUSES
    )
    if terminal_count == run.total:
        if run.failed == run.total:
            run.status = "failed"
        elif run.failed:
            run.status = "partial_failed"
        else:
            run.status = "completed"
        run.finished_at = now
    else:
        run.status = "running"
        run.finished_at = None
    db.flush()
    return run


def claim_dimension_calibration_item(
    db: Session,
    *,
    run_id: int,
    worker_id: str,
    now: datetime | None = None,
) -> DimensionCalibrationItem | None:
    """Atomically claim the oldest queued item from one calibration run."""
    if not _WORKER_ID.fullmatch(worker_id):
        raise DimensionCalibrationContractError("校准 Worker ID 格式无效")
    timestamp = _aware_timestamp(now)
    run = db.get(DimensionCalibrationRun, run_id)
    if run is None:
        raise DimensionCalibrationStateError("校准运行不存在")
    if run.status in {"completed", "partial_failed", "failed"}:
        return None
    item_id = db.scalar(
        select(DimensionCalibrationItem.id)
        .where(
            DimensionCalibrationItem.run_id == run_id,
            DimensionCalibrationItem.status == "queued",
        )
        .order_by(DimensionCalibrationItem.id.asc())
        .limit(1)
    )
    if item_id is None:
        return None
    claimed = db.execute(
        update(DimensionCalibrationItem)
        .where(
            DimensionCalibrationItem.id == item_id,
            DimensionCalibrationItem.status == "queued",
        )
        .values(
            status="processing",
            worker_id=worker_id,
            started_at=timestamp,
        )
    )
    if claimed.rowcount != 1:
        return None
    db.flush()
    _refresh_run_summary(db, run_id=run_id, now=timestamp)
    return db.get(DimensionCalibrationItem, item_id)


def _processing_item(
    db: Session,
    *,
    item_id: int,
    worker_id: str,
) -> DimensionCalibrationItem:
    item = db.get(DimensionCalibrationItem, item_id)
    if item is None:
        raise DimensionCalibrationStateError("校准项不存在")
    if item.status != "processing":
        raise DimensionCalibrationStateError("校准项不处于 processing")
    if item.worker_id != worker_id:
        raise DimensionCalibrationStateError("校准项 Worker 身份不一致")
    return item


def _validate_resolution_snapshot(
    db: Session,
    *,
    item: DimensionCalibrationItem,
    precheck: dict[str, Any],
    resolution_snapshot: dict[str, Any],
) -> str:
    run = item.run
    bundle = run.strategy_bundle
    strategy_snapshot, prompt_a, sampling = _unique_bundle_inputs(db, bundle)
    if strategy_snapshot != run.strategy_snapshot_json:
        raise DimensionCalibrationContractError("校准运行策略快照已损坏")
    timestamp_value = resolution_snapshot.get("resolution_timestamp")
    if not isinstance(timestamp_value, str):
        raise DimensionCalibrationContractError("解析快照缺少带时区时间")
    try:
        resolution_timestamp = datetime.fromisoformat(timestamp_value)
    except ValueError as exc:
        raise DimensionCalibrationContractError(
            "解析快照时间格式无效"
        ) from exc
    expected = build_routed_evaluation_strategy_snapshot(
        bundle=bundle,
        prompt_a=prompt_a,
        sampling_policy=sampling,
        precheck=precheck,
        resolution_timestamp=resolution_timestamp,
    )
    actual = canonical_json(resolution_snapshot)
    if actual != expected:
        raise DimensionCalibrationContractError(
            "解析快照不能由冻结 Bundle 与 A 输出重放"
        )
    return actual


def _bounded_raw(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    if len(value) > _MAX_RAW_RESPONSE_CHARS:
        raise DimensionCalibrationContractError(f"{label} 超过安全长度")
    return value


def complete_dimension_calibration_item(
    db: Session,
    *,
    item_id: int,
    worker_id: str,
    terminal_status: str,
    precheck: dict[str, Any],
    resolution_snapshot: str | dict[str, Any],
    aesthetic: dict[str, Any] | None = None,
    raw_response_a: str | None = None,
    raw_response_b: str | None = None,
    now: datetime | None = None,
) -> DimensionCalibrationItem:
    """Persist one isolated business outcome without touching official results."""
    if terminal_status not in CALIBRATION_BUSINESS_STATUSES:
        raise DimensionCalibrationContractError("校准业务终态无效")
    if not isinstance(precheck, dict):
        raise DimensionCalibrationContractError("A 预检必须是对象")
    timestamp = _aware_timestamp(now)
    item = _processing_item(
        db,
        item_id=item_id,
        worker_id=worker_id,
    )
    resolution = _json_object(
        resolution_snapshot,
        label="解析快照",
    )
    resolution_json = _validate_resolution_snapshot(
        db,
        item=item,
        precheck=precheck,
        resolution_snapshot=resolution,
    )
    resolution_status = resolution.get("resolution_status")
    expected_resolution_status = (
        "resolved" if terminal_status == "completed" else terminal_status
    )
    if resolution_status != expected_resolution_status:
        raise DimensionCalibrationContractError(
            "校准终态与 A 后解析状态不一致"
        )

    precheck_json = canonical_json(precheck)
    bounded_raw_a = _bounded_raw(
        raw_response_a,
        label="A 原始响应",
    )
    bounded_raw_b = _bounded_raw(
        raw_response_b,
        label="B 原始响应",
    )
    aesthetic_json: str | None
    scoring_json: str | None
    score: float | None
    level: str | None
    confidence: float | None
    needs_review: bool
    if terminal_status == "completed":
        if not isinstance(aesthetic, dict):
            raise DimensionCalibrationContractError(
                "resolved 校准结果必须包含 B 美感输出"
            )
        dimension_definition = resolution.get(
            "resolved_dimensions_snapshot"
        )
        if not isinstance(dimension_definition, dict):
            raise DimensionCalibrationContractError(
                "resolved 校准结果缺少冻结维度定义"
            )
        scoring = calculate_score(
            precheck,
            aesthetic,
            dimension_schema=dimension_definition,
        )
        if (
            scoring.get("formal") is not True
            or scoring.get("score") is None
            or scoring.get("level") is None
            or scoring.get("confidence") is None
        ):
            raise DimensionCalibrationContractError(
                "resolved 校准结果未形成完整正式评分"
            )
        aesthetic_json = canonical_json(aesthetic)
        scoring_json = canonical_json(scoring)
        score = float(scoring["score"])
        level = str(scoring["level"])
        confidence = float(scoring["confidence"])
        needs_review = bool(
            scoring.get("needs_review")
            or resolution.get("needs_review")
        )
    else:
        if aesthetic is not None or raw_response_b is not None:
            raise DimensionCalibrationContractError(
                "非 resolved 校准结果不得携带 B 输出"
            )
        aesthetic_json = None
        scoring_json = None
        score = None
        level = None
        confidence = None
        needs_review = True

    # All contract checks must finish before the ORM object is mutated.  A
    # rejected result must leave the claimed item reusable in the same
    # transaction instead of requiring a rollback that also loses the claim.
    item.status = terminal_status
    item.resolution_snapshot_json = resolution_json
    item.precheck_json = precheck_json
    item.aesthetic_json = aesthetic_json
    item.scoring_json = scoring_json
    item.raw_response_a = bounded_raw_a
    item.raw_response_b = bounded_raw_b
    item.score = score
    item.level = level
    item.confidence = confidence
    item.needs_review = needs_review
    item.error_type = None
    item.error_message = ""
    item.finished_at = timestamp
    db.flush()
    _refresh_run_summary(db, run_id=item.run_id, now=timestamp)
    return item


def fail_dimension_calibration_item(
    db: Session,
    *,
    item_id: int,
    worker_id: str,
    error_type: str,
    error_message: str,
    now: datetime | None = None,
) -> DimensionCalibrationItem:
    """Persist a bounded failure without exception objects or stack traces."""
    if not _ERROR_TYPE.fullmatch(error_type):
        raise DimensionCalibrationContractError("校准错误类型格式无效")
    message = error_message.strip()
    if not message:
        raise DimensionCalibrationContractError("校准错误信息不能为空")
    timestamp = _aware_timestamp(now)
    item = _processing_item(
        db,
        item_id=item_id,
        worker_id=worker_id,
    )
    item.status = "failed"
    item.resolution_snapshot_json = None
    item.precheck_json = None
    item.aesthetic_json = None
    item.scoring_json = None
    item.raw_response_a = None
    item.raw_response_b = None
    item.score = None
    item.level = None
    item.confidence = None
    item.needs_review = True
    item.error_type = error_type
    item.error_message = message[:500]
    item.finished_at = timestamp
    db.flush()
    _refresh_run_summary(db, run_id=item.run_id, now=timestamp)
    return item
