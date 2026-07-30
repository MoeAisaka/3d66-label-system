import json
from types import SimpleNamespace

from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    canonical_hash,
    space_schema_definition_for_version,
)
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


def test_configurable_thresholds_and_policy_version_are_applied() -> None:
    decision = build_review_sampling(
        result_stub(confidence=0.82, level="L4"),
        combination_index=4,
        sample_rate=17,
        low_confidence_threshold=0.85,
        medium_confidence_threshold=0.95,
        cold_start_required_count=3,
        high_level_required_from=5,
        policy_version="smart-sampling-v1.1/policy-8",
    )

    assert decision["version"] == "smart-sampling-v1.1/policy-8"
    assert decision["sample_rate"] == 17
    assert decision["tier"] == "required"
    assert "low_confidence" in reason_codes(decision)
    assert "new_combination" not in reason_codes(decision)
    assert "high_level" not in reason_codes(decision)


def _three_dimension_snapshot() -> tuple[dict, str]:
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
    snapshot = {
        "schema_version": "strategy-bundle-v2",
        "resolved_dimension_schema_hash": canonical_hash(schema),
        "resolved_dimensions_snapshot": schema,
    }
    return schema, json.dumps(snapshot, ensure_ascii=False)


def test_sampling_uses_result_bound_non_eight_schema() -> None:
    _, snapshot = _three_dimension_snapshot()
    grades = {
        key: {"grade": 4}
        for key in (
            "visual_hierarchy",
            "inspiration_reference",
            "presentation_integrity",
        )
    }
    result = result_stub(
        strategy_snapshot_json=snapshot,
        aesthetic_json=json.dumps({"dimensions": grades}),
    )

    collapsed = build_review_sampling(result, combination_index=12)
    assert collapsed["tier"] == "required"
    assert "grade_collapse" in reason_codes(collapsed)
    assert any(
        reason["label"] == "3个维度完全同分"
        for reason in collapsed["reasons"]
    )

    grades.pop("visual_hierarchy")
    result.aesthetic_json = json.dumps({"dimensions": grades})
    incomplete = build_review_sampling(result, combination_index=12)
    assert "incomplete_dimensions" in reason_codes(incomplete)
    assert any(
        reason["label"] == "3个美感维度结果不完整"
        for reason in incomplete["reasons"]
    )


def test_sampling_fails_closed_for_tampered_dimension_snapshot() -> None:
    _, snapshot = _three_dimension_snapshot()
    payload = json.loads(snapshot)
    payload["resolved_dimension_schema_hash"] = "f" * 64
    decision = build_review_sampling(
        result_stub(
            strategy_snapshot_json=json.dumps(payload),
        ),
        combination_index=12,
    )

    assert decision["tier"] == "required"
    assert decision["priority"] == 100
    assert "dimension_contract_invalid" in reason_codes(decision)
