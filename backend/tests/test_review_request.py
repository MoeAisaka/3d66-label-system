import pytest
from pydantic import ValidationError

from app.main import ReviewCorrection, ReviewRequest


def test_corrected_review_requires_dimension_correction() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(
            reviewer_name="审核员",
            decision="corrected",
            expected_stage="initial",
            expected_review_revision=0,
        )

    with pytest.raises(ValidationError):
        ReviewRequest(
            reviewer_name="审核员",
            decision="corrected",
            expected_stage="initial",
            expected_review_revision=0,
            corrected_level="L4",
            note="   ",
        )


def test_corrected_review_rejects_manually_selected_level() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(
            reviewer_name="审核员",
            decision="corrected",
            expected_stage="initial",
            expected_review_revision=0,
            corrected_level="L4",
            corrections=[
                ReviewCorrection(
                    target_type="dimension",
                    field_key="color_material",
                    model_value=5,
                    human_value=3,
                    reason_codes=["overrated"],
                )
            ],
        )


def test_non_corrected_review_rejects_corrected_level() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(
            reviewer_name="审核员",
            decision="approved",
            expected_stage="initial",
            expected_review_revision=0,
            corrected_level="L4",
        )


def test_dimension_correction_can_keep_final_level() -> None:
    review = ReviewRequest(
        reviewer_name="审核员",
        decision="corrected",
        expected_stage="initial",
        expected_review_revision=0,
        corrections=[
            ReviewCorrection(
                target_type="dimension",
                field_key="color_material",
                model_value=5,
                human_value=3,
                reason_codes=["confused_photography_with_design"],
                note="统一色调来自摄影调色，材质本身普通",
            )
        ],
    )

    assert review.corrections[0].human_value == 3


def test_dimension_correction_requires_changed_score_and_reason() -> None:
    with pytest.raises(ValidationError):
        ReviewCorrection(
            target_type="dimension",
            field_key="color_material",
            model_value=4,
            human_value=4,
            reason_codes=["overrated"],
        )

    with pytest.raises(ValidationError):
        ReviewCorrection(
            target_type="dimension",
            field_key="color_material",
            model_value=4,
            human_value=3,
            reason_codes=[],
        )


def test_production_key_field_correction_accepts_structured_values() -> None:
    tags = ReviewCorrection(
        target_type="key_field",
        field_key="production_fields.tags",
        model_value=["客厅", "现代", "简约", "木饰面"],
        human_value=["客厅", "现代", "简约", "原木"],
        reason_codes=["wrong_visible_attribute"],
    )
    media = ReviewCorrection(
        target_type="key_field",
        field_key="media_form",
        model_value={"rendering": {"status": "yes"}},
        human_value={
            "rendering": {
                "status": "no",
                "confidence": 0.95,
                "evidence": ["画面存在真实拍摄噪点"],
            }
        },
        reason_codes=["enum_mismatch"],
    )

    assert tags.human_value[-1] == "原木"
    assert media.human_value["rendering"]["status"] == "no"

    with pytest.raises(ValidationError, match="至少包含 4 个"):
        ReviewCorrection(
            target_type="key_field",
            field_key="production_fields.tags",
            model_value=["客厅", "现代", "简约", "木饰面"],
            human_value=["客厅", "现代", "简约"],
            reason_codes=["wrong_visible_attribute"],
        )

    with pytest.raises(ValidationError, match="confidence"):
        ReviewCorrection(
            target_type="key_field",
            field_key="media_form",
            model_value={"rendering": {"status": "yes"}},
            human_value={
                "rendering": {"status": "yes", "confidence": 2, "evidence": []}
            },
            reason_codes=["enum_mismatch"],
        )
