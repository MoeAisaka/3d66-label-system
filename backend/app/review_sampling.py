from __future__ import annotations

import hashlib
import json
from typing import Any


SMART_SAMPLING_VERSION = "smart-sampling-v1.1"
DEFAULT_SAMPLE_RATE = 10

DIMENSION_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)

LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}
QUALITY_LABELS = {
    "normal": "画质正常",
    "slight": "轻微画质问题",
    "moderate": "中度画质问题",
    "severe": "严重画质问题",
    "unusable": "画质不可用",
    "uncertain": "画质待确认",
}


def _json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _sample_hit(result: Any, sample_rate: int) -> bool:
    key = ":".join(
        (
            str(result.id),
            str(result.model_id),
            str(result.prompt_a_version),
            str(result.prompt_b_version or ""),
        )
    )
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < sample_rate


def build_review_sampling(
    result: Any,
    *,
    is_golden: bool = False,
    previous_level: str | None = None,
    combination_index: int | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    low_confidence_threshold: float = 0.7,
    medium_confidence_threshold: float = 0.9,
    cold_start_required_count: int = 5,
    high_level_required_from: int = 4,
    policy_version: str | None = None,
) -> dict[str, Any]:
    """Return a stable and explainable review recommendation for one evaluation result."""
    latest_review = result.reviews[-1] if getattr(result, "reviews", None) else None
    if latest_review and latest_review.decision in {"approved", "corrected"}:
        return {
            "version": policy_version or SMART_SAMPLING_VERSION,
            "tier": "reviewed",
            "priority": 0,
            "sample_rate": sample_rate,
            "reasons": [{"code": "human_reviewed", "label": "已完成人工审核"}],
        }

    precheck = _json_object(getattr(result, "precheck_json", None))
    aesthetic = _json_object(getattr(result, "aesthetic_json", None))
    risk_review = _json_object(getattr(result, "risk_review_json", None))
    classification = precheck.get("classification") or {}
    media = precheck.get("media_form") or {}
    quality = precheck.get("image_quality") or {}
    dimensions = aesthetic.get("dimensions") or {}
    scope_status = classification.get("scope_status")
    quality_severity = str(quality.get("quality_severity") or "uncertain")

    required: list[dict[str, str]] = []
    sampled: list[dict[str, str]] = []
    required_priority = 0
    sampled_priority = 0

    def require(code: str, label: str, priority: int) -> None:
        nonlocal required_priority
        if not any(item["code"] == code for item in required):
            required.append({"code": code, "label": label})
        required_priority = max(required_priority, priority)

    def sample(code: str, label: str, priority: int) -> None:
        nonlocal sampled_priority
        if not any(item["code"] == code for item in sampled):
            sampled.append({"code": code, "label": label})
        sampled_priority = max(sampled_priority, priority)

    if latest_review and latest_review.decision == "rejected":
        require("human_rejected", "人工已退回，等待重新确认", 100)
    if is_golden:
        require("golden_sample", "属于已锁定黄金样本", 100)
    if getattr(result, "needs_review", False):
        require("model_needs_review", "模型标记需要人工复核", 88)
    low_percent = round(low_confidence_threshold * 100)
    medium_percent = round(medium_confidence_threshold * 100)
    if getattr(result, "confidence", None) is None or result.confidence < low_confidence_threshold:
        require("low_confidence", f"模型置信度低于{low_percent}%或缺失", 90)
    elif result.confidence < medium_confidence_threshold:
        sample("medium_confidence", f"模型置信度低于{medium_percent}%", 55)

    result_level_rank = LEVEL_RANK.get(getattr(result, "level", None), 0)
    if result_level_rank >= high_level_required_from:
        require("high_level", f"模型给出高等级{result.level}", 82)

    professional = media.get("professional_photography") or {}
    if professional.get("status") == "yes":
        require("professional_photography", "模型判定为专业摄影", 84)

    if quality_severity in {"severe", "unusable", "uncertain"}:
        require(
            "quality_high_risk",
            f"画质结论为{QUALITY_LABELS.get(quality_severity, quality_severity)}",
            92,
        )
    elif quality_severity == "moderate":
        sample("quality_moderate", "存在中度画质问题", 62)

    if risk_review.get("verdict") in {"uncertain", "error"}:
        require("risk_review_uncertain", "高风险自动复核未形成稳定结论", 96)
    elif risk_review.get("verdict") == "downgrade":
        require("risk_review_downgrade", "高风险自动复核修改了模型结论", 86)

    if previous_level in LEVEL_RANK and getattr(result, "level", None) in LEVEL_RANK:
        gap = abs(LEVEL_RANK[result.level] - LEVEL_RANK[previous_level])
        if gap >= 2:
            require("version_disagreement", f"与同素材上一结果相差{gap}个等级", 94)
        elif gap == 1:
            sample("version_shift", "与同素材上一结果相差1个等级", 60)

    if combination_index is not None and combination_index <= cold_start_required_count:
        require("new_combination", f"模型与提示词组合的第{combination_index}条结果", 80)

    grades = [
        int((dimensions.get(key) or {}).get("grade") or 0)
        for key in DIMENSION_KEYS
    ]
    valid_grades = [grade for grade in grades if 1 <= grade <= 5]
    if scope_status != "out_of_scope" and len(valid_grades) < len(DIMENSION_KEYS):
        require("incomplete_dimensions", "八个美感维度结果不完整", 98)
    elif len(valid_grades) == len(DIMENSION_KEYS) and len(set(valid_grades)) == 1:
        require("grade_collapse", "八个维度完全同分", 90)

    if scope_status == "boundary":
        sample("scope_boundary", "素材范围判定处于边界", 58)
    elif scope_status == "out_of_scope":
        sample("scope_out", "抽查范围外判断是否正确", 42)

    if required:
        return {
            "version": policy_version or SMART_SAMPLING_VERSION,
            "tier": "required",
            "priority": min(100, required_priority + min(8, len(required) - 1)),
            "sample_rate": sample_rate,
            "reasons": required + sampled,
        }

    if _sample_hit(result, sample_rate):
        sample("stable_random_sample", f"稳定随机抽中{sample_rate}%常规结果", 45)

    if sampled:
        return {
            "version": policy_version or SMART_SAMPLING_VERSION,
            "tier": "sampled",
            "priority": min(79, sampled_priority + min(8, len(sampled) - 1)),
            "sample_rate": sample_rate,
            "reasons": sampled,
        }

    return {
        "version": policy_version or SMART_SAMPLING_VERSION,
        "tier": "deferred",
        "priority": 20,
        "sample_rate": sample_rate,
        "reasons": [{"code": "stable_low_risk", "label": "当前未发现高风险信号"}],
    }
