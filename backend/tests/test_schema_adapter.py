from app.schema_adapter import adapt_combined_aesthetic_response, is_combined_aesthetic_response
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
