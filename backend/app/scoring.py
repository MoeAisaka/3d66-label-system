from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from typing import Any

from .dimension_schema_registry import (
    SPACE_INPUT_DIMENSION_ALIASES,
    canonical_hash,
    space_schema_definition_for_scoring_profile,
    space_schema_definition_for_version,
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
)


class DimensionScoringContractError(ValueError):
    """Raised when a frozen DimensionSchema cannot drive scoring safely."""


def dimension_schema_from_strategy_snapshot(
    snapshot: str | dict[str, Any] | None,
    *,
    aesthetic: dict[str, Any] | None,
) -> dict[str, Any]:
    """Read the exact result-bound schema, with v1 compatibility fallback."""
    if snapshot is None:
        return space_schema_definition_for_scoring_profile(
            aesthetic.get("scoring_profile")
            if isinstance(aesthetic, dict)
            else None
        )
    if isinstance(snapshot, str):
        try:
            payload = json.loads(snapshot)
        except json.JSONDecodeError as exc:
            raise DimensionScoringContractError(
                "结果策略快照不是有效 JSON"
            ) from exc
    else:
        payload = snapshot
    if not isinstance(payload, dict):
        raise DimensionScoringContractError(
            "结果策略快照必须是 JSON 对象"
        )
    schema_version = payload.get("schema_version")
    if schema_version == "strategy-bundle-v1":
        return space_schema_definition_for_scoring_profile(
            aesthetic.get("scoring_profile")
            if isinstance(aesthetic, dict)
            else None
        )
    if schema_version != "strategy-bundle-v2":
        raise DimensionScoringContractError(
            "结果策略快照版本不受支持"
        )
    definition = payload.get("resolved_dimensions_snapshot")
    expected_hash = payload.get("resolved_dimension_schema_hash")
    if (
        not isinstance(definition, dict)
        or not isinstance(expected_hash, str)
        or canonical_hash(definition) != expected_hash
    ):
        raise DimensionScoringContractError(
            "结果绑定的 DimensionSchema 快照无法复算"
        )
    return deepcopy(definition)


def _weights_from_definition(
    definition: dict[str, Any],
) -> dict[str, float]:
    return {
        str(dimension["key"]): float(dimension["weight"])
        for dimension in definition["dimensions"]
        if dimension.get("aggregation_role") == "score"
    }


def _grade_points_from_definition(
    definition: dict[str, Any],
) -> dict[int, float]:
    return {
        int(grade): float(points)
        for grade, points in definition["aggregation"][
            "grade_points"
        ].items()
    }


_HISTORICAL_SCHEMA = space_schema_definition_for_version(
    HISTORICAL_DEFAULT_VERSION
)
_ACTIVE_SCHEMA = space_schema_definition_for_version(ACTIVE_V13_VERSION)
ENGINE_VERSION = str(_HISTORICAL_SCHEMA["aggregation"]["engine_version"])
if _ACTIVE_SCHEMA["aggregation"]["engine_version"] != ENGINE_VERSION:
    raise RuntimeError("空间维度兼容修订的评分引擎版本不一致")
GRADE_POINTS = _grade_points_from_definition(_HISTORICAL_SCHEMA)
WEIGHTS = _weights_from_definition(_HISTORICAL_SCHEMA)
COMBINED_WEIGHTS = _weights_from_definition(_ACTIVE_SCHEMA)
LEVEL_THRESHOLDS = {
    key: float(value)
    for key, value in _HISTORICAL_SCHEMA["aggregation"][
        "level_thresholds"
    ].items()
}


def _level_for_score(
    score: float,
    thresholds: dict[str, float] | None = None,
) -> str:
    resolved = thresholds or LEVEL_THRESHOLDS
    if score < resolved["L2"]:
        return "L1"
    if score < resolved["L3"]:
        return "L2"
    if score < resolved["L4"]:
        return "L3"
    if score < resolved["L5"]:
        return "L4"
    return "L5"


def _compile_dimension_contract(
    definition: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise DimensionScoringContractError(
            "DimensionSchema 定义必须是 JSON 对象"
        )
    aggregation = definition.get("aggregation")
    dimensions = definition.get("dimensions")
    output_contract = definition.get("output_contract")
    if (
        not isinstance(aggregation, dict)
        or not isinstance(dimensions, list)
        or not dimensions
        or not isinstance(output_contract, dict)
    ):
        raise DimensionScoringContractError(
            "DimensionSchema 缺少维度、聚合或输出合同"
        )
    if aggregation.get("engine_version") != ENGINE_VERSION:
        raise DimensionScoringContractError(
            "DimensionSchema 与当前评分引擎版本不兼容"
        )

    try:
        grade_points = _grade_points_from_definition(definition)
    except (KeyError, TypeError, ValueError) as exc:
        raise DimensionScoringContractError(
            "DimensionSchema 的等级换算无效"
        ) from exc
    if set(grade_points) != {1, 2, 3, 4, 5}:
        raise DimensionScoringContractError(
            "DimensionSchema 必须定义 1 至 5 级换算"
        )

    raw_thresholds = aggregation.get("level_thresholds")
    if not isinstance(raw_thresholds, dict):
        raise DimensionScoringContractError(
            "DimensionSchema 缺少 L1-L5 阈值"
        )
    try:
        thresholds = {
            key: float(raw_thresholds[key])
            for key in ("L2", "L3", "L4", "L5")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise DimensionScoringContractError(
            "DimensionSchema 的 L1-L5 阈值无效"
        ) from exc
    if list(thresholds.values()) != sorted(
        thresholds.values()
    ) or len(set(thresholds.values())) != 4:
        raise DimensionScoringContractError(
            "DimensionSchema 的 L1-L5 阈值必须严格递增"
        )

    dimension_keys: list[str] = []
    seen_keys: set[str] = set()
    weights: dict[str, float] = {}
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise DimensionScoringContractError(
                "DimensionSchema 维度项必须是对象"
            )
        key = dimension.get("key")
        if not isinstance(key, str) or not key or key in seen_keys:
            raise DimensionScoringContractError(
                "DimensionSchema 维度键为空或重复"
            )
        seen_keys.add(key)
        if dimension.get("aggregation_role") != "score":
            continue
        try:
            weight = float(dimension["weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DimensionScoringContractError(
                f"维度 {key} 缺少有效权重"
            ) from exc
        dimension_grade_points = dimension.get("grade_points")
        try:
            normalized_dimension_points = {
                int(grade): float(points)
                for grade, points in dimension_grade_points.items()
            }
        except (AttributeError, TypeError, ValueError) as exc:
            raise DimensionScoringContractError(
                f"维度 {key} 的等级换算无效"
            ) from exc
        if normalized_dimension_points != grade_points:
            raise DimensionScoringContractError(
                f"维度 {key} 的等级换算与聚合合同不一致"
            )
        if weight <= 0:
            raise DimensionScoringContractError(
                f"维度 {key} 的权重必须大于零"
            )
        dimension_keys.append(key)
        weights[key] = weight
    if not dimension_keys or abs(sum(weights.values()) - 1.0) > 1e-9:
        raise DimensionScoringContractError(
            "DimensionSchema 计分维度权重之和必须等于 1"
        )

    output_keys = output_contract.get("dimension_output_keys")
    if output_keys != dimension_keys:
        raise DimensionScoringContractError(
            "DimensionSchema 输出键顺序与计分维度不一致"
        )
    if output_contract.get("unknown_key_policy") != "reject":
        raise DimensionScoringContractError(
            "P1 只接受拒绝未知维度键的输出合同"
        )

    for rule_name in (
        "collapse_rule",
        "high_evidence_rule",
        "top_level_rule",
        "decision_rule_policy",
    ):
        if not isinstance(aggregation.get(rule_name), dict):
            raise DimensionScoringContractError(
                f"DimensionSchema 缺少 {rule_name}"
            )
    collapse_rule = aggregation["collapse_rule"]
    high_evidence_rule = aggregation["high_evidence_rule"]
    top_level_rule = aggregation["top_level_rule"]
    decision_rule_policy = aggregation["decision_rule_policy"]
    try:
        collapse_minimum = int(
            collapse_rule["all_equal_minimum_grade"]
        )
        same_grade_count = int(
            collapse_rule["same_grade_count_for_review"]
        )
        high_grade_minimum = int(
            high_evidence_rule["high_grade_minimum"]
        )
        minimum_evidence = int(
            high_evidence_rule["minimum_evidence"]
        )
        high_evidence_count = int(
            high_evidence_rule["dimensions_for_l3_cap"]
        )
        grade_five_count = int(
            top_level_rule["grade_five_minimum_count"]
        )
        other_minimum = int(
            top_level_rule["other_dimension_minimum_grade"]
        )
        minimum_confidence = float(
            top_level_rule["minimum_confidence"]
        )
        requires_no_review_raw = top_level_rule[
            "requires_no_model_review"
        ]
        score_round_digits = int(
            aggregation.get("score_round_digits", 2)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DimensionScoringContractError(
            "DimensionSchema 的聚合规则参数无效"
        ) from exc
    if (
        collapse_minimum not in grade_points
        or not 1 <= same_grade_count <= len(dimension_keys)
        or high_grade_minimum not in grade_points
        or minimum_evidence < 0
        or not 1 <= high_evidence_count <= len(dimension_keys)
        or not 1 <= grade_five_count <= len(dimension_keys)
        or other_minimum not in grade_points
        or not 0 <= minimum_confidence <= 1
        or not isinstance(requires_no_review_raw, bool)
        or not 0 <= score_round_digits <= 6
    ):
        raise DimensionScoringContractError(
            "DimensionSchema 的聚合规则超出安全范围"
        )
    allowed_caps = decision_rule_policy.get("allowed_level_caps")
    hard_gate_target = decision_rule_policy.get("hard_gate_target")
    if (
        not isinstance(allowed_caps, list)
        or not allowed_caps
        or set(allowed_caps) - {"L1", "L2", "L3", "L4"}
        or hard_gate_target not in allowed_caps
    ):
        raise DimensionScoringContractError(
            "DimensionSchema 的等级封顶策略无效"
        )
    return {
        "dimension_keys": tuple(dimension_keys),
        "weights": weights,
        "grade_points": grade_points,
        "level_thresholds": thresholds,
        "score_round_digits": score_round_digits,
        "collapse_rule": collapse_rule,
        "high_evidence_rule": high_evidence_rule,
        "top_level_rule": {
            **top_level_rule,
            "requires_no_model_review": requires_no_review_raw,
        },
        "decision_rule_policy": decision_rule_policy,
    }


def _status(item: dict[str, Any] | None) -> tuple[str, float]:
    if not isinstance(item, dict):
        return "uncertain", 0.0
    return str(item.get("status", "uncertain")), float(item.get("confidence") or 0.0)


def _cap_level(level: str, cap: int) -> str:
    current = int(level.removeprefix("L"))
    return f"L{min(current, cap)}"


def calculate_score(
    precheck: dict[str, Any],
    aesthetic: dict[str, Any] | None,
    *,
    dimension_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    definition = (
        deepcopy(dimension_schema)
        if dimension_schema is not None
        else space_schema_definition_for_scoring_profile(
            aesthetic.get("scoring_profile")
        )
    )
    contract = _compile_dimension_contract(definition)
    weighted_score = 0.0
    dimension_points: dict[str, dict[str, float | int]] = {}
    dimension_grades: dict[str, int] = {}
    dimensions = aesthetic.get("dimensions") or {}
    if not isinstance(dimensions, dict):
        raise ValueError("美感结果缺少维度对象")
    dimensions = deepcopy(dimensions)
    dimension_keys = contract["dimension_keys"]
    for alias, canonical_key in SPACE_INPUT_DIMENSION_ALIASES.items():
        if (
            alias not in dimensions
            or alias in dimension_keys
            or canonical_key not in dimension_keys
        ):
            continue
        if (
            canonical_key not in dimensions
            or dimensions[canonical_key] != dimensions[alias]
        ):
            raise ValueError(
                f"兼容维度别名 {alias} 与 {canonical_key} 不一致"
            )
        dimensions.pop(alias)
    unknown_keys = set(dimensions) - set(dimension_keys)
    if unknown_keys:
        raise ValueError(
            "美感结果包含未发布的维度："
            + "、".join(sorted(unknown_keys))
        )
    weights = contract["weights"]
    grade_points = contract["grade_points"]
    for key, weight in weights.items():
        item = dimensions.get(key) or {}
        grade = int(item.get("grade") or 0)
        if grade not in grade_points:
            raise ValueError(f"维度 {key} 的等级无效：{grade}")
        points = grade_points[grade]
        weighted_score += points * weight
        dimension_grades[key] = grade
        dimension_points[key] = {"grade": grade, "points": points, "weight": weight}

    score = round(weighted_score, contract["score_round_digits"])
    raw_level = _level_for_score(
        score,
        contract["level_thresholds"],
    )
    final_level = raw_level
    final_score = score
    caps: list[dict[str, Any]] = []

    def apply_cap(cap: int, reason: str) -> None:
        nonlocal final_level, final_score
        before = final_level
        final_level = _cap_level(final_level, cap)
        next_level = f"L{cap + 1}"
        final_score = min(
            final_score,
            contract["level_thresholds"][next_level] - 1.0,
        )
        if before != final_level or int(raw_level[1:]) > cap:
            caps.append({"cap": f"L{cap}", "reason": reason})

    quality = precheck.get("image_quality") or {}
    severity = quality.get("quality_severity")
    quality_confidence = float(quality.get("confidence") or 0.0)
    quality_evidence = quality.get("evidence") or []
    if severity in {"slight", "moderate", "severe", "unusable"}:
        apply_cap(2, "画质受损最高 L2")
    if severity in {"severe", "unusable"} and quality_confidence >= 0.8 and len(quality_evidence) >= 2:
        apply_cap(1, "严重或不可用画质，且证据与置信度达到规则阈值")

    media = precheck.get("media_form") or {}
    casual_status, casual_confidence = _status(media.get("casual_snapshot"))
    if casual_status == "yes":
        apply_cap(2, "随拍图最高 L2")
        if casual_confidence < 0.75:
            review_reasons.append("随拍图判断置信度低于 0.75，等级已封顶并需要复核")
    elif casual_status == "uncertain":
        review_reasons.append("随拍图判断不确定")

    for key, cap, label in (
        ("ai_generated", 4, "AI 图"),
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
            if (
                score < contract["level_thresholds"]["L5"]
                or grade_fives < 2
            ):
                apply_cap(4, "效果图进入 L5 的原始分或 5 级特殊检查数量不足")

    model_review = bool(precheck.get("needs_review")) or bool(aesthetic.get("needs_review"))
    confidence = float(aesthetic.get("assessment_confidence") or 0.0)

    collapse_rule = contract["collapse_rule"]
    calibrated_profile = (
        aesthetic.get("scoring_profile")
        == collapse_rule.get("applies_to_scoring_profile")
    )
    if calibrated_profile:
        grade_values = list(dimension_grades.values())
        if (
            len(set(grade_values)) == 1
            and grade_values[0]
            >= int(collapse_rule["all_equal_minimum_grade"])
        ):
            review_reasons.append(
                (
                    "八维高分完全一致"
                    if len(dimension_keys) == 8
                    else "全部计分维度高分完全一致"
                )
                + "，疑似出现评分坍缩"
            )
        same_grade_threshold = int(
            collapse_rule["same_grade_count_for_review"]
        )
        if (
            max(Counter(grade_values).values(), default=0)
            >= same_grade_threshold
        ):
            review_reasons.append(
                (
                    "六个以上维度"
                    if same_grade_threshold == 6
                    else f"{same_grade_threshold}个以上维度"
                )
                + "等级相同，疑似出现中间分坍缩"
            )

        high_evidence_rule = contract["high_evidence_rule"]
        minimum_evidence = int(
            high_evidence_rule["minimum_evidence"]
        )
        unsupported_high = [
            key
            for key, grade in dimension_grades.items()
            if grade
            >= int(high_evidence_rule["high_grade_minimum"])
            and len((dimensions.get(key) or {}).get("evidence") or [])
            < minimum_evidence
        ]
        if len(unsupported_high) >= int(
            high_evidence_rule["dimensions_for_l3_cap"]
        ):
            apply_cap(
                3,
                "多个高分维度缺少至少"
                f"{minimum_evidence}条独立视觉证据",
            )
            review_reasons.append("高分证据不足：" + "、".join(unsupported_high))

        if raw_level == "L5":
            top_level_rule = contract["top_level_rule"]
            grade_fives = sum(1 for grade in grade_values if grade == 5)
            if (
                grade_fives
                < int(top_level_rule["grade_five_minimum_count"])
                or min(grade_values)
                < int(
                    top_level_rule[
                        "other_dimension_minimum_grade"
                    ]
                )
                or confidence
                < float(top_level_rule["minimum_confidence"])
                or (
                    bool(
                        top_level_rule["requires_no_model_review"]
                    )
                    and model_review
                )
            ):
                apply_cap(
                    4,
                    "L5 需要至少"
                    f"{int(top_level_rule['grade_five_minimum_count'])}"
                    "个5级维度、其余不低于"
                    f"{int(top_level_rule['other_dimension_minimum_grade'])}"
                    "级、置信度不低于"
                    f"{float(top_level_rule['minimum_confidence']):g}"
                    "且满足复核约束",
                )

        professional_status, _ = _status(media.get("professional_photography"))
        documentary_status, _ = _status(media.get("documentary_record"))
        if professional_status == "yes" and documentary_status == "yes":
            review_reasons.append("专业摄影与现场记录不能同时为是")

    decision_rules = aesthetic.get("decision_rules") or {}
    if decision_rules.get("hard_gate_triggered") is True:
        reasons = decision_rules.get("hard_gate_reasons") or []
        hard_gate_target = str(
            contract["decision_rule_policy"]["hard_gate_target"]
        )
        apply_cap(
            int(hard_gate_target[1]),
            "；".join(reasons)
            or f"综合提示词触发 {hard_gate_target} 质量硬门槛",
        )
    declared_cap = str(decision_rules.get("level_cap") or "none")
    allowed_caps = set(
        contract["decision_rule_policy"]["allowed_level_caps"]
    )
    if declared_cap in allowed_caps:
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
        "score": (
            round(final_score, contract["score_round_digits"])
            if formal
            else None
        ),
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
    *,
    dimension_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recalculate the authoritative score after human dimension corrections."""
    if not aesthetic:
        raise ValueError("当前结果没有可纠正的美感维度")
    corrected = deepcopy(aesthetic)
    dimensions = corrected.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError("当前结果缺少美感维度")
    definition = (
        deepcopy(dimension_schema)
        if dimension_schema is not None
        else space_schema_definition_for_scoring_profile(
            corrected.get("scoring_profile")
        )
    )
    contract = _compile_dimension_contract(definition)
    for correction in corrections:
        if correction.get("target_type") != "dimension":
            raise ValueError("人工纠正只能修改维度分数")
        key = str(correction.get("field_key") or "")
        if key not in contract["weights"] or not isinstance(
            dimensions.get(key), dict
        ):
            raise ValueError(f"未知的纠正维度：{key}")
        grade = correction.get("human_value")
        if (
            not isinstance(grade, int)
            or grade not in contract["grade_points"]
        ):
            raise ValueError(f"维度 {key} 的人工分数无效")
        dimensions[key]["grade"] = grade
    return calculate_score(
        precheck,
        corrected,
        dimension_schema=definition,
    )
