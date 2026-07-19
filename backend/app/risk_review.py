from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


RISK_REVIEW_VERSION = "risk-review-v1.1"

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

QUALITY_RANK = {
    "normal": 0,
    "slight": 1,
    "uncertain": 1,
    "moderate": 2,
    "severe": 3,
    "unusable": 4,
}

CAP_RANK = {"none": 6, "L5": 5, "L4": 4, "L3": 3, "L2": 2, "L1": 1}

RISK_REVIEW_SYSTEM_PROMPT = """你是3D66图片评测的保守复核员。初评经常把普通、整洁、对称、清楚误判为专业摄影和3至5级。忽略初评的褒义措辞，先独立看原图，再寻找反证。

判定规则：
1. 专业摄影是稀有结论。必须同时确认：非日常站立视角的明确机位设计；垂直线和边缘裁切受控；光线对主体或材质有主动塑造而非仅照亮；高光暗部和白平衡受控；主体组织与后期具有发布级完成度。任一项不明确即输出no或uncertain。居中、对称、整洁、清楚、通道透视、自然光和射灯存在都不是专业摄影证据。
2. 能说明空间但摄影表达普通，输出documentary_record=yes。普通站立视角、平直记录、弱光影塑造、远端遮挡、陈列机械、画面偏平或偏黄，均支持现场记录。
3. 画质normal指接近可发布的技术质量，不是“能看清”。只要存在偏色、灰雾、低微反差、高光泛白、暗部堵塞、软焦、压缩、远近清晰度不一致、遮挡或异常裁切，至少slight；两项或大面积问题至少moderate。
4. 八维基准不是3级而是2级：普通、常见、能用、关系基本成立、缺少摄影表达或设计新意，均为2级。3级必须在该维度明显高于普通图库素材，并有一条可定位、非通用的优势。4级至少两条，5级至少三条且具有代表性。
5. 不得八维同分。逐项判断：普通正面记录可使构图或空间关系达到3，但平光、常规配色、普通陈列、有限细节、陈旧趋势和一般呈现仍应为2。
6. 中心透视、色调统一、整齐陈列、简约、低饱和、原木、通道清晰、轨道灯等常见做法最多证明基础成立，不能单独把任何维度从2升到3。
7. 普通现场记录最高L3；若至少四个维度为2，或画质moderate，等级上限应为L2。
8. 你只能保持或降低原结论，不能提高任何等级或改善画质结论。risk_reasons必须写原图中可定位的缺陷，不能只复述规则。

只输出JSON：
{
  "verdict": "keep|downgrade|uncertain",
  "risk_reasons": [],
  "professional_photography": "yes|no|uncertain",
  "documentary_record": "yes|no|uncertain",
  "quality_severity": "normal|slight|moderate|severe|unusable|uncertain",
  "dimension_grades": {
    "composition_viewpoint": 1,
    "lighting_atmosphere": 1,
    "color_material": 1,
    "spatial_design_furnishing": 1,
    "visual_hierarchy": 1,
    "detail_completion": 1,
    "inspiration_reference": 1,
    "presentation_integrity": 1
  },
  "level_cap": "none|L4|L3|L2|L1",
  "confidence": 0.0
}"""


def risk_review_reasons(
    precheck: dict[str, Any], aesthetic: dict[str, Any] | None, scoring: dict[str, Any]
) -> list[str]:
    if not aesthetic:
        return []
    reasons: list[str] = []
    media = precheck.get("media_form") or {}
    professional = media.get("professional_photography") or {}
    if professional.get("status") == "yes":
        reasons.append("模型判定为专业摄影")
    level = scoring.get("level")
    if level in {"L4", "L5"}:
        reasons.append(f"初评分达到{level}")
    dimensions = aesthetic.get("dimensions") or {}
    grade_fives = sum(
        1 for key in DIMENSION_KEYS if (dimensions.get(key) or {}).get("grade") == 5
    )
    if grade_fives:
        reasons.append(f"存在{grade_fives}个5级维度")
    return reasons


def build_risk_review_user_prompt(
    precheck: dict[str, Any], aesthetic: dict[str, Any], scoring: dict[str, Any]
) -> str:
    media = precheck.get("media_form") or {}
    compact_media = {
        key: {
            "status": (value or {}).get("status"),
            "evidence": (value or {}).get("evidence") or [],
        }
        for key, value in media.items()
        if key
        in {
            "real_photo",
            "rendering",
            "ai_generated",
            "professional_photography",
            "documentary_record",
            "casual_snapshot",
        }
        and isinstance(value, dict)
    }
    dimensions = {}
    for key in DIMENSION_KEYS:
        value = (aesthetic.get("dimensions") or {}).get(key) or {}
        dimensions[key] = {
            "grade": value.get("grade"),
            "evidence": value.get("evidence") or [],
            "defects": value.get("defects") or [],
        }
    payload = {
        "classification": precheck.get("classification") or {},
        "scene_scope": precheck.get("scene_scope") or {},
        "media_form": compact_media,
        "image_quality": precheck.get("image_quality") or {},
        "dimensions": dimensions,
        "initial_level": scoring.get("level"),
        "initial_score": scoring.get("score"),
    }
    return (
        "复核所附原图与以下初评。重点寻找不能支持高分的可见反证。"
        "不得因为初评写了证据就默认其真实，只输出规定JSON。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def apply_risk_review(
    precheck: dict[str, Any], aesthetic: dict[str, Any], audit: dict[str, Any]
) -> dict[str, Any]:
    """Apply only conservative corrections. The audit can never raise a model conclusion."""
    corrections: list[dict[str, Any]] = []
    reasons = [str(item) for item in audit.get("risk_reasons") or [] if str(item).strip()]
    media = precheck.setdefault("media_form", {})
    quality = precheck.setdefault("image_quality", {})

    professional = media.setdefault("professional_photography", {})
    audited_professional = audit.get("professional_photography")
    if professional.get("status") == "yes" and audited_professional in {"no", "uncertain"}:
        before = professional.get("status")
        professional.update(
            {
                "status": audited_professional,
                "confidence": float(audit.get("confidence") or 0.0),
                "evidence": reasons or ["高风险复核未确认专业摄影条件"],
            }
        )
        corrections.append(
            {"field": "professional_photography", "before": before, "after": audited_professional}
        )

    audited_documentary = audit.get("documentary_record")
    documentary = media.setdefault("documentary_record", {})
    if audited_documentary == "yes" and documentary.get("status") != "yes":
        before = documentary.get("status")
        documentary.update(
            {
                "status": "yes",
                "confidence": float(audit.get("confidence") or 0.0),
                "evidence": reasons or ["高风险复核判定为现场记录"],
            }
        )
        corrections.append(
            {"field": "documentary_record", "before": before, "after": "yes"}
        )

    current_quality = str(quality.get("quality_severity") or "uncertain")
    audited_quality = str(audit.get("quality_severity") or current_quality)
    if QUALITY_RANK.get(audited_quality, 0) > QUALITY_RANK.get(current_quality, 0):
        quality["quality_severity"] = audited_quality
        quality["confidence"] = float(audit.get("confidence") or 0.0)
        evidence = list(quality.get("evidence") or [])
        evidence.extend(reason for reason in reasons if reason not in evidence)
        quality["evidence"] = evidence
        corrections.append(
            {"field": "quality_severity", "before": current_quality, "after": audited_quality}
        )

    dimensions = aesthetic.get("dimensions") or {}
    for key, audited_grade in (audit.get("dimension_grades") or {}).items():
        if key not in DIMENSION_KEYS or not isinstance(audited_grade, int):
            continue
        item = dimensions.get(key)
        if not isinstance(item, dict):
            continue
        current_grade = item.get("grade")
        if isinstance(current_grade, int) and 1 <= audited_grade < current_grade:
            item["grade"] = audited_grade
            defects = list(item.get("defects") or [])
            for reason in reasons:
                if reason not in defects:
                    defects.append(reason)
            item["defects"] = defects
            corrections.append(
                {"field": f"dimensions.{key}", "before": current_grade, "after": audited_grade}
            )

    audited_cap = str(audit.get("level_cap") or "none")
    decision_rules = aesthetic.setdefault("decision_rules", {})
    current_cap = str(decision_rules.get("level_cap") or "none")
    if audited_cap in CAP_RANK and CAP_RANK[audited_cap] < CAP_RANK.get(current_cap, 6):
        decision_rules["level_cap"] = audited_cap
        decision_rules["level_cap_reasons"] = reasons or ["高风险复核设置等级上限"]
        corrections.append({"field": "level_cap", "before": current_cap, "after": audited_cap})

    confidence = float(audit.get("confidence") or 0.0)
    if audit.get("verdict") == "uncertain" or confidence < 0.7:
        precheck["needs_review"] = True
        aesthetic["needs_review"] = True
        review_reasons = list(aesthetic.get("review_reasons") or [])
        marker = "高风险复核置信度不足，需要人工确认"
        if marker not in review_reasons:
            review_reasons.append(marker)
        aesthetic["review_reasons"] = review_reasons

    return {
        "version": RISK_REVIEW_VERSION,
        "triggered": True,
        "verdict": audit.get("verdict") or ("downgrade" if corrections else "keep"),
        "confidence": confidence,
        "reasons": reasons,
        "corrections": corrections,
        "original_audit": deepcopy(audit),
    }
