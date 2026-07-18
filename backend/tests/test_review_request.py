import pytest
from pydantic import ValidationError

from app.main import ReviewRequest


def test_corrected_review_requires_level_and_reason() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(reviewer_name="审核员", decision="corrected")

    with pytest.raises(ValidationError):
        ReviewRequest(
            reviewer_name="审核员",
            decision="corrected",
            corrected_level="L4",
            note="   ",
        )


def test_corrected_review_accepts_explicit_level_and_reason() -> None:
    review = ReviewRequest(
        reviewer_name="审核员",
        decision="corrected",
        corrected_level="L4",
        note="模型把艺术性浅景深误判为画质问题",
    )

    assert review.corrected_level == "L4"


def test_non_corrected_review_rejects_corrected_level() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(
            reviewer_name="审核员",
            decision="approved",
            corrected_level="L4",
        )
