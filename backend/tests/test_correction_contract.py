from __future__ import annotations

import pytest

from app.correction_contract import (
    ContractValidationError,
    assert_correction_contract_complete,
    correction_contract_hash,
    inherit_correction_node,
    normalize_correction_contract,
    validate_node_value,
)


def test_normalize_contract_sorts_nodes_and_hash_is_stable() -> None:
    raw = {
        "contract_version": "1",
        "category_key": "inspiration_image",
        "nodes": [
            {
                "node_key": "b",
                "layer": "B",
                "label": "字段B",
                "type": "text",
                "semantic_version": "1",
            },
            {
                "node_key": "a",
                "layer": "A",
                "label": "字段A",
                "type": "enum",
                "options": ["x"],
                "semantic_version": "1",
            },
        ],
    }

    normalized = normalize_correction_contract(raw, category_key="inspiration_image")

    assert [node["node_key"] for node in normalized["nodes"]] == ["a", "b"]
    assert correction_contract_hash(normalized) == correction_contract_hash(normalized.copy())

    with_hash = {**normalized, "contract_hash": "ignored-by-hash"}
    assert correction_contract_hash(with_hash) == correction_contract_hash(normalized)


def test_normalize_contract_keeps_unknown_node_metadata() -> None:
    normalized = normalize_correction_contract(
        {
            "contract_version": "1",
            "category_key": "inspiration_image",
            "nodes": [
                {
                    "node_key": "a",
                    "layer": "A",
                    "path": "source",
                    "order": 2,
                    "label": "字段A",
                    "type": "text",
                    "semantic_version": "1",
                    "future_flag": True,
                }
            ],
        },
        category_key="inspiration_image",
    )

    assert normalized["nodes"][0]["metadata"] == {"future_flag": True}


def test_validate_complete_contract_requires_v3_recompute_reference() -> None:
    contract = {
        "contract_version": "1",
        "category_key": "inspiration_image",
        "nodes": [
            {
                "node_key": "v3.final",
                "layer": "V3",
                "path": "final",
                "order": 1,
                "label": "最终等级",
                "description": "根据规则确定最终等级",
                "type": "enum",
                "options": ["L1", "L2"],
                "semantic_version": "1",
                "compatibility_key": "final-level",
                "required": True,
                "evidence": {"description": "需要边界证据"},
            }
        ],
    }

    errors = []
    from app.correction_contract import validate_correction_contract

    errors = validate_correction_contract(contract)

    assert any("recompute_ref" in error for error in errors)
    with pytest.raises(ContractValidationError) as exc_info:
        assert_correction_contract_complete(contract)
    assert exc_info.value.code == "CORRECTION_CONTRACT_INCOMPLETE"
    assert "v3.final" in exc_info.value.fields


def test_inherit_only_compatible_stable_node() -> None:
    previous = {
        "node_key": "v3.final",
        "type": "enum",
        "semantic_version": "2",
        "compatibility_key": "final-level",
        "human_value": "L2",
        "reason": "证据",
        "evidence": [{"text": "边界"}],
    }
    current = {
        "node_key": "v3.final",
        "type": "enum",
        "semantic_version": "2",
        "compatibility_key": "final-level",
    }

    inherited = inherit_correction_node(previous, current)

    assert inherited["inheritance"]["status"] == "inherited"
    assert inherited["human_value"] == "L2"
    assert inherited["reason"] == "证据"


def test_semantic_change_does_not_inherit() -> None:
    previous = {
        "node_key": "v3.final",
        "type": "enum",
        "semantic_version": "1",
        "compatibility_key": "old",
        "human_value": "L2",
    }
    current = {
        "node_key": "v3.final",
        "type": "enum",
        "semantic_version": "2",
        "compatibility_key": "new",
    }

    result = inherit_correction_node(previous, current)

    assert result["inheritance"]["status"] == "changed"
    assert "human_value" not in result


def test_validate_node_value_rejects_value_outside_enum() -> None:
    node = {"node_key": "a", "type": "enum", "options": ["yes", "no"]}

    with pytest.raises(ContractValidationError) as exc_info:
        validate_node_value(node, "maybe")

    assert exc_info.value.code == "CORRECTION_NODE_VALUE_INVALID"
    assert exc_info.value.fields == ["a"]


def test_complete_contract_rejects_unknown_node_type_but_accepts_rule_hit() -> None:
    base = {
        "contract_version": "1",
        "category_key": "inspiration_image",
        "nodes": [
            {
                "node_key": "v3.rule",
                "layer": "V3",
                "path": "dimension.composition.hit_rules.r1",
                "order": 1,
                "label": "构图规则",
                "description": "人工判断构图规则是否命中",
                "type": "unknown_widget",
                "semantic_version": "1",
                "compatibility_key": "composition-r1",
                "required": True,
                "evidence": {"description": "需要图片证据"},
                "recompute_ref": "evaluation_v3_pipeline.recompute_qualified_v3",
            }
        ],
    }

    with pytest.raises(ContractValidationError) as exc_info:
        assert_correction_contract_complete(base)
    assert "type" in str(exc_info.value)

    base["nodes"][0]["type"] = "rule_hit"
    assert_correction_contract_complete(base)
