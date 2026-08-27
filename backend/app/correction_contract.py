"""Canonical correction-contract metadata and value validation."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from numbers import Real
from typing import Any, Mapping


_HAN_PATTERN = re.compile(r"[\u3400-\u9fff]")
_LAYER_ORDER = {"A": 0, "B": 1, "V3": 2}
SUPPORTED_CORRECTION_NODE_TYPES = frozenset(
    {
        "enum",
        "enumeration",
        "integer",
        "int",
        "number",
        "float",
        "decimal",
        "boolean",
        "bool",
        "text",
        "string",
        "list",
        "array",
        "object",
        "json_object",
        "rule_hit",
        "rule_judgement",
        "rule_judgment",
    }
)
_NODE_FIELDS = {
    "node_key",
    "layer",
    "path",
    "order",
    "label",
    "description",
    "type",
    "semantic_version",
    "compatibility_key",
    "required",
    "evidence",
    "options",
    "allowed_values",
    "values",
    "min",
    "max",
    "minimum",
    "maximum",
    "min_value",
    "max_value",
    "recompute_ref",
    "metadata",
}


class ContractValidationError(ValueError):
    """Stable, structured validation failure for correction contracts."""

    def __init__(self, code: str, message: str, fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.fields = list(fields or [])


def _canonical_payload(contract: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(contract))
    payload.pop("contract_hash", None)
    return payload


def correction_contract_hash(contract: Mapping[str, Any]) -> str:
    """Hash canonical JSON while excluding the hash field itself."""
    encoded = json.dumps(
        _canonical_payload(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_correction_contract(
    raw: Mapping[str, Any], *, category_key: str
) -> dict[str, Any]:
    """Return a deterministic, detached contract representation."""
    if not isinstance(raw, Mapping):
        raise ContractValidationError(
            "CORRECTION_CONTRACT_INVALID",
            "纠偏合同必须是对象",
            ["contract"],
        )
    normalized = deepcopy(dict(raw))
    normalized["category_key"] = category_key
    nodes = normalized.get("nodes", [])
    if not isinstance(nodes, list):
        raise ContractValidationError(
            "CORRECTION_CONTRACT_INVALID",
            "纠偏合同 nodes 必须是数组",
            ["nodes"],
        )

    canonical_nodes: list[dict[str, Any]] = []
    for item in nodes:
        if not isinstance(item, Mapping):
            canonical_nodes.append(deepcopy(item))
            continue
        node = deepcopy(dict(item))
        unknown = {
            key: value for key, value in node.items() if key not in _NODE_FIELDS
        }
        existing_metadata = node.get("metadata")
        if existing_metadata is not None and not isinstance(existing_metadata, Mapping):
            existing_metadata = {"value": deepcopy(existing_metadata)}
        metadata = dict(existing_metadata or {})
        metadata.update(unknown)
        if metadata:
            node["metadata"] = metadata
        for key in unknown:
            node.pop(key, None)
        canonical_nodes.append(node)

    def sort_key(node: Mapping[str, Any]) -> tuple[int, str, int, str]:
        layer = node.get("layer")
        path = node.get("path")
        order = node.get("order", 0)
        return (
            _LAYER_ORDER.get(layer, len(_LAYER_ORDER)),
            str(path or ""),
            order if isinstance(order, int) and not isinstance(order, bool) else 0,
            str(node.get("node_key", "")),
        )

    normalized["nodes"] = sorted(canonical_nodes, key=sort_key)
    return normalized


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_chinese(value: Any) -> bool:
    return _nonempty_string(value) and bool(_HAN_PATTERN.search(value))


def _bounds(node: Mapping[str, Any]) -> tuple[Any, Any]:
    lower = next(
        (node[key] for key in ("minimum", "min", "min_value") if key in node),
        None,
    )
    upper = next(
        (node[key] for key in ("maximum", "max", "max_value") if key in node),
        None,
    )
    return lower, upper


def _is_numeric_type(node_type: str) -> bool:
    return (
        node_type in {"integer", "int", "number", "float", "decimal"}
        or node_type.startswith("integer_")
        or node_type.startswith("number_")
        or node_type.startswith("float_")
    )


def validate_correction_contract(contract: Mapping[str, Any]) -> list[str]:
    """Return human-readable field failures without mutating the contract."""
    errors: list[str] = []
    if not isinstance(contract, Mapping):
        return ["contract 必须是对象"]
    if not _nonempty_string(contract.get("contract_version")):
        errors.append("contract_version 必须是非空字符串")
    if not _nonempty_string(contract.get("category_key")):
        errors.append("category_key 必须是非空字符串")
    nodes = contract.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return errors + ["nodes 必须是非空数组"]

    seen: set[str] = set()
    for index, node in enumerate(nodes):
        prefix = f"nodes[{index}]"
        if not isinstance(node, Mapping):
            errors.append(f"{prefix} 必须是对象")
            continue
        node_key = node.get("node_key")
        field_prefix = f"{prefix}.{node_key}" if _nonempty_string(node_key) else prefix
        if not _nonempty_string(node_key):
            errors.append(f"{field_prefix}.node_key 缺失")
        elif node_key in seen:
            errors.append(f"{field_prefix}.node_key 重复")
        else:
            seen.add(node_key)
        if node.get("layer") not in _LAYER_ORDER:
            errors.append(f"{field_prefix}.layer 必须是 A、B 或 V3")
        for field in ("label", "description"):
            if not _has_chinese(node.get(field)):
                errors.append(f"{field_prefix}.{field} 必须是非空中文描述")
        for field in ("type", "semantic_version", "compatibility_key"):
            if not _nonempty_string(node.get(field)):
                errors.append(f"{field_prefix}.{field} 缺失")
        if not isinstance(node.get("required"), bool):
            errors.append(f"{field_prefix}.required 必须显式为布尔值")
        evidence = node.get("evidence")
        if not isinstance(evidence, Mapping) or not _nonempty_string(
            evidence.get("description")
        ):
            errors.append(f"{field_prefix}.evidence.description 缺失")
        node_type = str(node.get("type", "")).lower()
        if (
            node_type
            and node_type not in SUPPORTED_CORRECTION_NODE_TYPES
            and not _is_numeric_type(node_type)
        ):
            errors.append(f"{field_prefix}.type 不受支持")
        options = node.get("options", node.get("allowed_values", node.get("values")))
        if node_type in {"enum", "enumeration"} and (
            not isinstance(options, list) or not options
        ):
            errors.append(f"{field_prefix}.options 必须是非空数组")
        if _is_numeric_type(node_type):
            lower, upper = _bounds(node)
            if lower is None or upper is None:
                errors.append(f"{field_prefix} 必须声明数值上下界")
            elif (
                not isinstance(lower, Real)
                or isinstance(lower, bool)
                or not isinstance(upper, Real)
                or isinstance(upper, bool)
            ):
                errors.append(f"{field_prefix} 数值上下界必须是数字")
            elif lower > upper:
                errors.append(f"{field_prefix} 数值上下界无效")
        if node.get("layer") == "V3" and not _nonempty_string(node.get("recompute_ref")):
            errors.append(f"{field_prefix}.recompute_ref 缺失")
    contract_hash = contract.get("contract_hash")
    if contract_hash is not None and contract_hash != correction_contract_hash(contract):
        errors.append("contract_hash 与规范化合同不一致")
    return errors


def assert_correction_contract_complete(contract: Mapping[str, Any]) -> None:
    errors = validate_correction_contract(contract)
    if errors:
        fields: list[str] = []
        for error in errors:
            location = error.split(" ", 1)[0]
            parts = location.split(".")
            if len(parts) >= 3 and parts[0].startswith("nodes["):
                fields.append(".".join(parts[1:-1]))
            else:
                fields.append(location)
        raise ContractValidationError(
            "CORRECTION_CONTRACT_INCOMPLETE",
            "纠偏合同不完整：" + "；".join(errors),
            fields,
        )


def validate_node_value(node: Mapping[str, Any], value: Any) -> None:
    node_key = str(node.get("node_key", "node"))
    if value is None and node.get("required") is False:
        return
    node_type = str(node.get("type", "")).lower()
    options = node.get("options", node.get("allowed_values", node.get("values")))
    valid = True
    if node_type in {"enum", "enumeration"}:
        valid = isinstance(options, list) and value in options
    elif node_type in {"integer", "int"} or node_type.startswith("integer_"):
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif _is_numeric_type(node_type):
        valid = isinstance(value, Real) and not isinstance(value, bool)
    elif node_type in {"boolean", "bool"}:
        valid = isinstance(value, bool)
    elif node_type in {"text", "string"}:
        valid = isinstance(value, str)
    elif node_type in {"list", "array"}:
        valid = isinstance(value, list)
    elif node_type in {"object", "json_object"}:
        valid = isinstance(value, Mapping)
    elif node_type in {"rule_hit", "rule_judgement", "rule_judgment"}:
        valid = (
            isinstance(value, Mapping)
            and _nonempty_string(value.get("rule_id"))
            and value.get("confidence") in {"high", "medium", "low"}
            and _nonempty_string(value.get("evidence"))
        )
    if valid and _is_numeric_type(node_type):
        lower, upper = _bounds(node)
        valid = (lower is None or value >= lower) and (upper is None or value <= upper)
    if not valid:
        raise ContractValidationError(
            "CORRECTION_NODE_VALUE_INVALID",
            f"节点 {node_key} 的值不符合合同约束",
            [node_key],
        )


def inherit_correction_node(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> dict[str, Any]:
    result = deepcopy(dict(current))
    compatible = (
        isinstance(previous, Mapping)
        and previous.get("node_key") == current.get("node_key")
        and previous.get("type") == current.get("type")
        and previous.get("semantic_version") == current.get("semantic_version")
        and previous.get("compatibility_key") == current.get("compatibility_key")
    )
    if compatible:
        for field in ("human_value", "reason", "evidence"):
            if field in previous:
                result[field] = deepcopy(previous[field])
        result["inheritance"] = {"status": "inherited"}
    elif previous is None:
        result["inheritance"] = {"status": "new"}
    else:
        result["inheritance"] = {"status": "changed"}
    return result


def _fallback_label(prefix: str, key: Any) -> str:
    return f"{prefix}{str(key or '未命名')}"


def _source_nodes(
    source: Mapping[str, Any] | None,
    *,
    layer: str,
    path_prefix: str,
    label_prefix: str,
) -> list[dict[str, Any]]:
    if not isinstance(source, Mapping):
        return []
    raw_nodes = source.get("nodes")
    if isinstance(raw_nodes, list):
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_nodes):
            if not isinstance(raw, Mapping):
                continue
            node = deepcopy(dict(raw))
            key = str(node.get("node_key") or node.get("key") or f"node_{index}")
            node["node_key"] = key
            node.setdefault("layer", layer)
            node.setdefault("path", f"{path_prefix}.{key}")
            node.setdefault("order", index)
            node.setdefault("label", node.get("name") or _fallback_label(label_prefix, key))
            node.setdefault("description", f"冻结{node['label']}的纠偏判断")
            node.setdefault("type", node.get("value_type") or "text")
            node.setdefault("semantic_version", "1")
            node.setdefault("compatibility_key", key)
            node.setdefault("required", False)
            node.setdefault("evidence", {"description": f"请提供{node['label']}的图片证据"})
            result.append(node)
        return result
    raw_fields = source.get("fields")
    if not isinstance(raw_fields, list):
        return []
    result = []
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("key") or raw.get("field_key") or f"field_{index}")
        label = str(raw.get("label") or raw.get("name") or _fallback_label(label_prefix, key))
        node = {
            **deepcopy(dict(raw)),
            "node_key": f"{path_prefix}.{key}",
            "layer": layer,
            "path": f"{path_prefix}.{key}",
            "order": index,
            "label": label,
            "description": str(raw.get("description") or f"冻结{label}的纠偏判断"),
            "type": str(raw.get("type") or raw.get("value_type") or "text"),
            "semantic_version": str(raw.get("semantic_version") or "1"),
            "compatibility_key": str(raw.get("compatibility_key") or key),
            "required": bool(raw.get("required", False)),
            "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), Mapping) else {"description": f"请提供{label}的图片证据"},
        }
        result.append(node)
    return result


_PRODUCTION_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "title": {
        "label": "素材标题",
        "description": "用于检索与推荐的素材标题",
        "type": "text",
        "required": True,
    },
    "seotitle": {
        "label": "搜索标题",
        "description": "用于搜索召回的标准化标题",
        "type": "text",
        "required": True,
    },
    "category": {
        "label": "素材类目",
        "description": "素材所属的业务类目",
        "type": "text",
        "required": True,
    },
    "style": {
        "label": "素材风格",
        "description": "素材呈现的主要风格与视觉方向",
        "type": "text",
        "required": True,
    },
    "tags": {
        "label": "素材标签",
        "description": "用于检索与推荐的素材标签列表",
        "type": "list",
        "required": True,
    },
    "cons": {
        "label": "素材缺点",
        "description": "素材需要降权或人工关注的缺点",
        "type": "text",
        "required": True,
    },
    "design": {
        "label": "设计说明",
        "description": "素材的设计意图与表现说明",
        "type": "text",
        "required": True,
    },
    "score": {
        "label": "素材分数",
        "description": "调用 A 输出的标准化素材分数",
        "type": "integer",
        "min": 0,
        "max": 100,
        "required": True,
    },
    "reason": {
        "label": "过滤原因",
        "description": "触发过滤或降权判断的原因列表",
        "type": "list",
        "required": True,
        "options": [
            "是截图",
            "有大面积文字说明",
            "是多拼图",
            "有二维码",
            "是随手拍",
            "是颠倒图",
        ],
    },
    "image_defects": {
        "label": "图片缺陷",
        "description": "图片是否存在水印等明确缺陷",
        "type": "enum",
        "options": ["", "有水印"],
        "required": True,
    },
    "trait": {
        "label": "素材媒介",
        "description": "素材属于实拍、三维效果图、人工智能图或其它媒介",
        "type": "enum",
        "options": ["AI图", "实景照片", "3D数字效果图", "其它"],
        "required": True,
    },
}


def _chinese_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text and _has_chinese(text) else fallback


def _node_evidence(label: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "description": f"请提供{label}的图片或人工判断证据",
        "required": required,
    }


def _redline_reason_options(
    v3_bundle: Mapping[str, Any] | None,
) -> list[str] | None:
    if not isinstance(v3_bundle, Mapping):
        return None
    contract = v3_bundle.get("contract")
    policy = contract.get("redline_policy") if isinstance(contract, Mapping) else None
    if not isinstance(policy, Mapping):
        return None
    options: list[str] = []
    for rule in policy.get("rules") or []:
        if not isinstance(rule, Mapping):
            continue
        for value in rule.get("match_any") or []:
            if isinstance(value, str) and value.strip() and value.strip() not in options:
                options.append(value.strip())
    return options


def _production_field_nodes(
    source: Mapping[str, Any] | None,
    *,
    v3_bundle: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build call-A nodes from an explicit or compatibility production contract."""

    raw_items: list[Any] = []
    if isinstance(source, Mapping):
        candidate = source.get("fields")
        if not isinstance(candidate, list):
            candidate = source.get("nodes")
        if isinstance(candidate, list):
            raw_items = candidate
    if not raw_items:
        raw_items = [
            {"key": key, **deepcopy(spec)}
            for key, spec in _PRODUCTION_FIELD_SPECS.items()
        ]

    nodes: list[dict[str, Any]] = []
    frozen_reason_options = _redline_reason_options(v3_bundle)
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("key") or raw.get("field_key") or "").strip()
        raw_node_key = str(raw.get("node_key") or "").strip()
        if raw_node_key.startswith("call_a."):
            node_key = raw_node_key
            field_key = raw_node_key.split(".", 1)[1]
        elif key:
            field_key = key.removeprefix("call_a.")
            node_key = f"call_a.{field_key}"
        else:
            continue
        fallback = _PRODUCTION_FIELD_SPECS.get(field_key, {})
        label = _chinese_text(
            raw.get("label") or raw.get("name") or fallback.get("label"),
            f"生产字段 {field_key}",
        )
        node: dict[str, Any] = {
            **deepcopy(dict(raw)),
            "node_key": node_key,
            "layer": "A",
            "path": str(raw.get("path") or node_key),
            "order": index,
            "label": label,
            "description": _chinese_text(
                raw.get("description") or fallback.get("description"),
                f"冻结{label}的人工判断",
            ),
            "type": str(
                raw.get("type")
                or raw.get("value_type")
                or fallback.get("type")
                or "text"
            ),
            "semantic_version": str(raw.get("semantic_version") or "1"),
            "compatibility_key": str(
                raw.get("compatibility_key") or f"production-field:{field_key}"
            ),
            "required": bool(raw.get("required", fallback.get("required", False))),
            "evidence": (
                deepcopy(raw["evidence"])
                if isinstance(raw.get("evidence"), Mapping)
                else _node_evidence(label)
            ),
            "metadata": {
                "node_type": "call_a_field",
                "field_key": field_key,
                **(
                    deepcopy(dict(raw.get("metadata")))
                    if isinstance(raw.get("metadata"), Mapping)
                    else {}
                ),
            },
        }
        for bound in ("options", "allowed_values", "values", "min", "max", "minimum", "maximum"):
            if bound not in node and bound in fallback:
                node[bound] = deepcopy(fallback[bound])
        if field_key == "reason" and frozen_reason_options is not None:
            node["options"] = list(frozen_reason_options)
        nodes.append(node)
    return nodes


def _dimension_rule_nodes(
    dimension_definition: Mapping[str, Any] | None,
    v3_bundle: Mapping[str, Any] | None,
    *,
    selected_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Flatten frozen dimension and subcategory rule definitions into B nodes."""

    definitions: list[tuple[Mapping[str, Any], bool]] = []

    def add_dimensions(value: Any, *, respect_selection: bool) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            if isinstance(item, Mapping):
                definitions.append((item, respect_selection))

    if isinstance(dimension_definition, Mapping):
        add_dimensions(
            dimension_definition.get("dimensions"),
            respect_selection=True,
        )

    if isinstance(v3_bundle, Mapping):
        subcategories = v3_bundle.get("subcategory_dimensions")
        if isinstance(subcategories, Mapping):
            for config in subcategories.values():
                if not isinstance(config, Mapping):
                    continue
                for group_name in ("common_group", "specific_group"):
                    group = config.get(group_name)
                    schema = group.get("schema_definition") if isinstance(group, Mapping) else None
                    if isinstance(schema, Mapping):
                        add_dimensions(
                            schema.get("dimensions"),
                            respect_selection=False,
                        )

    selected = set(selected_keys or [])
    seen: set[tuple[str, str, str]] = set()
    nodes: list[dict[str, Any]] = []
    for dimension, respect_selection in definitions:
        dimension_key = str(dimension.get("key") or "").strip()
        if (
            not dimension_key
            or (respect_selection and selected and dimension_key not in selected)
        ):
            continue
        dimension_label = _chinese_text(
            dimension.get("label") or dimension.get("name"),
            f"维度 {dimension_key}",
        )
        for rule_kind, source_key, path_key in (
            ("deduction", "deduction_rules", "hit_rules"),
            ("bonus", "bonus_rules", "hit_bonus_rules"),
        ):
            rules = dimension.get(source_key)
            if not isinstance(rules, list):
                continue
            for index, raw_rule in enumerate(rules):
                if not isinstance(raw_rule, Mapping):
                    continue
                rule_id = str(raw_rule.get("rule_id") or raw_rule.get("key") or "").strip()
                if not rule_id:
                    continue
                identity = (dimension_key, rule_kind, rule_id)
                if identity in seen:
                    continue
                seen.add(identity)
                description = _chinese_text(
                    raw_rule.get("description"),
                    f"{dimension_label}的{rule_id}规则判断",
                )
                label = f"{dimension_label}：{description}"
                nodes.append(
                    {
                        "node_key": f"call_b.{dimension_key}.{rule_id}",
                        "layer": "B",
                        "path": f"dimension.{dimension_key}.{path_key}.{rule_id}",
                        "order": len(nodes),
                        "label": label,
                        "description": description,
                        "type": "rule_hit",
                        "semantic_version": str(
                            raw_rule.get("semantic_version")
                            or dimension.get("semantic_version")
                            or "1"
                        ),
                        "compatibility_key": f"dimension-rule:{dimension_key}:{rule_kind}:{rule_id}",
                        "required": False,
                        "evidence": _node_evidence(label, required=True),
                        "metadata": {
                            "node_type": "dimension_rule",
                            "dimension_key": dimension_key,
                            "rule_id": rule_id,
                            "rule_kind": rule_kind,
                            "editable": True,
                        },
                    }
                )
    return nodes


def _hard_defect_options(v3_bundle: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Read the frozen hard-defect rule list so options follow the mechanism.

    Options come from the frozen contract rather than a frontend constant, so
    adding or removing a defect in a new mechanism version needs no code change.
    """
    if not isinstance(v3_bundle, Mapping):
        return []
    contract = v3_bundle.get("contract")
    modifiers = contract.get("common_modifiers") if isinstance(contract, Mapping) else None
    veto = modifiers.get("high_score_veto") if isinstance(modifiers, Mapping) else None
    rules = veto.get("rules") if isinstance(veto, Mapping) else None
    options: list[dict[str, Any]] = []
    for rule in rules or []:
        if not isinstance(rule, Mapping) or rule.get("source") != "hard_defects":
            continue
        key = str(rule.get("key") or "").strip()
        if not key or any(item["value"] == key for item in options):
            continue
        options.append(
            {
                "value": key,
                "label": _chinese_text(rule.get("description"), key),
                "severity": str(rule.get("severity") or ""),
            }
        )
    return options


def _judgement_nodes(v3_bundle: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Model judgements that drive the score but had no correctable node.

    Both write through the deterministic replay path: hard defects cap or veto
    the score, and the Call-B aesthetic score is the matcher's starting score.
    Without these nodes an operator could see a judgement they disagree with and
    have no way to correct it.
    """
    if not isinstance(v3_bundle, Mapping) or not isinstance(
        v3_bundle.get("contract"), Mapping
    ):
        return []
    spec_version = str(v3_bundle["contract"].get("spec_version") or "1")
    defect_options = _hard_defect_options(v3_bundle)
    nodes: list[dict[str, Any]] = [
        {
            "node_key": "call_a.hard_defects",
            "layer": "A",
            "path": "precheck.hard_defects",
            "order": 0,
            "label": "硬缺陷判定",
            "description": "调用A判定的硬缺陷；命中会压分或封顶，改动后服务端重算",
            "type": "list",
            "options": [option["value"] for option in defect_options],
            "semantic_version": spec_version,
            "compatibility_key": "precheck-hard-defects",
            "required": False,
            "evidence": _node_evidence("硬缺陷判定", required=True),
            "metadata": {
                "node_type": "precheck_field",
                "option_labels": {
                    option["value"]: option["label"] for option in defect_options
                },
                "option_severities": {
                    option["value"]: option["severity"] for option in defect_options
                },
            },
            "recompute_ref": "evaluation_v3_pipeline.recompute_qualified_v3",
        },
        {
            "node_key": "call_b.aesthetic_score",
            "layer": "B",
            "path": "aesthetic.aesthetic_score",
            "order": 0,
            "label": "调用B美感分",
            "description": "调用B给出的 0-100 美感分，是等级撮合器的初始分",
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "semantic_version": spec_version,
            "compatibility_key": "call-b-aesthetic-score",
            "required": False,
            "evidence": _node_evidence("调用B美感分", required=True),
            "metadata": {"node_type": "aesthetic_score"},
            "recompute_ref": "evaluation_v3_pipeline.recompute_qualified_v3",
        },
    ]
    return nodes


def _v3_nodes(v3_bundle: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Expose readable V3 decision nodes while keeping rule execution server-side."""

    if not isinstance(v3_bundle, Mapping):
        return []
    contract = v3_bundle.get("contract")
    if not isinstance(contract, Mapping):
        return []
    nodes: list[dict[str, Any]] = []
    track_block = contract.get("track_classification")
    tracks = track_block.get("tracks") if isinstance(track_block, Mapping) else None
    track_options = [
        str(item.get("key"))
        for item in tracks or []
        if isinstance(item, Mapping) and item.get("key")
    ]
    track_labels = {
        str(item.get("key")): str(item.get("label"))
        for item in tracks or []
        if isinstance(item, Mapping) and item.get("key")
    }
    if track_options:
        nodes.append(
            {
                "node_key": "v3.track_key",
                "layer": "V3",
                "path": "scoring.track_key",
                "order": len(nodes),
                "label": "等级撮合赛道",
                "description": "根据冻结分类合同确定图片所属赛道",
                "type": "enum",
                "options": track_options,
                "semantic_version": str(contract.get("spec_version") or "1"),
                "compatibility_key": "v3-track-classification",
                "required": True,
                "evidence": _node_evidence("等级撮合赛道", required=True),
                "metadata": {"node_type": "track", "option_labels": track_labels},
                "recompute_ref": "evaluation_v3_pipeline.recompute_qualified_v3",
            }
        )

    # 等级阈值有两套字段：旧 level_thresholds 与新 level_scale（现行合同用后者，
    # 旧字段为 null）。此前只认旧字段，导致新合同下「最终等级」节点整个不生成，
    # 复核页那一行没有纠偏按钮——运营反馈「纠偏改不了等级」的根因。
    thresholds = contract.get("level_thresholds")
    if not (isinstance(thresholds, (list, Mapping)) and thresholds):
        scale = contract.get("level_scale")
        if isinstance(scale, Mapping):
            levels_raw = scale.get("levels")
            if isinstance(levels_raw, list) and levels_raw:
                thresholds = [
                    {
                        "level": item.get("level"),
                        "min_score": item.get("min_score"),
                    }
                    for item in levels_raw
                    if isinstance(item, Mapping)
                    and item.get("level")
                    and item.get("enabled", True)
                ]
    if isinstance(thresholds, (list, Mapping)) and thresholds:
        threshold_value = deepcopy(thresholds)
        threshold_type = "list" if isinstance(thresholds, list) else "object"
        nodes.append(
            {
                "node_key": "v3.level_thresholds",
                "layer": "V3",
                "path": "scoring.level_thresholds",
                "order": len(nodes),
                "label": "等级分数阈值",
                "description": "本轮冻结的分数到等级映射阈值，仅供查看",
                "type": threshold_type,
                "semantic_version": str(contract.get("spec_version") or "1"),
                "compatibility_key": "v3-level-thresholds",
                "required": True,
                "evidence": _node_evidence("等级分数阈值"),
                "metadata": {
                    "node_type": "v3_thresholds",
                    "editable": False,
                    "frozen_value": threshold_value,
                    "read_only_reason": "等级阈值属于冻结规则，只能通过候选机制版本修改",
                },
                "recompute_ref": "evaluation_v3_pipeline.recompute_qualified_v3",
            }
        )
        levels = [
            str(item.get("level"))
            for item in thresholds
            if isinstance(item, Mapping) and item.get("level")
        ] if isinstance(thresholds, list) else [
            str(key) for key in thresholds if str(key) in {"L1", "L2", "L3", "L4", "L5"}
        ]
        if levels:
            nodes.append(
                {
                    "node_key": "v3.final_level",
                    "layer": "V3",
                    "path": "scoring.level",
                    "order": len(nodes),
                    "label": "最终等级",
                    "description": "由服务端权威评分引擎根据冻结规则计算最终等级",
                    "type": "enum",
                    "options": list(dict.fromkeys(levels)),
                    "semantic_version": str(contract.get("spec_version") or "1"),
                    "compatibility_key": "v3-final-level",
                    "required": True,
                    "evidence": _node_evidence("最终等级", required=True),
                    "metadata": {"node_type": "final_level"},
                    "recompute_ref": "evaluation_v3_pipeline.recompute_qualified_v3",
                }
            )
    return nodes


def freeze_correction_contract(
    *,
    category_key: str,
    prompt_snapshot: Mapping[str, Any],
    dimension_snapshot: Mapping[str, Any],
    production_field_snapshot: Mapping[str, Any],
    v3_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Compose and hash an immutable contract from the selected run inputs."""
    prompt_nodes = _source_nodes(
        prompt_snapshot,
        layer=str(prompt_snapshot.get("layer") or prompt_snapshot.get("stage") or "A"),
        path_prefix="prompt",
        label_prefix="提示词节点 ",
    )
    dimension_nodes = _source_nodes(
        dimension_snapshot,
        layer="A",
        path_prefix="dimensions",
        label_prefix="维度 ",
    )
    field_nodes = _source_nodes(
        production_field_snapshot,
        layer="B",
        path_prefix="production_fields",
        label_prefix="生产字段 ",
    )
    v3_nodes = _source_nodes(
        v3_snapshot,
        layer="V3",
        path_prefix="v3",
        label_prefix="规则节点 ",
    )
    # V3 nodes must retain the server-side recompute reference; a missing
    # reference intentionally remains incomplete and is blocked at release.
    nodes = prompt_nodes + dimension_nodes + field_nodes + v3_nodes
    contract = normalize_correction_contract(
        {
            "contract_version": "correction-contract-v1",
            "category_key": category_key,
            "nodes": nodes,
            "sources": {
                "prompt": deepcopy(dict(prompt_snapshot)),
                "dimensions": deepcopy(dict(dimension_snapshot)),
                "production_fields": deepcopy(dict(production_field_snapshot)),
                "v3": deepcopy(dict(v3_snapshot)),
            },
        },
        category_key=category_key,
    )
    contract["contract_hash"] = correction_contract_hash(contract)
    return contract


def correction_contract_from_run_snapshot(
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract a detached contract from a run snapshot without active fallback."""
    if not isinstance(snapshot, Mapping):
        return None
    candidate = snapshot.get("correction_contract")
    if not isinstance(candidate, Mapping):
        if "contract_version" in snapshot and isinstance(snapshot.get("nodes"), list):
            candidate = snapshot
        else:
            return None
    return deepcopy(dict(candidate))


def freeze_contract_from_execution_snapshot(
    *, category_key: str, execution_snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Build a contract from a frozen category execution snapshot.

    The execution snapshot is the only source of truth here.  An explicitly
    embedded contract wins; otherwise the adapter derives the form nodes from
    the frozen production-field, dimension, and V3 blocks.  No active database
    configuration is consulted as a compatibility shortcut.
    """

    explicit = execution_snapshot.get("correction_contract")
    if isinstance(explicit, Mapping) and isinstance(explicit.get("nodes"), list):
        normalized = normalize_correction_contract(explicit, category_key=category_key)
        normalized["contract_hash"] = correction_contract_hash(normalized)
        return normalized

    pipeline = execution_snapshot.get("pipeline_config")
    if not isinstance(pipeline, Mapping):
        pipeline = {}
    dimension_contract = execution_snapshot.get("dimension_contract")
    if not isinstance(dimension_contract, Mapping):
        dimension_contract = {}
    dimension_definition = dimension_contract.get("definition")
    if not isinstance(dimension_definition, Mapping):
        dimension_definition = execution_snapshot.get("dimension_selection")
    if not isinstance(dimension_definition, Mapping):
        dimension_definition = {}
    production_fields = pipeline.get("production_fields")
    if not isinstance(production_fields, Mapping):
        production_fields = {}
    v3 = execution_snapshot.get("v3_authoritative_bundle")
    if not isinstance(v3, Mapping):
        v3 = {}
    selection = execution_snapshot.get("dimension_selection")
    selected_keys = None
    if isinstance(selection, Mapping) and selection.get("mode") != "none":
        effective_keys = selection.get("effective_keys")
        if isinstance(effective_keys, list):
            selected_keys = [str(item) for item in effective_keys if str(item)]

    production_nodes = _production_field_nodes(
        production_fields,
        v3_bundle=v3,
    )
    dimension_nodes = _dimension_rule_nodes(
        dimension_definition,
        v3,
        selected_keys=selected_keys,
    )
    v3_nodes = _v3_nodes(v3)
    # Model judgements that drive the score but had no correctable node before:
    # the hard-defect list (layer A) and the Call-B aesthetic score (layer B).
    # Each node carries its own layer, so appending them here keeps the frozen
    # snapshot grouping intact.
    for node in _judgement_nodes(v3):
        if node["layer"] == "B":
            dimension_nodes.append(node)
        else:
            production_nodes.append(node)
    prompt_snapshot = {
        "stage": "A",
        "version": execution_snapshot.get("rubric_version") or "frozen",
        "nodes": [],
    }
    contract = freeze_correction_contract(
        category_key=category_key,
        prompt_snapshot=prompt_snapshot,
        dimension_snapshot={"nodes": dimension_nodes},
        production_field_snapshot={"nodes": production_nodes},
        v3_snapshot={"nodes": v3_nodes},
    )
    # Keep the complete frozen source payload for audit/debugging while the
    # browser receives only normalized, executable-safe nodes.
    contract["sources"] = {
        "prompt": deepcopy(dict(prompt_snapshot)),
        "dimensions": deepcopy(dict(dimension_contract)),
        "production_fields": deepcopy(dict(production_fields)),
        "v3": deepcopy(dict(v3)),
    }
    contract["contract_hash"] = correction_contract_hash(contract)
    return contract
