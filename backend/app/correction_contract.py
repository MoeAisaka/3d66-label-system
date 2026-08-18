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
        options = node.get("options", node.get("allowed_values", node.get("values")))
        if node_type in {"enum", "enumeration"} and (
            not isinstance(options, list) or not options
        ):
            errors.append(f"{field_prefix}.options 必须是非空数组")
        if _is_numeric_type(node_type):
            lower, upper = _bounds(node)
            if lower is None or upper is None:
                errors.append(f"{field_prefix} 必须声明数值上下界")
            elif not isinstance(lower, Real) or isinstance(lower, bool) or not isinstance(upper, Real) or isinstance(upper, bool):
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
