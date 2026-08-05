"""Calling-B rule-deduction bridge and deterministic score composition.

The multimodal model does not grade dimensions.  It receives the configured
rules and returns only which rules were hit, with independent confidence and
evidence.  Server code validates those IDs and deterministically converts the
hits into weighted point deductions for the v3 aggregator.

Provider failures are intentionally fail-open for this one subjective node per
the Owner-approved contract: all dimensions receive ``hit_rules=[]`` and the
warning is carried into the evaluation steps.  Contract/config corruption still
fails closed before any provider call.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .category_evaluation_contract import (
    DeductionRule,
    DimensionDeductionOutput,
)
from .dimension_composition import validate_subcategory_dimensions
from .dimension_grade_bridge import (
    DimensionGradeBridgeError,
    resolve_dimension_weight_scale,
)


DEDUCTION_BRIDGE_VERSION = "dimension-deduction-bridge-v2"
DEDUCTION_PROMPT_TEMPLATE_VERSION = "dimension-deduction-prompt-v1"
RULE_COMPOSITION_VERSION = "dimension-rule-composition-v1"
FALLBACK_WARNING = "调用B失败，维度分按满分通过（安全兜底）"
_WEIGHT_TOLERANCE = 1e-9


class DimensionDeductionBridgeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def dimension_result_map(value: Any) -> dict[str, dict[str, Any]]:
    """Read current key-addressable output and the short-lived v1 array."""
    if isinstance(value, dict):
        return {
            key: item
            for key, item in value.items()
            if isinstance(key, str) and isinstance(item, dict)
        }
    if not isinstance(value, list):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for item in value:
        key = item.get("dimension_key") if isinstance(item, dict) else None
        if isinstance(key, str) and key and key not in normalized:
            normalized[key] = item
    return normalized


def dimension_definitions(config: Any) -> list[dict[str, Any]]:
    """Return all configured common/specific dimensions in stable order."""
    validate_subcategory_dimensions(config)
    definitions: list[dict[str, Any]] = []
    for group_name in ("common_group", "specific_group"):
        group = config.get(group_name)
        if not isinstance(group, dict):
            continue
        schema = group.get("schema_definition")
        dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
        if isinstance(dimensions, list):
            definitions.extend(dimensions)
    return definitions


def has_deduction_rules(config: Any) -> bool:
    """True when every non-empty dimension uses the new rule contract."""
    dimensions = dimension_definitions(config)
    return bool(dimensions) and all(
        isinstance(dimension.get("deduction_rules"), list)
        and bool(dimension["deduction_rules"])
        for dimension in dimensions
    )


def empty_deduction_output(
    config: dict[str, Any], *, warning: str | None = None,
    prompt_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "bridge_version": DEDUCTION_BRIDGE_VERSION,
        "prompt_identity": prompt_identity,
        "dimensions": {
            dimension["key"]: {"hit_rules": []}
            for dimension in dimension_definitions(config)
        },
        "overall_note": "",
        "warning": warning,
        "raw_payload": None,
    }


def _configured_rules(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, DeductionRule]]]:
    dimensions = dimension_definitions(config)
    if not dimensions:
        raise DimensionDeductionBridgeError(
            "dimensions_empty", "规则扣分调用B至少需要一个维度"
        )
    rules_by_dimension: dict[str, dict[str, DeductionRule]] = {}
    for dimension in dimensions:
        raw_rules = dimension.get("deduction_rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise DimensionDeductionBridgeError(
                "deduction_rules_missing",
                f"维度 {dimension['key']} 缺少 deduction_rules，应走旧 grade fallback",
            )
        rules_by_dimension[dimension["key"]] = {
            rule.rule_id: rule
            for rule in (DeductionRule.model_validate(item) for item in raw_rules)
        }
    return dimensions, rules_by_dimension


def normalize_dimension_deduction_output(
    payload: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Validate provider JSON against the frozen configured dimensions/rules."""
    dimensions, rules_by_dimension = _configured_rules(config)
    if not isinstance(payload, dict):
        raise DimensionDeductionBridgeError(
            "response_not_object", "调用B响应必须是 JSON 对象"
        )
    raw_dimensions = payload.get("dimensions")
    if isinstance(raw_dimensions, dict):
        raw_items = []
        for dimension_key, value in raw_dimensions.items():
            if not isinstance(value, dict):
                raise DimensionDeductionBridgeError(
                    "dimension_output_invalid",
                    f"调用B维度 {dimension_key} 输出必须是对象",
                )
            raw_items.append({**value, "dimension_key": dimension_key})
    elif isinstance(raw_dimensions, list):
        # Compatibility with responses created by bridge-v1 before the public
        # mapping shape was aligned with the contract.
        raw_items = raw_dimensions
    else:
        raise DimensionDeductionBridgeError(
            "dimensions_not_object", "调用B响应 dimensions 必须是维度 key 到命中结果的对象"
        )

    parsed: dict[str, DimensionDeductionOutput] = {}
    for raw in raw_items:
        try:
            item = DimensionDeductionOutput.model_validate(raw)
        except ValueError as exc:
            raise DimensionDeductionBridgeError(
                "dimension_output_invalid", f"调用B维度输出无效：{exc}"
            ) from exc
        if item.dimension_key in parsed:
            raise DimensionDeductionBridgeError(
                "dimension_duplicate", f"维度重复：{item.dimension_key}"
            )
        configured = rules_by_dimension.get(item.dimension_key)
        if configured is None:
            raise DimensionDeductionBridgeError(
                "dimension_unknown", f"调用B返回未知维度：{item.dimension_key}"
            )
        seen_rules: set[str] = set()
        for hit in item.hit_rules:
            if hit.rule_id not in configured:
                raise DimensionDeductionBridgeError(
                    "rule_unknown",
                    f"维度 {item.dimension_key} 返回未知 rule_id：{hit.rule_id}",
                )
            if hit.rule_id in seen_rules:
                raise DimensionDeductionBridgeError(
                    "rule_duplicate",
                    f"维度 {item.dimension_key} 重复命中 rule_id：{hit.rule_id}",
                )
            seen_rules.add(hit.rule_id)
        parsed[item.dimension_key] = item

    expected_keys = [dimension["key"] for dimension in dimensions]
    if set(parsed) != set(expected_keys):
        missing = sorted(set(expected_keys) - set(parsed))
        extra = sorted(set(parsed) - set(expected_keys))
        raise DimensionDeductionBridgeError(
            "dimension_keys_mismatch",
            f"调用B维度必须与合同完全一致（缺失 {missing}，多余 {extra}）",
        )
    return {
        "bridge_version": DEDUCTION_BRIDGE_VERSION,
        "dimensions": {
            key: {"hit_rules": parsed[key].model_dump()["hit_rules"]}
            for key in expected_keys
        },
        "overall_note": str(payload.get("overall_note") or "").strip(),
        "warning": None,
    }


def build_dimension_deduction_prompt(
    config: dict[str, Any], *, precheck: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Build the Chinese rule-by-rule calling-B prompt from frozen config."""
    dimensions, _ = _configured_rules(config)
    system = (
        "你是灵感素材质量核验专家。你只做规则命中判断，不打1-5分、不输出总分或等级。"
        "逐维度检查每条扣分规则；只返回确有视觉证据的命中项。每条命中必须给出独立、"
        "可定位的中文证据和 high/medium/low 置信度。严格输出 JSON。"
    )
    rule_blocks: list[str] = []
    for dimension in dimensions:
        rule_lines = [
            f"- {rule['rule_id']}: {rule['description']}（扣 {rule['deduction']} 分）"
            for rule in dimension["deduction_rules"]
        ]
        rule_blocks.append(
            f"# 维度：{dimension.get('label') or dimension['key']}（{dimension['key']}）\n"
            + "\n".join(rule_lines)
        )
    response_contract = {
        "dimensions": {
            dimension["key"]: {
                "hit_rules": [
                    {
                        "rule_id": "命中的规则ID",
                        "confidence": "high|medium|low",
                        "evidence": "图中具体证据",
                    }
                ],
            }
            for dimension in dimensions
        },
        "overall_note": "整体说明",
    }
    user = (
        "\n\n".join(rule_blocks)
        + "\n\n调用A预检字段：\n"
        + json.dumps(precheck or {}, ensure_ascii=False, sort_keys=True)
        + "\n\n输出结构（未命中时 hit_rules 必须为空数组）：\n"
        + json.dumps(response_contract, ensure_ascii=False)
    )
    return system, user


def _deduction_prompt_identity(system_prompt: str, user_prompt: str) -> dict[str, str]:
    return {
        "template_version": DEDUCTION_PROMPT_TEMPLATE_VERSION,
        "system_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "user_sha256": hashlib.sha256(user_prompt.encode("utf-8")).hexdigest(),
    }


async def call_multimodal_for_dimension_deductions(
    image: Any,
    contract: dict[str, Any],
    *,
    client: Any,
    mime_type: str,
    precheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the multimodal model and normalize rule hits.

    ``contract`` is the resolved track's ``subcategory-dimensions-v1`` config.
    A provider/parse/validation failure returns empty hits plus ``warning``;
    malformed local rule configuration is validated before the ``try`` and
    therefore fails closed instead of being disguised as a model outage.
    """
    _configured_rules(contract)  # local contract corruption must fail closed
    system_prompt, user_prompt = build_dimension_deduction_prompt(
        contract, precheck=precheck
    )
    prompt_identity = _deduction_prompt_identity(system_prompt, user_prompt)
    try:
        response = await client.chat_json(
            system_prompt,
            user_prompt,
            image_path=image,
            mime_type=mime_type,
        )
        parsed = response.parsed if hasattr(response, "parsed") else response
        normalized = normalize_dimension_deduction_output(parsed, contract)
        normalized["prompt_identity"] = prompt_identity
        normalized["raw_payload"] = getattr(response, "raw_payload", parsed)
        return normalized
    except Exception:  # noqa: BLE001 - approved subjective-node fail-open policy
        return empty_deduction_output(
            contract, warning=FALLBACK_WARNING, prompt_identity=prompt_identity
        )


def compose_rule_deductions(
    *,
    config: dict[str, Any],
    dimension_output: dict[str, Any],
) -> dict[str, Any]:
    """Convert rule hits into weighted point deductions for the aggregator.

    For each dimension: ``dimension_score=max(0, 100-sum(hit deductions))``.
    The lost percentage is then applied to that dimension's normalized share of
    its group's effective ``dimension_max`` slice.  This preserves the existing
    track/group weighting while replacing subjective grades with explicit rules.
    """
    validate_subcategory_dimensions(config)
    dimensions, rules_by_dimension = _configured_rules(config)
    normalized = normalize_dimension_deduction_output(dimension_output, config)
    hit_by_key = {
        key: value["hit_rules"]
        for key, value in normalized["dimensions"].items()
    }

    non_empty_groups: list[tuple[str, dict[str, Any]]] = []
    for group_name in ("common_group", "specific_group"):
        group = config.get(group_name)
        schema = group.get("schema_definition") if isinstance(group, dict) else None
        group_dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
        if isinstance(group_dimensions, list) and group_dimensions:
            non_empty_groups.append((group_name, group))
    weight_total = sum(float(group["group_weight"]) for _, group in non_empty_groups)
    if weight_total <= 0:
        raise DimensionDeductionBridgeError(
            "group_weight_invalid", "非空维度组 group_weight 之和必须大于0"
        )

    deductions: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    dimension_max = float(config["dimension_max"])
    for group_name, group in non_empty_groups:
        schema_dimensions = group["schema_definition"]["dimensions"]
        effective_max = float(group["group_weight"]) / weight_total * dimension_max
        try:
            weight_scale, weight_mode = resolve_dimension_weight_scale(
                schema_dimensions, effective_max
            )
        except DimensionGradeBridgeError as exc:
            raise DimensionDeductionBridgeError(exc.code, str(exc)) from exc
        for dimension in schema_dimensions:
            key = dimension["key"]
            configured_rules = rules_by_dimension[key]
            hits = hit_by_key[key]
            raw_rule_deduction = sum(
                configured_rules[hit["rule_id"]].deduction for hit in hits
            )
            applied_rule_deduction = min(100.0, float(raw_rule_deduction))
            dimension_score = max(0.0, 100.0 - applied_rule_deduction)
            share = float(dimension["weight"]) * weight_scale
            point_deduction = round(share * applied_rule_deduction / 100.0, 4)
            deductions[key] = point_deduction
            evidence[key] = {
                "group": group_name,
                "share": round(share, 6),
                "weight_mode": weight_mode,
                "raw_rule_deduction": float(raw_rule_deduction),
                "applied_rule_deduction": applied_rule_deduction,
                "dimension_score": dimension_score,
                "point_deduction": point_deduction,
                "hit_rules": hits,
            }

    return {
        "composition_version": RULE_COMPOSITION_VERSION,
        "mode": "rule_deduction",
        "sub_category_key": config["sub_category_key"],
        "dimension_max": dimension_max,
        "dimension_deductions": dict(normalized["dimensions"]),
        "deductions": deductions,
        "evidence": evidence,
        "warning": dimension_output.get("warning"),
        "overall_note": normalized.get("overall_note", ""),
        "prompt_identity": dimension_output.get("prompt_identity"),
    }


def extract_dimension_deduction_rules(
    subcategory_dimensions: dict[str, Any],
) -> dict[str, Any]:
    """Build the persisted rule-only mirror from full dimension configs."""
    extracted: dict[str, Any] = {}
    for track_key, config in subcategory_dimensions.items():
        track: dict[str, Any] = {}
        for group_name in ("common_group", "specific_group"):
            group = config.get(group_name) if isinstance(config, dict) else None
            schema = group.get("schema_definition") if isinstance(group, dict) else None
            dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
            if not isinstance(dimensions, list):
                continue
            track[group_name] = {
                dimension["key"]: list(dimension.get("deduction_rules") or [])
                for dimension in dimensions
            }
        extracted[track_key] = track
    return extracted
