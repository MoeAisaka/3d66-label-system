"""Route evidenced human corrections to the smallest mechanism layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


RouteLayer = Literal["A", "B", "V3"]


@dataclass(frozen=True)
class RouteDecision:
    layers: tuple[RouteLayer, ...]
    route_key: str
    reason_codes: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    dependency_order: tuple[RouteLayer, ...]
    confidence: float


_NODE_LAYER: dict[str, RouteLayer] = {
    "call_a_field": "A",
    "scope": "A",
    "classification": "A",
    "content_fact": "A",
    "quality": "A",
    "media": "A",
    "hard_defect": "A",
    "production_field": "A",
    "dimension_rule": "B",
    "aesthetic_dimension": "B",
    "visual_evidence": "B",
    "final_level": "V3",
    "level_mapping": "V3",
    "threshold": "V3",
    "score_cap": "V3",
    "scoring_rule": "V3",
    "v3_rule": "V3",
}
_DEPENDENCY_ORDER: tuple[RouteLayer, ...] = ("A", "B", "V3")


def _items(evidence: Any) -> list[Mapping[str, Any]]:
    if isinstance(evidence, Mapping):
        raw = evidence.get("node_corrections", [])
    else:
        raw = getattr(evidence, "node_corrections", [])
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


def route_correction_evidence(evidence: Any) -> RouteDecision:
    """Return one deterministic route for a human-finalized correction."""

    nodes = _items(evidence)
    if not nodes:
        raise ValueError("纠偏证据为空，无法路由")
    layers: set[RouteLayer] = set()
    reason_codes: list[str] = []
    paths: list[str] = []
    for node in nodes:
        node_type = str(node.get("node_type") or "").strip().lower()
        layer = _NODE_LAYER.get(node_type)
        if layer is None:
            raise ValueError(f"纠偏节点 {node_type or '<empty>'} 无法路由")
        layers.add(layer)
        reason = str(node.get("reason") or "").strip()
        if reason:
            reason_codes.append(f"{layer}:{reason[:80]}")
        path = str(node.get("node_path") or "").strip()
        if path:
            paths.append(path)
        evidence_items = node.get("evidence")
        if not evidence_items:
            raise ValueError(f"纠偏节点 {node_type} 缺少证据")
    ordered = tuple(layer for layer in _DEPENDENCY_ORDER if layer in layers)
    return RouteDecision(
        layers=ordered,
        route_key="+".join(ordered),
        reason_codes=tuple(reason_codes),
        evidence_paths=tuple(dict.fromkeys(paths)),
        dependency_order=ordered,
        confidence=1.0,
    )


def validate_route_against_frozen_mechanism(
    decision: RouteDecision,
    mechanism_snapshot: Mapping[str, Any],
) -> None:
    """Fail closed when a selected route is not present in the frozen contract."""

    aliases = {"A": "call_a", "B": "call_b", "V3": "v3"}
    missing = [
        layer
        for layer in decision.layers
        if not isinstance(mechanism_snapshot.get(aliases[layer]), Mapping)
    ]
    if missing:
        raise ValueError("纠偏路由缺少冻结机制快照：" + ",".join(missing))

