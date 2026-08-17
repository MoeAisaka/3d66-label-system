from types import SimpleNamespace

import pytest

from app.automation_routing import (
    route_correction_evidence,
    validate_route_against_frozen_mechanism,
)


def _evidence(*, node_types=None, node_type=None, reason="", evidence=None):
    values = node_types or ([node_type] if node_type else [])
    return SimpleNamespace(
        node_corrections=[
            {
                "node_type": value,
                "node_path": value,
                "reason": reason or "人工确认该节点错误",
                "evidence": evidence or [{"path": value, "text": "可见证据"}],
                "source": "human",
            }
            for value in values
        ],
        human_reviews=[],
        mechanism_snapshot={"call_a": {}, "call_b": {}, "v3": {}},
    )


def test_route_scope_fact_quality_media_and_hard_defect_to_call_a():
    decision = route_correction_evidence(
        _evidence(node_type="call_a_field", reason="是截图且有大面积文字")
    )
    assert decision.layers == ("A",)
    assert decision.dependency_order == ("A",)


def test_route_aesthetic_evidence_to_call_b():
    decision = route_correction_evidence(
        _evidence(
            node_type="dimension_rule",
            reason="构图平衡度被低估",
            evidence=[{"path": "dimensions.balance", "text": "主体偏置"}],
        )
    )
    assert decision.layers == ("B",)


def test_route_threshold_cap_and_level_mapping_to_v3():
    decision = route_correction_evidence(
        _evidence(node_type="final_level", reason="89分应升入L1")
    )
    assert decision.layers == ("V3",)


def test_multi_node_correction_produces_one_ordered_combined_route():
    decision = route_correction_evidence(
        _evidence(
            node_types=["call_a_field", "dimension_rule", "final_level"],
        )
    )
    assert decision.layers == ("A", "B", "V3")
    assert decision.dependency_order == ("A", "B", "V3")
    assert decision.route_key == "A+B+V3"


def test_route_rejects_unknown_node_without_safe_fallback():
    with pytest.raises(ValueError, match="无法路由"):
        route_correction_evidence(_evidence(node_type="unknown_node"))


def test_route_must_match_frozen_mechanism():
    decision = route_correction_evidence(_evidence(node_type="final_level"))
    with pytest.raises(ValueError, match="机制快照"):
        validate_route_against_frozen_mechanism(decision, {"v3": None})
