from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .models import (
    Asset,
    ContentIngressEvent,
    ContentRecord,
    EvaluationCategoryProfile,
    EvaluationResult,
    HumanReview,
    LabelOutboxEvent,
    LabelRelease,
    PublishedLabel,
    ReviewPanel,
)


SCHEMA_VERSION = "content-ingress-v1"
LABEL_SCHEMA_VERSION = "published-label-v1"
INGRESS_TYPES = {"content.created", "content.updated", "content.deleted"}


class LabelIntegrationConflict(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def ingest_content_event(
    db: Session,
    *,
    event_id: str,
    schema_version: str,
    event_type: str,
    source_system: str,
    occurred_at: datetime,
    payload: dict[str, Any],
    received_by: str,
) -> tuple[ContentIngressEvent, ContentRecord, bool]:
    if schema_version != SCHEMA_VERSION:
        raise ValueError("不支持的内容接入 schema_version")
    if event_type not in INGRESS_TYPES:
        raise ValueError("不支持的内容接入 event_type")
    content_id = str(payload.get("content_id", "")).strip()
    category_key = str(payload.get("category_key", "")).strip()
    source_version = str(payload.get("content_version", "")).strip()
    if not content_id or len(content_id) > 160:
        raise ValueError("payload.content_id 必须填写且长度不超过 160")
    if not category_key or len(category_key) > 40:
        raise ValueError("payload.category_key 必须填写且长度不超过 40")
    if not source_version or len(source_version) > 120:
        raise ValueError("payload.content_version 必须填写且长度不超过 120")
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == category_key,
            EvaluationCategoryProfile.status == "active",
        )
    )
    if profile is None:
        raise ValueError("payload.category_key 不是启用中的评测类目")
    asset_id = payload.get("asset_id")
    asset: Asset | None = None
    if asset_id is not None:
        if not isinstance(asset_id, int) or asset_id < 1:
            raise ValueError("payload.asset_id 必须为正整数")
        asset = db.get(Asset, asset_id)
        if asset is None:
            raise ValueError("payload.asset_id 对应素材不存在")
        if asset.category_key != category_key:
            raise ValueError("payload.asset_id 与 category_key 不一致")

    payload_hash = _payload_hash(payload)
    existing_event = db.scalar(
        select(ContentIngressEvent).where(ContentIngressEvent.event_id == event_id)
    )
    if existing_event is not None:
        if (
            existing_event.payload_hash != payload_hash
            or existing_event.schema_version != schema_version
            or existing_event.event_type != event_type
            or existing_event.source_system != source_system
            or _aware(existing_event.occurred_at) != _aware(occurred_at)
        ):
            raise LabelIntegrationConflict("同一 event_id 的内容接入载荷不一致")
        record = db.get(ContentRecord, existing_event.content_record_id)
        if record is None:
            raise RuntimeError("内容接入事件缺少本地内容投影")
        return existing_event, record, True

    record = db.scalar(
        select(ContentRecord).where(
            ContentRecord.source_system == source_system,
            ContentRecord.source_content_id == content_id,
        )
    )
    incoming_time = _aware(occurred_at)
    status = "deleted" if event_type == "content.deleted" else (
        "ready" if asset is not None or (record is not None and record.asset_id is not None)
        else "awaiting_material"
    )
    event_status = status if status == "awaiting_material" else "applied"
    if record is None:
        record = ContentRecord(
            source_system=source_system,
            source_content_id=content_id,
            category_key=category_key,
            source_version=source_version,
            source_occurred_at=occurred_at,
            asset_id=asset.id if asset else None,
            status=status,
        )
        db.add(record)
        db.flush()
    elif incoming_time <= _aware(record.source_occurred_at):
        event_status = "stale"
    else:
        record.category_key = category_key
        record.source_version = source_version
        record.source_occurred_at = occurred_at
        if asset is not None:
            record.asset_id = asset.id
        record.status = status
        record.updated_at = datetime.now(timezone.utc)

    event = ContentIngressEvent(
        event_id=event_id,
        schema_version=schema_version,
        event_type=event_type,
        source_system=source_system,
        occurred_at=occurred_at,
        payload_hash=payload_hash,
        payload_json=canonical_json(payload),
        content_record_id=record.id,
        status=event_status,
        received_by=received_by,
    )
    db.add(event)
    db.flush()
    append_audit_event(
        db,
        category="content_ingress",
        action=event_status,
        subject_type="content_ingress_event",
        subject_id=event_id,
        actor=received_by,
        payload={
            "content_key": f"{source_system}:{content_id}",
            "category_key": category_key,
            "event_type": event_type,
            "source_version": source_version,
            "status": event_status,
        },
        event_key=f"content-ingress:{event_id}",
    )
    return event, record, False


def _content_key(db: Session, evaluation: EvaluationResult, requested: str | None) -> str:
    if requested:
        value = requested.strip()
        if ":" not in value or len(value) > 320:
            raise ValueError("content_key 必须使用 source_system:content_id 格式")
        if value == f"asset:{evaluation.asset_id}":
            return value
        if value.startswith("asset:"):
            raise ValueError("asset content_key 与待发布评测的素材不一致")
        source_system, source_content_id = value.split(":", 1)
        record = db.scalar(
            select(ContentRecord).where(
                ContentRecord.source_system == source_system,
                ContentRecord.source_content_id == source_content_id,
            )
        )
        if record is None or record.status != "ready":
            raise ValueError("content_key 尚未接入完成本地素材绑定")
        if record.asset_id != evaluation.asset_id or record.category_key != evaluation.job.category_key:
            raise ValueError("content_key 与待发布评测的素材或类目不一致")
        return value
    record = db.scalar(select(ContentRecord).where(ContentRecord.asset_id == evaluation.asset_id))
    return (
        f"{record.source_system}:{record.source_content_id}"
        if record is not None
        else f"asset:{evaluation.asset_id}"
    )


def build_label_snapshot(
    db: Session,
    *,
    evaluation_id: int,
    content_key: str | None,
) -> tuple[str, int, int, dict[str, Any]]:
    evaluation = db.get(EvaluationResult, evaluation_id)
    if evaluation is None:
        raise ValueError("评测结果不存在")
    panel = db.scalar(select(ReviewPanel).where(ReviewPanel.evaluation_id == evaluation.id))
    if panel is None or panel.status != "completed" or panel.final_review_id is None:
        raise ValueError("评测尚未完成初审，不能形成正式标签")
    final_review = db.get(HumanReview, panel.final_review_id)
    if final_review is None or final_review.decision not in {"approved", "corrected"}:
        raise ValueError("人工真值不是可发布状态")
    if evaluation.job.category_key != evaluation.asset.category_key:
        raise ValueError("评测类目与素材类目不一致")
    precheck = json.loads(evaluation.precheck_json or "{}")
    aesthetic = json.loads(evaluation.aesthetic_json or "{}")
    truth = json.loads(panel.final_truth_json or "{}")
    final_level = truth.get("corrected_level") or evaluation.level
    final_score = truth.get("corrected_score")
    if final_score is None:
        final_score = evaluation.score
    if not final_level or final_score is None:
        raise ValueError("人工真值缺少正式等级或服务端分数")
    payload = {
        "schema_version": LABEL_SCHEMA_VERSION,
        "content_key": _content_key(db, evaluation, content_key),
        "category_key": evaluation.job.category_key,
        "level": final_level,
        "score": final_score,
        "classification": precheck.get("classification", {}),
        "dimensions": truth.get("dimensions") or aesthetic.get("dimensions", {}),
        "key_fields": truth.get("key_fields", {}),
        "provenance": {
            "evaluation_id": evaluation.id,
            "job_id": evaluation.job_id,
            "final_review_id": final_review.id,
            "strategy_bundle_id": evaluation.strategy_bundle_id,
            "model_id": evaluation.model_id,
            "prompt_a_version": evaluation.prompt_a_version,
            "prompt_b_version": evaluation.prompt_b_version,
            "rubric_version": evaluation.rubric_version,
            "engine_version": evaluation.engine_version,
        },
    }
    return payload["content_key"], evaluation.id, final_review.id, payload


def create_release(
    db: Session,
    *,
    release_key: str,
    evaluation_id: int,
    content_key: str | None,
    requested_by: str,
) -> tuple[LabelRelease, bool]:
    existing = db.scalar(select(LabelRelease).where(LabelRelease.release_key == release_key))
    if existing is not None:
        if (
            existing.evaluation_id != evaluation_id
            or (content_key is not None and existing.content_key != content_key)
        ):
            raise LabelIntegrationConflict("同一 release_key 的发布请求不一致")
        return existing, True
    key, eval_id, review_id, payload = build_label_snapshot(
        db, evaluation_id=evaluation_id, content_key=content_key
    )
    raw = canonical_json(payload)
    release = LabelRelease(
        release_key=release_key,
        content_key=key,
        category_key=str(payload["category_key"]),
        evaluation_id=eval_id,
        final_review_id=review_id,
        label_schema_version=LABEL_SCHEMA_VERSION,
        label_payload_json=raw,
        payload_hash=_payload_hash(payload),
        status="pending_review",
        requested_by=requested_by,
    )
    db.add(release)
    db.flush()
    append_audit_event(
        db,
        category="label_release",
        action="requested",
        subject_type="label_release",
        subject_id=release.id,
        actor=requested_by,
        payload={"release_key": release_key, "content_key": key, "evaluation_id": eval_id},
        event_key=f"label-release:requested:{release_key}",
    )
    return release, False


def publish_release(db: Session, *, release: LabelRelease, actor: str) -> tuple[PublishedLabel, bool]:
    if release.status == "published":
        published = db.scalar(select(PublishedLabel).where(PublishedLabel.release_id == release.id))
        if published is None:
            raise RuntimeError("已发布 release 缺少发布标签")
        return published, True
    if release.status not in {"pending_review", "approved"}:
        raise ValueError("当前发布版本不在可审批状态")
    if release.evaluation_id is not None:
        build_label_snapshot(db, evaluation_id=release.evaluation_id, content_key=release.content_key)
    now = datetime.now(timezone.utc)
    release.status = "published"
    release.approved_by = actor
    release.approved_at = now
    release.published_at = now
    current = db.scalar(
        select(PublishedLabel).where(
            PublishedLabel.content_key == release.content_key,
            PublishedLabel.status == "published",
        ).order_by(PublishedLabel.version.desc(), PublishedLabel.id.desc())
    )
    version = (current.version + 1) if current else 1
    if current is not None:
        current.status = "superseded"
        current.superseded_at = now
    label = PublishedLabel(
        release_id=release.id,
        content_key=release.content_key,
        category_key=release.category_key,
        version=version,
        label_schema_version=release.label_schema_version,
        label_payload_json=release.label_payload_json,
        payload_hash=release.payload_hash,
        status="published",
        published_at=now,
    )
    db.add(label)
    db.flush()
    payload = {
        "schema_version": "label-change-event-v1",
        "operation": "rolled_back" if release.source_release_id else "published",
        "content_key": release.content_key,
        "version": version,
        "label": json.loads(label.label_payload_json),
        "release_id": release.id,
        "payload_hash": label.payload_hash,
    }
    operation = payload["operation"]
    outbox = LabelOutboxEvent(
        event_id=f"label-release:{release.id}:{operation}",
        release_id=release.id,
        published_label_id=label.id,
        content_key=release.content_key,
        operation=operation,
        payload_hash=_payload_hash(payload),
        payload_json=canonical_json(payload),
    )
    db.add(outbox)
    append_audit_event(
        db,
        category="label_release",
        action=operation,
        subject_type="label_release",
        subject_id=release.id,
        actor=actor,
        payload={"content_key": release.content_key, "version": version, "outbox_event_id": outbox.event_id},
        event_key=f"label-release:{release.id}:{operation}",
    )
    return label, False


def rollback_release(
    db: Session,
    *,
    target: PublishedLabel,
    rollback_key: str,
    actor: str,
) -> tuple[LabelRelease, PublishedLabel, bool]:
    existing = db.scalar(select(LabelRelease).where(LabelRelease.release_key == rollback_key))
    if existing is not None:
        if existing.source_release_id != target.release_id:
            raise LabelIntegrationConflict("同一 rollback_key 的回滚目标不一致")
        published = db.scalar(select(PublishedLabel).where(PublishedLabel.release_id == existing.id))
        if published is None:
            raise RuntimeError("回滚 release 缺少发布标签")
        return existing, published, True
    if target.status == "published":
        raise LabelIntegrationConflict("目标标签已是当前生效版本，无需回滚")
    release = LabelRelease(
        release_key=rollback_key,
        content_key=target.content_key,
        category_key=target.category_key,
        source_release_id=target.release_id,
        label_schema_version=target.label_schema_version,
        label_payload_json=target.label_payload_json,
        payload_hash=target.payload_hash,
        status="approved",
        requested_by=actor,
        approved_by=actor,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    published, _ = publish_release(db, release=release, actor=actor)
    return release, published, False


def release_payload(release: LabelRelease, published: PublishedLabel | None = None) -> dict[str, Any]:
    return {
        "id": release.id,
        "release_key": release.release_key,
        "content_key": release.content_key,
        "category_key": release.category_key,
        "evaluation_id": release.evaluation_id,
        "final_review_id": release.final_review_id,
        "source_release_id": release.source_release_id,
        "status": release.status,
        "label_schema_version": release.label_schema_version,
        "payload_hash": release.payload_hash,
        "label": json.loads(release.label_payload_json),
        "requested_by": release.requested_by,
        "requested_at": release.requested_at,
        "approved_by": release.approved_by,
        "approved_at": release.approved_at,
        "published_at": release.published_at,
        "published_label_id": published.id if published else None,
        "published_version": published.version if published else None,
        "is_current": published.status == "published" if published else None,
    }
