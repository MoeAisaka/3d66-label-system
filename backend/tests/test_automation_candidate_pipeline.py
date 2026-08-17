import pytest

from app.automation_candidate_pipeline import (
    build_immutable_candidate_package,
    build_three_role_regression_plan,
)


def _candidate():
    return {
        "category_key": "inspiration_image",
        "lane_key": "space_image|incremental|2|abc|A+B+V3",
        "mechanism_fingerprint": "a" * 64,
        "route_decision": {"layers": ["A", "B", "V3"], "route_key": "A+B+V3"},
        "prompt_snapshot": {"A": "A-v2", "B": "B-v2"},
        "v3_snapshot": {"revision": 4},
        "change_reasons": ["人工纠偏证据"],
    }


def test_candidate_package_is_canonical_and_immutable():
    package = build_immutable_candidate_package(_candidate())
    assert package.package_key.startswith("candidate-")
    assert package.manifest["schema_version"] == "automation-candidate-v1"
    with pytest.raises(TypeError):
        package.manifest["category_key"] = "other"


def test_three_role_plan_requires_target_stable_and_blind():
    plan = build_three_role_regression_plan(
        candidate_package=build_immutable_candidate_package(_candidate()),
        sample_roles={
            "target_error": [11, 12],
            "stable_control": [21],
            "blind_holdout": [31, 32],
        },
    )
    assert plan["roles"] == ("target_error", "stable_control", "blind_holdout")
    assert plan["sample_ids"] == (11, 12, 21, 31, 32)


def test_three_role_plan_rejects_missing_role():
    with pytest.raises(ValueError, match="三角色"):
        build_three_role_regression_plan(
            candidate_package=build_immutable_candidate_package(_candidate()),
            sample_roles={"target_error": [1], "stable_control": [2]},
        )
