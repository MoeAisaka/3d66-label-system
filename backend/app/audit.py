from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def append_audit_event(
    db: Session,
    *,
    category: str,
    action: str,
    subject_type: str,
    subject_id: str | int,
    actor: str,
    payload: dict[str, Any],
    event_key: str | None = None,
) -> AuditEvent:
    payload_json = canonical_json(payload)
    key = event_key or hashlib.sha256(
        (
            f"{category}:{action}:{subject_type}:{subject_id}:"
            f"{actor}:{payload_json}"
        ).encode("utf-8")
    ).hexdigest()
    existing = db.query(AuditEvent).filter_by(event_key=key).one_or_none()
    if existing is not None:
        return existing
    event = AuditEvent(
        event_key=key,
        category=category,
        action=action,
        subject_type=subject_type,
        subject_id=str(subject_id),
        actor=actor,
        payload_json=payload_json,
    )
    db.add(event)
    db.flush()
    return event
