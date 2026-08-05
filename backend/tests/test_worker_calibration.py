from app.models import EvaluationJob
from app.worker import (
    INSPIRATION_AUTHORITATIVE_PRECHECK_PROMPT_CONTRACT,
    _is_inspiration_baseline_job,
    aesthetic_grade_collapse,
)


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


def aesthetic_with_grades(grades: list[int]) -> dict[str, object]:
    return {
        "dimensions": {
            key: {"grade": grade}
            for key, grade in zip(DIMENSIONS, grades, strict=True)
        }
    }


def test_grade_collapse_detects_uniform_and_seven_to_one_results() -> None:
    assert aesthetic_grade_collapse(aesthetic_with_grades([3] * 8)) is True
    assert aesthetic_grade_collapse(aesthetic_with_grades([3] * 7 + [4])) is True


def test_grade_collapse_allows_evidence_based_spread() -> None:
    assert aesthetic_grade_collapse(aesthetic_with_grades([2, 2, 2, 3, 3, 3, 3, 4])) is False


def test_inspiration_baseline_excludes_legacy_production_fields_contract() -> None:
    inspiration = EvaluationJob(
        asset_id=1,
        category_key="inspiration_image",
        baseline_regression_item_id=10,
    )
    ordinary_inspiration = EvaluationJob(
        asset_id=1,
        category_key="inspiration_image",
    )
    space_baseline = EvaluationJob(
        asset_id=1,
        category_key="space_image",
        baseline_regression_item_id=10,
    )

    assert _is_inspiration_baseline_job(inspiration) is True
    assert _is_inspiration_baseline_job(ordinary_inspiration) is False
    assert _is_inspiration_baseline_job(space_baseline) is False


def test_inspiration_baseline_uses_minimum_authoritative_precheck_contract() -> None:
    prompt = INSPIRATION_AUTHORITATIVE_PRECHECK_PROMPT_CONTRACT
    for field in (
        "redline_triggered",
        "hard_defects",
        "image_defects",
        "decisive_evidence",
        "decision_status",
        "uncertain_fields",
    ):
        assert field in prompt
    assert "title" not in prompt and "seotitle" not in prompt and "tags" not in prompt
