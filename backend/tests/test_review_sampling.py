import json
from types import SimpleNamespace

from app.review_sampling import build_review_sampling


DIMENSION_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)


def result_stub(**overrides):
    grades = {key: {"grade": (index % 4) + 1} for index, key in enumerate(DIMENSION_KEYS)}
    values = {
        "id": 101,
        "model_id": "doubao-seed-2.0-lite",
        "prompt_a_version": "space_precheck_v1.3",
        "prompt_b_version": "space_dimensions_v1.3",
        "precheck_json": json.dumps(
            {
                "classification": {"scope_status": "in_scope"},
                "media_form": {"professional_photography": {"status": "no"}},
                "image_quality": {"quality_severity": "normal"},
            }
        ),
        "aesthetic_json": json.dumps({"dimensions": grades}),
        "risk_review_json": json.dumps({"verdict": "keep"}),
        "reviews": [],
        "needs_review": False,
        "confidence": 0.95,
        "level": "L3",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def reason_codes(decision):
    return {reason["code"] for reason in decision["reasons"]}


def test_golden_low_confidence_and_collapsed_grades_are_required() -> None:
    same_grades = {key: {"grade": 4} for key in DIMENSION_KEYS}
    result = result_stub(
        confidence=0.62,
        level="L4",
        aesthetic_json=json.dumps({"dimensions": same_grades}),
    )

    decision = build_review_sampling(result, is_golden=True, combination_index=9)

    assert decision["tier"] == "required"
    assert decision["priority"] >= 90
    assert {"golden_sample", "low_confidence", "high_level", "grade_collapse"} <= reason_codes(decision)


def test_completed_human_review_is_not_queued_again() -> None:
    result = result_stub(reviews=[SimpleNamespace(decision="approved")])

    decision = build_review_sampling(result, is_golden=True, combination_index=1)

    assert decision["tier"] == "reviewed"
    assert decision["priority"] == 0
    assert reason_codes(decision) == {"human_reviewed"}


def test_large_level_shift_is_required() -> None:
    decision = build_review_sampling(
        result_stub(level="L4"),
        previous_level="L2",
        combination_index=12,
    )

    assert decision["tier"] == "required"
    assert "version_disagreement" in reason_codes(decision)


def test_stable_random_sampling_does_not_change_between_requests() -> None:
    result = result_stub(id=1042)

    first = build_review_sampling(result, combination_index=12)
    second = build_review_sampling(result, combination_index=12)

    assert first == second
    assert first["tier"] in {"sampled", "deferred"}


def test_first_results_of_new_model_prompt_combination_are_required() -> None:
    decision = build_review_sampling(result_stub(), combination_index=3)

    assert decision["tier"] == "required"
    assert "new_combination" in reason_codes(decision)
