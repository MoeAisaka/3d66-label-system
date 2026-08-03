from __future__ import annotations

import pytest

from app.category_evaluation_aggregator import (
    AGGREGATOR_VERSION,
    LEVEL_SEMANTICS_VERSION,
    CategoryEvaluationAggregatorError,
    aggregate_category_evaluation,
)
from app.category_evaluation_contract import CATEGORY_EVALUATION_CONTRACT_VERSION
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


# --- Inspiration-image fixtures: three tracks 一类40+60=100 / 二类20+60=80 /
# --- 三类40+30=70, media 实拍0/效果图-5/AI-15, veto 80→79. ---


def _redline_policy() -> dict:
    return {
        "format_version": REDLINE_POLICY_FORMAT_VERSION,
        "enabled": True,
        "hit_level": "L5",
        "hit_score_cap": 49,
        "rules": [
            {
                "key": "screenshot",
                "signal": "production_fields.reason",
                "match_any": ["是截图"],
                "exemptions": [],
            }
        ],
    }


def _track_classification() -> dict:
    return {
        "format_version": "track-classification-v1",
        "tracks": [
            {
                "key": "class_one",
                "label": "一类",
                "base_score": 40,
                "dimension_max": 60,
                "track_cap": 100,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
            {
                "key": "class_two",
                "label": "二类",
                "base_score": 20,
                "dimension_max": 60,
                "track_cap": 80,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
            {
                "key": "class_three",
                "label": "三类",
                "base_score": 40,
                "dimension_max": 30,
                "track_cap": 70,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
        ],
        "default_track": "class_three",
    }


def _common_modifiers() -> dict:
    return {
        "format_version": "common-modifiers-v1",
        "media_type_penalty": {
            "baseline": "real_photo",
            "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -15, "other": 0},
        },
        "high_score_veto": {"threshold": 80, "cap_to": 79},
    }


def _contract() -> dict:
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": _common_modifiers(),
    }


def _precheck(*, trait: str = "实景照片", reason=None, hard_defects=None) -> dict:
    precheck: dict = {"production_fields": {"trait": trait, "reason": list(reason or [])}}
    if hard_defects is not None:
        precheck["hard_defects"] = hard_defects
    return precheck


def _dimensions(deductions=None, evidence=None) -> dict:
    result: dict = {"deductions": dict(deductions or {})}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def _step_score(result: dict, step: str):
    for entry in result["steps"]:
        if entry["step"] == step:
            return entry["score_after"]
    raise AssertionError(f"step {step} not recorded")


# --- Redline: direct L5 + hard_reject + score capped at 49, no scoring steps. ---


def test_redline_hit_returns_l5_hard_reject_capped_and_terminates() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片", reason=["是截图"]),
        _dimensions(),
        track_key="class_one",
    )
    assert result["hard_reject"] is True
    assert result["terminated_at"] == "redline"
    assert result["level"] == "L5"
    assert result["raw_level"] == "L5"
    assert result["score"] == 49
    assert result["hit_rules"] == ["screenshot"]
    assert result["track_key"] is None
    # No dimension/track/media/veto step ran — only the redline step.
    recorded = {entry["step"] for entry in result["steps"]}
    assert recorded == {"redline"}
    assert any(cap["cap"] == "redline" for cap in result["caps"])


# --- Class-one perfect chain (no deductions, real photo): 40+60=100 → L1. ---


def test_class_one_full_score_real_photo_is_l1() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions(),
        track_key="class_one",
    )
    assert result["hard_reject"] is False
    assert result["terminated_at"] is None
    assert result["track_key"] == "class_one"
    assert result["base_score"] == 40
    assert result["dimension_max"] == 60
    assert result["score"] == 100
    assert result["level"] == "L1"
    assert result["raw_level"] == "L1"
    assert _step_score(result, "media") == 100.0


# --- AI-image penalty: 100 - 15 = 85 → still L1, media evidence recorded. ---


def test_ai_image_penalty_keeps_l1_and_records_media_evidence() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="AI图"),
        _dimensions(),
        track_key="class_one",
    )
    assert result["score"] == 85
    assert result["level"] == "L1"
    media_note = next(s["note"] for s in result["steps"] if s["step"] == "media")
    assert "ai_image" in media_note
    assert "-15" in media_note
    assert _step_score(result, "media") == 85.0


def test_render_3d_penalty_is_minus_five() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="3D数字效果图"),
        _dimensions(),
        track_key="class_one",
    )
    assert result["score"] == 95
    assert result["level"] == "L1"


# --- Dimension deductions cumulative clamp to dimension_max. ---


def test_dimension_deductions_clamp_to_dimension_max() -> None:
    # 一类 dimension_max=60; ask for 100 total deduction — clamps to 60.
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions({"visual_structure": 70, "color": 30}),
        track_key="class_one",
    )
    # base40 + dim60 - min(100,60) = 40; real photo penalty 0.
    assert result["score"] == 40
    assert result["level"] == "L3"
    assert _step_score(result, "dimensions") == 40.0
    dim_note = next(s["note"] for s in result["steps"] if s["step"] == "dimensions")
    assert "封顶" in dim_note


def test_dimension_deductions_partial() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions({"visual_structure": 5, "color": 3}),
        track_key="class_one",
    )
    # 100 - 8 = 92 → L1.
    assert result["score"] == 92
    assert result["level"] == "L1"


# --- High-score veto: score>=80 with hard_defects → capped to 79 → L2. ---


def test_high_score_veto_caps_to_79_and_downgrades_to_l2() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片", hard_defects=["构图敷衍"]),
        _dimensions(),
        track_key="class_one",
    )
    assert result["score"] == 79
    assert result["level"] == "L2"
    # raw_level (pre-veto) reflects the un-capped 100 → L1.
    assert result["raw_level"] == "L1"
    assert any(cap["cap"] == "high_score_veto" for cap in result["caps"])


def test_high_score_veto_not_triggered_without_hard_defects() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片", hard_defects=[]),
        _dimensions(),
        track_key="class_one",
    )
    assert result["score"] == 100
    assert result["level"] == "L1"
    assert not any(cap["cap"] == "high_score_veto" for cap in result["caps"])


def test_high_score_veto_not_triggered_below_threshold() -> None:
    # Deduct to 79 (below threshold 80) even with hard_defects present.
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片", hard_defects=["构图敷衍"]),
        _dimensions({"visual_structure": 21}),
        track_key="class_one",
    )
    assert result["score"] == 79
    assert not any(cap["cap"] == "high_score_veto" for cap in result["caps"])


# --- Track caps: class_two <=80 even at full score; class_three <=70. ---


def test_class_two_capped_at_80() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions(),
        track_key="class_two",
    )
    # base20 + dim60 = 80, cap 80 → L1 boundary.
    assert result["score"] == 80
    assert result["level"] == "L1"


def test_class_three_capped_at_70() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions(),
        track_key="class_three",
    )
    # base40 + dim30 = 70, cap 70 → L2.
    assert result["score"] == 70
    assert result["level"] == "L2"


# --- score→L boundaries (80/60/40/20), L5 = worst direction. ---


@pytest.mark.parametrize(
    ("deduction", "expected_score", "expected_level"),
    [
        (0, 80, "L1"),   # 80 → L1
        (1, 79, "L2"),   # 79 → L2
        (20, 60, "L2"),  # 60 → L2 boundary
        (21, 59, "L3"),  # 59 → L3
        (40, 40, "L3"),  # 40 → L3 boundary
        (41, 39, "L4"),  # 39 → L4
        (60, 20, "L4"),  # 20 → L4 boundary
    ],
)
def test_score_to_level_boundaries(deduction, expected_score, expected_level) -> None:
    # Use class_two (base20+dim60=80, cap80) so the whole 80..20 range is reachable.
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions({"d": deduction}),
        track_key="class_two",
    )
    assert result["score"] == expected_score
    assert result["level"] == expected_level


def test_score_below_20_maps_to_l5_without_redline() -> None:
    # Custom track that can reach a sub-20 score via base_score alone.
    contract = _contract()
    contract["track_classification"]["tracks"].append({
        "key": "class_low",
        "label": "低分档",
        "base_score": 10,
        "dimension_max": 30,
        "track_cap": 40,
        "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
    })
    result = aggregate_category_evaluation(
        contract,
        _precheck(trait="实景照片"),
        _dimensions({"d": 30}),  # 10+30-30 = 10
        track_key="class_low",
    )
    assert result["score"] == 10
    assert result["level"] == "L5"
    # This L5 is score-driven, not a redline hard reject.
    assert result["hard_reject"] is False


# --- Default track fallback (track_key omitted → default_track). ---


def test_default_track_fallback_uses_default_track() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="实景照片"),
        _dimensions(),
    )
    # default_track is class_three.
    assert result["track_key"] == "class_three"
    assert result["score"] == 70


# --- Unknown/uncertain media trait falls back to `other` (penalty 0). ---


def test_unknown_trait_falls_back_to_other_and_flags_uncertainty() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="未知类型"),
        _dimensions(),
        track_key="class_one",
    )
    assert result["score"] == 100
    media_note = next(s["note"] for s in result["steps"] if s["step"] == "media")
    assert "other" in media_note
    assert "不确定性" in media_note


def test_missing_trait_falls_back_to_other() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        {"production_fields": {"reason": []}},
        _dimensions(),
        track_key="class_one",
    )
    assert result["score"] == 100
    media_note = next(s["note"] for s in result["steps"] if s["step"] == "media")
    assert "other" in media_note


# --- Fail-closed: invalid contract / unknown track_key / negative deduction. ---


def test_invalid_contract_fails_closed_with_prefixed_code() -> None:
    contract = _contract()
    contract["schema_version"] = "evaluation-category-profile-v2"
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(contract, _precheck(), _dimensions())
    assert excinfo.value.code == "contract.schema_version_unsupported"


def test_unknown_track_key_fails_closed() -> None:
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(
            _contract(), _precheck(), _dimensions(), track_key="class_nine"
        )
    assert excinfo.value.code == "track_key_unknown"


def test_negative_dimension_deduction_fails_closed() -> None:
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(
            _contract(),
            _precheck(),
            _dimensions({"visual_structure": -3}),
            track_key="class_one",
        )
    assert excinfo.value.code == "dimension_deduction_negative"


def test_missing_deductions_block_fails_closed() -> None:
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(
            _contract(), _precheck(), {}, track_key="class_one"
        )
    assert excinfo.value.code == "dimension_deductions_invalid"


def test_non_object_precheck_fails_closed() -> None:
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(_contract(), "not-a-dict", _dimensions())
    assert excinfo.value.code == "precheck_invalid"


def test_invalid_level_thresholds_override_fails_closed() -> None:
    contract = _contract()
    contract["level_thresholds"] = [{"min_score": 50, "level": "L1"}]  # no 0 catch-all
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(
            contract, _precheck(), _dimensions(), track_key="class_one"
        )
    assert excinfo.value.code == "level_thresholds_invalid"


# --- Contract-overridable level thresholds. ---


def test_contract_level_thresholds_override_is_honored() -> None:
    contract = _contract()
    # Make L1 require >=95 instead of >=80; 一类 AI图 = 85 now falls to L2.
    contract["level_thresholds"] = [
        {"min_score": 95, "level": "L1"},
        {"min_score": 60, "level": "L2"},
        {"min_score": 0, "level": "L5"},
    ]
    result = aggregate_category_evaluation(
        contract, _precheck(trait="AI图"), _dimensions(), track_key="class_one"
    )
    assert result["score"] == 85
    assert result["level"] == "L2"


# --- Output tagging + JSON-serializability + determinism. ---


def test_output_is_tagged_with_versions() -> None:
    result = aggregate_category_evaluation(
        _contract(), _precheck(), _dimensions(), track_key="class_one"
    )
    assert result["aggregator_version"] == AGGREGATOR_VERSION
    assert result["level_semantics_version"] == LEVEL_SEMANTICS_VERSION == "doc-l5-worst-v1"


def test_output_is_json_serializable() -> None:
    import json

    result = aggregate_category_evaluation(
        _contract(),
        _precheck(trait="AI图", hard_defects=["构图敷衍"]),
        _dimensions({"visual_structure": 5}, evidence={"note": "demo"}),
        track_key="class_one",
    )
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_determinism_same_input_same_output() -> None:
    contract, precheck, dims = _contract(), _precheck(trait="AI图"), _dimensions({"d": 5})
    first = aggregate_category_evaluation(contract, precheck, dims, track_key="class_one")
    second = aggregate_category_evaluation(contract, precheck, dims, track_key="class_one")
    third = aggregate_category_evaluation(
        _contract(), _precheck(trait="AI图"), _dimensions({"d": 5}), track_key="class_one"
    )
    assert first == second == third
