from app.schema_adapter import (
    adapt_combined_aesthetic_response,
    is_combined_aesthetic_response,
    normalize_precheck_business_rules,
)
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


def professional_precheck(
    *,
    evidence: list[str],
    quality_severity: str = "normal",
    scene_scope: str = "full_space",
) -> dict:
    return {
        "scene_scope": {"type": scene_scope},
        "media_form": {
            "real_photo": {"status": "yes"},
            "rendering": {"status": "no"},
            "ai_generated": {"status": "no"},
            "professional_photography": {
                "status": "yes",
                "evidence": evidence,
            },
            "documentary_record": {"status": "no"},
            "casual_snapshot": {"status": "no"},
        },
        "image_quality": {
            "quality_severity": quality_severity,
            "capture_quality": "good",
            "issues": [],
            "evidence": [],
        },
        "display_flags": {"watermark": False, "decorative_border": False},
    }


def test_three_professional_evidence_items_are_accepted() -> None:
    normalized = normalize_precheck_business_rules(
        professional_precheck(evidence=["机位与透视", "边缘与裁切", "光线控制"])
    )
    assert normalized["media_form"]["professional_photography"]["status"] == "yes"
    assert normalized["media_form"]["documentary_record"]["status"] == "no"


def test_two_professional_evidence_items_are_rejected() -> None:
    normalized = normalize_precheck_business_rules(
        professional_precheck(evidence=["机位与透视", "光线控制"])
    )
    assert normalized["media_form"]["professional_photography"]["status"] == "no"
    assert normalized["media_form"]["professional_photography"]["evidence"] == [
        "系统规则：专业摄影缺少三类互不重复的可见证据"
    ]
    assert normalized["media_form"]["documentary_record"]["status"] == "yes"


def test_slight_quality_does_not_reject_professional_photography() -> None:
    normalized = normalize_precheck_business_rules(
        professional_precheck(
            evidence=["机位与透视", "边缘与裁切", "光线控制"],
            quality_severity="slight",
        )
    )
    assert normalized["media_form"]["professional_photography"]["status"] == "yes"


def test_moderate_quality_rejects_professional_photography() -> None:
    normalized = normalize_precheck_business_rules(
        professional_precheck(
            evidence=["机位与透视", "边缘与裁切", "光线控制"],
            quality_severity="moderate",
        )
    )
    assert normalized["media_form"]["professional_photography"]["status"] == "no"


def test_partial_space_does_not_inject_quality_issue_or_reject_professional() -> None:
    precheck = professional_precheck(
        evidence=["机位与透视", "边缘与裁切", "光线控制"],
        scene_scope="partial_space",
    )
    normalized = normalize_precheck_business_rules(precheck)
    assert normalized["image_quality"]["quality_severity"] == "normal"
    assert normalized["image_quality"]["capture_quality"] == "good"
    assert "presentation_incomplete" not in normalized["image_quality"]["issues"]
    assert normalized["media_form"]["professional_photography"]["status"] == "yes"
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
    assert "watermark_or_border" in normalized["image_quality"]["issues"]
    assert normalized["media_form"]["professional_photography"]["status"] == "no"
