from app.worker import aesthetic_grade_collapse


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
