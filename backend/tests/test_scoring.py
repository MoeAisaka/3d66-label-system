from __future__ import annotations

from app.scoring import calculate_corrected_score, calculate_score


DIMENSIONS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)


def precheck(*, confidence: float = 0.95, scope: str = "in_scope") -> dict:
    return {
        "classification": {
            "scope_status": scope,
            "primary_confidence": confidence,
        },
        "image_quality": {
            "quality_severity": "good",
            "confidence": 0.95,
            "evidence": [],
        },
        "media_form": {},
        "needs_review": False,
        "review_reasons": [],
    }


def aesthetic(grade: int = 4) -> dict:
    return {
        "dimensions": {key: {"grade": grade} for key in DIMENSIONS},
        "special_checks": {},
        "assessment_confidence": 0.9,
        "needs_review": False,
        "review_reasons": [],
    }


def test_uniform_grade_four_is_l4() -> None:
    result = calculate_score(precheck(), aesthetic(4))
    assert result["formal"] is True
    assert result["score"] == 82.0
    assert result["level"] == "L4"
    assert result["caps"] == []


def test_ai_image_is_capped_at_l4() -> None:
    check = precheck()
    check["media_form"]["ai_generated"] = {"status": "yes", "confidence": 0.93}
    result = calculate_score(check, aesthetic(5))
    assert result["raw_level"] == "L5"
    assert result["level"] == "L4"
    assert result["caps"][0]["cap"] == "L4"


def test_casual_snapshot_is_capped_at_l2() -> None:
    check = precheck()
    check["media_form"]["casual_snapshot"] = {"status": "yes", "confidence": 0.4}
    result = calculate_score(check, aesthetic(5))
    assert result["raw_level"] == "L5"
    assert result["level"] == "L2"
    assert result["score"] == 59.0
    assert any(item["cap"] == "L2" and "随拍图" in item["reason"] for item in result["caps"])
    assert result["needs_review"] is True


def test_damaged_quality_is_capped_at_l2() -> None:
    for severity in ("slight", "moderate", "severe", "unusable"):
        check = precheck()
        check["image_quality"]["quality_severity"] = severity
        result = calculate_score(check, aesthetic(5))
        assert result["raw_level"] == "L5"
        assert result["level"] == "L2"
        assert result["score"] == 59.0
        assert any(item["cap"] == "L2" and "画质受损" in item["reason"] for item in result["caps"])


def test_severe_quality_with_evidence_is_capped_at_l1() -> None:
    check = precheck()
    check["image_quality"] = {
        "quality_severity": "severe",
        "confidence": 0.91,
        "evidence": ["明显失焦", "大面积压缩块"],
    }
    result = calculate_score(check, aesthetic(5))
    assert result["level"] == "L1"


def test_low_classification_confidence_has_no_formal_score() -> None:
    result = calculate_score(precheck(confidence=0.4), aesthetic(5))
    assert result["formal"] is False
    assert result["score"] is None
    assert result["level"] is None
    assert result["needs_review"] is True


def test_out_of_scope_skips_aesthetic_scoring() -> None:
    result = calculate_score(precheck(scope="out_of_scope"), None)
    assert result["formal"] is False
    assert result["score"] is None
    assert result["level"] is None


def test_calibrated_uniform_high_grades_with_weak_evidence_are_capped() -> None:
    result_aesthetic = aesthetic(4)
    result_aesthetic["scoring_profile"] = "space_aesthetic_v1.3"
    for item in result_aesthetic["dimensions"].values():
        item["evidence"] = ["只有一条笼统证据"]
    result = calculate_score(precheck(), result_aesthetic)
    assert result["raw_level"] == "L4"
    assert result["level"] == "L3"
    assert result["score"] == 74.0
    assert result["needs_review"] is True
    assert any("高分证据不足" in reason for reason in result["review_reasons"])


def test_calibrated_l5_requires_high_confidence() -> None:
    result_aesthetic = aesthetic(5)
    result_aesthetic["scoring_profile"] = "space_aesthetic_v1.3"
    result_aesthetic["assessment_confidence"] = 0.8
    for item in result_aesthetic["dimensions"].values():
        item["evidence"] = ["优势一", "优势二", "优势三"]
    result = calculate_score(precheck(), result_aesthetic)
    assert result["raw_level"] == "L5"
    assert result["level"] == "L4"
    assert result["score"] == 89.0


def test_calibrated_uniform_mid_grades_require_review() -> None:
    result_aesthetic = aesthetic(3)
    result_aesthetic["scoring_profile"] = "space_aesthetic_v1.3"
    result = calculate_score(precheck(), result_aesthetic)
    assert result["level"] == "L3"
    assert result["needs_review"] is True
    assert any("中间分坍缩" in reason for reason in result["review_reasons"])


def test_human_dimension_correction_recalculates_score_and_level() -> None:
    result = calculate_corrected_score(
        precheck(),
        aesthetic(4),
        [
            {
                "target_type": "dimension",
                "field_key": "spatial_design_furnishing",
                "human_value": 1,
            }
        ],
    )
    assert result["score"] == 70.84
    assert result["level"] == "L3"
