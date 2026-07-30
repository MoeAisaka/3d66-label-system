from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .models import OptimizationCaseQueue, ProductionFeedbackEvent


SUPPORTED_SCHEMA_VERSION = "production-feedback-v1"
SUPPORTED_EVENT_TYPE = "human_correction_finalized"


class FeedbackConflict(ValueError):
    pass


def ingest_production_feedback(
    db: Session,
    *,
    event_id: str,
    schema_version: str,
    event_type: str,
    source_system: str,
    occurred_at: datetime,
    payload: dict[str, Any],
    received_by: str,
) -> tuple[ProductionFeedbackEvent, OptimizationCaseQueue, bool]:
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError("不支持的生产反馈 schema_version")
    if event_type != SUPPORTED_EVENT_TYPE:
        raise ValueError("不支持的生产反馈 event_type")
    required = {
        "production_case_id",
        "prompt_version",
        "severity",
        "model_output",
        "human_truth",
    }
    if not required.issubset(payload):
        raise ValueError("生产反馈 payload 缺少必填字段")
    if payload["severity"] not in {"P0", "P1", "P2", "P3"}:
        raise ValueError("生产反馈 severity 非法")
    if not isinstance(payload["model_output"], dict) or not isinstance(
        payload["human_truth"], dict
    ):
        raise ValueError("生产反馈模型结果与人工真值必须为对象")
    prompt_version = str(payload["prompt_version"]).strip()
    if not prompt_version or len(prompt_version) > 40:
        raise ValueError("生产反馈 prompt_version 非法")

    payload_json = canonical_json(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    existing = db.scalar(
        select(ProductionFeedbackEvent).where(
            ProductionFeedbackEvent.event_id == event_id
        )
    )
    if existing is not None:
        existing_occurred = existing.occurred_at
        if existing_occurred.tzinfo is None:
            existing_occurred = existing_occurred.replace(tzinfo=timezone.utc)
        incoming_occurred = occurred_at
        if incoming_occurred.tzinfo is None:
            incoming_occurred = incoming_occurred.replace(tzinfo=timezone.utc)
        if (
            existing.payload_hash != payload_hash
            or existing.schema_version != schema_version
            or existing.event_type != event_type
            or existing.source_system != source_system
            or existing_occurred != incoming_occurred
        ):
            raise FeedbackConflict("同一 event_id 的事件载荷不一致")
        existing_case = db.scalar(
            select(OptimizationCaseQueue).where(
                OptimizationCaseQueue.source_event_id == existing.id
            )
        )
        if existing_case is None:
            raise RuntimeError("生产反馈事件缺少优化队列映射")
        return existing, existing_case, True

    event = ProductionFeedbackEvent(
        event_id=event_id,
        schema_version=schema_version,
        event_type=event_type,
        source_system=source_system,
        occurred_at=occurred_at,
        payload_hash=payload_hash,
        payload_json=payload_json,
        status="mapped",
        received_by=received_by,
    )
    db.add(event)
    db.flush()
    case = OptimizationCaseQueue(
        idempotency_key=f"production:{event_id}",
        evaluation_id=None,
        final_review_id=None,
        source_type="production_feedback",
        source_event_id=event.id,
        prompt_version=prompt_version,
        severity=str(payload["severity"]),
        case_json=canonical_json(
            {
                "schema_version": "optimization-case-v1",
                "source": "production_feedback",
                "source_event_id": event_id,
                "production_case_id": payload["production_case_id"],
                "model_output": payload["model_output"],
                "human_truth": payload["human_truth"],
                "reason_codes": payload.get("reason_codes", []),
                "production_applied": bool(
                    payload.get("production_applied", False)
                ),
                "writes_production_database": False,
            }
        ),
        status="pending",
    )
    db.add(case)
    db.flush()
    append_audit_event(
        db,
        category="production_feedback",
        action="accepted",
        subject_type="production_feedback_event",
        subject_id=event.event_id,
        actor=received_by,
        payload={
            "payload_hash": payload_hash,
            "optimization_case_id": case.id,
            "writes_production_database": False,
        },
        event_key=f"production-feedback:{event_id}",
    )
    return event, case, False
