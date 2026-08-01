from __future__ import annotations

import pytest

from app.category_pipeline import default_pipeline, pipeline_catalog_payload, validate_pipeline_config


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
