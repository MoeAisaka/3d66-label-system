"""Validation and normalization for the unified Call-B aesthetic foundation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


FOUNDATION_SCHEMA_VERSION = "b-aesthetic-foundation-v1"


class BAestheticFoundationError(ValueError):
    """Raised when Call-B cannot provide a safe starting score."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _evidence(payload: Mapping[str, Any]) -> list[str]:
    overall = payload.get("overall_evidence") or payload.get("aesthetic_evidence")
    if isinstance(overall, list):
        values = [item.strip() for item in overall if isinstance(item, str) and item.strip()]
        if values:
            return values
    dimensions = payload.get("dimensions")
    if isinstance(dimensions, Mapping):
        values: list[str] = []
        for item in dimensions.values():
            if not isinstance(item, Mapping):
                continue
            raw = item.get("evidence")
            if isinstance(raw, list):
                values.extend(
                    value.strip()
                    for value in raw
                    if isinstance(value, str) and value.strip()
                )
        if values:
            return values
    return []


def normalize_b_aesthetic_foundation(payload: Any) -> dict[str, Any]:
    """Return a stable foundation payload or fail closed with a coded error."""
    if not isinstance(payload, Mapping):
        raise BAestheticFoundationError(
            "aesthetic_output_invalid", "调用B美感结果必须是 JSON 对象"
        )
    score = payload.get("aesthetic_score")
    # 输出契约示例把分值写成描述式占位（防照抄）后，个别模型会把整数写成
    # 字符串（"75"）。数字字符串无歧义，宽容转换；非数字仍按缺失拒绝。
    if isinstance(score, str) and score.strip().isdigit():
        score = int(score.strip())
    if isinstance(score, bool) or not isinstance(score, int):
        raise BAestheticFoundationError(
            "aesthetic_score_missing", "调用B必须返回 0-100 的 aesthetic_score 整数"
        )
    if not 0 <= score <= 100:
        raise BAestheticFoundationError(
            "aesthetic_score_invalid", "调用B aesthetic_score 必须在 0-100 之间"
        )
    evidence = _evidence(payload)
    if not evidence:
        raise BAestheticFoundationError(
            "aesthetic_evidence_missing", "调用B美感分必须包含非空可见证据"
        )
    confidence = payload.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        raise BAestheticFoundationError(
            "aesthetic_confidence_invalid", "调用B confidence 必须在 0-1 之间"
        )
    return {
        "schema_version": FOUNDATION_SCHEMA_VERSION,
        "aesthetic_score": score,
        "evidence": evidence,
        "confidence": float(confidence) if confidence is not None else None,
        "dimensions": payload.get("dimensions") if isinstance(payload.get("dimensions"), Mapping) else {},
    }
