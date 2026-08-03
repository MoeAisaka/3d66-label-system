import pytest

from app.schema_adapter import (
    adapt_combined_aesthetic_response,
    is_combined_aesthetic_response,
    normalize_aesthetic_dimensions_for_schema,
    normalize_precheck_business_rules,
    normalize_production_fields,
)
from app.dimension_schema_registry import space_schema_definition_for_scoring_profile
from app.scoring import calculate_score


def combined_payload() -> dict:
    dimensions = {
        "composition_viewpoint": {"grade": 3, "evidence": ["构图证据"]},
        "lighting_atmosphere": {"grade": 3},
        "color_material": {"grade": 3},
        "spatial_design_coherence": {"grade": 5},
        "visual_hierarchy": {"grade": 3},
        "detail_finish": {"grade": 3},
        "contemporary_relevance": {"grade": 3},
        "presentation_integrity": {"grade": 3},
    }
    return {
        "prompt_version": "space_aesthetic_v1.3-draft.2",
        "scope": {"is_in_scope": True, "profile_route": "space"},
        "classification": {
            "primary_category": "住宅设计",
            "category_confidence": 0.95,
        },
        "media_analysis": {
            "media_type": "real_photo",
            "ai_status": "no",
            "shooting_style": "professional",
        },
        "special_flags": {
            "is_collage": False,
            "is_multi_view_layout": False,
            "is_unfinished_site": False,
            "is_pure_white_product": False,
        },
        "quality_analysis": {
            "asset_file_damage": "none",
            "quality_issue_codes": [],
            "observable_evidence": ["画面清晰"],
        },
        "dimensions": dimensions,
        "decision_rules": {
            "hard_gate_triggered": False,
            "level_cap": "none",
            "manual_review_required": False,
        },
        "overall_confidence": 0.9,
    }


def test_combined_response_is_adapted_and_scored_with_declared_weights() -> None:
    payload = combined_payload()
    assert is_combined_aesthetic_response(payload) is True

    precheck, aesthetic = adapt_combined_aesthetic_response(payload)
    assert precheck["classification"]["scope_status"] == "in_scope"
    assert precheck["classification"]["primary_confidence"] == 0.95
    assert aesthetic["dimensions"]["spatial_design_furnishing"]["grade"] == 5
    assert aesthetic["dimensions"]["detail_completion"]["grade"] == 3
    assert aesthetic["dimensions"]["inspiration_reference"]["grade"] == 3

    scoring = calculate_score(precheck, aesthetic)
    assert scoring["formal"] is True
    assert scoring["score"] == 69.8
    assert scoring["level"] == "L3"


def test_combined_response_applies_declared_level_cap() -> None:
    payload = combined_payload()
    payload["dimensions"] = {
        key: {**value, "grade": 5} for key, value in payload["dimensions"].items()
    }
    payload["decision_rules"]["level_cap"] = "L3"
    payload["decision_rules"]["level_cap_reasons"] = ["现场记录图最高 L3"]
    precheck, aesthetic = adapt_combined_aesthetic_response(payload)

    scoring = calculate_score(precheck, aesthetic)
    assert scoring["raw_level"] == "L5"
    assert scoring["level"] == "L3"
    assert scoring["caps"][-1]["reason"] == "现场记录图最高 L3"


def test_aesthetic_aliases_are_canonicalized_without_dropping_unknown_keys() -> None:
    original = {
        "dimensions": {
            "spatial_design_coherence": {"grade": 3},
            "detail_finish": {"grade": 4},
            "contemporary_relevance": {"grade": 5},
            "future_dimension": {"grade": 2},
        }
    }
    schema = space_schema_definition_for_scoring_profile("space_aesthetic_v1.3")

    normalized = normalize_aesthetic_dimensions_for_schema(original, schema)

    assert normalized is not original
    assert normalized["dimensions"]["spatial_design_furnishing"]["grade"] == 3
    assert normalized["dimensions"]["detail_completion"]["grade"] == 4
    assert normalized["dimensions"]["inspiration_reference"]["grade"] == 5
    assert "spatial_design_coherence" not in normalized["dimensions"]
    assert normalized["dimensions"]["future_dimension"] == {"grade": 2}
    assert "spatial_design_coherence" in original["dimensions"]


def test_partial_space_is_not_normal_or_professional_in_business_rules() -> None:
    precheck = {
        "scene_scope": {"type": "partial_space"},
        "media_form": {
            "real_photo": {"status": "yes"},
            "rendering": {"status": "no"},
            "ai_generated": {"status": "no"},
            "professional_photography": {"status": "yes"},
            "documentary_record": {"status": "no"},
            "casual_snapshot": {"status": "no"},
        },
        "image_quality": {
            "quality_severity": "normal",
            "capture_quality": "good",
            "issues": [],
            "evidence": [],
        },
        "display_flags": {"watermark": False, "decorative_border": False},
    }
    normalized = normalize_precheck_business_rules(precheck)
    assert normalized["image_quality"]["quality_severity"] == "slight"
    assert normalized["image_quality"]["capture_quality"] == "acceptable"
    assert normalized["media_form"]["professional_photography"]["status"] == "no"
    assert normalized["media_form"]["documentary_record"]["status"] == "yes"


def test_rendering_with_watermark_is_not_professional_or_normal() -> None:
    precheck = {
        "scene_scope": {"type": "full_space"},
        "media_form": {
            "real_photo": {"status": "no"},
            "rendering": {"status": "yes"},
            "ai_generated": {"status": "no"},
            "professional_photography": {"status": "yes"},
        },
        "image_quality": {
            "quality_severity": "normal",
            "render_fidelity": "good",
            "issues": [],
            "evidence": [],
        },
        "display_flags": {"watermark": True, "decorative_border": False},
    }
    normalized = normalize_precheck_business_rules(precheck)
    assert normalized["image_quality"]["quality_severity"] == "slight"
    assert normalized["image_quality"]["render_fidelity"] == "acceptable"
    assert normalized["media_form"]["professional_photography"]["status"] == "no"


def test_production_fields_contract_normalizes_legacy_top_level_payload() -> None:
    payload = {
        "title": "现代客厅",
        "seotitle": "现代简约客厅空间设计效果图",
        "category": "住宅空间，客厅",
        "style": "现代简约",
        "tags": ["客厅", "现代", "简约", "木饰面", "客厅"],
        "cons": "电视墙留白偏多，视觉重心略散。",
        "design": "以横向线条和暖木材质组织开放客厅。",
        "score": 82,
        "reason": [],
        "image_defects": "",
        "trait": "3D数字效果图",
        "image_quality": {"quality_severity": "normal"},
        "media_form": {
            "rendering": {
                "status": "yes",
                "confidence": 0.98,
                "evidence": ["材质和灯光呈现为数字渲染"],
            }
        },
    }

    normalized = normalize_production_fields(payload, required=True)

    assert normalized["production_fields"]["tags"] == [
        "客厅", "现代", "简约", "木饰面"
    ]
    assert normalized["production_fields"]["score"] == 82


def test_production_fields_contract_rejects_missing_tags_and_invalid_media() -> None:
    payload = {
        "production_fields": {
            "title": "客厅",
            "seotitle": "客厅效果图",
            "category": "住宅空间，客厅",
            "style": "现代",
            "tags": ["客厅", "现代", "简约"],
            "cons": "可见缺点",
            "design": "可见设计",
            "score": 80,
            "reason": [],
            "image_defects": "",
            "trait": "3D数字效果图",
        },
        "image_quality": {"quality_severity": "normal"},
        "media_form": {
            "rendering": {"status": "maybe", "confidence": 2, "evidence": []}
        },
    }

    with pytest.raises(ValueError, match="至少包含 4 个"):
        normalize_production_fields(payload, required=True)
