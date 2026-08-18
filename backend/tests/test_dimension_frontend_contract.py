from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.dimension_schema_registry import canonical_hash
from app.main import _evaluation_dimension_schema_payload
from app.model_3d_su_category_seed import (
    build_model_3d_su_classification_map,
    build_model_3d_su_contract,
    build_model_3d_su_subcategory_dimensions,
)
from app.v3_review_dimensions import calculate_v3_review_corrected_score


def _result(
    *,
    definition: dict | None = None,
    strategy_version: str | None = "strategy-bundle-v2",
) -> SimpleNamespace:
    snapshot: dict = {}
    if strategy_version is not None:
        snapshot["schema_version"] = strategy_version
    if definition is not None and strategy_version == "strategy-bundle-v2":
        snapshot.update(
            {
                "resolved_dimension_schema_id": 91,
                "resolved_dimension_schema_key": "dimension.non-eight",
                "resolved_dimension_schema_version": "test-v1",
                "resolved_dimension_schema_hash": canonical_hash(definition),
                "resolved_dimensions_snapshot": definition,
            }
        )
    return SimpleNamespace(
        strategy_snapshot_json=(
            json.dumps(snapshot, ensure_ascii=False)
            if strategy_version is not None
            else None
        ),
        aesthetic_json=json.dumps(
            {
                "scoring_profile": "space_aesthetic_v1.3",
                "dimensions": {
                    "clarity": {"grade": 4},
                    "utility": {"grade": 3},
                },
            },
            ensure_ascii=False,
        ),
    )


def test_frontend_payload_exposes_non_eight_bound_contract() -> None:
    definition = {
        "format_version": "dimension-schema-definition-v1",
        "dimensions": [
            {"key": "clarity", "label": "清晰度"},
            {"key": "novelty", "label": "新鲜度"},
            {"key": "utility", "label": "可用性"},
        ],
        "output_contract": {
            "dimension_output_keys": [
                "clarity",
                "novelty",
                "utility",
            ],
            "unknown_key_policy": "reject",
        },
    }

    payload = _evaluation_dimension_schema_payload(
        _result(definition=definition)
    )

    assert payload == {
        "status": "resolved",
        "schema_id": 91,
        "schema_key": "dimension.non-eight",
        "version": "test-v1",
        "canonical_hash": canonical_hash(definition),
        "legacy_derived": False,
        "dimension_keys": ["clarity", "novelty", "utility"],
        "dimension_selection": None,
        "dimension_mode": "all",
        "definition": definition,
        "error": None,
    }


def test_frontend_payload_keeps_legacy_results_readable() -> None:
    payload = _evaluation_dimension_schema_payload(
        _result(strategy_version=None)
    )

    assert payload["status"] == "resolved"
    assert payload["schema_id"] is None
    assert payload["legacy_derived"] is True
    assert len(payload["dimension_keys"]) == 8
    assert (
        payload["definition"]["compatibility_revision"]
        == "active_v1_3"
    )


def test_frontend_payload_fails_closed_for_invalid_contract() -> None:
    payload = _evaluation_dimension_schema_payload(
        _result(strategy_version="unknown")
    )

    assert payload["status"] == "invalid"
    assert payload["definition"] is None
    assert payload["dimension_keys"] == ["clarity", "utility"]
    assert "不受支持" in payload["error"]


def test_frontend_payload_uses_frozen_v3_track_dimensions_instead_of_legacy_space_schema() -> None:
    dimension_keys = [
        "model_detail",
        "material_rendering",
        "lighting",
        "design_trend",
        "visual_composition",
    ]
    frozen_bundle = {
        "contract": build_model_3d_su_contract(),
        "classification_map": build_model_3d_su_classification_map(),
        "subcategory_dimensions": build_model_3d_su_subcategory_dimensions(),
        "config_revision": 2,
    }
    result = SimpleNamespace(
        strategy_snapshot_json=None,
        aesthetic_json=json.dumps(
            {
                "dimensions": {
                    key: {"grade": 5, "evidence": [f"{key} 可见表现完整"]}
                    for key in dimension_keys
                }
            },
            ensure_ascii=False,
        ),
        scoring_json=json.dumps(
            {
                "scoring_mode": "v3_authoritative",
                "dimension_scoring_mode": "grade_fallback",
                "track_key": "space_building",
                "score": 100,
                "level": "L1",
                "caps": [],
            },
            ensure_ascii=False,
        ),
        job=SimpleNamespace(
            category_key="model_3d_su",
            category_profile_snapshot_json=json.dumps(
                {
                    "schema_version": "evaluation-category-profile-v2",
                    "category_key": "model_3d_su",
                    "v3_authoritative_bundle": frozen_bundle,
                },
                ensure_ascii=False,
            ),
        ),
    )

    payload = _evaluation_dimension_schema_payload(result)

    assert payload["status"] == "resolved"
    assert payload["schema_key"] == "model_3d_su_aesthetic:space_building"
    assert payload["dimension_keys"] == dimension_keys
    assert [item["label"] for item in payload["definition"]["dimensions"]] == [
        "模型细节",
        "质感渲染",
        "光感表现",
        "设计感及流行度",
        "视觉构图",
    ]
    assert payload["definition"]["aggregation"] == {
        "preview_mode": "v3_grade_bridge",
        "score_round_digits": 0,
        "base_score": 0,
        "dimension_max": 100,
        "track_cap": 100,
        "level_scale": frozen_bundle["contract"]["level_scale"]["levels"],
    }


def test_v3_review_correction_recalculates_with_frozen_grade_contract() -> None:
    dimension_keys = [
        "model_detail",
        "material_rendering",
        "lighting",
        "design_trend",
        "visual_composition",
    ]
    frozen_bundle = {
        "contract": build_model_3d_su_contract(),
        "classification_map": build_model_3d_su_classification_map(),
        "subcategory_dimensions": build_model_3d_su_subcategory_dimensions(),
        "config_revision": 2,
    }
    result = SimpleNamespace(
        precheck_json=json.dumps(
            {
                "classification": {
                    "scope_status": "in_scope",
                    "primary_category": "家装",
                    "primary_confidence": 0.99,
                }
            },
            ensure_ascii=False,
        ),
        aesthetic_json=json.dumps(
            {
                "dimensions": {
                    key: {"grade": 5, "evidence": [f"{key} 可见表现完整"]}
                    for key in dimension_keys
                }
            },
            ensure_ascii=False,
        ),
        scoring_json=json.dumps(
            {
                "scoring_mode": "v3_authoritative",
                "dimension_scoring_mode": "grade_fallback",
                "track_key": "space_building",
                "score": 100,
                "level": "L1",
            },
            ensure_ascii=False,
        ),
        job=SimpleNamespace(
            category_key="model_3d_su",
            category_profile_snapshot_json=json.dumps(
                {"v3_authoritative_bundle": frozen_bundle},
                ensure_ascii=False,
            ),
        ),
    )

    recalculated = calculate_v3_review_corrected_score(
        result,
        [
            {
                "target_type": "dimension",
                "field_key": "material_rendering",
                "model_value": 5,
                "human_value": 1,
                "reason_codes": ["overrated"],
            }
        ],
    )

    assert recalculated == {"score": 75, "level": "L2"}

    with pytest.raises(ValueError, match="未知维度"):
        calculate_v3_review_corrected_score(
            result,
            [
                {
                    "target_type": "dimension",
                    "field_key": "legacy_dimension",
                    "model_value": 5,
                    "human_value": 1,
                    "reason_codes": ["overrated"],
                }
            ],
        )
