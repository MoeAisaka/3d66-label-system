from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any


ENGINE_VERSION = "engine-v2.4.0"

GRADE_POINTS = {1: 20.0, 2: 45.0, 3: 65.0, 4: 82.0, 5: 95.0}
WEIGHTS = {
    "composition_viewpoint": 0.15,
    "lighting_atmosphere": 0.12,
    "color_material": 0.12,
    "spatial_design_furnishing": 0.18,
    "visual_hierarchy": 0.10,
    "detail_completion": 0.10,
    "inspiration_reference": 0.08,
    "presentation_integrity": 0.15,
}
COMBINED_WEIGHTS = {
    "composition_viewpoint": 0.15,
    "lighting_atmosphere": 0.12,
    "color_material": 0.12,
    "spatial_design_furnishing": 0.16,
    "visual_hierarchy": 0.10,
    "detail_completion": 0.10,
    "inspiration_reference": 0.10,
    "presentation_integrity": 0.15,
}


def _level_for_score(score: float) -> str:
    if score < 40:
        return "L1"
    if score < 60:
        return "L2"
    if score < 75:
        return "L3"
    if score < 90:
        return "L4"
    return "L5"


def _status(item: dict[str, Any] | None) -> tuple[str, float]:
    if not isinstance(item, dict):
        return "uncertain", 0.0
    return str(item.get("status", "uncertain")), float(item.get("confidence") or 0.0)


def _cap_level(level: str, cap: int) -> str:
    current = int(level.removeprefix("L"))
    return f"L{min(current, cap)}"


def calculate_score(precheck: dict[str, Any], aesthetic: dict[str, Any] | None) -> dict[str, Any]:
    classification = precheck.get("classification") or {}
    scope_status = classification.get("scope_status", "out_of_scope")
    primary_confidence = float(classification.get("primary_confidence") or 0.0)
    review_reasons: list[str] = list(precheck.get("review_reasons") or [])

    if scope_status not in {"in_scope", "boundary"} or not aesthetic:
        return {
            "engine_version": ENGINE_VERSION,
            "formal": False,
            "score": None,
            "level": None,
            "raw_level": None,
            "caps": [],
            "needs_review": bool(precheck.get("needs_review")),
            "review_reasons": review_reasons or ["首期评测范围外，未执行美感评分"],
        }

    weighted_score = 0.0
    dimension_points: dict[str, dict[str, float | int]] = {}
    dimension_grades: dict[str, int] = {}
    dimensions = aesthetic.get("dimensions") or {}
    weights = (
        COMBINED_WEIGHTS
        if aesthetic.get("scoring_profile") == "space_aesthetic_v1.3"
        else WEIGHTS
    )
    for key, weight in weights.items():
        item = dimensions.get(key) or {}
        grade = int(item.get("grade") or 0)
        if grade not in GRADE_POINTS:
            raise ValueError(f"维度 {key} 的等级无效：{grade}")
        points = GRADE_POINTS[grade]
        weighted_score += points * weight
        dimension_grades[key] = grade
        dimension_points[key] = {"grade": grade, "points": points, "weight": weight}

    score = round(weighted_score, 2)
    raw_level = _level_for_score(score)
    final_level = raw_level
    final_score = score
    caps: list[dict[str, Any]] = []

    def apply_cap(cap: int, reason: str) -> None:
        nonlocal final_level, final_score
        before = final_level
        final_level = _cap_level(final_level, cap)
        final_score = min(final_score, {1: 39.0, 2: 59.0, 3: 74.0, 4: 89.0}[cap])
        if before != final_level or int(raw_level[1:]) > cap:
            caps.append({"cap": f"L{cap}", "reason": reason})

    quality = precheck.get("image_quality") or {}
    severity = quality.get("quality_severity")
    quality_confidence = float(quality.get("confidence") or 0.0)
    quality_evidence = quality.get("evidence") or []
    if severity in {"severe", "unusable"} and quality_confidence >= 0.8 and len(quality_evidence) >= 2:
        apply_cap(1, "严重或不可用画质，且证据与置信度达到规则阈值")

    media = precheck.get("media_form") or {}
    for key, cap, label in (
        ("ai_generated", 4, "AI 图"),
        ("casual_snapshot", 3, "随拍图"),
        ("documentary_record", 3, "现场记录图"),
        ("collage_or_multiview", 3, "拼图或多视角"),
        ("unfinished_scene", 3, "未完工现场"),
    ):
        status, confidence = _status(media.get(key))
        if status == "yes" and confidence >= 0.75:
            apply_cap(cap, f"{label}置信度达到 0.75")
        elif status == "uncertain":
            review_reasons.append(f"{label}判断不确定")

    white_status, white_confidence = _status(media.get("white_background_product"))
    if white_status == "yes" and white_confidence >= 0.75:
        apply_cap(4, "纯白底产品图最高 L4")

    rendering_status, rendering_confidence = _status(media.get("rendering"))
    if rendering_status == "yes" and rendering_confidence >= 0.75 and int(raw_level[1:]) >= 4:
        special = aesthetic.get("special_checks") or {}
        applicable_grades = [
            int(item.get("grade") or 0)
            for item in special.values()
            if isinstance(item, dict) and item.get("applicable") is True
        ]
        if any(grade < 4 for grade in applicable_grades):
            apply_cap(3, "效果图进入 L4 的适用特殊检查未全部达到 4 级")
        if raw_level == "L5":
            grade_fives = sum(1 for grade in applicable_grades if grade == 5)
            if score < 90 or grade_fives < 2:
                apply_cap(4, "效果图进入 L5 的原始分或 5 级特殊检查数量不足")

    model_review = bool(precheck.get("needs_review")) or bool(aesthetic.get("needs_review"))
    confidence = float(aesthetic.get("assessment_confidence") or 0.0)

    if aesthetic.get("scoring_profile") == "space_aesthetic_v1.3":
        grade_values = list(dimension_grades.values())
        if len(set(grade_values)) == 1 and grade_values[0] >= 4:
            review_reasons.append("八维高分完全一致，疑似出现评分坍缩")
        if max(Counter(grade_values).values(), default=0) >= 6:
            review_reasons.append("六个以上维度等级相同，疑似出现中间分坍缩")

        unsupported_high = [
            key
            for key, grade in dimension_grades.items()
            if grade >= 4
            and len((dimensions.get(key) or {}).get("evidence") or []) < 2
        ]
        if len(unsupported_high) >= 2:
            apply_cap(3, "多个高分维度缺少至少两条独立视觉证据")
            review_reasons.append("高分证据不足：" + "、".join(unsupported_high))

        if raw_level == "L5":
            grade_fives = sum(1 for grade in grade_values if grade == 5)
            if (
                grade_fives < 5
                or min(grade_values) < 4
                or confidence < 0.9
                or model_review
            ):
                apply_cap(4, "L5 需要至少五个5级维度、其余不低于4级、置信度不低于0.9且无复核项")

        professional_status, _ = _status(media.get("professional_photography"))
        documentary_status, _ = _status(media.get("documentary_record"))
        if professional_status == "yes" and documentary_status == "yes":
            review_reasons.append("专业摄影与现场记录不能同时为是")

    decision_rules = aesthetic.get("decision_rules") or {}
    if decision_rules.get("hard_gate_triggered") is True:
        reasons = decision_rules.get("hard_gate_reasons") or []
        apply_cap(1, "；".join(reasons) or "综合提示词触发 L1 质量硬门槛")
    declared_cap = str(decision_rules.get("level_cap") or "none")
    if declared_cap in {"L1", "L2", "L3", "L4"}:
        reasons = decision_rules.get("level_cap_reasons") or []
        apply_cap(int(declared_cap[1]), "；".join(reasons) or f"综合提示词声明 {declared_cap} 上限")

    if 0.55 <= primary_confidence < 0.75:
        review_reasons.append("业务分类置信度处于运营复核区间")
    if primary_confidence < 0.55:
        review_reasons.append("业务分类置信度低于 0.55，不生成正式等级")

    review_reasons.extend(aesthetic.get("review_reasons") or [])
    formal = primary_confidence >= 0.55
    return {
        "engine_version": ENGINE_VERSION,
        "formal": formal,
        "score": round(final_score, 2) if formal else None,
        "level": final_level if formal else None,
        "raw_level": raw_level,
        "raw_score": score,
        "dimension_points": dimension_points,
        "caps": caps,
        "confidence": confidence,
        "needs_review": model_review or bool(review_reasons),
        "review_reasons": list(dict.fromkeys(review_reasons)),
    }


def calculate_corrected_score(
    precheck: dict[str, Any],
    aesthetic: dict[str, Any] | None,
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recalculate the authoritative score after human dimension corrections."""
    if not aesthetic:
        raise ValueError("当前结果没有可纠正的美感维度")
    corrected = deepcopy(aesthetic)
    dimensions = corrected.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError("当前结果缺少美感维度")
    for correction in corrections:
        if correction.get("target_type") != "dimension":
            raise ValueError("人工纠正只能修改维度分数")
        key = str(correction.get("field_key") or "")
        if key not in WEIGHTS or not isinstance(dimensions.get(key), dict):
            raise ValueError(f"未知的纠正维度：{key}")
        grade = correction.get("human_value")
        if not isinstance(grade, int) or grade not in GRADE_POINTS:
            raise ValueError(f"维度 {key} 的人工分数无效")
        dimensions[key]["grade"] = grade
    return calculate_score(precheck, corrected)
