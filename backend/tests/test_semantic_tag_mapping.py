from __future__ import annotations

import pytest

from app.schema_adapter import attach_semantic_candidates
from app.semantic_tag_mapping import (
    SemanticExecutionRoute,
    candidate_bundle,
    candidate,
    map_standard_entities,
    normalize_semantic_candidates,
)


def fixture_mapping(field_key: str) -> dict[str, object]:
    return {
        "field_key": field_key,
        "values": {
            "现代简约": {
                "entity_id": "style.modern_minimal",
                "names": {"zh": "现代简约", "en": "Modern Minimal"},
                "aliases": ["modern minimal"],
            },
            "沙发": {
                "entity_id": "object.sofa",
                "names": {"zh": "沙发", "en": "Sofa"},
                "aliases": ["sofa"],
            },
        },
    }


def empty_mapping() -> dict[str, object]:
    return {"field_key": "material", "values": {}}


def test_mapping_keeps_structured_rank_weight_and_bilingual_names() -> None:
    bundle = candidate_bundle(
        field_key="style",
        values=[candidate("现代简约", rank=1, weight=0.7)],
    )
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=fixture_mapping("style"),
        normalization_version="semantic-normalization-v1",
        mapping_version="style-map-v1",
    )
    assert result.values[0].entity_id == "style.modern_minimal"
    assert result.values[0].localized_names == {"zh": "现代简约", "en": "Modern Minimal"}
    assert result.values[0].rank == 1
    assert result.values[0].weight == 0.7


def test_mapping_marks_unknown_values_without_discarding_evidence() -> None:
    bundle = candidate_bundle(field_key="material", values=[candidate("未知复合面")])
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=empty_mapping(),
        normalization_version="semantic-normalization-v1",
        mapping_version="material-map-v1",
    )
    assert list(result.unmapped_values) == ["未知复合面"]
    assert result.field_status == "needs_review"
    assert list(result.evidence_refs) == ["evidence:未知复合面"]


def test_duplicate_entities_merge_deterministically() -> None:
    bundle = candidate_bundle(
        field_key="object",
        values=[
            candidate("沙发", rank=1, weight=0.6),
            candidate("sofa", locale="en", rank=2, weight=0.4),
        ],
    )
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=fixture_mapping("object"),
        normalization_version="semantic-normalization-v1",
        mapping_version="object-map-v1",
    )
    assert len(result.values) == 1
    assert result.values[0].weight == pytest.approx(0.6)
    assert result.values[0].rank == 1


def test_duplicate_entities_keep_the_highest_relative_importance_level() -> None:
    bundle = candidate_bundle(
        field_key="object",
        values=[
            candidate("沙发", rank=2, weight=0.5),
            candidate("sofa", locale="en", rank=1, weight=0.7),
        ],
    )
    result = map_standard_entities(
        bundle=bundle,
        mapping_registry=fixture_mapping("object"),
        normalization_version="semantic-normalization-v1",
        mapping_version="object-map-v1",
    )
    assert result.values[0].weight == pytest.approx(0.7)
    assert result.values[0].rank == 1


def test_normalization_rejects_string_when_field_requires_array() -> None:
    route = SemanticExecutionRoute(
        contract_id=1,
        contract_version=1,
        contract_hash="a" * 64,
        site_scope="domestic",
        asset_scope="whole",
        locale="zh",
        category_key="model_3d_su",
        prompt_variant="whole",
        prompt_version="prompt-v1",
        model_version="model-v1",
        fields={"style": "optional"},
        asset_version_id=1,
    )
    with pytest.raises(ValueError, match="style 必须是数组或对象"):
        normalize_semantic_candidates(
            route=route,
            provider_payload={"semantic": {"style": "现代简约"}},
            evidence_prefix="evaluation:1",
        )


def test_schema_adapter_only_attaches_candidates_without_creating_release() -> None:
    route = SemanticExecutionRoute(
        contract_id=1,
        contract_version=1,
        contract_hash="a" * 64,
        site_scope="domestic",
        asset_scope="whole",
        locale="zh",
        category_key="model_3d_su",
        prompt_variant="whole",
        prompt_version="prompt-v1",
        model_version="model-v1",
        fields={"style": "optional"},
        asset_version_id=1,
    )
    precheck: dict[str, object] = {"classification": {"scope_status": "in_scope"}}
    normalized = attach_semantic_candidates(
        precheck,
        route=route,
        provider_payload={"semantic": {"style": [{"value": "现代简约", "rank": 1, "weight": 0.7, "evidence": ["画面"]}]}},
        evidence_prefix="evaluation:1",
    )
    assert normalized["semantic_candidates"]["style"][0]["value"] == "现代简约"
    assert "label_release" not in normalized
    assert "published_label" not in normalized
