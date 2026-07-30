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


def test_grade_collapse_detects_five_matching_dimensions() -> None:
    assert aesthetic_grade_collapse(aesthetic_with_grades([3] * 5 + [2, 4, 5])) is True


def test_grade_collapse_allows_at_most_four_matching_dimensions() -> None:
    assert aesthetic_grade_collapse(aesthetic_with_grades([2, 2, 2, 3, 3, 3, 3, 4])) is False
