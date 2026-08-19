"""Contract-driven correction views and append-only submissions."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .correction_contract import (
    ContractValidationError,
    SUPPORTED_CORRECTION_NODE_TYPES,
    correction_contract_hash,
    inherit_correction_node,
    validate_correction_contract,
    validate_node_value,
)
from .node_correction_api import CorrectNodeRequest, apply_node_correction
from .baseline_regression import persist_human_level_truth


_EXECUTABLE_METADATA_KEYS = {
    "code",
    "expression",
    "executable",
    "javascript",
    "python",
    "rule_code",
    "threshold_override",
}


class CorrectionViewError(ValueError):
    """Stable error returned by every correction-view write boundary."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        fields: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.fields = list(fields or [])

    def detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.fields:
            payload["fields"] = self.fields
        return payload


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return deepcopy(value)
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _run_contract(run: Any) -> tuple[dict[str, Any] | None, str | None, str]:
    contract = _json_object(getattr(run, "correction_contract_json", None))
    source = "frozen"
    if not contract:
        execution = _json_object(getattr(run, "execution_snapshot_json", None))
        embedded = execution.get("correction_contract")
        contract = deepcopy(dict(embedded)) if isinstance(embedded, Mapping) else {}
        source = "embedded_frozen" if contract else "legacy_missing"
    if not contract:
        return None, None, source
    expected_hash = getattr(run, "correction_contract_hash", None)
    if not isinstance(expected_hash, str) or not expected_hash:
        embedded_hash = contract.get("contract_hash")
        expected_hash = embedded_hash if isinstance(embedded_hash, str) else None
    return contract, expected_hash, source


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if str(key).lower() not in _EXECUTABLE_METADATA_KEYS
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return deepcopy(value)


def _safe_metadata(value: Any) -> dict[str, Any]:
    safe = _safe_value(value)
    return safe if isinstance(safe, dict) else {}


def _node_steps(node: Mapping[str, Any]) -> list[str]:
    metadata = node.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    raw = node.get("steps", metadata.get("steps"))
    if isinstance(raw, list):
        steps = [str(item).strip() for item in raw if str(item).strip()]
        if steps:
            return steps
    if node.get("layer") == "V3":
        return [
            "读取本轮冻结合同与当前节点证据",
            "由服务端权威评分引擎重新计算",
            "返回新的分数、等级和规则路径",
        ]
    return []


def _human_entry(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = deepcopy(dict(value))
        if "human_value" not in result and "new_value" in result:
            result["human_value"] = deepcopy(result["new_value"])
        return result
    return {"human_value": deepcopy(value)}


def build_correction_nodes(
    contract: Mapping[str, Any],
    *,
    model_values: Mapping[str, Any],
    human_values: Mapping[str, Any],
    previous_values: Mapping[str, Any] | None,
    current_values: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Materialize UI-safe nodes in the exact order frozen by the run."""

    previous_values = previous_values or {}
    current_values = current_values or {}
    result: list[dict[str, Any]] = []
    raw_nodes = contract.get("nodes")
    if not isinstance(raw_nodes, list):
        return result
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            continue
        node = _safe_value(raw)
        node_key = str(node.get("node_key") or "")
        if not node_key:
            continue
        node["metadata"] = _safe_metadata(node.get("metadata"))
        for key in _EXECUTABLE_METADATA_KEYS:
            node.pop(key, None)
        metadata_editable = node["metadata"].get("editable", True) is not False
        frozen_value = node["metadata"].get("frozen_value")
        model_value = model_values.get(node_key)
        if model_value is None and "archived_value" in node["metadata"]:
            model_value = deepcopy(node["metadata"]["archived_value"])
        if model_value is None and not metadata_editable and "frozen_value" in node["metadata"]:
            model_value = frozen_value
        node["model_value"] = deepcopy(model_value)
        current_value = current_values.get(node_key)
        if current_value is None and "archived_value" in node["metadata"]:
            current_value = deepcopy(node["metadata"]["archived_value"])
        node["current_value"] = deepcopy(current_value if current_value is not None else model_value)
        previous = previous_values.get(node_key)
        inherited = inherit_correction_node(
            previous if isinstance(previous, Mapping) else None,
            node,
        )
        current_human = human_values.get(node_key)
        if current_human is not None:
            inherited.update(_human_entry(current_human))
            inherited["inheritance"] = {"status": "current"}
        inherited["steps"] = _node_steps(node)
        inherited["editable"] = metadata_editable
        if not metadata_editable:
            inherited["read_only"] = True
            inherited["read_only_reason"] = str(
                node["metadata"].get("read_only_reason")
                or "本节点属于冻结规则，只能查看"
            )
        result.append(inherited)
    return result


def _mapping_path(root: Any, path: str) -> Any:
    current = root
    for part in [item for item in path.split(".") if item]:
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
            continue
        if isinstance(current, list):
            matched = next(
                (
                    item
                    for item in current
                    if isinstance(item, Mapping)
                    and str(
                        item.get("dimension_key")
                        or item.get("node_key")
                        or item.get("rule_id")
                        or item.get("key")
                    )
                    == part
                ),
                None,
            )
            if matched is None:
                return None
            current = matched
            continue
        return None
    return deepcopy(current)


def _value_for_node(roots: Mapping[str, Any], node: Mapping[str, Any]) -> Any:
    node_key = str(node.get("node_key") or "")
    if node_key in roots:
        return deepcopy(roots[node_key])
    path = str(node.get("path") or node_key)
    if path in roots:
        return deepcopy(roots[path])
    return _mapping_path(roots, path)


def _model_roots(item: Any) -> dict[str, Any]:
    snapshot = _json_object(getattr(item, "result_snapshot_json", None))
    stage_a = snapshot.get("stage_a")
    stage_a = stage_a if isinstance(stage_a, Mapping) else {}
    production_fields = stage_a.get("production_fields")
    production_fields = (
        production_fields if isinstance(production_fields, Mapping) else {}
    )
    stage_b = snapshot.get("stage_b")
    stage_b = stage_b if isinstance(stage_b, Mapping) else {}
    dimensions = stage_b.get("dimensions")
    dimensions = dimensions if isinstance(dimensions, (Mapping, list)) else {}
    level = snapshot.get("predicted_level")
    score = snapshot.get("authoritative_score")
    return {
        **snapshot,
        "call_a": {**dict(production_fields), "grade": level, "score": score},
        "production_fields": dict(production_fields),
        "precheck": dict(stage_a),
        "redline": dict(stage_a),
        "dimension": deepcopy(dimensions),
        "dimensions": deepcopy(dimensions),
        "aesthetic": dict(stage_b),
        "final_level": level,
        "level": level,
        "scoring": {
            "level": level,
            "score": score,
            **(
                dict(snapshot.get("scoring"))
                if isinstance(snapshot.get("scoring"), Mapping)
                else {}
            ),
        },
    }


def _current_roots(item: Any) -> dict[str, Any]:
    evaluation = getattr(item, "evaluation", None)
    if evaluation is None:
        return _model_roots(item)
    precheck = _json_object(getattr(evaluation, "precheck_json", None))
    production_fields = precheck.get("production_fields")
    production_fields = (
        production_fields if isinstance(production_fields, Mapping) else {}
    )
    scoring = _json_object(getattr(evaluation, "scoring_json", None))
    aesthetic = _json_object(getattr(evaluation, "aesthetic_json", None))
    level = getattr(evaluation, "level", None)
    score = getattr(evaluation, "score", None)
    return {
        "call_a": {**dict(production_fields), "grade": level, "score": score},
        "production_fields": dict(production_fields),
        "precheck": precheck,
        "redline": precheck,
        "dimension": aesthetic.get("dimensions", {}),
        "dimensions": aesthetic.get("dimensions", {}),
        "aesthetic": aesthetic,
        "scoring": scoring,
        "track": scoring.get("track_key"),
        "track_key": scoring.get("track_key"),
        "final_level": level,
        "level": level,
    }


def _human_values(item: Any, contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    evaluation = getattr(item, "evaluation", None)
    if evaluation is None:
        return {}
    nodes = [node for node in contract.get("nodes", []) if isinstance(node, Mapping)]
    key_by_path = {
        str(node.get("path") or node.get("node_key")): str(node.get("node_key"))
        for node in nodes
    }
    known_keys = {str(node.get("node_key")) for node in nodes}
    values: dict[str, dict[str, Any]] = {}
    for event in _json_list(getattr(evaluation, "correction_history_json", None)):
        if not isinstance(event, Mapping):
            continue
        if event.get("corrector_confidence") is not None or event.get(
            "corrector_policy"
        ):
            continue
        node_key = event.get("node_key")
        if node_key not in known_keys:
            node_key = key_by_path.get(str(event.get("node_path") or ""))
        if not node_key:
            continue
        values[str(node_key)] = {
            "human_value": deepcopy(event.get("new_value")),
            "reason": str(event.get("reason") or ""),
            "evidence": deepcopy(
                event.get("contract_evidence", event.get("evidence", []))
            ),
            "source": {
                "type": "node_correction",
                "corrector": event.get("corrector"),
                "corrected_at": event.get("corrected_at"),
                "review_revision": event.get("resulting_review_revision"),
            },
        }

    reviews = list(getattr(evaluation, "reviews", []) or [])
    reviews.sort(
        key=lambda review: (
            str(getattr(review, "created_at", "") or ""),
            int(getattr(review, "id", 0) or 0),
        )
    )
    for review in reviews:
        for correction in _json_list(getattr(review, "corrections_json", None)):
            if not isinstance(correction, Mapping):
                continue
            raw_path = str(correction.get("field_key") or "")
            node_key = key_by_path.get(raw_path)
            if node_key is None and raw_path.startswith("production_fields."):
                node_key = key_by_path.get("call_a." + raw_path.split(".", 1)[1])
            if not node_key or node_key in values:
                continue
            values[node_key] = {
                "human_value": deepcopy(correction.get("human_value")),
                "reason": str(correction.get("note") or getattr(review, "note", "")),
                "evidence": [],
                "source": {
                    "type": "human_review",
                    "review_id": getattr(review, "id", None),
                    "reviewer": getattr(review, "reviewer_name", None),
                },
            }
    return values


def _values_for_contract(
    roots: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        str(node.get("node_key")): _value_for_node(roots, node)
        for node in contract.get("nodes", [])
        if isinstance(node, Mapping) and node.get("node_key")
    }


def _previous_human_values(previous_item: Any | None) -> dict[str, Any]:
    if previous_item is None:
        return {}
    previous_run = getattr(previous_item, "run", None)
    if previous_run is None:
        return {}
    previous_contract, _, _ = _run_contract(previous_run)
    if previous_contract is None:
        return {}
    values = _human_values(previous_item, previous_contract)
    by_key = {
        str(node.get("node_key")): node
        for node in previous_contract.get("nodes", [])
        if isinstance(node, Mapping) and node.get("node_key")
    }
    return {
        key: {**deepcopy(by_key[key]), **deepcopy(value)}
        for key, value in values.items()
        if key in by_key
    }


def _legacy_archive_payload(item: Any) -> Mapping[str, Any] | list[Any] | None:
    """Read only item-owned archive fields; never consult a live mechanism."""

    snapshot = _json_object(getattr(item, "result_snapshot_json", None))
    for key in (
        "archived_correction_fields",
        "archived_correction_contract",
        "archived_fields",
        "correction_fields",
        "frozen_correction_fields",
    ):
        value = snapshot.get(key)
        if isinstance(value, (Mapping, list)):
            return value
    for key in (
        "archived_correction_fields",
        "archived_correction_contract",
        "archived_fields",
        "correction_fields",
    ):
        value = getattr(item, key, None)
        if isinstance(value, (Mapping, list)):
            return value
    return None


def _legacy_archive_nodes(
    archive: Mapping[str, Any] | list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize a historical field subset and report unavailable node keys."""

    if isinstance(archive, Mapping):
        raw_nodes = archive.get("nodes")
        if not isinstance(raw_nodes, list):
            raw_nodes = archive.get("fields")
        if isinstance(raw_nodes, Mapping):
            raw_nodes = [
                {"node_key": key, "value": value}
                for key, value in raw_nodes.items()
            ]
        if raw_nodes is None:
            reserved = {
                "contract_version",
                "category_key",
                "expected_nodes",
                "node_keys",
                "contract",
                "contract_hash",
                "nodes",
                "fields",
            }
            direct_fields = {
                key: value
                for key, value in archive.items()
                if key not in reserved
            }
            if direct_fields:
                raw_nodes = [
                    {"node_key": key, "value": value}
                    for key, value in direct_fields.items()
                ]
        expected = archive.get("expected_nodes")
        if not isinstance(expected, list):
            expected = archive.get("node_keys")
    else:
        raw_nodes = archive
        expected = None
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    expected_keys = [str(value) for value in expected or [] if str(value).strip()]
    nodes: list[dict[str, Any]] = []
    unavailable: list[str] = []
    seen: set[str] = set()
    required_fields = (
        "node_key",
        "layer",
        "path",
        "label",
        "description",
        "type",
        "semantic_version",
        "compatibility_key",
        "required",
        "evidence",
    )
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, Mapping):
            unavailable.append(f"归档节点[{index}]")
            continue
        source = deepcopy(dict(raw))
        node_key = str(source.get("node_key") or source.get("key") or "").strip()
        path = str(source.get("path") or node_key).strip()
        if not node_key and path:
            node_key = path
        layer = str(source.get("layer") or "").strip()
        if not layer:
            layer = "V3" if path.startswith(("v3.", "scoring.")) else "B" if path.startswith(("call_b.", "dimension.", "dimensions.")) else "A"
        missing = [field for field in required_fields if field not in source]
        if layer == "V3" and not source.get("recompute_ref"):
            missing.append("recompute_ref")
        if not node_key:
            unavailable.append(f"归档节点[{index}]")
            continue
        if node_key in seen:
            unavailable.append(node_key)
            continue
        seen.add(node_key)
        raw_metadata = source.get("metadata")
        metadata = deepcopy(dict(raw_metadata)) if isinstance(raw_metadata, Mapping) else {}
        metadata.setdefault("node_type", source.get("node_type") or "call_a_field")
        metadata["archived_value"] = deepcopy(
            source.get("value", source.get("model_value"))
        )
        metadata["archive_complete"] = not missing
        node = {
            **source,
            "node_key": node_key,
            "layer": layer,
            "path": path or node_key,
            "order": int(source.get("order", index) or index),
            "metadata": metadata,
        }
        node.pop("value", None)
        node.pop("model_value", None)
        if missing:
            unavailable.append(f"{node_key}（缺少：{'、'.join(missing)}）")
        nodes.append(node)
    for expected_key in expected_keys:
        if expected_key not in seen:
            unavailable.append(expected_key)
    return nodes, unavailable


def legacy_correction_view_from_archived_fields(item: Any) -> dict[str, Any]:
    """Build the narrowest possible view for a run predating contract snapshots.

    The archive is intentionally treated as untrusted metadata.  Complete
    archived nodes can be edited in place; any missing node description or
    server recompute reference makes the entire legacy view read-only.  No
    current category, prompt, or V3 configuration is queried here.
    """

    run = getattr(item, "run", None)
    evaluation = getattr(item, "evaluation", None)
    archive = _legacy_archive_payload(item)
    contract: dict[str, Any] | None = None
    unavailable: list[str] = []
    nodes: list[dict[str, Any]] = []
    if archive is not None:
        archive_mapping = archive if isinstance(archive, Mapping) else {}
        raw_contract = (
            archive_mapping.get("contract")
            if isinstance(archive_mapping, Mapping)
            else None
        )
        raw_nodes_source: Mapping[str, Any] | list[Any] = (
            raw_contract if isinstance(raw_contract, Mapping) else archive
        )
        nodes, unavailable = _legacy_archive_nodes(raw_nodes_source)
        category_key = str(
            archive_mapping.get("category_key")
            if isinstance(archive_mapping, Mapping)
            else ""
        ) or str(getattr(run, "category_key", "legacy"))
        contract = {
            "contract_version": str(
                archive_mapping.get("contract_version", "legacy-correction-contract-v1")
                if isinstance(archive_mapping, Mapping)
                else "legacy-correction-contract-v1"
            ),
            "category_key": category_key,
            "nodes": nodes,
            "sources": {"archive": deepcopy(dict(archive_mapping))}
            if isinstance(archive_mapping, Mapping)
            else {"archive": deepcopy(archive)},
        }
        normalized = deepcopy(contract)
        normalized["contract_hash"] = correction_contract_hash(normalized)
        contract = normalized
        validation_errors = validate_correction_contract(contract)
        if validation_errors:
            unavailable.extend(
                error.split(".", 1)[0]
                if error.startswith("nodes[") and "." not in error.split(" ", 1)[0][7:]
                else error
                for error in validation_errors
            )
    else:
        unavailable = ["全部合同节点"]

    review_revision = int(getattr(evaluation, "review_revision", 0) or 0)
    read_only = bool(unavailable or evaluation is None or contract is None)
    if contract is not None:
        model_values = _values_for_contract(_model_roots(item), contract)
        current_values = _values_for_contract(_current_roots(item), contract)
        materialized = build_correction_nodes(
            contract,
            model_values=model_values,
            current_values=current_values,
            human_values=_human_values(item, contract),
            previous_values={},
        )
        if read_only:
            for node in materialized:
                node["editable"] = False
                node["read_only"] = True
                node["read_only_reason"] = "历史归档字段不完整，只能查看"
        nodes = materialized
    lane = "baseline"
    if getattr(run, "__tablename__", "") == "evaluation_production_runs":
        lane = "incremental"
    elif getattr(run, "__tablename__", "") == "prompt_regression_runs":
        lane = "candidate"
    return {
        "schema_version": "correction-view-v1",
        "lane": lane,
        "run_id": getattr(run, "id", getattr(item, "run_id", None)),
        "item_id": getattr(item, "id", None),
        "evaluation_id": getattr(item, "evaluation_id", None),
        "category_key": contract.get("category_key") if contract else getattr(run, "category_key", None),
        "snapshot_status": "legacy_compatible" if contract is not None and not read_only else "legacy_read_only",
        "snapshot_source": "archived_item_fields",
        "read_only": read_only,
        "unavailable_reason": "；".join(unavailable) if unavailable else None,
        "unavailable_nodes": list(dict.fromkeys(unavailable)),
        "contract": (
            {
                "contract_version": contract.get("contract_version"),
                "contract_hash": contract["contract_hash"],
                "category_key": contract["category_key"],
            }
            if contract
            else None
        ),
        "review_revision": review_revision,
        "nodes": nodes,
        "idempotent_replay": False,
    }


def build_correction_view(
    db: Session | None,
    *,
    run: Any,
    item: Any,
    previous_item: Any | None = None,
) -> dict[str, Any]:
    """Build one correction form exclusively from the run's frozen data."""

    del db  # Reserved for lane adapters; active configuration is never queried.
    contract, expected_hash, snapshot_source = _run_contract(run)
    evaluation = getattr(item, "evaluation", None)
    review_revision = int(getattr(evaluation, "review_revision", 0) or 0)
    if contract is None:
        return legacy_correction_view_from_archived_fields(item)

    actual_hash = correction_contract_hash(contract)
    contract_errors = validate_correction_contract(contract)
    hash_mismatch = bool(expected_hash and expected_hash != actual_hash)
    read_only = bool(contract_errors or hash_mismatch or evaluation is None)
    unavailable: list[str] = []
    if hash_mismatch:
        unavailable.append("合同哈希与冻结快照不一致")
    unavailable.extend(contract_errors)
    if evaluation is None:
        unavailable.append("评测结果尚不可用")
    model_values = _values_for_contract(_model_roots(item), contract)
    current_values = _values_for_contract(_current_roots(item), contract)
    nodes = build_correction_nodes(
        contract,
        model_values=model_values,
        current_values=current_values,
        human_values=_human_values(item, contract),
        previous_values=_previous_human_values(previous_item),
    )
    if read_only:
        for node in nodes:
            node["editable"] = False
    contract_hash = expected_hash or actual_hash
    return {
        "schema_version": "correction-view-v1",
        "lane": "baseline",
        "run_id": getattr(run, "id", None),
        "item_id": getattr(item, "id", None),
        "evaluation_id": getattr(item, "evaluation_id", None),
        "category_key": contract.get("category_key")
        or getattr(run, "category_key", None),
        "snapshot_status": (
            "frozen"
            if not contract_errors and not hash_mismatch
            else "legacy_read_only"
        ),
        "snapshot_source": snapshot_source,
        "read_only": read_only,
        "unavailable_reason": "；".join(unavailable) if unavailable else None,
        "unavailable_nodes": unavailable,
        "contract": {
            "contract_version": contract.get("contract_version"),
            "contract_hash": contract_hash,
            "category_key": contract.get("category_key"),
        },
        "review_revision": review_revision,
        "nodes": nodes,
        "idempotent_replay": False,
    }


def _evidence_required(node: Mapping[str, Any]) -> bool:
    evidence = node.get("evidence")
    metadata = node.get("metadata")
    return bool(
        (isinstance(evidence, Mapping) and evidence.get("required"))
        or (isinstance(metadata, Mapping) and metadata.get("evidence_required"))
    )


def _node_runtime_type(node: Mapping[str, Any]) -> str:
    metadata = node.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    explicit = node.get("node_type", metadata.get("node_type"))
    if isinstance(explicit, str) and explicit:
        return explicit
    path = str(node.get("path") or node.get("node_key") or "")
    if path.startswith("call_a."):
        return "call_a_field"
    if path.startswith("dimension.") or path.startswith("dimensions."):
        return "dimension_rule"
    if path.startswith("redline."):
        return "redline"
    if path.startswith("precheck."):
        return "precheck_field"
    if path in {"track", "track_key", "scoring.track_key"}:
        return "track"
    if path in {"final_level", "level", "scoring.level"}:
        return "final_level"
    raise CorrectionViewError(
        422,
        "CORRECTION_NODE_RUNTIME_UNSUPPORTED",
        f"节点 {node.get('node_key')} 没有可用的服务端纠偏映射",
        fields=[str(node.get("node_key"))],
    )


def _normalized_evidence(
    node_key: str, evidence: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, Mapping):
            raise CorrectionViewError(
                422,
                "CORRECTION_EVIDENCE_INVALID",
                f"节点 {node_key} 的第 {index} 条证据必须是对象",
                fields=[node_key],
            )
        allowed = {
            key: deepcopy(item[key])
            for key in (
                "rule_id",
                "old_confidence",
                "new_confidence",
                "old_evidence",
                "new_evidence",
            )
            if key in item
        }
        allowed.setdefault(
            "rule_id",
            str(item.get("field") or item.get("evidence_key") or f"{node_key}:{index}"),
        )
        if "new_evidence" not in allowed:
            text = item.get("text", item.get("description", item.get("value", "")))
            allowed["new_evidence"] = (
                str(text)
                if isinstance(text, (str, int, float, bool))
                else _canonical_json(text)
            )
        normalized.append(allowed)
    return normalized


def _submission_payload_hash(
    *, contract_hash: str, nodes: list[Mapping[str, Any]]
) -> str:
    canonical_nodes = sorted(
        (deepcopy(dict(node)) for node in nodes),
        key=lambda node: str(node.get("node_key") or ""),
    )
    return hashlib.sha256(
        _canonical_json(
            {"contract_hash": contract_hash, "nodes": canonical_nodes}
        ).encode("utf-8")
    ).hexdigest()


def _existing_idempotent_event(
    evaluation: Any, idempotency_key: str
) -> Mapping[str, Any] | None:
    return next(
        (
            event
            for event in _json_list(
                getattr(evaluation, "correction_history_json", None)
            )
            if isinstance(event, Mapping)
            and event.get("idempotency_key") == idempotency_key
        ),
        None,
    )


def submit_correction_nodes(
    db: Session,
    *,
    run: Any,
    item: Any,
    contract_hash: str,
    nodes: list[Mapping[str, Any]],
    review_revision: int,
    idempotency_key: str,
    actor: str,
) -> dict[str, Any]:
    """Validate and append one atomic batch of human node corrections."""

    contract, expected_hash, _ = _run_contract(run)
    if contract is None:
        raise CorrectionViewError(
            409,
            "CORRECTION_CONTRACT_UNAVAILABLE",
            "该历史运行没有完整纠偏合同，只能只读查看",
        )
    actual_hash = correction_contract_hash(contract)
    frozen_hash = expected_hash or actual_hash
    if contract_hash != frozen_hash or actual_hash != frozen_hash:
        raise CorrectionViewError(
            409,
            "CORRECTION_CONTRACT_STALE",
            "纠偏合同已变化，请刷新后重试",
        )
    contract_errors = validate_correction_contract(contract)
    if contract_errors:
        raise CorrectionViewError(
            409,
            "CORRECTION_CONTRACT_INCOMPLETE",
            "本轮冻结纠偏合同不完整，只能只读查看",
            fields=contract_errors,
        )
    if getattr(item, "run_id", None) != getattr(run, "id", None):
        raise CorrectionViewError(
            409,
            "CORRECTION_ITEM_RUN_MISMATCH",
            "纠偏条目不属于该运行",
        )
    evaluation = getattr(item, "evaluation", None)
    if evaluation is None:
        raise CorrectionViewError(
            409,
            "CORRECTION_RESULT_UNAVAILABLE",
            "该条目尚无可纠偏的评测结果",
        )
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise CorrectionViewError(
            422,
            "CORRECTION_IDEMPOTENCY_KEY_INVALID",
            "幂等键不能为空",
        )
    if not isinstance(nodes, list) or not nodes:
        raise CorrectionViewError(
            422,
            "CORRECTION_NODES_EMPTY",
            "至少提交一个纠偏节点",
        )

    submission_hash = _submission_payload_hash(
        contract_hash=contract_hash,
        nodes=nodes,
    )
    existing = _existing_idempotent_event(evaluation, idempotency_key)
    if existing is not None:
        if existing.get("submission_hash") != submission_hash:
            raise CorrectionViewError(
                409,
                "CORRECTION_IDEMPOTENCY_CONFLICT",
                "幂等键已用于不同的纠偏内容",
            )
        replay = build_correction_view(db, run=run, item=item)
        replay["idempotent_replay"] = True
        return replay

    by_key = {
        str(node.get("node_key")): node
        for node in contract.get("nodes", [])
        if isinstance(node, Mapping) and node.get("node_key")
    }
    prepared: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    seen: set[str] = set()
    for request_node in nodes:
        if not isinstance(request_node, Mapping):
            raise CorrectionViewError(
                422,
                "CORRECTION_NODE_INVALID",
                "纠偏节点必须是对象",
            )
        node_key = str(request_node.get("node_key") or "")
        if node_key not in by_key:
            raise CorrectionViewError(
                409,
                "CORRECTION_NODE_UNKNOWN",
                f"节点 {node_key or '未命名'} 不属于本轮冻结合同",
                fields=[node_key] if node_key else [],
            )
        if node_key in seen:
            raise CorrectionViewError(
                422,
                "CORRECTION_NODE_DUPLICATE",
                f"节点 {node_key} 不能重复提交",
                fields=[node_key],
            )
        seen.add(node_key)
        contract_node = by_key[node_key]
        metadata = contract_node.get("metadata")
        if isinstance(metadata, Mapping) and metadata.get("editable") is False:
            raise CorrectionViewError(
                422,
                "CORRECTION_NODE_READ_ONLY",
                f"节点 {node_key} 属于冻结规则，只能查看，不能直接修改",
                fields=[node_key],
            )
        value_type = str(contract_node.get("type") or "").lower()
        if (
            value_type not in SUPPORTED_CORRECTION_NODE_TYPES
            and not value_type.startswith(("integer_", "number_", "float_"))
        ):
            raise CorrectionViewError(
                422,
                "CORRECTION_NODE_TYPE_UNSUPPORTED",
                f"节点 {node_key} 的类型不受支持",
                fields=[node_key],
            )
        try:
            validate_node_value(contract_node, request_node.get("human_value"))
        except ContractValidationError as exc:
            raise CorrectionViewError(
                422,
                exc.code,
                str(exc),
                fields=exc.fields,
            ) from exc
        reason = request_node.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CorrectionViewError(
                422,
                "CORRECTION_REASON_REQUIRED",
                f"节点 {node_key} 必须填写人工决策理由",
                fields=[node_key],
            )
        evidence = request_node.get("evidence", [])
        if not isinstance(evidence, list):
            raise CorrectionViewError(
                422,
                "CORRECTION_EVIDENCE_INVALID",
                f"节点 {node_key} 的证据必须是数组",
                fields=[node_key],
            )
        if _evidence_required(contract_node) and not evidence:
            raise CorrectionViewError(
                422,
                "CORRECTION_EVIDENCE_REQUIRED",
                f"节点 {node_key} 必须提供人工判断证据",
                fields=[node_key],
            )
        prepared.append((contract_node, request_node))

    if int(getattr(evaluation, "review_revision", 0) or 0) != review_revision:
        raise CorrectionViewError(
            409,
            "CORRECTION_REVIEW_STALE",
            "审核修订号已变化，请刷新后重试",
        )

    savepoint = db.begin_nested()
    try:
        for contract_node, request_node in prepared:
            node_key = str(contract_node["node_key"])
            current_roots = _current_roots(item)
            current_value = _value_for_node(current_roots, contract_node)
            original_evidence = deepcopy(request_node.get("evidence", []))
            correction_key = "contract:" + hashlib.sha256(
                f"{idempotency_key}:{node_key}".encode("utf-8")
            ).hexdigest()[:48]
            apply_node_correction(
                db,
                result=evaluation,
                payload=CorrectNodeRequest(
                    correction_key=correction_key,
                    node_type=_node_runtime_type(contract_node),
                    node_path=str(
                        contract_node.get("path") or contract_node["node_key"]
                    ),
                    old_value=current_value,
                    new_value=deepcopy(request_node.get("human_value")),
                    evidence=_normalized_evidence(node_key, original_evidence),
                    reason=str(request_node.get("reason") or "").strip(),
                ),
                corrector=actor,
            )
            history = _json_list(evaluation.correction_history_json)
            event = next(
                (
                    entry
                    for entry in reversed(history)
                    if isinstance(entry, dict)
                    and entry.get("correction_key") == correction_key
                ),
                None,
            )
            if event is None:
                raise CorrectionViewError(
                    409,
                    "CORRECTION_HISTORY_APPEND_FAILED",
                    f"节点 {node_key} 未形成可审计的纠偏历史",
                    fields=[node_key],
                )
            event.update(
                {
                    "schema_version": "contract-node-correction-v1",
                    "node_key": node_key,
                    "contract_hash": contract_hash,
                    "idempotency_key": idempotency_key,
                    "submission_hash": submission_hash,
                    "submitted_review_revision": review_revision,
                    "resulting_review_revision": review_revision + 1,
                    "contract_evidence": original_evidence,
                }
            )
            evaluation.correction_history_json = json.dumps(
                history, ensure_ascii=False
            )
            if (
                _node_runtime_type(contract_node) == "final_level"
                and getattr(run, "baseline_set_id", None) is not None
            ):
                category_key = (
                    getattr(run, "category_key", None)
                    or
                    getattr(getattr(evaluation, "job", None), "category_key", None)
                    or getattr(getattr(evaluation, "asset", None), "category_key", None)
                )
                if isinstance(category_key, str) and category_key:
                    persist_human_level_truth(
                        db,
                        category_key=category_key,
                        asset_id=int(evaluation.asset_id),
                        source_result_id=int(evaluation.id),
                        level=str(request_node.get("human_value")),
                        actor=actor,
                        reason=str(request_node.get("reason") or "人工纠偏更新最终等级"),
                    )
        evaluation.review_revision = review_revision + 1
        db.flush()
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        raise

    return build_correction_view(db, run=run, item=item)
