from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    space_schema_definition_for_version,
)
from app.risk_review import (
    apply_risk_review,
    build_risk_review_system_prompt,
    build_risk_review_user_prompt,
    risk_review_reasons,
)


def _aesthetic(grades: list[int]) -> dict:
    keys = (
        "composition_viewpoint",
        "lighting_atmosphere",
        "color_material",
        "spatial_design_furnishing",
        "visual_hierarchy",
        "detail_completion",
        "inspiration_reference",
        "presentation_integrity",
    )
    return {
        "dimensions": {
            key: {"grade": grade, "evidence": ["e1", "e2"], "defects": []}
            for key, grade in zip(keys, grades, strict=True)
        },
        "decision_rules": {"level_cap": "none", "level_cap_reasons": []},
        "needs_review": False,
    }


def test_high_risk_review_only_applies_conservative_corrections() -> None:
    precheck = {
        "classification": {"primary_category": "通用空间"},
        "media_form": {
            "real_photo": {"status": "yes"},
            "professional_photography": {"status": "yes", "evidence": ["a", "b", "c", "d"]},
            "documentary_record": {"status": "no"},
        },
        "image_quality": {"quality_severity": "normal", "evidence": ["清晰"]},
    }
    aesthetic = _aesthetic([5, 4, 4, 3, 4, 5, 4, 4])
    scoring = {"level": "L4", "score": 82.53}

    reasons = risk_review_reasons(precheck, aesthetic, scoring)
    assert "模型判定为专业摄影" in reasons
    assert "初评分达到L4" in reasons
    assert "存在2个5级维度" in reasons
    assert "initial_score" in build_risk_review_user_prompt(precheck, aesthetic, scoring)

    report = apply_risk_review(
        precheck,
        aesthetic,
        {
            "verdict": "downgrade",
            "risk_reasons": ["常规现场记录，光线偏平且细节不足"],
            "professional_photography": "no",
            "documentary_record": "yes",
            "quality_severity": "moderate",
            "dimension_grades": {
                "composition_viewpoint": 3,
                "lighting_atmosphere": 2,
                "color_material": 2,
                "spatial_design_furnishing": 5,
                "visual_hierarchy": 3,
                "detail_completion": 2,
                "inspiration_reference": 2,
                "presentation_integrity": 2,
            },
            "level_cap": "L3",
            "confidence": 0.9,
        },
    )

    assert precheck["media_form"]["professional_photography"]["status"] == "no"
    assert precheck["media_form"]["documentary_record"]["status"] == "yes"
    assert precheck["image_quality"]["quality_severity"] == "moderate"
    assert aesthetic["dimensions"]["composition_viewpoint"]["grade"] == 3
    assert aesthetic["dimensions"]["spatial_design_furnishing"]["grade"] == 3
    assert aesthetic["decision_rules"]["level_cap"] == "L3"
    assert len(report["corrections"]) == 11


def test_low_risk_result_does_not_trigger_extra_call() -> None:
    precheck = {
        "media_form": {"professional_photography": {"status": "no"}},
        "image_quality": {"quality_severity": "slight"},
    }
    aesthetic = _aesthetic([2, 3, 3, 3, 3, 3, 2, 2])
    assert risk_review_reasons(precheck, aesthetic, {"level": "L2", "score": 55}) == []


def test_non_eight_schema_limits_risk_review_to_bound_dimensions() -> None:
    schema = space_schema_definition_for_version(ACTIVE_V13_VERSION)
    keys = [
        "visual_hierarchy",
        "inspiration_reference",
        "presentation_integrity",
    ]
    schema["dimensions"] = [
        item for item in schema["dimensions"] if item["key"] in keys
    ]
    schema["output_contract"]["dimension_output_keys"] = keys
    schema["risk_review"]["dimension_keys"] = keys
    aesthetic = {
        "dimensions": {
            "visual_hierarchy": {"grade": 5, "evidence": ["a", "b"]},
            "inspiration_reference": {
                "grade": 4,
                "evidence": ["a", "b"],
            },
            "presentation_integrity": {
                "grade": 4,
                "evidence": ["a", "b"],
            },
        },
        "decision_rules": {"level_cap": "none"},
        "needs_review": False,
    }
    precheck = {
        "media_form": {
            "professional_photography": {"status": "no"},
            "documentary_record": {"status": "no"},
        },
        "image_quality": {"quality_severity": "normal"},
    }

    assert risk_review_reasons(
        precheck,
        aesthetic,
        {"level": "L3"},
        dimension_schema=schema,
    ) == ["存在1个5级维度"]
    system_prompt = build_risk_review_system_prompt(schema)
    assert '"visual_hierarchy": 1' in system_prompt
    assert "composition_viewpoint" not in system_prompt

    report = apply_risk_review(
        precheck,
        aesthetic,
        {
            "verdict": "downgrade",
            "risk_reasons": ["层级证据不足"],
            "dimension_grades": {
                "visual_hierarchy": 3,
                "composition_viewpoint": 1,
            },
            "level_cap": "none",
            "confidence": 0.9,
        },
        dimension_schema=schema,
    )
    assert aesthetic["dimensions"]["visual_hierarchy"]["grade"] == 3
    assert all(
        correction["field"] != "dimensions.composition_viewpoint"
        for correction in report["corrections"]
    )
