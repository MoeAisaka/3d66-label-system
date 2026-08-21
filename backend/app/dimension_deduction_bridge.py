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
    BonusRule,
    DeductionRule,
    DimensionDeductionOutput,
    dimension_rule_mode,
    validate_dimension_deduction_cap,
)
from .b_aesthetic_foundation import normalize_b_aesthetic_foundation
from .dimension_composition import validate_subcategory_dimensions
from .dimension_grade_bridge import (
    DimensionGradeBridgeError,
    resolve_dimension_weight_scale,
)


DEDUCTION_BRIDGE_VERSION = "dimension-deduction-bridge-v2"
BONUS_CAP_BRIDGE_VERSION = "dimension-deduction-bridge-v3-bonus-cap"
DEDUCTION_PROMPT_TEMPLATE_VERSION = "dimension-deduction-prompt-v1"
BONUS_CAP_PROMPT_TEMPLATE_VERSION = "dimension-deduction-prompt-v2-bonus-cap"
RULE_COMPOSITION_VERSION = "dimension-rule-composition-v1"
RULE_COMPOSITION_V2 = "dimension-rule-composition-v2-bonus-cap"
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
    """Backward-compatible probe for either explicit rule-scoring mode."""
    return rule_scoring_mode(config) in {"deduction_v1", "bonus_cap_v2"}


def rule_scoring_mode(config: Any) -> str:
    """Return the validated raw-field mode without mutating the contract."""
    dimensions = dimension_definitions(config)
    if not dimensions:
        return "grade_fallback"
    modes = {dimension_rule_mode(dimension) for dimension in dimensions}
    if len(modes) != 1:
        raise DimensionDeductionBridgeError(
            "dimension_rule_mode_mixed", "同一赛道的维度规则模式不一致"
        )
    return next(iter(modes))


def empty_deduction_output(
    config: dict[str, Any], *, warning: str | None = None,
    prompt_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    mode = rule_scoring_mode(config)
    bonus_cap = mode == "bonus_cap_v2"
    return {
        "bridge_version": (
            BONUS_CAP_BRIDGE_VERSION if bonus_cap else DEDUCTION_BRIDGE_VERSION
        ),
        "prompt_identity": prompt_identity,
        "dimensions": {
            dimension["key"]: (
                {"hit_rules": [], "hit_bonus_rules": []}
                if bonus_cap
                else {"hit_rules": []}
            )
            for dimension in dimension_definitions(config)
        },
        "overall_note": "",
        "aesthetic_score": None,
        "aesthetic_evidence": [],
        "aesthetic_confidence": None,
        "warning": warning,
        "raw_payload": None,
    }


def _configured_rules(
    config: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, DeductionRule]],
    dict[str, dict[str, BonusRule]],
    str,
]:
    dimensions = dimension_definitions(config)
    if not dimensions:
        raise DimensionDeductionBridgeError(
            "dimensions_empty", "规则扣分调用B至少需要一个维度"
        )
    mode = rule_scoring_mode(config)
    if mode == "grade_fallback":
        raise DimensionDeductionBridgeError(
            "rule_contract_missing", "当前维度合同应走旧 grade fallback"
        )
    rules_by_dimension: dict[str, dict[str, DeductionRule]] = {}
    bonus_rules_by_dimension: dict[str, dict[str, BonusRule]] = {}
    for dimension in dimensions:
        raw_rules = dimension.get("deduction_rules")
        if not isinstance(raw_rules, list) or (mode == "deduction_v1" and not raw_rules):
            raise DimensionDeductionBridgeError(
                "deduction_rules_missing",
                f"维度 {dimension['key']} 缺少 deduction_rules，应走旧 grade fallback",
            )
        rules_by_dimension[dimension["key"]] = {
            rule.rule_id: rule
            for rule in (DeductionRule.model_validate(item) for item in raw_rules)
        }
        raw_bonus_rules = dimension.get("bonus_rules", [])
        if mode == "bonus_cap_v2" and not isinstance(raw_bonus_rules, list):
            raise DimensionDeductionBridgeError(
                "bonus_rules_missing", f"维度 {dimension['key']} 缺少 bonus_rules"
            )
        bonus_rules_by_dimension[dimension["key"]] = {
            rule.rule_id: rule
            for rule in (BonusRule.model_validate(item) for item in raw_bonus_rules)
        }
    return dimensions, rules_by_dimension, bonus_rules_by_dimension, mode


def normalize_dimension_deduction_output(
    payload: Any,
    config: dict[str, Any],
    *,
    require_foundation: bool | None = None,
) -> dict[str, Any]:
    """Validate provider JSON against the frozen configured dimensions/rules."""
    dimensions, rules_by_dimension, bonus_rules_by_dimension, mode = _configured_rules(config)
    if not isinstance(payload, dict):
        raise DimensionDeductionBridgeError(
            "response_not_object", "调用B响应必须是 JSON 对象"
        )
    foundation: dict[str, Any] | None = None
    if require_foundation is None:
        require_foundation = isinstance(config.get("b_aesthetic_foundation"), dict)
    if require_foundation and isinstance(config.get("b_aesthetic_foundation"), dict):
        foundation = normalize_b_aesthetic_foundation(
            {
                "aesthetic_score": payload.get("aesthetic_score"),
                "overall_evidence": payload.get("aesthetic_evidence")
                or ([payload.get("overall_note")] if payload.get("overall_note") else []),
                "confidence": payload.get("aesthetic_confidence"),
            }
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
        if mode == "bonus_cap_v2" and (
            "hit_rules" not in raw or "hit_bonus_rules" not in raw
        ):
            raise DimensionDeductionBridgeError(
                "dimension_output_invalid",
                "bonus-cap-v2 维度输出必须同时包含 hit_rules 与 hit_bonus_rules",
            )
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
        configured_bonus = bonus_rules_by_dimension.get(item.dimension_key)
        if configured is None or configured_bonus is None:
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
        for hit in item.hit_bonus_rules:
            if hit.rule_id not in configured_bonus:
                raise DimensionDeductionBridgeError(
                    "rule_unknown",
                    f"维度 {item.dimension_key} 返回未知 bonus rule_id：{hit.rule_id}",
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
        "bridge_version": (
            BONUS_CAP_BRIDGE_VERSION
            if mode == "bonus_cap_v2"
            else DEDUCTION_BRIDGE_VERSION
        ),
        "dimensions": {
            key: (
                {
                    "hit_rules": parsed[key].model_dump()["hit_rules"],
                    "hit_bonus_rules": parsed[key].model_dump()["hit_bonus_rules"],
                }
                if mode == "bonus_cap_v2"
                else {"hit_rules": parsed[key].model_dump()["hit_rules"]}
            )
            for key in expected_keys
        },
        "overall_note": str(payload.get("overall_note") or "").strip(),
        **(
            {
                "aesthetic_score": foundation["aesthetic_score"],
                "aesthetic_evidence": foundation["evidence"],
                "aesthetic_confidence": foundation["confidence"],
            }
            if foundation is not None
            else {}
        ),
        "warning": None,
    }


def build_dimension_deduction_prompt(
    config: dict[str, Any], *, precheck: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Build the Chinese rule-by-rule calling-B prompt from frozen config."""
    dimensions, _, _, mode = _configured_rules(config)
    bonus_cap = mode == "bonus_cap_v2"
    requires_foundation = isinstance(config.get("b_aesthetic_foundation"), dict)
    system = (
        "你是灵感素材质量核验专家。"
        + (
            "先输出0-100的 aesthetic_score 作为基础美感分，再逐条判断合同规则命中；"
            "基础分不得输出最终等级。"
            if requires_foundation
            else "你只做规则命中判断，不打1-5分、不输出总分或等级。"
        )
        + (
            "逐维度分别检查每条扣分规则与加分规则；只返回确有视觉证据的命中项。"
            if bonus_cap
            else "逐维度检查每条扣分规则；只返回确有视觉证据的命中项。"
        )
        + "每条命中必须给出独立、可定位的中文证据和 high/medium/low 置信度。严格输出 JSON。"
    )
    rule_blocks: list[str] = []
    for dimension in dimensions:
        deduction_lines = [
            f"- {rule['rule_id']}: {rule['description']}（扣 {rule['deduction']} 分）"
            for rule in dimension["deduction_rules"]
        ]
        bonus_lines = [
            f"- {rule['rule_id']}: {rule['description']}（加 {rule['bonus']} 分）"
            for rule in dimension.get("bonus_rules", [])
        ]
        rules_text = "扣分规则：\n" + ("\n".join(deduction_lines) or "- 无")
        if bonus_cap:
            rules_text += "\n加分规则：\n" + ("\n".join(bonus_lines) or "- 无")
        rule_blocks.append(
            f"# 维度：{dimension.get('label') or dimension['key']}（{dimension['key']}）\n"
            + rules_text
        )
    response_contract = {
        **(
            {
                "aesthetic_score": 88,
                "aesthetic_evidence": ["整体可见美感证据"],
                "aesthetic_confidence": 0.8,
            }
            if requires_foundation
            else {}
        ),
        "dimensions": {
            dimension["key"]: {
                "hit_rules": [
                    {
                        "rule_id": "命中的规则ID",
                        "confidence": "high|medium|low",
                        "evidence": "图中具体证据",
                    }
                ],
                **(
                    {
                        "hit_bonus_rules": [
                            {
                                "rule_id": "命中的加分规则ID",
                                "confidence": "high|medium|low",
                                "evidence": "图中具体证据",
                            }
                        ]
                    }
                    if bonus_cap
                    else {}
                ),
            }
            for dimension in dimensions
        },
        "overall_note": "整体说明",
    }
    user = (
        "\n\n".join(rule_blocks)
        + "\n\n调用A预检字段：\n"
        + json.dumps(precheck or {}, ensure_ascii=False, sort_keys=True)
        + (
            "\n\n输出结构（未命中时 hit_rules 与 hit_bonus_rules 必须为空数组）：\n"
            if bonus_cap
            else "\n\n输出结构（未命中时 hit_rules 必须为空数组）：\n"
        )
        + json.dumps(response_contract, ensure_ascii=False)
    )
    return system, user


def _deduction_prompt_identity(
    system_prompt: str, user_prompt: str, *, bonus_cap: bool = False
) -> dict[str, str]:
    return {
        "template_version": (
            BONUS_CAP_PROMPT_TEMPLATE_VERSION
            if bonus_cap
            else DEDUCTION_PROMPT_TEMPLATE_VERSION
        ),
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
    _, _, _, mode = _configured_rules(contract)  # local corruption fails closed
    system_prompt, user_prompt = build_dimension_deduction_prompt(
        contract, precheck=precheck
    )
    prompt_identity = _deduction_prompt_identity(
        system_prompt, user_prompt, bonus_cap=mode == "bonus_cap_v2"
    )
    try:
        response = await client.chat_json(
            system_prompt,
            user_prompt,
            image_path=image,
            mime_type=mime_type,
        )
        parsed = response.parsed if hasattr(response, "parsed") else response
        normalized = normalize_dimension_deduction_output(
            parsed, contract, require_foundation=True
        )
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
    require_foundation: bool = False,
) -> dict[str, Any]:
    """Convert rule hits into weighted point deductions for the aggregator.

    For each dimension: ``dimension_score=max(0, 100-sum(hit deductions))``.
    The lost percentage is then applied to that dimension's normalized share of
    its group's effective ``dimension_max`` slice.  This preserves the existing
    track/group weighting while replacing subjective grades with explicit rules.
    """
    if rule_scoring_mode(config) == "bonus_cap_v2":
        return compose_rule_scores(
            config=config,
            dimension_output=dimension_output,
            require_foundation=require_foundation,
        )
    validate_subcategory_dimensions(config)
    dimensions, rules_by_dimension, _, _ = _configured_rules(config)
    normalized = normalize_dimension_deduction_output(
        dimension_output,
        config,
        require_foundation=require_foundation,
    )
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
            deduction_cap = float(dimension.get("dimension_deduction_cap", 100))
            applied_rule_deduction = min(
                deduction_cap, float(raw_rule_deduction)
            )
            dimension_score = max(0.0, 100.0 - applied_rule_deduction)
            share = float(dimension["weight"]) * weight_scale
            point_deduction = round(share * applied_rule_deduction / 100.0, 4)
            deductions[key] = point_deduction
            evidence[key] = {
                "group": group_name,
                "share": round(share, 6),
                "weight_mode": weight_mode,
                "raw_rule_deduction": float(raw_rule_deduction),
                "dimension_deduction_cap": deduction_cap,
                "applied_rule_deduction": applied_rule_deduction,
                "dimension_score": dimension_score,
                "cap_applied": applied_rule_deduction < raw_rule_deduction,
                "cap_reason": (
                    "维度累计扣分按 "
                    f"dimension_deduction_cap={float(dimension.get('dimension_deduction_cap', 100)):g} 封顶"
                    if applied_rule_deduction < raw_rule_deduction
                    else None
                ),
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
        "aesthetic_score": normalized.get("aesthetic_score"),
        "aesthetic_evidence": normalized.get("aesthetic_evidence") or [],
        "aesthetic_confidence": normalized.get("aesthetic_confidence"),
        "overall_note": normalized.get("overall_note", ""),
        "prompt_identity": dimension_output.get("prompt_identity"),
    }


def compose_rule_scores(
    *,
    config: dict[str, Any],
    dimension_output: dict[str, Any],
    require_foundation: bool = False,
) -> dict[str, Any]:
    """Compose explicit deduction/bonus hits into capped dimension scores."""
    validate_subcategory_dimensions(config)
    (
        _dimensions,
        rules_by_dimension,
        bonus_rules_by_dimension,
        mode,
    ) = _configured_rules(config)
    if mode != "bonus_cap_v2":
        raise DimensionDeductionBridgeError(
            "bonus_cap_mode_required", "compose_rule_scores 仅接受 bonus-cap-v2 合同"
        )
    normalized = normalize_dimension_deduction_output(
        dimension_output,
        config,
        require_foundation=require_foundation,
    )

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
            hits = normalized["dimensions"][key]["hit_rules"]
            bonus_hits = normalized["dimensions"][key]["hit_bonus_rules"]
            raw_rule_deduction = sum(
                rules_by_dimension[key][hit["rule_id"]].deduction for hit in hits
            )
            raw_rule_bonus = sum(
                bonus_rules_by_dimension[key][hit["rule_id"]].bonus
                for hit in bonus_hits
            )
            deduction_cap = float(dimension.get("dimension_deduction_cap", 100))
            applied_rule_deduction = min(
                deduction_cap, float(raw_rule_deduction)
            )
            raw_dimension_score = (
                100.0 - float(applied_rule_deduction) + float(raw_rule_bonus)
            )
            score_before_cap = max(raw_dimension_score, 0.0)
            cap = float(dimension["dimension_score_cap"])
            dimension_score = min(score_before_cap, cap)
            share = float(dimension["weight"]) * weight_scale
            point_contribution = round(share * dimension_score / 100.0, 4)
            point_deduction = round(share - point_contribution, 4)
            deductions[key] = point_deduction
            evidence[key] = {
                "group": group_name,
                "share": round(share, 6),
                "weight_mode": weight_mode,
                "raw_rule_deduction": float(raw_rule_deduction),
                "dimension_deduction_cap": deduction_cap,
                "applied_rule_deduction": float(applied_rule_deduction),
                "raw_rule_bonus": float(raw_rule_bonus),
                "applied_rule_bonus": float(raw_rule_bonus),
                "raw_dimension_score": raw_dimension_score,
                "score_before_cap": score_before_cap,
                "dimension_score_cap": cap,
                "dimension_score": dimension_score,
                "cap_applied": (
                    applied_rule_deduction < raw_rule_deduction
                    or dimension_score < score_before_cap
                ),
                "cap_reason": (
                    (
                        f"维度累计扣分按 dimension_deduction_cap={deduction_cap:g} 封顶"
                        if applied_rule_deduction < raw_rule_deduction
                        else f"维度分数按 dimension_score_cap={cap:g} 封顶"
                    )
                    if (
                        applied_rule_deduction < raw_rule_deduction
                        or dimension_score < score_before_cap
                    )
                    else None
                ),
                "point_contribution": point_contribution,
                "point_deduction": point_deduction,
                "hit_rules": hits,
                "hit_bonus_rules": bonus_hits,
            }

    return {
        "composition_version": RULE_COMPOSITION_V2,
        "mode": "rule_deduction",
        "rule_mode": "bonus_cap_v2",
        "sub_category_key": config["sub_category_key"],
        "dimension_max": dimension_max,
        "dimension_deductions": dict(normalized["dimensions"]),
        "deductions": deductions,
        "evidence": evidence,
        "warning": dimension_output.get("warning"),
        "aesthetic_score": normalized.get("aesthetic_score"),
        "aesthetic_evidence": normalized.get("aesthetic_evidence") or [],
        "aesthetic_confidence": normalized.get("aesthetic_confidence"),
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


def extract_dimension_scoring_rules(
    subcategory_dimensions: dict[str, Any],
) -> dict[str, Any]:
    """Build a complete executable rule mirror for a frozen contract.

    The historical ``extract_dimension_deduction_rules`` shape intentionally
    remains a rule-id-to-list mapping for old migrations and seed rows.  New
    contract-bound revisions use this richer shape so positive rules and
    per-dimension caps cannot disappear between candidate creation and replay.
    """
    extracted: dict[str, Any] = {}
    for track_key, config in subcategory_dimensions.items():
        track: dict[str, Any] = {}
        for group_name in ("common_group", "specific_group"):
            group = config.get(group_name) if isinstance(config, dict) else None
            schema = group.get("schema_definition") if isinstance(group, dict) else None
            dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
            if not isinstance(dimensions, list):
                continue
            track[group_name] = {}
            for dimension in dimensions:
                if not isinstance(dimension, dict):
                    continue
                rule_set: dict[str, Any] = {
                    "deduction_rules": list(dimension.get("deduction_rules") or [])
                }
                if "bonus_rules" in dimension:
                    rule_set["bonus_rules"] = list(dimension.get("bonus_rules") or [])
                if "dimension_score_cap" in dimension:
                    rule_set["dimension_score_cap"] = dimension["dimension_score_cap"]
                if "dimension_deduction_cap" in dimension:
                    validate_dimension_deduction_cap(
                        dimension["dimension_deduction_cap"],
                        dimension_key=str(dimension.get("key") or "dimension"),
                    )
                    rule_set["dimension_deduction_cap"] = dimension[
                        "dimension_deduction_cap"
                    ]
                track[group_name][dimension["key"]] = rule_set
        extracted[track_key] = track
    return extracted
