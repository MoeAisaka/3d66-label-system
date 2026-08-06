from __future__ import annotations

import copy

import pytest

from app.inspiration_aesthetic_foundation import (
    AESTHETIC_CALL_B_VERSION,
    DIMENSION_KEYS,
    AestheticFoundationError,
    apply_aesthetic_v3_rules,
    canonical_foundation,
    validate_aesthetic_output,
)
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_v3_contract,
)


def valid_payload(score: int = 89) -> dict:
    return {
        "contract_version": "inspiration-aesthetic-foundation-v1",
        "aesthetic_score": score,
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": [f"{key} 可见证据"],
                "shortcomings": [f"{key} 可见不足"],
            }
            for key in DIMENSION_KEYS
        },
        "overall_evidence": ["整体可见证据"],
        "confidence": 0.82,
    }


def precheck(*, redline: str | None = None, track: str = "建筑设计") -> dict:
    reason = redline or "无红线"
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": track,
            "primary_confidence": 0.95,
        },
        "production_fields": {"reason": [reason], "trait": "实景照片"},
        "hard_defects": [],
        "image_defects": [],
        "decisive_signal_validation": {"status": "valid"},
    }


def test_contract_is_new_b_and_uses_temporary_downward_mapping() -> None:
    contract = build_inspiration_v3_contract()
    assert contract["prompt_bindings"]["call_b_version"] == AESTHETIC_CALL_B_VERSION
    block = contract["aesthetic_foundation"]
    assert block["calibration_status"] == "temporary_pending_calibration"
    assert block["boundary_policy"] == "floor_to_lower_band"
    assert block["score_thresholds"] == [
        {"min_score": 90, "level": "L1"},
        {"min_score": 75, "level": "L2"},
        {"min_score": 60, "level": "L3"},
        {"min_score": 0, "level": "L4"},
    ]


def test_strict_validator_accepts_exact_eight_dimensions() -> None:
    normalized = validate_aesthetic_output(valid_payload())
    assert normalized["aesthetic_score"] == 89
    assert tuple(normalized["dimensions"]) == DIMENSION_KEYS


@pytest.mark.parametrize("bad_grade", [0, 6, None, True])
def test_strict_validator_rejects_zero_and_illegal_grades(bad_grade) -> None:
    payload = valid_payload()
    payload["dimensions"][DIMENSION_KEYS[0]]["grade"] = bad_grade
    with pytest.raises(AestheticFoundationError, match="grade"):
        validate_aesthetic_output(payload)


def test_strict_validator_rejects_missing_extra_and_final_fields() -> None:
    missing = valid_payload()
    missing["dimensions"].pop(DIMENSION_KEYS[-1])
    with pytest.raises(AestheticFoundationError, match="八维"):
        validate_aesthetic_output(missing)
    extra = valid_payload()
    extra["dimensions"]["extra"] = copy.deepcopy(
        extra["dimensions"][DIMENSION_KEYS[0]]
    )
    with pytest.raises(AestheticFoundationError, match="八维"):
        validate_aesthetic_output(extra)
    forbidden = valid_payload()
    forbidden["final_level"] = "L2"
    with pytest.raises(AestheticFoundationError, match="最终等级"):
        validate_aesthetic_output(forbidden)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(100, "L1"), (90, "L1"), (89, "L2"), (75, "L2"),
     (74, "L3"), (60, "L3"), (59, "L4"), (0, "L4")],
)
def test_boundary_mapping_is_deterministic_and_downward(score: int, expected: str) -> None:
    contract = build_inspiration_v3_contract()
    result = apply_aesthetic_v3_rules(
        contract=contract,
        classification_map=build_inspiration_classification_map(),
        precheck=precheck(),
        foundation=valid_payload(score),
    )
    assert result["raw_level"] == expected


def test_track_cap_and_hard_defect_do_not_pollute_foundation() -> None:
    contract = build_inspiration_v3_contract()
    payload = valid_payload(96)
    before = canonical_foundation(payload)
    item = precheck(track="意向图")
    item["hard_defects"] = ["garish_color"]
    result = apply_aesthetic_v3_rules(
        contract=contract,
        classification_map=build_inspiration_classification_map(),
        precheck=item,
        foundation=payload,
    )
    assert result["inspiration_aesthetic_score"] == 96
    assert result["score"] == 60
    assert result["level"] == "L3"
    assert canonical_foundation(payload) == before
    assert result["foundation_before_rules"] == result["foundation_after_rules"]


def test_redline_is_l5_and_does_not_require_call_b() -> None:
    result = apply_aesthetic_v3_rules(
        contract=build_inspiration_v3_contract(),
        classification_map=build_inspiration_classification_map(),
        precheck=precheck(redline="是截图"),
        foundation=None,
    )
    assert result["hard_reject"] is True
    assert result["level"] == "L5"
    assert result["inspiration_aesthetic_score"] is None
