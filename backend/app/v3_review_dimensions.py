"""Frozen V3 grade contracts for human-review display and recalculation.

The review UI historically read only the legacy strategy dimension snapshot.
V3 authoritative evaluations instead freeze their executable contract on the
evaluation job.  This module projects that frozen contract into the small UI
schema used by human correction and replays dimension corrections through the
same pure ``evaluate_one`` scoring chain used by the worker.
"""

from __future__ import annotations

import json
from typing import Any

from .dimension_composition import validate_subcategory_dimensions
from .dimension_grade_bridge import resolve_dimension_weight_scale
from .dimension_schema_registry import canonical_hash
from .inspiration_category_seed import evaluate_one


STRICT_GRADE_OUTPUT_CONTRACT = {
    "format_version": "dimension-grade-output-v1",
    "require_exact_keys": True,
    "evidence_required": True,
}


class V3ReviewDimensionError(ValueError):
    """The frozen V3 review contract cannot be interpreted safely."""


def _json_object(raw: Any, *, field: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise V3ReviewDimensionError(f"{field} 必须是 JSON 对象")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise V3ReviewDimensionError(f"{field} 必须是 JSON 对象")
    return parsed


def _frozen_context(evaluation: Any) -> dict[str, Any] | None:
    raw_scoring = getattr(evaluation, "scoring_json", None)
    if raw_scoring is None:
        return None
    scoring = _json_object(
        raw_scoring, field="scoring_json"
    )
    if (
        scoring.get("scoring_mode") != "v3_authoritative"
        or scoring.get("dimension_scoring_mode") != "grade_fallback"
    ):
        return None

    job = getattr(evaluation, "job", None)
    if job is None:
        raise V3ReviewDimensionError("V3 评测缺少冻结 Job")
    profile = _json_object(
        getattr(job, "category_profile_snapshot_json", None),
        field="category_profile_snapshot_json",
    )
    bundle = profile.get("v3_authoritative_bundle")
    if not isinstance(bundle, dict):
        raise V3ReviewDimensionError("V3 评测缺少冻结权威合同")

    contract = bundle.get("contract")
    classification_map = bundle.get("classification_map")
    subcategory_dimensions = bundle.get("subcategory_dimensions")
    if not isinstance(contract, dict):
        raise V3ReviewDimensionError("冻结 V3 contract 无效")
    if not isinstance(classification_map, dict):
        raise V3ReviewDimensionError("冻结 V3 classification_map 无效")
    if not isinstance(subcategory_dimensions, dict):
        raise V3ReviewDimensionError("冻结 V3 subcategory_dimensions 无效")

    track_key = scoring.get("track_key")
    if not isinstance(track_key, str) or not track_key:
        raise V3ReviewDimensionError("V3 评分缺少 track_key")
    track_config = subcategory_dimensions.get(track_key)
    if not isinstance(track_config, dict):
        raise V3ReviewDimensionError(f"冻结 V3 合同缺少赛道 {track_key}")

    grade_contract = track_config.get("grade_output_contract")
    if grade_contract is None:
        return None
    if grade_contract != STRICT_GRADE_OUTPUT_CONTRACT:
        raise V3ReviewDimensionError("冻结 V3 grade_output_contract 无效")
    validate_subcategory_dimensions(track_config)

    track_classification = contract.get("track_classification")
    if not isinstance(track_classification, dict):
        raise V3ReviewDimensionError("冻结 V3 track_classification 无效")
    tracks = track_classification.get("tracks")
    track = next(
        (
            item
            for item in tracks or []
            if isinstance(item, dict) and item.get("key") == track_key
        ),
        None,
    )
    if not isinstance(track, dict):
        raise V3ReviewDimensionError(f"V3 contract 未定义赛道 {track_key}")
    return {
        "scoring": scoring,
        "bundle": bundle,
        "contract": contract,
        "classification_map": classification_map,
        "subcategory_dimensions": subcategory_dimensions,
        "track_key": track_key,
        "track_config": track_config,
        "track": track,
    }


def _groups(track_config: dict[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for group_name in ("common_group", "specific_group"):
        group = track_config.get(group_name)
        if not isinstance(group, dict):
            continue
        schema = group.get("schema_definition")
        dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
        if isinstance(dimensions, list) and dimensions:
            groups.append((group, dimensions))
    if not groups:
        raise V3ReviewDimensionError("冻结 V3 赛道没有可纠偏维度")
    return groups


def _grade_points(
    dimension: dict[str, Any], schema: dict[str, Any]
) -> dict[str, float]:
    candidates = [dimension.get("grade_points")]
    aggregation = schema.get("aggregation")
    if isinstance(aggregation, dict):
        candidates.append(aggregation.get("grade_points"))
    candidates.append(schema.get("grade_points"))
    raw = next((item for item in candidates if item is not None), None)
    if not isinstance(raw, dict):
        raise V3ReviewDimensionError(
            f"维度 {dimension.get('key')} 缺少 grade_points"
        )
    points: dict[str, float] = {}
    for grade in range(1, 6):
        value = raw.get(str(grade))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise V3ReviewDimensionError(
                f"维度 {dimension.get('key')} 的 grade_points 无效"
            )
        points[str(grade)] = float(value)
    if points["5"] <= points["1"]:
        raise V3ReviewDimensionError(
            f"维度 {dimension.get('key')} 的 grade_points 方向无效"
        )
    return points


def v3_review_dimension_schema_payload(evaluation: Any) -> dict[str, Any] | None:
    """Return a frontend review schema for strict static-grade V3 results."""
    context = _frozen_context(evaluation)
    if context is None:
        return None
    contract = context["contract"]
    track = context["track"]
    track_config = context["track_config"]
    groups = _groups(track_config)
    dimension_max = float(track["dimension_max"])
    group_weight_total = sum(float(group["group_weight"]) for group, _ in groups)
    if group_weight_total <= 0:
        raise V3ReviewDimensionError("V3 非空维度组权重之和必须大于 0")

    projected_dimensions: list[dict[str, Any]] = []
    schema_keys: list[str] = []
    seen: set[str] = set()
    for group, dimensions in groups:
        schema = group["schema_definition"]
        schema_key = schema.get("schema_key")
        if isinstance(schema_key, str) and schema_key:
            schema_keys.append(schema_key)
        effective_max = (
            float(group["group_weight"]) / group_weight_total
        ) * dimension_max
        weight_scale, _ = resolve_dimension_weight_scale(dimensions, effective_max)
        for dimension in dimensions:
            key = dimension.get("key")
            label = dimension.get("label")
            if not isinstance(key, str) or not key or key in seen:
                raise V3ReviewDimensionError("V3 维度 key 缺失或重复")
            if not isinstance(label, str) or not label:
                raise V3ReviewDimensionError(f"维度 {key} 缺少展示名称")
            weight = dimension.get("weight")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise V3ReviewDimensionError(f"维度 {key} 的 weight 无效")
            seen.add(key)
            projected_dimensions.append(
                {
                    "key": key,
                    "label": label,
                    "weight": (
                        float(weight) * weight_scale / dimension_max
                        if dimension_max > 0
                        else 0.0
                    ),
                    "grade_points": _grade_points(dimension, schema),
                }
            )

    base_schema_key = schema_keys[0] if schema_keys else str(
        contract.get("category_key") or "v3_review"
    )
    schema_key = f"{base_schema_key}:{context['track_key']}"
    level_scale = contract.get("level_scale")
    levels = level_scale.get("levels") if isinstance(level_scale, dict) else None
    if not isinstance(levels, list) or not levels:
        raise V3ReviewDimensionError("冻结 V3 合同缺少 level_scale")
    definition = {
        "format_version": "v3-review-dimension-schema-v1",
        "schema_key": schema_key,
        "version": str(contract.get("spec_version") or "v3-frozen"),
        "dimensions": projected_dimensions,
        "aggregation": {
            "preview_mode": "v3_grade_bridge",
            "score_round_digits": 0,
            "base_score": track["base_score"],
            "dimension_max": track["dimension_max"],
            "track_cap": track["track_cap"],
            "level_scale": levels,
        },
        "output_contract": {
            "dimension_output_keys": [item["key"] for item in projected_dimensions],
            "unknown_key_policy": "reject",
        },
    }
    return {
        "status": "resolved",
        "schema_id": None,
        "schema_key": schema_key,
        "version": definition["version"],
        "canonical_hash": canonical_hash(definition),
        "legacy_derived": False,
        "dimension_keys": list(definition["output_contract"]["dimension_output_keys"]),
        "dimension_selection": None,
        "dimension_mode": "all",
        "definition": definition,
        "error": None,
    }


def calculate_v3_review_corrected_score(
    evaluation: Any,
    corrections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Replay corrected V3 grades through the frozen pure scoring engine."""
    context = _frozen_context(evaluation)
    if context is None:
        return None
    groups = _groups(context["track_config"])
    expected_keys = [
        str(dimension["key"])
        for _, dimensions in groups
        for dimension in dimensions
    ]
    aesthetic = _json_object(
        getattr(evaluation, "aesthetic_json", None), field="aesthetic_json"
    )
    raw_dimensions = aesthetic.get("dimensions")
    if not isinstance(raw_dimensions, dict):
        raise V3ReviewDimensionError("V3 aesthetic.dimensions 必须是对象")
    if set(raw_dimensions) != set(expected_keys):
        raise V3ReviewDimensionError("V3 结果维度与冻结合同不一致")

    grades: dict[str, int] = {}
    for key in expected_keys:
        item = raw_dimensions.get(key)
        grade = item.get("grade") if isinstance(item, dict) else None
        if isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 5:
            raise V3ReviewDimensionError(f"维度 {key} 的原始 grade 无效")
        grades[key] = grade

    for correction in corrections:
        key = correction.get("field_key")
        if key not in grades:
            raise V3ReviewDimensionError(f"人工纠偏包含未知维度：{key}")
        grade = correction.get("human_value")
        if isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 5:
            raise V3ReviewDimensionError(f"维度 {key} 的人工 grade 必须是 1 至 5")
        grades[key] = grade

    common_group = context["track_config"].get("common_group")
    specific_group = context["track_config"].get("specific_group")
    common_schema = (
        common_group.get("schema_definition", {})
        if isinstance(common_group, dict)
        else {}
    )
    specific_schema = (
        specific_group.get("schema_definition", {})
        if isinstance(specific_group, dict)
        else {}
    )
    common_keys = [
        item["key"] for item in common_schema.get("dimensions", [])
    ] if isinstance(common_schema, dict) else []
    specific_keys = [
        item["key"] for item in specific_schema.get("dimensions", [])
    ] if isinstance(specific_schema, dict) else []
    precheck = _json_object(
        getattr(evaluation, "precheck_json", None), field="precheck_json"
    )
    try:
        outcome = evaluate_one(
            contract=context["contract"],
            classification_map=context["classification_map"],
            subcategory_dimensions=context["subcategory_dimensions"],
            precheck=precheck,
            common_grades_by_track={
                context["track_key"]: {key: grades[key] for key in common_keys}
            } if common_keys else {},
            specific_grades_by_track={
                context["track_key"]: {key: grades[key] for key in specific_keys}
            } if specific_keys else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V3ReviewDimensionError(f"V3 人工纠偏重算失败：{exc}") from exc
    result = outcome.get("result")
    if not isinstance(result, dict):
        raise V3ReviewDimensionError("V3 人工纠偏重算未返回结果")
    if not result.get("hard_reject") and result.get("track_key") != context["track_key"]:
        raise V3ReviewDimensionError("V3 人工纠偏重算赛道与原评测不一致")
    score = result.get("score")
    level = result.get("level")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not isinstance(level, str)
    ):
        raise V3ReviewDimensionError("V3 人工纠偏无法计算正式分数与等级")
    return {"score": score, "level": level}
