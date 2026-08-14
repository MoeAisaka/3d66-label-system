from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.semantic_tag_contracts import (
    PLATFORM_SEMANTIC_FIELD_KEYS,
    SemanticTagContractError,
    canonical_contract_hash,
    validate_semantic_field_result,
    validate_tag_demand_contract,
)


def _tag_value(*, value: str = "现代", rank: int = 1, weight: float | None = 1.0) -> dict:
    return {
        "value": value,
        "entity_id": "style-modern",
        "locale": "zh",
        "rank": rank,
        "weight": weight,
        "source": "model",
        "evidence_ref": "asset-version:av-001#frame-1",
        "model_version": "model-v1",
        "prompt_version": "prompt-v1",
        "normalization_version": "normalization-v1",
        "mapping_version": "mapping-v1",
        "review_status": "candidate",
    }


def _field_definition(key: str) -> dict:
    return {
        "field_key": key,
        "cardinality": "multi" if key == "object" else "single",
        "localized": True,
        "vocabulary_owner": "tpeng-semantic-platform",
        "max_values": 10 if key == "object" else 1,
        "default_value": [_tag_value()] if key == "style" else [],
    }


def valid_contract() -> dict:
    field_keys = (*PLATFORM_SEMANTIC_FIELD_KEYS, "title")
    return {
        "schema_version": "tag-demand-contract-v1",
        "semantic_schema": {
            "schema_version": "semantic-tag-schema-v1",
            "fields": {key: _field_definition(key) for key in field_keys},
        },
        "category_applicability": {
            "model_3d_su": {
                key: "required" if key in {"space", "object", "style"} else "optional"
                for key in field_keys
            }
        },
        "execution_variants": [
            {
                "site_scope": "domestic",
                "asset_scope": "whole",
                "locale": "zh",
                "category_key": "model_3d_su",
                "prompt_variant": "whole",
                "prompt_version": "prompt-v1",
                "model_version": "model-v1",
            }
        ],
        "quality_gates": {
            "style": {
                "min_precision": 0.8,
                "min_recall": 0.7,
                "min_mapping_coverage": 0.9,
                "max_conflict_rate": 0.1,
            }
        },
        "projection_targets": [
            {
                "target_key": "domestic_material_tags",
                "mode": "dry_run",
                "locale": "zh",
            }
        ],
    }


def test_platform_contract_accepts_shared_semantic_fields_and_structured_values() -> None:
    contract = valid_contract()
    parsed = validate_tag_demand_contract(contract)
    assert parsed.schema_version == "tag-demand-contract-v1"
    assert set(parsed.semantic_schema.fields) == set(PLATFORM_SEMANTIC_FIELD_KEYS) | {"title"}
    assert parsed.semantic_schema.fields["object"].cardinality == "multi"


@pytest.mark.parametrize("status", ["not_applicable", "not_detected", "needs_review"])
def test_field_null_semantics_remain_distinct(status: str) -> None:
    value = validate_semantic_field_result({"status": status, "values": []})
    assert value.status == status


def test_contract_rejects_comma_joined_canonical_values() -> None:
    contract = valid_contract()
    contract["semantic_schema"]["fields"]["style"]["default_value"] = "现代,极简"
    with pytest.raises(SemanticTagContractError, match="结构化数组"):
        validate_tag_demand_contract(contract)


@pytest.mark.parametrize("status", ["not_applicable", "not_detected"])
def test_inapplicable_and_not_detected_results_reject_values(status: str) -> None:
    with pytest.raises(SemanticTagContractError, match="必须为空"):
        validate_semantic_field_result(
            {"status": status, "values": [_tag_value()]}
        )


def test_required_result_rejects_empty_values() -> None:
    with pytest.raises(SemanticTagContractError, match="不能为空"):
        validate_semantic_field_result({"status": "required", "values": []})


def test_field_result_rejects_duplicate_ranks() -> None:
    with pytest.raises(SemanticTagContractError, match="rank 不能重复"):
        validate_semantic_field_result(
            {
                "status": "optional",
                "values": [
                    _tag_value(value="现代", rank=1, weight=0.5),
                    _tag_value(value="极简", rank=1, weight=0.5),
                ],
            }
        )


def test_field_result_rejects_aggregate_weight_over_one() -> None:
    with pytest.raises(SemanticTagContractError, match="weight 总和不能超过 1.0"):
        validate_semantic_field_result(
            {
                "status": "optional",
                "values": [
                    _tag_value(value="现代", rank=1, weight=0.7),
                    _tag_value(value="极简", rank=2, weight=0.6),
                ],
            }
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda variant: variant.update(locale="en"), "domestic.*zh"),
        (lambda variant: variant.update(site_scope="overseas"), "overseas.*en"),
        (lambda variant: variant.update(prompt_variant="single"), "whole.*prompt_variant=whole"),
        (
            lambda variant: variant.update(asset_scope="single"),
            "single.*prompt_variant=single",
        ),
    ],
)
def test_contract_rejects_invalid_execution_variant_bindings(mutation, message: str) -> None:
    contract = valid_contract()
    mutation(contract["execution_variants"][0])
    with pytest.raises(SemanticTagContractError, match=message):
        validate_tag_demand_contract(contract)


def test_contract_rejects_platform_field_missing_from_category_matrix() -> None:
    contract = valid_contract()
    del contract["category_applicability"]["model_3d_su"]["material"]
    with pytest.raises(SemanticTagContractError, match="material.*适用性矩阵"):
        validate_tag_demand_contract(contract)


def test_contract_hash_is_key_order_independent() -> None:
    contract = valid_contract()
    reordered = {
        "projection_targets": contract["projection_targets"],
        "quality_gates": contract["quality_gates"],
        "execution_variants": contract["execution_variants"],
        "category_applicability": contract["category_applicability"],
        "semantic_schema": contract["semantic_schema"],
        "schema_version": contract["schema_version"],
    }
    assert canonical_contract_hash(validate_tag_demand_contract(contract)) == canonical_contract_hash(
        validate_tag_demand_contract(reordered)
    )


def test_parsed_contract_is_frozen() -> None:
    parsed = validate_tag_demand_contract(valid_contract())
    with pytest.raises(ValidationError, match="frozen"):
        parsed.schema_version = "tag-demand-contract-v1"  # type: ignore[misc]


def test_contract_rejects_unknown_variant_category() -> None:
    contract = deepcopy(valid_contract())
    contract["execution_variants"][0]["category_key"] = "unknown_category"
    with pytest.raises(SemanticTagContractError, match="unknown_category.*适用性矩阵"):
        validate_tag_demand_contract(contract)
