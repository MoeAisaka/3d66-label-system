from __future__ import annotations

import pytest

from app.category_pipeline import (
    dimension_options_from_definition,
    dimension_selection_payload,
    default_pipeline,
    pipeline_catalog_payload,
    validate_pipeline_config,
)


def test_pipeline_catalog_exposes_controlled_dimensions_and_model_nodes() -> None:
    catalog = pipeline_catalog_payload()

    assert {item["key"] for item in catalog["dimension_options"]} >= {
        "composition_viewpoint",
        "presentation_integrity",
    }
    assert catalog["model_nodes"][0] == {
        "key": "evaluation_main",
        "label": "主评测",
        "required": True,
    }
    assert [item["key"] for item in catalog["dimension_modes"]] == [
        "all",
        "selected",
        "none",
    ]
    assert catalog["dimension_config_contract"] == {
        "schema_version": "category-dimension-config-v1",
        "selected_key_field": "selected_keys",
        "legacy_selected_key_field": "enabled_keys",
        "published_schemas_immutable": True,
        "selection_is_frozen_per_job": True,
    }


def test_pipeline_normalizes_optional_model_nodes_and_rejects_unknown_contract_keys() -> None:
    pipeline = default_pipeline("space_image")
    pipeline["model_nodes"] = {"evaluation_main": True}

    normalized = validate_pipeline_config(pipeline)

    assert normalized["model_nodes"]["evaluation_main"] is True
    assert normalized["model_nodes"]["pdf_summary"] is False

    pipeline["model_nodes"]["python.user_node"] = True
    with pytest.raises(ValueError, match="未知模型节点"):
        validate_pipeline_config(pipeline)


def test_pipeline_requires_main_node_and_known_selected_dimensions() -> None:
    pipeline = default_pipeline("space_image")
    pipeline["model_nodes"]["evaluation_main"] = False
    with pytest.raises(ValueError, match="主评测节点"):
        validate_pipeline_config(pipeline)

    pipeline = default_pipeline("space_image")
    pipeline["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "enabled_keys": ["invented_metric"],
    }
    with pytest.raises(ValueError, match="未知维度指标"):
        validate_pipeline_config(pipeline)


def test_dimension_modes_normalize_legacy_and_prompt_only_contracts() -> None:
    pipeline = default_pipeline("space_image")
    normalized = validate_pipeline_config(pipeline)
    assert normalized["dimensions"] == {
        "enabled": True,
        "mode": "all",
        "selected_keys": [],
        "enabled_keys": [],
    }

    pipeline["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "enabled_keys": ["composition_viewpoint"],
    }
    normalized = validate_pipeline_config(pipeline)
    assert normalized["dimensions"]["selected_keys"] == [
        "composition_viewpoint"
    ]
    assert normalized["dimensions"]["enabled_keys"] == [
        "composition_viewpoint"
    ]

    pipeline["dimensions"] = {"enabled": False, "mode": "none"}
    normalized = validate_pipeline_config(pipeline)
    selection = dimension_selection_payload(normalized)
    assert normalized["dimensions"] == {
        "enabled": False,
        "mode": "none",
        "selected_keys": [],
        "enabled_keys": [],
    }
    assert selection["enabled"] is False
    assert selection["prompt_only"] is True
    assert selection["effective_keys"] == []

    # Older category-pipeline-v1 clients only knew all/selected.  Their
    # disabled payload is accepted, but never persisted as a contradiction.
    pipeline["dimensions"] = {"enabled": False, "mode": "all"}
    assert validate_pipeline_config(pipeline)["dimensions"]["mode"] == "none"


@pytest.mark.parametrize(
    ("dimensions", "message"),
    [
        ({"enabled": True, "mode": "none"}, "开关与模式不一致"),
        (
            {
                "enabled": True,
                "mode": "selected",
                "selected_keys": [],
            },
            "至少选择一个指标",
        ),
        (
            {
                "enabled": True,
                "mode": "selected",
                "selected_keys": [
                    "composition_viewpoint",
                    "composition_viewpoint",
                ],
            },
            "不能重复",
        ),
        (
            {
                "enabled": True,
                "mode": "all",
                "selected_keys": ["composition_viewpoint"],
            },
            "不能携带",
        ),
        (
            {
                "enabled": False,
                "mode": "none",
                "selected_keys": ["composition_viewpoint"],
            },
            "不能携带",
        ),
        (
            {
                "enabled": True,
                "mode": "selected",
                "selected_keys": ["composition_viewpoint"],
                "enabled_keys": ["presentation_integrity"],
            },
            "不一致",
        ),
        (
            {"enabled": True, "mode": "all", "unexpected": True},
            "未知字段",
        ),
    ],
)
def test_dimension_config_rejects_contradictions_and_invalid_shapes(
    dimensions: dict[str, object],
    message: str,
) -> None:
    pipeline = default_pipeline("space_image")
    pipeline["dimensions"] = dimensions

    with pytest.raises(ValueError, match=message):
        validate_pipeline_config(pipeline)


def test_dimension_selection_uses_bound_schema_keys_without_mutating_definition() -> None:
    definition = {
        "dimensions": [
            {"key": "texture", "label": "纹理", "display_order": 2, "weight": 0.4},
            {"key": "finish", "label": "工艺", "display_order": 1, "weight": 0.6},
        ]
    }
    original = {
        "dimensions": [dict(item) for item in definition["dimensions"]]
    }
    options = dimension_options_from_definition(definition)
    pipeline = default_pipeline("material_image")
    pipeline["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "selected_keys": ["finish"],
    }
    normalized = validate_pipeline_config(
        pipeline,
        allowed_dimension_keys={"texture", "finish"},
    )

    selection = dimension_selection_payload(
        normalized,
        dimension_options=options,
        schema_key="material",
        schema_version="v1",
        schema_hash="a" * 64,
    )

    assert selection["effective_keys"] == ["finish"]
    assert selection["source_schema"] == {
        "schema_key": "material",
        "version": "v1",
        "canonical_hash": "a" * 64,
    }
    assert definition == original


def test_dimension_management_is_isolated_per_category() -> None:
    space = default_pipeline("space_image")
    material = default_pipeline("material_image")
    space["dimensions"] = {
        "enabled": True,
        "mode": "selected",
        "selected_keys": ["composition_viewpoint"],
    }
    material["dimensions"] = {
        "enabled": False,
        "mode": "none",
        "selected_keys": [],
    }

    space_selection = dimension_selection_payload(
        validate_pipeline_config(space)
    )
    material_selection = dimension_selection_payload(
        validate_pipeline_config(material)
    )

    assert space_selection["effective_keys"] == ["composition_viewpoint"]
    assert material_selection["effective_keys"] == []
    assert dimension_selection_payload(
        validate_pipeline_config(default_pipeline("space_image"))
    )["mode"] == "all"
